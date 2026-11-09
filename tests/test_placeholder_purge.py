"""正文红线：不得出现"待补充/待填写"等占位符（缺的内容就不体现）。"""
from __future__ import annotations


def test_purge_removes_bracket_placeholders():
    from services.placeholder_purge import purge

    text = ("本节依据【待补充：立项批复或可研摘要】编制，"
            "并对照【待补全：技术规范书条款原文引用及逐条响应】。")
    out, removed = purge(text)
    assert "待补充" not in out and "待补全" not in out
    assert removed and removed[0]["placeholder"]
    # 断裂句（"依据为依据"）整句删掉，不留残句
    assert "为依据" not in out or out.strip() == ""


def test_purge_keeps_clean_content():
    from services.placeholder_purge import purge

    text = "现有系统 12 个，用户约 3000 人，数据量 2TB，2026 年完成改造。"
    out, removed = purge(text)
    assert out == text and removed == []


def test_purge_removes_bare_words_and_keeps_other_sentences():
    from services.placeholder_purge import purge

    text = "现有系统 12 个。具体指标待补充。另需核对等保级别。"
    out, _removed = purge(text)
    assert "待补充" not in out
    assert "现有系统 12 个" in out


def test_purge_redacts_source_inline_note():
    from services.placeholder_purge import purge

    text = "依据《网络安全法》【来源：全国人大（具体条款号待补充）】开展合规设计。"
    out, _removed = purge(text)
    assert "待补充" not in out
    assert "网络安全法" in out


def test_purge_table_cells_and_bad_tokens():
    from services.placeholder_purge import purge

    text = "| 指标 | 值 |\n| --- | --- |\n| 并发 | 【待填写：并发数】 |"
    out, removed = purge(text)
    assert "待填写" not in out and removed
    assert out.startswith("| 指标 | 值 |")


def test_conflict_check_tolerates_system_normalization():
    """保存冲突判定：只有"系统自动规范化"差异时不报 409（否则用户会凭空丢编辑）。"""
    from routes.blocks import _normalized_for_compare

    # 客户端持有的是"带占位符/未规整表格"的旧版本；库里的已被规范化
    client_view = "## 一\n本节依据【待补充：立项批复】编制。\n"
    stored = _normalized_for_compare(client_view)
    assert "待补充" not in stored
    # 归一后应一致 → 不触发冲突
    assert _normalized_for_compare(client_view) == _normalized_for_compare(stored)
    # 真正的内容差异仍然要能识别
    assert _normalized_for_compare("完全不同的正文内容。") != _normalized_for_compare(stored)
