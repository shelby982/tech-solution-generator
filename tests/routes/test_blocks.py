"""GET /api/projects/{id}/blocks 的 run 归属过滤。

block_id 是位置派生的（parse 给 s1..sN，目录派生给 s1/s1.1），两次 run 的顶层 id
会撞车、行在同一个 project_id 下长期共存。接口默认只返回当前（最新一次）run 的
行；从未跑过 run 的老项目退回返回全部行，避免页面突然全空。
"""

from __future__ import annotations

import json

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

    from routes import materials as materials_routes

    application = FastAPI()
    application.include_router(blocks_routes.router, prefix="/api")
    application.include_router(materials_routes.router, prefix="/api")
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


# ── PUT /api/blocks/{id}：提炼字段的局部更新 ──────────────
# 工作台的参考卡片是"改一个字段"的交互。旧实现只认 content / requirement，
# 其余 6 个字段的 PUT 被 pydantic 静默丢掉（后端 200 返回原值，只有前端本地副本变了，
# 刷新即回退）。这里锚住：给什么写什么，没给的不动。

async def _seed_block(conn, pid, tid="run-1", **matrix):
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id) VALUES (?, ?)", (tid, pid),
    )
    await upsert_outline_block(
        conn, pid, "s1", level=2, title="技术要求", order_idx=0,
        matrix={"requirement": "原要求", "key_points": "原要点",
                "veto_items": "原否决", "indicators": "原指标",
                "constraint_level": "mandatory"},
        run_thread_id=tid,
    )
    await conn.commit()
    row = await (await conn.execute(
        "SELECT id FROM blocks WHERE project_id = ? AND block_id = 's1'", (pid,),
    )).fetchone()
    return row["id"]


async def test_put_updates_single_matrix_field_without_clobbering(env):
    conn, pid, client = env
    block_id = await _seed_block(conn, pid)

    r = await client.put(f"/api/blocks/{block_id}", json={"key_points": "新要点"})

    assert r.status_code == 200
    assert r.json()["key_points"] == "新要点"
    # 其它字段必须原样保留（局部更新，不是整行覆盖）
    assert r.json()["requirement"] == "原要求"
    assert r.json()["veto_items"] == "原否决"
    assert r.json()["indicators"] == "原指标"
    assert r.json()["constraint_level"] == "mandatory"


async def test_put_updates_constraint_level(env):
    conn, pid, client = env
    block_id = await _seed_block(conn, pid)

    r = await client.put(f"/api/blocks/{block_id}", json={"constraint_level": "optional"})

    assert r.status_code == 200
    assert r.json()["constraint_level"] == "optional"
    assert r.json()["key_points"] == "原要点"      # 别的字段不受影响


async def test_put_rejects_unknown_constraint_level(env):
    """三选一枚举决定正文字数档，非法值必须 422 而不是写进库里。"""
    conn, pid, client = env
    block_id = await _seed_block(conn, pid)

    r = await client.put(f"/api/blocks/{block_id}", json={"constraint_level": "urgent"})

    assert r.status_code == 422
    row = await (await conn.execute(
        "SELECT constraint_level FROM blocks WHERE id = ?", (block_id,),
    )).fetchone()
    assert row["constraint_level"] == "mandatory"


# ── PUT /api/blocks/{id}/evidence：人工挂接 / 解绑 ──────────
# 自动归属（规则 + 语义）覆盖不到时，用户要能手动把一段素材挂到某条要求上。
# 两条硬约束：① 写库；② by='human' / locked=True（下一轮重匹配不能把它抹掉）。

class _FakeRunner:
    """只实现 set_chapter_evidence 的最小替身：真实 runner 要 checkpointer 与图，
    而这组测试要锚的是**路由契约**（404/409/参数透传）与**库写入**。"""

    def __init__(self, conn):
        self.conn = conn
        self.calls: list[dict] = []

    async def set_chapter_evidence(self, tid, bid, *, action, chunk_id=None, item_ids=None):
        self.calls.append({"tid": tid, "bid": bid, "action": action,
                           "chunk_id": chunk_id, "item_ids": item_ids})
        row = await (await self.conn.execute(
            "SELECT id FROM blocks WHERE project_id = ? AND block_id = ?", (1, bid),
        )).fetchone()
        if action == "unmount":
            rows = []
        else:
            rows = [{"chunk_id": chunk_id, "material_id": 41, "content": "原文",
                     "filename": "规格书.pdf", "locator": "第 2 段", "by": "human",
                     "locked": True, "item_ids": list(item_ids or [])}]
        await self.conn.execute("UPDATE blocks SET source = ? WHERE id = ?",
                                (json.dumps(rows, ensure_ascii=False), row["id"]))
        await self.conn.commit()
        return {"block_id": bid, "evidence": rows}


@pytest.fixture
async def env_with_runner(env, monkeypatch):
    conn, pid, client = env
    from routes import blocks as br
    from routes import workflow as wr

    runner = _FakeRunner(conn)
    monkeypatch.setattr(wr, "_runner", runner)
    # 路由里是 `from routes.workflow import get_runner`（函数内 import），替身注入到模块即可
    monkeypatch.setattr(br, "get_runner", lambda: runner, raising=False)
    await _seed_block(conn, pid)
    yield conn, pid, client, runner


async def test_mount_evidence_writes_source_and_passes_item_ids(env_with_runner):
    conn, pid, client, runner = env_with_runner
    row = await (await conn.execute("SELECT id FROM blocks WHERE block_id='s1'")).fetchone()

    r = await client.put(f"/api/blocks/{row['id']}/evidence",
                         json={"action": "mount", "chunk_id": 7, "item_ids": ["s1#R1"]})

    assert r.status_code == 200
    assert runner.calls[0]["bid"] == "s1"          # 传的是字符串 block_id，不是主键
    assert runner.calls[0]["chunk_id"] == 7 and runner.calls[0]["item_ids"] == ["s1#R1"]
    stored = await (await conn.execute(
        "SELECT source FROM blocks WHERE id=?", (row["id"],))).fetchone()
    sources = json.loads(stored["source"])
    assert len(sources) == 1
    assert sources[0]["by"] == "human" and sources[0]["locked"] is True
    assert sources[0]["item_ids"] == ["s1#R1"]


async def test_unmount_clears_source(env_with_runner):
    conn, pid, client, runner = env_with_runner
    row = await (await conn.execute("SELECT id FROM blocks WHERE block_id='s1'")).fetchone()
    await client.put(f"/api/blocks/{row['id']}/evidence",
                     json={"action": "mount", "chunk_id": 7})

    r = await client.put(f"/api/blocks/{row['id']}/evidence",
                         json={"action": "unmount", "chunk_id": 7})

    assert r.status_code == 200
    stored = await (await conn.execute("SELECT source FROM blocks WHERE id=?", (row["id"],))).fetchone()
    assert json.loads(stored["source"]) == []


async def test_evidence_rejects_unknown_block_and_bad_action(env_with_runner):
    conn, pid, client, runner = env_with_runner
    r = await client.put("/api/blocks/99999/evidence", json={"action": "mount", "chunk_id": 1})
    assert r.status_code == 404
    row = await (await conn.execute("SELECT id FROM blocks WHERE block_id='s1'")).fetchone()
    r2 = await client.put(f"/api/blocks/{row['id']}/evidence", json={"action": "explode", "chunk_id": 1})
    assert r2.status_code == 422      # 非法 action 由 pydantic 拦下（Literal）


# ── GET /api/projects/{id}/material-chunks（人工挂接的"找素材"选择器）──
# 只回我方素材：招标文件不该被我方当成"我方素材依据"（方案 §4.D 的用途受限）。

async def test_material_chunks_lists_only_ours_with_filter(env):
    conn, pid, client = env
    from services.block_store import create_chunk, create_material

    ours = await create_material(conn, project_id=pid, filename="规格书.pdf",
                                 type_="pdf", file_path="uploads/a", role="source")
    tender = await create_material(conn, project_id=pid, filename="招标文件.pdf",
                                   type_="pdf", file_path="uploads/b", role="spec")
    await create_chunk(conn, material_id=ours["id"], chunk_index=0,
                       content="服务团队\n项目经理 PMP 认证", page=8)
    await create_chunk(conn, material_id=ours["id"], chunk_index=1, content="性能\nTPS 1200")
    await create_chunk(conn, material_id=tender["id"], chunk_index=0, content="技术评分表\n技术方案 20 分")
    await conn.commit()

    r = await client.get(f"/api/projects/{pid}/material-chunks")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [c["filename"] for c in body["chunks"]] == ["规格书.pdf", "规格书.pdf"]
    assert body["chunks"][0]["page"] == 8
    assert body["chunks"][0]["preview"].startswith("服务团队")

    # q 过滤 + materialId 过滤
    assert r.json() and (await client.get(
        f"/api/projects/{pid}/material-chunks", params={"q": "TPS"})).json()["total"] == 1
    assert (await client.get(
        f"/api/projects/{pid}/material-chunks", params={"materialId": 999})).json()["total"] == 0
