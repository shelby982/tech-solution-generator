"""infra/docx 导出器冒烟测试。"""


# ─────────────────────────────────────────────
# 最小输入的端到端导出
# ─────────────────────────────────────────────

def test_export_minimal_dict_does_not_raise(tmp_path):
    """最小冒烟：给一个最小章节字典，导出不报错且产出非空 docx。"""
    from infra.docx import sections_to_docx

    sections = [
        {
            "id": "s1",
            "title": "项目概述",
            "level": 1,
            "content": "这是一段最小的正文内容。",
            "done": True,
        }
    ]

    data = sections_to_docx(sections, doc_title="冒烟测试")

    assert isinstance(data, (bytes, bytearray))
    assert len(data) > 0
    # DOCX 本质是 zip，魔数 PK\x03\x04
    assert data[:2] == b"PK"

    out = tmp_path / "out.docx"
    out.write_bytes(data)
    assert out.exists()
    assert out.stat().st_size > 0


def test_export_skips_undone_sections():
    """done=False 的章节不应导致异常，且仍产出有效 docx。"""
    from infra.docx import sections_to_docx

    sections = [
        {"id": "s1", "title": "已完成", "level": 1, "content": "正文。", "done": True},
        {"id": "s2", "title": "未完成", "level": 1, "content": "草稿。", "done": False},
    ]

    data = sections_to_docx(sections)
    assert isinstance(data, (bytes, bytearray))
    assert len(data) > 0
    assert data[:2] == b"PK"
