"""证据需求派生：规范/编写要求/评审补料 → 本节需要什么证据。"""
import json

from db import get_db
from services.block_store import upsert_outline_block
from services.evidence_needs import section_evidence_needs


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('证据需求')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("need-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="need-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET content=? WHERE id=?",
                         ("## 总体架构\n\n旧正文。", block["id"]))
        await db.execute("INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,order_idx) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (pid, "need-thread", "sr-need", "技术", "云原生要求",
                          "采用 Kubernetes 服务网关、注册中心、配置中心，并实现熔断限流。", 1))
        plan = {"chapters": [{"block_id": "s1", "title": "技术方案",
                              "requirements": [
                                  {"id": "r1", "text": "写清楚云原生架构", "source": "point"},
                                  {"id": "r2", "text": "技术规范条款：Kubernetes 服务治理",
                                   "source": "spec", "spec_requirement_id": "sr-need"},
                              ]}]}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("need-plan", pid, "need-thread", "s1", "confirmed", json.dumps(plan)))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())
    return block


async def test_section_needs_include_plan_and_spec_without_duplicate():
    block = await _seed()
    needs = await section_evidence_needs(block, {"title": "总体架构", "points": ["云原生"]}, "need-thread")
    assert needs
    texts = [item["text"] for item in needs]
    assert any("Kubernetes" in text for text in texts)
    assert all("写清楚" not in text for text in texts)
    assert sum(1 for item in needs if item.get("requirement_id") == "sr-need") == 1
    assert all(item["section"] == "总体架构" for item in needs)
    assert all(item["query"] for item in needs)
    assert all(item["covered"] is False for item in needs)


async def test_section_needs_cover_by_requirement_binding():
    block = await _seed()
    async with get_db() as db:
        await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page,status) "
                         "VALUES(1,0,'Kubernetes 服务治理片段。',3,'confirmed')")
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind,requirement_id) "
                         "VALUES(?,?,?,?,?,?)", (block["id"], 1, "总体架构", "data", "file", "sr-need"))
        await db.commit()
    needs = await section_evidence_needs(block, {"title": "总体架构"}, "need-thread")
    spec_need = next(item for item in needs if item.get("requirement_id") == "sr-need")
    assert spec_need["covered"] is True
    assert spec_need["evidence_count"] == 1


async def test_no_requirements_returns_empty():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('无需求')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("empty-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s9", level=1, title="空章", order_idx=0,
                                   run_thread_id="empty-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
    needs = await section_evidence_needs(block, {"title": "空节"}, "empty-thread")
    assert needs == []
