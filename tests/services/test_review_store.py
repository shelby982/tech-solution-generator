"""统一评审落库：chapter_reviews 当前态 + review_history + 旧 reviews 回填。"""

from __future__ import annotations

import json

import aiosqlite
import pytest

from db import init_db
from services.review_store import (
    append_history,
    combine_findings,
    load_current_for_thread,
    promote_to_final,
    upsert_chapter_review,
)


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "review.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


def _data(**over):
    base = {
        "tech_score": 90, "comp_score": 80, "score": 85, "satisfied": "partial",
        "criterion_text": "口径", "covered_points": ["a"], "missing_points": ["b"],
        "issues": [{"severity": "high", "point": "p", "agent": "wang_anshi"}],
        "needs_material": [],
    }
    base.update(over)
    return base


async def _block(db, code="s1", thread="t1"):
    await db.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await db.execute(
        "INSERT INTO blocks (project_id, block_id, content, run_thread_id, status) "
        "VALUES (1, ?, '正文', ?, 'generating')", (code, thread))
    row = await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=1 AND block_id=?", (code,))).fetchone()
    return dict(row)


def test_combine_findings_merges_agents_and_worst_satisfaction():
    out = combine_findings(
        {"score": 90, "satisfied": "satisfied", "issues": [{"point": "a", "severity": "low"}]},
        {"score": 60, "satisfied": "unsatisfied", "issues": [{"point": "b", "severity": "critical"}]},
    )
    assert out["score"] == 75
    assert out["satisfied"] == "satisfied"     # 与 chapter_review._combine 同口径
    assert [i["agent"] for i in out["issues"]] == ["bao_zheng", "wang_anshi"]


@pytest.mark.asyncio
async def test_upsert_history_and_promote(db):
    block = await _block(db)
    await upsert_chapter_review(
        db, block_pk=block["id"], project_id=1, thread_id="t1", stage="writing",
        data=_data(), versions={"content_version": "c1", "evidence_version": "e1"}, tier="fast")
    await append_history(
        db, block_pk=block["id"], project_id=1, thread_id="t1", stage="writing",
        data=_data(), tier="fast", versions={"content_version": "c1", "evidence_version": "e1"})

    row = await (await db.execute(
        "SELECT * FROM chapter_reviews WHERE block_id=?", (block["id"],))).fetchone()
    assert row["stage"] == "writing" and row["tier"] == "fast"
    assert json.loads(row["issues"])[0]["agent"] == "wang_anshi"
    hist = await (await db.execute(
        "SELECT stage, thread_id, content_version, evidence_version FROM review_history WHERE block_id=?",
        (block["id"],))).fetchone()
    assert (hist["stage"], hist["thread_id"]) == ("writing", "t1")
    assert (hist["content_version"], hist["evidence_version"]) == ("c1", "e1")

    await promote_to_final(db, block_pk=block["id"], thread_id="t1")
    row = await (await db.execute(
        "SELECT stage FROM chapter_reviews WHERE block_id=?", (block["id"],))).fetchone()
    assert row["stage"] == "final"

    rows = await load_current_for_thread(db, 1, "t1")
    assert rows and rows[0]["block_code"] == "s1"


@pytest.mark.asyncio
async def test_backfill_legacy_reviews_is_idempotent(db):
    from services.review_migration import backfill_review_tables

    block = await _block(db)
    await db.execute(
        "INSERT INTO reviews (thread_id, block_id, agent, score, issues, satisfied) "
        "VALUES ('t1', 's1', 'wang_anshi', 70, '[]', 'partial')")
    await db.execute(
        "INSERT INTO reviews (thread_id, block_id, agent, score, issues, satisfied) "
        "VALUES ('t1', 's1', 'bao_zheng', 80, '[]', 'satisfied')")
    await db.commit()

    assert await backfill_review_tables(db) == 1
    row = await (await db.execute(
        "SELECT stage, score, tech_score, comp_score FROM chapter_reviews WHERE block_id=?",
        (block["id"],))).fetchone()
    assert dict(row) == {"stage": "final", "score": 75, "tech_score": 70, "comp_score": 80}
    assert await backfill_review_tables(db) == 0
