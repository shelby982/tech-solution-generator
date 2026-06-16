"""
LLM 业务编排层：
- 多 API 配置轮询 + Fallback
- prompt 构造 / JSON 抽取
- 公开 dispatch_* / generate_* 入口
- 公文章节识别（is_letter_section / LETTER_KEYWORDS）

I/O 细节由 clients.py 承担；本模块只做 prompt 与调度。
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, AsyncIterator

from agents.prompts import (
    LETTER_SYSTEM,
    OUTLINE_EXTRACT_SYSTEM,
    SECTION_OUTLINE_SYSTEM,
    TONE_SYSTEM_PROMPTS,
    build_letter_user,
    build_outline_extract_user,
    build_section_outline_user,
)
from services.config_store import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS

from . import clients
from .json_utils import extract_json_object as _extract_json_object

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 公开：连通性验证（按 provider 分发）
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
            return await clients.verify_openai(config)
        elif config.provider == "claude":
            return await clients.verify_claude(config)
        else:
            return False, f"不支持的 provider：{config.provider}"
    except Exception as e:
        logger.exception(f"API Key 验证异常：{e}")
        return False, f"验证过程发生异常：{str(e)}"


# ─────────────────────────────────────────────
# 文档摘要（非流式）
# ─────────────────────────────────────────────

async def _generate_doc_summary(
    config: LLMConfig,
    sections: list[dict],
) -> str:
    """
    根据所有章节标题和内容，生成整篇文档的结构化项目概述。
    非流式调用，返回完整文本。
    """
    # 按内容长度加权分配字符配额：每章配额 = TOTAL_BUDGET * (该章长度 / 全部章节总长度)，
    # 至少 100 字。遍历时用剩余预算限制单章配额，使总长度自然落在 TOTAL_BUDGET 内。
    TOTAL_BUDGET = 8000
    sections_with_content = [(s, len(s.get("content", ""))) for s in sections]
    total_len = sum(l for _, l in sections_with_content) or 1

    remaining = TOTAL_BUDGET
    input_parts = []
    for sec, content_len in sections_with_content:
        title = sec.get("title", "")
        content = sec.get("content", "")
        if not content:
            input_parts.append(f"【{title}】")
            continue
        if remaining <= 0:
            input_parts.append(f"【{title}】")
            continue
        quota = max(100, int(TOTAL_BUDGET * content_len / total_len))
        quota = min(quota, remaining)
        snippet = content[:quota]
        input_parts.append(f"【{title}】\n{snippet}")
        remaining -= len(snippet)

    doc_outline = "\n\n".join(input_parts)

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
        return await clients.generate_oneshot_openai(config, system_prompt, user_prompt, max_tokens=2000)
    elif config.provider == "claude":
        return await clients.generate_oneshot_claude(config, system_prompt, user_prompt, max_tokens=2000)
    else:
        raise ValueError(f"不支持的 provider：{config.provider}")


# ─────────────────────────────────────────────
# 流式生成（单章节正文）
# ─────────────────────────────────────────────


async def _stream_generate(
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
    system_prompt = TONE_SYSTEM_PROMPTS.get(tone, TONE_SYSTEM_PROMPTS["official"])

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
        f"5. 专业适配：贴合平台运营、资源汇聚、系统整合、生态建设、服务输出、账号统一、办公联动等政企信息化通用专业语境，用词贴合汇报材料、建设方案、平台介绍文案风格。\n"
        f"6. 应标者身份（强约束）：使用第一人称「我方」「本公司」「我公司」撰写，禁止第三人称「投标方」「投标人」；"
        f"对要求逐条做明确响应/承诺（「我方将…」「我方已…」「本公司承诺…」「我方完全响应…」「我方满足…」），"
        f"禁止「应当」「应该」「建议」「需要」「可以」等咨询/分析口吻，"
        f"禁止笼统讨论应该如何应答，禁止元层次描述（「本节将阐述…」「以下从X方面说明…」），直接进入实质内容。"
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

    # 当有应标指导或参考素材时，要求在 Markdown 输出中用标记包裹不同来源的内容
    if extra_prompt and ("【应标指导】" in extra_prompt or "【参考素材】" in extra_prompt):
        user_prompt += (
            "\n\n来源标注要求：在输出的 Markdown 正文中，用以下标记成对包裹来自不同来源的内容片段：\n"
            "- 来自【应标指导】（核心要求/策略要点/否决项/加分项）的内容用 [G]...[/G] 包裹\n"
            "- 来自【参考素材】的内容用 [M]...[/M] 包裹\n"
            "- 你基于上下文合理补充的内容用 [E]...[/E] 包裹\n"
            "标记必须成对出现，标记内只放纯文本不嵌套；标记不影响 Markdown 段落和标题结构；"
            "正常使用 Markdown 语法（标题、列表、加粗等），只在内容片段外侧加这些来源标记。"
        )

    if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        async for token in clients.stream_openai(config, system_prompt, user_prompt):
            yield token
    elif config.provider == "claude":
        async for token in clients.stream_claude(config, system_prompt, user_prompt):
            yield token
    else:
        raise ValueError(f"不支持的 provider：{config.provider}")


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
    tone: str = "official",
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
            async for token in _stream_generate(
                config, section_title, original_content, target_words,
                doc_summary, extra_prompt, doc_template, tone,
            ):
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
            return await _generate_doc_summary(config, sections)
        except Exception as e:
            last_error = e
            logger.warning(
                f"API [{config.provider}/{config.model}] 摘要生成失败，"
                f"{'尝试下一个' if i < n - 1 else '已无可用 API'}：{e}"
            )

    raise last_error  # type: ignore[misc]


async def dispatch_block_write(
    configs: list[LLMConfig],
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
    sections: list[dict],
):
    """
    按文档原始章节并发提炼响应矩阵（8 字段），按 idx 顺序逐章 yield。

    并发上限 5；每章节内部仍按"轮询 + Fallback"试遍所有 config，起点按 idx 散开。

    sections: [{"title": str, "content": str, "special_marks": str (optional),
                "scoring_context": str (optional), "evaluation_context": str (optional)}, ...]
    Yields: {"title": str, "requirement": str, "key_points": str, "veto_items": str,
             "bonus_items": str, "score_items": str, "evidence_required": str,
             "constraint_level": str, "indicators": str, "error": str}

    error 为空字符串表示成功；非空则表示该章节全部 API 失败，其它字段是占位空值。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    sem = asyncio.Semaphore(5)

    async def _worker(idx: int, sec: dict) -> dict:
        async with sem:
            return await _extract_one_section(idx, sec, configs, rr_start_index, n)

    tasks = [asyncio.create_task(_worker(i, s)) for i, s in enumerate(sections)]
    try:
        for t in tasks:
            yield await t
    except BaseException:
        for t in tasks:
            if not t.done():
                t.cancel()
        raise


async def _extract_one_section(
    idx: int,
    sec: dict,
    configs: list[LLMConfig],
    rr_start_index: int,
    n: int,
) -> dict:
    """单章节提炼 worker：按 (rr_start_index + idx) 起点轮询全部 API，全失败时返回空占位。"""
    title = sec["title"]
    user = build_outline_extract_user(
        title=title,
        content=sec.get("content", "") or "",
        special_marks=sec.get("special_marks", "") or "",
        scoring_context=sec.get("scoring_context") or "",
        evaluation_context=sec.get("evaluation_context") or "",
    )

    last_error: Exception | None = None
    chain_start = (rr_start_index + idx) % n
    for i in range(n):
        config = configs[(chain_start + i) % n]
        try:
            logger.info(f"提炼章节「{title}」，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(config, OUTLINE_EXTRACT_SYSTEM, user, max_tokens=4000)
            else:
                result = await clients.generate_oneshot_claude(config, OUTLINE_EXTRACT_SYSTEM, user, max_tokens=4000)

            obj = _extract_json_object(result)
            return {
                "title": title,
                "requirement": str(obj.get("requirement", "")),
                "key_points": str(obj.get("key_points", "")),
                "veto_items": str(obj.get("veto_items", "")),
                "bonus_items": str(obj.get("bonus_items", "")),
                "score_items": str(obj.get("score_items", "")),
                "evidence_required": str(obj.get("evidence_required", "")),
                "constraint_level": str(obj.get("constraint_level", "recommended")),
                "indicators": str(obj.get("indicators", "")),
                "error": "",
            }
        except Exception as e:
            last_error = e
            logger.warning(f"章节「{title}」提炼失败（API {i+1}/{n}）：{e}")

    logger.error(f"章节「{title}」全部 API 失败，使用空占位：{last_error}")
    return {
        "title": title,
        "requirement": "", "key_points": "",
        "veto_items": "", "bonus_items": "",
        "score_items": "", "evidence_required": "",
        "constraint_level": "recommended", "indicators": "",
        "error": f"全部 API 失败：{last_error}" if last_error else "提炼失败",
    }


# ─────────────────────────────────────────────
# 模块写作大纲（生成正文前的中间步骤，含表格样例与占位符）
# ─────────────────────────────────────────────


async def generate_section_outline(
    configs: list[LLMConfig],
    rr_start_index: int,
    block: dict,
    extra_context: str = "",
) -> str:
    """
    为单个 block 生成"写作大纲"（含表格样例 + 占位符），用于约束后续正文生成。
    轮询 + Fallback 策略：从 rr_start_index 起依次尝试，全部失败时返回空字符串。
    """
    n = len(configs)
    if n == 0:
        return ""

    user_prompt = build_section_outline_user(block, extra_context)
    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"为「{block.get('title')}」生成写作大纲，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, SECTION_OUTLINE_SYSTEM, user_prompt, max_tokens=2500,
                )
            return await clients.generate_oneshot_claude(
                config, SECTION_OUTLINE_SYSTEM, user_prompt, max_tokens=2500,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"模块大纲生成失败（API {i+1}/{n}）：{e}")

    logger.error(f"模块大纲全部 API 失败：{last_error}")
    return ""


# ─────────────────────────────────────────────
# 承诺书 / 保证函 / 声明书等公文类章节专用生成
# ─────────────────────────────────────────────

# Phase 2.5 起，公文识别的真实定义在 backend/domain/letter_detector.py。
# 此处保留 re-export 以兼容仍按旧路径 `from infra.llm.dispatcher import is_letter_section`
# 的调用点；Phase 7 服务层删除时一并清理 re-export。
from domain.letter_detector import LETTER_KEYWORDS, is_letter_section  # noqa: F401


async def generate_letter_content(
    configs: list[LLMConfig],
    rr_start_index: int,
    block: dict,
) -> str:
    """承诺书/保证函/声明书等公文类章节的专用生成（非流式）。"""
    n = len(configs)
    if n == 0:
        return ""

    user_prompt = build_letter_user(block)
    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"为公文章节「{block.get('title')}」生成内容，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, LETTER_SYSTEM, user_prompt, max_tokens=2500,
                )
            return await clients.generate_oneshot_claude(
                config, LETTER_SYSTEM, user_prompt, max_tokens=2500,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"公文生成失败（API {i+1}/{n}）：{e}")

    logger.error(f"公文章节全部 API 失败：{last_error}")
    return ""
