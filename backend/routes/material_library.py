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
        # 搜索来源的片段：首次访问时按 citations.segments 懒生成（默认待确认）
        try:
            cites = await (await db.execute(
                "SELECT id, title, segments FROM citations WHERE project_id=?", (project_id,))).fetchall()
            for cite in cites:
                have = (await (await db.execute(
                    "SELECT COUNT(*) FROM citation_chunks WHERE citation_id=?", (cite[0],))).fetchone())[0]
                if have:
                    continue
                import json as _json
                try:
                    segments = _json.loads(cite[2] or "[]")
                except Exception:
                    segments = []
                for index, seg in enumerate(segments or []):
                    text = str((seg or {}).get("content") or "").strip()
                    if len(text) < 20:
                        continue
                    await db.execute(
                        "INSERT OR IGNORE INTO citation_chunks (citation_id, chunk_index, heading, content, status) "
                        "VALUES (?,?,?,?, 'confirmed')",
                        (cite[0], index, str((seg or {}).get("heading") or cite[1] or "")[:80], text[:1200]))
            await db.commit()
        except Exception:
            pass
        rows = await (await db.execute(sql, args)).fetchall()
        cite_rows = await (await db.execute(
            "SELECT cc.id, c.id, cc.chunk_index, cc.heading, cc.content, cc.status, c.domain "
            "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
            "WHERE c.project_id=? AND (cc.status=? OR ?='confirmed') ORDER BY cc.id DESC LIMIT ?",
            (project_id, status, status, max(1, min(200, limit))))).fetchall()
        counts = {}
        for state in ("confirmed", "pending"):
            file_count = (await (await db.execute(
                "SELECT COUNT(*) FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                "WHERE m.project_id=? AND m.deleted_at IS NULL AND COALESCE(mc.status,'confirmed')=?",
                (project_id, state))).fetchone())[0]
            cite_count = (await (await db.execute(
                "SELECT COUNT(*) FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                "WHERE c.project_id=? AND cc.status=?", (project_id, state))).fetchone())[0]
            counts[state] = file_count + cite_count
        files = await (await db.execute(
            "SELECT m.id, m.filename, m.role, m.parse_status, "
            "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id) AS chunks, "
            "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id AND COALESCE(c.status,'confirmed')='confirmed') AS confirmed "
            "FROM materials m WHERE m.project_id=? AND m.deleted_at IS NULL ORDER BY m.id DESC",
            (project_id,))).fetchall()
    items = [{"chunk_id": row[0], "material_id": row[1], "index": row[2], "page": row[3],
              "text": row[4], "status": row[5] or "confirmed", "filename": row[6], "role": row[7],
              "chunk_kind": "file", "source_type": "upload"}
             for row in rows]
    for row in cite_rows:
        items.append({"chunk_id": row[0], "material_id": 0, "index": row[2], "page": 0,
                      "text": row[4], "status": row[5], "filename": row[3] or (row[6] or "公开来源"),
                      "role": "cite", "chunk_kind": "cite", "source_type": "web"})
    items.sort(key=lambda item: (item["status"] != status, item["chunk_id"]), reverse=False)
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
    cite_ids = [int(cid) for cid in body.chunk_ids if int(cid) < 0]      # 负数 = 搜索片段（-id）
    file_ids = [int(cid) for cid in body.chunk_ids if int(cid) > 0]
    changed = 0
    async with get_db() as db:
        for cid in cite_ids:
            cur = await db.execute(
                "UPDATE citation_chunks SET status=?, confirmed_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND citation_id IN (SELECT id FROM citations WHERE project_id=?)",
                (status, -cid, project_id))
            changed += cur.rowcount or 0
        if file_ids:
            placeholders = ",".join("?" for _ in file_ids)
            cur = await db.execute(
                f"UPDATE material_chunks SET status=?, confirmed_at=CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) AND material_id IN "
                f"(SELECT id FROM materials WHERE project_id=? AND deleted_at IS NULL)",
                [status, *file_ids, project_id])
            changed += cur.rowcount or 0
        await db.commit()
    return {"changed": changed, "status": status}


@router.get('/blocks/{block_id}/materials/bound')
async def bound(block_id: int):
    """本章已引用的素材（片段级）。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT b.chunk_id, mc.material_id, b.section, b.mode, 1, "
            "       mc.chunk_index, mc.page, mc.content, m.filename, COALESCE(b.chunk_kind,'file') "
            "FROM chapter_material_chunks b JOIN material_chunks mc ON mc.id=b.chunk_id "
            "LEFT JOIN materials m ON m.id=mc.material_id "
            "WHERE b.block_id=? AND COALESCE(b.chunk_kind,'file')='file'",
            (block_id,))).fetchall()
        cite_rows = await (await db.execute(
            "SELECT b.chunk_id, 0, b.section, b.mode, 1, cc.chunk_index, 0, cc.content, cc.heading, 'cite' "
            "FROM chapter_material_chunks b JOIN citation_chunks cc ON cc.id=ABS(b.chunk_id) "
            "WHERE b.block_id=? AND b.chunk_kind='cite'", (block_id,))).fetchall()
        rows = list(rows) + list(cite_rows)
    return {"items": [{"chunk_id": r[0], "material_id": r[1], "section": r[2] or "", "mode": r[3] or "cite",
                       "confirmed": bool(r[4]), "index": r[5], "page": r[6],
                       "text": r[7] or "", "filename": r[8] or "", "chunk_kind": r[9] or "file"}
                      for r in rows]}


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
            if int(chunk_id) < 0:                          # 负数 = 搜索片段
                row = await (await db.execute(
                    "SELECT cc.id, 0, cc.status FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                    "WHERE cc.id=? AND c.project_id=?", (-int(chunk_id), block["project_id"]))).fetchone()
                kind = "cite"
            else:
                row = await (await db.execute(
                    "SELECT mc.id, mc.material_id, COALESCE(mc.status,'confirmed') FROM material_chunks mc "
                    "JOIN materials m ON m.id=mc.material_id "
                    "WHERE mc.id=? AND m.project_id=? AND m.deleted_at IS NULL",
                    (chunk_id, block["project_id"]))).fetchone()
                kind = "file"
            if not row or (row[2] or "confirmed") == "rejected":
                continue                                    # 丢弃的素材不可用（方案 C：解析即可用）
            await db.execute(
                "INSERT INTO chapter_material_chunks (block_id, chunk_id, section, mode, chunk_kind) "
                "VALUES (?,?,?,?,?) ON CONFLICT(block_id, chunk_id) DO UPDATE SET "
                "section=excluded.section, mode=excluded.mode, chunk_kind=excluded.chunk_kind",
                (block_id, int(chunk_id), body.section, body.mode, kind))
            added += 1
        await db.commit()
    return {"added": added}


@router.delete('/blocks/{block_id}/materials/bound/{chunk_id}')
async def unbind(block_id: int, chunk_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM chapter_material_chunks WHERE block_id=? AND chunk_id=?", (block_id, chunk_id))
        await db.commit()
    return {"removed": True}


_CITE_IN_BODY = None


@router.get('/blocks/{block_id}/citations')
async def chapter_citations(block_id: int):
    """本章「引用清单」：正文里每处【来源：X】→ 对应素材、所在小节与段落、状态。"""
    import re as _re

    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        body = str(block["content"] or "")
        bound = await (await db.execute(
            "SELECT b.chunk_id, b.section, b.mode, COALESCE(b.chunk_kind,'file') "
            "FROM chapter_material_chunks b WHERE b.block_id=?", (block_id,))).fetchall()

    # 素材索引：label → {kind, 位置, 原文}
    labels: dict[str, dict] = {}
    for chunk_id, section, mode, kind in bound:
        if kind == "cite":
            row = await (await db.execute(
                "SELECT cc.heading, cc.content FROM citation_chunks cc WHERE cc.id=?",
                (abs(int(chunk_id)),))).fetchone()
            if row:
                labels[row[0][:40]] = {"kind": "web", "locator": row[0][:40],
                                       "excerpt": row[1][:200], "chunk_id": chunk_id}
        else:
            row = await (await db.execute(
                "SELECT mc.page, mc.content, m.filename FROM material_chunks mc "
                "JOIN materials m ON m.id=mc.material_id WHERE mc.id=?", (int(chunk_id),))).fetchone()
            if row:
                labels[(row[2] or "")[:40]] = {"kind": "upload",
                                               "locator": f"{row[2]} 第 {row[0]} 页",
                                               "excerpt": row[1][:200], "chunk_id": chunk_id}

    # 正文引用点：按小节 + 段落
    points: list[dict] = []
    section = ""
    paragraph = 0
    for line in body.splitlines():
        head = _re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if head:
            section, paragraph = head.group(1).strip(), 0
            continue
        if not line.strip() or line.strip().startswith("|"):
            continue
        paragraph += 1
        for match in _re.finditer(r"【来源[:：]([^】]{1,60})】", line):
            label = match.group(1).strip()
            info = labels.get(label[:40]) or {}
            if not info:
                if any(word in label for word in ("招标文件", "技术规范", "需求文件", "采购文件")):
                    info = {"kind": "upload", "locator": label, "excerpt": ""}
                elif _re.search(r"(法|条例|办法|规定|标准|规范)$", label) or label.startswith("中华人民共和国"):
                    info = {"kind": "doc", "locator": label, "excerpt": ""}
                elif "http" in label or "www." in label:
                    info = {"kind": "web", "locator": label, "excerpt": ""}
            points.append({
                "section": section, "paragraph": paragraph, "source_label": label,
                "kind": info.get("kind") or "unknown",
                "locator": info.get("locator") or label,
                "excerpt": info.get("excerpt") or "",
                "chunk_id": info.get("chunk_id"),
                "quote": line.strip()[:60],
                "state": "ok" if info else "unverified",
            })
    # 未标来源：含数字/政策名但该段没有【来源】
    missing: list[dict] = []
    section, paragraph = "", 0
    for line in body.splitlines():
        head = _re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if head:
            section, paragraph = head.group(1).strip(), 0
            continue
        if not line.strip() or line.strip().startswith("|"):
            continue
        paragraph += 1
        if "【来源" in line:
            continue
        if _re.search(r"(\d+(?:\.\d+)?\s*%|《[^》]{2,30}》|等级保护|标准号|GB/T)", line):
            missing.append({"section": section, "paragraph": paragraph,
                            "quote": line.strip()[:70], "state": "missing"})
    by_kind = {"upload": 0, "web": 0, "doc": 0, "unknown": 0}
    for point in points:
        by_kind[point["kind"]] = by_kind.get(point["kind"], 0) + 1
    return {"points": points, "missing": missing[:20],
            "summary": {"citations": len(points), "missing": len(missing), "by_kind": by_kind}}
