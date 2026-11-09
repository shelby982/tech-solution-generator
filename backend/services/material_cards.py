"""素材证据卡：把文件片段/网页片段归一成前端可展示、可追溯的 DTO。"""
from __future__ import annotations

import re

_AUTHORITY = {
    "main_rfp": ("招标文件", 1.0),
    "spec": ("技术规范书", 1.0),
    "scoring": ("评分标准", 1.0),
    "source": ("我方材料", 0.85),
    "web_official": ("官方公开来源", 0.9),
    "web": ("公开来源", 0.6),
}


def _authority(role: str, source_kind: str) -> tuple[str, float]:
    key = str(role or "")
    if source_kind == "web":
        return _AUTHORITY.get(key, ("公开来源", 0.6))
    return _AUTHORITY.get(key, ("我方材料", 0.85))


def _web_authority(domain: str) -> tuple[str, float]:
    domain = str(domain or "").lower()
    if domain.endswith(".gov.cn") or ".gov." in domain:
        return ("政府/官方来源", 0.95)
    if any(word in domain for word in ("std", "sac", "miit", "ndrc")):
        return ("标准/主管机构", 0.9)
    return ("公开来源", 0.6)


def _file_card_from_row(row, *, block_id=0, section="", requirement_id="", mode="cite") -> dict:
    authority, confidence = _authority(row[6], "file")
    return {
        "chunk_id": int(row[0]), "signed_id": int(row[0]), "chunk_kind": "file",
        "source_type": "upload", "text": str(row[1] or ""), "page": row[2] or 0,
        "source_title": row[5] or "", "source_id": int(row[4] or 0),
        "locator": f"{row[5] or '素材'}" + (f" 第 {row[2]} 页" if row[2] else ""),
        "authority": authority, "confidence": confidence,
        "status": str(row[3] or "confirmed"), "block_id": int(block_id or 0),
        "section": section, "requirement_id": requirement_id, "mode": mode,
        "used": bool(block_id),
    }


def _web_card_from_row(row, *, block_id=0, section="", requirement_id="", mode="cite") -> dict:
    authority, confidence = _web_authority(row[7])
    return {
        "chunk_id": int(row[0]), "signed_id": -int(row[0]), "chunk_kind": "cite",
        "source_type": "web", "text": str(row[1] or ""), "page": 0,
        "source_title": row[2] or row[5] or row[7] or "公开来源", "source_id": str(row[4] or ""),
        "locator": row[2] or row[5] or row[7] or "公开来源",
        "authority": authority, "confidence": confidence,
        "status": str(row[3] or "confirmed"), "block_id": int(block_id or 0),
        "section": section, "requirement_id": requirement_id, "mode": mode,
        "used": bool(block_id), "url": row[6] or "", "domain": row[7] or "",
    }


async def file_card(db, chunk_id: int, *, block_id: int = 0, section: str = "",
                    requirement_id: str = "", mode: str = "cite") -> dict | None:
    row = await (await db.execute(
        "SELECT mc.id, mc.content, mc.page, mc.status, m.id, m.filename, m.role "
        "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
        "WHERE mc.id=? AND m.deleted_at IS NULL", (int(chunk_id),))).fetchone()
    return _file_card_from_row(row, block_id=block_id, section=section,
                               requirement_id=requirement_id, mode=mode) if row else None

async def web_card(db, citation_chunk_id: int, *, block_id: int = 0, section: str = "",
                   requirement_id: str = "", mode: str = "cite") -> dict | None:
    row = await (await db.execute(
        "SELECT cc.id, cc.content, cc.heading, cc.status, c.id, c.title, c.url, c.domain "
        "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id WHERE cc.id=?",
        (int(citation_chunk_id),))).fetchone()
    return _web_card_from_row(row, block_id=block_id, section=section,
                              requirement_id=requirement_id, mode=mode) if row else None

async def card(db, chunk_id: int, kind: str = "file", **kwargs) -> dict | None:
    if str(kind) == "cite" or int(chunk_id) < 0:
        return await web_card(db, abs(int(chunk_id)), **kwargs)
    return await file_card(db, int(chunk_id), **kwargs)


async def bound_cards(db, block_id: int, section: str = "") -> list[dict]:
    sql = ("SELECT chunk_id, COALESCE(chunk_kind,'file'), section, mode, COALESCE(requirement_id,'') "
           "FROM chapter_material_chunks WHERE block_id=?")
    args: list = [int(block_id)]
    if section:
        sql += " AND section=?"
        args.append(str(section))
    rows = await (await db.execute(sql, args)).fetchall()
    out = []
    for chunk_id, kind, sec, mode, requirement_id in rows:
        item = await card(db, int(chunk_id), kind, block_id=block_id, section=sec or "",
                          requirement_id=requirement_id or "", mode=mode or "cite")
        if item:
            out.append(item)
    return out


def usable_card(item: dict) -> bool:
    return item["status"] in {"selected", "confirmed"} and bool(item["text"].strip())


def supports_need(item: dict, text: str) -> bool:
    if not usable_card(item) or item["mode"] not in {"cite", "data"}:
        return False
    bidder_evidence = re.search(r"业绩|资质|证书|合同|人员|团队|承诺|我方|投标人.*证明", text)
    return not bidder_evidence or (
        item["source_type"] == "upload" and item["authority"] == "我方材料"
    )


def confidence_label(value: float) -> str:
    if value >= 0.9:
        return "高"
    if value >= 0.7:
        return "中"
    return "待确认"


def public_dto(item: dict) -> dict:
    """去掉内部字段，补前端展示字段。"""
    out = dict(item or {})
    out["confidence_label"] = confidence_label(float(out.get("confidence") or 0))
    return out


async def bound_cards_many(db, block_ids: list[int], section: str = "") -> dict[int, list[dict]]:
    """批量取多章绑定的 cards；避免每章/每 chunk 单独查库（evidence_hash 批量路径）。"""
    ids = [int(x) for x in block_ids]
    out: dict[int, list[dict]] = {bid: [] for bid in ids}
    if not ids:
        return out
    meta: dict[int, list[tuple]] = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        marks = ",".join("?" for _ in part)
        sql = ("SELECT block_id, chunk_id, COALESCE(chunk_kind,'file'), section, mode, "
               "COALESCE(requirement_id,'') FROM chapter_material_chunks "
               f"WHERE block_id IN ({marks})")
        args = list(part)
        if section:
            sql += " AND section=?"
            args.append(str(section))
        for row in await (await db.execute(sql, args)).fetchall():
            meta.setdefault(int(row[0]), []).append(tuple(row))
    file_ids = sorted({int(row[1]) for rows in meta.values() for row in rows
                       if str(row[2]) != "cite" and int(row[1]) >= 0})
    web_ids = sorted({abs(int(row[1])) for rows in meta.values() for row in rows
                      if str(row[2]) == "cite" or int(row[1]) < 0})
    file_map = {}
    for start in range(0, len(file_ids), 500):
        part = file_ids[start:start + 500]
        marks = ",".join("?" for _ in part)
        for row in await (await db.execute(
                "SELECT mc.id, mc.content, mc.page, mc.status, m.id, m.filename, m.role "
                "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
                f"WHERE mc.id IN ({marks}) AND m.deleted_at IS NULL", part)).fetchall():
            file_map[int(row[0])] = row
    web_map = {}
    for start in range(0, len(web_ids), 500):
        part = web_ids[start:start + 500]
        marks = ",".join("?" for _ in part)
        for row in await (await db.execute(
                "SELECT cc.id, cc.content, cc.heading, cc.status, c.id, c.title, c.url, c.domain "
                "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id "
                f"WHERE cc.id IN ({marks})", part)).fetchall():
            web_map[int(row[0])] = row
    for bid, rows in meta.items():
        cards = []
        for _block_id, chunk_id, kind, sec, mode, requirement_id in rows:
            cid = int(chunk_id)
            if str(kind) == "cite" or cid < 0:
                row = web_map.get(abs(cid))
                item = _web_card_from_row(row, block_id=bid, section=sec or "",
                                          requirement_id=requirement_id or "",
                                          mode=mode or "cite") if row else None
            else:
                row = file_map.get(cid)
                item = _file_card_from_row(row, block_id=bid, section=sec or "",
                                           requirement_id=requirement_id or "",
                                           mode=mode or "cite") if row else None
            if item:
                cards.append(item)
        out[bid] = cards
    return out
