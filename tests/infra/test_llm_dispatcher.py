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


# ─────────────────────────────────────────────
# dispatch_outline_draft_json：目录派生
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_under_mock(monkeypatch):
    """mock 模式下按「提炼应答文件目录」关键词路由到目录 fixture。"""
    from infra.llm import dispatch_outline_draft_json

    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()

    obj = await dispatch_outline_draft_json([cfg], 0, {
        "instruction": "按评分项逐条拆章",
        "doc_summary": "摘要",
        "spec_digest": "【技术方案】\n正文",
    })

    assert isinstance(obj["nodes"], list)
    assert len(obj["nodes"]) > 0
    assert all("title" in n and "level" in n for n in obj["nodes"])
    # fixture 里要有真正的层级，否则前端树渲染在 mock 下测不出东西
    assert max(n["level"] for n in obj["nodes"]) >= 3


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_forwards_follow_structure(monkeypatch):
    """payload 里的 follow_structure 必须走到 user prompt —— 定位命中时目录要复刻结构。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        seen["user"] = user
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_outline_draft_json([_fake_config()], 0, {
        "instruction": "按评分要求中的每一点拆章",
        "spec_digest": "【已定位到的材料（请据此编排章节）】\n【标包2】\n技术评分标准",
        "follow_structure": True,
    })
    assert "严格按材料自身的结构拆分章节" in seen["user"]

    await dispatch_outline_draft_json([_fake_config()], 0, {
        "instruction": "按评分项逐条拆章", "spec_digest": "【一】",
    })
    assert "严格按材料自身的结构拆分章节" not in seen["user"]


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_uses_enlarged_budget(monkeypatch):
    """目录派生必须用放大后的预算与超时，并且空响应要抛出来。

    推理模型实测会把 8000 token 全烧在思考上、正文一个字不吐（一次成功的调用
    要 142 秒），沿用默认值就是必然失败。
    """
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients
    import infra.llm.dispatcher as dispatcher

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        seen.update(max_tokens=max_tokens, timeout=timeout,
                    raise_on_empty=raise_on_empty, call_site=call_site)
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})

    assert dispatcher.OUTLINE_DRAFT_MAX_TOKENS == 32000
    assert dispatcher.OUTLINE_DRAFT_TIMEOUT == 300.0
    assert seen["max_tokens"] == 32000
    assert seen["timeout"] == 300.0
    assert seen["raise_on_empty"] is True
    # 用量记账靠这个标签归因：漏传就全记成空串，「花在哪条路径」就查不出来了
    assert seen["call_site"] == "outline_draft"


@pytest.mark.asyncio
async def test_scope_select_keeps_default_budget(monkeypatch):
    """其余调用点不受影响：仍走 60s 默认超时，空内容仍是回空串而不是抛异常。"""
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=60.0, raise_on_empty=False, call_site=""):
        seen.update(timeout=timeout, raise_on_empty=raise_on_empty)
        return '{"files": [], "keywords": ["标包2"]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_scope_select_json([_fake_config()], 0, {"instruction": "x"})

    assert seen["timeout"] == 60.0
    assert seen["raise_on_empty"] is False


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_raises_without_configs():
    from infra.llm import dispatch_outline_draft_json

    with pytest.raises(ValueError):
        await dispatch_outline_draft_json([], 0, {"spec_digest": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_falls_back_to_next_config(monkeypatch):
    """第一个 config 失败要轮询到下一个，而不是直接把异常抛给调用方。"""
    from infra import llm
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    calls = []

    async def _gen(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        calls.append(config.model)
        if config.model == "bad":
            raise RuntimeError("第一个 API 挂了")
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _gen)

    good = _fake_config()
    good.model = "good"
    bad = _fake_config()
    bad.model = "bad"

    obj = await dispatch_outline_draft_json([bad, good], 0, {"spec_digest": "x"})

    assert calls == ["bad", "good"]
    assert obj["nodes"][0]["title"] == "OK"


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_raises_last_error_when_all_fail(monkeypatch):
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _always_fail(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        raise RuntimeError(f"{config.model} 挂了")

    monkeypatch.setattr(clients, "generate_oneshot_openai", _always_fail)

    a = _fake_config()
    a.model = "a"
    b = _fake_config()
    b.model = "b"

    with pytest.raises(RuntimeError, match="b 挂了"):
        await dispatch_outline_draft_json([a, b], 0, {"spec_digest": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_rejects_non_nodes_output(monkeypatch):
    """模型输出是合法 JSON 但没有 nodes 数组 → 当失败处理，走降级。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _wrong_shape(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        return '{"chapters": [{"title": "错的字段"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _wrong_shape)

    with pytest.raises(ValueError, match="nodes"):
        await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})


# ─────────────────────────────────────────────
# dispatch_scope_select_json：检索范围兜底
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_scope_select_json_under_mock(monkeypatch):
    """mock 按「圈定检索范围」路由。该分支必须排在目录派生之前 ——
    它的 system prompt 同样含「评审」二字，落到「评审」分支会走错 fixture。"""
    from infra.llm import dispatch_scope_select_json

    monkeypatch.setenv("LLM_MODE", "mock")

    obj = await dispatch_scope_select_json([_fake_config()], 0, {
        "instruction": "按标包2 的技术评分要求拆分",
        "file_names": ["主招标文件.pdf"],
        "section_titles": ["第一章 总则"],
    })

    assert isinstance(obj["keywords"], list)
    assert obj["keywords"]
    assert "nodes" not in obj   # 没落到目录派生 fixture


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_raises_without_configs():
    from infra.llm import dispatch_scope_select_json

    with pytest.raises(ValueError):
        await dispatch_scope_select_json([], 0, {"instruction": "x"})


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_falls_back_to_next_config(monkeypatch):
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    calls = []

    async def _gen(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        calls.append(config.model)
        if config.model == "bad":
            raise RuntimeError("第一个 API 挂了")
        return '{"files": [], "keywords": ["标包2"]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _gen)

    good = _fake_config()
    good.model = "good"
    bad = _fake_config()
    bad.model = "bad"

    obj = await dispatch_scope_select_json([bad, good], 0, {"instruction": "x"})

    assert calls == ["bad", "good"]
    assert obj["keywords"] == ["标包2"]


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_raises_last_error_when_all_fail(monkeypatch):
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    async def _always_fail(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        raise RuntimeError(f"{config.model} 挂了")

    monkeypatch.setattr(clients, "generate_oneshot_openai", _always_fail)

    a = _fake_config()
    a.model = "a"

    with pytest.raises(RuntimeError, match="a 挂了"):
        await dispatch_scope_select_json([a], 0, {"instruction": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_salvages_truncated_prefix(monkeypatch):
    """扁平数组格式的核心动机：被 max_tokens 截断后仍能抢救出完整前缀，部分成功。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _truncated(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        # 最后一条写到一半就没了，外层 }]} 也没收尾
        return (
            '{"nodes": [{"level": 1, "title": "完整一"}, '
            '{"level": 1, "title": "完整二"}, {"level": 2, "title": "被截'
        )

    monkeypatch.setattr(clients, "generate_oneshot_openai", _truncated)

    obj = await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})

    assert [n["title"] for n in obj["nodes"]] == ["完整一", "完整二"]


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_fails_when_nothing_salvageable(monkeypatch):
    """连一个完整节点都没有 → 仍然算失败，由节点层降级。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _barely_started(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        return '{"nodes": [{"level": 1, "tit'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _barely_started)

    with pytest.raises(ValueError):
        await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})
