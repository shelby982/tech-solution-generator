"""Shared chapter roles and version fingerprints for writing and review."""
from __future__ import annotations

import hashlib
import json

from services.chapter_studio import content_hash, writing_units


def node_scopes(blocks: list[dict]) -> dict[int, dict]:
    units = {block['id'] for block in writing_units(blocks)}
    scopes = {}
    for index, block in enumerate(blocks):
        children = []
        for child in blocks[index + 1:]:
            if int(child.get('level') or 1) <= int(block.get('level') or 1):
                break
            if child['id'] in units:
                children.append(child)
        scopes[block['id']] = {
            'role': 'content' if block['id'] in units else 'structure',
            'unit_ids': [child['id'] for child in children],
            'pending_ids': [child['id'] for child in children if not str(child.get('content') or '').strip()],
        }
    return scopes


async def evidence_hash_many(db, blocks: list[dict], *, cache: dict | None = None) -> dict[int, str]:
    """批量版 evidence_hash：cards/materials 各一次查询，specs/points/facts 走 cache。"""
    from services.material_cards import bound_cards_many
    ids = [int(b["id"]) for b in blocks if b.get("id") is not None]
    if not ids:
        return {}
    cache = cache if cache is not None else {}
    cards_by = await bound_cards_many(db, ids)
    materials: dict[int, list] = {bid: [] for bid in ids}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        marks = ",".join("?" for _ in part)
        rows = await (await db.execute(
            "SELECT cm.block_id, m.id,m.filename,m.deleted_at,m.parse_status,cm.confirmed,"
            "c.id,c.content,c.status "
            "FROM chapter_materials cm JOIN materials m ON m.id=cm.material_id "
            "LEFT JOIN material_chunks c ON c.material_id=m.id "
            f"WHERE cm.block_id IN ({marks}) ORDER BY cm.block_id,m.id,c.id", part)).fetchall()
        for row in rows:
            materials.setdefault(int(row[0]), []).append(list(row[1:]))

    async def _thread_payload(tid: str) -> tuple[list, list]:
        key = ("specs", tid)
        if key not in cache:
            cache[key] = await (await db.execute(
                "SELECT requirement_id,title,text,mandatory_level FROM spec_requirements "
                "WHERE run_thread_id=? ORDER BY requirement_id", (tid,))).fetchall()
        key2 = ("points", tid)
        if key2 not in cache:
            cache[key2] = await (await db.execute(
                "SELECT point_id,factor,item,criterion,points FROM review_points "
                "WHERE run_thread_id=? ORDER BY point_id", (tid,))).fetchall()
        return cache[key], cache[key2]

    out: dict[int, str] = {}
    for block in blocks:
        bid = int(block["id"])
        tid = block.get("run_thread_id") or ""
        pid = block.get("project_id")
        specs, points = await _thread_payload(tid)
        key = ("facts", pid)
        if key not in cache:
            cache[key] = await (await db.execute(
                "SELECT facts FROM project_writing_facts WHERE project_id=?", (pid,))).fetchone()
        facts = cache[key]
        cards = sorted(cards_by.get(bid, []),
                       key=lambda item: (item["signed_id"], item["section"], item["requirement_id"]))
        payload = {
            "facts": facts[0] if facts else "",
            "cards": cards,
            "materials": materials.get(bid, []),
            "specs": [list(row) for row in specs],
            "points": [list(row) for row in points],
            "requirements": [block.get(k) for k in
                              ("requirement", "review_point_ids", "spec_requirement_ids", "evidence_required")],
        }
        out[bid] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return out


async def evidence_hash(db, block: dict, *, cache: dict | None = None) -> str:
    hashes = await evidence_hash_many(db, [block], cache=cache)
    return hashes[int(block["id"])]


async def versions(db, block: dict, *, cache: dict | None = None) -> dict:
    return {'content_version': content_hash(block.get('content')),
            'evidence_version': await evidence_hash(db, block, cache=cache)}


async def versions_many(db, blocks: list[dict], *, cache: dict | None = None) -> dict[int, dict]:
    evidence = await evidence_hash_many(db, blocks, cache=cache)
    return {int(b["id"]): {"content_version": content_hash(b.get("content")),
                           "evidence_version": evidence[int(b["id"])]} for b in blocks}


async def review_freshness(db, block: dict, review: dict, *, cache: dict | None = None) -> str:
    if not review.get('content_version') or not review.get('evidence_version'):
        return 'unknown'
    # 先比内容哈希（便宜）；内容已变就不必再算 evidence_hash（贵）。
    if str(review.get('content_version')) != content_hash(block.get('content')):
        return 'stale'
    if str(review.get('evidence_version')) != await evidence_hash(db, block, cache=cache):
        return 'stale'
    return 'current'


async def review_freshness_many(db, blocks: list[dict], reviews_by_block: dict, *,
                                cache: dict | None = None) -> dict[int, str]:
    """批量 freshness：内容先比；仅内容相同的章批量算 evidence_hash。"""
    out: dict[int, str] = {}
    pending: list[dict] = []
    review_by_id: dict[int, dict] = {}
    for block in blocks:
        bid = int(block["id"])
        review = reviews_by_block.get(block.get("block_id")) or reviews_by_block.get(bid)
        if not review:
            out[bid] = "unknown"
            continue
        if not review.get("content_version") or not review.get("evidence_version"):
            out[bid] = "unknown"
            continue
        if str(review.get("content_version")) != content_hash(block.get("content")):
            out[bid] = "stale"
            continue
        pending.append(block)
        review_by_id[bid] = review
    if pending:
        versions_by_id = await versions_many(db, pending, cache=cache)
        for block in pending:
            bid = int(block["id"])
            out[bid] = ("current"
                        if str(review_by_id[bid].get("evidence_version"))
                        == versions_by_id[bid]["evidence_version"] else "stale")
    return out
