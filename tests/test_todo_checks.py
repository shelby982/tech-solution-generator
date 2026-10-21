"""待办 C/D 组检查（确定性规则）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.todo_checks import decision_items, number_items, proofread_items


def test_hard_promise_without_citation_is_blocking():
    content = "## 一\n\n我方保证系统零故障运行，响应时间 7×24 小时。"
    items = decision_items(content)
    assert any("承诺强度" in i["title"] and i["severity"] == "block" for i in items)


def test_promise_with_citation_not_flagged():
    content = "## 一\n\n按招标文件要求提供 7×24 响应【来源：技术规范书】。"
    assert not any("承诺强度" in i["title"] for i in decision_items(content))


def test_role_conflict_flagged():
    content = "## 一\n\n本项目由我方牵头实施。\n\n## 二\n\n我方参与平台建设。"
    assert any("角色口径不一致" in i["title"] for i in decision_items(content))


def test_term_variants_flagged():
    content = "## 一\n\n营销管理系统运行十年；营销系统覆盖全省。"
    items = proofread_items(content)
    assert items and items[0]["group"] == "proof"
    assert "术语不统一" in items[0]["title"]


def test_cite_style_mixed_flagged():
    content = "## 一\n\n甲【来源：A】乙[来源: B]"
    assert any("引用格式不统一" in i["title"] for i in proofread_items(content))


def test_table_numbers_must_exist_in_body():
    content = ("## 一\n\n用户规模约 1.2 亿。\n\n| 指标 | 数值 |\n| --- | --- |\n"
               "| 用户 | 1.2亿 |\n| 效率 | 9000万 |\n")
    items = number_items(content)
    assert items and "表格里" in items[0]["title"]


def test_same_metric_two_values_conflict():
    content = "## 一\n\n服务用户数 1.2 亿。\n\n## 二\n\n服务用户数 1.5 亿。\n"
    items = number_items(content)
    assert any("不同数值" in i["title"] for i in items)


def test_verb_subject_not_treated_as_metric():
    """「预计达到 0.98 万 / 3.18 万」是两个目标，不是同一指标冲突。"""
    content = "## 一\n\n2026 年预计达到 0.98 万笔。\n\n## 二\n\n2030 年预计达到 3.18 万笔。\n"
    assert not any("不同数值" in i["title"] for i in number_items(content))


def test_term_item_has_fix_payload():
    content = "## 一\n\n营销管理系统运行十年；营销系统覆盖全省。"
    items = proofread_items(content)
    assert items[0].get("fix", {}).get("replacements") == [["营销系统", "营销管理系统"]]
