"""插入面板：AI 生成表格的 Markdown 规整（不依赖模型）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from routes.chapter_studio import _normalize_markdown_table


def test_normalize_plain_table():
    raw = "| 维度 | 现状 | 目标 |\n| --- | --- | --- |\n| 覆盖范围 | 【待补充：范围】 | 全省 |"
    md = _normalize_markdown_table(raw)
    assert md.splitlines()[0] == "| 维度 | 现状 | 目标 |"
    assert "---" in md.splitlines()[1]
    assert "【待补充：范围】" in md


def test_normalize_fixes_ragged_columns():
    raw = "| A | B |\n| --- | --- | --- |\n| 1 |\n| 2 | 3 | 4 |"
    md = _normalize_markdown_table(raw)
    rows = md.splitlines()
    assert rows[0].count("|") == 4        # 补齐到 3 列
    assert all(r.count("|") == 4 for r in rows)


def test_normalize_strips_code_fence_and_noise():
    raw = "说明文字\n```\n| 要求 | 响应 |\n| --- | --- |\n| r1 | s1 |\n```"
    md = _normalize_markdown_table(raw)
    assert md.startswith("| 要求 | 响应 |")
    assert "```" not in md


def test_normalize_returns_empty_for_non_table():
    assert _normalize_markdown_table("这不是表格") == ""
