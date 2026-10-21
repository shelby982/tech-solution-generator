

def test_build_todo_items_groups_by_owner():
    """待办按"谁来消除"分组：A 用户 / B 检索 / E 自动 / F 门槛。"""
    from services.writing_plans import build_todo_items

    items = build_todo_items(
        deliverables=[
            {"kind": "text", "title": "背景", "status": "不足", "written_words": 6, "target_words": 833},
            {"kind": "figure", "title": "对比图", "status": "未写"},
        ],
        material_gaps=[
            {"kind": "我方材料（数据/案例/业绩）", "lack": 1, "availability": "upload",
             "purpose": "提供现状数据与佐证", "reason": "评分点要求举证"},
            {"kind": "政策/制度原文", "lack": 2, "availability": "public",
             "suggestions": ["gov.cn"], "purpose": "支撑政策依据"},
        ],
        generation_gaps=[{"text": "依据核对：缺少测算依据原文"}],
        word_plan={"written": 48, "total": 3000},
        content="## 背景\n\n正文",
        is_parent=False,
        is_catalog=False,
        completion={"ok": False, "reasons": ["5 条要求正文未回应"]},
    )
    groups = {item["group"] for item in items}
    assert {"user", "search", "auto", "gate"} <= groups
    # 阻塞项排在最前
    assert items[0]["severity"] == "block"
    # 用户类带上传动作，检索类带一键检索
    assert any(i["group"] == "user" and i["action"] == "upload" for i in items)
    assert any(i["group"] == "search" and i["action"] == "search" for i in items)
    # 每个条目都有统一形态字段
    for item in items:
        assert set(item) >= {"id", "group", "group_label", "title", "evidence", "source",
                             "severity", "action", "action_label", "status"}
