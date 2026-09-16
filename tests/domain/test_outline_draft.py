"""domain.spec.outline_draft 纯函数测试 — 模型输出规整与 Section id 分配。

覆盖边界：level 越级、孤儿 parent、乱序、深层嵌套、同级重名、
超上限截断、截断前缀自洽、空输入。
"""

from __future__ import annotations

from domain.spec.outline_draft import (
    MAX_LEVEL,
    build_sections,
    count_top_level,
    normalize_nodes,
)


def _ids(sections):
    return [s.id for s in sections]


def _shape(sections):
    return [(s.id, s.level, s.title) for s in sections]


# ─────────────────────────────────────────────
# normalize_nodes
# ─────────────────────────────────────────────

def test_normalize_accepts_well_formed_tree():
    nodes, warnings = normalize_nodes([
        {"level": 1, "title": "技术方案"},
        {"level": 2, "parent": 0, "title": "总体架构"},
        {"level": 3, "parent": 1, "title": "数据层"},
    ])
    assert [(n.level, n.parent, n.title) for n in nodes] == [
        (1, None, "技术方案"),
        (2, 0, "总体架构"),
        (3, 1, "数据层"),
    ]
    assert warnings == []


def test_normalize_strips_title_and_drops_empty():
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "  技术方案  "},
        {"level": 1, "title": "   "},
        {"level": 1, "title": ""},
        {"level": 1},
    ])
    assert [n.title for n in nodes] == ["技术方案"]


def test_normalize_clamps_level_into_range():
    """level 先夹到 1..MAX_LEVEL，再由文档顺序决定实际挂载层级。

    第二个节点 raw_level=9 夹到 4，但前面只有个 level-1 的前驱，
    按「最近更浅前驱 +1」规则落到 2 —— 文档顺序是层级的最终权威。
    """
    nodes, _ = normalize_nodes([
        {"level": 0, "title": "零级"},
        {"level": 9, "title": "九级"},
    ])
    assert [n.level for n in nodes] == [1, 2]
    assert all(1 <= n.level <= MAX_LEVEL for n in nodes)


def test_normalize_rehangs_orphan_level_two_without_parent():
    """level=2 却没有一级前驱 → 降为一级（找不到更浅的前驱）。"""
    nodes, warnings = normalize_nodes([
        {"level": 2, "title": "孤儿二级"},
    ])
    assert [(n.level, n.parent) for n in nodes] == [(1, None)]
    # raw_level=2 但落到一级，不计入「重挂」（rehung 只统计挂到非 None 父节点的情况）
    assert warnings == []


def test_normalize_rehangs_when_parent_index_is_forward_reference():
    """parent 指向更晚的节点（前向引用）不成立，回退到文档顺序。"""
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "parent": 5, "title": "B"},
    ])
    assert [(n.level, n.parent) for n in nodes] == [(1, None), (2, 0)]


def test_normalize_rehangs_when_level_is_not_parent_plus_one():
    """parent 合法但 level 不自洽（差 2 级）→ 回退到最近更浅前驱。"""
    nodes, warnings = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 3, "parent": 0, "title": "B"},
    ])
    # 最近一个 level<3 的前驱是 A(level=1) → B 挂 A 下，level=2
    assert [(n.level, n.parent) for n in nodes] == [(1, None), (2, 0)]
    assert any("重挂" in w for w in warnings)


def test_normalize_interleaved_siblings_follow_document_order():
    """乱序 level：两个二级分别归属各自最近的一级前驱。"""
    sections = build_sections(normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "title": "A1"},
        {"level": 1, "title": "B"},
        {"level": 2, "title": "B1"},
    ])[0])
    assert _shape(sections) == [
        ("s1", 1, "A"), ("s1.1", 2, "A1"),
        ("s2", 1, "B"), ("s2.1", 2, "B1"),
    ]


def test_normalize_never_exceeds_max_level():
    """嵌套再深也不能超过 MAX_LEVEL —— 第 5 层重挂回第 4 层。"""
    raw = [{"level": 1, "title": "L1"}]
    for lv in range(2, 8):
        raw.append({"level": lv, "title": f"L{lv}"})
    nodes, _ = normalize_nodes(raw)
    assert max(n.level for n in nodes) == MAX_LEVEL
    # 层级仍严格 parent.level + 1
    for n in nodes:
        if n.parent is not None:
            assert n.level == nodes[n.parent].level + 1


def test_normalize_drops_duplicate_sibling_titles():
    nodes, warnings = normalize_nodes([
        {"level": 1, "title": "技术方案"},
        {"level": 1, "title": "技术方案"},
    ])
    assert [n.title for n in nodes] == ["技术方案"]
    assert any("重名" in w for w in warnings)


def test_normalize_keeps_same_title_under_different_parents():
    """同名但不同父节点不算重复 —— 「概述」挂在两个不同章节下是合法的。"""
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "title": "概述"},
        {"level": 1, "title": "B"},
        {"level": 2, "title": "概述"},
    ])
    assert [n.title for n in nodes] == ["A", "概述", "B", "概述"]


def test_normalize_truncates_over_max_nodes_and_prefix_stays_consistent():
    raw = [{"level": 1, "title": f"章{i}"} for i in range(10)]
    nodes, warnings = normalize_nodes(raw, max_nodes=4)
    assert len(nodes) == 4
    assert any("上限" in w for w in warnings)
    # 前缀截断后 parent 仍指向更早的节点
    for idx, n in enumerate(nodes):
        if n.parent is not None:
            assert n.parent < idx


def test_normalize_empty_and_garbage_input():
    assert normalize_nodes([])[0] == []
    assert normalize_nodes(None)[0] == []
    assert normalize_nodes(["not a dict", 42, None])[0] == []


# ─────────────────────────────────────────────
# build_sections
# ─────────────────────────────────────────────

def test_build_sections_assigns_hierarchical_ids():
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "title": "A1"},
        {"level": 3, "title": "A1a"},
        {"level": 3, "title": "A1b"},
        {"level": 2, "title": "A2"},
        {"level": 1, "title": "B"},
        {"level": 2, "title": "B1"},
    ])
    sections = build_sections(nodes)
    assert _ids(sections) == [
        "s1", "s1.1", "s1.1.1", "s1.1.2", "s1.2", "s2", "s2.1",
    ]


def test_build_sections_resets_deeper_counters_on_new_branch():
    """换一级章节后，二级/三级计数必须重置，不能续着上一章往下排。"""
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "title": "A1"},
        {"level": 3, "title": "A1a"},
        {"level": 1, "title": "B"},
        {"level": 2, "title": "B1"},
        {"level": 3, "title": "B1a"},
    ])
    assert _ids(build_sections(nodes)) == [
        "s1", "s1.1", "s1.1.1", "s2", "s2.1", "s2.1.1",
    ]


def test_build_sections_leaves_content_empty_for_grounding():
    sections = build_sections(normalize_nodes([{"level": 1, "title": "A"}])[0])
    assert sections[0].raw_content == ""
    assert sections[0].special_marks == []


def test_build_sections_empty():
    assert build_sections([]) == []


def test_count_top_level():
    nodes, _ = normalize_nodes([
        {"level": 1, "title": "A"},
        {"level": 2, "title": "A1"},
        {"level": 1, "title": "B"},
    ])
    assert count_top_level(build_sections(nodes)) == 2
    assert count_top_level([]) == 0
