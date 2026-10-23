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
    # 素材 = 已选中的片段（status='selected'）
    status = "selected" if status in ("confirmed", "selected") else status
    sql = ("SELECT mc.id, mc.material_id, mc.chunk_index, mc.page, mc.content, mc.status,"
           "       m.filename, m.role FROM material_chunks mc JOIN materials m ON m.id=mc.material_id"
           " WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status=?")
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
            mapped = "selected" if state == "confirmed" else ("candidate" if state == "pending" else state)
            file_count = (await (await db.execute(
                "SELECT COUNT(*) FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                "WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status=?",
                (project_id, mapped))).fetchone())[0]
            cite_count = (await (await db.execute(
                "SELECT COUNT(*) FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                "WHERE c.project_id=? AND cc.status=?", (project_id, mapped))).fetchone())[0]
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
    mode: str = Field(default="cite", pattern="^(cite|data|context|paste)$")


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
                    "WHERE cc.id=? AND c.project_id=? AND cc.status<>'invalid'", (-int(chunk_id), block["project_id"]))).fetchone()
                kind = "cite"
            else:
                row = await (await db.execute(
                    "SELECT mc.id, mc.material_id, mc.status FROM material_chunks mc "
                    "JOIN materials m ON m.id=mc.material_id "
                    "WHERE mc.id=? AND m.project_id=? AND m.deleted_at IS NULL AND mc.status<>'invalid'",
                    (chunk_id, block["project_id"]))).fetchone()
                kind = "file"
            if not row or str(row[2] or "") not in ("selected", "confirmed"):
                continue                                    # 只有"已选为素材"的片段可应用
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


# ── 素材出处（我的文件 / 联网来源）与片段挑选（片段=素材）──────────────────
@router.get('/projects/{project_id}/material-sources')
async def material_sources(project_id: int, kind: str = "file"):
    """素材的两类出处：file（我上传的文件）/ web（联网来源）。"""
    kind = "web" if kind == "web" else "file"
    async with get_db() as db:
        if kind == "file":
            rows = await (await db.execute(
                "SELECT m.id, m.filename, m.role, m.parse_status, "
                "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id AND c.status<>'invalid') AS chunks, "
                "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id AND c.status='selected') AS picked "
                "FROM materials m WHERE m.project_id=? AND m.deleted_at IS NULL ORDER BY m.id DESC",
                (project_id,))).fetchall()
            items = [{"id": r[0], "title": r[1], "sub": r[2] or "", "status": r[3],
                      "chunks": r[4], "picked": r[5]} for r in rows]
        else:
            rows = await (await db.execute(
                "SELECT c.id, c.title, c.domain, c.published_at, "
                "(SELECT COUNT(*) FROM citation_chunks cc WHERE cc.citation_id=c.id AND cc.status<>'invalid') AS chunks, "
                "(SELECT COUNT(*) FROM citation_chunks cc WHERE cc.citation_id=c.id AND cc.status='selected') AS picked "
                "FROM citations c WHERE c.project_id=? ORDER BY c.created_at DESC",
                (project_id,))).fetchall()
            items = [{"id": r[0], "title": r[1] or (r[2] or "网页"), "sub": (r[2] or "") + (f" · {r[3]}" if r[3] else ""),
                      "status": "done", "chunks": r[4], "picked": r[5]} for r in rows]
    return {"kind": kind, "items": items}


@router.get('/projects/{project_id}/material-sources/{kind}/{source_id}/chunks')
async def source_chunks(project_id: int, kind: str, source_id: str, q: str = "",
                        limit: int = 150, picked_only: bool = False):
    """某个出处下的片段（片段 = 素材候选；picked = 已选为素材）。"""
    kind = "web" if kind == "web" else "file"
    async with get_db() as db:
        if kind == "file":
            sql = ("SELECT mc.id, mc.chunk_index, mc.page, mc.content, mc.status, mc.path "
                   "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                   "WHERE mc.material_id=? AND m.project_id=? AND m.deleted_at IS NULL "
                   "AND mc.status<>'invalid'")
            args: list = [source_id, project_id]
        else:
            sql = ("SELECT cc.id, cc.chunk_index, 0, cc.content, cc.status, cc.path "
                   "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                   "WHERE cc.citation_id=? AND c.project_id=? AND cc.status<>'invalid'")
            args = [source_id, project_id]
        if q:
            sql += " AND COALESCE(" + ("mc.content" if kind == "file" else "cc.content") + ",'') LIKE ?"
            args.append(f"%{q}%")
        if picked_only:
            sql += " AND " + ("mc.status" if kind == "file" else "cc.status") + "='selected'"
        sql += " ORDER BY " + ("mc.chunk_index" if kind == "file" else "cc.chunk_index") + " LIMIT ?"
        args.append(max(1, min(300, limit)))
        rows = await (await db.execute(sql, args)).fetchall()
    return {"kind": kind, "source_id": source_id,
            "items": [{"chunk_id": r[0], "index": r[1], "page": r[2], "text": r[3],
                       "picked": str(r[4]) == "selected",
                       "path": (r[5] if len(r) > 5 else "") or "",
                       "chunk_kind": "cite" if kind == "web" else "file"} for r in rows]}


class PickBody(BaseModel):
    chunk_ids: list[int] = Field(default_factory=list)
    picked: bool = True
    source_kind: str = "file"


@router.post('/projects/{project_id}/material-sources/pick')
async def pick_chunks(project_id: int, body: PickBody):
    """勾选/取消：**片段即素材**（picked=True → status=selected）。"""
    kind = "web" if body.source_kind == "web" else "file"
    table = "citation_chunks" if kind == "web" else "material_chunks"
    status = "selected" if body.picked else "candidate"
    if not body.chunk_ids:
        return {"changed": 0}
    async with get_db() as db:
        placeholders = ",".join("?" for _ in body.chunk_ids)
        cur = await db.execute(f"UPDATE {table} SET status=? WHERE id IN ({placeholders}) AND status<>'invalid'",
                               [status, *body.chunk_ids])
        await db.commit()
        changed = cur.rowcount or 0
    return {"changed": changed, "status": status}


@router.delete('/projects/{project_id}/material-sources/{kind}/{source_id}')
async def delete_source(project_id: int, kind: str, source_id: str):
    """删除出处：文件软删 / 来源删除；**其下素材标失效（invalid），记录保留**。"""
    kind = "web" if kind == "web" else "file"
    async with get_db() as db:
        if kind == "file":
            try:
                file_id = int(source_id)
            except (TypeError, ValueError):
                raise HTTPException(422, "文件 ID 非法")
            await db.execute("UPDATE materials SET deleted_at=CURRENT_TIMESTAMP "
                             "WHERE id=? AND project_id=?", (file_id, project_id))
            await db.execute("UPDATE material_chunks SET status='invalid' WHERE material_id=?", (file_id,))
        else:
            await db.execute("UPDATE citation_chunks SET status='invalid' WHERE citation_id=?", (str(source_id),))
            await db.execute("DELETE FROM citations WHERE id=? AND project_id=?", (str(source_id), project_id))
        await db.commit()
    return {"removed": True, "materials": "invalidated"}


@router.post('/projects/{project_id}/materials/{material_id}/recut')
async def recut_one(project_id: int, material_id: int):
    """按新规则重新切片（保住已选素材，按内容指纹映射）。"""
    from services.chunk_quality import recut_material

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM materials WHERE id=? AND project_id=? AND deleted_at IS NULL",
            (material_id, project_id))).fetchone()
        if not row:
            raise HTTPException(404, "素材不存在")
        return await recut_material(db, material_id)


@router.post('/projects/{project_id}/materials/recut-all')
async def recut_all(project_id: int):
    """批量重新切片（一次性改好后，之后上传的素材自动走新规则）。"""
    from services.chunk_quality import recut_material

    results = []
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id FROM materials WHERE project_id=? AND deleted_at IS NULL AND parse_status='done'",
            (project_id,))).fetchall()
        for row in rows:
            results.append(await recut_material(db, row[0]))
    return {"results": results}


@router.get('/blocks/{block_id}/materials/recommend')
async def recommend_materials(block_id: int, section: str = "", limit: int = 10):
    """本章/本节推荐素材：关键词 + 类型 + 要点覆盖 + 来源可信度（可解释）。"""
    from services.material_match import recommend, section_profile

    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        project_id = int(block["project_id"])
        # 章节画像：优先用传入的节标题，否则用大纲第一节
        outline = []
        try:
            from services.writing_plans import chapter_plan
            plan = await chapter_plan(db, project_id, str(block["run_thread_id"] or ""), str(block["block_id"] or ""))
            outline = plan.get("outline") or []
        except Exception:
            plan = {}
        target = next((s for s in outline if str(s.get("title") or "") == section), None) if section else None
        target = target or (outline[0] if outline else {"title": str(block["title"] or "")})
        profile = section_profile(target, plan)

        # 候选池：未失效的片段（文件 + 网页），排除已选/已用
        rows = await (await db.execute(
            "SELECT mc.id, mc.content, mc.page, m.role, 'file' AS kind, m.filename, mc.tags "
            "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
            "WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status='candidate' "
            "UNION ALL "
            "SELECT cc.id, cc.content, 0, 'web', 'web', c.domain, cc.tags "
            "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
            "WHERE c.project_id=? AND cc.status='candidate' LIMIT 800",
            (project_id, project_id))).fetchall()
    import json as _json
    def _tags(raw):
        try:
            return _json.loads(raw or "[]") or []
        except Exception:
            return []
    pool = [{"chunk_id": r[0], "text": r[1], "page": r[2], "role": r[3],
             "source_kind": "web" if r[4] == "web" else "file", "source_title": r[5] or "",
             "tags": _tags(r[6])} for r in rows]
    picked = recommend(profile, pool, limit=max(1, min(30, limit)))
    return {"section": profile["title"], "types_wanted": profile["types_wanted"],
            "keywords": sorted(profile["keywords"])[:20],
            "items": picked, "pool": len(pool)}


@router.get('/blocks/{block_id}/citations/{chunk_kind}/{chunk_id}/excerpt')
async def citation_excerpt(block_id: int, chunk_kind: str, chunk_id: int):
    """引用点「看原文」：取片段原文 + 出处（页码 / URL）。"""
    async with get_db() as db:
        if chunk_kind == "cite":
            row = await (await db.execute(
                "SELECT cc.content, cc.heading, c.url, c.publisher, c.published_at "
                "FROM citation_chunks cc LEFT JOIN citations c ON c.id=cc.citation_id WHERE cc.id=?",
                (chunk_id,))).fetchone()
            if not row:
                raise HTTPException(404, "片段不存在")
            return {"text": row[0], "title": row[1], "url": row[2] or "",
                    "publisher": row[3] or "", "published_at": row[4] or "", "page": 0}
        row = await (await db.execute(
            "SELECT mc.content, mc.page, m.filename, m.id FROM material_chunks mc "
            "JOIN materials m ON m.id=mc.material_id WHERE mc.id=?", (chunk_id,))).fetchone()
    if not row:
        raise HTTPException(404, "片段不存在")
    return {"text": row[0], "title": row[2], "url": "", "publisher": "",
            "published_at": "", "page": row[1], "material_id": row[3]}


class FillCitationBody(BaseModel):
    section: str = ""
    quote: str = ""
    chunk_kind: str = "file"
    chunk_id: int = 0
    source_label: str = ""


@router.post('/blocks/{block_id}/citations/fill')
async def fill_citation(block_id: int, body: FillCitationBody):
    """把某处"未标来源"补成【来源：X】：定位原句 → 追加来源标注 → 写版本。"""
    from services.block_store import add_revision, update_block_content

    if not body.quote:
        raise HTTPException(422, "缺少原句")
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        label = body.source_label
        if not label and body.chunk_id:
            if body.chunk_kind == "cite":
                row = await (await db.execute(
                    "SELECT heading, content FROM citation_chunks WHERE id=?", (body.chunk_id,))).fetchone()
                label = (row[0] if row else "")[:40]
            else:
                row = await (await db.execute(
                    "SELECT mc.page, m.filename FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                    "WHERE mc.id=?", (body.chunk_id,))).fetchone()
                if row:
                    label = f"{row[1]}（第 {row[0]} 页）"[:60]
        label = (label or "招标文件").strip()
        before = str(block["content"] or "")
        quote = str(body.quote).strip()
        at = before.find(quote)
        if at < 0:
            raise HTTPException(409, "原句已变化，请重新核对")
        insert_at = at + len(quote)
        after = before[:insert_at] + f"【来源：{label}】" + before[insert_at:]
        await add_revision(db, block_id, before, summary="补充引用来源", source="ai")
        await update_block_content(db, block_id, after)
    return {"ok": True, "label": label, "content": after}


class SwapCitationBody(BaseModel):
    old_label: str = ""
    new_label: str = ""


@router.post('/blocks/{block_id}/citations/swap')
async def swap_citation(block_id: int, body: SwapCitationBody):
    """换来源：把正文里的【来源：旧】替换为【来源：新】。"""
    from services.block_store import add_revision, update_block_content

    if not body.old_label or not body.new_label:
        raise HTTPException(422, "缺少原/新来源")
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        before = str(block["content"] or "")
        old = f"【来源：{body.old_label}】"
        if old not in before:
            raise HTTPException(409, "正文里找不到该来源标注")
        after = before.replace(old, f"【来源：{body.new_label}】", 1)
        await add_revision(db, block_id, before, summary="替换引用来源", source="ai")
        await update_block_content(db, block_id, after)
    return {"ok": True, "content": after}


@router.get('/projects/{project_id}/materials/{material_id}/images')
async def material_images(project_id: int, material_id: int):
    """某文件里抽出的图片素材（配图注与页码）。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT i.id, i.page, i.caption, i.source, i.size_bytes FROM material_images i "
            "JOIN materials m ON m.id=i.material_id "
            "WHERE i.material_id=? AND m.project_id=? AND m.deleted_at IS NULL ORDER BY i.id",
            (material_id, project_id))).fetchall()
    return {"items": [{"id": r[0], "page": r[1], "caption": r[2] or "",
                       "source": r[3] or "", "size": r[4] or 0} for r in rows]}


@router.get('/studio/images/{image_id}/file')
async def image_file(image_id: int):
    """返回图片文件（给前端缩略图/插入正文用）。"""
    from fastapi.responses import FileResponse

    async with get_db() as db:
        row = await (await db.execute("SELECT file_path FROM material_images WHERE id=?",
                                      (image_id,))).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "图片不存在")
    from pathlib import Path as _Path
    path = _Path(row[0])
    if not path.exists():
        raise HTTPException(404, "图片文件缺失")
    return FileResponse(str(path))
