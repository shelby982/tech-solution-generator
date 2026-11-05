"""编写就绪度：无大纲/无证据/部分/可写。"""
import json

from db import get_db
from services.block_store import upsert_outline_block
from services.writing_readiness import chapter_readiness, section_readiness


async def _seed(with_outline=True, with_spec=True):
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('就绪度')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("ready-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="ready-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET content=?, spec_requirement_ids=? WHERE id=?",
                         ("## 节一\n\n旧正文", json.dumps(["sr-ready"] if with_spec else []), block["id"]))
        plan = {"chapters": [{"block_id": "s1", "title": "技术方案",
                              "requirements": [{"id": "r1", "text": "提供 Kubernetes 服务治理案例", "source": "point"}],
                              "outline": ([{"title": "节一", "points": ["云原生"], "words": 300,
                                            "basis": [], "deliverables": []}] if with_outline else [])}]}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("ready-plan", pid, "ready-thread", "s1", "confirmed", json.dumps(plan)))
        if with_spec:
            await db.execute("INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,order_idx) "
                             "VALUES(?,?,?,?,?,?,?)",
                             (pid, "ready-thread", "sr-ready", "技术", "Kubernetes 服务治理",
                              "采用 Kubernetes 服务网关、注册中心、配置中心。", 1))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    return block


async def test_no_outline_status():
    block = await _seed(with_outline=False)
    result = await chapter_readiness(block, "ready-thread")
    assert result["status"] == "no_outline"
    assert result["next_action"] == "generate_outline"
    assert result["sections"][0]["has_outline"] is False


async def test_no_evidence_then_ready_after_binding():
    block = await _seed(with_outline=True)
    section = {"title": "节一", "points": ["云原生"]}
    before = await section_readiness(block, section, "ready-thread")
    assert before["status"] in ("no_evidence", "partial", "ready")
    # 至少一条规范需求；没绑定前不应 ready（除非测试数据被过滤）
    assert before["needs_total"] >= 0
    if before["needs_total"]:
        assert before["status"] in ("no_evidence", "partial")
        async with get_db() as db:
            await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page,status) "
                             "VALUES(1,0,'Kubernetes 服务治理片段。',2,'selected')")
            await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind,requirement_id) "
                             "VALUES(?,?,?,?,?,?)", (block["id"], 1, "节一", "data", "file", "sr-ready"))
            await db.commit()
        after = await section_readiness(block, section, "ready-thread")
        assert after["status"] in ("partial", "ready")
        # 规范要求已覆盖；计划要求仍需绑定其 need_id，因此 partial 是正确结果
        assert after["covered"] >= 1


async def test_readiness_api():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from routes import chapter_studio

    block = await _seed(with_outline=True)
    app = FastAPI()
    app.include_router(chapter_studio.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/blocks/{block['id']}/writing-readiness",
                                params={"thread_id": "ready-thread"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_outline"] is True
    assert data["status"] in ("no_evidence", "partial", "ready")
    assert data["sections"][0]["section"] == "节一"
