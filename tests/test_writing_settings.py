"""M1：模型分工设置解析（章节覆盖 → 项目默认 → 内置默认 → 回退）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import services.writing_settings as ws


def _run(coro):
    return asyncio.run(coro)


def test_role_dictionary_complete():
    assert set(ws.ROLE_LABELS) == {"chief", "text", "rewrite", "diagram", "table"}
    assert ws.ROLE_DEFAULTS["rewrite"] == "pro"      # 精修默认用强模型
    assert ws.ROLE_DEFAULTS["text"] == "flash"


def test_resolve_order_block_over_project(monkeypatch):
    async def fake_block(db, block_id):
        return {"text": "deepseek-v4-pro"}

    async def fake_project(db, project_id):
        return {"text": "deepseek-flash"}

    monkeypatch.setattr(ws, "block_settings", fake_block)
    monkeypatch.setattr(ws, "project_settings", fake_project)
    resolved = _run(ws.resolve_model(None, 29, 41828, "text"))
    assert resolved["used"] == "deepseek-v4-pro"
    assert resolved["source"] == "block"
    assert resolved["fallback"] is False


def test_resolve_falls_back_to_available(monkeypatch):
    async def fake_block(db, block_id):
        return {"text": "不存在的模型"}

    async def fake_project(db, project_id):
        return {}

    monkeypatch.setattr(ws, "block_settings", fake_block)
    monkeypatch.setattr(ws, "project_settings", fake_project)
    resolved = _run(ws.resolve_model(None, 29, 41828, "text"))
    assert resolved["used"]                      # 一定落到一个可用模型
    assert resolved["requested"] == "不存在的模型"
    assert resolved["fallback"] is True or resolved["source"] == "builtin"
