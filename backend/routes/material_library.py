"""素材库（M1）：上传/搜索 → 解析成片段 → 逐段确认 → 项目级素材库 → 章节引用。

设计要点（用户口径）：
- 确认粒度 = **片段**（material_chunks）
- 素材归属 = **项目级素材库 + 章节引用关系**（chapter_materials）
- 只有 confirmed 的片段才是"素材"，才会被编写使用
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db

router = APIRouter(tags=["material-library"])


@router.get('/projects/{project_id}/materials/library')
async def library(project_id: int, status: str = "confirmed", q: str = "",
                  material_id: int = 0, limit: int = 50):
    """素材库 / 待确认：按片段列出（带所属文件与页码）。"""
    status = status if status in ("confirmed", "pending", "rejected") else "confirmed"
    sql = ("SELECT mc.id, mc.material_id, mc.chunk_index, mc.page, mc.content, mc.status,"
           "       m.filename, m.role FROM material_chunks mc JOIN materials m ON m.id=mc.material_id"
           " WHERE m.project_id=? AND m.deleted_at IS NULL AND COALESCE(mc.status,'confirmed')=?")
    args: list = [project_id, status]
    if material_id:
        sql += " AND mc.material_id=?"
        args.append(material_id)
    if q:
        sql += " AND mc.content LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY mc.material_id DESC, mc.chunk_index LIMIT ?"
    args.append(max(1, min(200, limit)))
    async with get_db() as db:
        rows = await (await db.execute(sql, args)).fetchall()
        counts = {}
        for state in ("confirmed", "pending"):
            counts[state] = (await (await db.execute(
                "SELECT COUNT(*) FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                "WHERE m.project_id=? AND m.deleted_at IS NULL AND COALESCE(mc.status,'confirmed')=?",
                (project_id, state))).fetchone())[0]
        files = await (await db.execute(
            "SELECT m.id, m.filename, m.role, m.parse_status, "
            "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id) AS chunks, "
            "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id AND COALESCE(c.status,'confirmed')='confirmed') AS confirmed "
            "FROM materials m WHERE m.project_id=? AND m.deleted_at IS NULL ORDER BY m.id DESC",
            (project_id,))).fetchall()
    items = [{"chunk_id": row[0], "material_id": row[1], "index": row[2], "page": row[3],
              "text": row[4], "status": row[5] or "confirmed", "filename": row[6], "role": row[7]}
             for row in rows]
    return {"items": items, "counts": counts,
            "files": [{"id": r[0], "filename": r[1], "role": r[2], "parse_status": r[3],
                       "chunks": r[4], "confirmed": r[5]} for r in files]}


class ChunkActionBody(BaseModel):
    chunk_ids: list[int] = Field(default_factory=list)
    action: str = Field(default="confirm", pattern="^(confirm|reject|pending)$")


@router.post('/projects/{project_id}/materials/chunks')
async def confirm_chunks(project_id: int, body: ChunkActionBody):
    """逐段确认/丢弃（确认后才进素材库、才能被编写使用）。"""
    if not body.chunk_ids:
        return {"changed": 0}
    status = {"confirm": "confirmed", "reject": "rejected", "pending": "pending"}[body.action]
    async with get_db() as db:
        placeholders = ",".join("?" for _ in body.chunk_ids)
        cur = await db.execute(
            f"UPDATE material_chunks SET status=?, confirmed_at=CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders}) AND material_id IN "
            f"(SELECT id FROM materials WHERE project_id=? AND deleted_at IS NULL)",
            [status, *body.chunk_ids, project_id])
        await db.commit()
        changed = cur.rowcount or 0
    return {"changed": changed, "status": status}


@router.get('/blocks/{block_id}/materials/bound')
async def bound(block_id: int):
    """本章已引用的素材（片段级）。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT b.chunk_id, mc.material_id, b.section, b.mode, 1, "
            "       mc.chunk_index, mc.page, mc.content, m.filename "
            "FROM chapter_material_chunks b JOIN material_chunks mc ON mc.id=b.chunk_id "
            "LEFT JOIN materials m ON m.id=mc.material_id WHERE b.block_id=?",
            (block_id,))).fetchall()
    return {"items": [{"chunk_id": r[0], "material_id": r[1], "section": r[2] or "", "mode": r[3] or "cite",
                       "confirmed": bool(r[4]), "index": r[5], "page": r[6],
                       "text": r[7] or "", "filename": r[8] or ""} for r in rows]}


class BindBody(BaseModel):
    chunk_ids: list[int] = Field(default_factory=list)
    material_id: int = 0
    section: str = ""
    mode: str = Field(default="cite", pattern="^(cite|context|paste)$")


@router.post('/blocks/{block_id}/materials/bind')
async def bind(block_id: int, body: BindBody):
    """把已确认素材应用到本章（引用/上下文/粘贴）。"""
    if not body.chunk_ids:
        raise HTTPException(422, "请选择素材片段")
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        added = 0
        for chunk_id in body.chunk_ids:
            row = await (await db.execute(
                "SELECT mc.id, mc.material_id, COALESCE(mc.status,'confirmed') FROM material_chunks mc "
                "JOIN materials m ON m.id=mc.material_id "
                "WHERE mc.id=? AND m.project_id=? AND m.deleted_at IS NULL",
                (chunk_id, block["project_id"]))).fetchone()
            if not row or row[2] != "confirmed":
                continue                                    # 未确认的素材不能用
            await db.execute(
                "INSERT INTO chapter_material_chunks (block_id, chunk_id, section, mode) VALUES (?,?,?,?) "
                "ON CONFLICT(block_id, chunk_id) DO UPDATE SET section=excluded.section, mode=excluded.mode",
                (block_id, chunk_id, body.section, body.mode))
            added += 1
        await db.commit()
    return {"added": added}


@router.delete('/blocks/{block_id}/materials/bound/{chunk_id}')
async def unbind(block_id: int, chunk_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM chapter_material_chunks WHERE block_id=? AND chunk_id=?", (block_id, chunk_id))
        await db.commit()
    return {"removed": True}
