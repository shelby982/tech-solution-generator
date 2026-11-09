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


async def evidence_hash(db, block: dict) -> str:
    """Hash adopted evidence, its content/status, and chapter requirements."""
    from services.material_cards import bound_cards
    cards = await bound_cards(db, block['id'])
    materials = await (await db.execute(
        'SELECT m.id,m.filename,m.deleted_at,m.parse_status,cm.confirmed,c.id,c.content,c.status '
        'FROM chapter_materials cm JOIN materials m ON m.id=cm.material_id '
        'LEFT JOIN material_chunks c ON c.material_id=m.id WHERE cm.block_id=? ORDER BY m.id,c.id',
        (block['id'],))).fetchall()
    tid = block.get('run_thread_id') or ''
    specs = await (await db.execute(
        'SELECT requirement_id,title,text,mandatory_level FROM spec_requirements WHERE run_thread_id=? ORDER BY requirement_id',
        (tid,))).fetchall()
    points = await (await db.execute(
        'SELECT point_id,factor,item,criterion,points FROM review_points WHERE run_thread_id=? ORDER BY point_id',
        (tid,))).fetchall()
    facts = await (await db.execute('SELECT facts FROM project_writing_facts WHERE project_id=?', (block['project_id'],))).fetchone()
    payload = {
        'facts': facts[0] if facts else '',
        'cards': sorted(cards, key=lambda item: (item['signed_id'], item['section'], item['requirement_id'])),
        'materials': [list(row) for row in materials],
        'specs': [list(row) for row in specs], 'points': [list(row) for row in points],
        'requirements': [block.get(key) for key in ('requirement', 'review_point_ids', 'spec_requirement_ids', 'evidence_required')],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def versions(db, block: dict) -> dict:
    return {'content_version': content_hash(block.get('content')), 'evidence_version': await evidence_hash(db, block)}


async def review_freshness(db, block: dict, review: dict) -> str:
    if not review.get('content_version') or not review.get('evidence_version'):
        return 'unknown'
    current = await versions(db, block)
    return 'current' if all(review.get(key) == value for key, value in current.items()) else 'stale'
