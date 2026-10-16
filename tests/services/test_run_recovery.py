"""启动对账：区分"进程中途死掉"与"正常等在门上"。

用户遇到的现象：某个运行的 stage 停在 points_review，与"正等用户确认评分点"完全同形，
但最新检查点落在**干活节点**（没有 gate_ 检查点）—— 说明进程死在两个节点之间。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.run_recovery import mark_interrupted_runs, node_from_metadata


def test_node_from_metadata():
    assert node_from_metadata('{"source": "loop", "writes": {"zhang_heng_outline_draft": {"stage": "points_review"}}}') == "zhang_heng_outline_draft"
    assert node_from_metadata('{"source": "loop", "writes": {"gate_outline": {"stage": "outline_review"}}}') == "gate_outline"
    assert node_from_metadata("") == ""
    assert node_from_metadata(None) == ""


CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT,
    type TEXT, checkpoint BLOB, metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)
"""


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "rec.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute(CHECKPOINTS_DDL)      # 由 LangGraph saver 建表，测试里手工建
    await conn.commit()
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.commit()
    yield conn
    await conn.close()


async def _run(db, tid, stage, node):
    await db.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage) VALUES (?, 1, ?)", (tid, stage))
    await db.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
        "VALUES (?, '', ?, 'json', '{}', ?)",
        (tid, f"cp-{tid}", '{"source": "loop", "writes": {"%s": {"stage": "%s"}}}' % (node, stage)))
    await db.commit()


async def test_marks_worker_node_but_not_gate(db):
    await _run(db, "dead", "points_review", "zhang_heng_outline_draft")   # 死在中途
    await _run(db, "parked", "outline_review", "gate_outline")            # 正常等在门
    await _run(db, "cancelled", "aborted", "zhang_heng_parse")            # 用户取消：终态

    marked = await mark_interrupted_runs(db)
    assert marked == 1

    rows = {r["thread_id"]: r for r in await (await db.execute(
        "SELECT thread_id, interrupted_at, interrupted_node FROM workflow_runs")).fetchall()}
    assert rows["dead"]["interrupted_node"] == "zhang_heng_outline_draft"
    assert rows["dead"]["interrupted_at"], "死在中途的运行要被标记"
    assert not rows["parked"]["interrupted_at"], "等在门上的运行不能动"
    assert not rows["cancelled"]["interrupted_at"], "用户取消是终态，不能动"


async def test_idempotent(db):
    await _run(db, "dead", "points_review", "zhang_heng_parse")
    assert await mark_interrupted_runs(db) == 1
    assert await mark_interrupted_runs(db) == 0, "重复启动不应重复标记"
