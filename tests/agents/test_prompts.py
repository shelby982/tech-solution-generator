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
