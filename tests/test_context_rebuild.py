"""旧章节按新写作上下文重建。"""
import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import chapter_sse as write_routes
from services import chapter_sse, writing_settings
from services.block_store import upsert_outline_block
from services.section_context import SECTION_CONTEXT_VERSION


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('重建测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("rebuild-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="rebuild-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET content=? WHERE id=?",
                         ("## 节一\n\n旧正文。\n\n## 节二\n\n旧正文二。", block["id"]))
        plan = {"chapters": [{"block_id": "s1", "title": "技术方案", "outline": [
            {"title": "节一", "points": ["a"], "words": 100, "basis": [], "deliverables": []},
            {"title": "节二", "points": ["b"], "words": 100, "basis": [], "deliverables": []},
        ]}]}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("rebuild-plan", pid, "rebuild-thread", "s1", "confirmed", json.dumps(plan)))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    return block


async def test_stale_list_and_force_rebuild(monkeypatch):
    block = await _seed()
    app = FastAPI()
    app.include_router(write_routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = (await client.get("/api/projects/1/studio/context-rebuild",
                                    params={"thread_id": "rebuild-thread"})).json()
        # project id from block
        preview = (await client.get(f"/api/projects/{block['project_id']}/studio/context-rebuild",
                                    params={"thread_id": "rebuild-thread"})).json()
        assert preview["current_version"] == SECTION_CONTEXT_VERSION
        assert preview["count"] == 1
        dry = (await client.post(f"/api/projects/{block['project_id']}/studio/context-rebuild",
                                 json={"thread_id": "rebuild-thread", "dry_run": True})).json()
        assert dry["created"] == [] and dry["stale"]

    async def fake_stream(block, section, thread_id, model):
        yield f"## {section['title']}\n\n新正文：{section['title']}。" + "建设内容" * 20

    async def fake_resolve(db, project_id, block_id, role):
        return {"used": "test", "source": "test"}

    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)
    monkeypatch.setattr(writing_settings, "resolve_model", fake_resolve)
    async with get_db() as db:
        job = await chapter_sse.create_job(block["project_id"], "rebuild-thread", block["id"],
                                           force_rewrite=True)
    await chapter_sse.run_job(job)
    async with get_db() as db:
        saved = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
        job_row = await (await db.execute("SELECT * FROM chapter_writing_jobs WHERE id=?", (job,))).fetchone()
    print('JOB', dict(job_row))
    print('CONTENT', saved["content"][:300])
    assert "新正文" in saved["content"]
    assert int(saved["context_version"] or 0) == SECTION_CONTEXT_VERSION
    assert job_row["status"] == "done"


async def test_clean_rewrite_replaces_old_body(monkeypatch):
    block = await _seed()

    async def fake_stream(block, section, thread_id, model):
        yield "## 新章节\n\n这是全新正文。" + "建设内容" * 20

    async def fake_resolve(db, project_id, block_id, role):
        return {"used": "test", "source": "test"}

    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)
    monkeypatch.setattr(writing_settings, "resolve_model", fake_resolve)
    async with get_db() as db:
        job = await chapter_sse.create_job(block["project_id"], "rebuild-thread", block["id"],
                                           force_rewrite=True, clean_rewrite=True)
    await chapter_sse.run_job(job)
    async with get_db() as db:
        saved = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    assert "旧正文" not in saved["content"]
    assert "这是全新正文" in saved["content"]
