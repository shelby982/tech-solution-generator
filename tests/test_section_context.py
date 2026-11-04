"""写作上下文：标包规范/计划要求/本节正文/素材必须进 Prompt。"""
import json

from db import get_db
from services.block_store import upsert_outline_block
from services.section_context import build_section_context, coverage_for_text, render_context


async def test_section_context_includes_spec_plan_body_and_material():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('上下文测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id, package_no) VALUES(?,?,?)",
                         ("ctx-thread", pid, "PKG-01"))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0,
                                   run_thread_id="ctx-thread")
        await db.commit()
        block = dict(await (await db.execute(
            "SELECT * FROM blocks WHERE project_id=? AND block_id='s1'", (pid,))).fetchone())
        await db.execute("UPDATE blocks SET content=?, spec_requirement_ids=? WHERE id=?",
                         ("## 总体架构\n\n当前节已有内容：采用云原生架构。\n\n## 下一节\n\n其他内容。",
                          json.dumps(["sr-ctx-1"]), block["id"]))
        await db.execute(
            "INSERT INTO spec_requirements(project_id,run_thread_id,requirement_id,topic,title,text,order_idx) "
            "VALUES(?,?,?,?,?,?,?)",
            (pid, "ctx-thread", "sr-ctx-1", "项目内容", "云原生架构要求",
             "采用 Kubernetes 服务网关、注册中心、配置中心，并实现熔断限流。", 1))
        plan = {"chapters": [{"block_id": "s1", "title": "技术方案",
                              "requirements": [{"id": "r1", "text": "写清楚云原生架构"}]}],
                "thread_id": "ctx-thread"}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("ctx-plan", pid, "ctx-thread", "s1", "confirmed", json.dumps(plan)))
        mat = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                "VALUES(?,?,?,?,?)", (pid, "技术规范.docx", "docx", "source", "done"))).lastrowid
        await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page) VALUES(?,?,?,?)",
                         (mat, 0, "云原生平台应采用 Kubernetes，配置服务网关、注册中心和配置中心。", 12))
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE id=?", (block["id"],))).fetchone())

    context = await build_section_context(block, {
        "title": "总体架构", "points": ["采用云原生架构"],
        "basis": [{"type": "clause", "ref": "云原生架构要求"}],
    }, "ctx-thread")
    assert context["spec_clauses"][0]["requirement_id"] == "sr-ctx-1"
    assert "Kubernetes" in context["spec_clauses"][0]["text"]
    assert context["chapter_requirements"][0]["text"] == "写清楚云原生架构"
    assert "当前节已有内容" in context["section_text"]
    assert "下一节" not in context["section_text"]
    assert context["materials"] and "Kubernetes" in context["materials"][0]["content"]
    text = render_context(context)
    assert "本章技术规范条款" in text and "sr-ctx-1" in text
    assert "本章编写要求" in text
    assert "Kubernetes" in text


def test_coverage_flags_missing_spec_terms():
    context = {"spec_clauses": [{"requirement_id": "sr-1", "title": "云原生",
                                 "text": "采用 Kubernetes 服务网关、注册中心和配置中心，并实现熔断限流。"}]}
    good = coverage_for_text("采用 Kubernetes 服务网关和注册中心，实现熔断限流。", context)
    bad = coverage_for_text("本章按招标文件要求建设。", context)
    assert good["covered"] == 1 and good["missing"] == []
    assert bad["covered"] == 0 and bad["missing"] == ["sr-1"]


def test_section_specific_spec_selection_and_missing_detail():
    from services.section_context import _select_spec_for_section
    spec = [
        {"requirement_id": "sr-ddd", "title": "DDD 微服务边界",
         "text": "基于领域驱动设计划分微服务边界，形成核心稳定外围敏态。"},
        {"requirement_id": "sr-k8s", "title": "Kubernetes 服务治理",
         "text": "基于 Kubernetes 的服务网关、注册中心、配置中心与熔断限流。"},
        {"requirement_id": "sr-security", "title": "安全等保",
         "text": "落实网络安全等级保护要求与安全边界防护。"},
    ]
    picked = _select_spec_for_section(spec, {"title": "Kubernetes 服务治理", "points": ["服务网关与注册中心"]})
    assert picked and picked[0]["requirement_id"] == "sr-k8s"

    coverage = coverage_for_text("本章落实安全等保要求。", {"spec_clauses": spec[:1]})
    assert coverage["missing_detail"][0]["requirement_id"] == "sr-ddd"
