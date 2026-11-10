"""重写本节必须"替换"而不是"追加"—— 用户反馈：点重新编写后正文出现重复的一块。"""
from __future__ import annotations


def test_replace_markdown_heading():
    from services.section_outline import replace_section

    content = "## 现状\n甲段落内容。\n\n## 目标\n乙段落内容。\n"
    merged, replaced = replace_section(content, "现状", "新写的现状。")
    assert replaced is True
    assert merged.count("## 现状") == 1
    assert "甲段落内容" not in merged and "新写的现状" in merged
    assert "乙段落内容" in merged                      # 其他节不受影响


def test_replace_bold_heading_does_not_duplicate():
    """真实正文用 **标题**（不是 ##）—— 之前会匹配不到而追加，导致重复。"""
    from services.section_outline import replace_section

    content = ("**进度计划总体思路**\n\n旧思路。\n\n"
               "**阶段划分**\n\n旧阶段。\n\n"
               "**里程碑**\n\n旧里程碑。\n")
    merged, replaced = replace_section(content, "阶段划分", "新阶段说明。")
    assert replaced is True
    assert merged.count("**阶段划分**") == 1
    assert "旧阶段" not in merged
    assert "**里程碑**" in merged and "旧里程碑" in merged


def test_replace_twice_is_idempotent():
    """连续重写两次：长度不应持续膨胀（核心回归）。"""
    from services.section_outline import replace_section

    content = "**阶段划分**\n\n旧阶段。\n\n**里程碑**\n\n旧里程碑。\n"
    first, ok1 = replace_section(content, "阶段划分", "第一版。")
    second, ok2 = replace_section(first, "阶段划分", "第二版。")
    assert ok1 and ok2
    assert second.count("**阶段划分**") == 1
    assert len(second) == len(first)                   # 替换同长度内容 → 长度不变


def test_replace_numbered_title_matches_plain_heading():
    """计划里的节标题带编号（1 项目背景），正文标题不带编号 —— 归一化后要能匹配。"""
    from services.section_outline import replace_section

    content = "## 项目背景与建设动因\n\n旧背景。\n\n## 建设目标\n\n目标。\n"
    merged, replaced = replace_section(content, "1 项目背景与建设动因", "新背景。")
    assert replaced is True
    assert merged.count("项目背景与建设动因") == 1
    assert "新背景" in merged and "旧背景" not in merged


def test_replace_appends_only_when_really_missing():
    from services.section_outline import replace_section

    content = "## 现状\n内容。\n"
    merged, replaced = replace_section(content, "从未出现的节", "新增内容。")
    assert replaced is False
    assert "## 从未出现的节" in merged


def test_section_ranges_supports_three_styles():
    from services.section_outline import normalize_title, section_ranges

    assert [t for t, _, _ in section_ranges("## 甲\n内容\n## 乙\n内容\n")] == ["甲", "乙"]
    assert [t for t, _, _ in section_ranges("**甲**\n内容\n**乙**\n内容\n")] == ["甲", "乙"]
    assert normalize_title("**3. 项目背景**") == "项目背景"
    assert normalize_title("## 4.2 建设内容：") == "建设内容"


def test_job_level_replace_uses_same_logic():
    """任务级写回（chapter_sse）也必须替换而不是追加。"""
    from services.chapter_sse import replace_section_text

    content = "**阶段划分**\n\n旧阶段。\n\n**里程碑**\n\n旧里程碑。\n"
    merged = replace_section_text(content, "阶段划分", "新阶段。")
    assert merged.count("**阶段划分**") == 1
    assert "旧阶段" not in merged


def test_content_sections_parse_matches_backend_styles():
    """进度面板兜底解析：正文里的 ## 与 **标题** 都要被识别成小节（无大纲也能重写）。"""
    from services.section_outline import section_ranges

    content = "## 建设范围\n\n内容一。\n\n**三层结构说明**\n\n内容二。\n"
    titles = [t for t, _, _ in section_ranges(content)]
    assert titles == ["建设范围", "三层结构说明"]


def test_merge_media_sections_into_parent():
    """「表：X」「图：X」是父节的交付物，不能和父节并列成独立小节。"""
    from services.section_outline import merge_media_sections

    sections = [
        {"title": "政策要求与项目对应关系", "deliverables": []},
        {"title": "表：政策要求与项目对应关系", "need": "table"},
        {"title": "行业背景分析", "deliverables": []},
    ]
    merged = merge_media_sections(sections)
    assert [s["title"] for s in merged] == ["政策要求与项目对应关系", "行业背景分析"]
    assert merged[0]["deliverables"][0]["kind"] == "table"
    assert merged[0]["deliverables"][0]["title"] == "政策要求与项目对应关系"
    # 正常小节不受影响
    assert merged[1]["deliverables"] == []


def test_section_ranges_skips_media_captions():
    """正文里的「**表：X**」是表名（caption），不能当成小节 → 否则列表里会与父节并列编号。"""
    from services.section_outline import section_ranges

    content = ("**项目现状描述**\n\n正文一。\n\n"
               "**表：项目现状描述**\n\n| 指标 | 值 |\n| --- | --- |\n| 并发 | 500 |\n\n"
               "**现状差距分析**\n\n正文二。\n")
    assert [t for t, _, _ in section_ranges(content)] == ["项目现状描述", "现状差距分析"]
