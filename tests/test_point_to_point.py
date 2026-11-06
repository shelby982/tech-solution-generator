"""技术规范书点对点应答模板：原文顺序、标准列、证据映射、可落库。"""
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import chapter_studio
from services.block_store import upsert_outline_block
from services.point_to_point import collect_clauses, render_table


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('点对点')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("p2p-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s3", level=1, title="技术规范书点对点应答", order_idx=0,
                                   run_thread_id="p2p-thread")
        await upsert_outline_block(db, pid, "s3.1", level=2, title="1. 总则", order_idx=1,
                                   run_thread_id="p2p-thread")
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=2,
                                   run_thread_id="p2p-thread")
        await db.commit()
        root = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=? AND block_id='s3'", (pid,))).fetchone())
        child = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=? AND block_id='s3.1'", (pid,))).fetchone())
        front = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=? AND block_id='s1'", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET spec_requirement_ids=? WHERE id=?",
                         (json.dumps(["sr-p1", "sr-p2"]), child["id"]))
        await db.execute("UPDATE blocks SET spec_requirement_ids=?, content=? WHERE id=?",
                         (json.dumps(["sr-p1"]), "## 技术方案\n\n对应章节正文。", front["id"]))
        for idx, (rid, title, text, level) in enumerate([
            ("sr-p1", "总则 > 技术要求1", "系统应支持高可靠部署。", "mandatory"),
            ("sr-p2", "总则 > 技术要求2", "系统应支持弹性扩展。", "normal"),
        ], 1):
            await db.execute("INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,mandatory_level,order_idx) "
                             "VALUES(?,?,?,?,?,?,?,?)", (pid, "p2p-thread", rid, "技术", title, text, level, idx))
        await db.commit()
        root = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (root["id"],))).fetchone())
    return pid, root


async def test_collect_and_render_template():
    pid, root = await _seed()
    async with get_db() as db:
        data = await collect_clauses(db, pid, "p2p-thread", "s3")
    assert [c["requirement_id"] for c in data["clauses"]] == ["sr-p1", "sr-p2"]
    rendered = render_table(data["clauses"])
    assert rendered["count"] == 2
    assert "招标条款号/章节" in rendered["markdown"]
    assert "完全响应" in rendered["markdown"]
    assert "技术方案" in rendered["markdown"]          # 前部关联章节作为证明
    assert "★" in rendered["markdown"]                  # 实质性条款标记
    assert rendered["rows"][0]["clause_no"] != rendered["rows"][1]["clause_no"]
    assert "技术要求1" in rendered["rows"][0]["clause_no"]


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(chapter_studio.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_point_to_point_api_preview_and_save(client):
    pid, root = await _seed()
    preview = (await client.post(f"/api/blocks/{root['id']}/point-to-point", json={"save": False})).json()
    assert preview["count"] == 2 and preview["saved"] is False
    saved = (await client.post(f"/api/blocks/{root['id']}/point-to-point", json={"save": True})).json()
    assert saved["saved"] is True
    async with get_db() as db:
        row = await (await db.execute("SELECT content FROM blocks WHERE id=?", (root["id"],))).fetchone()
    assert "技术规范书点对点应答表" in row["content"]
