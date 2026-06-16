"""infra/llm dispatcher + mock 模式测试。"""
import pytest

from infra.llm import (
    LLMConfig,
    dispatch_outline_json,
    generate_letter_content,
    is_letter_section,
)


def _fake_config() -> LLMConfig:
    """构造一份占位配置；mock 模式下不会真的发起网络调用。"""
    return LLMConfig(
        provider="openai",
        api_key="sk-fake",
        base_url="https://example.invalid/v1",
        model="gpt-test",
    )


# ─────────────────────────────────────────────
# letter detection
# ─────────────────────────────────────────────

def test_letter_section_detection():
    assert is_letter_section("投标承诺书") is True
    assert is_letter_section("廉洁承诺函") is True
    assert is_letter_section("授权委托书") is True
    assert is_letter_section("技术方案概述") is False
    assert is_letter_section("") is False


# ─────────────────────────────────────────────
# mock 模式下大纲提炼：8 字段 + title + error
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_outline_json_under_mock_returns_eight_fields(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()
    sections = [{"title": "技术架构要求", "content": "系统须采用微服务架构…"}]

    results = []
    async for item in dispatch_outline_json([cfg], 0, sections):
        results.append(item)

    assert len(results) == 1
    item = results[0]
    expected_keys = {
        "title",
        "requirement",
        "key_points",
        "veto_items",
        "bonus_items",
        "score_items",
        "evidence_required",
        "constraint_level",
        "indicators",
        "error",
    }
    assert expected_keys.issubset(item.keys())
    assert item["title"] == "技术架构要求"
    assert item["error"] == ""
    assert item["constraint_level"] in {"mandatory", "recommended", "optional"}


# ─────────────────────────────────────────────
# mock 模式下公文生成：必含两个占位符
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_letter_response_contains_required_placeholders(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()
    block = {
        "title": "投标承诺书",
        "requirement": "投标方须承诺产品质量、交付工期、售后服务等。",
    }
    text = await generate_letter_content([cfg], 0, block)
    assert "【招标人/采购人名称】" in text
    assert "【公司全称】" in text
