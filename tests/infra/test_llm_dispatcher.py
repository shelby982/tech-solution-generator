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


# ─────────────────────────────────────────────
# 协同闭环：dispatch_block_write 注入评审意见
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_block_write_injects_feedback(monkeypatch):
    """feedback_text 非空时注入 extra_prompt，内容出现在实际发出的 prompt 里。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    tokens = [
        t async for t in dispatcher.dispatch_block_write(
            configs=[], rr_start_index=0, title="配电系统",
            requirement="提供业绩证明", chunks=[],
            feedback_text="【上轮评审意见】\n1. [CRITICAL] 缺业绩证明",
        )
    ]

    assert tokens == ["ok"]
    assert "上轮评审意见" in captured["extra_prompt"]
    assert "提供业绩证明" in captured["extra_prompt"]


@pytest.mark.asyncio
async def test_dispatch_block_write_without_feedback_unchanged(monkeypatch):
    """不传 feedback_text 时，prompt 内容与改造前一致。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    async for _ in dispatcher.dispatch_block_write(
        configs=[], rr_start_index=0, title="配电系统",
        requirement="提供业绩证明", chunks=[],
    ):
        pass

    assert "上轮评审意见" not in captured["extra_prompt"]
    # 逐字节钉死改造前的 prompt —— 仅断言"不含评审意见"是弱断言：
    # 把它改成无条件拼接 f"\n\n{feedback_text}" 也照样通过。
    assert captured["extra_prompt"] == "【应标要求】\n提供业绩证明\n\n"
