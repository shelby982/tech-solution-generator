"""5 个 agent 的 prompt 模板与构造函数集中管理。

每个 agent 一段：
- 张衡（spec 解析与 8 字段提炼）
- 沈括（素材 LLM 重排）
- 诸葛亮（写作大纲 + 正文 + 公文）
- 王安石（技术评审）
- 包拯（合规评审）

prompt 统一接受最小 dict / 字符串入参，构造完整 user prompt。
agent 类负责把 dispatcher 配置 + state 切片转换为 prompt 入参。
"""


# ─────────────────────────────────────────────
# 应标者身份强约束（被诸葛亮 tone 系统 prompt 复用）
# ─────────────────────────────────────────────

BIDDER_IDENTITY_RULE = (
    "【应标者身份强约束】"
    "你的身份是【投标人本身】，正在撰写投标文件正文。"
    "必须使用第一人称：主语用「我方」「本公司」「我公司」「我单位」，禁止用第三人称（如「投标方」「投标人」「投标单位」）。"
    "对每条要求做出明确响应或承诺，使用肯定语气（「我方将…」「我方已…」「本公司承诺…」「我方满足…」「我方完全响应…」）。"
    "禁止使用咨询/分析口吻（如「应当…」「应该…」「建议…」「可以…」「需要…」「投标方需…」），"
    "禁止笼统讨论该如何应答，禁止任何元层次描述（如「本节将阐述…」「以下从X方面进行说明…」）。"
)


# ─────────────────────────────────────────────
# 张衡：8 字段响应矩阵提炼
# ─────────────────────────────────────────────

OUTLINE_EXTRACT_SYSTEM = (
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


def build_outline_extract_user(
    title: str,
    content: str,
    special_marks: str = "",
    scoring_context: str = "",
    evaluation_context: str = "",
) -> str:
    """构造单章节 8 字段提炼 user prompt。

    内嵌 marks 提示、评分/评审上下文（截断至 3000 字符）；
    content 超 16000 字符截断 + ellipsis。

    返回的字符串末尾必须包含关键短语 '提炼以下八项内容'，
    以便 LLM mock 模式按关键词路由到 8 字段 fixture。
    """
    content = content or ""
    if len(content) > 16000:
        content = content[:16000] + "..."

    marks = special_marks or ""
    marks_hint = ""
    if "★" in marks:
        marks_hint += "\n【注意】本章节标记有★（否决条款），请重点提取 veto_items 和 constraint_level=mandatory。"
    if "▲" in marks:
        marks_hint += "\n【注意】本章节标记有▲（加分项），请重点提取 bonus_items 和 score_items。"

    scoring_ctx = (scoring_context or "").strip()
    evaluation_ctx = (evaluation_context or "").strip()
    extra_blocks = ""
    if scoring_ctx:
        extra_blocks += f"\n\n【评分上下文（来自评分表）】\n{scoring_ctx[:3000]}"
    if evaluation_ctx:
        extra_blocks += f"\n\n【评审上下文（来自评审要素）】\n{evaluation_ctx[:3000]}"

    return (
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


# ─────────────────────────────────────────────
# 张衡：应答文件目录派生
# ─────────────────────────────────────────────

# 要求文件章节正文进入 prompt 的总预算（字符）。加权分配算法同
# ``dispatcher._generate_doc_summary``，遍历时用剩余预算限制单章配额。
OUTLINE_DRAFT_SPEC_BUDGET = 8000
# 「未选中」部分的独立子预算（字符）。定位于某一部分时，其余部分只列标题、不列正文；
# 单给一份子预算，免得它们的标题把选中部分的正文挤出总预算。
OUTLINE_DRAFT_UNSELECTED_BUDGET = 1200
# build_spec_digest 里给「以下 N 个章节未列出」那行留的字符数
_TRUNCATION_MARKER_RESERVE = 40
# 用户提炼要求进 prompt 的预算（字符），置顶且标注为最高优先级。
OUTLINE_DRAFT_INSTRUCTION_BUDGET = 1000

OUTLINE_DRAFT_SYSTEM = (
    "你是资深的投标文件编制专家，擅长依据用户提供的要求文件（招标文件 / 技术规范书 / "
    "评分表 / 评审要素），为投标人编排应答文件的章节目录。\n"
    "你的任务不是复述要求文件的目录，而是**站在投标人的角度，重新组织出一份应答文件应有的目录结构**。\n"
    "编排原则：\n"
    "1. 覆盖性：**给定材料中**出现的技术要求、评分项、评审要素，都应在目录中找到明确的落点章节；"
    "材料被分成两部分时，以标注为「已定位到的材料」的那部分为准，标注为「文件的其余部分」的条目仅供了解文档全貌，不要据此展开章节；\n"
    "2. 可响应性：章节标题应当指向「我方要写什么」，而不是「招标方提了什么」"
    "（用户在 user prompt 中要求「按材料自身结构拆分」时不适用，以用户要求为准）；\n"
    "3. 层次性：一级章节为大的应答板块，其下按需拆分二级、三级，最多四级；避免只有一层或层级过深；\n"
    "4. 用户优先：用户给出的提炼要求与拆分逻辑具有最高优先级，与之冲突时以用户要求为准；\n"
    "5. 可读性：标题简洁明确，不用「关于……的说明」这类冗余前缀，不加序号（序号由系统生成）。\n"
    "只输出合法 JSON 对象，不包含任何额外说明或 markdown 代码块。"
)


_SELECTED_HEADER = "【已定位到的材料（请据此编排章节）】"
_UNSELECTED_HEADER = "【文件的其余部分（仅列标题，不要据此展开章节）】"


def build_spec_digest(
    sections: list[dict],
    budget: int = OUTLINE_DRAFT_SPEC_BUDGET,
    unselected_budget: int = OUTLINE_DRAFT_UNSELECTED_BUDGET,
) -> str:
    """把要求文件章节列表压成带预算的摘要文本。

    按内容长度加权分配字符配额（每章至少 100 字），遍历时用剩余预算限制单章配额。

    **标题行同样计入预算。** 招投标文件动辄上千个章节，只算正文不算标题的话
    预算形同虚设：实测一份 1785 章的采购文件，正文被限在 8000 字，光标题行
    就额外输出了 5.9 万字符，prompt 直接撑爆。预算耗尽即停止列出，并在末尾
    标注被截断的章节数，免得模型把「列出来的这些」误当成全部目录。

    条目带 ``selected: False`` 时（提炼要求点名了文件里的某一部分，见
    ``infra.retrieval.segment``），正文不进 prompt，只在该部分自己的
    ``unselected_budget`` 里占一行标题。没有未选中条目时（含改造前的全部调用点）
    输出与旧实现逐字一致。

    sections: [{"title": str, "content": str, "selected": bool}, ...]
    """
    entries = list(sections or [])
    unselected = [s for s in entries if not s.get("selected", True)]
    if not unselected:
        return _digest_selected(entries, budget)

    selected = [s for s in entries if s.get("selected", True)]
    selected_budget = max(0, budget - len(_SELECTED_HEADER) - 2)
    titles_budget = max(0, unselected_budget - len(_UNSELECTED_HEADER) - 2)
    return "\n\n".join([
        _SELECTED_HEADER + "\n" + _digest_selected(selected, selected_budget),
        _UNSELECTED_HEADER + "\n" + _digest_titles(unselected, titles_budget),
    ])


def _digest_selected(sections: list[dict], budget: int) -> str:
    """选中部分：标题 + 按长度加权分配的正文配额。"""
    entries = [
        (s, len(s.get("content") or ""))
        for s in (sections or [])
    ]
    total_len = sum(length for _, length in entries) or 1

    remaining = budget
    parts: list[str] = []
    listed = 0

    def _take(text: str, *, reserve: int = 0) -> bool:
        """按 join 之后的实际占用扣预算（+1 是分隔换行），放得下才收。

        reserve 是不许动用的余量 —— 列章节时给末尾的截断提示留位，
        否则提示行会因为预算被章节吃光而写不出去。
        """
        nonlocal remaining
        cost = len(text) + 1
        if cost + reserve > remaining:
            return False
        remaining -= cost
        parts.append(text)
        return True

    for sec, content_len in entries:
        title = sec.get("title") or ""
        content = sec.get("content") or ""
        if not _take(f"【{title}】", reserve=_TRUNCATION_MARKER_RESERVE):
            break
        listed += 1
        if not content:
            continue
        quota = max(100, int(budget * content_len / total_len))
        quota = min(quota, remaining - _TRUNCATION_MARKER_RESERVE)
        if quota <= 0:
            continue
        _take(content[:quota], reserve=_TRUNCATION_MARKER_RESERVE)

    truncated = len(entries) - listed
    if truncated:
        _take(f"【……以下 {truncated} 个章节因长度限制未列出】")

    return "\n".join(parts)


def _digest_titles(sections: list[dict], budget: int) -> str:
    """未选中部分：只列标题，正文一律丢弃。"""
    parts: list[str] = []
    remaining = budget
    for sec in sections or []:
        line = f"【{sec.get('title') or ''}】"
        if len(line) + 1 + _TRUNCATION_MARKER_RESERVE > remaining:
            break
        remaining -= len(line) + 1
        parts.append(line)

    truncated = len(sections or []) - len(parts)
    if truncated:
        parts.append(f"【……其余 {truncated} 个部分因长度限制未列出】")
    return "\n".join(parts)


# 提炼要求点名的那一部分已经定位到时，追加的拆分规则。
# 定位成功意味着「材料就是用户要的那一块」，此时目录应当**复刻材料自身的结构**，
# 而不是让模型重新组织 —— 后者会把「按评分要求中的每一点拆章」拆成自己想的一套板块。
FOLLOW_STRUCTURE_RULE = (
    "【最高优先级：严格按材料自身的结构拆分章节】\n"
    "上面【已定位到的材料】就是用户的提炼要求点名的那一部分，请在它的基础上拆章节，"
    "**忠实还原这部分自身的组织结构**：\n"
    "1. 材料里每一个评分因素 / 评审项 / 编号条目，各自对应一个章节，一条都不要漏、彼此不要合并；\n"
    "2. 章节名称取自材料原文（如「技术方案」「项目管理」），并保持它们在材料中的先后顺序；\n"
    "3. 只拆材料里真实存在的结构，不要另行增设材料未涉及的一级板块；\n"
    "4. 材料自身有层级时（如「评分因素 → 详细评审项」），按同样的层级拆成多级章节。"
)


def build_outline_draft_user(
    instruction: str,
    doc_summary: str,
    spec_digest: str,
    previous_outline: str = "",
    material_digest: str = "",
    follow_structure: bool = False,
) -> str:
    """构造应答文件目录派生的 user prompt。

    instruction 置顶并标注为最高优先级，截断至 ``OUTLINE_DRAFT_INSTRUCTION_BUDGET``。
    follow_structure 在提炼要求点名的那一部分已被定位到（``spec_digest`` 里带
    「已定位到的材料」）时为真，追加 ``FOLLOW_STRUCTURE_RULE``，让目录复刻该部分
    自身的结构而不是重新组织。
    previous_outline 非空时（整版重出场景）告诉模型在上一版基础上调整，保证迭代有连续性。
    material_digest 当前恒为空串 —— 本轮不读素材，保留形参作为下一轮的扩展点。

    返回的字符串末尾必须包含关键短语 '提炼应答文件目录'，
    以便 LLM mock 模式按关键词路由到目录 fixture。
    """
    instruction = (instruction or "").strip()
    instruction = instruction[:OUTLINE_DRAFT_INSTRUCTION_BUDGET]

    parts: list[str] = []

    if instruction:
        parts.append(
            "【用户的提炼要求与拆分逻辑 —— 最高优先级，与之冲突时以本节为准】\n"
            f"{instruction}"
        )

    if doc_summary:
        parts.append(f"【要求文件项目概述】\n{doc_summary.strip()}")

    if follow_structure:
        # 紧挨着材料放，模型读材料时是带着这条规则读的
        parts.append(FOLLOW_STRUCTURE_RULE)

    parts.append(f"【要求文件章节目录与原文摘录】\n{spec_digest}")

    if material_digest:
        parts.append(f"【原始素材摘录】\n{material_digest}")

    if previous_outline:
        parts.append(
            "【上一版目录 —— 用户已看过这一版，请在此基础上按上面的要求调整】\n"
            f"{previous_outline}"
        )

    body = "\n\n".join(parts)

    return (
        f"{body}\n\n"
        "请依据以上材料，提炼应答文件目录。\n\n"
        "输出格式（**扁平数组 + 显式父节点下标**，不要嵌套 children）：\n"
        '{"nodes": [{"level": 1, "title": "项目理解与总体方案"}, '
        '{"level": 2, "parent": 0, "title": "项目背景与需求理解"}]}\n\n'
        "字段说明：\n"
        "- level：层级，1 为一级章节，最大 4；\n"
        "- parent：父节点在 nodes 数组中的**下标**（从 0 开始），必须小于当前节点下标；"
        "level=1 的节点不输出 parent 或输出 null；"
        "某节点的 level 必须恰好比其 parent 的 level 大 1；\n"
        "- title：章节标题，不带序号、不带「第X章」前缀。\n\n"
        "要求：一级章节 3-12 个为宜；总节点数不超过 120 个；按应答文件的阅读顺序排列。\n"
        '只输出 JSON 对象，格式：{"nodes":[...]}'
    )


# ─────────────────────────────────────────────
# 张衡：检索范围兜底（确定性定位全空时才调）
# ─────────────────────────────────────────────

# 章节标题进 prompt 的预算（字符）
SCOPE_SELECT_TITLES_BUDGET = 2000

SCOPE_SELECT_SYSTEM = (
    "你是招投标文档的检索专家。用户用口语描述了他要的是要求文件的哪一部分，"
    "但按字面没能定位到。请把它翻译成文档里可能原样出现的字面标识。\n"
    "你只需要给出**检索关键词**，不需要挑选或复述内容。\n"
    "只输出合法 JSON 对象，不包含任何额外说明或 markdown 代码块。"
)


def build_scope_select_user(
    instruction: str,
    file_names: list[str],
    section_titles: list[str],
) -> str:
    """构造「圈定检索范围」的 user prompt。

    返回的字符串末尾必须包含关键短语 '圈定检索范围'，以便 mock 模式按关键词路由；
    该分支要排在 ``_select_fixture`` 最前面 —— 本 system prompt 含「评审」二字，
    落到后面的「评审」分支会走错 fixture。
    """
    files_block = "\n".join(f"- {n}" for n in (file_names or [])) or "（未提供）"
    titles = "、".join(t for t in (section_titles or []) if t)
    titles = titles[:SCOPE_SELECT_TITLES_BUDGET] or "（未提供）"
    instruction = (instruction or "").strip()[:OUTLINE_DRAFT_INSTRUCTION_BUDGET]

    return (
        f"【用户的提炼要求】\n{instruction}\n\n"
        f"【要求文件】\n{files_block}\n\n"
        f"【文件里的章节标题】\n{titles}\n\n"
        "请判断用户要的是哪份文件的哪一部分，输出用于检索的字面标识：\n"
        "- files：命中的文件名（可多个），没有把握时给空数组；\n"
        "- keywords：文档里可能原样出现的标识词（如「标包2」「技术评分标准」），可多个。\n\n"
        '只输出 JSON 对象，格式：{"files":[...],"keywords":[...]}\n'
        "圈定检索范围"
    )


# ─────────────────────────────────────────────
# 沈括：素材 LLM 重排
# ─────────────────────────────────────────────

RERANK_SYSTEM = (
    "你是技术应标素材匹配专家。给定一条应标要求与若干候选素材片段，"
    "你需要为每个素材打 0-10 分（10=高度相关，0=完全无关），"
    "并给出简短理由与命中的需求要点。\n"
    "只输出合法 JSON 对象，不要任何额外说明或 markdown 围栏。"
)


def build_rerank_user(chunks: list[dict], query: str, requirement: str) -> str:
    """构造沈括 LLM 重排 user prompt。"""
    lines = []
    for i, c in enumerate(chunks, start=1):
        cid = c.get("chunk_id", c.get("id", i))
        content = (c.get("content") or "")[:600]
        lines.append(f"[{i}] chunk_id={cid}\n{content}")
    chunks_block = "\n\n".join(lines)

    return (
        f"【应标要求】\n{requirement}\n\n"
        f"【检索查询】\n{query}\n\n"
        f"【候选素材片段】\n{chunks_block}\n\n"
        '请输出形如 {"matches":[{"chunk_id":...,"score":0-10,"reason":"...","hit_points":["..."]}]} 的 JSON。'
        "score 越高越相关，hit_points 列出该素材命中的应标要点；"
        "无关素材也要列出（score 设低分）。"
    )


# ─────────────────────────────────────────────
# 诸葛亮：tone system prompts + 写作大纲 + 公文
# ─────────────────────────────────────────────

TONE_SYSTEM_PROMPTS: dict[str, str] = {
    "official": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "在原文基础上补充背景释义、功能价值和应用场景，采用政企项目方案正式书面文风，"
        "不篡改原意，不新增无关内容。"
        "请使用 Markdown 格式输出。\n"
        + BIDDER_IDENTITY_RULE
    ),
    "tech": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "保留原文逻辑框架，使用准确的技术术语和架构语言，"
        "突出系统设计、接口规范、性能指标等技术要素，逻辑严密、表述精准。"
        "请使用 Markdown 格式输出。\n"
        + BIDDER_IDENTITY_RULE
    ),
    "concise": (
        "你是投标人正在撰写投标文件的技术应答正文。"
        "保留原文核心信息，去除冗余修饰，每句话都有实际信息量，"
        "句式简短清晰，避免空泛表述和套话。"
        "请使用 Markdown 格式输出。\n"
        + BIDDER_IDENTITY_RULE
    ),
}


SECTION_OUTLINE_SYSTEM = (
    "你是技术应标专家。在撰写正文前，先为指定模块产出一份"
    "「写作大纲」，用于指导后续正文撰写、保证内容完整不遗漏。\n"
    "输出 markdown，使用 ## / - / | 表格语法；不要解释、不要寒暄。"
)


def build_section_outline_user(block: dict, extra_context: str) -> str:
    """构造写作大纲 user prompt（移自 dispatcher._build_section_outline_prompt）。

    返回字符串包含关键短语 '产出**写作大纲**' 与表格样例 / 占位符规约。
    """
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


LETTER_SYSTEM = (
    "你是技术应标专家，擅长撰写规范的投标承诺书、保证函、声明书等公文。\n"
    "输出符合中文公文行文规范，结构完整可直接签署。"
)


def build_letter_user(block: dict) -> str:
    """构造公文（承诺书等）user prompt（移自 dispatcher._build_letter_prompt）。

    返回的字符串必须包含 '撰写投标承诺书' 或 '承诺书'，
    以便 LLM mock 模式路由到 letter fixture。
    """
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


# ─────────────────────────────────────────────
# 王安石：技术评审（新增）
# ─────────────────────────────────────────────

TECH_REVIEW_SYSTEM = (
    "你是技术评审专家王安石，正在对一份投标方案的单个章节做技术视角评审。\n"
    "评审视角：\n"
    "1. 技术架构合理性（拓扑、分层、可扩展性）\n"
    "2. 实施可行性（资源约束、关键路径、风险点）\n"
    "3. 实施风险（技术债、依赖外部组件、运维成本）\n"
    "4. 专业术语准确性（用语是否规范、是否有错用）\n"
    "5. 技术指标响应程度（是否逐条响应应标要求中的量化指标）\n\n"
    "只输出合法 JSON 对象，不要任何额外说明或 markdown 代码块。"
)


def build_tech_review_user(block: dict, matrix_row: dict) -> str:
    """构造王安石技术评审 user prompt。

    block: {block_id, title, content, kind}
    matrix_row: 8 字段响应矩阵（requirement, key_points, indicators 等）

    返回字符串必须包含关键短语 '评审'，以便 mock 路由。
    """
    title = block.get("title") or ""
    content = (block.get("content") or "")[:8000]
    requirement = matrix_row.get("requirement") or ""
    key_points = matrix_row.get("key_points") or ""
    indicators = matrix_row.get("indicators") or ""

    return (
        f"以下是投标方案中「{title}」章节的内容：\n\n"
        f"---\n{content}\n---\n\n"
        f"对照应标要求：\n"
        f"【应标要求】\n{requirement}\n\n"
        f"【应答要点】\n{key_points}\n\n"
        f"【量化指标】\n{indicators}\n\n"
        "请站在技术评审的视角进行评审：\n"
        "- score: 0-100 的整数（90+ 优秀，70-90 合格，<70 不合格）\n"
        "- issues: 问题清单 [{severity, point, suggestion, needs_material, material_query}]，"
        "severity 取 critical/high/medium/low\n"
        "  - needs_material：该问题是否**必须依靠补充外部素材**（证书/合同/检测报告/业绩证明/"
        "参数表）才能修复。若属于「正文没写」「写法不佳」「表述不准确」这类重写即可解决的问题，"
        "**必须为 false**。\n"
        "  - material_query：当 needs_material=true 时，填入用于检索素材库的查询词"
        "（15 字以内，具体到材料名称，如「配电柜型式试验报告」）；否则留空字符串。\n"
        "- strengths: 亮点清单（字符串数组）\n\n"
        '只输出 JSON 对象：{"score":N,"issues":[...],"strengths":[...]}'
    )


# ─────────────────────────────────────────────
# 包拯：合规评审（新增）
# ─────────────────────────────────────────────

COMPLIANCE_REVIEW_SYSTEM = (
    "你是合规评审专家包拯，正在对一份投标方案的单个章节做合规视角评审。\n"
    "评审视角：\n"
    "1. 响应合规性：是否对应标要求的每条都有明确响应或承诺\n"
    "2. 否决项覆盖：是否完整覆盖 veto_items 中的硬性约束（任一遗漏即 critical）\n"
    "3. 加分项遗漏：是否充分利用 bonus_items 提升评分\n"
    "4. 证明材料齐备：evidence_required 中要求的证书/合同/报告是否在文中明确承诺提供\n"
    "5. 量化指标响应：是否对 indicators 中每个数值/百分比/时限做出明确响应\n\n"
    "只输出合法 JSON 对象，不要任何额外说明或 markdown 代码块。"
)


def build_compliance_review_user(block: dict, matrix_row: dict) -> str:
    """构造包拯合规评审 user prompt。"""
    title = block.get("title") or ""
    content = (block.get("content") or "")[:8000]
    requirement = matrix_row.get("requirement") or ""
    veto_items = matrix_row.get("veto_items") or ""
    bonus_items = matrix_row.get("bonus_items") or ""
    evidence_required = matrix_row.get("evidence_required") or ""
    indicators = matrix_row.get("indicators") or ""

    return (
        f"以下是投标方案中「{title}」章节的内容：\n\n"
        f"---\n{content}\n---\n\n"
        f"对照应标要求：\n"
        f"【应标要求】\n{requirement}\n\n"
        f"【否决项（必须全部响应）】\n{veto_items}\n\n"
        f"【加分项（应尽量覆盖）】\n{bonus_items}\n\n"
        f"【需提供证明材料】\n{evidence_required}\n\n"
        f"【量化指标】\n{indicators}\n\n"
        "请站在合规评审的视角进行评审：\n"
        "- score: 0-100 的整数\n"
        "- issues: 问题清单 [{severity, point, suggestion, needs_material, material_query}]，"
        "severity 取 critical/high/medium/low\n"
        "  - needs_material：该问题是否**必须依靠补充外部素材**（证书/合同/检测报告/业绩证明/"
        "参数表）才能修复。若属于「正文没写」「写法不佳」「表述不准确」这类重写即可解决的问题，"
        "**必须为 false**。\n"
        "  - material_query：当 needs_material=true 时，填入用于检索素材库的查询词"
        "（15 字以内，具体到材料名称，如「配电柜型式试验报告」）；否则留空字符串。\n"
        "否决项任一未响应必须列为 critical\n"
        "- strengths: 合规亮点清单（字符串数组）\n\n"
        '只输出 JSON 对象：{"score":N,"issues":[...],"strengths":[...]}'
    )


# ─────────────────────────────────────────────
# 协同闭环：评审意见 → 生成 prompt 的转译
# ─────────────────────────────────────────────

def format_feedback_block(issues: list[dict] | None) -> str:
    """把上一轮评审意见格式化为可注入生成 prompt 的文本块。

    issues: [{"severity","point","suggestion","needs_material","material_query"}]
    空列表 / None 返回 "" —— 调用方靠空串判断是否走修订分支。
    """
    if not issues:
        return ""

    lines = ["【上轮评审意见（必须逐条修复）】"]
    for i, it in enumerate(issues, 1):
        severity = str(it.get("severity") or "medium").upper()
        lines.append(f"{i}. [{severity}] {it.get('point') or ''}")
        if it.get("suggestion"):
            lines.append(f"   修改建议：{it['suggestion']}")
        if it.get("needs_material"):
            lines.append(
                "   本条需外部材料支撑：【参考素材】中若仍无对应内容，"
                "请用占位符 `【待补充：材料名称】` 标注，不要编造具体数据、证书编号或业绩。"
            )

    lines.append("")
    lines.append("要求：逐条消除上述问题；已满足的项不要改动。")
    return "\n".join(lines)


__all__ = [
    "BIDDER_IDENTITY_RULE",
    "OUTLINE_EXTRACT_SYSTEM",
    "build_outline_extract_user",
    "RERANK_SYSTEM",
    "build_rerank_user",
    "TONE_SYSTEM_PROMPTS",
    "SECTION_OUTLINE_SYSTEM",
    "build_section_outline_user",
    "LETTER_SYSTEM",
    "build_letter_user",
    "TECH_REVIEW_SYSTEM",
    "build_tech_review_user",
    "COMPLIANCE_REVIEW_SYSTEM",
    "build_compliance_review_user",
    "format_feedback_block",
]
