"""LLM 用量记账的用例。

背景：客户端层此前完全不记 usage —— 只在响应为空时读一次做诊断，成功路径连
用了多少 token 都不留。于是「今天的 token 花在哪」只能靠翻库推断，查不出实测值。
引入 llm_usage 表后这里锁住四件事：

1. 调用点 / 模型 / token 字段被正确上报（含 cache 字段 —— 它是长上下文的主要成本）；
2. 失败与空响应同样落账 —— 那正是最需要留下证据的场景；
3. 记账本身绝不影响 LLM 调用（无 sink、sink 抛异常都不能外溢）；
4. mock 模式不记账（没有真实用量，记了反而污染统计）。
"""

import logging

import pytest

import db as db_module
from db import get_db, init_db
from infra.llm import clients, usage
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


@pytest.fixture
def records():
    """装一个收集用的 sink 替掉落库，用后卸载。"""
    recs = []

    async def sink(rec):
        recs.append(rec)

    usage.set_sink(sink)
    yield recs
    usage.set_sink(None)


def _openai_response(content="ok", *, prompt=100, completion=20, cached=30):
    choice = _Obj(
        finish_reason="stop" if content else "length",
        message=_Obj(content=content, reasoning_content=""),
    )
    return _Obj(
        choices=[choice],
        usage=_Obj(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_tokens_details=_Obj(cached_tokens=cached),
        ),
    )


def _fake_openai(monkeypatch, response):
    """把 openai.AsyncOpenAI 换成固定回包（或固定异常）的假客户端。"""

    class _Completions:
        async def create(self, **kw):
            if isinstance(response, Exception):
                raise response
            return response

    class _Client:
        chat = _Obj(completions=_Completions())

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: _Client())


def _fake_claude(monkeypatch, message):
    class _Messages:
        async def create(self, **kw):
            if isinstance(message, Exception):
                raise message
            return message

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda **kw: _Client())


# ─────────────────────────────────────────────
# 上报通道本身：绝不能影响调用方
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_without_sink_is_noop():
    """未装配 sink（测试、脚本）时静默丢弃，不抛。"""
    usage.set_sink(None)
    await usage.emit({"call_site": "whatever"})


@pytest.mark.asyncio
async def test_sink_failure_is_swallowed(caplog):
    """落库失败只记日志：记账不能把一次成功的模型调用变成失败。"""

    async def boom(rec):
        raise RuntimeError("disk full")

    usage.set_sink(boom)
    try:
        with caplog.at_level(logging.WARNING, logger="infra.llm.usage"):
            await usage.emit({"call_site": "outline_extract"})
    finally:
        usage.set_sink(None)

    assert any("记账失败" in r.message for r in caplog.records), caplog.text


# ─────────────────────────────────────────────
# 成功路径
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oneshot_openai_records_usage(monkeypatch, records):
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, _openai_response("ok"))

    out = await clients.generate_oneshot_openai(
        _cfg(), "sys", "user", call_site="outline_extract",
    )

    assert out == "ok"
    assert len(records) == 1
    r = records[0]
    assert r["call_site"] == "outline_extract"
    assert r["provider"] == "deepseek"
    assert r["model"] == "deepseek-v4-pro"
    assert r["streaming"] is False
    assert r["ok"] is True
    assert r["input_tokens"] == 100
    assert r["output_tokens"] == 20
    assert r["cache_read_tokens"] == 30
    assert r["total_tokens"] == 120
    assert isinstance(r["latency_ms"], int)


@pytest.mark.asyncio
async def test_claude_records_cache_tokens(monkeypatch, records):
    """Anthropic 的缓存读写分两个字段记 —— 长上下文的成本主要在这里。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_claude(monkeypatch, _Obj(
        content=[_Obj(text="hi")],
        stop_reason="end_turn",
        usage=_Obj(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=50,
        ),
    ))

    out = await clients.generate_oneshot_claude(_cfg(), "sys", "user", call_site="letter")

    assert out == "hi"
    assert len(records) == 1
    r = records[0]
    assert r["call_site"] == "letter"
    assert r["input_tokens"] == 10
    assert r["output_tokens"] == 5
    assert r["cache_read_tokens"] == 900
    assert r["cache_creation_tokens"] == 50


# ─────────────────────────────────────────────
# 失败路径同样落账
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_response_recorded_as_failure(monkeypatch, records, caplog):
    """空响应（推理 token 吃光预算）要留下用量证据，而不是只留在日志里。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, _openai_response(""))

    with caplog.at_level(logging.WARNING, logger="infra.llm.clients"):
        await clients.generate_oneshot_openai(
            _cfg(), "sys", "user", max_tokens=8000, call_site="outline_draft",
        )

    assert len(records) == 1
    assert records[0]["ok"] is False
    assert "finish_reason='length'" in records[0]["error"]


@pytest.mark.asyncio
async def test_transport_error_recorded_and_reraised(monkeypatch, records):
    """调用抛异常：记 ok=0 与原因，然后把原异常照原样抛回去。"""
    monkeypatch.delenv("LLM_MODE", raising=False)
    _fake_openai(monkeypatch, RuntimeError("connection reset"))

    with pytest.raises(RuntimeError, match="connection reset"):
        await clients.generate_oneshot_openai(_cfg(), "sys", "user", call_site="outline_draft")

    assert len(records) == 1
    r = records[0]
    assert r["ok"] is False
    assert "connection reset" in r["error"]
    # provider 没返回用量：留 None 而不是 0，汇总时才能区分「没数据」和「真的是 0」
    assert r["input_tokens"] is None


@pytest.mark.asyncio
async def test_mock_mode_not_recorded(monkeypatch, records):
    """mock 模式没有真实用量，记账会污染统计。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    await clients.generate_oneshot_openai(_cfg(), "sys", "user", call_site="doc_summary")

    assert records == []


# ─────────────────────────────────────────────
# 落库
# ─────────────────────────────────────────────

@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    """把 DB_PATH 指向 tmp_path 并建表（同 tests/routes 的既有做法）。"""
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "usage.db"))
    async with get_db() as conn:
        await init_db(conn)


@pytest.mark.asyncio
async def test_usage_store_writes_row(fresh_db):
    from services import usage_store

    await usage_store.record({
        "call_site": "outline_extract",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "streaming": False,
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "total_tokens": 120,
        "ok": True,
        "error": "",
        "latency_ms": 42,
    })

    async with get_db() as conn:
        cur = await conn.execute(
            "SELECT call_site, provider, model, streaming, input_tokens,"
            " cache_read_tokens, latency_ms, ok FROM llm_usage"
        )
        rows = await cur.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["call_site"] == "outline_extract"
    assert row["provider"] == "deepseek"
    assert row["model"] == "deepseek-v4-pro"
    assert row["streaming"] == 0
    assert row["input_tokens"] == 100
    assert row["cache_read_tokens"] == 30
    assert row["latency_ms"] == 42
    assert row["ok"] == 1


@pytest.mark.asyncio
async def test_missing_tokens_stored_as_null(fresh_db):
    """缺字段写 NULL：记成 0 会被误读成「这次调用免费」。"""
    from services import usage_store

    await usage_store.record({"call_site": "letter", "provider": "deepseek", "model": "m"})

    async with get_db() as conn:
        cur = await conn.execute("SELECT input_tokens, output_tokens, ok FROM llm_usage")
        row = await cur.fetchone()

    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["ok"] == 1
