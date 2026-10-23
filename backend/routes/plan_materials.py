"""计划阶段的素材确认（v4）：按节匹配建议 → 用户确认 → 计划锁定"哪节用什么素材"。

匹配原料 = 项目素材库里 status='selected' 的片段（用户已勾选过的素材）。
生成只读"已确认"的绑定（见 services/material_context）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services.material_match import recommend, section_profile

router = APIRouter(tags=["plan-materials"])

# 类型 → 默认用法
_MODE_BY_TAG = {"table": "data", "data": "data", "clause": "data",
                "qualification": "cite", "policy": "cite"}


async def _pool(db, project_id: int) -> list[dict]:
    """候选池：素材库已选片段（文件 + 联网）。"""
    rows = await (await db.execute(
        "SELECT mc.id, mc.content, mc.page, m.role, 'file', m.filename, mc.tags "
        "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
        "WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status='selected' "
        "UNION ALL "
        "SELECT cc.id, cc.content, 0, 'web', 'web', c.domain, cc.tags "
        "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
        "WHERE c.project_id=? AND cc.status='selected'", (project_id, project_id))).fetchall()

    def _tags(raw):
        try:
            return json.loads(raw or "[]") or []
        except Exception:
            return []

    return [{"chunk_id": r[0], "text": r[1], "page": r[2], "role": r[3],
             "source_kind": "web" if r[4] == "web" else "file", "source_title": r[5] or "",
             "tags": _tags(r[6])} for r in rows]


async def _bound(db, block_id: int) -> dict[str, list[dict]]:
    """已确认的"节 → 素材"绑定。"""
    rows = await (await db.execute(
        "SELECT b.section, b.chunk_id, COALESCE(b.chunk_kind,'file'), COALESCE(b.mode,'cite') "
        "FROM chapter_material_chunks b WHERE b.block_id=?", (block_id,))).fetchall()
    result: dict[str, list[dict]] = {}
    for section, chunk_id, kind, mode in rows:
        result.setdefault(str(section or ""), []).append(
            {"chunk_id": chunk_id, "chunk_kind": kind, "mode": mode})
    return result


@router.get('/blocks/{block_id}/plan/material-suggestions')
async def suggestions(block_id: int, per_section: int = 3):
    """为本章每一节给出"建议素材"（含已确认状态）。"""
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        project_id = int(block["project_id"])
        outline = []
        try:
            from services.writing_plans import chapter_plan
            plan = await chapter_plan(db, project_id, str(block["run_thread_id"] or ""),
                                      str(block["block_id"] or ""))
            outline = plan.get("outline") or []
        except Exception:
            plan = {}
        pool = await _pool(db, project_id)
        bound = await _bound(db, block_id)

    result = []
    for section in outline:
        title = str(section.get("title") or "")
        profile = section_profile(section, plan)
        picked = recommend(profile, pool, limit=max(1, min(8, per_section + 3)), min_score=0.12)
        confirmed = bound.get(title, [])
        confirmed_ids = {int(item["chunk_id"]) for item in confirmed}
        candidates = []
        for item in picked:
            chunk_id = int(item["chunk_id"])
            signed = -chunk_id if item["source_kind"] == "web" else chunk_id
            candidates.append({
                "chunk_id": signed, "text": str(item["text"])[:400], "page": item.get("page") or 0,
                "source_kind": item["source_kind"], "source_title": item.get("source_title") or "",
                "score": item["score"], "reason": item["reason"],
                "tags": item.get("tags") or [],
                "confirmed": signed in {(-abs(cid) if kind == "cite" else abs(cid)) for cid in confirmed_ids} or chunk_id in confirmed_ids,
                "suggested_mode": next((_MODE_BY_TAG[tag] for tag in (item.get("tags") or []) if tag in _MODE_BY_TAG), "cite"),
            })
        # 已确认但不在候选里的（用户手工换过的）也带上
        for item in confirmed:
            chunk_id = int(item["chunk_id"])
            if any(c["chunk_id"] == chunk_id for c in candidates):
                continue
            candidates.insert(0, {"chunk_id": chunk_id, "text": "", "page": 0,
                                  "source_kind": "web" if item["chunk_kind"] == "cite" else "file",
                                  "source_title": "", "score": 1.0,
                                  "reason": "已确认", "tags": [], "confirmed": True,
                                  "suggested_mode": item.get("mode") or "cite"})
        result.append({"section": title, "words": section.get("words") or 0,
                       "types_wanted": profile["types_wanted"],
                       "candidates": candidates,
                       "confirmed_count": len(confirmed),
                       "has_material": bool(candidates)})
    return {"sections": result, "pool": len(pool)}


class ApplyBody(BaseModel):
    section: str = Field(min_length=1)
    chunk_ids: list[int] = Field(default_factory=list)
    mode: str = Field(default="cite", pattern="^(cite|data|context|auto)$")


@router.post('/blocks/{block_id}/plan/material-suggestions/apply')
async def apply_suggestion(block_id: int, body: ApplyBody):
    """确认某节的素材（覆盖该节原绑定；chunk_ids 为空 = 清空本节素材）。"""
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        project_id = int(block["project_id"])
        # 清空该节原绑定
        await db.execute("DELETE FROM chapter_material_chunks WHERE block_id=? AND section=?",
                         (block_id, body.section))
        added = 0
        for signed in body.chunk_ids:
            signed = int(signed)
            kind = "cite" if signed < 0 else "file"
            chunk_id = abs(signed)
            table = "citation_chunks" if kind == "cite" else "material_chunks"
            if kind == "cite":
                row = await (await db.execute(
                    f"SELECT id, status, tags FROM {table} WHERE id=? AND citation_id IN "
                    f"(SELECT id FROM citations WHERE project_id=?)", (chunk_id, project_id))).fetchone()
            else:
                row = await (await db.execute(
                    f"SELECT mc.id, mc.status, mc.tags FROM material_chunks mc "
                    f"JOIN materials m ON m.id=mc.material_id "
                    f"WHERE mc.id=? AND m.project_id=? AND m.deleted_at IS NULL",
                    (chunk_id, project_id))).fetchone()
            if not row or str(row[1] or "") != "selected":
                continue
            mode = body.mode
            if mode == "auto":
                try:
                    tags = json.loads(row[2] or "[]") or []
                except Exception:
                    tags = []
                mode = next((_MODE_BY_TAG[tag] for tag in tags if tag in _MODE_BY_TAG), "cite")
            await db.execute(
                "INSERT INTO chapter_material_chunks (block_id, chunk_id, section, mode, chunk_kind) "
                "VALUES (?,?,?,?,?) ON CONFLICT(block_id, chunk_id) DO UPDATE SET "
                "section=excluded.section, mode=excluded.mode, chunk_kind=excluded.chunk_kind",
                (block_id, signed, body.section, mode, kind))
            added += 1
        await db.commit()
    return {"section": body.section, "added": added}


@router.post('/projects/{project_id}/plan/material-suggestions/apply-all')
async def apply_all(project_id: int, per_section: int = 3):
    """全项目：为每章每节自动采用建议素材（批量确认）。"""
    async with get_db() as db:
        blocks = await (await db.execute(
            "SELECT b.id, b.block_id FROM blocks b WHERE b.project_id=? "
            "AND b.is_catalog=0 AND b.has_children=0 AND COALESCE(b.content,'')='' ", (project_id,))).fetchall()
    applied = 0
    for row in blocks:
        block_id = int(row[0])
        data = await suggestions(block_id, per_section=per_section)
        for item in data["sections"]:
            if not item["candidates"]:
                continue
            ids = [c["chunk_id"] for c in item["candidates"] if not c["confirmed"]][:per_section]
            if not ids:
                continue
            await apply_suggestion(block_id, ApplyBody(section=item["section"], chunk_ids=ids, mode="auto"))
            applied += 1
    return {"blocks": len(blocks), "sections_applied": applied}
