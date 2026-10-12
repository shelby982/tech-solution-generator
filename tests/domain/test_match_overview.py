"""匹配总览聚合（domain/material/overview.py）。

背景：匹配跑完界面只有一个「依据 N」小徽标，用户看不到"哪章缺依据、缺哪几条、
哪份素材没用上"。这个投影就是给这些数字提供**一份**权威算法（后端算，前端只渲染）。
"""

from __future__ import annotations

import json

from domain.material.overview import build_match_overview, evidence_from_chunk


def _blk(bid, title, *, source=None, **fields):
    return {"block_id": bid, "title": title, "level": 1,
            "source": json.dumps(source, ensure_ascii=False) if source is not None else "",
            **fields}


def _ev(chunk_id, *, mid=41, filename="规格书.pdf", item_ids=None, content="原文"):
    return {"chunk_id": chunk_id, "material_id": mid, "content": content,
            "filename": filename, "locator": f"第 {chunk_id} 段", "kind": "fact",
            "source_kind": "ours", "by": "semantic", "confidence": 0.8,
            "item_ids": list(item_ids or ()), "locked": False}


def test_counts_chapters_requirements_and_evidence():
    ov = build_match_overview([
        _blk("s1", "服务方案", source=[_ev(1, item_ids=["s1#R1"]), _ev(2)],
             requirement="支持国产数据库", key_points="给出方案"),
        _blk("s2", "性能", source=[], requirement="并发不低于 2000"),
    ])

    assert ov["chapters"] == {"total": 2, "with_evidence": 1, "without_evidence": 1}
    # 条目：s1 有 R1 + K1，s2 有 R1 → 共 3 条；覆盖 1 条（s1#R1）
    assert {k: ov["requirements"][k] for k in ("total", "covered", "uncovered")} == {
        "total": 3, "covered": 1, "uncovered": 2}
    # 要回应的事项：本例没有评分口径条目 → 与总数一致
    assert ov["requirements"]["actionable_total"] == 3
    assert ov["requirements"]["actionable_covered"] == 1
    assert ov["evidence"] == {"total": 2, "item_level": 1, "chapter_level": 1}


def test_chapter_detail_lists_uncovered_items_for_coverage_check():
    """未覆盖条目 id 是撰写阶段覆盖度自检的输入 —— 必须逐条给出，而不是只给个数。"""
    ov = build_match_overview([
        _blk("s1", "服务方案", source=[_ev(1, item_ids=["s1#R1"])],
             requirement="支持国产数据库", indicators="并发 2000", veto_items="未提供检测报告即无效"),
    ])
    d = ov["chapters_detail"][0]
    assert d["items_total"] == 3
    assert d["items_covered"] == 1
    # indicators（量化指标）是"说明性"条目 → 不进"要回应的事项"，也不进未覆盖清单
    assert d["actionable_items_total"] == 2
    assert d["uncovered_item_ids"] == ["s1#V1"]
    assert d["uncovered_texts"] == ["未提供检测报告即无效"]
    assert d["evidence"] == 1 and d["chapter_level_evidence"] == 0


def test_dirty_item_ids_do_not_fake_coverage():
    """素材挂到不存在的条目 id（历史脏数据）不能算"已覆盖"，否则会虚报覆盖度。"""
    ov = build_match_overview([
        _blk("s1", "服务方案", source=[_ev(1, item_ids=["s1#R9", "s1#R1"])],
             requirement="支持国产数据库"),
    ])
    assert {k: ov["requirements"][k] for k in ("total", "covered", "uncovered")} == {
        "total": 1, "covered": 1, "uncovered": 0}
    assert ov["chapters_detail"][0]["items_covered"] == 1


def test_material_view_groups_by_material_id_across_chapters():
    """素材视角（反向视图）：同一份素材供了哪些章、几条依据、命中多少条目。"""
    ov = build_match_overview([
        _blk("s1", "服务方案", source=[_ev(1, item_ids=["s1#R1"])], requirement="支持国产数据库"),
        _blk("s2", "性能", source=[_ev(2, item_ids=["s2#R1"]), _ev(3, mid=42, filename="架构.pptx")],
             requirement="并发不低于两千用户"),
    ])
    spec = next(m for m in ov["materials"] if m["material_id"] == 41)
    assert spec["evidence"] == 2 and spec["items"] == 2
    assert [c["block_id"] for c in spec["chapters"]] == ["s1", "s2"]
    assert [c["title"] for c in spec["chapters"]] == ["服务方案", "性能"]
    # 排序按依据数倒序：41 有 2 条，42 有 1 条
    assert [m["material_id"] for m in ov["materials"]] == [41, 42]


def test_tolerates_broken_source_and_empty_project():
    assert build_match_overview([])["chapters"]["total"] == 0
    ov = build_match_overview([_blk("s1", "坏数据", source="这不是 JSON"),
                               _blk("s2", "空", source=None)])
    assert ov["evidence"]["total"] == 0
    assert ov["chapters"]["without_evidence"] == 2
    # 没有 item_ids 的旧依据算章节级
    ov2 = build_match_overview([_blk("s1", "老数据", source=[{"chunk_id": 1, "content": "x"}])])
    assert ov2["evidence"] == {"total": 1, "item_level": 0, "chapter_level": 1}


def test_evidence_from_chunk_builds_human_locked_contract():
    ev = evidence_from_chunk(
        {"id": 7, "material_id": 41, "content": "服务团队\n项目经理 PMP 认证",
         "filename": "规格书.pdf", "page": 3, "chunk_index": 8},
        item_ids=["s1#R1"],
    ).to_dict()
    assert ev["chunk_id"] == 7 and ev["material_id"] == 41
    assert ev["locator"].startswith("P3") and "第 9 段" in ev["locator"]
    assert ev["by"] == "human" and ev["locked"] is True   # 人工挂接：重匹配不得覆盖
    assert ev["item_ids"] == ["s1#R1"] and ev["confidence"] == 1.0
    assert ev["kind"] in ("fact", "solution", "background")


def test_scoring_clauses_are_informative_not_actionable():
    """评分口径（优良 7-10 分 / 未作响应不得分 / 满分 10 分）不算"要回应的事项"。

    实测背景（项目 18 的 s1.1）：提炼把整段评分细则塞进 requirement，展开出的 12 条里
    8 条是评分档位 —— 混进同一张清单就是满屏"无依据"，用户完全看不懂。它们进
    `informative`，不参与覆盖度统计、界面上也不给「找素材」。
    """
    ov = build_match_overview([
        _blk("s1", "技术条款响应总体说明", source=[_ev(1, item_ids=["s1#R1"])],
             requirement=("1. 投标方需对招标技术条款作出响应。\n"
                          "优良（7-10分）：响应度较高。\n"
                          "中等（6分）：响应度良好。\n"
                          "未作响应不得分。"),
             key_points="编制《技术条款响应偏离表》。",
             indicators="技术评分标准满分10分\n优良（7-10分）",
             score_items="技术评分标准 10分"),
    ])
    d = ov["chapters_detail"][0]
    # 总条目 8 条，其中 6 条是评分口径（含「中等（6分）」这种单值档位）→ 要回应的只有 2 条
    assert d["items_total"] == 8
    assert d["informative_items_total"] == 6
    assert d["actionable_items_total"] == 2
    assert d["actionable_items_covered"] == 1
    assert ov["requirements"]["actionable_total"] == 2
    assert ov["requirements"]["actionable_covered"] == 1
    # 未覆盖清单里不该出现评分档位（它们永远没有依据，列出来是纯噪声）
    assert d["uncovered_item_ids"] == ["s1#K1"]
    assert d["uncovered_texts"] == ["编制《技术条款响应偏离表》。"]
