"""
LLM 业务编排层：
- 多 API 配置轮询 + Fallback
- prompt 构造 / JSON 抽取
- 公开 dispatch_* / generate_* 入口
- 公文章节识别（is_letter_section / LETTER_KEYWORDS）

I/O 细节由 clients.py 承担；本模块只做 prompt 与调度。
"""

import asyncio
import inspect
import json
import logging
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable, Optional

from agents.prompts import (
    LETTER_SYSTEM,
    OUTLINE_DRAFT_SYSTEM,
    OUTLINE_EXTRACT_SYSTEM,
    SCOPE_SELECT_SYSTEM,
    SECTION_OUTLINE_SYSTEM,
    TONE_SYSTEM_PROMPTS,
    build_letter_user,
    build_outline_draft_user,
    build_outline_extract_user,
    build_section_outline_user,
    build_scope_select_user,
    build_subpoints_user,
    SUBPOINTS_SYSTEM,
)
from services.config_store import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS

from . import clients
from . import errors
from .json_utils import extract_json_object as _extract_json_object
from .json_utils import salvage_nodes_array as _salvage_nodes_array

logger = logging.getLogger(__name__)

# 目录派生的输出预算 = 思考 token + 正文 token。deepseek-v4-pro 这类推理模型
# 实测会把 8000 全部烧在思考上、正文一个字不吐（finish_reason=length、content
# 为空），所以给足预算，并把客户端超时放到容得下一次长思考的量级 —— 实测一次
# 成功的调用要 142 秒，60 秒的默认超时连失败原因都截断。
OUTLINE_DRAFT_MAX_TOKENS = 32000
OUTLINE_DRAFT_TIMEOUT = 300.0

# 章节提炼的预算同样是「思考 + 正文」。比目录派生小一档（单章节矩阵的正文远短于
# 整份目录），但 4000 对推理模型实测不够：deepseek-v4-pro 把 4000 全烧在思考上，
# finish_reason=length、content 为空，章节矩阵直接落成空占位。
# 撰写阶段每条素材原文的注入上限（见 dispatch_block_write）
MATERIAL_SNIPPET_CHARS = 600

OUTLINE_EXTRACT_MAX_TOKENS = 16000
OUTLINE_EXTRACT_TIMEOUT = 180.0

# ── 正文长度分档（汉字）──────────────────────
# 原来的提示词只写「扩写后内容字数**不少于** 600 字」——只有下限没有上限，实测
# 输出 2,200 汉字（3.7 倍）并顶到 max_tokens 被硬截断。应标文档整体偏长，所以
# 档位比通用文案宽，但必须有上限：超上限时要求收束，避免「越写越长 + 截断」。
LENGTH_BANDS: dict[str, tuple[int, int]] = {
    "brief":    (600, 1000),    # 概述 / 说明类章节
    "standard": (1000, 1600),   # 一般技术方案章节
    "core":     (1600, 2400),   # 核心方案 / 强制要求 / 评分项覆盖
}


def length_band(requirement: str, constraint_level: str = "") -> tuple[int, int]:
    """按应标要求的篇幅与强制等级选正文长度档，返回 (下限, 上限)，单位汉字。

    判据刻意简单（可测、可解释）：强制等级 mandatory 或要求较长 → core；
    要求中等 → standard；其余 → brief。
    """
    text = (requirement or "").strip()
    if (constraint_level or "").strip().lower() == "mandatory" or len(text) >= 400:
        return LENGTH_BANDS["core"]
    if len(text) >= 150:
        return LENGTH_BANDS["standard"]
    return LENGTH_BANDS["brief"]


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
        return await clients.generate_oneshot_openai(
            config, system_prompt, user_prompt, max_tokens=2000, call_site="doc_summary",
        )
    elif config.provider == "claude":
        return await clients.generate_oneshot_claude(
            config, system_prompt, user_prompt, max_tokens=2000, call_site="doc_summary",
        )
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
    max_words: Optional[int] = None,
) -> AsyncIterator[str]:
    """
    流式生成单个章节的技术方案内容。

    Args:
        config:           LLM 配置
        section_title:    章节标题
        original_content: 章节原始内容（来自规范书）；为空时按「应标要求 + 参考素材」
                          撰写，不再要求「按原文逐段扩写」（那是没有对象的指令）
        target_words:     目标字数（给 max_words 时它是区间下限）
        max_words:        字数上限；给定则写入区间与收束要求，None 时沿用旧的
                          「不少于 N 字」措辞（工作台手动路径保持原行为）
        doc_summary:      文档整体摘要（作为全局上下文）

    Yields:
        str — 每次 yield 一个 token 片段
    """
    system_prompt = TONE_SYSTEM_PROMPTS.get(tone, TONE_SYSTEM_PROMPTS["official"])

    # 构建 user_prompt 的顺序原则：**稳定段在前、动态段在后**。
    # 6 条核心规则在整轮生成里逐字不变（同一分支下），把它放在最前面能让 system +
    # 规则块构成一段稳定的共同前缀，命中上下文缓存（实测命中率 29% → 目标 60%+）。
    # 原实现的顺序是「摘要 → 标题 → 要求/素材 → 规则」，前缀第 20 个字就分叉。
    original = (original_content or "").strip()

    if original:
        fidelity_rule = (
            "1. 忠于原意：完全保留原文核心含义、业务定位、逻辑框架，不篡改、不删减、不新增无关内容。\n"
        )
        expand_rule = (
            "3. 扩写逻辑：在原文基础上补充背景释义、功能价值、作用意义、应用场景，拉长句式、丰富表述，"
            "合理扩充篇幅，不空洞凑字、不堆砌冗余语句。\n"
        )
        format_rule = (
            "4. 格式规范：严格保留原文的段落结构与小标题，不改变原有段落顺序；在每个小标题后紧接输出扩写正文，"
            "段落之间用空行分隔，排版整齐；禁止输出任何自创的段落编号标签（如'第一段''第一段扩写内容'等）。\n"
        )
        stable_head = "请按下面给出的原文，逐段扩写本节的投标应答正文。\n"
    else:
        # 自动生成路径（dispatch_block_write）不传规范书原文 —— 此时再要求「按原文
        # 逐段扩写」是一句没有对象的指令，模型只能自由发挥（也更容易越写越长）。
        # 改为以【本章应答要求】+【参考素材】为依据撰写，与工作台手动路径的语义对齐。
        fidelity_rule = (
            "1. 忠于要求：严格围绕【本章应答要求】展开，**逐条对应编号回应**（R=要求、"
            "K=响应建议、V=否决项、B=加分项、S=评分项、I=量化指标、E=证明材料），"
            "不遗漏要点、不新增无关内容。否决项必须写明如何满足，量化指标必须给出具体数值，"
            "评分项必须写明我方如何拿到该项得分。\n"
        )
        expand_rule = (
            "3. 展开逻辑：对每条要求给出具体响应措施、实现方式与预期效果，"
            "把「我方将满足」写实为可核查的做法，不空洞凑字、不堆砌冗余语句。\n"
        )
        format_rule = (
            "4. 格式规范：按应答要求的条目顺序组织小标题，一段一个要点，段落之间用空行分隔，排版整齐；"
            "禁止输出任何自创的段落编号标签（如'第一段''第一段扩写内容'等）。\n"
        )
        stable_head = (
            "请撰写下面给出的章节的投标应答正文，撰写依据是其后提供的"
            "【本章应答要求】与【参考素材】（若有）。\n"
        )

    parts = [
        stable_head,
        "核心规则：\n"
        f"{fidelity_rule}"
        "2. 文风标准：采用政企、园区、数字化平台、项目方案正式书面文风，语言严谨专业、通顺流畅、格调正式。\n"
        f"{expand_rule}"
        f"{format_rule}"
        "5. 专业适配：贴合平台运营、资源汇聚、系统整合、生态建设、服务输出、账号统一、办公联动等政企信息化通用专业语境，用词贴合汇报材料、建设方案、平台介绍文案风格。\n"
        "6. 应标者身份（强约束）：使用第一人称「我方」「本公司」「我公司」撰写，禁止第三人称「投标方」「投标人」；"
        "对要求逐条做明确响应/承诺（「我方将…」「我方已…」「本公司承诺…」「我方完全响应…」「我方满足…」），"
        "禁止「应当」「应该」「建议」「需要」「可以」等咨询/分析口吻，"
        "禁止笼统讨论应该如何应答，禁止元层次描述（「本节将阐述…」「以下从X方面说明…」），直接进入实质内容。\n",
    ]

    # ── 以下全部是动态内容 ──
    parts.append(f"\n【章节】{section_title}\n")

    if max_words is not None:
        # 区间 + 收束：只写「不少于」时实测写到 3.7 倍目标，并顶到 max_tokens 被硬截断
        parts.append(
            f"\n正文长度要求：{target_words}–{max_words} 字。\n"
            f"达到 {max_words} 字时必须收束：补齐当前段落即结束，不得再展开新的小节。\n"
        )
    else:
        parts.append(f"\n扩写后内容字数不少于 {target_words} 字。\n")

    if original:
        parts.append(
            f"\n以下是技术规范书中关于「{section_title}」的原文内容结构：\n\n"
            f"---\n{original}\n---\n"
        )

    if doc_summary:
        # 截断保护：摘要最多注入 3000 字符，避免超窗
        safe_summary = doc_summary[:3000] + ("\n..." if len(doc_summary) > 3000 else "")
        parts.append(
            f"\n以下是该项目的整体概述：\n\n"
            f"===\n{safe_summary}\n===\n"
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

    # 有应答要求或**真的带了素材**时，才要求用标记包裹不同来源的内容。
    # 判据刻意用「块首」而不是子串包含：稳定规则头里就写着「与【参考素材】（若有）」，
    # 用子串判断会在无素材时也要求 [M]...[/M] —— 等于指示模型给自造内容盖章成"来自素材"
    # （方案 §1.3 的同一个坑）。
    if extra_prompt and (
        "\n【本章应答要求】" in extra_prompt
        or "\n【应标要求】" in extra_prompt
        or "\n【应标指导】" in extra_prompt
        or "\n【参考素材】" in extra_prompt
    ):
        user_prompt += (
            "\n\n来源标注要求：在输出的 Markdown 正文中，用以下标记成对包裹来自不同来源的内容片段：\n"
            "- 来自【本章应答要求】（核心要求/策略要点/否决项/加分项）的内容用 [G]...[/G] 包裹\n"
            "- 来自【参考素材】的内容用 [M]...[/M] 包裹\n"
            "- 你基于上下文合理补充的内容用 [E]...[/E] 包裹\n"
            "标记必须成对出现，标记内只放纯文本不嵌套；标记不影响 Markdown 段落和标题结构；"
            "正常使用 Markdown 语法（标题、列表、加粗等），只在内容片段外侧加这些来源标记。"
        )

    if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        async for token in clients.stream_openai(config, system_prompt, user_prompt, call_site="stream_generate"):
            yield token
    elif config.provider == "claude":
        async for token in clients.stream_claude(config, system_prompt, user_prompt, call_site="stream_generate"):
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
    max_words: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    从 rr_start_index 指定的 API 开始尝试流式生成，失败时自动 fallback 到下一个。

    Fallback 策略（2026-10-12 改）：
    - 每条配置的流**先缓冲整段、成功收尾后才 yield**，因此"已吐一半被截断"也能安全换配置
      重试，不必把半截正文交给调用方。实测 background：deepseek-v4-pro 单次应答 140s+，
      会在 120s 处被掐断并**静默**落库半截正文（方案 §1.2 的"静默截断"）。
      旧语义是"一旦开始 yield 就不再 fallback"，代价就是半截内容只能将错就错。
    - 流被截断（``errors.StreamTruncatedError``：无 ``finish_reason`` 或顶到 max_tokens）
      按可重试处理，换下一条配置（通常是更快/更稳的档）重生成整节。
    - 全部配置都失败时：若最后一轮是**截断**且已有正文，记 warning 后仍把这段交出去
      （半截正文对用户仍有价值，人工可续写），而不是抛错丢掉已花的钱；
      其余情况上抛最后一个异常。
    - 代价：调用方不再"边说边看"。自动路径本就是整节写完后落库，工作台手动路径走的是
      ``dispatch_stream_generate`` 之外的直连，不受影响。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先在「设置」中添加配置")

    last_error: Exception | None = None
    best_partial: tuple[str, str] = ("", "")   # (部分正文, 截断原因)
    # 跳过冷却中的坏配置（余额不足/Key 无效等）：否则一个不可用的配置会被后面
    # 每个小节反复重试。全部冷却时 usable 为空 → 直接快速失败并给出聚合原因。
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        buf: list[str] = []
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 生成章节「{section_title}」")
            async for token in _stream_generate(
                config, section_title, original_content, target_words,
                doc_summary, extra_prompt, doc_template, tone, max_words,
            ):
                buf.append(token)
            # 正常收尾：这时才把整段交出去
            for token in buf:
                yield token
            return
        except errors.StreamTruncatedError as e:
            text = "".join(buf)
            if len(text) > len(best_partial[0]):
                best_partial = (text, str(e))
            last_error = e
            kind = errors.KIND_RETRYABLE
            logger.warning(
                f"API [{config.provider}/{config.model}] 流被截断"
                f"（已有 {len(text)} 字）：{e}"
                f"{'，换下一条配置重生成整节' if i < len(usable) - 1 else ''}"
            )
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"API [{config.provider}/{config.model}] 生成失败"
                f"（{errors.describe_error(e)}），"
                f"{'尝试下一个' if i < len(usable) - 1 else '已无可用 API'}：{e}"
            )
            if errors.blocks_chain(kind):
                break

    if best_partial[0]:
        # 全部配置都截断了：交出最长的一段并说清楚，不静默、也不白花钱
        logger.warning(
            f"章节「{section_title}」所有配置均被截断，交出 {len(best_partial[0])} 字"
            f"（{best_partial[1]}）；内容可能不完整，建议重生成"
        )
        yield best_partial[0]
        return

    raise errors.exhausted_error(configs, last_error)


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
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 生成文档摘要")
            return await _generate_doc_summary(config, sections)
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"API [{config.provider}/{config.model}] 摘要生成失败"
                f"（{errors.describe_error(e)}），"
                f"{'尝试下一个' if i < len(usable) - 1 else '已无可用 API'}：{e}"
            )
            if errors.blocks_chain(kind):
                break

    raise errors.exhausted_error(configs, last_error)


async def dispatch_subpoints_json(
    configs: list[LLMConfig],
    rr_start_index: int,
    payload: dict,
) -> dict:
    """从一个评分点的「分项要素」里抽出应答要点，返回 ``{"points": [...]}``。

    为什么单独一次调用：目录的**结构**由 ``infra.parser.outline_builder`` 用规则定死，
    模型只被允许做这一件"从异质长文里抽细项"的事 —— 规则在这上面会抽出
    `%RLRH1`、`δ偏为评标价格` 这类碎片，模型明显更强。结果由
    ``agents.subpoints`` 做接地校验并按内容哈希缓存，因此可复现。

    payload 字段：criterion
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    user = build_subpoints_user(payload.get("criterion", "") or "")

    last_error: Exception | None = None
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 抽取评分点细项")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(
                    config, SUBPOINTS_SYSTEM, user, max_tokens=800,
                    call_site="outline_subpoints",
                )
            else:
                result = await clients.generate_oneshot_claude(
                    config, SUBPOINTS_SYSTEM, user, max_tokens=800,
                    call_site="outline_subpoints",
                )
            obj = _extract_json_object(result)
            if not isinstance(obj, dict) or not isinstance(obj.get("points"), list):
                raise ValueError("模型输出不是含 points 的 JSON 对象")
            return obj
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"API [{config.provider}/{config.model}] 抽取细项失败"
                f"（{errors.describe_error(e)}），"
                f"{'尝试下一个' if i < len(usable) - 1 else '已无可用 API'}：{e}"
            )
            if errors.blocks_chain(kind):
                break

    raise errors.exhausted_error(configs, last_error)


async def dispatch_outline_draft_json(
    configs: list[LLMConfig],
    rr_start_index: int,
    payload: dict,
) -> dict:
    """依据规范书 + 用户提炼要求派生「应答文件目录」，返回解析后的 JSON 对象。

    payload 字段（全部可选，缺省空串）：
        instruction / doc_summary / spec_digest / previous_outline / material_digest
    follow_structure 为真时（提炼要求点名的那一部分已定位到），要求模型复刻材料
    自身的结构拆章节，而不是重新组织。

    从 rr_start_index 起轮询 + fallback，全部失败时上抛最后一个异常（由节点兜底）。
    模型输出不是合法 JSON 对象时同样抛 ValueError，走同一条降级路径。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    user = build_outline_draft_user(
        instruction=payload.get("instruction", "") or "",
        doc_summary=payload.get("doc_summary", "") or "",
        spec_digest=payload.get("spec_digest", "") or "",
        previous_outline=payload.get("previous_outline", "") or "",
        material_digest=payload.get("material_digest", "") or "",
        follow_structure=bool(payload.get("follow_structure")),
        category=payload.get("category", "") or "",
    )

    last_error: Exception | None = None
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 派生应答文件目录")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(
                    config, OUTLINE_DRAFT_SYSTEM, user,
                    max_tokens=OUTLINE_DRAFT_MAX_TOKENS,
                    timeout=OUTLINE_DRAFT_TIMEOUT,
                    # 空内容在这里是「名字」清楚的失败，别让它退化成
                    # 「未找到 JSON 起始 {」这种指不出原因的错
                    raise_on_empty=True,
                    call_site="outline_draft",
                )
            else:
                result = await clients.generate_oneshot_claude(
                    config, OUTLINE_DRAFT_SYSTEM, user,
                    max_tokens=OUTLINE_DRAFT_MAX_TOKENS,
                    timeout=OUTLINE_DRAFT_TIMEOUT,
                    raise_on_empty=True,
                    call_site="outline_draft",
                )
            try:
                obj = _extract_json_object(result)
            except ValueError:
                # 输出被 max_tokens 截断：抢救数组里已写完整的节点，降级为部分成功
                salvaged = _salvage_nodes_array(result)
                if not salvaged:
                    raise
                logger.warning(
                    f"目录派生输出被截断，仅抢救出 {len(salvaged)} 个节点"
                )
                # 带上「被截断」标记：抢救出来的目录是**部分成功**，不标的话用户
                # 看到的是「派生完成」，只会觉得目录少了几节（实测 32000 token
                # 预算对推理模型并非不会触顶）。标记由 draft_outline 并进
                # outline_error → 闸门 1 展示并建议重新拆分。
                obj = {"nodes": salvaged, "_truncated_nodes": len(salvaged)}

            if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), list):
                raise ValueError("模型输出缺少 nodes 数组")
            return obj
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"API [{config.provider}/{config.model}] 目录派生失败"
                f"（{errors.describe_error(e)}），"
                f"{'尝试下一个' if i < len(usable) - 1 else '已无可用 API'}：{e}"
            )
            if errors.blocks_chain(kind):
                break

    raise errors.exhausted_error(configs, last_error)


async def dispatch_scope_select_json(
    configs: list[LLMConfig],
    rr_start_index: int,
    payload: dict,
) -> dict:
    """把口语化的提炼要求翻译成文档里的字面检索标识，返回解析后的 JSON 对象。

    只在确定性定位全空时才调（见 ``agents.zhang_heng.draft_outline``）——用户明明
    点名了文件里的某一部分（「标包2」），但按字面哪儿都认不出来。模型只做「换个
    说法」这一件事，输出几十 token，结果可复现。

    payload 字段（全部可选）：
        instruction / file_names / section_titles

    从 rr_start_index 起轮询 + fallback，全部失败时上抛最后一个异常（由调用方兜底）。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    user = build_scope_select_user(
        instruction=payload.get("instruction", "") or "",
        file_names=payload.get("file_names") or [],
        section_titles=payload.get("section_titles") or [],
    )

    last_error: Exception | None = None
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"使用 API [{config.provider}/{config.model}] 圈定检索范围")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(
                    config, SCOPE_SELECT_SYSTEM, user, max_tokens=500,
                    call_site="scope_select",
                )
            else:
                result = await clients.generate_oneshot_claude(
                    config, SCOPE_SELECT_SYSTEM, user, max_tokens=500,
                    call_site="scope_select",
                )
            obj = _extract_json_object(result)
            if not isinstance(obj, dict):
                raise ValueError("模型输出不是 JSON 对象")
            return obj
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"API [{config.provider}/{config.model}] 圈定检索范围失败"
                f"（{errors.describe_error(e)}），"
                f"{'尝试下一个' if i < len(usable) - 1 else '已无可用 API'}：{e}"
            )
            if errors.blocks_chain(kind):
                break

    raise errors.exhausted_error(configs, last_error)


# 应答条目类型 → 写作输入里的标签。顺序即 prompt 里的顺序。
_ITEM_LABELS: dict[str, str] = {
    "requirement": "要求",
    "key_point": "响应建议",
    "veto": "否决项",
    "bonus": "加分项",
    "score": "评分项",
    "indicator": "量化指标",
    "evidence": "证明材料",
}
# 条目类型 → 标号前缀（与 domain.spec.response_items.TYPE_PREFIX 同一套，
# 这样写作 prompt 里的 V1/S1/I1 与工作台界面的标号是同一个东西）
_ITEM_PREFIXES: dict[str, str] = {
    "requirement": "R",
    "key_point": "K",
    "veto": "V",
    "bonus": "B",
    "score": "S",
    "indicator": "I",
    "evidence": "E",
}
# 必须显式回应、否则一定扣分/废标的类型（在 prompt 里单独前置，其余照常逐条列出）
_CRITICAL_ITEM_TYPES = ("veto", "score", "bonus", "indicator")


def build_write_requirement_block(
    requirement: str,
    items: Optional[list[dict]] = None,
    *,
    max_chars: int = 4000,
) -> str:
    """把本章的 8 字段应答要求拼成「逐条编号」的写作输入块（不含素材）。

    为什么需要（``docs/superpowers/plans/2026-09-21-response-coverage-and-matching-plan.md``
    §1.1、§4.B）：提炼产出 8 个字段，但写作输入此前**只带 requirement 一个** ——
    评分项分值 / 否决项 / 加分项 / 量化指标写手全看不到，于是"先让人在信息不全的情况下写，
    再拿全量规则扣分"，必然漏评分项、不显式响应否决项、指标不数值化 → 评审扣分 → 回炉。
    这是单点收益最大的一处改动。

    无 ``items``（旧调用方、数据缺失）时退回只给 requirement，行为与改造前逐字一致。

    编号规则（与 ``domain.spec.response_items`` 的前端标号同一套）：每条按类型独立计数，
    形如 ``V1`` / ``S1`` / ``I1``，写手因此可以按编号逐条作答、覆盖度自检也能按编号核对。
    """
    text = (requirement or "").strip()
    legacy = f"【本章应答要求】\n{text}\n" if text else ""
    if not items:
        return legacy

    tagged: list[tuple[str, str, bool]] = []  # (tag, 渲染行, 是否必须显式回应)
    counters: dict[str, int] = {}
    for it in items:
        body = (it.get("text") or "").strip()
        if not body:
            continue
        type_ = str(it.get("type") or "")
        prefix = _ITEM_PREFIXES.get(type_, "")
        if prefix:
            counters[prefix] = counters.get(prefix, 0) + 1
            tag = f"{prefix}{counters[prefix]}"
        else:
            tag = "-"
        # 评分口径/量化档位（informative）加"说明性"标注：它们不是"要回应的事项"，
        # 但必须让写手看到（否则漏评分项）—— 让模型对着它写，而不是逐条复述档位。
        note = "（评分口径：照此写，不要复述档位）" if it.get("informative") else ""
        label = _ITEM_LABELS.get(type_, "条目")
        tagged.append((tag, f"{tag} {label}：{body}{note}", type_ in _CRITICAL_ITEM_TYPES))

    if not tagged:
        return legacy

    # 三类分组呈现（实测驱动的口径）：
    # ① 否决项 = 必须显式回应的硬约束 → 保留编号，逐条写明满足
    # ② 评分口径/量化档位 = 评委打分依据 → **不做成编号题号**，否则模型会去"回应评分规则"
    #    本身（实测把「优良档7-10分…」抄进正文），改成说明块，只提取"该拿哪一档"
    # ③ 要回应的事项（要求/建议/证明材料）= 逐条作答
    veto = [(t, l) for t, l, c in tagged if c and t.startswith("V")]
    scoring = [(t, l) for t, l, c in tagged if c and not t.startswith("V")]
    rest = [(t, l) for t, l, c in tagged if not c]

    head = "【本章应答要求】（逐条回应下列每一条，编号即题号）\n"
    if text and text.replace("\n", "").strip() not in "".join(l for _, l, _ in tagged).replace("\n", ""):
        # requirement 原文里有条目化没覆盖到的内容（拆条丢句）时兜底附上，不静默丢要求
        head += f"要求原文：{text}\n"
    if veto:
        head += (
            "以下**否决项必须先逐条写明我方如何满足**，否则投标无效：\n"
            + "\n".join(l for _, l in veto)
            + "\n"
        )
    if scoring:
        head += (
            "以下是评分与量化口径（说明性，**不要逐条复述档位或分值**）："
            "按其中的要求把正文写到对应档位，量化指标必须在正文给出具体数值。\n"
            + "\n".join(l for _, l in scoring)
            + "\n"
        )
    if rest:
        head += "以下是要回应的事项，逐条作答：\n"
    block = head + "\n".join(l for _, l in rest) + "\n"
    if len(block) > max_chars:
        block = block[:max_chars] + "\n…（本条要求过长，已截断，按上述要点响应）\n"
    return block


async def dispatch_block_write(
    configs: list[LLMConfig],
    rr_start_index: int,
    title: str,
    requirement: str,
    chunks: list[dict],
    target_words: int = 600,
    feedback_text: str = "",
    max_words: Optional[int] = None,
    items: Optional[list[dict]] = None,
):
    """
    为单个 block 流式生成正文内容。
    将 requirement + chunks 拼入 extra_prompt，复用 dispatch_stream_generate。

    ``items``：该章节的应答条目（``domain.spec.response_items.expand_response_items`` 的产出）。
    给了它就把 8 字段逐条编号进 prompt（见 ``build_write_requirement_block``）；不给则退回
    只给 requirement，行为与改造前一致。

    **不传规范书原文**（original_content 为空）：因此提示词走「按应标要求 + 参考素材
    撰写」分支，而不是「按原文逐段扩写」。需要原文逐段扩写的是工作台手动路径
    （routes/blocks.py），它把 block 正文作为 original_content 传入。

    target_words / max_words：字数区间（给 max_words 时才生效）。
    feedback_text：协同闭环回灌的上一轮评审意见（已由
    ``agents.prompts.format_feedback_block`` 格式化）。为空时行为与改造前一致。
    """
    # 素材原文每条的注入上限：以前是 200 字，素材原文（契约要求带 content）会被腰斩成半句。
    # 600 字与 rerank 阶段看到的窗口一致，5 条 ≈ 3k 字符，prompt 预算可接受。
    snippets = "\n".join(
        f"- {c['content'][:MATERIAL_SNIPPET_CHARS]}" for c in chunks
    )
    extra_prompt = build_write_requirement_block(requirement, items)
    # 无素材时**不拼**【参考素材】：此前固定拼一段，哪怕匹配为空/弱相关，
    # 内容还会被 [M] 标成"来自素材"——等于给自造内容盖章（§1.3）。
    if snippets:
        extra_prompt += f"【参考素材】\n{snippets}"
    if feedback_text:
        extra_prompt += f"\n\n{feedback_text}"

    async for token in dispatch_stream_generate(
        configs=configs,
        rr_start_index=rr_start_index,
        section_title=title,
        original_content="",
        target_words=target_words,
        extra_prompt=extra_prompt,
        max_words=max_words,
    ):
        yield token


# ─────────────────────────────────────────────
# 大纲生成（非流式，返回结构化列表）
# ─────────────────────────────────────────────




async def dispatch_outline_json(
    configs: list[LLMConfig],
    rr_start_index: int,
    sections: list[dict],
    *,
    on_start: Optional[Callable[[int], Awaitable[None] | None]] = None,
    concurrency: int = 1,
    should_cancel: Optional[Callable[[], bool]] = None,
):
    """
    按文档原始章节提炼响应矩阵（8 字段），默认**严格按目录顺序**逐章 yield。

    ``concurrency`` 默认 1 = 串行：前端能自上而下看到「提炼中 → 已完成」，
    不被先完成的章节打乱顺序。调大则并发执行、按完成顺序 yield（快，但顺序会跳）。

    每章节内部仍按"轮询 + Fallback"试遍所有 config，起点按 idx 散开。
    每个 item 自带 ``idx``，调用方据此回填到 toc。

    on_start: 可选，章节真正开始处理前回调一次（调 LLM 前），
              用于让前端把对应卡片切换到"提炼中"骨架屏。

    sections: [{"title": str, "content": str, "special_marks": str (optional),
                "scoring_context": str (optional), "evaluation_context": str (optional)}, ...]
    Yields: {"idx": int, "title": str, "requirement": str, "key_points": str, "veto_items": str,
             "bonus_items": str, "score_items": str, "evidence_required": str,
             "constraint_level": str, "indicators": str, "error": str}

    error 为空字符串表示成功；非空则表示该章节全部 API 失败，其它字段是占位空值。
    """
    n = len(configs)
    if n == 0:
        raise ValueError("未配置任何 API，请先添加模型配置")

    async def _run_one(idx: int, sec: dict) -> dict:
        if on_start is not None:
            try:
                rv = on_start(idx)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"dispatch_outline_json on_start 回调失败：{e}")
        result = await _extract_one_section(idx, sec, configs, rr_start_index, n)
        result["idx"] = idx
        return result

    if concurrency <= 1:
        # 严格按目录自上而下逐节提炼：前端才能一路看到「提炼中 → 已完成」，
        # 不被先完成的章节打乱顺序。代价是总耗时 ≈ 章节数 × 单节耗时。
        #
        # should_cancel：每节开始前检查一次。用户点「取消」时立刻停在这里，
        # 剩下的章节一次模型调用都不发 —— 这是取消唯一能真正省钱的位置。
        for i, sec in enumerate(sections):
            if should_cancel is not None and should_cancel():
                logger.info(f"逐节提炼被取消：已完成 {i}/{len(sections)} 个章节，停止后续调用")
                return
            yield await _run_one(i, sec)
        return

    sem = asyncio.Semaphore(concurrency)

    async def _worker(idx: int, sec: dict) -> dict:
        async with sem:
            return await _run_one(idx, sec)

    tasks = [asyncio.create_task(_worker(i, s)) for i, s in enumerate(sections)]
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                yield t.result()
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

    if not (sec.get("content") or "").strip():
        # 没有可提炼的原文：用户新增的章节（目录结构编辑加进来的）与纯目录/分组节点
        # 都是这种。这里**不发模型调用** —— 旧行为是把空内容块发过去（prompt 里
        # 是「---\n\n---」），既白花一次调用，又可能让模型凭空编出 requirement /
        # veto_items，而那些假要求会一路流进素材匹配、正文生成与评审。
        return {
            "title": title,
            "requirement": "", "key_points": "",
            "veto_items": "", "bonus_items": "",
            "score_items": "", "evidence_required": "",
            "constraint_level": "recommended", "indicators": "",
            # error_kind='no_source'：这是**正常**结果（用户新增/纯目录节点），不必重试；
            # 与下面的 'failed' 必须在界面上区分开，否则失败会被当成"已完成"。
            "error": "本章节没有可提炼的原文（用户新增或纯目录节点），要点请在正文阶段手写补充",
            "error_kind": "no_source",
            "ok": True,
        }
    user = build_outline_extract_user(
        title=title,
        content=sec.get("content", "") or "",
        special_marks=sec.get("special_marks", "") or "",
        scoring_context=sec.get("scoring_context") or "",
        evaluation_context=sec.get("evaluation_context") or "",
    )

    last_error: Exception | None = None
    # 逐节提炼是本项目调用量最大的环节（每节一次），坏配置在这里被反复重试的
    # 代价也最大：实测 3 条配置全部余额不足时，每个小节都要白试 3 次。
    usable = errors.available_configs(configs, (rr_start_index + idx) % max(n, 1))
    for i, config in enumerate(usable):
        try:
            logger.info(f"提炼章节「{title}」，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                result = await clients.generate_oneshot_openai(
                    config, OUTLINE_EXTRACT_SYSTEM, user,
                    max_tokens=OUTLINE_EXTRACT_MAX_TOKENS,
                    timeout=OUTLINE_EXTRACT_TIMEOUT,
                    # 空内容在这里是「名字」清楚的失败，别让它退化成
                    # 「未找到 JSON 起始 {」这种指不出原因的错
                    raise_on_empty=True,
                    call_site="outline_extract",
                )
            else:
                result = await clients.generate_oneshot_claude(
                    config, OUTLINE_EXTRACT_SYSTEM, user,
                    max_tokens=OUTLINE_EXTRACT_MAX_TOKENS,
                    timeout=OUTLINE_EXTRACT_TIMEOUT,
                    raise_on_empty=True,
                    call_site="outline_extract",
                )

            obj = _extract_json_object(result)
            return {
                "title": title,
                "error": "", "error_kind": "", "ok": True,
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
            kind = errors.note_failure(config, e)
            logger.warning(
                f"章节「{title}」提炼失败（API {i+1}/{len(usable)}，"
                f"{errors.describe_error(e)}）：{e}"
            )
            if errors.blocks_chain(kind):
                break

    summary = errors.exhausted_summary(configs, last_error)
    logger.error(f"章节「{title}」无可用 API，使用空占位：{summary}")
    return {
        "title": title,
        "requirement": "", "key_points": "",
        "veto_items": "", "bonus_items": "",
        "score_items": "", "evidence_required": "",
        "constraint_level": "recommended", "indicators": "",
        "error": f"全部 API 失败：{summary}",
        "error_kind": "failed",     # 需要重试：界面要标出来，且**不能**算"已提炼"
        "ok": False,
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
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"为「{block.get('title')}」生成写作大纲，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, SECTION_OUTLINE_SYSTEM, user_prompt, max_tokens=2500,
                    call_site="section_outline",
                )
            return await clients.generate_oneshot_claude(
                config, SECTION_OUTLINE_SYSTEM, user_prompt, max_tokens=2500,
                call_site="section_outline",
            )
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"模块大纲生成失败（API {i+1}/{len(usable)}，"
                f"{errors.describe_error(e)}）：{e}"
            )
            if errors.blocks_chain(kind):
                break

    logger.error(f"模块大纲无可用 API：{errors.exhausted_summary(configs, last_error)}")
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
    usable = errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            logger.info(f"为公文章节「{block.get('title')}」生成内容，使用 API [{config.provider}/{config.model}]")
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                return await clients.generate_oneshot_openai(
                    config, LETTER_SYSTEM, user_prompt, max_tokens=2500,
                    call_site="letter",
                )
            return await clients.generate_oneshot_claude(
                config, LETTER_SYSTEM, user_prompt, max_tokens=2500,
                call_site="letter",
            )
        except Exception as e:
            last_error = e
            kind = errors.note_failure(config, e)
            logger.warning(
                f"公文生成失败（API {i+1}/{len(usable)}，"
                f"{errors.describe_error(e)}）：{e}"
            )
            if errors.blocks_chain(kind):
                break

    logger.error(f"公文章节无可用 API：{errors.exhausted_summary(configs, last_error)}")
    return ""
