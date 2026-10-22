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
    system_tasks = payload["system"]
    # 标题动词开头、不超过 24 字
    for task in need:
        assert len(task["title"]) <= 24
        assert task["title"][0] in "上传定核补系统精统一生成", task["title"]
    # 材料缺口 → 上传卡；决定项 → 选择卡；引用 → 核对卡（同类合并）
    assert any(t["action"]["type"] == "upload_material" for t in need)
    assert any(t["action"]["type"] == "choose" for t in need)
    # 引用来源改由"系统先查"（system 区），用户区不再出现
    assert any("引用" in t["title"] for t in (need + system_tasks))
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


def test_delivery_gate_scans_and_fixes():
    """P0 交付门：内部语法/术语/占位符 三类硬门 + 可自动清理。"""
    from services.delivery_gate import auto_fix, scan

    # 合法图块（≥2 节点）不算语法残留；未闭合块才算
    content = ("## 一\n\n本项目由任务包已确认的结论构成，评分点要求深入。\n\n"
               ":::diagram type=layered 对比图\n甲\n乙\n:::\n\n"
               ":::diagram type=flow 未闭合图\n甲\n\n"
               "正文还有【待补充：招标文件条款编号】。\n")
    result = scan(content)
    kinds = result["summary"]["by_kind"]
    assert kinds["syntax"] >= 1, result["summary"]      # 只有未闭合块
    assert kinds["term"] >= 2 and kinds["placeholder"] == 1
    assert result["summary"]["blocking"] == result["summary"]["auto_fixable"] + result["summary"]["manual"]

    fixed, changed, _changes = auto_fix(content)
    assert changed > 0
    assert ":::diagram type=layered 对比图" in fixed        # 合法图块必须保住
    assert "未闭合图" not in fixed or ":::diagram type=flow" not in fixed
    assert "任务包" not in fixed and "招标文件" in fixed
    remaining = scan(fixed)["summary"]
    assert remaining["by_kind"]["syntax"] == 0 and remaining["by_kind"]["term"] == 0
    assert remaining["by_kind"]["placeholder"] == 1        # 占位符需人工处理


def test_section_contract_checks():
    """小节契约：现状缺事实、目标无指标、行业复述法条 → 都能判出来。"""
    from services.section_contract import check_chapter, contract_for, kind_of

    assert kind_of("行业发展背景分析") == "industry"
    assert kind_of("项目目标体系") == "goal"
    assert "指标名" in contract_for("项目目标体系")

    filler = "整体运行尚不规范，管理水平有待提升，各方协同不足，流程仍需梳理完善。" * 3
    content = (f"## 项目现状描述\n\n{filler}\n\n"
               "## 项目目标体系\n\n完成平台建设，落实合规要求，实现管理提升与流程规范，持续优化运营效率。\n\n"
               "## 行业发展背景分析\n\n"
               "《甲法》要求…《乙法》要求…《丙法》要求…行业持续演进，同业普遍加强数据治理与安全建设。\n")
    items = check_chapter(content)
    titles = [i["title"] for i in items]
    assert any("现状节缺事实" in t for t in titles)
    assert any("目标节没有量化指标" in t for t in titles)
    assert any("行业节在复述政策条文" in t for t in titles)


def test_contract_result_has_no_quantified_target():
    """真实章节：目标节确实没有量化指标（内容审核抓到的同一问题）。"""
    from services.section_contract import check_chapter

    body = "## 项目目标体系\n\n" + "完成信息化系统建设，通过安全合规验收，落实分类分级与权限控制。" * 6
    items = check_chapter(body)
    assert any("量化指标" in i["title"] for i in items)


def test_weak_phrasing_and_citation_format():
    """弱表述与引用格式：真实存在的两类扣分点要能判出来。"""
    from services.todo_checks import citation_format_items, weak_phrasing_items

    weak = weak_phrasing_items(
        "## 一\n\n数据分类分级规则将按实施方案明确。\n\n拟在实施阶段明确权限控制。\n" + "拟将待" * 10)
    titles = [i["title"] for i in weak]
    assert any("含糊承诺" in t for t in titles)
    assert any("模糊表述偏多" in t for t in titles)

    fmt = citation_format_items(
        "## 一\n\n《中华人民共和国网络安全法》要求…按网络安全等级保护要求实施。\n"
        "【来源：招标文件-项目背景与建设依据】\n")
    fmt_titles = [i["title"] for i in fmt]
    assert any("缺文号" in t for t in fmt_titles)
    assert any("标准号" in t for t in fmt_titles)
    assert any("条款级" in t for t in fmt_titles)


def test_placeholder_purge_removes_all_placeholders():
    """正文净化：不允许任何"待补充"字眼；能删就删，删完整句，并记录分类。"""
    from services.placeholder_purge import classify, purge

    content = ("## 项目现状描述\n\n业务运行基本状态表现为有业务、有流程，但规范化程度不足。"
               "业务量以【待补充：项目单位现有业务量、岗位设置与运行统计】为准。\n\n"
               "安全防护以常规技术措施为主，等级保护落实情况【待补充：现有系统等级保护备案与测评情况】。\n\n"
               "招标文件具体条款编号、发文机关及文号标注为【待补充：招标文件对应条款原文及发文机关、文号】。\n")
    after, removed = purge(content)
    for word in ("待补充", "待补事实", "待确认", "待核实"):
        assert word not in after, after
    groups = classify(removed)
    assert groups["summary"]["upload"] >= 1
    assert groups["summary"]["search"] >= 1


def test_purge_keeps_complete_sentences_only():
    """删掉占位后若句子残缺（以连接词结尾）→ 整句删掉，不留病句。"""
    from services.placeholder_purge import purge

    after, _ = purge("## 一\n\n本节重点包括【待补充：条款原文】。\n\n正文结论明确。\n")
    assert "本节重点包括" not in after
    assert "正文结论明确" in after


def test_citation_enrich_adds_doc_numbers_and_standard():
    """引用补齐：政策文号与等级保护标准号由系统补（确定性信息）。"""
    from services.citation_enrich import enrich

    content = ("## 项目政策依据梳理\n\n"
               "《中华人民共和国网络安全法》要求履行安全保护义务。\n"
               "本项目按网络安全等级保护相关要求实施。\n")
    after, changes = enrich(content)
    assert any("补文号" in c for c in changes)
    assert "主席令第五十三号" in after
    assert any("补标准号" in c for c in changes)
    assert "GB/T 22239-2019" in after
    # 已有文号时不再重复补
    again, changes_again = enrich(after)
    assert changes_again == []
