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
    async def fake_generate(config, brief, *, project_id, block_id, prompt=None):
        html = ('<!doctype html><html><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
                'role="img"><title>总体技术方案</title><desc>架构示意</desc>'
                '<rect width="10" height="10" fill="#fff"/><text>接入层</text><text>服务层</text>'
                '</svg></body></html>')
        return html, {"self_check": True, "labels": 2}

    async def fake_render(html_path, png_path, scale=2):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return 1280, 720, {"texts": 2, "overflow": [], "overlaps": []}

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
    assert status["status"] == "done", status
    asset_id = status["asset_id"]
    assert status["image_url"] == f"/api/studio/diagrams/{asset_id}/image"
    image = await env.client.get(f"/api/studio/diagrams/{asset_id}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    preview = await env.client.get(f"/api/studio/diagrams/{asset_id}/preview")
    assert preview.status_code == 200 and "default-src 'none'" in preview.headers["content-security-policy"]
    svg = await env.client.get(f"/api/studio/diagrams/{asset_id}/svg")
    assert svg.status_code == 200 and "<svg" in svg.text


async def test_failed_render_marks_job_failed(env, monkeypatch):
    async def fake_generate(config, brief, *, project_id, block_id, prompt=None):
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


async def test_diagram_quality_setting_roundtrip(env):
    got = (await env.client.get(f"/api/projects/{env.pid}/diagram-settings")).json()
    assert got["quality"] == "fast" and got["options"]
    saved = await env.client.put(f"/api/projects/{env.pid}/diagram-settings",
                                 json={"quality": "design_all"})
    assert saved.status_code == 200 and saved.json()["quality"] == "design_all"
    got = (await env.client.get(f"/api/projects/{env.pid}/diagram-settings")).json()
    assert got["quality"] == "design_all"
    # 非法值回退 fast
    await env.client.put(f"/api/projects/{env.pid}/diagram-settings", json={"quality": "nope"})
    assert (await env.client.get(f"/api/projects/{env.pid}/diagram-settings")).json()["quality"] == "fast"


async def test_chart_uses_design_when_project_quality_enabled(monkeypatch):
    from services import chapter_sse, diagram_design

    async def fake_emit(*args, **kwargs):
        return None

    async def fake_quality(project_id):
        return "design_architecture"

    async def fake_design(block, section, deliverable):
        return {"ok": True, "asset_id": "a" * 32, "title": "架构图",
                "model": "m", "checks": {"self_check": True},
                "markdown": ":::figure 架构图\n![架构图](/api/studio/diagrams/" + "a" * 32 + "/image)\n:::"}

    monkeypatch.setattr(chapter_sse, "emit", fake_emit)
    monkeypatch.setattr(diagram_design, "project_quality", fake_quality)
    monkeypatch.setattr(diagram_design, "design_section_figure", fake_design)
    result = await chapter_sse._generate_chart(
        "job", 0, {"title": "总体架构", "points": ["接入层"]},
        {"kind": "figure", "title": "总体架构图", "type": "layered"}, "t" * 10, "m",
        {"project_id": 1, "id": 1},
    )
    assert result["ok"] and result["kind"] == "design"
    assert result["markdown"].startswith(":::figure")


async def test_same_prompt_reuses_cached_asset(env, monkeypatch):
    calls = {"generate": 0}

    async def fake_generate(config, brief, *, project_id, block_id, prompt=None):
        calls["generate"] += 1
        html = ('<!doctype html><html><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
                'role="img"><title>图</title><desc>说明</desc><rect width="10" height="10"/>'
                '<text>接入层</text><text>服务层</text></svg></body></html>')
        return html, {"self_check": True}

    async def fake_render(html_path, png_path, scale=2):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return 1280, 720, {"texts": 2, "overflow": [], "overlaps": []}

    async def fake_config(db, block):
        return SimpleNamespace(model="test-model", provider="openai"), {}

    monkeypatch.setattr(diagram_design, "generate_valid_html", fake_generate)
    monkeypatch.setattr(diagram_design, "resolve_config", fake_config)
    monkeypatch.setattr("services.diagram_render.render_png", fake_render)
    payload = {"title": "缓存图", "text": "接入层、服务层", "skill_type": "architecture"}
    first = (await env.client.post(f"/api/blocks/{env.block['id']}/studio/diagram-assets", json=payload)).json()
    await diagram_design.run_job(first["job_id"])
    second = (await env.client.post(f"/api/blocks/{env.block['id']}/studio/diagram-assets", json=payload)).json()
    await diagram_design.run_job(second["job_id"])
    assert calls["generate"] == 1
    a = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{first['job_id']}")).json()
    b = (await env.client.get(f"/api/blocks/{env.block['id']}/studio/diagram-assets/{second['job_id']}")).json()
    assert a["asset_id"] == b["asset_id"]


def test_compact_prompt_keeps_chinese_rules_and_is_smaller():
    system, user = diagram_design.build_prompt({
        "title": "总体技术架构图", "skill_type": "architecture", "size_preset": "doc-wide",
        "text": "接入层、服务层、数据层", "elements": ["接入层", "服务层", "数据层"],
    })
    assert "Simplified Chinese" in system
    assert "Mandatory connector rules" in system
    assert len(system) < 70000, len(system)
    assert "总体技术架构图" in user
