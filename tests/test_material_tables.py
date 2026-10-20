"""素材表格抽取（用于「从素材插入表格」）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def test_extract_docx_tables(tmp_path):
    from docx import Document
    from routes.materials import _extract_tables_from_file

    path = tmp_path / "a.docx"
    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    for c, text in enumerate(["项目", "金额", "年份"]):
        table.cell(0, c).text = text
    for c, text in enumerate(["某银行营销系统", "1200万", "2023"]):
        table.cell(1, c).text = text
    for c, text in enumerate(["某运营商计费", "800万", "2024"]):
        table.cell(2, c).text = text
    doc.save(str(path))

    tables = _extract_tables_from_file(str(path), ".docx")
    assert len(tables) == 1
    assert tables[0]["header"] == ["项目", "金额", "年份"]
    assert len(tables[0]["rows"]) == 3
    assert tables[0]["cols"] == 3


def test_extract_xlsx_tables(tmp_path):
    from openpyxl import Workbook
    from routes.materials import _extract_tables_from_file

    path = tmp_path / "b.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "业绩表"
    ws.append(["客户", "项目", "金额(万)"])
    ws.append(["A银行", "营销系统", "1200"])
    ws.append(["B运营商", "计费系统", "800"])
    wb.save(str(path))

    tables = _extract_tables_from_file(str(path), ".xlsx")
    assert len(tables) == 1
    assert tables[0]["name"] == "业绩表"
    assert tables[0]["rows"][1] == ["A银行", "营销系统", "1200"]


def test_extract_unsupported_returns_empty(tmp_path):
    from routes.materials import _extract_tables_from_file

    assert _extract_tables_from_file("/tmp/does-not-exist.pdf", ".pdf") == []
