"""素材聚合的持久化层 — 基于现有 materials + material_chunks 表。"""

from typing import Optional

import aiosqlite

from services.block_store import (
    create_chunk as _create_chunk,
    create_material as _create_material,
    delete_material as _delete_material,
    get_chunk as _get_chunk,
    list_chunks_by_materials as _list_chunks_by_materials,
    list_chunks_by_project as _list_chunks_by_project,
    list_chunks_with_filename as _list_chunks_with_filename,
    list_materials as _list_materials,
    update_chunk_content as _update_chunk_content,
    update_material_parse_status as _update_material_parse_status,
)

from .models import Chunk, Material


class MaterialRepository:
    """素材聚合的持久化（基于现有 materials + material_chunks 表）。

    本仓不直接写 SQL，调用 services.block_store 中的现有 CRUD。
    Phase 7 services 删除时会切换到原生 SQL 或新存储层。
    """

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    # ─────────────────────────────────────────────────────
    # materials
    # ─────────────────────────────────────────────────────

    async def create_material(
        self,
        project_id: int,
        filename: str,
        type_: str,
        file_path: str,
        role: str,
        file_size: int = 0,
    ) -> Material:
        row = await _create_material(
            self.db,
            project_id=project_id,
            filename=filename,
            type_=type_,
            file_path=file_path,
            role=role,
            file_size=file_size,
        )
        return Material.from_dict(row)

    async def list_materials(self, project_id: int) -> list[Material]:
        rows = await _list_materials(self.db, project_id)
        return [Material.from_dict(r) for r in rows]

    async def delete_material(self, material_id: int) -> bool:
        return await _delete_material(self.db, material_id)

    async def mark_parsing(self, material_id: int) -> None:
        await _update_material_parse_status(self.db, material_id, "parsing")

    async def mark_parsed(self, material_id: int) -> None:
        # update_material_parse_status 在 status="done" 时会同时更新 parsed_at
        await _update_material_parse_status(self.db, material_id, "done")

    async def mark_failed(self, material_id: int) -> None:
        await _update_material_parse_status(self.db, material_id, "failed")

    # ─────────────────────────────────────────────────────
    # chunks
    # ─────────────────────────────────────────────────────

    async def create_chunk(
        self, material_id: int, chunk_index: int, content: str
    ) -> Chunk:
        row = await _create_chunk(self.db, material_id, chunk_index, content)
        return Chunk.from_dict(row)

    async def list_chunks_by_project(self, project_id: int) -> list[Chunk]:
        """返回所有 chunk（不带 filename，供沈括 agent 检索用）。"""
        rows = await _list_chunks_by_project(self.db, project_id)
        return [Chunk.from_dict(r) for r in rows]

    async def list_chunks_with_filename(self, project_id: int) -> list[Chunk]:
        """返回所有 chunk 并附带 material.filename（前端展示用）。"""
        rows = await _list_chunks_with_filename(self.db, project_id)
        return [Chunk.from_dict(r) for r in rows]

    async def list_chunks_by_materials(
        self, material_ids: list[int]
    ) -> list[Chunk]:
        """按 material_id 列表过滤，附带 filename。空列表返回 []。"""
        rows = await _list_chunks_by_materials(self.db, material_ids)
        return [Chunk.from_dict(r) for r in rows]

    async def get_chunk(
        self, material_id: int, chunk_index: int
    ) -> Optional[Chunk]:
        row = await _get_chunk(self.db, material_id, chunk_index)
        return Chunk.from_dict(row) if row else None

    async def update_chunk_content(
        self, material_id: int, chunk_index: int, content: str
    ) -> Optional[Chunk]:
        row = await _update_chunk_content(self.db, material_id, chunk_index, content)
        return Chunk.from_dict(row) if row else None
