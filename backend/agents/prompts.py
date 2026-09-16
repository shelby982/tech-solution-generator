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

# 规范书章节正文进入 prompt 的总预算（字符）。加权分配算法同
# ``dispatcher._generate_doc_summary``，遍历时用剩余预算限制单章配额。
OUTLINE_DRAFT_SPEC_BUDGET = 8000
# 用户提炼要求进 prompt 的预算（字符），置顶且标注为最高优先级。
OUTLINE_DRAFT_INSTRUCTION_BUDGET = 1000

OUTLINE_DRAFT_SYSTEM = (
    "你是资深的投标文件编制专家，擅长依据招标文件与规范书，为投标人编排应答文件的章节目录。\n"
    "你的任务不是复述招标文件的目录，而是**站在投标人的角度，重新组织出一份应答文件应有的目录结构**。\n"
    "编排原则：\n"
    "1. 覆盖性：招标文件与规范书中出现的技术要求、评分项、评审要素，都应在目录中找到明确的落点章节；\n"
    "2. 可响应性：章节标题应当指向「我方要写什么」，而不是「招标方提了什么」；\n"
    "3. 层次性：一级章节为大的应答板块，其下按需拆分二级、三级，最多四级；避免只有一层或层级过深；\n"
    "4. 用户优先：用户给出的提炼要求与拆分逻辑具有最高优先级，与之冲突时以用户要求为准；\n"
    "5. 可读性：标题简洁明确，不用「关于……的说明」这类冗余前缀，不加序号（序号由系统生成）。\n"
    "只输出合法 JSON 对象，不包含任何额外说明或 markdown 代码块。"
)


def build_spec_digest(sections: list[dict], budget: int = OUTLINE_DRAFT_SPEC_BUDGET) -> str:
    """把规范书章节列表压成带预算的摘要文本。

    按内容长度加权分配字符配额（每章至少 100 字），遍历时用剩余预算限制单章
    配额，使总长度自然落在 ``budget`` 内。

    sections: [{"title": str, "content": str}, ...]
    """
    entries = [
        (s, len(s.get("content") or ""))
        for s in (sections or [])
    ]
    total_len = sum(length for _, length in entries) or 1

    remaining = budget
    parts: list[str] = []
    for sec, content_len in entries:
        title = sec.get("title") or ""
        content = sec.get("content") or ""
        if not content or remaining <= 0:
            parts.append(f"【{title}】")
            continue
        quota = max(100, int(budget * content_len / total_len))
        quota = min(quota, remaining)
        snippet = content[:quota]
        parts.append(f"【{title}】\n{snippet}")
        remaining -= len(snippet)

    return "\n\n".join(parts)


def build_outline_draft_user(
    instruction: str,
    doc_summary: str,
    spec_digest: str,
    previous_outline: str = "",
    material_digest: str = "",
) -> str:
    """构造应答文件目录派生的 user prompt。

    instruction 置顶并标注为最高优先级，截断至 ``OUTLINE_DRAFT_INSTRUCTION_BUDGET``。
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
        parts.append(f"【规范书项目概述】\n{doc_summary.strip()}")

    parts.append(f"【规范书章节目录与原文摘录】\n{spec_digest}")

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
