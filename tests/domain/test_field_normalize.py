"""提炼字段口径归一化：评分档位从 requirement 挪到 score_items。

背景（用户实测）：项目 18 的 s1.1「技术条款响应总体说明」的 requirement 里，
12 条有 8 条是评分档位（优良 7-10 分 / 中等 6 分 / 未作响应不得分…）。
它们不可能有素材依据 → 匹配页满屏"无依据"。
"""

from __future__ import annotations

from domain.spec import OutlineMatrixRow
from domain.spec.field_normalize import normalize_row_fields, split_scoring_clauses


def _row(requirement="", score_items="", indicators=""):
    return OutlineMatrixRow(block_id="s1", title="t",
                            requirement=requirement, score_items=score_items,
                            indicators=indicators)


def test_moves_score_bands_out_of_requirement():
    row = _row(requirement=(
        "1. 投标方需对招标技术条款作出响应。\n"
        "2. 技术评分标准满分10分，按响应度分档评审：\n"
        "优良（7-10分）：投标响应度较高。\n"
        "中等（6分）：投标响应度良好。\n"
        "未作响应不得分。\n"
        "3. 投标方应完全满足招标技术要求。"
    ))
    moved = normalize_row_fields(row)["moved"]

    assert len(moved) == 4
    assert "投标方需对招标技术条款作出响应。" in row.requirement
    assert "投标方应完全满足招标技术要求。" in row.requirement
    # 评分口径一行不留在 requirement，且全部进了 score_items（保留原顺序）
    assert "优良（7-10分）" not in row.requirement
    assert row.score_items.split("\n") == [
        "2. 技术评分标准满分10分，按响应度分档评审：",
        "优良（7-10分）：投标响应度较高。",
        "中等（6分）：投标响应度良好。",
        "未作响应不得分。",
    ]


def test_merges_with_existing_score_items_without_duplicates():
    row = _row(requirement="投标方应完全满足招标技术要求。\n未作响应不得分。",
               score_items="技术评分标准 10分\n未作响应不得分。")
    normalize_row_fields(row)

    # 已有的不重复追加
    assert row.score_items.split("\n") == ["技术评分标准 10分", "未作响应不得分。"]
    assert row.requirement == "投标方应完全满足招标技术要求。"


def test_never_empties_requirement():
    """整段都是评分口径时**不动**：requirement 是章节主字段，清空会让匹配与撰写都没抓手。"""
    src = "技术评分标准满分10分\n优良（7-10分）：响应度较高。\n未作响应不得分。"
    row = _row(requirement=src)
    assert normalize_row_fields(row)["moved"] == []
    assert row.requirement == src


def test_keeps_actionable_sentences_that_mention_scoring():
    """带动作的句子不搬（按行判定，不拆句子）——「优于要求可获优良档评分」是我方要做的事。"""
    row = _row(requirement=("如存在部分或全部条款优于招标技术要求，可获得优良档评分。\n"
                            "投标方应避免非实质性负偏差，否则评分将降为一般档；"))
    assert normalize_row_fields(row)["moved"] == []
    assert "可获得优良档评分" in row.requirement


def test_empty_and_plain_requirement_is_noop():
    assert normalize_row_fields(_row())["moved"] == []
    assert normalize_row_fields(_row(requirement="提供实施进度计划。"))["moved"] == []


def test_split_scoring_clauses_reports_what_moved():
    keep, scoring = split_scoring_clauses("投标方应提供方案。\n满分10分")
    assert keep == "投标方应提供方案。"
    assert scoring == ["满分10分"]


def test_action_sentences_with_marks_are_not_moved():
    """带义务动词的句子不搬（实测误伤）：「投标响应须完全满足…获得中等（6分）及以上评分的前提。」

    requirement 是撰写阶段的主抓手，丢掉一条"必须做的事"比留下一条评分口径更糟。
    """
    row = _row(requirement=(
        "1. 投标人须按响应表逐条实质性响应，不得出现未作响应。\n"
        "2. 投标响应须完全满足招标技术要求，这是获得中等（6分）及以上评分的前提。\n"
        "3. 若投标响应度良好，对应中等档，得6分。"
    ))
    moved = normalize_row_fields(row)["moved"]

    assert moved == ["3. 若投标响应度良好，对应中等档，得6分。"]
    assert "须完全满足" in row.requirement
    assert "不得出现未作响应" in row.requirement


def test_drops_correspond_lines_already_in_score_items():
    """requirement 末尾的【评分对应】若与 score_items 逐条重复 → 删掉（实测 s2.1 重复 7 条）。

    这些行来自提炼 prompt 里那句"用【评分对应】追加对应条款"。
    同一个分值出现两处：界面上要求原文与评分项卡各显示一遍，撰写阶段也被重复喂。
    """
    row = _row(
        requirement=("1. 方案可行性：总体架构设计合理，技术方案可行。\n"
                     "2. 方案先进性：技术方案先进。\n"
                     "【评分对应】方案可行性 评分项 8-12分\n"
                     "【评分对应】方案先进性 评分项 12-18分"),
        score_items="方案可行性 8-12分\n方案先进性 12-18分",
    )
    res = normalize_row_fields(row)

    assert res["dropped"] == ["【评分对应】方案可行性 评分项 8-12分",
                              "【评分对应】方案先进性 评分项 12-18分"]
    assert res["moved"] == []
    assert row.requirement == "1. 方案可行性：总体架构设计合理，技术方案可行。\n2. 方案先进性：技术方案先进。"
    # score_items 不因此变多（没有重复追加）
    assert row.score_items == "方案可行性 8-12分\n方案先进性 12-18分"


def test_keeps_correspond_lines_that_score_items_lacks():
    """score_items 里没有的分值 → 仍按老行为补进去，不丢信息。"""
    row = _row(requirement="投标方应提供方案。\n【评分对应】技术评分标准 10分", score_items="")
    res = normalize_row_fields(row)

    assert res["dropped"] == []
    assert res["moved"] == ["【评分对应】技术评分标准 10分"]
    assert row.score_items == "【评分对应】技术评分标准 10分"
    assert row.requirement == "投标方应提供方案。"


def test_prunes_indicator_lines_that_restate_score_bands():
    """indicators 复述评分档位 → 删（数字全在 score_items 里才算复述）。

    实测项目 18：132 行 indicators 里 46 行是这类复述（"…总分：30分"/"技术评分标准分值：10分"），
    在界面上表现为「评分项」卡与「量化指标」卡上下重复。
    真正的量化指标（"不少于35人"）没有档位/总分/评分字样，不会被误删。
    """
    row = _row(requirement="投标方应提供方案。",
               score_items="方案可行性 8-12分\n方案可行性+方案先进性 30分",
               indicators=("方案可行性得分档位：8-12分、0分（没有内容不得分）\n"
                           "方案可行性+方案先进性总分：30分\n"
                           "不少于35人\n"
                           "交付周期90天"))
    res = normalize_row_fields(row)

    assert res["pruned_indicators"] == ["方案可行性得分档位：8-12分、0分（没有内容不得分）",
                                        "方案可行性+方案先进性总分：30分"]
    assert row.indicators == "不少于35人\n交付周期90天"
