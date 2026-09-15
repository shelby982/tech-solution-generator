"""张衡 — 规范书解析专家 agent。

职责（spec §3）：
1. parse: 把规范书文件解析为 toc + 项目概述（doc_summary）
2. extract: 对每章节做 8 字段响应矩阵提炼（含 ★/▲ 标记的特殊处理）

不持有 LLM 配置；通过依赖注入接受 services.config_store.config_store 或自己的轻量 wrapper。
本期直接 import config_store 单例（route 层会注入相同实例）。
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, BinaryIO, Callable, Optional, Union

from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# parse 阶段产出
# ─────────────────────────────────────────────

@dataclass
class SpecParseResult:
    """parse 阶段的产出。

    与 spec §5 SpecState 字段对齐，但 outline_matrix 由 extract 阶段填充。
    """
    doc_id: str
    doc_title: str
    doc_summary: str
    toc: list[DomainSection]


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

class ZhangHengAgent:
    """张衡：规范书解析与 8 字段响应矩阵提炼。"""

    def __init__(
        self,
        configs_provider: Optional[Callable[[], tuple[list, int]]] = None,
    ):
        """
        configs_provider: 返回 (configs, rr_start_index) 的函数。
        默认从 services.config_store 读，便于单测注入 fixture configs。
        """
        if configs_provider is None:
            from services.config_store import config_store
            configs_provider = lambda: config_store.get_configs_and_next_index()
        self._configs_provider = configs_provider

    # ── parse ──────────────────────────────────

    async def parse(
        self,
        file_source: Union[str, BinaryIO],
        *,
        suffix: str = "",
        filename: str = "",
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> SpecParseResult:
        """解析规范书文件。

        - 调 infra.parser.parse_document 拿到 ParsedDocument
        - 调 infra.llm.dispatch_doc_summary 生成 doc_summary（失败留空，不抛）
        - 把 infra.parser.Section → domain.spec.Section
        """
        import asyncio

        from infra.llm import dispatch_doc_summary
        from infra.parser import parse_document

        # parse_document 是同步且耗时（解析 docx/pdf）。直接 await 会阻塞事件循环，
        # 导致 SSE emitter 的 stream() 在解析期间一帧都吐不出。扔到后台线程跑。
        parsed = await asyncio.to_thread(
            parse_document,
            file_source,
            suffix=suffix,
            filename=filename,
            progress_callback=progress_callback,
        )

        # infra Section → domain Section
        toc: list[DomainSection] = [
            DomainSection(
                id=s.id,
                level=s.level,
                title=s.title,
                raw_content=s.raw_content,
                special_marks=list(s.special_marks),
            )
            for s in parsed.sections
        ]

        # 生成 doc_summary（输入是各 section 的 title + content）
        doc_summary = ""
        configs, rr_start = self._configs_provider()
        if configs and toc:
            sections_for_summary = [
                {"title": s.title, "content": s.raw_content}
                for s in toc
            ]
            try:
                doc_summary = await dispatch_doc_summary(
                    configs, rr_start, sections_for_summary,
                )
            except Exception as e:
                logger.warning(f"张衡：doc_summary 生成失败，留空：{e}")
                doc_summary = ""

        return SpecParseResult(
            doc_id=parsed.doc_id,
            doc_title=parsed.title,
            doc_summary=doc_summary,
            toc=toc,
        )

    # ── extract ────────────────────────────────

    async def extract(
        self,
        toc: list[DomainSection],
        *,
        scoring_contexts: Optional[dict[str, str]] = None,
        evaluation_contexts: Optional[dict[str, str]] = None,
        on_section: Optional[
            Callable[[DomainSection, "OutlineMatrixRow"], Optional[Awaitable[None]]]
        ] = None,
        on_section_start: Optional[
            Callable[[DomainSection], Optional[Awaitable[None]]]
        ] = None,
    ) -> dict[str, OutlineMatrixRow]:
        """对 toc 中每个 section 做 8 字段提炼。

        scoring_contexts / evaluation_contexts 是按 block_id 索引的可选上下文
        （来自评分表 / 评审要素，由 routes 注入）。

        on_section: 每完成一章节立即回调一次，签名 (section, row) → None | awaitable[None]。
        on_section_start: 章节真正开始处理时（拿到信号量、调 LLM 前）回调一次，
                         前端用它把卡片切到"提炼中"状态，避免并发等待期看不到反馈。

        失败的章节：行的 8 字段保持默认空，error 字段写错误描述，不抛错（per spec §3）。

        返回：dict 的 key 是 block_id（与 Section.id 同），按 toc 顺序构建。
        """
        # 注意：函数内 import — 测试可通过 monkeypatch infra.llm.dispatch_outline_json 覆盖。
        import infra.llm

        async def _fire(section: DomainSection, row: OutlineMatrixRow) -> None:
            if on_section is None:
                return
            try:
                rv = on_section(section, row)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"张衡：on_section 回调失败（block={section.id}）：{e}")

        async def _fire_start(section: DomainSection) -> None:
            if on_section_start is None:
                return
            try:
                rv = on_section_start(section)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"张衡：on_section_start 回调失败（block={section.id}）：{e}")

        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("张衡：未配置 LLM，extract 返回空字段 + error")
            matrix: dict[str, OutlineMatrixRow] = {}
            for s in toc:
                row = OutlineMatrixRow(
                    block_id=s.id,
                    title=s.title,
                    error="未配置 LLM",
                )
                matrix[s.id] = row
                await _fire(s, row)
            return matrix

        # 构造 sections 列表交给 dispatch_outline_json
        sections_for_extract: list[dict[str, Any]] = []
        for s in toc:
            entry: dict[str, Any] = {
                "title": s.title,
                "content": s.raw_content,
                "special_marks": "".join(s.special_marks),
            }
            if scoring_contexts and s.id in scoring_contexts:
                entry["scoring_context"] = scoring_contexts[s.id]
            if evaluation_contexts and s.id in evaluation_contexts:
                entry["evaluation_context"] = evaluation_contexts[s.id]
            sections_for_extract.append(entry)

        # dispatch_outline_json 是 async generator，按完成顺序 yield；
        # 每条到手立即按 idx 回填到 toc + on_section，前端实时看到一条 outline_extract。
        async def _dispatch_on_start(idx: int) -> None:
            if 0 <= idx < len(toc):
                await _fire_start(toc[idx])

        # 测试用 monkeypatch 注入的 fake_dispatch 可能不接受 on_start —— 用签名探测降级。
        try:
            sig = inspect.signature(infra.llm.dispatch_outline_json)
            supports_on_start = "on_start" in sig.parameters
        except (TypeError, ValueError):
            supports_on_start = False

        if supports_on_start:
            gen = infra.llm.dispatch_outline_json(
                configs, rr_start, sections_for_extract, on_start=_dispatch_on_start,
            )
        else:
            gen = infra.llm.dispatch_outline_json(
                configs, rr_start, sections_for_extract,
            )

        matrix: dict[str, OutlineMatrixRow] = {}
        seq_fallback_idx = 0
        async for item in gen:
            # 优先从 item 拿 idx（新 dispatch 按完成顺序 yield 时携带），
            # 兼容旧合约（无 idx 字段时按 toc 顺序匹配）。
            raw_idx = item.get("idx") if isinstance(item, dict) else None
            if raw_idx is None:
                idx = seq_fallback_idx
                seq_fallback_idx += 1
            else:
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    idx = seq_fallback_idx
                    seq_fallback_idx += 1
            if idx < 0 or idx >= len(toc):
                continue
            section = toc[idx]
            row = OutlineMatrixRow(
                block_id=section.id,
                title=section.title,
                requirement=str(item.get("requirement", "") or ""),
                key_points=str(item.get("key_points", "") or ""),
                veto_items=str(item.get("veto_items", "") or ""),
                bonus_items=str(item.get("bonus_items", "") or ""),
                score_items=str(item.get("score_items", "") or ""),
                evidence_required=str(item.get("evidence_required", "") or ""),
                constraint_level=str(
                    item.get("constraint_level", "recommended") or "recommended",
                ),
                indicators=str(item.get("indicators", "") or ""),
                error=str(item.get("error", "") or ""),
            )
            matrix[section.id] = row
            await _fire(section, row)

        # toc 中存在但 results 缺少的章节兜底（防 dispatch bug）
        for section in toc:
            if section.id not in matrix:
                row = OutlineMatrixRow(
                    block_id=section.id,
                    title=section.title,
                    error="提炼缺失",
                )
                matrix[section.id] = row
                await _fire(section, row)

        return matrix


__all__ = [
    "ZhangHengAgent",
    "SpecParseResult",
]
