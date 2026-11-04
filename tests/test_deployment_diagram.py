"""生产级部署拓扑：结构化 spec → 确定性 SVG/PNG。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import diagram_assets as routes
from services import diagram_assets, diagram_design, deployment_diagram
from services.block_store import upsert_outline_block


SPEC = {
    "zones": [
        {"id": "internet", "label": "互联网", "kind": "internet"},
        {"id": "dmz", "label": "DMZ", "kind": "dmz"},
        {"id": "app", "label": "应用区", "kind": "app"},
        {"id": "data", "label": "数据区", "kind": "data"},
    ],
    "nodes": [
        {"id": "user", "label": "外部用户", "zone": "internet", "kind": "user"},
        {"id": "waf", "label": "WAF", "zone": "dmz", "kind": "security"},
        {"id": "lb", "label": "负载均衡", "zone": "dmz", "kind": "gateway"},
        {"id": "app1", "label": "应用服务", "zone": "app", "kind": "server"},
        {"id": "db1", "label": "业务数据库", "zone": "data", "kind": "database"},
    ],
    "edges": [
        {"from": "user", "to": "waf", "kind": "call", "label": "HTTPS 443", "port": "443"},
        {"from": "waf", "to": "lb", "kind": "call", "label": "转发"},
        {"from": "lb", "to": "app1", "kind": "call", "label": "请求分发"},
        {"from": "app1", "to": "db1", "kind": "data", "label": "读写"},
    ],
    "claim": "双中心双活，业务 RTO ≤ 30 分钟。",
}


def test_render_is_deterministic_with_zones_nodes_edges():
    html = deployment_diagram.render_html(
        deployment_diagram.normalize_spec(SPEC), title="图 3-1 部署拓扑",
        score_refs=["技术架构先进性（10分）"], section="部署架构", evidence=["招标文件 5.2.3"],
    )
    assert 'aria-labelledby="deploy-title deploy-desc"' in html
    assert "互联网" in html and "数据区" in html
    assert "业务数据库" in html and "HTTPS 443" in html
    assert "arrow-call" in html and "arrow-data" in html
    assert "图 3-1 部署拓扑" in html


def test_invalid_spec_is_rejected():
    bad = dict(SPEC)
    bad["edges"] = [{"from": "missing", "to": "db1"}]
    with pytest.raises(deployment_diagram.DeploymentSpecError):
        deployment_diagram.normalize_spec(bad)


@pytest.fixture
async def env(monkeypatch, tmp_path):
    monkeypatch.setattr(diagram_assets, "ROOT", tmp_path / "diagrams")
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('部署图测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("tech", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="部署架构", order_idx=0, run_thread_id="tech")
        await db.commit()
        block = await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone()
    queued = []
    monkeypatch.setattr(routes, "queue_job", queued.append)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, pid=pid, block=dict(block), queued=queued)


async def test_deployment_job_generates_png(env, monkeypatch):
    async def fake_extract(config, block, brief):
        return deployment_diagram.normalize_spec(SPEC)

    async def fake_render(html_path, png_path, scale=2):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return 1280, 760, {"texts": 10, "overflow": [], "overlaps": [], "shapes_overflow": []}

    async def fake_config(db, block):
        return SimpleNamespace(model="test", provider="openai"), {}

    monkeypatch.setattr(deployment_diagram, "extract_spec", fake_extract)
    monkeypatch.setattr(diagram_design, "resolve_config", fake_config)
    monkeypatch.setattr("services.diagram_render.render_png", fake_render)
    created = await env.client.post(
        f"/api/blocks/{env.block['id']}/studio/deployment-diagrams",
        json={"title": "图 3-1 部署拓扑", "text": "互联网、DMZ、应用区、数据区；LB、应用、数据库。", "claim": "双活"},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    await diagram_design.run_job(job_id)
    data = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{job_id}")).json()
    assert data["status"] == "done" and data["asset_id"]
    image = await env.client.get(f"/api/studio/diagrams/{data['asset_id']}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    checks = data["checks"]
    assert checks["pipeline"] == "deployment"
    assert checks["zones"] == 4 and checks["nodes"] == 5 and checks["edges"] == 4
