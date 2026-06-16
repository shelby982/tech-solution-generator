"""domain/letter_detector 测试。"""

from domain.letter_detector import LETTER_KEYWORDS, is_letter_section


def test_is_letter_section_matches_canonical_letters():
    assert is_letter_section("投标承诺书") is True
    assert is_letter_section("廉洁承诺函") is True
    assert is_letter_section("授权委托书") is True
    assert is_letter_section("法定代表人声明书") is True


def test_is_letter_section_negative_cases():
    assert is_letter_section("技术方案概述") is False
    assert is_letter_section("项目背景") is False
    assert is_letter_section("") is False
    assert is_letter_section("   ") is False


def test_is_letter_section_strips_whitespace():
    assert is_letter_section("  投标承诺书 ") is True


def test_letter_keywords_is_tuple_and_non_empty():
    """LETTER_KEYWORDS 必须是 tuple（不可变）且至少含一项。"""
    assert isinstance(LETTER_KEYWORDS, tuple)
    assert len(LETTER_KEYWORDS) > 0
    assert "承诺书" in LETTER_KEYWORDS


def test_dispatcher_reexports_letter_detector():
    """旧路径 `from infra.llm.dispatcher import is_letter_section` 仍可用。"""
    from infra.llm.dispatcher import is_letter_section as legacy_is_letter
    from infra.llm.dispatcher import LETTER_KEYWORDS as legacy_keywords
    assert legacy_is_letter is is_letter_section
    assert legacy_keywords is LETTER_KEYWORDS
