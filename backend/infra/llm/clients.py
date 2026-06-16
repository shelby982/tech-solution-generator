"""
LLM 提供商客户端层：纯 I/O，构造 OpenAI / Anthropic 客户端，
直接做验证 / 流式 / 一次性调用。

mock 模式：环境变量 `LLM_MODE=mock` 时短路到 `_mock.respond()`，
不发起真实网络调用。
"""

import logging
import os
from typing import AsyncIterator

from services.config_store import LLMConfig

from . import _mock

logger = logging.getLogger(__name__)

# 验证用的极小 prompt（消耗极少 Token）
_VERIFY_PROMPT = "Reply with the single word: ok"


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

async def generate_oneshot_openai(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
) -> str:
    if _is_mock_mode():
        return await _mock.respond(system_prompt, user_prompt, stream=False, max_tokens=max_tokens)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=60.0,
    )
    response = await client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


async def generate_oneshot_claude(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
) -> str:
    if _is_mock_mode():
        return await _mock.respond(system_prompt, user_prompt, stream=False, max_tokens=max_tokens)

    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=config.api_key,
        base_url=config.base_url if config.base_url != "https://api.anthropic.com" else None,
        timeout=60.0,
    )
    message = await client.messages.create(
        model=config.model,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    )
    return message.content[0].text if message.content else ""


# ─────────────────────────────────────────────
# 流式调用
# ─────────────────────────────────────────────

async def stream_openai(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
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
    # 使用 create(stream=True) 替代 .stream() 上下文管理器，兼容性更广
    stream = await client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4096,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta is not None:
            yield delta


async def stream_claude(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
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
    async with client.messages.stream(
        model=config.model,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4096,
    ) as stream:
        async for text in stream.text_stream:
            yield text
