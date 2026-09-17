"""
LLM 提供商客户端层：纯 I/O，构造 OpenAI / Anthropic 客户端，
直接做验证 / 流式 / 一次性调用。

mock 模式：环境变量 `LLM_MODE=mock` 时短路到 `_mock.respond()`，
不发起真实网络调用。
"""

import asyncio
import logging
import os
import time
from typing import AsyncIterator

from services.config_store import LLMConfig

from . import _mock, usage

logger = logging.getLogger(__name__)

# 验证用的极小 prompt（消耗极少 Token）
_VERIFY_PROMPT = "Reply with the single word: ok"

# 流式 chunk 级超时（秒），可通过环境变量 LLM_CHUNK_TIMEOUT 覆盖
CHUNK_TIMEOUT = float(os.getenv("LLM_CHUNK_TIMEOUT", "30"))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _openai_usage_fields(response) -> dict:
    """从 OpenAI 响应/流式末帧取用量。字段可能缺失，一律 getattr 兜底。"""
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    details = getattr(u, "prompt_tokens_details", None)
    return {
        "input_tokens": getattr(u, "prompt_tokens", None),
        "output_tokens": getattr(u, "completion_tokens", None),
        # OpenAI 兼容协议里缓存读计在 prompt_tokens_details.cached_tokens
        "cache_read_tokens": getattr(details, "cached_tokens", None),
        "total_tokens": getattr(u, "total_tokens", None),
    }


def _claude_usage_fields(u) -> dict:
    """从 Anthropic message.usage 取用量。"""
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", None),
        "output_tokens": getattr(u, "output_tokens", None),
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", None),
        "cache_creation_tokens": getattr(u, "cache_creation_input_tokens", None),
    }


_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_tokens",
)


async def _emit_usage(
    config: LLMConfig,
    call_site: str,
    *,
    streaming: bool,
    fields: dict,
    ok: bool,
    error: str,
    started: float,
) -> None:
    """上报一次调用的用量。经 usage.emit 转发，失败不影响调用方。

    记录形状固定：provider 没返回的字段补 None，而不是省略键 —— sink 不必猜
    这次调用有哪些列，且 NULL 与 0 在汇总时能区分开。
    """
    tokens = {k: None for k in _USAGE_TOKEN_KEYS}
    tokens.update(fields)
    await usage.emit({
        "call_site": call_site,
        "provider": config.provider,
        "model": config.model,
        "streaming": streaming,
        **tokens,
        "ok": ok,
        "error": error,
        "latency_ms": _elapsed_ms(started),
    })



async def _iter_with_chunk_timeout(aiter, label: str, timeout: float):
    """流式迭代器加 chunk-level 超时；超时抛 TimeoutError。"""
    it = aiter.__aiter__()
    while True:
        try:
            yield await asyncio.wait_for(it.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"{label} stream chunk timeout ({timeout:.0f}s without data)"
            )


def _is_mock_mode() -> bool:
    """是否启用 mock：环境变量 LLM_MODE=mock"""
    return os.environ.get("LLM_MODE", "").strip().lower() == "mock"


# ─────────────────────────────────────────────
# 连通性验证
# ─────────────────────────────────────────────

async def verify_openai(config: LLMConfig) -> tuple[bool, str]:
    """验证 OpenAI（或兼容模式）API Key。"""
    if _is_mock_mode():
        return True, f"[mock] 验证成功，模型：{config.model}"

    from openai import AsyncOpenAI, AuthenticationError, APIConnectionError

    try:
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=15.0,
        )
        response = await client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": _VERIFY_PROMPT}],
            max_tokens=5,
        )
        reply = response.choices[0].message.content or ""
        logger.info(f"OpenAI Key 验证成功，模型响应：{reply!r}")
        return True, f"验证成功，模型：{config.model}"

    except AuthenticationError:
        return False, "API Key 无效，请检查密钥是否正确"
    except APIConnectionError as e:
        return False, f"无法连接到 API 服务（{config.base_url}），请检查网络或 Base URL：{e}"
    except Exception as e:
        err_msg = str(e)
        if "model" in err_msg.lower() or "does not exist" in err_msg.lower():
            return False, f"模型「{config.model}」不存在，请检查模型名称"
        return False, f"验证失败：{err_msg}"


async def verify_claude(config: LLMConfig) -> tuple[bool, str]:
    """验证 Anthropic Claude API Key。"""
    if _is_mock_mode():
        return True, f"[mock] 验证成功，模型：{config.model}"

    import anthropic

    try:
        client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url if config.base_url != "https://api.anthropic.com" else None,
            timeout=15.0,
        )
        message = await client.messages.create(
            model=config.model,
            max_tokens=5,
            messages=[{"role": "user", "content": _VERIFY_PROMPT}],
        )
        reply = message.content[0].text if message.content else ""
        logger.info(f"Claude Key 验证成功，模型响应：{reply!r}")
        return True, f"验证成功，模型：{config.model}"

    except anthropic.AuthenticationError:
        return False, "API Key 无效，请检查密钥是否正确"
    except anthropic.APIConnectionError as e:
        return False, f"无法连接到 Anthropic API，请检查网络：{e}"
    except Exception as e:
        err_msg = str(e)
        if "model" in err_msg.lower():
            return False, f"模型「{config.model}」不存在，请检查模型名称"
        return False, f"验证失败：{err_msg}"


# ─────────────────────────────────────────────
# 一次性（非流式）调用
# ─────────────────────────────────────────────

def _warn_empty_oneshot(provider: str, model: str, max_tokens: int, detail: str) -> None:
    """响应为空时把可诊断的形状信息记下来，不含任何凭据。

    空响应是最难查的失败：调用本身没抛异常，下游只看到一句
    「未找到 JSON 起始 {：」（json_utils.extract_json_object 拼上空的 text[:120]），
    完全看不出是截断了、被思考 token 吃光了，还是别的。finish_reason 与
    reasoning_tokens 分开记，才能区分「max_tokens 用完」和「模型真的没吐东西」。
    """
    logger.warning(
        f"[{provider}/{model}] 一次性调用返回空内容（max_tokens={max_tokens}）：{detail}"
    )


class LLMEmptyResponseError(RuntimeError):
    """模型返回了空内容。

    空响应是最难查的失败：调用本身不抛异常，下游只看到一句「未找到 JSON 起始 {」。
    调用方传 ``raise_on_empty=True`` 时把它抛出来，好让上游把原因和下一步动作
    直接显示给用户，而不是只留在日志里。
    """


def _empty_message(provider: str, model: str, max_tokens: int, detail: str) -> str:
    return (
        f"[{provider}/{model}] 模型返回空内容（max_tokens={max_tokens}）：{detail}。"
        "推理型模型会把 max_tokens 连同思考一起用尽，请调大该值或改用非推理模型"
    )


async def generate_oneshot_openai(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
    timeout: float = 60.0,
    raise_on_empty: bool = False,
    call_site: str = "",
) -> str:
    if _is_mock_mode():
        return await _mock.respond(system_prompt, user_prompt, stream=False, max_tokens=max_tokens)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=timeout,
    )
    started = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
        )
    except Exception as e:
        await _emit_usage(config, call_site, streaming=False, fields={},
                          ok=False, error=str(e), started=started)
        raise

    choice = response.choices[0]
    content = choice.message.content or ""
    ok = bool(content.strip())
    detail = ""
    if not ok:
        detail = _openai_empty_detail(response, choice)
        _warn_empty_oneshot("openai", config.model, max_tokens, detail)

    # 空响应也要落账：它正是最需要留下用量证据的场景（思考 token 吃光预算）
    await _emit_usage(config, call_site, streaming=False,
                      fields=_openai_usage_fields(response),
                      ok=ok, error=detail, started=started)

    if not ok and raise_on_empty:
        raise LLMEmptyResponseError(
            _empty_message(config.provider, config.model, max_tokens, detail)
        )
    return content


def _openai_empty_detail(response, choice) -> str:
    """空响应的形状读数。字段可能因 SDK/提供商而异，一律 getattr 兜底。"""
    msg = choice.message
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    parts = [
        f"finish_reason={getattr(choice, 'finish_reason', None)!r}",
        f"content_len={len(msg.content or '')}",
        # 思考型模型把预算烧在 reasoning 上时 content 会是空串，这个长度是判据
        f"reasoning_len={len(getattr(msg, 'reasoning_content', None) or '')}",
        f"completion_tokens={getattr(usage, 'completion_tokens', None)}",
        f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)}",
    ]
    return " ".join(parts)


async def generate_oneshot_claude(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
    timeout: float = 60.0,
    raise_on_empty: bool = False,
    call_site: str = "",
) -> str:
    if _is_mock_mode():
        return await _mock.respond(system_prompt, user_prompt, stream=False, max_tokens=max_tokens)

    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=config.api_key,
        base_url=config.base_url if config.base_url != "https://api.anthropic.com" else None,
        timeout=timeout,
    )
    started = time.monotonic()
    try:
        message = await client.messages.create(
            model=config.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_tokens,
        )
    except Exception as e:
        await _emit_usage(config, call_site, streaming=False, fields={},
                          ok=False, error=str(e), started=started)
        raise

    ok = bool(message.content)
    detail = ""
    if not ok:
        # 空内容时没有 block 可看，stop_reason 是唯一线索（max_tokens / end_turn）
        detail = f"stop_reason={getattr(message, 'stop_reason', None)!r} content_blocks=0"
        _warn_empty_oneshot("claude", config.model, max_tokens, detail)

    await _emit_usage(config, call_site, streaming=False,
                      fields=_claude_usage_fields(getattr(message, "usage", None)),
                      ok=ok, error=detail, started=started)

    if not ok and raise_on_empty:
        raise LLMEmptyResponseError(
            _empty_message(config.provider, config.model, max_tokens, detail)
        )
    return message.content[0].text if message.content else ""


# ─────────────────────────────────────────────
# 流式调用
# ─────────────────────────────────────────────

# OpenAI 兼容实现未必都认 stream_options.include_usage。只有报出这类参数错误时
# 才退回不带用量的普通流式 —— 少一份用量数据，但不能让生成整条断掉。
_UNSUPPORTED_PARAM_HINTS = (
    "stream_options", "include_usage", "unrecognized", "unknown parameter", "unsupported",
)


async def stream_openai(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    call_site: str = "",
) -> AsyncIterator[str]:
    if _is_mock_mode():
        iterator = await _mock.respond(system_prompt, user_prompt, stream=True, max_tokens=4096)
        async for chunk in iterator:
            yield chunk
        return

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=120.0,
    )
    started = time.monotonic()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    async def _create(with_usage: bool):
        # 使用 create(stream=True) 替代 .stream() 上下文管理器，兼容性更广
        return await client.chat.completions.create(
            model=config.model,
            messages=messages,
            max_tokens=4096,
            stream=True,
            **({"stream_options": {"include_usage": True}} if with_usage else {}),
        )

    try:
        stream = await _create(with_usage=True)
    except Exception as e:
        if not any(h in str(e).lower() for h in _UNSUPPORTED_PARAM_HINTS):
            await _emit_usage(config, call_site, streaming=True, fields={},
                              ok=False, error=str(e), started=started)
            raise
        logger.info(f"OpenAI 流式不支持 include_usage（{e}），退回不带用量的流式")
        try:
            stream = await _create(with_usage=False)
        except Exception as e2:
            await _emit_usage(config, call_site, streaming=True, fields={},
                              ok=False, error=str(e2), started=started)
            raise

    fields: dict = {}
    try:
        async for chunk in _iter_with_chunk_timeout(stream, "OpenAI", CHUNK_TIMEOUT):
            # include_usage 的末帧只带 usage、choices 为空
            if getattr(chunk, "usage", None) is not None:
                fields = _openai_usage_fields(chunk)
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta is not None:
                yield delta
    except Exception as e:
        await _emit_usage(config, call_site, streaming=True, fields=fields,
                          ok=False, error=str(e), started=started)
        raise

    await _emit_usage(config, call_site, streaming=True, fields=fields,
                      ok=True, error="", started=started)


async def stream_claude(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    call_site: str = "",
) -> AsyncIterator[str]:
    if _is_mock_mode():
        iterator = await _mock.respond(system_prompt, user_prompt, stream=True, max_tokens=4096)
        async for chunk in iterator:
            yield chunk
        return

    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=config.api_key,
        base_url=config.base_url if config.base_url != "https://api.anthropic.com" else None,
        timeout=120.0,
    )
    started = time.monotonic()
    fields: dict = {}
    try:
        async with client.messages.stream(
            model=config.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=4096,
        ) as stream:
            async for text in _iter_with_chunk_timeout(stream.text_stream, "Claude", CHUNK_TIMEOUT):
                yield text
            # 用量只在收尾消息里，text_stream 走完才能拿到
            final = await stream.get_final_message()
            fields = _claude_usage_fields(getattr(final, "usage", None))
    except Exception as e:
        await _emit_usage(config, call_site, streaming=True, fields={},
                          ok=False, error=str(e), started=started)
        raise

    await _emit_usage(config, call_site, streaming=True, fields=fields,
                      ok=True, error="", started=started)
