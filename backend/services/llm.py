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
# 文档摘要生成（非流式）
# ─────────────────────────────────────────────

async def generate_doc_summary(
    config: LLMConfig,
    sections: list[dict],
) -> str:
    """
    根据所有章节标题和内容摘要，生成整篇文档的项目概述（~500字）。
    非流式调用，返回完整文本。
    """
    # 拼接所有章节的标题和内容作为输入
    input_parts = []
    for sec in sections:
        title = sec.get("title", "")
        content = sec.get("content", "")
        if content:
            input_parts.append(f"【{title}】{content[:300]}")
        else:
            input_parts.append(f"【{title}】")

    doc_outline = "\n".join(input_parts)
    # 限制输入长度避免超 token
    if len(doc_outline) > 6000:
        doc_outline = doc_outline[:6000] + "\n..."

    system_prompt = (
        "你是一位专业的技术方案分析专家，擅长从技术规范书中忠实提炼项目核心信息。"
        "你只基于原文内容进行提炼，不推断或补充原文未明确提及的信息。"
    )
    user_prompt = (
        "以下是一份技术规范书的章节目录和各章节原文内容摘要：\n\n"
        f"---\n{doc_outline}\n---\n\n"
        "请严格基于以上原文内容，提炼出该项目的整体概述，包括：\n"
        "1. 项目背景与目标\n"
        "2. 核心技术要求和约束条件\n"
        "3. 主要工作内容范围\n\n"
        "要求：\n"
        "- 字数控制在 500 字左右\n"
        "- 所有内容严格来源于原文，不添加原文未提及的内容\n"
        "- 语言精炼、信息密度高，突出关键技术指标和约束\n"
        "- 使用 Markdown 格式"
    )

    if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        return await _generate_oneshot_openai(config, system_prompt, user_prompt)
    elif config.provider == "claude":
        return await _generate_oneshot_claude(config, system_prompt, user_prompt)
    else:
        raise ValueError(f"不支持的 provider：{config.provider}")


async def _generate_oneshot_openai(config: LLMConfig, system_prompt: str, user_prompt: str) -> str:
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
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


async def _generate_oneshot_claude(config: LLMConfig, system_prompt: str, user_prompt: str) -> str:
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
        max_tokens=1500,
    )
    return message.content[0].text if message.content else ""


# ─────────────────────────────────────────────
# 流式生成
# ─────────────────────────────────────────────

async def stream_generate(
    config: LLMConfig,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
) -> AsyncIterator[str]:
    """
    流式生成单个章节的技术方案内容。

    Args:
        config:           LLM 配置
        section_title:    章节标题
        original_content: 章节原始内容（来自规范书）
        target_words:     目标字数（默认 500 字）
        doc_summary:      文档整体摘要（作为全局上下文）

    Yields:
        str — 每次 yield 一个 token 片段
    """
    system_prompt = (
        "你是一位专业的政企信息化项目方案撰写专家，擅长对技术规范书原文进行逐段忠实扩写。"
        "你的核心职责是：完全保留原文核心含义与逻辑框架，在此基础上补充背景释义、功能价值和应用场景，"
        "采用政企项目方案正式书面文风，不篡改原意，不新增无关内容。"
        "请使用 Markdown 格式输出。"
    )

    # 构建 user_prompt：先注入项目整体摘要，再给出章节内容
    parts = []
    if doc_summary:
        parts.append(
            f"以下是该项目的整体概述：\n\n"
            f"===\n{doc_summary}\n===\n\n"
        )
    parts.append(
        f"以下是技术规范书中关于「{section_title}」的原文内容结构：\n\n"
        f"---\n{original_content}\n---\n\n"
        f"请严格按照提供的原文逐段进行独立扩写，一段原文对应一段扩写内容，保持原有段落结构不变。\n"
        f"扩写后内容字数不少于 {target_words} 字。\n\n"
        f"核心规则：\n"
        f"1. 忠于原意：完全保留原文核心含义、业务定位、逻辑框架，不篡改、不删减、不新增无关内容。\n"
        f"2. 文风标准：采用政企、园区、数字化平台、项目方案正式书面文风，语言严谨专业、通顺流畅、格调正式。\n"
        f"3. 扩写逻辑：在原文基础上补充背景释义、功能价值、作用意义、应用场景，拉长句式、丰富表述，合理扩充篇幅，不空洞凑字、不堆砌冗余语句。\n"
        f"4. 格式规范：严格保留原文的段落结构与小标题，不改变原有段落顺序；在每个小标题后紧接输出扩写正文，段落之间用空行分隔，排版整齐；禁止输出任何自创的段落编号标签（如'第一段''第一段扩写内容'等）。\n"
        f"5. 专业适配：贴合平台运营、资源汇聚、系统整合、生态建设、服务输出、账号统一、办公联动等政企信息化通用专业语境，用词贴合汇报材料、建设方案、平台介绍文案风格。"
    )
    user_prompt = "".join(parts)

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
