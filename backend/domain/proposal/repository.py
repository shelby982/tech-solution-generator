"""方案聚合的持久化层 — 基于已有 blocks 表。"""

import json
from typing import Optional

import aiosqlite

from services.block_store import (
    get_block as _get_block,
    list_blocks as _list_blocks,
    update_block_content as _update_block_content,
    update_block_status as _update_block_status,
)

from .models import Block, BlockOutput


class ProposalRepository:
    """方案聚合的持久化（基于已有 blocks 表）。

    本仓不直接写 SQL，调用 services.block_store 中的现有 CRUD。
    Phase 7 services 删除时会切换到原生 SQL 或新存储层。
    """

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    # ─────────────────────────────────────────────────────
    # 读
    # ─────────────────────────────────────────────────────

    async def list_blocks(self, project_id: int) -> list[Block]:
        """读取 project 下所有 block，按 order_idx 排序。"""
        rows = await _list_blocks(self.db, project_id)
        return [Block.from_row(r) for r in rows]

    async def get_block(self, db_id: int) -> Optional[Block]:
        """按 blocks.id 读取单个 block。"""
        row = await _get_block(self.db, db_id)
        return Block.from_row(row) if row else None

    # ─────────────────────────────────────────────────────
    # 写
    # ─────────────────────────────────────────────────────

    async def save_block_output(
        self,
        db_id: int,
        output: BlockOutput,
    ) -> Optional[Block]:
        """落地诸葛亮产出：写 content + source(JSON) + status='done'，更新 updated_at。

        sources 序列化为 JSON 字符串写入 blocks.source 列。
        outline / needs_diagram 不持久化（工作流中间态）。
        kind 不在此处更新（Spec 阶段已落 kind，公文 block 由 zhuge_liang
        在 LangGraph state 内自行决定走 letter 分支，blocks 表的 kind 列保持原值）。

        实现细节：先调 update_block_content 写 content + status（含 commit），
        再单独 UPDATE source 列再 commit。两次 commit 不是 transaction，
        Phase 7 重写存储层时再统一处理。
        """
        sources_json = json.dumps(
            [s.to_dict() for s in output.sources],
            ensure_ascii=False,
        )
        # 1) 写 content + status（update_block_content 内部已 commit 并刷新 updated_at）
        updated = await _update_block_content(
            self.db, db_id, output.content, status="done",
        )
        if updated is None:
            return None
        # 2) 单独写 source 列
        await self.db.execute(
            "UPDATE blocks SET source = ? WHERE id = ?",
            (sources_json, db_id),
        )
        await self.db.commit()
        return await self.get_block(db_id)

    async def mark_block_failed(self, db_id: int) -> None:
        """标记 block 生成失败：status='failed'。"""
        await _update_block_status(self.db, db_id, "failed")
