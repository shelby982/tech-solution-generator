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


def test_delivery_tasks_three_zones_and_verb_titles():
    """v3：任务卡 —— 三区、动词标题、影响文案、同类合并。"""
    from services.todo_tasks import build_delivery_tasks

    payload = build_delivery_tasks(
        block_id="s1.1",
        content="## 一\n\n采集范围待明确。\n\n《某办法》相关规定。",
        deliverables=[{"kind": "figure", "title": "对比图", "status": "未写"},
                      {"kind": "text", "title": "背景", "status": "不足",
                       "written_words": 6, "target_words": 800}],
        material_gaps=[{"kind": "我方材料（业绩）", "lack": 1, "availability": "upload",
                        "purpose": "举证", "reason": "公开渠道没有"}],
        generation_gaps=[{"text": "缺少测算依据"}],
        word_plan={"written": 6000, "total": 3000},
        decisions=[{"group": "decide", "title": "口径未定：出现「待明确」",
                    "evidence": "## 一：采集范围待明确", "suggestions": ["改为拟实施", "删除"], 
                    "target": "pending:待明确"}],
        proofs=[{"group": "proof", "title": "引用未标来源：《某办法》", "evidence": "## 一：《某办法》"}],
        completion={"ok": False, "reasons": ["4 条要求正文未回应"]},
    )
    zones = set(payload)
    assert {"need_user", "system", "suggestion", "summary"} <= zones
    need = payload["need_user"]
    # 标题动词开头、不超过 24 字
    for task in need:
        assert len(task["title"]) <= 24
        assert task["title"][0] in "上传定核补系统精统一生成", task["title"]
    # 材料缺口 → 上传卡；决定项 → 选择卡；引用 → 核对卡（同类合并）
    assert any(t["action"]["type"] == "upload_material" for t in need)
    assert any(t["action"]["type"] == "choose" for t in need)
    assert any("引用" in t["title"] for t in need)
    # 系统任务：依据 + 图表 + 补写，且不计入 need_user
    assert any("依据" in t["title"] for t in payload["system"])
    # 篇幅超 → 建议
    assert any("精简" in t["title"] for t in payload["suggestion"])
    # 每张卡都有影响文案与定位字段
    for task in need + payload["system"] + payload["suggestion"]:
        assert task["impact"] in ("export_block", "completion_block", "quality", "suggestion")
        assert task["impact_text"]
        assert "target" in task


def test_todo_states_merge_removes_ignored(monkeypatch):
    """v3：服务端状态合并 —— 忽略/稍后/已消除的从待办移出。"""
    from services.todo_states import merge_status

    tasks = {"need_user": [{"id": "a"}, {"id": "b"}], "system": [{"id": "c"}], "suggestion": [],
             "summary": {"need_user": 2, "system": 1, "suggestion": 0}}
    merged = merge_status(tasks, {"a": {"status": "ignored", "reason": "", "until_at": None},
                                  "c": {"status": "snoozed", "reason": "", "until_at": "2030-01-01"}})
    assert [t["id"] for t in merged["need_user"]] == ["b"]
    assert merged["system"] == []
    assert merged["summary"]["need_user"] == 1
    assert merged["summary"]["ignored"] == 2


def test_citation_paragraph_level_avoids_false_positive():
    """段末统一标来源是标书常见写法 —— 不能按句子报缺来源（实测踩过的假阳性）。"""
    from services.todo_checks import citation_items

    content = ("## 一\n\n"
               "①《中华人民共和国网络安全法》要求保障数据安全；"
               "②《中华人民共和国数据安全法》要求分类分级。"
               "上述法律以现行文本为准【来源：网络安全法】【来源：数据安全法】。\n\n"
               "二、分项目标：同步落实《中华人民共和国网络安全法》要求。\n")
    items = citation_items(content)
    # 第一段有来源 → 不报；第二段没有 → 报 1 条
    assert len(items) == 1
    assert "网络安全法" in items[0]["title"]


def test_tasks_without_locator_are_dropped():
    """v3 硬规则：带原句但正文里找不到的条目不能进待办。"""
    from services.todo_tasks import build_delivery_tasks

    payload = build_delivery_tasks(
        block_id="s1.1", content="## 一\n\n正文里没有那句话。",
        deliverables=[], material_gaps=[], generation_gaps=[],
        word_plan={"written": 10, "total": 1000},
        decisions=[{"group": "decide", "title": "口径未定", "sentence": "这句话并不存在于正文",
                    "evidence": "## 一：这句话并不存在于正文", "suggestions": ["改"], "target": "x"}],
        proofs=[], completion=None)
    assert payload["need_user"] == []
