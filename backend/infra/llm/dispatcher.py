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

from services.config_store import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS

from . import clients

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

_BIDDER_IDENTITY_RULE = (
    "【应标者身份强约束】"
    "你的身份是【投标人本身】，正在撰写投标文件正文。"
    "必须使用第一人称：主语用「我方」「本公司」「我公司」「我单位」，禁止用第三人称（如「投标方」「投标人」「投标单位」）。"
    "对每条要求做出明确响应或承诺，使用肯定语气（「我方将…」「我方已…」「本公司承诺…」「我方满足…」「我方完全响应…」）。"
    "禁止使用咨询/分析口吻（如「应当…」「应该…」「建议…」「可以…」「需要…」「投标方需…」），"
    "禁止笼统讨论该如何应答，禁止任何元层次描述（如「本节将阐述…」「以下从X方面进行说明…」）。"
)


_TONE_SYSTEM_PROMPTS: dict[str, str] = {
    "official": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "在原文基础上补充背景释义、功能价值和应用场景，采用政企项目方案正式书面文风，"
        "不篡改原意，不新增无关内容。"
        "请使用 Markdown 格式输出。\n"
        + _BIDDER_IDENTITY_RULE
    ),
    "tech": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "保留原文逻辑框架，使用准确的技术术语和架构语言，"
        "突出系统设计、接口规范、性能指标等技术要素，逻辑严密、表述精准。"
        "请使用 Markdown 格式输出。\n"
        + _BIDDER_IDENTITY_RULE
    ),
    "concise": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "保留原文核心信息，去除冗余修饰，每句话都有实际信息量，"
        "句式简短清晰，避免空泛表述和套话。"
        "请使用 Markdown 格式输出。\n"
        + _BIDDER_IDENTITY_RULE
    ),
}


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
    system_prompt = _TONE_SYSTEM_PROMPTS.get(tone, _TONE_SYSTEM_PROMPTS["official"])

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

def _extract_json_object(text: str) -> dict:
    """
    从模型返回里抽出第一个完整 JSON 对象。先剥 ``` / ```json 围栏，
    再用栈匹配第一对 {...}（跳过字符串内的 { 和 }）。
    抛 ValueError 表示找不到合法 JSON。
    """
    s = text.strip()
    # 剥围栏
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()

    start = s.find("{")
    if start == -1:
        raise ValueError(f"未找到 JSON 起始 {{：{text[:120]}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError(f"JSON 对象未闭合：{text[:120]}")


_OUTLINE_SYSTEM_PROMPT = (
    "你是专业的技术应标顾问，擅长从招标技术规范书中提炼响应矩阵。\n"
    "分析时重点识别：\n"
    "1. 强制性要求：含'必须''不得''否则废标''★''资格无效''一票否决''强制'等标志\n"
    "2. 评分标准：含'▲''加X分''满分条件''评分档位''得分'等标志\n"
    "3. 证明材料：含'提供''证书''合同''证明''原件''复印件''报告'等标志\n"
    "4. 量化指标：含具体数值、时限、百分比、性能参数、'不低于''不超过''至少'等\n"
    "5. 否决项和加分项请尽量引用原文表述，保持准确\n"
    "6. 如果章节内容较短或属于通用说明，相应字段可留空\n"
    "7. 当用户提供【评分上下文】或【评审上下文】时，请在 requirement 字段末尾用"
    "「【评分对应】XXX 评分项 X分」「【评审对应】XXX」格式追加对应条款，便于审阅人定位\n"
    "只输出合法 JSON 对象，不包含任何额外说明或 markdown 代码块。"
)


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
    content = sec.get("content", "") or ""
    if len(content) > 16000:
        content = content[:16000] + "..."

    marks = sec.get("special_marks", "") or ""
    marks_hint = ""
    if "★" in marks:
        marks_hint += "\n【注意】本章节标记有★（否决条款），请重点提取 veto_items 和 constraint_level=mandatory。"
    if "▲" in marks:
        marks_hint += "\n【注意】本章节标记有▲（加分项），请重点提取 bonus_items 和 score_items。"

    scoring_ctx = (sec.get("scoring_context") or "").strip()
    evaluation_ctx = (sec.get("evaluation_context") or "").strip()
    extra_blocks = ""
    if scoring_ctx:
        extra_blocks += f"\n\n【评分上下文（来自评分表）】\n{scoring_ctx[:3000]}"
    if evaluation_ctx:
        extra_blocks += f"\n\n【评审上下文（来自评审要素）】\n{evaluation_ctx[:3000]}"

    user = (
        f"以下是招标文件中「{title}」章节的内容：\n\n"
        f"---\n{content}\n---\n"
        f"{marks_hint}"
        f"{extra_blocks}\n\n"
        "请针对本章节提炼以下八项内容，输出单个 JSON 对象：\n"
        "- requirement：本章节对投标方的核心技术要求，要求**全面提炼、覆盖原文所有要点、不遗漏关键信息**，"
        "采用分点列出（用\\n分隔），不限字数；"
        "若有【评分上下文】或【评审上下文】，请在末尾用「【评分对应】XXX X分」「【评审对应】XXX」追加对应条款\n"
        "- key_points：站在投标方角度，针对 requirement 中的**每一条要求**给出具体响应建议，"
        "说明应提供的内容形式（图、文、表、案例、流程图、参数对照表等不限形式），"
        "**必须覆盖 requirement 的全部要点、不遗漏**；分点列出（用\\n分隔），不限字数\n"
        "- veto_items：可能导致废标/投标无效的硬性约束，引用原文，多条用\\n分隔，没有则留空\n"
        "- bonus_items：能提升评分的加分要素，引用原文，多条用\\n分隔，没有则留空\n"
        "- score_items：关联的评分项及分值，格式如'评分项名称 X分'，多条用\\n分隔，没有则留空\n"
        "- evidence_required：投标方需提供的证明材料，多条用\\n分隔，没有则留空\n"
        "- constraint_level：本章节要求的强制性等级，三选一：mandatory（必须响应）/recommended（应当响应）/optional（可选响应）\n"
        "- indicators：量化指标和时限要求，引用原文中的具体数值，多条用\\n分隔，没有则留空\n\n"
        '只输出 JSON 对象，格式：{"requirement":"...","key_points":"...","veto_items":"...","bonus_items":"...","score_items":"...","evidence_required":"...","constraint_level":"...","indicators":"..."}'
    )

    last_error: Exception | None = None
    chain_start = (rr_start_index + idx) % n
    for i in range(n):
        config = configs[(chain_start + i) % n]
        try:
            logger.info(f"提炼章节「{title}」，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(config, _OUTLINE_SYSTEM_PROMPT, user, max_tokens=4000)
            else:
                result = await clients.generate_oneshot_claude(config, _OUTLINE_SYSTEM_PROMPT, user, max_tokens=4000)

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

_SECTION_OUTLINE_SYSTEM_PROMPT = (
    "你是技术应标专家。在撰写正文前，先为指定模块产出一份"
    "「写作大纲」，用于指导后续正文撰写、保证内容完整不遗漏。\n"
    "输出 markdown，使用 ## / - / | 表格语法；不要解释、不要寒暄。"
)


def _build_section_outline_prompt(block: dict, extra_context: str) -> str:
    title = block.get("title") or ""
    parts = []
    if block.get("requirement"):
        parts.append(f"【应标要求】\n{block['requirement']}")
    if block.get("key_points"):
        parts.append(f"【应标重点】\n{block['key_points']}")
    if block.get("veto_items"):
        parts.append(f"【否决项（必须满足）】\n{block['veto_items']}")
    if block.get("bonus_items"):
        parts.append(f"【加分项（尽量覆盖）】\n{block['bonus_items']}")
    if block.get("score_items"):
        parts.append(f"【评分项关联】\n{block['score_items']}")
    if block.get("evidence_required"):
        parts.append(f"【需提供证明材料】\n{block['evidence_required']}")
    if block.get("indicators"):
        parts.append(f"【量化指标/时限】\n{block['indicators']}")
    ref_part = "\n\n".join(parts) if parts else "（无提炼字段）"
    ctx_part = f"\n\n【参考素材】\n{extra_context[:6000]}" if extra_context else ""

    return (
        f"请为「{title}」模块产出**写作大纲**，要求：\n"
        "1. 逐条对应【应标要求】中的要点，给出具体响应思路（每点 1-2 句）；"
        "**必须覆盖应标要求里所有要点，不得遗漏**\n"
        "2. 涉及对比/规格/参数/资质清单等内容，先用 markdown 表格给出**示例表头 + 1-2 行示例数据**，"
        "正文阶段会在其上补全实际数据\n"
        "3. 涉及架构图/拓扑图/流程图/部署图/时序图等图形内容，用占位符表达，格式："
        "`【架构图：xx 系统部署架构】`、`【流程图：xx 业务流程】`\n"
        "4. 暂时无法直接生成的具体数据/案例/品牌型号/数值，用占位符："
        "`【待补充：xx】`，便于用户后续自定义补充\n"
        "5. 必须覆盖所有【否决项】要求；尽量覆盖【加分项】\n"
        "6. 大纲采用二级标题（##）+ 项目符号（-）+ 表格 的形式，结构清晰\n"
        "7. **公文展开标记**：如果应标要求或加分项中提到要附「承诺书 / 保证函 / 声明书 / 授权委托书 / 履约保证 / 廉洁承诺」"
        "等公文，**在大纲对应位置明确写出「展开完整 XX 公文正文」**，提醒正文阶段直接生成可签署文本（含标题、抬头、分点承诺、落款占位），"
        "不要只用一句话概括\n\n"
        f"{ref_part}{ctx_part}"
    )


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

    user_prompt = _build_section_outline_prompt(block, extra_context)
    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"为「{block.get('title')}」生成写作大纲，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, _SECTION_OUTLINE_SYSTEM_PROMPT, user_prompt, max_tokens=2500,
                )
            return await clients.generate_oneshot_claude(
                config, _SECTION_OUTLINE_SYSTEM_PROMPT, user_prompt, max_tokens=2500,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"模块大纲生成失败（API {i+1}/{n}）：{e}")

    logger.error(f"模块大纲全部 API 失败：{last_error}")
    return ""


# ─────────────────────────────────────────────
# 承诺书 / 保证函 / 声明书等公文类章节专用生成
# ─────────────────────────────────────────────

LETTER_KEYWORDS = (
    "承诺书", "承诺函", "保证书", "保证函", "声明书", "声明函",
    "履约保证", "廉洁承诺", "廉政承诺", "诚信承诺", "授权委托书",
    "投标声明", "无违法承诺", "技术承诺", "服务承诺", "保密承诺",
    "质量承诺", "进度承诺", "供货承诺", "售后服务承诺",
)


def is_letter_section(title: str) -> bool:
    """根据章节标题判断是否为承诺书/保证函/声明书等公文类内容。"""
    if not title:
        return False
    t = title.strip()
    return any(kw in t for kw in LETTER_KEYWORDS)


_LETTER_SYSTEM_PROMPT = (
    "你是技术应标专家，擅长撰写规范的投标承诺书、保证函、声明书等公文。\n"
    "输出符合中文公文行文规范，结构完整可直接签署。"
)


def _build_letter_prompt(block: dict) -> str:
    title = block.get("title") or "承诺书"
    parts = []
    if block.get("requirement"):
        parts.append(f"【应答要求】\n{block['requirement']}")
    if block.get("key_points"):
        parts.append(f"【应标重点】\n{block['key_points']}")
    if block.get("veto_items"):
        parts.append(f"【硬性约束】\n{block['veto_items']}")
    if block.get("indicators"):
        parts.append(f"【量化指标】\n{block['indicators']}")
    ref = "\n\n".join(parts) if parts else ""

    return (
        f"请基于以下应标背景，为「{title}」撰写一份**完整可签署的公文正文**。\n\n"
        "格式要求（严格遵循）：\n"
        f"1. 标题居中：# {title}\n"
        "2. 抬头（致函对象）：使用占位符 `致：【招标人/采购人名称】`\n"
        "3. 正文：站在投标方角度，针对应答要求逐条作出明确承诺；语气庄重、表述肯定，"
        "每条承诺自成一段或以编号「一、二、三」分点\n"
        "4. 落款（必须包含且使用占位符）：\n"
        "   - 投标人名称：【公司全称】\n"
        "   - 法定代表人/授权代表（签字）：【法定代表人】\n"
        "   - 公章位置：（盖章处）\n"
        "   - 日期：【签署日期】\n"
        "5. 输出 markdown，使用 # 标题、段落、序号；不要其他说明文字\n\n"
        "内容要求：\n"
        "- 必须覆盖应答要求中的每一项要点，不得遗漏\n"
        "- 涉及具体数值/工期/质量标准等，引用应答要求中的原文数据\n"
        "- 保留所有占位符 `【…】` 原样，便于用户后续替换为实际信息\n\n"
        f"{ref}"
    )


async def generate_letter_content(
    configs: list[LLMConfig],
    rr_start_index: int,
    block: dict,
) -> str:
    """承诺书/保证函/声明书等公文类章节的专用生成（非流式）。"""
    n = len(configs)
    if n == 0:
        return ""

    user_prompt = _build_letter_prompt(block)
    last_error: Exception | None = None
    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            logger.info(f"为公文章节「{block.get('title')}」生成内容，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, _LETTER_SYSTEM_PROMPT, user_prompt, max_tokens=2500,
                )
            return await clients.generate_oneshot_claude(
                config, _LETTER_SYSTEM_PROMPT, user_prompt, max_tokens=2500,
            )
        except Exception as e:
            last_error = e
            logger.warning(f"公文生成失败（API {i+1}/{n}）：{e}")

    logger.error(f"公文章节全部 API 失败：{last_error}")
    return ""
