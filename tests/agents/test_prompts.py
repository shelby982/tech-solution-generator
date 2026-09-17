"""agent prompt 模板与构造函数的契约测试。

确保：
- 系统 prompt 非空
- tone 三档完整且内嵌 BIDDER_IDENTITY_RULE
- 各 user prompt builder 输出包含关键短语（同时是 mock 路由关键字）
"""

from agents.prompts import (
    BIDDER_IDENTITY_RULE,
    COMPLIANCE_REVIEW_SYSTEM,
    LETTER_SYSTEM,
    OUTLINE_EXTRACT_SYSTEM,
    RERANK_SYSTEM,
    SECTION_OUTLINE_SYSTEM,
    TECH_REVIEW_SYSTEM,
    TONE_SYSTEM_PROMPTS,
    build_compliance_review_user,
    build_letter_user,
    build_outline_extract_user,
    build_rerank_user,
    build_section_outline_user,
    build_tech_review_user,
)


def test_all_system_prompts_non_empty():
    for s in (
        OUTLINE_EXTRACT_SYSTEM,
        RERANK_SYSTEM,
        SECTION_OUTLINE_SYSTEM,
        LETTER_SYSTEM,
        TECH_REVIEW_SYSTEM,
        COMPLIANCE_REVIEW_SYSTEM,
        BIDDER_IDENTITY_RULE,
    ):
        assert isinstance(s, str)
        assert len(s.strip()) > 50


def test_tone_system_prompts_has_three_tones():
    assert set(TONE_SYSTEM_PROMPTS.keys()) >= {"official", "tech", "concise"}
    for tone, prompt in TONE_SYSTEM_PROMPTS.items():
        assert BIDDER_IDENTITY_RULE in prompt, f"{tone} 必须内嵌 BIDDER_IDENTITY_RULE"


def test_outline_extract_user_contains_mock_keyword():
    """user prompt 必须含 '提炼以下八项内容' 关键短语，以触发 LLM mock 路由。"""
    user = build_outline_extract_user(
        title="技术架构要求",
        content="系统须采用微服务架构...",
    )
    assert "技术架构要求" in user
    assert "系统须采用微服务架构" in user
    assert "提炼以下八项内容" in user


def test_outline_extract_user_includes_marks_and_contexts():
    user = build_outline_extract_user(
        title="t",
        content="c",
        special_marks="★▲",
        scoring_context="评分要点 A",
        evaluation_context="评审要点 B",
    )
    assert "评分上下文" in user or "评分对应" in user or "评分要点 A" in user
    assert "评审上下文" in user or "评审对应" in user or "评审要点 B" in user


def test_letter_user_contains_mock_keyword():
    user = build_letter_user({"title": "投标承诺书", "requirement": "x"})
    assert "投标承诺书" in user
    # 触发 mock fixture 路由
    assert "承诺书" in user


def test_section_outline_user_contains_block_title():
    user = build_section_outline_user(
        {"title": "技术方案", "requirement": "X", "key_points": "Y"},
        extra_context="参考素材...",
    )
    assert "技术方案" in user
    assert "写作大纲" in user


def test_review_users_contain_keyword_for_mock_routing():
    """王安石/包拯 user prompt 必须含 '评审' 触发 mock 路由。"""
    block = {"block_id": "s1", "title": "X", "content": "Y", "kind": "tech"}
    matrix = {
        "requirement": "R",
        "key_points": "K",
        "veto_items": "V",
        "bonus_items": "B",
        "evidence_required": "E",
        "indicators": "I",
    }
    tech_user = build_tech_review_user(block, matrix)
    comp_user = build_compliance_review_user(block, matrix)
    assert "评审" in tech_user
    assert "评审" in comp_user


def test_rerank_user_contains_chunks_and_query():
    user = build_rerank_user(
        chunks=[{"chunk_id": "a", "content": "微服务"}],
        query="架构",
        requirement="高可用",
    )
    assert "微服务" in user
    assert "架构" in user
    assert "高可用" in user
    # 锁定 mock 路由用的稳定关键词，避免后续 prompt 调优时无意丢失
    assert "应标要求" in user
    assert "matches" in user


def test_review_users_handle_none_block_fields():
    """title / content 显式为 None 时不应渲染 'None' 字面量。"""
    block = {"block_id": "s1", "title": None, "content": None}
    matrix = {"requirement": None, "key_points": None, "veto_items": None,
              "bonus_items": None, "evidence_required": None, "indicators": None}
    tech_user = build_tech_review_user(block, matrix)
    comp_user = build_compliance_review_user(block, matrix)
    assert "None" not in tech_user
    assert "None" not in comp_user


def test_review_prompts_request_material_fields():
    """两位评审的 user prompt 都必须要求输出 needs_material / material_query。"""
    from agents.prompts import build_compliance_review_user, build_tech_review_user

    block = {"block_id": "s1", "title": "配电系统", "content": "正文", "kind": "tech"}
    row = {"requirement": "提供业绩证明", "veto_items": "★必须提供 3 年内业绩"}

    for prompt in (
        build_tech_review_user(block, row),
        build_compliance_review_user(block, row),
    ):
        assert "needs_material" in prompt
        assert "material_query" in prompt


def test_format_feedback_block_renders_issues():
    """评审意见被格式化为带严重度的逐条清单。"""
    from agents.prompts import format_feedback_block

    issues = [
        {"severity": "critical", "point": "缺业绩证明", "suggestion": "补充合同"},
        {"severity": "low", "point": "表述冗余", "suggestion": "精简"},
    ]

    text = format_feedback_block(issues)

    assert "上轮评审意见" in text
    assert "CRITICAL" in text
    assert "缺业绩证明" in text
    assert "补充合同" in text


def test_format_feedback_block_flags_material_issues():
    """needs_material 的问题必须提示用占位符而不是编造数据。"""
    from agents.prompts import format_feedback_block

    issues = [{
        "severity": "critical", "point": "缺型式试验报告",
        "suggestion": "补充", "needs_material": True,
    }]

    text = format_feedback_block(issues)

    assert "待补充" in text
    assert "不要编造" in text


def test_format_feedback_block_empty_returns_blank():
    """空意见返回空串——调用方靠它判断是否走修订分支。"""
    from agents.prompts import format_feedback_block

    assert format_feedback_block([]) == ""
    assert format_feedback_block(None) == ""


# ─────────────────────────────────────────────
# build_outline_draft_user / build_spec_digest
# ─────────────────────────────────────────────

def test_outline_draft_prompt_contains_mock_routing_keyword():
    """mock 模式靠这个关键词路由到目录 fixture，删了会让 mock/单测静默走错分支。"""
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(instruction="", doc_summary="", spec_digest="【一】")
    assert "提炼应答文件目录" in user


def test_outline_draft_prompt_puts_instruction_first_and_marks_priority():
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(
        instruction="按评分项逐条拆章",
        doc_summary="摘要",
        spec_digest="【一】正文",
    )
    assert "最高优先级" in user
    assert "按评分项逐条拆章" in user
    # 用户要求必须排在规范书摘要之前
    assert user.index("按评分项逐条拆章") < user.index("【一】正文")


def test_outline_draft_prompt_truncates_instruction():
    from agents.prompts import (
        OUTLINE_DRAFT_INSTRUCTION_BUDGET,
        build_outline_draft_user,
    )
    user = build_outline_draft_user(
        instruction="x" * (OUTLINE_DRAFT_INSTRUCTION_BUDGET + 500),
        doc_summary="", spec_digest="",
    )
    assert "x" * (OUTLINE_DRAFT_INSTRUCTION_BUDGET + 1) not in user
    assert "x" * OUTLINE_DRAFT_INSTRUCTION_BUDGET in user


def test_outline_draft_prompt_omits_empty_previous_and_material():
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(instruction="", doc_summary="", spec_digest="")
    assert "上一版目录" not in user
    assert "原始素材摘录" not in user


def test_outline_draft_prompt_includes_previous_outline_when_given():
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(
        instruction="", doc_summary="", spec_digest="",
        previous_outline="- 上一版一级",
    )
    assert "上一版目录" in user
    assert "- 上一版一级" in user


def test_outline_draft_prompt_keeps_material_extension_point():
    """本轮不读素材，但形参要留着 —— 下一轮接入时只改调用侧。"""
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(
        instruction="", doc_summary="", spec_digest="",
        material_digest="素材片段甲",
    )
    assert "原始素材摘录" in user
    assert "素材片段甲" in user


def test_spec_digest_respects_budget():
    """总长度（含标题行）必须落在预算内。"""
    from agents.prompts import build_spec_digest
    sections = [
        {"title": f"章节{i}", "content": "正文" * 500}
        for i in range(20)
    ]
    digest = build_spec_digest(sections, budget=2000)
    assert len(digest) <= 2000
    assert "章节0" in digest


def test_spec_digest_title_lines_count_against_budget():
    """标题行不计预算时，上千个章节光标题就能把 prompt 撑爆（实测 5.9 万字符）。"""
    from agents.prompts import build_spec_digest
    sections = [
        {"title": "很长的章节标题" * 4, "content": ""}
        for _ in range(500)
    ]

    digest = build_spec_digest(sections, budget=2000)

    assert len(digest) <= 2000
    assert "因长度限制未列出" in digest   # 截断要显式告知模型


def test_spec_digest_no_truncation_marker_when_all_fit():
    from agents.prompts import build_spec_digest
    digest = build_spec_digest([
        {"title": "章节一", "content": "正文一"},
        {"title": "章节二", "content": "正文二"},
    ], budget=2000)
    assert "未列出" not in digest
    assert "章节一" in digest and "章节二" in digest


def test_spec_digest_keeps_titles_for_empty_sections():
    from agents.prompts import build_spec_digest
    digest = build_spec_digest([
        {"title": "空章节", "content": ""},
        {"title": "有内容", "content": "正文"},
    ])
    assert "【空章节】" in digest
    assert "正文" in digest


def test_spec_digest_handles_empty_input():
    from agents.prompts import build_spec_digest
    assert build_spec_digest([]) == ""
    assert build_spec_digest(None) == ""


# ─────────────────────────────────────────────
# build_spec_digest：选中 / 未选中分开处理
# ─────────────────────────────────────────────

def test_spec_digest_without_selected_key_is_unchanged():
    """改造前的调用点不带 selected 键，输出必须逐字保持原样（无分组标题）。"""
    from agents.prompts import build_spec_digest
    digest = build_spec_digest([
        {"title": "章节一", "content": "正文一"},
        {"title": "章节二", "content": "正文二"},
    ], budget=2000)

    assert "已定位到的材料" not in digest
    assert "文件的其余部分" not in digest
    assert digest == "【章节一】\n正文一\n【章节二】\n正文二"


def test_spec_digest_keeps_unselected_sections_as_titles_only():
    """未选中的部分正文一律丢弃，只留标题。"""
    from agents.prompts import build_spec_digest
    digest = build_spec_digest([
        {"title": "标包2：高可靠技术专题", "content": "评分标准正文", "selected": True},
        {"title": "标包1：关键业务场景", "content": "无关正文甲", "selected": False},
        {"title": "标包3：主数据管理", "content": "无关正文乙", "selected": False},
    ], budget=2000, unselected_budget=500)

    assert "评分标准正文" in digest
    assert "标包1：关键业务场景" in digest and "标包3：主数据管理" in digest
    assert "无关正文甲" not in digest
    assert "无关正文乙" not in digest
    # 两段有各自的小标题，模型才知道哪部分是重点
    assert "已定位到的材料" in digest
    assert "文件的其余部分" in digest
    assert digest.index("已定位到的材料") < digest.index("文件的其余部分")


def test_spec_digest_unselected_titles_have_their_own_budget():
    """未选中标题不能把选中部分的正文挤出总预算（实测 379 章标题合计 7000+ 字符）。"""
    from agents.prompts import (
        OUTLINE_DRAFT_UNSELECTED_BUDGET,
        build_spec_digest,
    )
    sections = [{"title": "标包2：目标", "content": "正文" * 300, "selected": True}]
    sections += [
        {"title": f"很长的无关章节标题{i}", "content": "无关" * 50, "selected": False}
        for i in range(300)
    ]

    digest = build_spec_digest(sections, budget=2000, unselected_budget=300)

    assert "正文" * 300 in digest                      # 选中段正文完整保留
    assert "其余" in digest and "未列出" in digest      # 截断要显式告知
    # 未选中部分受自己的子预算约束，不会吃掉选中段的配额
    unselected_block = digest.split("文件的其余部分", 1)[1]
    assert len(unselected_block) <= 300 + len("【文件的其余部分（仅列标题，不要据此展开章节）】")


def test_spec_digest_unselected_only_truncates_when_needed():
    from agents.prompts import build_spec_digest
    digest = build_spec_digest([
        {"title": "选中", "content": "正文", "selected": True},
        {"title": "未选中", "content": "", "selected": False},
    ], budget=2000, unselected_budget=1200)

    assert "未选中" in digest
    assert "未列出" not in digest


# ─────────────────────────────────────────────
# 检索范围兜底
# ─────────────────────────────────────────────

def test_scope_select_prompt_contains_mock_routing_keyword():
    """mock 模式靠这个关键词路由；它必须排在最前 —— system prompt 含「评审」二字。"""
    from agents.prompts import build_scope_select_user
    user = build_scope_select_user("标包2", ["a.pdf"], ["第一章 总则"])
    assert "圈定检索范围" in user


def test_scope_select_prompt_carries_instruction_titles_and_files():
    from agents.prompts import build_scope_select_user
    user = build_scope_select_user(
        instruction="用标包2的技术评分要求拆分",
        file_names=["主招标文件.pdf", "技术招标文件.docx"],
        section_titles=["第一章 总则", "2.2.4 技术评分标准"],
    )

    assert "用标包2的技术评分要求拆分" in user
    assert "主招标文件.pdf" in user
    assert "2.2.4 技术评分标准" in user
    # 只要检索关键词，不让模型挑内容
    assert "只输出 JSON 对象" in user


def test_scope_select_prompt_truncates_titles():
    from agents.prompts import SCOPE_SELECT_TITLES_BUDGET, build_scope_select_user
    user = build_scope_select_user("要求", [], ["很长的标题" * 5000])
    assert "很长的标题" * (SCOPE_SELECT_TITLES_BUDGET // 5 + 1) not in user


def test_outline_draft_prompt_follows_material_structure_when_located():
    """用户要的是「按评分要求中的每一点拆章」——定位到那部分后目录要复刻它的结构。"""
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(
        instruction="按照评分要求中的每一点进行大纲拆分章节",
        doc_summary="",
        spec_digest="【已定位到的材料（请据此编排章节）】\n【标包2】\n技术评分标准：…",
        follow_structure=True,
    )

    assert "严格按材料自身的结构拆分章节" in user
    assert "各自对应一个章节" in user
    # 规则要排在材料之前 —— 模型读材料时得带着这条规则读
    assert user.index("严格按材料自身的结构拆分章节") < user.index("【要求文件章节目录与原文摘录】")


def test_outline_draft_prompt_reorganizes_by_default():
    """没定位到具体部分时维持原行为：由模型重新组织，不加结构约束。"""
    from agents.prompts import build_outline_draft_user
    user = build_outline_draft_user(
        instruction="按评分项逐条拆章", doc_summary="", spec_digest="【一】正文",
    )
    assert "严格按材料自身的结构拆分章节" not in user


def test_outline_draft_system_yields_to_user_structure_request():
    """system 的「可响应性」会把标题推向「我方要写什么」，得给结构复刻留出例外。"""
    from agents.prompts import OUTLINE_DRAFT_SYSTEM
    assert "按材料自身结构拆分" in OUTLINE_DRAFT_SYSTEM


def test_outline_draft_system_scopes_coverage_to_given_material():
    """「要求文件中出现的」会把模型推向覆盖全文，收窄成「给定材料」才压得下节点数。"""
    from agents.prompts import OUTLINE_DRAFT_SYSTEM
    assert "给定材料" in OUTLINE_DRAFT_SYSTEM
    assert "要求文件中出现的" not in OUTLINE_DRAFT_SYSTEM
    assert "不要据此展开章节" in OUTLINE_DRAFT_SYSTEM
