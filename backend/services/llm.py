"""
LLM 调用封装
支持 OpenAI（含兼容模式）、Anthropic Claude、豆包、Kimi 等 provider。
豆包和 Kimi 均使用 OpenAI 兼容接口。
多 API 配置时支持轮询 + 故障 fallback。
"""

import logging
from typing import AsyncGenerator, AsyncIterator
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


async def _verify_claude(config: LLMConfig) -> tuple[bool, str]:
    """验证 Anthropic Claude API Key"""
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
# 文档摘要生成（非流式）
# ─────────────────────────────────────────────

async def generate_doc_summary(
    config: LLMConfig,
    sections: list[dict],
) -> str:
    """
    根据所有章节标题和内容，生成整篇文档的结构化项目概述。
    非流式调用，返回完整文本。
    """
    # 按内容长度加权分配字符配额，内容丰富的节获得更多配额
    TOTAL_BUDGET = 8000
    sections_with_content = [(s, len(s.get("content", ""))) for s in sections]
    total_len = sum(l for _, l in sections_with_content) or 1

    input_parts = []
    for sec, content_len in sections_with_content:
        title = sec.get("title", "")
        content = sec.get("content", "")
        if content:
            # 按内容比例分配配额，最少 100 字、最多 1500 字
            quota = max(100, min(1500, int(TOTAL_BUDGET * content_len / total_len)))
            input_parts.append(f"【{title}】\n{content[:quota]}")
        else:
            input_parts.append(f"【{title}】")

    doc_outline = "\n\n".join(input_parts)
    if len(doc_outline) > TOTAL_BUDGET:
        doc_outline = doc_outline[:TOTAL_BUDGET] + "\n..."

    system_prompt = (
        "你是一位专业的技术方案分析专家，擅长从技术规范书中忠实提炼项目核心信息。"
        "你只基于原文内容进行提炼，不推断或补充原文未明确提及的信息。"
        "提炼结果必须保留原文中所有数值、技术指标、标准编号，不得模糊化处理。"
    )
    user_prompt = (
        "以下是一份技术规范书的章节目录和各章节原文内容：\n\n"
        f"---\n{doc_outline}\n---\n\n"
        "请严格基于以上原文，按如下结构提炼项目核心信息：\n\n"
        "## 项目基本信息\n"
        "- 项目名称：（从原文提取，如无则填【未明确】）\n"
        "- 建设单位（甲方）：（从原文提取，如无则填【未明确】）\n"
        "- 承建单位（乙方）：（从原文提取，如无则填【未明确】）\n\n"
        "## 核心技术要求\n"
        "（每条一行，用-列出，必须保留原文中的具体数值和技术指标）\n\n"
        "## 硬性约束条件\n"
        "（工期要求、验收标准、引用的规范标准编号等，每条一行）\n\n"
        "## 主要建设范围\n"
        "（主要功能模块或建设内容，每条一行）\n\n"
        "要求：\n"
        "- 所有内容严格来源于原文，不添加原文未提及的内容\n"
        "- 保留所有数值、百分比、标准编号等关键技术参数原文\n"
        "- 如原文中某项信息确实不存在，该条目填【原文未提及】\n"
        "- 总字数控制在 800 字以内"
    )

    if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        return await _generate_oneshot_openai(config, system_prompt, user_prompt, max_tokens=2000)
    elif config.provider == "claude":
        return await _generate_oneshot_claude(config, system_prompt, user_prompt, max_tokens=2000)
    else:
        raise ValueError(f"不支持的 provider：{config.provider}")


async def _generate_oneshot_openai(config: LLMConfig, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
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


async def _generate_oneshot_claude(config: LLMConfig, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
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
# 流式生成
# ─────────────────────────────────────────────

async def stream_generate(
    config: LLMConfig,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
    tone: str = "official",
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
    _SYSTEM_PROMPTS = {
        "official": (
            "你是一位专业的政企信息化项目方案撰写专家，擅长对技术规范书原文进行逐段忠实扩写。"
            "你的核心职责是：完全保留原文核心含义与逻辑框架，在此基础上补充背景释义、功能价值和应用场景，"
            "采用政企项目方案正式书面文风，不篡改原意，不新增无关内容。"
            "请使用 Markdown 格式输出。"
        ),
        "tech": (
            "你是一位资深技术架构师，擅长将技术规范书内容转化为精准的技术方案描述。"
            "你的核心职责是：保留原文逻辑框架，使用准确的技术术语和架构语言，"
            "突出系统设计、接口规范、性能指标等技术要素，逻辑严密、表述精准。"
            "请使用 Markdown 格式输出。"
        ),
        "concise": (
            "你是一位精简表达专家，擅长将技术规范书内容提炼为简洁有力的方案文字。"
            "你的核心职责是：保留原文核心信息，去除冗余修饰，每句话都有实际信息量，"
            "句式简短清晰，避免空泛表述和套话。"
            "请使用 Markdown 格式输出。"
        ),
    }
    system_prompt = _SYSTEM_PROMPTS.get(tone, _SYSTEM_PROMPTS["official"])

    # 构建 user_prompt：先注入项目整体摘要，再给出章节内容
    parts = []
    if doc_summary:
        # 截断保护：摘要最多注入 3000 字符，避免超窗
        safe_summary = doc_summary[:3000] + ("\n..." if len(doc_summary) > 3000 else "")
        parts.append(
            f"以下是该项目的整体概述：\n\n"
            f"===\n{safe_summary}\n===\n\n"
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

    if doc_template:
        # 截断保护：模板最多注入 5000 字符
        safe_template = doc_template[:5000] + ("\n..." if len(doc_template) > 5000 else "")
        user_prompt += (
            f"\n\n请严格参照以下模板的结构和风格来组织本章节的输出内容，"
            f"保持模板的段落结构顺序，用实际内容填充各部分：\n\n"
            f"===模板===\n{safe_template}\n===模板结束==="
        )

    if extra_prompt:
        user_prompt += f"\n\n额外优化要求：{extra_prompt}"

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
        if delta is not None:
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


# ─────────────────────────────────────────────
# 多 API 调度（轮询 + Fallback）
# ─────────────────────────────────────────────

async def dispatch_stream_generate(
    configs: list[LLMConfig],
    rr_start_index: int,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
) -> AsyncGenerator[str, None]:
    """
    从 rr_start_index 指定的 API 开始尝试流式生成，失败时自动 fallback 到下一个。

    Fallback 策略：
    - 仅在首个 token 到达之前发生的错误才 fallback（连接失败、认证失败等）
    - 一旦开始 yield token，说明流已建立，此时的错误由外层重试逻辑处理，
      避免将已发送的 token 与新一轮生成内容混在一起造成重复。
    全部 API 均在首 token 前失败时，上抛最后一个异常。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先在「设置」中添加配置")

    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        started = False  # 是否已开始 yield token
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 生成章节「{section_title}」")
            async for token in stream_generate(config, section_title, original_content, target_words, doc_summary, extra_prompt, doc_template):
                started = True
                yield token
            return  # 成功，退出
        except Exception as e:
            if started:
                # 流已开始，不能 fallback（前端已收到部分内容），直接上抛
                raise
            last_error = e
            logger.warning(
                f"API [{config.provider}/{config.model}] 连接失败，"
                f"{'尝试下一个' if i < n - 1 else '已无可用 API'}：{e}"
            )

    raise last_error  # type: ignore[misc]


async def dispatch_doc_summary(
    configs: list[LLMConfig],
    rr_start_index: int,
    sections: list[dict],
) -> str:
    """
    从 rr_start_index 指定的 API 开始尝试生成文档摘要，失败时自动 fallback 到下一个。
    全部失败时上抛最后一个异常。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先在「设置」中添加配置")

    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 生成文档摘要")
            return await generate_doc_summary(config, sections)
        except Exception as e:
            last_error = e
            logger.warning(
                f"API [{config.provider}/{config.model}] 摘要生成失败，"
                f"{'尝试下一个' if i < n - 1 else '已无可用 API'}：{e}"
            )

    raise last_error  # type: ignore[misc]


async def dispatch_section_requirement(
    configs: list[LLMConfig],
    rr_index: int,
    section_title: str,
    section_content: str,
    special_marks: list[str] | None = None,
) -> dict:
    """
    对单个章节提炼核心要求，返回结构化字典。

    Returns:
        {
            "title": str,
            "requirement": str,          # 本章核心技术要求
            "key_points": [str],         # 应标重点（3~5条）
            "hard_constraints": [str],   # 否决项/强制要求
        }
    失败时返回 fallback 结构，不上抛异常，由调用方决定是否跳过。
    """
    import json, re

    system = (
        "你是专业投标方案顾问。根据用户提供的技术规范书章节原文，"
        "提炼本章核心要求。只输出 JSON，不要有任何额外说明。"
    )
    marks_hint = ""
    if special_marks:
        if "★" in special_marks:
            marks_hint = "注意：本章包含否决条款（★），请在 hard_constraints 中明确列出。"
        elif "▲" in special_marks:
            marks_hint = "注意：本章包含加分项（▲），请在 key_points 中标注。"

    user = (
        f"以下是技术规范书中「{section_title}」章节的原文：\n\n"
        f"{section_content}\n\n"
        f"{marks_hint}\n"
        "请提炼并输出 JSON（字段说明：requirement=本章核心技术要求描述，"
        "key_points=应标方需重点响应的要点列表3~5条，"
        "hard_constraints=否决项或强制要求列表，无则为空数组）：\n"
        '{"requirement": "...", "key_points": ["..."], "hard_constraints": ["..."]}'
    )

    n = len(configs)
    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_index + i) % n]
        try:
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await _generate_oneshot_openai(config, system, user, max_tokens=800)
            else:
                result = await _generate_oneshot_claude(config, system, user, max_tokens=800)

            match = re.search(r'\{.*\}', result, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                return {
                    "title": section_title,
                    "requirement": parsed.get("requirement", ""),
                    "key_points": parsed.get("key_points", []),
                    "hard_constraints": parsed.get("hard_constraints", []),
                }
        except Exception as e:
            last_error = e
            logger.warning(f"章节「{section_title}」提炼失败（API {i+1}/{n}）：{e}")

    # 全部 API 失败，返回 fallback（保留原始内容片段）
    logger.error(f"章节「{section_title}」所有 API 均失败，使用 fallback：{last_error}")
    return {
        "title": section_title,
        "requirement": section_content[:300] if section_content else "",
        "key_points": [],
        "hard_constraints": ["★"] if special_marks and "★" in special_marks else [],
    }


async def dispatch_block_write(
    configs: list,
    rr_start_index: int,
    title: str,
    requirement: str,
    chunks: list[dict],
    target_words: int = 600,
):
    """
    为单个 block 流式生成正文内容。
    将 requirement + chunks 拼入 extra_prompt，复用 dispatch_stream_generate。
    """
    snippets = "\n".join(
        f"- {c['content'][:200]}" for c in chunks
    )
    extra_prompt = ""
    if requirement:
        extra_prompt += f"【应标要求】\n{requirement}\n\n"
    if snippets:
        extra_prompt += f"【参考素材】\n{snippets}"

    async for token in dispatch_stream_generate(
        configs=configs,
        rr_start_index=rr_start_index,
        section_title=title,
        original_content="",
        target_words=target_words,
        extra_prompt=extra_prompt,
    ):
        yield token


# ─────────────────────────────────────────────
# 大纲生成（非流式，返回结构化列表）
# ─────────────────────────────────────────────

async def dispatch_outline_json(
    configs: list[LLMConfig],
    rr_start_index: int,
    combined_text: str,
) -> list[dict]:
    """
    根据应标文件合并文本，生成结构化大纲列表。

    Returns:
        [{"title": str, "requirement": str}, ...]
    全部 API 失败时上抛最后一个异常。
    """
    import json, re

    BUDGET = 12000
    if len(combined_text) > BUDGET:
        combined_text = combined_text[:BUDGET] + "\n..."

    system = (
        "你是专业投标方案顾问，擅长从技术规范书中提炼应标大纲章节结构。"
        "只输出合法 JSON 数组，不包含任何额外说明或 markdown 代码块。"
    )
    user = (
        "以下是一份应标文件（技术规范书/招标文件）的章节内容：\n\n"
        f"---\n{combined_text}\n---\n\n"
        "请根据以上内容，为投标方生成一份应标方案大纲，要求：\n"
        "1. 提取 8~15 个应标响应章节，覆盖文件的核心应答要点\n"
        "2. 每个章节包含标题和对应的核心应标要求描述\n"
        "3. 标题使用规范的方案章节名称（如「项目概述」「技术方案」「实施计划」等）\n"
        "4. requirement 字段简明描述本章需要响应的具体内容（50字以内）\n\n"
        "只输出 JSON 数组，格式如下：\n"
        '[{"title": "项目概述", "requirement": "..."}, ...]'
    )

    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"生成大纲，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await _generate_oneshot_openai(config, system, user, max_tokens=2000)
            else:
                result = await _generate_oneshot_claude(config, system, user, max_tokens=2000)

            # 提取 JSON 数组
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if not match:
                raise ValueError(f"模型未返回 JSON 数组，原始输出：{result[:200]}")
            items = json.loads(match.group())
            if not isinstance(items, list) or len(items) == 0:
                raise ValueError("JSON 数组为空")
            # 规范化字段
            return [
                {"title": str(item.get("title", f"章节 {idx+1}")),
                 "requirement": str(item.get("requirement", ""))}
                for idx, item in enumerate(items)
                if isinstance(item, dict)
            ]
        except Exception as e:
            last_error = e
            logger.warning(f"大纲生成失败（API {i+1}/{n}）：{e}")

    raise last_error  # type: ignore[misc]
