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

import logging
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
        regenerate_targets: Optional[list[str]] = None,
        emitter: Optional[EventCallback] = None,
    ) -> tuple[dict[str, BlockOutput], list[str]]:
        """为每个 block 生成 BlockOutput。

        当 regenerate_targets 非空时仅跑这些 block_id；返回的第二个值是
        "已消费的 regenerate_targets"（调用方据此清空 state.proposal.regenerate_targets）。
        否则跑 outline_matrix 全部，第二个值为 []。

        materials: 沈括产出的 {block_id: list[Match]}
        emitter:   事件回调，签名 (event_type, payload) → None|awaitable[None]
                   事件类型：block_start, token, block_done
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

        results: dict[str, BlockOutput] = {}

        for idx, block_id in enumerate(target_ids):
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

            results[block_id] = output
            await _emit(emitter, "block_done", {
                "block_id": block_id,
                "kind": output.kind,
                "content_length": len(output.content),
                "sources_count": len(output.sources),
            })

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
