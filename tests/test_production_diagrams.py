"""生产级图模板：9 类结构化渲染 + 路由。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import diagram_assets as routes
from services import diagram_assets, diagram_design, production_diagrams
from services.block_store import upsert_outline_block

SPECS = {
    "layered": {"bands": [{"label": "业务层", "items": ["客户管理", "标签服务"]},
                          {"label": "应用层", "items": ["应用服务"]}]},
    "data_flow": {"stages": [{"label": "数据源", "items": ["业务系统"]},
                              {"label": "数据层", "items": ["数据仓库"]},
                              {"label": "应用", "items": ["分析服务"]}],
                  "edges": [{"from": "业务系统", "to": "数据仓库", "label": "采集"}]},
    "sequence": {"actors": [{"id": "u", "label": "用户"}, {"id": "a", "label": "应用"}],
                 "messages": [{"from": "u", "to": "a", "label": "请求"}]},
    "gantt": {"tasks": [{"name": "需求分析", "start": 0, "end": 2},
                         {"name": "上线", "start": 3, "end": 3, "milestone": True}]},
    "matrix": {"rows": [{"id": "r1", "label": "政策"}], "cols": [{"id": "c1", "label": "工作"}],
               "cells": [{"row": "r1", "col": "c1", "text": "落实"}]},
    "fishbone": {"effect": "可靠性不足", "categories": [{"name": "技术", "causes": ["单点"]}]},
    "pyramid": {"levels": [{"label": "目标", "items": ["高可靠"]}, {"label": "能力", "items": ["双活"]}]},
    "radar": {"metrics": [{"label": "可靠性", "current": 3, "target": 5, "max": 5},
                          {"label": "安全性", "current": 4, "target": 5, "max": 5},
                          {"label": "性能", "current": 2, "target": 4, "max": 5}]},
    "org": {"root": {"label": "项目组织", "children": [{"label": "技术组"}, {"label": "实施组"}]}},
}


def test_each_production_template_renders_accessible_svg():
    for template, spec in SPECS.items():
        html = production_diagrams.RENDERERS[template](spec, {
            "title": f"图 1-1 {production_diagrams.TEMPLATES[template]}",
            "claim": "结论示例", "score_refs": ["评分点"], "section": "章节", "evidence": ["依据"],
        })
        assert 'aria-labelledby="prod-title prod-desc"' in html, template
        assert "结论示例" in html or "结论" in html, template
        assert "<svg" in html and "</svg>" in html


@pytest.fixture
async def env(monkeypatch, tmp_path):
    monkeypatch.setattr(diagram_assets, "ROOT", tmp_path / "diagrams")
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('生产图测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("tech", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="技术方案", order_idx=0, run_thread_id="tech")
        await db.commit()
        block = await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone()
    queued = []
    monkeypatch.setattr(routes, "queue_job", queued.append)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, pid=pid, block=dict(block), queued=queued)


async def test_production_job_creates_asset(env, monkeypatch):
    async def fake_extract(config, template, block, brief):
        return SPECS["layered"]

    async def fake_render(html_path, png_path, scale=2):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return 1280, 400, {"texts": 5, "overflow": [], "overlaps": [], "shapes_overflow": []}

    async def fake_config(db, block):
        return SimpleNamespace(model="test", provider="openai"), {}

    monkeypatch.setattr(production_diagrams, "extract_spec", fake_extract)
    monkeypatch.setattr(diagram_design, "resolve_config", fake_config)
    monkeypatch.setattr("services.diagram_render.render_png", fake_render)
    created = await env.client.post(
        f"/api/blocks/{env.block['id']}/studio/production-diagrams",
        json={"template": "layered", "title": "图 1-1 分层架构", "text": "业务层和应用层。", "claim": "满足要求"},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    await diagram_design.run_job(job_id)
    data = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{job_id}")).json()
    assert data["status"] == "done" and data["asset_id"]
    assert data["checks"]["pipeline"] == "production"
    assert data["checks"]["template"] == "layered"


def test_template_element_checklist_is_complete():
    assert set(production_diagrams.TEMPLATE_ELEMENTS) == set(production_diagrams.TEMPLATES)


def test_layered_has_inter_layer_arrows_and_legend():
    html = production_diagrams.render_layered(SPECS["layered"], {
        "title": "分层图", "claim": "结论", "score_refs": ["评分点"], "section": "章节", "evidence": ["依据"],
    })
    assert "marker-end" in html and "调用" in html
    assert "图例" in html


def test_gantt_has_real_time_axis():
    html = production_diagrams.render_gantt({
        "timeline": {"start": "2026-01", "end": "2026-06", "unit": "月"},
        "tasks": [{"name": "设计", "start": "2026-01", "end": "2026-03"},
                  {"name": "上线", "start": "2026-06", "end": "2026-06", "milestone": True}],
    }, {"title": "甘特图", "claim": "结论", "score_refs": [], "section": "", "evidence": []})
    assert "2026-01" in html and "2026-06" in html
    assert "时间：" in html


def test_score_is_optional_not_required_for_templates():
    html = production_diagrams.render_layered(SPECS["layered"], {
        "title": "分层图", "claim": "结论", "score_refs": [], "section": "", "evidence": [],
    })
    assert "对应评分点" not in html
    assert not any("评分点" in item for items in production_diagrams.TEMPLATE_ELEMENTS.values() for item in items)
