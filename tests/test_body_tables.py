"""表格表名规范化。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.body_tables import ensure_table_captions


def test_inserts_caption_above_table():
    content = "## 政策依据映射表\n\n| 文件 | 要求 |\n| --- | --- |\n| A | B |\n"
    cleaned, added = ensure_table_captions(content)
    assert added == 1
    lines = cleaned.splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith("| 文件"))
    assert lines[idx - 1] == ""
    assert lines[idx - 2] == "**表：政策依据映射表**"


def test_keeps_existing_caption():
    content = "## 某节\n\n**表：已有表名**\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    _, added = ensure_table_captions(content)
    assert added == 0


def test_multiple_tables_numbered():
    content = "## 某节\n\n| A |\n| --- |\n| 1 |\n\n| B |\n| --- |\n| 2 |\n"
    cleaned, added = ensure_table_captions(content)
    assert added == 2
    assert "**表：某节**" in cleaned
    assert "**表：某节（2）**" in cleaned
