"""API 安全边界：可选 token；未配置时保持本地同源可用。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_api_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", "s3cret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/model-defaults")
        assert r.status_code == 401
        r = await c.get("/api/model-defaults", headers={"X-Auth-Token": "s3cret"})
        assert r.status_code == 200
        r = await c.get("/api/model-defaults", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200
        # health 不是 /api/*，不受影响
        assert (await c.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_api_open_without_token(monkeypatch):
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/model-defaults")).status_code == 200
