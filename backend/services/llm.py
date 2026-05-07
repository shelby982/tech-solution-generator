"""
LLM 调用封装
支持 OpenAI（含兼容模式）、Anthropic Claude、豆包、Kimi 等 provider。
豆包和 Kimi 均使用 OpenAI 兼容接口。
"""

import logging
from typing import AsyncIterator
from services.config_store import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS

logger = logging.getLogger(__name__)

# 验证用的极小 prompt（消耗极少 Token）
_VERIFY_PROMPT = "Reply with the single word: ok"


# ─────────────────────────────────────────────
# 连通性验证
# ─────────────────────────────────────────────

async def verify_api_key(config: LLMConfig) -> tuple[bool, str]:
    """
    验证 API Key 是否有效。
    发送极小 prompt，检查是否能正常返回。

    Returns:
        (success: bool, message: str)
    """
    try:
        if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
            return await _verify_openai(config)
        elif config.provider == "claude":
            return await _verify_claude(config)
        else:
            return False, f"不支持的 provider：{config.provider}"
    except Exception as e:
        logger.exception(f"API Key 验证异常：{e}")
        return False, f"验证过程发生异常：{str(e)}"


async def _verify_openai(config: LLMConfig) -> tuple[bool, str]:
    """验证 OpenAI（或兼容模式）API Key"""
    try:
        from openai import AsyncOpenAI, AuthenticationError, APIConnectionError

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


async def _verify_claude(config: LLMConfig) -> tuple[bool, str]:
    """验证 Anthropic Claude API Key"""
    try:
        import anthropic

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
# 流式生成
# ─────────────────────────────────────────────

async def stream_generate(
    config: LLMConfig,
    section_title: str,
    original_content: str,
    target_words: int = 500,
) -> AsyncIterator[str]:
    """
    流式生成单个章节的技术方案内容。

    Args:
        config:           LLM 配置
        section_title:    章节标题
        original_content: 章节原始内容（来自规范书）
        target_words:     目标字数（默认 500 字）

    Yields:
        str — 每次 yield 一个 token 片段
    """
    system_prompt = (
        "你是一位专业的技术方案撰写专家，擅长基于技术规范书撰写详细的技术实施方案。"
        "请使用 Markdown 格式输出，内容要专业、具体、可落地。"
    )
    user_prompt = (
        f"以下是技术规范书中关于「{section_title}」的原始描述和要求：\n\n"
        f"---\n{original_content}\n---\n\n"
        f"请基于以上规范，撰写本章节完整的技术方案内容。\n"
        f"要求：\n"
        f"1. 内容字数不少于 {target_words} 字\n"
        f"2. 深度扩展原文内容，覆盖实施细节、注意事项与技术选型依据\n"
        f"3. 结合实际技术选型，给出具体可落地的实施建议\n"
        f"4. 适当增加图表说明（使用 Markdown 表格）\n"
        f"5. 保持与规范书整体风格一致"
    )

    if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        async for token in _stream_openai(config, system_prompt, user_prompt):
            yield token
    elif config.provider == "claude":
        async for token in _stream_claude(config, system_prompt, user_prompt):
            yield token
    else:
        raise ValueError(f"不支持的 provider：{config.provider}")


async def _stream_openai(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
) -> AsyncIterator[str]:
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
        if delta:
            yield delta


async def _stream_claude(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
) -> AsyncIterator[str]:
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
