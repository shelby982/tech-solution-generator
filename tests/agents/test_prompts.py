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
    from agents.prompts import build_spec_digest
    sections = [
        {"title": f"章节{i}", "content": "正文" * 500}
        for i in range(20)
    ]
    digest = build_spec_digest(sections, budget=2000)
    assert len(digest) < 2000 + 100 * len(sections)  # 标题开销之外不超预算
    assert "章节0" in digest


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
