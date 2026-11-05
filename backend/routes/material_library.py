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
            "(SELECT COUNT(*) FROM material_chunks c WHERE c.material_id=m.id AND COALESCE(c.status,'confirmed')='confirmed') AS confirmed, "
            "COALESCE(m.parse_error,'') AS parse_error "
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
                       "chunks": r[4], "confirmed": r[5], "parse_error": r[6] or "",
                       "can_retry": str(r[3] or "") in ("failed", "pending")} for r in files]}


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


@router.get('/blocks/{block_id}/materials/evidence-board')
async def evidence_board(block_id: int, thread_id: str = ""):
    """素材页主视图：按节聚合需求/已绑定证据/覆盖摘要。"""
    from services.evidence_needs import section_evidence_needs
    from services.material_cards import bound_cards, card, public_dto
    from services.material_match import recommend, section_profile
    from services.writing_plans import plan_of_block
    from routes.plan_materials import _pool
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        block = dict(block)
        found = await plan_of_block(db, int(block.get("project_id") or 0),
                                    thread_id or str(block.get("run_thread_id") or ""),
                                    str(block.get("block_id") or ""))
        outline = []
        if found:
            chapter = next((item for item in found[1].get("chapters") or []
                            if str(item.get("block_id")) == str(block.get("block_id"))), {})
            outline = chapter.get("outline") or []
        sections = []
        total_needs = total_covered = 0
        pool = await _pool(db, int(block.get("project_id") or 0))
        # 无大纲：回退到章节级需求，不伪造节名，也不显示成“0 需求”
        chapter_level = not outline
        if chapter_level:
            outline = [{"title": "", "chapter_level": True}]
        for section in outline:
            title = str(section.get("title") or "")
            chapter_flag = bool(section.get("chapter_level"))
            cards = [public_dto(item) for item in await bound_cards(db, block_id, title)]
            needs = await section_evidence_needs(block, section, thread_id or str(block.get("run_thread_id") or ""))
            active = [item for item in needs if not item.get("ignored")]
            total_needs += len(active)
            total_covered += sum(1 for item in active if item.get("covered"))
            recommend_items = []
            active_needs = [item for item in needs if not item.get("ignored")]
            if active_needs:
                try:
                    profile_section = section if not chapter_flag else {"title": str(block.get("title") or ""), "points": []}
                    profile = section_profile(profile_section, found[1] if found else {})
                    need_text = " ".join(str(item.get("text") or "") for item in active_needs)
                    profile["raw"] = (str(profile.get("raw") or "") + " " + need_text).strip()
                    from services.material_match import _terms, _expand
                    profile["keywords"] = set(profile.get("keywords") or set()) | _expand(_terms(need_text))
                    for picked in recommend(profile, pool, limit=3, min_score=0.12):
                        kind = "cite" if picked.get("source_kind") == "web" else "file"
                        signed = -int(picked["chunk_id"]) if kind == "cite" else int(picked["chunk_id"])
                        item = await card(db, signed, kind, section=title)
                        if item:
                            need = active_needs[0]
                            item["matched_need_id"] = need.get("need_id") or ""
                            item["requirement_id"] = need.get("requirement_id") or ""
                            item["reason"] = (picked.get("reason") or "") + " · 对应：" + str(need.get("text") or "")[:32]
                            recommend_items.append(public_dto(item))
                except Exception:
                    recommend_items = []
            sections.append({"section": title, "chapter_level": chapter_flag,
                             "needs": needs, "cards": cards,
                             "recommend": recommend_items,
                             "summary": {"needs": len(active),
                                         "covered": sum(1 for item in active if item.get("covered")),
                                         "ignored": sum(1 for item in needs if item.get("ignored")),
                                         "cards": len(cards)}})
    from services.citations import search_configured
    return {
        "sections": sections,
        "search_available": search_configured(),
        "summary": {"needs": total_needs, "covered": total_covered,
                    "cards": sum(len(item["cards"]) for item in sections),
                    "coverage": round(total_covered / total_needs, 3) if total_needs else None},
    }


@router.post('/blocks/{block_id}/materials/needs/action')
async def need_action(block_id: int, body: NeedActionBody):
    """忽略/恢复一条证据需求；持久化到 evidence_need_actions。"""
    async with get_db() as db:
        block = await (await db.execute("SELECT id FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        if body.action == "restore":
            await db.execute(
                "DELETE FROM evidence_need_actions WHERE block_id=? AND section=? AND need_id=?",
                (block_id, body.section, body.need_id))
        else:
            await db.execute(
                "INSERT INTO evidence_need_actions(block_id,section,need_id,action,reason,updated_at) "
                "VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(block_id,section,need_id) DO UPDATE SET "
                "action=excluded.action,reason=excluded.reason,updated_at=CURRENT_TIMESTAMP",
                (block_id, body.section, body.need_id, body.action, body.reason))
        await db.commit()
    return {"ok": True, "action": body.action, "need_id": body.need_id}


@router.get('/blocks/{block_id}/materials/evidence')
async def evidence(block_id: int, section: str = "", thread_id: str = ""):
    """本节证据：需求 + 已绑定证据卡 + 覆盖摘要。"""
    from services.evidence_needs import section_evidence_needs
    from services.material_cards import bound_cards, public_dto
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
        if not block:
            raise HTTPException(404, "章节不存在")
        cards = await bound_cards(db, block_id, section)
    block = dict(block)
    needs = await section_evidence_needs(block, {"title": section}, thread_id or str(block.get("run_thread_id") or ""))
    cards = [public_dto(item) for item in cards]
    from services.citations import search_configured
    return {
        "section": section, "needs": needs, "cards": cards,
        "search_available": search_configured(),
        "summary": {
            "needs": len([item for item in needs if not item.get("ignored")]),
            "covered": sum(1 for item in needs if item.get("covered") and not item.get("ignored")),
            "cards": len(cards),
            "coverage": (round(sum(1 for item in needs if item.get("covered") and not item.get("ignored")) /
                               max(1, len([item for item in needs if not item.get("ignored")])), 3)
                         if any(not item.get("ignored") for item in needs) else None),
            "missing": [item.get("text") for item in needs if not item.get("covered") and not item.get("ignored")][:5],
        },
    }


@router.get('/blocks/{block_id}/materials/bound')
async def bound(block_id: int):
    """本章已引用的素材（片段级）。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT b.chunk_id, mc.material_id, b.section, b.mode, 1, "
            "       mc.chunk_index, mc.page, mc.content, m.filename, COALESCE(b.chunk_kind,'file'), "
            "       COALESCE(b.requirement_id,'') "
            "FROM chapter_material_chunks b JOIN material_chunks mc ON mc.id=b.chunk_id "
            "LEFT JOIN materials m ON m.id=mc.material_id "
            "WHERE b.block_id=? AND COALESCE(b.chunk_kind,'file')='file'",
            (block_id,))).fetchall()
        cite_rows = await (await db.execute(
            "SELECT b.chunk_id, 0, b.section, b.mode, 1, cc.chunk_index, 0, cc.content, cc.heading, 'cite', "
            "       COALESCE(b.requirement_id,'') "
            "FROM chapter_material_chunks b JOIN citation_chunks cc ON cc.id=ABS(b.chunk_id) "
            "WHERE b.block_id=? AND b.chunk_kind='cite'", (block_id,))).fetchall()
        rows = list(rows) + list(cite_rows)
    return {"items": [{"chunk_id": r[0], "material_id": r[1], "section": r[2] or "", "mode": r[3] or "cite",
                       "confirmed": bool(r[4]), "index": r[5], "page": r[6],
                       "text": r[7] or "", "filename": r[8] or "", "chunk_kind": r[9] or "file",
                       "requirement_id": r[10] or ""}
                      for r in rows]}


class NeedActionBody(BaseModel):
    section: str = ""
    need_id: str = Field(min_length=1, max_length=80)
    action: str = Field(default="ignore", pattern="^(ignore|restore)$")
    reason: str = Field(default="", max_length=300)


class BindBody(BaseModel):
    chunk_ids: list[int] = Field(default_factory=list)
    material_id: int = 0
    section: str = ""
    requirement_id: str = ""
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
                "INSERT INTO chapter_material_chunks (block_id, chunk_id, section, mode, chunk_kind, requirement_id) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(block_id, chunk_id) DO UPDATE SET "
                "section=excluded.section, mode=excluded.mode, chunk_kind=excluded.chunk_kind, "
                "requirement_id=excluded.requirement_id",
                (block_id, int(chunk_id), body.section, body.mode, kind, body.requirement_id))
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


def _chunk_item(row, kind: str) -> dict:
    import json as _json

    def _tags(raw):
        try:
            return _json.loads(raw or "[]") or []
        except Exception:
            return []

    return {"chunk_id": row[0], "index": row[1], "page": row[2], "text": row[3],
            "picked": str(row[4]) == "selected",
            "path": (row[5] if len(row) > 5 else "") or "",
            "tags": _tags(row[6]) if len(row) > 6 else [],
            "chunk_kind": "cite" if kind == "web" else "file"}


@router.get('/projects/{project_id}/material-sources/{kind}/{source_id}/chunks')
async def source_chunks(project_id: int, kind: str, source_id: str, q: str = "",
                        limit: int = 150, picked_only: bool = False,
                        include_boilerplate: bool = False):
    """某个出处下的片段（片段 = 素材候选；picked = 已选为素材）。"""
    kind = "web" if kind == "web" else "file"
    async with get_db() as db:
        if kind == "file":
            sql = ("SELECT mc.id, mc.chunk_index, mc.page, mc.content, mc.status, mc.path, mc.tags "
                   "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                   "WHERE mc.material_id=? AND m.project_id=? AND m.deleted_at IS NULL "
                   "AND mc.status<>'invalid'")
            args: list = [source_id, project_id]
        else:
            sql = ("SELECT cc.id, cc.chunk_index, 0, cc.content, cc.status, cc.path, cc.tags "
                   "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                   "WHERE cc.citation_id=? AND c.project_id=? AND cc.status<>'invalid'")
            args = [source_id, project_id]
        if q:
            sql += " AND COALESCE(" + ("mc.content" if kind == "file" else "cc.content") + ",'') LIKE ?"
            args.append(f"%{q}%")
        if picked_only:
            sql += " AND " + ("mc.status" if kind == "file" else "cc.status") + "='selected'"
        if not include_boilerplate:
            column = "mc.tags" if kind == "file" else "cc.tags"
            sql += f" AND COALESCE({column},'') NOT LIKE '%boilerplate%'"
        sql += " ORDER BY " + ("mc.chunk_index" if kind == "file" else "cc.chunk_index") + " LIMIT ?"
        args.append(max(1, min(300, limit)))
        rows = await (await db.execute(sql, args)).fetchall()
    return {"kind": kind, "source_id": source_id,
            "items": [_chunk_item(r, kind) for r in rows]}


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


@router.post('/projects/{project_id}/materials/auto-pick')
async def auto_pick(project_id: int, per_type: int = 8, min_length: int = 80, max_length: int = 900,
                    keep_existing: bool = True, apply: bool = False):
    """智能备料（高质量预筛）：把"正文型片段"批量勾选为素材。

    规则（只要正文型，不要表格/表单/碎片）：
      · 长度 80~900 字（可配）
      · 含句子（。或；）—— 排除清单碎片
      · 排除 boilerplate（格式/资格/封面）
      · 按类型配额（policy/data/clause/qualification/table），每类最多 per_type 条
    """
    import json as _json

    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT mc.id, mc.content, mc.tags, mc.status, m.role, mc.path FROM material_chunks mc "
            "JOIN materials m ON m.id=mc.material_id "
            "WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status<>'invalid'",
            (project_id,))).fetchall()

    from services.chunk_quality import substance_score

    # 按"来源章节"过滤：招投标程序/合同/格式类章节对写正文无用，技术需求/评分/工作项类才有用
    BAD_PATH = ("投标人须知", "评标办法", "合同条款", "合同格式", "格式", "附件", "招标公告",
                "投标邀请", "保证金", "前附表", "投标文件编制", "开标", "废标", "无效投标")
    GOOD_PATH = ("技术规范", "技术要求", "需求", "工作项", "评分", "服务内容", "交付",
                 "指标", "功能", "架构", "安全", "实施", "运维", "培训", "业绩")

    QUOTA = (("policy", per_type), ("data", per_type), ("clause", per_type),
             ("qualification", max(3, per_type // 2)), ("table", max(3, per_type // 2)))
    BANNED = ("boilerplate", "procedural", "empty_table")
    candidates: dict[str, list[tuple[float, int]]] = {}
    for chunk_id, content, tags_raw, status, role, path in rows:
        if not keep_existing and str(status) == "selected":
            continue
        try:
            tags = _json.loads(tags_raw or "[]") or []
        except Exception:
            tags = []
        if any(tag in tags for tag in BANNED):
            continue
        text = str(content or "")
        if not (min_length <= len(text) <= max_length):
            continue
        if "。" not in text and "；" not in text:
            continue
        primary = next((tag for tag, _ in QUOTA if tag in tags), "")
        if not primary:
            continue
        source_path = str(path or "")
        if any(word in source_path for word in BAD_PATH):
            continue
        score = substance_score(text)
        if source_path and any(word in source_path for word in GOOD_PATH):
            score += 0.15                      # 技术/需求类章节优先
        if score < 0.15:
            continue
        candidates.setdefault(primary, []).append((score, int(chunk_id)))

    counts: dict[str, int] = {}
    picked: list[int] = []
    for tag, limit in QUOTA:
        ranked = sorted(candidates.get(tag, []), reverse=True)[:limit]
        if not ranked:
            continue
        counts[tag] = len(ranked)
        picked.extend(chunk_id for _, chunk_id in ranked)

    if picked and apply:
        async with get_db() as db:
            placeholders = ",".join("?" for _ in picked)
            await db.execute(
                f"UPDATE material_chunks SET status='selected' WHERE id IN ({placeholders}) "
                f"AND status<>'invalid'", picked)
            await db.commit()

    # 返回候选明细（供前端预览/逐条确认）
    detail = []
    if picked:
        async with get_db() as db:
            placeholders = ",".join("?" for _ in picked)
            found = await (await db.execute(
                f"SELECT id, content, tags, path, page FROM material_chunks WHERE id IN ({placeholders})",
                picked)).fetchall()
        for row in found:
            detail.append({"chunk_id": row[0], "text": str(row[1] or "")[:200],
                           "tags": _json.loads(row[2] or "[]") or [],
                           "path": row[3] or "", "page": row[4] or 0})
    return {"picked": len(picked), "by_type": counts, "total_pool": len(rows),
            "applied": bool(apply), "items": detail}


@router.get('/projects/{project_id}/materials/source-diagnosis')
async def source_diagnosis(project_id: int):
    """料源诊断：告诉你"现在手里的文件能不能备出好素材"。

    回答三件事：
      ① 可用（正文型）多少段、占比多少
      ② 程序性/格式类占多少（这些写正文用不上）
      ③ 建议补什么料（技术规范书 / 评分标准 / 我方业绩资质）
    """
    import json as _json

    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT mc.content, mc.tags, mc.status FROM material_chunks mc "
            "JOIN materials m ON m.id=mc.material_id "
            "WHERE m.project_id=? AND m.deleted_at IS NULL AND mc.status<>'invalid'",
            (project_id,))).fetchall()
        files = await (await db.execute(
            "SELECT filename, role, parse_status FROM materials WHERE project_id=? AND deleted_at IS NULL",
            (project_id,))).fetchall()

    total = len(rows)
    usable = procedural = boilerplate = empty_table = selected = 0
    for content, tags_raw, status in rows:
        try:
            tags = _json.loads(tags_raw or "[]") or []
        except Exception:
            tags = []
        text = str(content or "")
        if str(status) == "selected":
            selected += 1
        if "boilerplate" in tags:
            boilerplate += 1
        elif "procedural" in tags:
            procedural += 1
        elif "empty_table" in tags:
            empty_table += 1
        elif 80 <= len(text) <= 900 and ("。" in text or "；" in text):
            usable += 1
    ratio = round(usable / total * 100) if total else 0
    suggestions = []
    if not total:
        suggestions.append("还没有上传文件：先上传招标文件/技术规范书/评分标准，再备料")
    if ratio < 25:
        suggestions.append(f"可用正文型片段只占 {ratio}%：现有文件以招投标程序/合同条款为主，"
                           "建议补充「技术规范书」「评分标准/评分办法」「我方业绩与资质」")
    if boilerplate and boilerplate / max(1, total) > 0.3:
        suggestions.append("格式/范本类片段偏多（写到正文里没意义），备料时建议勾选「排除表单」")
    if selected == 0:
        suggestions.append("素材池还是空的：用「智能备料（建议）」一键筛出可用片段，再逐条确认")
    return {"total": total, "usable": usable, "usable_ratio": ratio, "procedural": procedural,
            "boilerplate": boilerplate, "empty_table": empty_table, "selected": selected,
            "files": [{"filename": r[0], "role": r[1], "status": r[2]} for r in files],
            "suggestions": suggestions}


@router.get('/blocks/{block_id}/materials/needs')
async def chapter_material_needs(block_id: int):
    """评审意见里"需补材料"的条目 → 本章需要补什么材料（issue → 素材联动）。"""
    import json as _json

    from services.chapter_review import load_review

    review = await load_review(block_id)
    if not review:
        return {"count": 0, "items": [], "reviewed": False}
    items = []
    for issue in (review.get("needs_material") or []):
        query = str(issue.get("material_query") or "").strip()
        point = str(issue.get("point") or "").strip()
        items.append({
            "query": query or point[:60],           # 检索关键词
            "reason": point[:120],                  # 为什么需要
            "severity": issue.get("severity") or "medium",
        })
    # 按 query 去重
    seen, unique = set(), []
    for item in items:
        key = item["query"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"count": len(unique), "items": unique[:8], "reviewed": True}


@router.get('/projects/{project_id}/materials/needs')
async def project_material_needs(project_id: int):
    """项目级：全章节评审汇总"需要补的材料"（去重、按次数排序）。"""
    import json as _json

    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT block_id, needs_material FROM chapter_reviews WHERE project_id=?", (project_id,))).fetchall()
        blocks = await (await db.execute(
            "SELECT id, title, block_id FROM blocks WHERE project_id=? AND COALESCE(block_id,'')<>''",
            (project_id,))).fetchall()
    titles = {int(r[0]): (r[1] or r[2]) for r in blocks}
    counter: dict[str, dict] = {}
    for block_pk, raw in rows:
        try:
            needs = _json.loads(raw or "[]") or []
        except Exception:
            needs = []
        for issue in needs:
            query = str(issue.get("material_query") or issue.get("point") or "").strip()[:60]
            if not query:
                continue
            entry = counter.setdefault(query, {"query": query, "reason": str(issue.get("point") or "")[:120],
                                               "severity": issue.get("severity") or "medium",
                                               "chapters": [], "count": 0})
            entry["count"] += 1
            if len(entry["chapters"]) < 5:
                entry["chapters"].append(titles.get(int(block_pk), str(block_pk)))
    ordered = sorted(counter.values(), key=lambda item: item["count"], reverse=True)
    return {"count": len(ordered), "items": ordered[:12]}
