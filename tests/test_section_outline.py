"""大纲计划 + 分节写稿的纯函数。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.section_outline import (
    build_section_payload, fallback_outline, normalize_outline, replace_section, section_ranges,
)


def test_fallback_outline_from_tasks():
    chapter = {"title": "项目背景", "tasks": [
        {"kind": "text", "title": "政策导向", "purpose": "说明政策要求"},
        {"kind": "table", "title": "指标对比", "purpose": "对比现状与目标"},
    ]}
    sections = fallback_outline(chapter)
    assert [s["title"] for s in sections] == ["政策导向", "指标对比"]
    assert sections[1]["need"] == "table"


def test_normalize_outline_accepts_model_output():
    raw = '{"sections":[{"title":"政策要求","points":["国家文件","行业标准"],"words":800,"need":"table"},' \
          '{"title":"现状差距","points":["数据对比"],"words":1000}]}'
    sections = normalize_outline(raw, {"title": "x"})
    assert len(sections) == 2
    assert sections[0]["need"] == "table"
    assert sections[0]["words"] == 800


def test_normalize_outline_falls_back_on_garbage():
    sections = normalize_outline("不是 JSON", {"title": "x", "tasks": [{"kind": "text", "title": "A"}]})
    assert len(sections) >= 1


def test_section_ranges_and_replace():
    content = "## 政策导向\n\n正文一\n\n## 现状差距\n\n正文二\n"
    ranges = section_ranges(content)
    assert [r[0] for r in ranges] == ["政策导向", "现状差距"]
    new_content, replaced = replace_section(content, "现状差距", "新的正文")
    assert replaced is True
    assert "新的正文" in new_content and "正文二" not in new_content
    assert "正文一" in new_content
    appended, replaced2 = replace_section(content, "解决措施", "措施正文")
    assert replaced2 is False and "## 解决措施" in appended


def test_build_payload_trims_evidence():
    payload = build_section_payload(
        {"title": "项目背景", "requirements": [{"text": "评分点A"}]},
        {"title": "政策导向", "points": ["p1"], "words": 900},
        evidence="x" * 9000,
    )
    assert payload["chapter"] == "项目背景"
    assert payload["requirements"] == ["评分点A"]
    assert len(payload["evidence"]) <= 6000
