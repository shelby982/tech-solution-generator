"""infra/parser 测试：DOCX TOC/标题扫描兜底 + 公共工具单测。"""
import io
import pytest

from infra.parser import parse_document, ParsedDocument
from infra.parser.toc import (
    extract_special_marks,
    detect_level_from_numbering,
    clean_title,
    _clean_toc_line,
    _text_similarity,
)


# ─────────────────────────────────────────────
# toc.py 公共工具
# ─────────────────────────────────────────────

def test_extract_special_marks_strips_and_collects():
    marks, cleaned = extract_special_marks("★▲技术要求")
    assert "★" in marks and "▲" in marks
    assert cleaned == "技术要求"


def test_extract_special_marks_no_marks():
    marks, cleaned = extract_special_marks("普通章节")
    assert marks == []
    assert cleaned == "普通章节"


def test_detect_level_from_numbering_all_levels():
    assert detect_level_from_numbering("一、概述") == 1
    assert detect_level_from_numbering("第三章 系统设计") == 1
    assert detect_level_from_numbering("1. 项目背景") == 2
    assert detect_level_from_numbering("（1）需求分析") == 3
    assert detect_level_from_numbering("(2) 需求分析") == 3
    assert detect_level_from_numbering("①概述") == 4
    assert detect_level_from_numbering("1.1 子项") == 4


def test_detect_level_returns_none_for_non_heading():
    assert detect_level_from_numbering("普通段落正文") is None
    assert detect_level_from_numbering("") is None


def test_clean_title_collapses_whitespace():
    assert clean_title("  标题   有空格  ") == "标题 有空格"


def test_clean_toc_line_strips_dot_leaders_and_pages():
    assert _clean_toc_line("第一章 概述...........5") == "第一章 概述"
    assert _clean_toc_line("一、技术要求\t\t12") == "一、技术要求"


def test_text_similarity_prefix_and_containment():
    assert _text_similarity("abcdef", "abcxyz") == pytest.approx(3 / 6)
    assert _text_similarity("abc", "abcdefg") == 0.9  # 包含关系
    assert _text_similarity("", "abc") == 0.0


# ─────────────────────────────────────────────
# DOCX 集成：内存构造一个小 docx，走标题扫描兜底路径
# ─────────────────────────────────────────────

def _build_minimal_docx() -> io.BytesIO:
    """构造无 TOC 的最小 docx：两个 Heading 1 + 一个表格。"""
    from docx import Document

    doc = Document()
    doc.add_heading("第一章 项目概述", level=1)
    doc.add_paragraph("这是项目背景介绍段落。")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "指标"
    table.rows[0].cells[1].text = "数值"
    table.rows[1].cells[0].text = "工期"
    table.rows[1].cells[1].text = "60 天"
    doc.add_heading("第二章 技术方案", level=1)
    doc.add_paragraph("这是技术方案段落。")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def test_parse_docx_heading_scan_returns_sections():
    """无 TOC 文档走标题扫描兜底，识别两个章节并把表格并入正文。"""
    buf = _build_minimal_docx()
    parsed = parse_document(buf, suffix=".docx", filename="test.docx")

    assert isinstance(parsed, ParsedDocument)
    assert len(parsed.sections) == 2

    s1 = parsed.sections[0]
    assert "项目概述" in s1.title
    assert "项目背景" in s1.raw_content
    # 表格应被 markdown 化并出现在 s1 的正文里
    assert "| 指标" in s1.raw_content
    assert "工期" in s1.raw_content

    s2 = parsed.sections[1]
    assert "技术方案" in s2.title
    assert "技术方案段落" in s2.raw_content


def test_parse_document_unsupported_suffix_raises():
    """不支持的扩展名抛 ValueError。"""
    with pytest.raises(ValueError):
        parse_document(io.BytesIO(b"x"), suffix=".txt", filename="x.txt")


# ─────────────────────────────────────────────
# PDF 路径：仅做导入冒烟（不构造 PDF fixture）
# ─────────────────────────────────────────────

def test_pdf_module_imports_cleanly():
    from infra.parser import pdf as pdf_module
    assert hasattr(pdf_module, "parse_pdf")
