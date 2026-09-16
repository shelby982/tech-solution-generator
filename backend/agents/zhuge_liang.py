"""诸葛亮 — 应答方案生成专家 agent。

职责（spec §3）：
按 block 类型分支生成应答方案：
- letter（is_letter_section=True）→ 公文模板，输出含 `致：【…】` 抬头与落款占位
- tech（普通技术 block）→ 先生成写作大纲（含表格样例 + 图占位符），再流式生成正文
- 含图占位符的 block → 正文中保留 `【架构图：…】` `【流程图：…】` 占位

支持事件回调：每个 block 开始时发 block_start，每 token 发 token，结束发 block_done。
回调可为 None；如未提供，agent 仅静默生成。

支持重生路径：state.proposal.regenerate_targets 非空时只跑指定 block，跑完返回
consumed_targets，由调用方据此清空 state 中的字段。
"""

import asyncio
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional, Union

from domain.letter_detector import is_letter_section
from domain.proposal import BlockOutput, Source
from domain.spec import OutlineMatrixRow
from infra.retrieval import Match

logger = logging.getLogger(__name__)


# 图占位符正则：用于检测 outline 是否要求嵌入架构图/流程图等
_DIAGRAM_PLACEHOLDER_RE = re.compile(
    r"【(?:架构图|流程图|拓扑图|部署图|时序图|网络图|示意图)[：:]"
)


# 大纲里的"待补充"占位符：写作阶段发现欠缺的外部素材/数据，
# 直接转成补料请求（见 spec §4.6 的补料请求抽取说明），省掉一次 LLM 调用。
_MATERIAL_PLACEHOLDER_RE = re.compile(r"【待补充[：:]([^】]+)】")


def _collect_material_requests(outline: str) -> list[dict]:
    """把写作大纲中的 【待补充：xx】 占位符转成补料请求（去重，保序）。

    返回 [{"query": xx, "reason": "..."}]；无占位符或大纲为空时返回 []。
    """
    if not outline:
        return []
    seen: set[str] = set()
    requests: list[dict] = []
    for m in _MATERIAL_PLACEHOLDER_RE.finditer(outline):
        query = m.group(1).strip()
        if not query or query in seen:
            continue
        seen.add(query)
        requests.append({
            "query": query,
            "reason": "写作大纲中的待补充占位符",
        })
    return requests


# per-block 并发上限：默认 1（按章节顺序逐个生成，让用户能看到一章接一章的流式进度，
# 同时点击暂停时只需等当前一章 LLM 流完成即可立即停下，不会有 5 个并发还在跑）。
# 如需提速可通过 env 覆盖到更高并发。
BLOCK_CONCURRENCY = int(os.getenv("ZHUGELIANG_BLOCK_CONCURRENCY", "1"))


# 事件回调签名：(event_type, payload) -> None | awaitable[None]
EventCallback = Callable[[str, dict], Union[None, Awaitable[None]]]


async def _emit(emitter: Optional[EventCallback], event_type: str, payload: dict) -> None:
    """统一 dispatch event callback；支持 sync 与 async 两种回调。"""
    if emitter is None:
        return
    result = emitter(event_type, payload)
    if hasattr(result, "__await__"):
        await result


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

class ZhugeLiangAgent:
    """诸葛亮：按 block 类型生成应答方案。"""

    def __init__(
        self,
        configs_provider: Optional[Callable[[], tuple[list, int]]] = None,
        *,
        target_words: int = 600,
    ):
        """
        configs_provider: 返回 (configs, rr_start_index) 的函数。
        默认从 services.config_store 读，便于单测注入 fixture configs。
        """
        if configs_provider is None:
            from services.config_store import config_store
            self._configs_provider = config_store.get_configs_and_next_index
        else:
            self._configs_provider = configs_provider
        self.target_words = target_words

    # ── generate ───────────────────────────────

    async def generate(
        self,
        outline_matrix: dict[str, OutlineMatrixRow],
        materials: dict[str, list[Match]],
        feedback: Optional[dict[str, list[dict]]] = None,
        regenerate_targets: Optional[list[str]] = None,
        emitter: Optional[EventCallback] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[dict[str, BlockOutput], list[str]]:
        """为每个 block 生成 BlockOutput。

        当 regenerate_targets 非空时仅跑这些 block_id；返回的第二个值是
        "已消费的 regenerate_targets"（调用方据此清空 state.proposal.regenerate_targets）。
        否则跑 outline_matrix 全部，第二个值为 []。

        materials: 沈括产出的 {block_id: list[Match]}
        emitter:   事件回调，签名 (event_type, payload) → None|awaitable[None]
                   事件类型：block_start, token, block_done
        should_cancel: 节点级取消信号探测；每个 block 开始前调一次，返回 True 则跳过剩余
                      block。让用户中途暂停时，已生成 block 内容仍保留，未生成的不再跑。
        """
        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("诸葛亮：未配置 LLM，generate 返回空")
            return ({}, list(regenerate_targets or []))

        # 决定要跑哪些 block_id
        if regenerate_targets:
            target_ids = [bid for bid in regenerate_targets if bid in outline_matrix]
        else:
            target_ids = list(outline_matrix.keys())

        sem = asyncio.Semaphore(BLOCK_CONCURRENCY)

        async def _run_one(idx: int, block_id: str) -> tuple[int, str, BlockOutput]:
            async with sem:
                # 用户暂停 / 取消时跳过尚未开始的 block；已经在跑的 block 让其完成。
                if should_cancel and should_cancel():
                    return (idx, block_id, None)
                row = outline_matrix[block_id]
                title = row.title
                kind = "letter" if is_letter_section(title) else "tech"

                await _emit(emitter, "block_start", {
                    "block_id": block_id,
                    "title": title,
                    "kind": kind,
                })

                try:
                    if kind == "letter":
                        output = await self._generate_letter(
                            block_id, row, configs,
                            (rr_start + idx) % max(len(configs), 1),
                        )
                    else:
                        matches = materials.get(block_id, []) or []
                        output = await self._generate_tech(
                            block_id, row, matches,
                            configs, (rr_start + idx) % max(len(configs), 1),
                            emitter,
                            feedback=(feedback or {}).get(block_id) or [],
                        )
                except Exception as e:
                    logger.warning(f"诸葛亮：block {block_id} 生成失败：{e}")
                    output = BlockOutput(
                        block_id=block_id,
                        kind=kind,
                        content="",
                        outline="",
                        sources=[],
                    )

                await _emit(emitter, "block_done", {
                    "block_id": block_id,
                    "kind": output.kind,
                    "content_length": len(output.content),
                    "sources_count": len(output.sources),
                })
                return (idx, block_id, output)

        # 串行模式（BLOCK_CONCURRENCY=1）下逐 block 执行，让暂停信号能在每个 block
        # 之间立即生效，不会有"已 schedule 但未启动"的 worker 拖累。
        # BLOCK_CONCURRENCY > 1 时仍走 gather 并发。
        results: dict[str, BlockOutput] = {}
        if BLOCK_CONCURRENCY <= 1:
            for i, bid in enumerate(target_ids):
                # 串行入口处再检查一次 cancel：避免已排队的 block 在 sem 释放后才发现要跳过
                if should_cancel and should_cancel():
                    break
                try:
                    triple = await _run_one(i, bid)
                except BaseException as e:
                    logger.warning("诸葛亮：_run_one 异常逃出兜底：%s", e)
                    continue
                _, b, out = triple
                if out is None:
                    continue
                results[b] = out
        else:
            # return_exceptions=True 兜底 _run_one 中 try/except 之外（如 _emit）
            # 抛出的异常，避免单 block 失败 cancel 同 gather 的其他 task。
            triples = await asyncio.gather(
                *[_run_one(i, bid) for i, bid in enumerate(target_ids)],
                return_exceptions=True,
            )
            for entry in triples:
                if isinstance(entry, BaseException):
                    logger.warning("诸葛亮：_run_one 异常逃出兜底：%s", entry)
                    continue
                _, bid, out = entry
                if out is None:
                    continue
                results[bid] = out

        consumed_targets = list(regenerate_targets or [])
        return results, consumed_targets

    # ── letter 分支 ────────────────────────────

    async def _generate_letter(
        self,
        block_id: str,
        row: OutlineMatrixRow,
        configs: list,
        rr_start: int,
    ) -> BlockOutput:
        """公文 block：调 generate_letter_content 取整段 markdown，无 outline 与 sources。"""
        from infra.llm import generate_letter_content

        block_dict = self._row_to_block_dict(row)
        content = await generate_letter_content(configs, rr_start, block_dict)
        return BlockOutput(
            block_id=block_id,
            kind="letter",
            content=content,
            outline="",
            sources=[],
            needs_diagram=False,
        )

    # ── tech 分支 ──────────────────────────────

    async def _generate_tech(
        self,
        block_id: str,
        row: OutlineMatrixRow,
        matches: list[Match],
        configs: list,
        rr_start: int,
        emitter: Optional[EventCallback],
        feedback: Optional[list[dict]] = None,
    ) -> BlockOutput:
        """普通技术 block：先生成 outline，再流式生成正文。"""
        # 函数内 import 让单测可通过 monkeypatch infra.llm.* 直接覆盖
        import infra.llm

        block_dict = self._row_to_block_dict(row)

        # 写作大纲（非流式，含表格样例 + 占位符）
        extra_context = self._matches_to_context(matches)
        outline = await infra.llm.generate_section_outline(
            configs, rr_start, block_dict, extra_context=extra_context,
        )
        needs_diagram = bool(_DIAGRAM_PLACEHOLDER_RE.search(outline or ""))

        # 上一轮评审意见：转成可注入文本；为空则走首次生成路径（行为不变）
        from agents.prompts import format_feedback_block
        feedback_text = format_feedback_block(feedback)

        # 流式正文（chunks 入参为 dict 列表，与 dispatch_block_write 现有签名一致）
        # 把沈括 rerank 后的 reason + hit_points 拼成 content，作为参考素材片段
        chunks_for_write = []
        for m in matches:
            hp = "、".join(m.hit_points) if m.hit_points else ""
            content_snippet = (m.reason or "")
            if hp:
                content_snippet = f"{content_snippet}（要点：{hp}）"
            chunks_for_write.append({"content": content_snippet})

        content_parts: list[str] = []
        async for token in infra.llm.dispatch_block_write(
            configs=configs,
            rr_start_index=rr_start,
            title=row.title,
            requirement=row.requirement or "",
            chunks=chunks_for_write,
            target_words=self.target_words,
            feedback_text=feedback_text,
        ):
            content_parts.append(token)
            await _emit(emitter, "token", {
                "block_id": block_id,
                "token": token,
            })

        content = "".join(content_parts)

        # sources：直接由 matches 元信息构造，material_id 暂用 chunk_id 兜底。
        # 真实 chunk_id → material_id 的映射在 routes 层串联（见 spec §5）。
        sources = [
            Source(
                material_id=(
                    int(m.chunk_id)
                    if isinstance(m.chunk_id, (int, str))
                    and str(m.chunk_id).isdigit()
                    else 0
                ),
                chunk_index=0,
                snippet=(m.reason or "")[:120],
            )
            for m in matches
        ]

        return BlockOutput(
            block_id=block_id,
            kind="tech",
            content=content,
            outline=outline or "",
            sources=sources,
            needs_diagram=needs_diagram,
            material_requests=_collect_material_requests(outline or ""),
        )

    # ── 辅助 ──────────────────────────────────

    @staticmethod
    def _row_to_block_dict(row: OutlineMatrixRow) -> dict[str, Any]:
        """把 OutlineMatrixRow 转 dict，便于直接喂给 infra.llm 的 prompt builder。"""
        return {
            "title": row.title,
            "requirement": row.requirement or "",
            "key_points": row.key_points or "",
            "veto_items": row.veto_items or "",
            "bonus_items": row.bonus_items or "",
            "score_items": row.score_items or "",
            "evidence_required": row.evidence_required or "",
            "indicators": row.indicators or "",
        }

    @staticmethod
    def _matches_to_context(matches: list[Match]) -> str:
        """把沈括产出的 matches 拼成 generate_section_outline 的 extra_context。"""
        if not matches:
            return ""
        parts = []
        for i, m in enumerate(matches, 1):
            line = f"{i}. {m.reason or ''}"
            if m.hit_points:
                line += "（要点：" + "、".join(m.hit_points) + "）"
            parts.append(line)
        return "\n".join(parts)


__all__ = ["ZhugeLiangAgent"]
