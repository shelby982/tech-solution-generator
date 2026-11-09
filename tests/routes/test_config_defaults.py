"""模型/Provider 默认值的单一来源契约（GET /api/model-defaults）。

前端新建配置弹窗只从这里取默认值；后端 `_build_config` 的缺省兜底也来自同一张
PROVIDER_CATALOG。本测试防止两处再次漂移。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from routes import config as config_routes
from services.config_store import (
    DEFAULT_MODELS,
    MODEL_OPTIONS,
    PROVIDER_CATALOG,
)


@pytest.fixture
def app():
    from fastapi import FastAPI

    application = FastAPI()
    application.include_router(config_routes.router, prefix="/api")
    return application


@pytest.mark.asyncio
async def test_model_defaults_is_single_source(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/model-defaults")

    assert resp.status_code == 200
    data = resp.json()
    providers = {p["id"]: p for p in data["providers"]}

    # 弹窗只展示 ui=True 的 provider；typesafe 是判断型，不该出现
    assert set(providers) == {pid for pid, meta in PROVIDER_CATALOG.items() if meta.get("ui") is not False}
    assert "typesafe" not in providers

    for pid, meta in PROVIDER_CATALOG.items():
        if meta.get("ui") is False:
            continue
        assert providers[pid]["default_base_url"] == meta["base_url"]
        assert providers[pid]["default_model"] == meta["model"]
        assert providers[pid]["name"] == meta["name"]

    # 兼容映射由 catalog 派生，不允许单独漂移
    assert DEFAULT_MODELS == {pid: meta["model"] for pid, meta in PROVIDER_CATALOG.items()}
    assert data["default_provider"] in providers
    assert data["model_options"] == MODEL_OPTIONS
