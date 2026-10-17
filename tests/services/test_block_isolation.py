"""blocks 按 run 隔离：多 run 项目里，某条 run 被清空后**不能**退回别的 run 的行。

实测（project 29）：技术草案的 124 条 blocks 被新解析按项目清空后，
读取侧 fallback 把全部行（725）渲染出来，页面成了两份目录的混杂。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.block_store import list_blocks


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "iso.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.commit()
    yield conn
    await conn.close()


async def _run(db, tid):
    await db.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage) VALUES (?, 1, 'outline_review')", (tid,))
    await db.commit()


async def _block(db, tid, block_id):
    await db.execute(
        "INSERT INTO blocks (project_id, block_id, title, content, order_idx, run_thread_id, status) "
        "VALUES (1, ?, ?, 'x', 0, ?, 'done')", (block_id, block_id, tid))
    await db.commit()


async def test_multi_run_project_does_not_fall_back(db):
    """两条 run：请求的那条没有行 → 返回空，而不是另一条的行。"""
    await _run(db, "run-a")
    await _run(db, "run-b")
    await _block(db, "run-a", "a1")
    rows = await list_blocks(db, 1, "run-b", fallback_all=True)
    assert rows == []


async def test_single_run_project_still_falls_back(db):
    """老项目（只有一条 run）仍回退，避免页面突然全空。"""
    await _run(db, "only")
    await _block(db, "only", "x1")
    # 该 run 没有任何行（用不存在的 run id 模拟"还没落库"）
    rows = await list_blocks(db, 1, "not-yet", fallback_all=True)
    assert len(rows) == 1
