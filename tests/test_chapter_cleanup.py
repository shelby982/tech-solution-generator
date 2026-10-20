"""正文占位内容清理。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.chapter_cleanup import has_pending, strip_pending_sections


def test_removes_pending_section_and_placeholder_lines():
    content = (
        "### 一、营销现状\n\n系统自2015年上线。\n\n"
        "### 待核对与补充\n\n"
        "【待补充：缺少招标范围内的任务清单】\n\n"
        "【待补充：缺少现状评估材料】\n\n"
        "### 二、政策动因\n\n政策要求高质量发展。\n"
    )
    cleaned, removed = strip_pending_sections(content)
    assert "待核对与补充" not in cleaned
    assert "【待补充" not in cleaned
    assert "营销现状" in cleaned and "政策动因" in cleaned
    assert len(removed) == 2


def test_keeps_table_placeholders():
    content = "| 维度 | 现状 |\n| --- | --- |\n| 覆盖 | 【待补充：范围】 |\n"
    cleaned, removed = strip_pending_sections(content)
    assert "【待补充：范围】" in cleaned      # 表格里的占位是要用户填的，保留
    assert removed == []


def test_has_pending():
    assert has_pending("### 待核对与补充\n【待补充：x】")
    assert not has_pending("### 一、正文\n正常内容")
