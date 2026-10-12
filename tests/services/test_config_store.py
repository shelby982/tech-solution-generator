"""config_store 判断型 provider（typesafe）的轮询排除用例。

背景：`.api_config.json` 里加入 TypeSafe System One（Jev）配置后，若生成链路的
轮询池不排除它，生成侧会按 claude 协议去调 ``/systemone`` 端点 —— 白烧一次失败
fallback 还污染日志。这里锚定三条行为：池排除、全排除时返回空、chat verify 跳过。
"""

import json

import pytest

import services.config_store as config_store_module
from services.config_store import (
    ConfigStore,
    JUDGMENT_PROVIDERS,
    LLMConfig,
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
)


def _cfg(provider: str, *, verified: bool = False) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        api_key=f"test-key-{provider}",
        base_url=DEFAULT_BASE_URLS[provider],
        model=DEFAULT_MODELS[provider],
        verified=verified,
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    """隔离的 ConfigStore：持久化指向临时文件，不碰真实 .api_config.json。"""
    monkeypatch.setattr(config_store_module, "CONFIG_FILE", tmp_path / "api_config.json")
    return ConfigStore()


def test_typesafe_defaults_registered():
    """typesafe 有默认 URL / 模型，且被登记为判断型 provider。"""
    assert DEFAULT_BASE_URLS["typesafe"] == "https://api.typesafe.ai/v1"
    assert DEFAULT_MODELS["typesafe"] == "jev-latest"
    assert "typesafe" in JUDGMENT_PROVIDERS


def test_generation_pool_excludes_judgment_providers(store):
    """生成轮询池排除判断型配置：无 verified 时走全量再过滤。"""
    store.save_all([
        _cfg("deepseek"),
        _cfg("typesafe"),
    ])
    pool, _ = store.get_configs_and_next_index()
    assert [c.provider for c in pool] == ["deepseek"]


def test_generation_pool_excludes_judgment_even_when_verified(store):
    """verified 池同样排除：判断型配置标了 verified 也不进生成轮询。"""
    store.save_all([
        _cfg("deepseek", verified=True),
        _cfg("typesafe", verified=True),
    ])
    pool, _ = store.get_configs_and_next_index()
    assert [c.provider for c in pool] == ["deepseek"]


def test_all_judgment_configs_yield_empty_pool(store):
    """只配了判断型 provider → 返回 ([], 0)，生成侧按"无 LLM 配置"降级。"""
    store.save_all([_cfg("typesafe")])
    assert store.get_configs_and_next_index() == ([], 0)


@pytest.mark.asyncio
async def test_verify_all_skips_judgment_provider(store, monkeypatch):
    """chat 式 verify 跳过判断型 provider：不发起网络调用，结果如实记 False。"""
    typesafe_cfg = _cfg("typesafe")
    store.save_all([typesafe_cfg])

    async def _boom(config):  # 若被调用即失败
        raise AssertionError("判断型 provider 不应走 chat verify")

    monkeypatch.setattr(
        "infra.llm.verify_api_key", _boom, raising=True,
    )
    results = await store.verify_all()
    assert results == {typesafe_cfg.id: False}


def test_persist_and_reload_typesafe_entry(store, tmp_path):
    """typesafe 条目能持久化并重载（字段不丢、verified 重置）。"""
    store.save_all([_cfg("typesafe"), _cfg("deepseek")])

    raw = json.loads((tmp_path / "api_config.json").read_text(encoding="utf-8"))
    assert {item["provider"] for item in raw} == {"typesafe", "deepseek"}
    assert raw[0]["base_url"] == "https://api.typesafe.ai/v1"

    # 重载路径：新建实例读同一份文件
    reloaded = ConfigStore()
    providers = {c.provider for c in reloaded.get_all()}
    assert providers == {"typesafe", "deepseek"}
    # 重载后 typesafe 依然被生成轮询排除
    pool, _ = reloaded.get_configs_and_next_index()
    assert [c.provider for c in pool] == ["deepseek"]
