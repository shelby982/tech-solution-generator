"""clients 空响应诊断的用例。

背景：模型返回空内容时不抛异常，一直到 json_utils.extract_json_object 才炸成
「未找到 JSON 起始 {：」（后面拼的是空串），现场只剩这一句话，分不清是
max_tokens 被思考 token 吃光、工具调用吞了正文，还是模型真的没吐东西。
这里锁住空响应时记录下来的形状读数。
"""

import logging

import pytest

from infra.llm import clients
from services.config_store import LLMConfig


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _cfg() -> LLMConfig:
    return LLMConfig(
        provider="deepseek",
        api_key="sk-not-a-real-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
    )


def _fake_openai(monkeypatch, *, content, reasoning="", captured=None):
    """把 openai.AsyncOpenAI 换成固定回包的假客户端。

    ``captured`` 非空时把构造客户端收到的关键字参数记进去 ——
    timeout 这类传输层参数只在这一层可见，底下 create() 看不到。
    """
    choice = _Obj(
        finish_reason="length" if not content else "stop",
        message=_Obj(content=content, reasoning_content=reasoning),
    )
    response = _Obj(
        choices=[choice],
        usage=_Obj(
            completion_tokens=8000,
            completion_tokens_details=_Obj(reasoning_tokens=7990),
        ),
    )

    class _Completions:
        async def create(self, **kw):
            return response

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    def _make_client(**kw):
        if captured is not None:
            captured.update(kw)
        return _Client()

    monkeypatch.setattr("openai.AsyncOpenAI", _make_client)


@pytest.mark.asyncio
async def test_empty_openai_response_logs_shape(monkeypatch, caplog):
    """content 空但 reasoning 有内容：必须记下 finish_reason 与 reasoning_tokens。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, content="", reasoning="想" * 3000)

    with caplog.at_level(logging.WARNING, logger="infra.llm.clients"):
        out = await clients.generate_oneshot_openai(
            _cfg(), "sys", "user", max_tokens=8000,
        )

    assert out == ""
    assert any("返回空内容" in r.message for r in caplog.records), caplog.text
    msg = next(r.message for r in caplog.records if "返回空内容" in r.message)
    assert "finish_reason='length'" in msg
    assert "reasoning_len=3000" in msg
    assert "reasoning_tokens=7990" in msg


@pytest.mark.asyncio
async def test_non_empty_openai_response_does_not_warn(monkeypatch, caplog):
    """正常有内容时不打这条 warning，别把日志淹掉。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, content='{"nodes": []}')

    with caplog.at_level(logging.WARNING, logger="infra.llm.clients"):
        out = await clients.generate_oneshot_openai(_cfg(), "sys", "user")

    assert out == '{"nodes": []}'
    assert not [r for r in caplog.records if "返回空内容" in r.message]


@pytest.mark.asyncio
async def test_empty_claude_response_logs_stop_reason(monkeypatch, caplog):
    """Anthropic 侧没有 content block 时，stop_reason 是唯一线索。"""
    monkeypatch.delenv("LLM_MODE", raising=False)

    class _Messages:
        async def create(self, **kw):
            return _Obj(content=[], stop_reason="max_tokens")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kw: _Client())

    with caplog.at_level(logging.WARNING, logger="infra.llm.clients"):
        out = await clients.generate_oneshot_claude(_cfg(), "sys", "user")

    assert out == ""
    msg = next(
        (r.message for r in caplog.records if "返回空内容" in r.message), "",
    )
    assert "stop_reason='max_tokens'" in msg


# ─────────────────────────────────────────────
# 超时可注入 + 空响应抛异常（目录派生专用）
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oneshot_openai_timeout_default_and_override(monkeypatch):
    """默认 60s 保护其余调用点；目录派生显式传 300s —— 实测一次成功的调用要 142 秒。"""
    monkeypatch.delenv("LLM_MODE", raising=False)

    default = {}
    _fake_openai(monkeypatch, content="ok", captured=default)
    await clients.generate_oneshot_openai(_cfg(), "sys", "user")
    assert default["timeout"] == 60.0

    override = {}
    _fake_openai(monkeypatch, content="ok", captured=override)
    await clients.generate_oneshot_openai(_cfg(), "sys", "user", timeout=300.0)
    assert override["timeout"] == 300.0


@pytest.mark.asyncio
async def test_empty_response_raises_with_actionable_message(monkeypatch, caplog):
    """raise_on_empty 时空内容要抛异常，把原因和下一步带出来，而不是退化成空串。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, content="", reasoning="想" * 1000)

    with caplog.at_level(logging.WARNING, logger="infra.llm.clients"):
        with pytest.raises(clients.LLMEmptyResponseError) as ei:
            await clients.generate_oneshot_openai(
                _cfg(), "sys", "user", max_tokens=8000, raise_on_empty=True,
            )

    msg = str(ei.value)
    assert "finish_reason='length'" in msg
    assert "reasoning_tokens=7990" in msg
    assert "调大该值或改用非推理模型" in msg
    assert "max_tokens=8000" in msg
    # 抛异常不能让诊断日志消失：日志才是长尾问题的现场
    assert any("返回空内容" in r.message for r in caplog.records), caplog.text


@pytest.mark.asyncio
async def test_empty_response_does_not_raise_by_default(monkeypatch):
    """默认仍返回空串：概述那条路径靠它降级成「缺概述但继续跑」。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, content="", reasoning="想")

    assert await clients.generate_oneshot_openai(_cfg(), "sys", "user") == ""


@pytest.mark.asyncio
async def test_empty_claude_response_raises_when_asked(monkeypatch):
    """Claude 分支同样接受 timeout 与 raise_on_empty。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    captured = {}

    class _Messages:
        async def create(self, **kw):
            return _Obj(content=[], stop_reason="max_tokens")

    class _Client:
        messages = _Messages()

    def _make_client(**kw):
        captured.update(kw)
        return _Client()

    monkeypatch.setattr("anthropic.AsyncAnthropic", _make_client)

    with pytest.raises(clients.LLMEmptyResponseError) as ei:
        await clients.generate_oneshot_claude(
            _cfg(), "sys", "user", timeout=300.0, raise_on_empty=True,
        )

    assert "stop_reason='max_tokens'" in str(ei.value)
    assert captured["timeout"] == 300.0
