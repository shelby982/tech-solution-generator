"""GET /api/projects/{id}/blocks 的 run 归属过滤。

block_id 是位置派生的（parse 给 s1..sN，目录派生给 s1/s1.1），两次 run 的顶层 id
会撞车、行在同一个 project_id 下长期共存。接口默认只返回当前（最新一次）run 的
行；从未跑过 run 的老项目退回返回全部行，避免页面突然全空。
"""

from __future__ import annotations

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import init_db
from routes import blocks as blocks_routes
from services.block_store import upsert_outline_block


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "blocks.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("测试",))
    pid = cursor.lastrowid
    await conn.commit()
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    application = FastAPI()
    application.include_router(blocks_routes.router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test",
    ) as client:
        yield conn, pid, client
    await conn.close()


async def _run(conn, pid, tid, created_at, block_title):
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, created_at) VALUES (?, ?, ?)",
        (tid, pid, created_at),
    )
    await upsert_outline_block(
        conn, pid, "s1", level=2, title=block_title, order_idx=0, run_thread_id=tid,
    )


async def test_explicit_thread_id_returns_only_that_run(env):
    conn, pid, client = env
    await _run(conn, pid, "run-old", "2026-01-01 00:00:00", "上一版目录")
    await _run(conn, pid, "run-new", "2026-02-01 00:00:00", "这一版目录")

    r = await client.get(f"/api/projects/{pid}/blocks", params={"threadId": "run-old"})

    assert r.status_code == 200
    assert [b["title"] for b in r.json()] == ["上一版目录"]


async def test_defaults_to_latest_run(env):
    conn, pid, client = env
    await _run(conn, pid, "run-old", "2026-01-01 00:00:00", "上一版目录")
    await _run(conn, pid, "run-new", "2026-02-01 00:00:00", "这一版目录")

    r = await client.get(f"/api/projects/{pid}/blocks")

    assert [b["title"] for b in r.json()] == ["这一版目录"]
    assert r.json()[0]["run_thread_id"] == "run-new"


async def test_falls_back_to_legacy_rows_when_run_has_none(env):
    """老项目：行没有 run 归属、当前 run 又还没落库 → 退回显示全部，页面不全空。"""
    conn, pid, client = env
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id) VALUES (?, ?)", ("run-new", pid),
    )
    await upsert_outline_block(conn, pid, "s1", level=2, title="老行", order_idx=0)
    await conn.commit()

    r = await client.get(f"/api/projects/{pid}/blocks")

    assert [b["title"] for b in r.json()] == ["老行"]
