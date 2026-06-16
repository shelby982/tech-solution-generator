"""张衡 — 规范书解析专家 agent。

职责（spec §3）：
1. parse: 把规范书文件解析为 toc + 项目概述（doc_summary）
2. extract: 对每章节做 8 字段响应矩阵提炼（含 ★/▲ 标记的特殊处理）

不持有 LLM 配置；通过依赖注入接受 services.config_store.config_store 或自己的轻量 wrapper。
本期直接 import config_store 单例（route 层会注入相同实例）。
"""

import logging
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Optional, Union

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
        from infra.llm import dispatch_doc_summary
        from infra.parser import parse_document

        parsed = parse_document(
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
    ) -> dict[str, OutlineMatrixRow]:
        """对 toc 中每个 section 做 8 字段提炼。

        scoring_contexts / evaluation_contexts 是按 block_id 索引的可选上下文
        （来自评分表 / 评审要素，由 routes 注入）。

        失败的章节：行的 8 字段保持默认空，error 字段写错误描述，不抛错（per spec §3）。

        返回：dict 的 key 是 block_id（与 Section.id 同），按 toc 顺序构建。
        """
        # 注意：函数内 import — 测试可通过 monkeypatch infra.llm.dispatch_outline_json 覆盖。
        import infra.llm

        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("张衡：未配置 LLM，extract 返回空字段 + error")
            return {
                s.id: OutlineMatrixRow(
                    block_id=s.id,
                    title=s.title,
                    error="未配置 LLM",
                )
                for s in toc
            }

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

        # dispatch_outline_json 是 async generator，按 toc 顺序 yield
        results: list[dict] = []
        async for item in infra.llm.dispatch_outline_json(
            configs, rr_start, sections_for_extract,
        ):
            results.append(item)

        # 拼装 dict[block_id, OutlineMatrixRow]
        matrix: dict[str, OutlineMatrixRow] = {}
        for section, item in zip(toc, results):
            matrix[section.id] = OutlineMatrixRow(
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

        # toc 中存在但 results 缺少的章节兜底（防 dispatch bug）
        for section in toc:
            if section.id not in matrix:
                matrix[section.id] = OutlineMatrixRow(
                    block_id=section.id,
                    title=section.title,
                    error="提炼缺失",
                )

        return matrix


__all__ = [
    "ZhangHengAgent",
    "SpecParseResult",
]
