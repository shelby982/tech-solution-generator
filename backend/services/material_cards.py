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


async def file_card(db, chunk_id: int, *, block_id: int = 0, section: str = "",
                    requirement_id: str = "", mode: str = "cite") -> dict | None:
    row = await (await db.execute(
        "SELECT mc.id, mc.content, mc.page, mc.status, m.id, m.filename, m.role "
        "FROM material_chunks mc JOIN materials m ON m.id=mc.material_id "
        "WHERE mc.id=? AND m.deleted_at IS NULL", (int(chunk_id),))).fetchone()
    if not row:
        return None
    authority, confidence = _authority(row[6], "file")
    return {
        "chunk_id": int(row[0]), "signed_id": int(row[0]), "chunk_kind": "file",
        "source_type": "upload", "text": str(row[1] or ""), "page": row[2] or 0,
        "source_title": row[5] or "", "source_id": int(row[4] or 0),
        "locator": f"{row[5] or '素材'}" + (f" 第 {row[2]} 页" if row[2] else ""),
        "authority": authority, "confidence": confidence,
        "status": str(row[3] or "confirmed"), "block_id": int(block_id or 0),
        "section": section, "requirement_id": requirement_id, "mode": mode, "used": bool(block_id),
    }


async def web_card(db, citation_chunk_id: int, *, block_id: int = 0, section: str = "",
                   requirement_id: str = "", mode: str = "cite") -> dict | None:
    row = await (await db.execute(
        "SELECT cc.id, cc.content, cc.heading, cc.status, c.id, c.title, c.url, c.domain "
        "FROM citation_chunks cc JOIN citations c ON c.id=cc.citation_id WHERE cc.id=?",
        (int(citation_chunk_id),))).fetchone()
    if not row:
        return None
    authority, confidence = _web_authority(row[7])
    return {
        "chunk_id": int(row[0]), "signed_id": -int(row[0]), "chunk_kind": "cite",
        "source_type": "web", "text": str(row[1] or ""), "page": 0,
        "source_title": row[2] or row[5] or row[7] or "公开来源", "source_id": str(row[4] or ""),
        "locator": row[2] or row[5] or row[7] or "公开来源",
        "authority": authority, "confidence": confidence,
        "status": str(row[3] or "confirmed"), "block_id": int(block_id or 0),
        "section": section, "requirement_id": requirement_id, "mode": mode, "used": bool(block_id),
        "url": row[6] or "", "domain": row[7] or "",
    }


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
