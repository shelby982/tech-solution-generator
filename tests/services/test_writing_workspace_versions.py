"""批量版本哈希与单章版本必须完全一致。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.writing_workspace import review_freshness_many, versions, versions_many


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "versions.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await conn.execute(
        "INSERT INTO blocks (project_id, block_id, content, run_thread_id, status) "
        "VALUES (1, 's1', '正文一', 't1', 'generating')")
    await conn.execute(
        "INSERT INTO blocks (project_id, block_id, content, run_thread_id, status) "
        "VALUES (1, 's2', '正文二', 't1', 'generating')")
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_versions_many_matches_single(db):
    blocks = [dict(r) for r in await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=1 ORDER BY id")).fetchall()]
    single = {b["id"]: await versions(db, b) for b in blocks}
    batch = await versions_many(db, blocks)
    assert batch == single


@pytest.mark.asyncio
async def test_freshness_many_matches_single_logic(db):
    blocks = [dict(r) for r in await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=1 ORDER BY id")).fetchall()]
    vs = await versions_many(db, blocks)
    reviews = {}
    for b in blocks:
        reviews[b["block_id"]] = {
            "content_version": vs[b["id"]]["content_version"],
            "evidence_version": vs[b["id"]]["evidence_version"],
        }
    states = await review_freshness_many(db, blocks, reviews)
    assert states == {b["id"]: "current" for b in blocks}

    # 内容改过 → stale；缺版本 → unknown
    broken = dict(reviews)
    broken["s1"] = dict(reviews["s1"], content_version="old")
    del broken["s2"]
    states = await review_freshness_many(db, blocks, broken)
    assert states[blocks[0]["id"]] == "stale"
    assert states[blocks[1]["id"]] == "unknown"


@pytest.mark.asyncio
async def test_versions_many_matches_with_bound_cards(db):
    await db.execute(
        "INSERT INTO materials (project_id, filename, role, parse_status) "
        "VALUES (1, '技术规范书.pdf', 'spec', 'done')")
    mid = (await (await db.execute("SELECT last_insert_rowid()")).fetchone())[0]
    await db.execute(
        "INSERT INTO material_chunks (material_id, chunk_index, content) VALUES (?, 0, '片段内容')",
        (mid,))
    cid = (await (await db.execute("SELECT last_insert_rowid()")).fetchone())[0]
    blocks = [dict(r) for r in await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=1 ORDER BY id")).fetchall()]
    for b in blocks:
        await db.execute(
            "INSERT INTO chapter_material_chunks (block_id, chunk_id, chunk_kind, section, mode) "
            "VALUES (?, ?, 'file', '正文', 'cite')", (b["id"], cid))
    await db.commit()

    single = {b["id"]: await versions(db, b) for b in blocks}
    batch = await versions_many(db, blocks)
    assert batch == single
