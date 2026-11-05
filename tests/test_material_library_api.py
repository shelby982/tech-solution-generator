"""素材证据 API：本节需求 + 证据卡 + 绑定 requirement_id。"""
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import material_library, plan_materials
from services.block_store import upsert_outline_block


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('素材API')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("api-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="api-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET content=?, spec_requirement_ids=? WHERE id=?",
                         ("## 节一\n\n正文。", json.dumps(["sr-api"]), block["id"]))
        await db.execute("INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,order_idx) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (pid, "api-thread", "sr-api", "技术", "Kubernetes 服务治理",
                          "采用 Kubernetes 服务网关、注册中心、配置中心。", 1))
        plan = {"chapters": [{"block_id": "s1", "title": "技术方案",
                              "outline": [{"title": "节一", "points": ["云原生"], "words": 300,
                                           "basis": [], "deliverables": []}],
                              "requirements": [{"id": "r1", "text": "提供 Kubernetes 服务治理案例", "source": "point"}]}]}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("api-plan", pid, "api-thread", "s1", "confirmed", json.dumps(plan)))
        mid = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                "VALUES(?,?,?,?,?)", (pid, "技术规范.docx", "docx", "spec", "done"))).lastrowid
        chunk = (await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page,status) "
                                  "VALUES(?,?,?,?,?)", (mid, 0, "Kubernetes 服务网关与注册中心。", 8, "selected"))).lastrowid
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind,requirement_id) "
                         "VALUES(?,?,?,?,?,?)", (block["id"], chunk, "节一", "data", "file", "sr-api"))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    return block, chunk


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(material_library.router, prefix="/api")
    app.include_router(plan_materials.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_evidence_api_contract(client):
    block, chunk = await _seed()
    resp = await client.get(f"/api/blocks/{block['id']}/materials/evidence",
                            params={"section": "节一", "thread_id": "api-thread"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["needs"] >= 2
    assert data["summary"]["covered"] >= 1
    assert data["cards"][0]["source_type"] == "upload"
    assert data["cards"][0]["requirement_id"] == "sr-api"
    assert data["cards"][0]["confidence_label"] in ("高", "中", "待确认")
    assert data["needs"][0]["section"] == "节一"
    assert data["search_available"] is False


async def test_suggestions_use_needs_and_cap_three(client):
    block, chunk = await _seed()
    resp = await client.get(f"/api/blocks/{block['id']}/plan/material-suggestions",
                            params={"per_section": 3})
    assert resp.status_code == 200
    section = resp.json()["sections"][0]
    assert section["needs"]
    assert len(section["candidates"]) <= 3


async def test_evidence_board_groups_by_section(client):
    block, chunk = await _seed()
    resp = await client.get(f"/api/blocks/{block['id']}/materials/evidence-board",
                            params={"thread_id": "api-thread"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sections"] and data["sections"][0]["section"] == "节一"
    assert data["sections"][0]["cards"]
    assert data["summary"]["needs"] >= 2
    recs = data["sections"][0]["recommend"]
    assert all(item.get("matched_need_id") for item in recs)


async def test_bind_requirement_id(client):
    block, chunk = await _seed()
    resp = await client.post(f"/api/blocks/{block['id']}/materials/bind", json={
        "chunk_ids": [chunk], "section": "节一", "mode": "data", "requirement_id": "r1",
    })
    assert resp.status_code == 200 and resp.json()["added"] == 1
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT requirement_id FROM chapter_material_chunks WHERE block_id=? AND chunk_id=?",
            (block["id"], chunk))).fetchone()
    assert row["requirement_id"] == "r1"


async def test_library_exposes_parse_error_and_retry(client):
    block, chunk = await _seed()
    async with get_db() as db:
        await db.execute("UPDATE materials SET parse_status='failed', parse_error='OCR 超时' WHERE project_id=?",
                         (block["project_id"],))
        await db.commit()
        row = await (await db.execute("SELECT id FROM materials WHERE project_id=?", (block["project_id"],))).fetchone()
        mid = row["id"]
    resp = await client.get(f"/api/projects/{block['project_id']}/materials/library")
    assert resp.status_code == 200
    item = next(f for f in resp.json()["files"] if f["id"] == mid)
    assert item["parse_status"] == "failed"
    assert item["parse_error"] == "OCR 超时"
    assert item["can_retry"] is True


async def test_need_action_ignore_and_restore(client):
    block, chunk = await _seed()
    data = (await client.get(f"/api/blocks/{block['id']}/materials/evidence",
                             params={"section": "节一", "thread_id": "api-thread"})).json()
    need = data["needs"][0]
    resp = await client.post(f"/api/blocks/{block['id']}/materials/needs/action",
                             json={"section": "节一", "need_id": need["need_id"],
                                   "action": "ignore", "reason": "非强制"})
    assert resp.status_code == 200
    after = (await client.get(f"/api/blocks/{block['id']}/materials/evidence",
                              params={"section": "节一", "thread_id": "api-thread"})).json()
    hit = next(item for item in after["needs"] if item["need_id"] == need["need_id"])
    assert hit["ignored"] is True and hit["status"] == "ignored"
    await client.post(f"/api/blocks/{block['id']}/materials/needs/action",
                      json={"section": "节一", "need_id": need["need_id"], "action": "restore"})
    restored = (await client.get(f"/api/blocks/{block['id']}/materials/evidence",
                                 params={"section": "节一", "thread_id": "api-thread"})).json()
    hit = next(item for item in restored["needs"] if item["need_id"] == need["need_id"])
    assert hit["ignored"] is False


async def test_board_falls_back_to_chapter_level_without_outline(client):
    block, chunk = await _seed()
    async with get_db() as db:
        plan = json.loads((await (await db.execute("SELECT plan FROM writing_plans WHERE id='api-plan'")).fetchone())["plan"])
        plan["chapters"][0]["outline"] = []
        await db.execute("UPDATE writing_plans SET plan=? WHERE id='api-plan'", (json.dumps(plan),))
        await db.commit()
    data = (await client.get(f"/api/blocks/{block['id']}/materials/evidence-board",
                             params={"thread_id": "api-thread"})).json()
    assert data["sections"][0]["chapter_level"] is True
    assert data["summary"]["needs"] >= 1
    assert data["summary"]["coverage"] is not None


async def test_board_zero_needs_has_no_fake_coverage(client):
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('无需求章节')")).lastrowid
        block = (await db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) "
                                  "VALUES(?,?,?,?,?)", (pid, "s-empty", "空章", "", "empty-board"))).lastrowid
        await db.commit()
    data = (await client.get(f"/api/blocks/{block}/materials/evidence-board",
                             params={"thread_id": "empty-board"})).json()
    assert data["summary"]["needs"] == 0
    assert data["summary"]["coverage"] is None
