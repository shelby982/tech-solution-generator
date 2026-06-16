"""规范书聚合的持久化层 — 项目摘要 + outline_matrix（基于 blocks 表）。"""

import aiosqlite

from services.block_store import (
    create_block,
    delete_blocks_by_project,
    get_project,
    list_blocks,
    update_project,
)

from .models import OutlineMatrixRow, Section


class SpecRepository:
    """规范书聚合的持久化：项目摘要 + outline_matrix（写到 blocks 表）。

    本期不持久化 raw TOC 与 raw_content（这些是工作流内部中间态，由 LangGraph state 管理）。
    """

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    # ─────────────────────────────────────────────────────
    # doc_summary
    # ─────────────────────────────────────────────────────

    async def save_doc_summary(self, project_id: int, summary: str) -> None:
        """更新 projects.summary。"""
        await update_project(self.db, project_id, summary=summary)

    async def get_doc_summary(self, project_id: int) -> str:
        """读取 projects.summary，无则返空串。"""
        proj = await get_project(self.db, project_id)
        if proj is None:
            return ""
        return proj.get("summary") or ""

    # ─────────────────────────────────────────────────────
    # outline_matrix
    # ─────────────────────────────────────────────────────

    async def save_outline_matrix(
        self,
        project_id: int,
        sections: list[Section],
        rows: list[OutlineMatrixRow],
    ) -> None:
        """把 toc + outline_matrix 落到 blocks 表。

        策略：先 delete_blocks_by_project 清空，再按 sections 顺序逐条 create_block。
        rows 通过 block_id 与 sections 对应；rows 中没有的 block 仅写入 toc 元信息（8 字段空值）。
        """
        await delete_blocks_by_project(self.db, project_id)
        row_by_id = {r.block_id: r for r in rows}
        for idx, section in enumerate(sections):
            row = row_by_id.get(section.id)
            await create_block(
                self.db,
                project_id=project_id,
                block_id=section.id,
                kind="tech",
                level=section.level,
                title=section.title,
                domain="",
                parent_title="",
                requirement=row.requirement if row else "",
                score="",
                source="",
                order_idx=idx,
                content="",
                status="empty",
                key_points=row.key_points if row else "",
                veto_items=row.veto_items if row else "",
                bonus_items=row.bonus_items if row else "",
                score_items=row.score_items if row else "",
                evidence_required=row.evidence_required if row else "",
                constraint_level=row.constraint_level if row else "",
                indicators=row.indicators if row else "",
            )

    async def get_outline_matrix(
        self,
        project_id: int,
    ) -> tuple[list[Section], list[OutlineMatrixRow]]:
        """从 blocks 表读回 (sections, rows)，按 order_idx 排序。

        sections 的 raw_content 留空（持久化时不存）；special_marks 同理留空。
        """
        records = await list_blocks(self.db, project_id)
        sections: list[Section] = []
        rows: list[OutlineMatrixRow] = []
        for rec in records:
            sections.append(
                Section(
                    id=rec.get("block_id") or "",
                    level=int(rec.get("level") or 1),
                    title=rec.get("title") or "",
                    raw_content="",
                    special_marks=[],
                )
            )
            rows.append(
                OutlineMatrixRow(
                    block_id=rec.get("block_id") or "",
                    title=rec.get("title") or "",
                    requirement=rec.get("requirement") or "",
                    key_points=rec.get("key_points") or "",
                    veto_items=rec.get("veto_items") or "",
                    bonus_items=rec.get("bonus_items") or "",
                    score_items=rec.get("score_items") or "",
                    evidence_required=rec.get("evidence_required") or "",
                    constraint_level=rec.get("constraint_level") or "recommended",
                    indicators=rec.get("indicators") or "",
                )
            )
        return sections, rows
