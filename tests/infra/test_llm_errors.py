"""Provider 错误分类 + 坏配置冷却的用例。

背景（2026-09-20 费用分析实测）：3 条配置全部返回 ``402 Insufficient Balance``，
而重试循环对任何异常都无差别换下一条配置 —— 一个坏配置会被后面每个小节反复重试
（208 小节 × 3 条 = 624 次无谓尝试），日志里还只看到「提炼失败」。

这里锁四件事：
1. 分类正确（402/401/403/404 → 配置不可用；400/422 → 请求问题；429/5xx/超时 → 可重试）；
2. 坏配置进冷却后**后续调用直接跳过**，不再逐条空试；
3. 全部冷却时**快速失败**，且给出的原因可执行（「2 条余额不足」而不是「全部 API 失败」）；
4. 冷却有 TTL，充值/换 key 后不必重启进程就能恢复。
"""

import pytest

import infra.llm.errors as errors
from infra.llm import dispatcher
from infra.llm.clients import LLMEmptyResponseError
from services.config_store import LLMConfig


class _HttpError(Exception):
    """带 status_code 的假 SDK 异常（openai/anthropic 的 APIStatusError 形状）。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _cfg(provider="deepseek", model="deepseek-v4-pro") -> LLMConfig:
    return LLMConfig(
        provider=provider, api_key="sk-fake",
        base_url="https://example.invalid/v1", model=model,
    )


@pytest.fixture(autouse=True)
def _clean_health():
    """每个用例前后都清空冷却状态，避免跨用例串味。"""
    errors.reset_health()
    yield
    errors.reset_health()


# ─────────────────────────────────────────────
# 分类
# ─────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_auth_and_balance_errors_mark_config_unusable(status):
    assert errors.classify_error(_HttpError(status, "nope")) == errors.KIND_CONFIG_UNUSABLE


@pytest.mark.parametrize("status", [400, 422])
def test_request_errors_are_request_side(status):
    kind = errors.classify_error(_HttpError(status, "bad request"))
    assert kind == errors.KIND_REQUEST_ERROR
    # 请求侧问题才中止本条调用链；配置不可用要换下一条配置，不中止
    assert errors.blocks_chain(kind) is True
    assert errors.is_retryable(errors.KIND_CONFIG_UNUSABLE) is True


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_errors_are_retryable(status):
    assert errors.classify_error(_HttpError(status, "later")) == errors.KIND_RETRYABLE


def test_classification_falls_back_to_message_text():
    """SDK 不一定给 status_code，文本兜底要认得出余额不足与超时。"""
    assert errors.classify_error(RuntimeError(
        "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"
    )) == errors.KIND_CONFIG_UNUSABLE
    assert errors.classify_error(RuntimeError("Request timed out.")) == errors.KIND_RETRYABLE
    # 认不出来时保守当可重试，不要误杀一条好配置
    assert errors.classify_error(RuntimeError("weird")) == errors.KIND_RETRYABLE


def test_describe_error_is_actionable():
    assert "余额不足" in errors.describe_error(_HttpError(402, "Insufficient Balance"))
    assert "Key 无效" in errors.describe_error(_HttpError(401, "unauthorized"))
    assert "超时" in errors.describe_error(RuntimeError("Request timed out."))


# ─────────────────────────────────────────────
# 冷却与跳过
# ─────────────────────────────────────────────

def test_available_configs_skips_cooling_config():
    good, bad = _cfg(model="good"), _cfg(model="bad")
    errors.mark_unusable(bad, "余额不足（402）")

    usable = errors.available_configs([bad, good], 0)

    assert [c.model for c in usable] == ["good"]


def test_available_configs_preserves_round_robin_start():
    a, b, c = _cfg(model="a"), _cfg(model="b"), _cfg(model="c")
    # 从 b 起轮询：b, c, a
    assert [x.model for x in errors.available_configs([a, b, c], 1)] == ["b", "c", "a"]


def test_all_cooling_yields_empty_and_summary_names_reason():
    a, b = _cfg(model="a"), _cfg(model="b")
    errors.mark_unusable(a, "余额不足（402）")
    errors.mark_unusable(b, "余额不足（402）")

    assert errors.available_configs([a, b], 0) == []
    summary = errors.exhausted_summary([a, b])
    assert "2 条余额不足（402）" in summary


def test_note_failure_only_cools_unusable_kinds():
    cfg = _cfg()
    assert errors.note_failure(cfg, RuntimeError("Request timed out.")) == errors.KIND_RETRYABLE
    assert errors.is_available(cfg) is True          # 超时不拉黑

    assert errors.note_failure(cfg, _HttpError(402, "Insufficient Balance")) \
        == errors.KIND_CONFIG_UNUSABLE
    assert errors.is_available(cfg) is False


def test_cooldown_expires_after_ttl(monkeypatch):
    """TTL 到点自动恢复：充值后不必重启进程。"""
    cfg = _cfg()
    monkeypatch.setenv("LLM_UNHEALTHY_TTL_SECONDS", "0.01")
    errors.mark_unusable(cfg, "余额不足（402）")
    assert errors.is_available(cfg) is False

    import time
    time.sleep(0.02)
    assert errors.is_available(cfg) is True


# ─────────────────────────────────────────────
# 接入重试循环：跳过坏配置 + 快速失败 + 原因可读
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_skips_cooling_config_and_never_calls_it(monkeypatch):
    """第一条配置已冷却 → 只调第二条，坏的那条一次都不再试。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    bad, good = _cfg(model="bad"), _cfg(model="good")
    errors.mark_unusable(bad, "余额不足（402）")

    calls: list[str] = []

    async def fake(config, system, user, **kw):
        calls.append(config.model)
        return '{"requirement": "r"}'

    monkeypatch.setattr(dispatcher.clients, "generate_oneshot_openai", fake)

    row = await dispatcher._extract_one_section(
        0, {"title": "总则", "content": "正文"}, [bad, good], 0, 2,
    )

    assert calls == ["good"]          # bad 被跳过
    assert row["requirement"] == "r"
    assert row["error"] == ""


@pytest.mark.asyncio
async def test_extract_fails_fast_with_actionable_reason_when_all_cooling(monkeypatch):
    """全部冷却 → 一次模型调用都不发，错误文案直接说明为什么。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    a, b = _cfg(model="a"), _cfg(model="b")
    errors.mark_unusable(a, "余额不足（402）")
    errors.mark_unusable(b, "Key 无效或被拒绝（401/403）")

    called = False

    async def fake(config, system, user, **kw):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(dispatcher.clients, "generate_oneshot_openai", fake)

    row = await dispatcher._extract_one_section(
        0, {"title": "总则", "content": "正文"}, [a, b], 0, 2,
    )

    assert called is False, "全部配置不可用时不应再发起调用"
    assert "余额不足（402）" in row["error"]
    assert "Key 无效或被拒绝（401/403）" in row["error"]


@pytest.mark.asyncio
async def test_extract_cools_config_after_balance_error(monkeypatch):
    """第一次 402 之后该配置进冷却：同批次后续小节不再试它。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    bad, good = _cfg(model="bad"), _cfg(model="good")

    calls: list[str] = []

    async def fake(config, system, user, **kw):
        calls.append(config.model)
        if config.model == "bad":
            raise _HttpError(402, "Insufficient Balance")
        return '{"requirement": "ok"}'

    monkeypatch.setattr(dispatcher.clients, "generate_oneshot_openai", fake)

    await dispatcher._extract_one_section(0, {"title": "A", "content": "x"}, [bad, good], 0, 2)
    await dispatcher._extract_one_section(0, {"title": "B", "content": "y"}, [bad, good], 0, 2)

    # 小节 1 试了 bad 再试 good；小节 2 直接只试 good
    assert calls == ["bad", "good", "good"]
    assert errors.is_available(bad) is False


@pytest.mark.asyncio
async def test_empty_response_error_is_not_treated_as_unusable(monkeypatch):
    """空响应（推理吃光预算）是模型/参数问题，不该把配置拉黑。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    cfg = _cfg()

    async def fake(config, system, user, **kw):
        raise LLMEmptyResponseError("模型返回空内容（max_tokens=2500）")

    monkeypatch.setattr(dispatcher.clients, "generate_oneshot_openai", fake)

    row = await dispatcher._extract_one_section(
        0, {"title": "A", "content": "x"}, [cfg], 0, 1,
    )

    assert errors.is_available(cfg) is True
    assert "空内容" in row["error"] or "失败" in row["error"]


# ─────────────────────────────────────────────
# 流式截断：不能静默当成功（实测 2026-10-12）
# ─────────────────────────────────────────────

def test_truncation_reason_flags_length_and_missing_finish_reason():
    """`length` 与「有正文但无 finish_reason」都判定为截断；正常收尾不误报。"""
    from infra.llm.clients import _truncation_reason

    assert "max_tokens" in _truncation_reason("length", 1200)
    assert "finish_reason" in _truncation_reason(None, 1518)
    # 正常收尾
    assert _truncation_reason("stop", 1200) == ""
    # 模型确实无话可说（0 字且无 finish_reason）不误报成故障
    assert _truncation_reason(None, 0) == ""


def test_stream_truncated_error_is_retryable_not_config_unusable():
    """截断要换配置重试（retryable），而不是把 key 拉黑。"""
    exc = errors.stream_truncated_error("连接在收尾前断开")

    assert isinstance(exc, errors.StreamTruncatedError)
    assert errors.classify_error(exc) == errors.KIND_RETRYABLE
    assert errors.is_retryable(errors.KIND_RETRYABLE) is True
    assert errors.blocks_chain(errors.KIND_RETRYABLE) is False
    assert errors.is_available(_cfg()) is True


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content=None, finish_reason=None):
        self.delta = _Delta(content)
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, content=None, finish_reason=None, usage=None):
        self.choices = [_Choice(content, finish_reason)] if content is not None or finish_reason else []
        self.usage = usage


class _FakeStream:
    """按脚本产出 chunk 的异步迭代器（模拟 openai 流式响应）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


async def _drain_stream_openai(monkeypatch, chunks):
    """跑一遍 stream_openai，返回 (收到的正文, 抛出的异常或 None)。"""
    from infra.llm import clients

    monkeypatch.delenv("LLM_MODE", raising=False)

    class _Completions:
        async def create(self, **kw):
            return _FakeStream(chunks)

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **kw):
            self.chat = _Chat()

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    monkeypatch.setattr(clients, "_emit_usage", lambda *a, **k: _noop())

    text, err = [], None
    try:
        async for t in clients.stream_openai(_cfg(), "sys", "user", call_site="stream_generate"):
            text.append(t)
    except Exception as e:      # noqa: BLE001 — 测试就是要拿到异常
        err = e
    return "".join(text), err


async def _noop():
    return None


@pytest.mark.asyncio
async def test_stream_openai_raises_when_finish_reason_missing(monkeypatch):
    """有正文但收尾帧缺 finish_reason → 抛截断异常，而不是静默算成功。"""
    chunks = [_Chunk("正文前半段。"), _Chunk("正文后半段")]   # 流结束但始终没有 stop
    text, err = await _drain_stream_openai(monkeypatch, chunks)

    assert isinstance(err, errors.StreamTruncatedError)
    assert "finish_reason" in str(err)
    assert text == "正文前半段。正文后半段"   # 已吐出的内容仍被调用方收到


@pytest.mark.asyncio
async def test_stream_openai_raises_when_hitting_max_tokens(monkeypatch):
    """finish_reason='length' → 抛截断异常（顶到 max_tokens，正文被腰斩）。"""
    chunks = [_Chunk("半截正文"), _Chunk(None, finish_reason="length")]
    _text, err = await _drain_stream_openai(monkeypatch, chunks)

    assert isinstance(err, errors.StreamTruncatedError)
    assert "max_tokens" in str(err)


@pytest.mark.asyncio
async def test_stream_openai_ok_on_normal_stop(monkeypatch):
    """正常收尾（finish_reason='stop'）不抛错。"""
    chunks = [_Chunk("完整正文"), _Chunk(None, finish_reason="stop")]
    text, err = await _drain_stream_openai(monkeypatch, chunks)

    assert err is None
    assert text == "完整正文"


@pytest.mark.asyncio
async def test_stream_generate_falls_back_to_next_config_on_truncation(monkeypatch):
    """被截断时换下一条配置**重生成整节**，且不把半截内容重复交出。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    slow, fast = _cfg(model="slow-model"), _cfg(model="fast-model")
    calls = []

    async def fake_stream(config, *a, **kw):
        calls.append(config.model)
        if config.model == "slow-model":
            yield "半截正文"
            raise errors.stream_truncated_error("连接在收尾前断开")
        for tok in ("完整", "正文"):
            yield tok

    monkeypatch.setattr(dispatcher, "_stream_generate", fake_stream)

    out = []
    async for tok in dispatcher.dispatch_stream_generate(
        [slow, fast], 0, "章节", "", extra_prompt="【本章应答要求】\nR1 要求\n",
    ):
        out.append(tok)

    assert calls == ["slow-model", "fast-model"]
    # 半截内容不得混进最终结果（缓冲后用完整那版覆盖）
    assert "".join(out) == "完整正文"
    assert "半截" not in "".join(out)


@pytest.mark.asyncio
async def test_stream_generate_yields_best_partial_when_all_truncated(monkeypatch):
    """全部配置都截断时，交出最长的一段并记 warning —— 不抛错丢掉已花的钱。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    a, b = _cfg(model="m-a"), _cfg(model="m-b")

    async def fake_stream(config, *a, **kw):
        if config.model == "m-a":
            yield "短"
        else:
            yield "长一些的半截正文"
        raise errors.stream_truncated_error("连接在收尾前断开")

    monkeypatch.setattr(dispatcher, "_stream_generate", fake_stream)

    out = []
    async for tok in dispatcher.dispatch_stream_generate(
        [a, b], 0, "章节", "", extra_prompt="【本章应答要求】\nR1 要求\n",
    ):
        out.append(tok)

    assert "".join(out) == "长一些的半截正文"
