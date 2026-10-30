"""设计版图表：类型/提示词/校验/资产路由。"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from db import get_db
from routes import diagram_assets as routes
from services import diagram_assets, diagram_design
from services.block_store import upsert_outline_block


def test_type_normalization_and_recommendation():
    assert diagram_design.normalize_type("layered") == "layers"
    assert diagram_design.normalize_type("flow") == "process"
    assert diagram_design.normalize_type("sketchy") == "architecture"
    assert diagram_design.normalize_size("print-a4-landscape") == "print-a4-landscape"
    assert diagram_design.normalize_size("nope") == "doc-wide"
    assert diagram_design.recommend_type("总体技术架构") == "architecture"
    assert diagram_design.recommend_type("实施流程与步骤") == "process"


def test_clean_html_removes_google_fonts_and_wraps_svg():
    raw = '<link href="https://fonts.googleapis.com/css2?family=Geist">' + \
          '<svg role="img" viewBox="0 0 10 10"><title>x</title><desc>y</desc></svg>'
    text = diagram_design.clean_html(raw)
    assert "fonts.googleapis.com" not in text
    assert text.lstrip().startswith("<!doctype html>")
    assert "<svg" in text


def test_validate_rejects_script_and_external():
    brief = {"title": "图", "skill_type": "architecture", "size_preset": "doc-wide", "text": "接入层"}
    with pytest.raises(diagram_design.DiagramValidationError):
        diagram_design.validate_html(
            '<html><body><svg viewBox="0 0 10 10" role="img"><title>图</title><desc>说明</desc>'
            '<script>alert(1)</script></svg></body></html>', brief)
    with pytest.raises(diagram_design.DiagramValidationError):
        diagram_design.validate_html(
            '<html><body><svg viewBox="0 0 10 10" role="img"><title>图</title><desc>说明</desc>'
            '<image href="https://evil.example/x.png"/></svg></body></html>', brief)


@pytest.fixture
async def env(monkeypatch, tmp_path):
    # 资产文件写到临时目录，绝不碰真实 backend/data。
    monkeypatch.setattr(diagram_assets, "ROOT", tmp_path / "diagrams")
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('图表测试')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("tech", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="总体技术方案", order_idx=0, run_thread_id="tech")
        await db.commit()
        row = await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone()
    queued = []
    monkeypatch.setattr(routes, "queue_job", queued.append)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, pid=pid, block=dict(row), queued=queued)


async def test_create_job_runs_and_serves_png(env, monkeypatch):
    async def fake_generate(config, brief, *, project_id, block_id):
        html = ('<!doctype html><html><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
                'role="img"><title>总体技术方案</title><desc>架构示意</desc>'
                '<rect width="10" height="10" fill="#fff"/><text>接入层</text><text>服务层</text>'
                '</svg></body></html>')
        return html, {"self_check": True, "labels": 2}

    async def fake_render(html_path, png_path, scale=2):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return 1280, 720

    async def fake_config(db, block):
        return SimpleNamespace(model="test-model", provider="openai"), {"used": "test-model", "source": "test"}

    monkeypatch.setattr(diagram_design, "generate_valid_html", fake_generate)
    monkeypatch.setattr(diagram_design, "resolve_config", fake_config)
    monkeypatch.setattr("services.diagram_render.render_png", fake_render)

    created = await env.client.post(f"/api/blocks/{env.block['id']}/studio/diagram-assets",
                                    json={"title": "总体技术方案", "text": "接入层、服务层、数据层", "skill_type": "architecture"})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert env.queued == [job_id]
    await diagram_design.run_job(job_id)

    status = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{job_id}")).json()
    assert status["status"] == "done"
    asset_id = status["asset_id"]
    assert status["image_url"] == f"/api/studio/diagrams/{asset_id}/image"
    image = await env.client.get(f"/api/studio/diagrams/{asset_id}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    preview = await env.client.get(f"/api/studio/diagrams/{asset_id}/preview")
    assert preview.status_code == 200 and "default-src 'none'" in preview.headers["content-security-policy"]


async def test_failed_render_marks_job_failed(env, monkeypatch):
    async def fake_generate(config, brief, *, project_id, block_id):
        return '<html><body><svg viewBox="0 0 10 10" role="img"><title>x</title><desc>y</desc></svg></body></html>', {}

    async def fake_config(db, block):
        return SimpleNamespace(model="test", provider="openai"), {}

    async def fail_render(*args, **kwargs):
        raise RuntimeError("Playwright 未安装")

    monkeypatch.setattr(diagram_design, "generate_valid_html", fake_generate)
    monkeypatch.setattr(diagram_design, "resolve_config", fake_config)
    monkeypatch.setattr("services.diagram_render.render_png", fail_render)
    created = await env.client.post(f"/api/blocks/{env.block['id']}/studio/diagram-assets",
                                    json={"title": "图", "text": "内容"})
    job_id = created.json()["job_id"]
    await diagram_design.run_job(job_id)
    status = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{job_id}")).json()
    assert status["status"] == "failed"
    assert "Playwright" in status["error"]


def test_word_export_accepts_diagram_image_url_and_rejects_external():
    from infra.docx.document_model import image_urls, parse_markdown
    ok = "/api/studio/diagrams/0123456789abcdef0123456789abcdef/image"
    blocks = parse_markdown(f"![图]({ok})")
    assert list(image_urls(blocks)) == [ok]
    bad = parse_markdown("![图](https://evil.example/x.png)")
    with pytest.raises(ValueError):
        list(image_urls(bad))
