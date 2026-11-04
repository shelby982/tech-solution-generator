"""语义覆盖/评标核对。"""
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import chapter_studio as studio
from services.block_store import upsert_outline_block
from services import coverage_review
from services.coverage_review import deterministic_coverage, review_coverage

CLAUSE = ("基于 Kubernetes 的微服务架构，服务网关、注册中心、配置中心，"
          "并实现熔断限流、弹性伸缩、灰度发布与全链路监控。")


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('覆盖测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("cov-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="cov-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        await db.execute("INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,order_idx) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (pid, "cov-thread", "sr-cov", "技术", "云原生服务治理", CLAUSE, 1))
        await db.execute("UPDATE blocks SET spec_requirement_ids=?, content=? WHERE id=?",
                         (json.dumps(["sr-cov"]), "## 云原生服务治理\n\n" + CLAUSE, block["id"]))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    return block


async def test_deterministic_coverage_marks_clause():
    block = await _seed()
    good = await deterministic_coverage(block, "云原生服务治理", CLAUSE, "cov-thread")
    assert good["total"] == 1 and good["covered"] == 1
    assert good["semantic_covered"] == 1
    bad = await deterministic_coverage(block, "云原生服务治理", "本章按招标文件要求建设。", "cov-thread")
    assert bad["covered"] == 0 and bad["missing"] == ["sr-cov"]
    assert bad["missing_detail"][0]["requirement_id"] == "sr-cov"


async def test_deep_review_merges_llm_and_caches(monkeypatch):
    block = await _seed()

    async def fake_llm(block, section_title, text, clauses):
        return {"covered": ["sr-cov"], "partial": [], "missing": [], "reviewer": "llm",
                "evidence": {"sr-cov": "Kubernetes 服务网关"}}

    monkeypatch.setattr(coverage_review, "llm_review", fake_llm)
    result = await review_coverage(block, "云原生服务治理", CLAUSE, thread_id="cov-thread", deep=True)
    assert result["reviewer"] == "deterministic+llm" and result["llm_covered"] == 1
    async with get_db() as db:
        row = await (await db.execute("SELECT * FROM section_coverage_reviews WHERE block_id=?", (block["id"],))).fetchone()
    assert row and row["llm_json"] != "{}"


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(studio.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_coverage_route(client):
    block = await _seed()
    resp = await client.post(f"/api/blocks/{block['id']}/studio/coverage-review",
                             json={"thread_id": "cov-thread", "section": "云原生服务治理", "text": CLAUSE})
    assert resp.status_code == 200
    assert resp.json()["covered"] == 1
