"""目录结构编辑的校验与层级自洽化（纯函数）。

背景：闸门 1 允许用户增删章节、改标题、上下移动、缩进/取消缩进，前端按
「有序列表 + level」推导树形。保存时如果不做自洽化，会出现「存了 level 3 但树上
挂在顶层」这类表里不一的目录 —— 用户下次刷新看到的结构会和自己刚摆的不一样。
"""

import pytest

from domain.spec.structure import (
    MAX_NODES,
    StructureError,
    clamp_levels,
    subtree_range,
    validate_structure,
)


def _node(bid, level=1, title=None):
    return {"block_id": bid, "level": level, "title": title or f"章节-{bid}"}


# ─────────────────────────────────────────────
# 校验
# ─────────────────────────────────────────────

def test_valid_structure_passes_through():
    nodes = [_node("s1", 1), _node("s2", 2), _node("s3", 1)]
    clean = validate_structure(nodes)
    assert [n["block_id"] for n in clean] == ["s1", "s2", "s3"]
    assert [n["level"] for n in clean] == [1, 2, 1]


def test_empty_structure_rejected():
    with pytest.raises(StructureError, match="不能为空"):
        validate_structure([])
    with pytest.raises(StructureError, match="不能为空"):
        validate_structure(None)


def test_duplicate_id_rejected():
    with pytest.raises(StructureError, match="id 重复"):
        validate_structure([_node("s1"), _node("s1", 2)])


def test_blank_title_rejected():
    with pytest.raises(StructureError, match="标题不能为空"):
        validate_structure([{"block_id": "s1", "level": 1, "title": "   "}])


def test_missing_block_id_rejected():
    with pytest.raises(StructureError, match="缺少 block_id"):
        validate_structure([{"level": 1, "title": "x"}])


def test_too_many_nodes_rejected():
    nodes = [_node(f"n{i}") for i in range(MAX_NODES + 1)]
    with pytest.raises(StructureError, match="节点过多"):
        validate_structure(nodes)


def test_title_is_stripped():
    clean = validate_structure([{"block_id": "s1", "level": 1, "title": "  技术方案  "}])
    assert clean[0]["title"] == "技术方案"


# ─────────────────────────────────────────────
# 层级自洽化
# ─────────────────────────────────────────────

def test_first_node_is_forced_to_top_level():
    """首节点给 level 3：树上它本来就是顶层，存 3 会表里不一。"""
    assert [n["level"] for n in clamp_levels([_node("a", 3)])] == [1]


def test_level_jump_is_flattened_to_one_step():
    """level 1 之后直接 level 4 → 树上挂在 level 1 下，应存成 2。"""
    clean = clamp_levels([_node("a", 1), _node("b", 4)])
    assert [n["level"] for n in clean] == [1, 2]


def test_level_clamped_into_range():
    clean = clamp_levels([_node("a", 1), _node("b", 0), _node("c", 9)])
    assert [n["level"] for n in clean] == [1, 1, 2]


def test_normal_nesting_untouched():
    nodes = [_node("a", 1), _node("b", 2), _node("c", 3), _node("d", 1)]
    assert [n["level"] for n in clamp_levels(nodes)] == [1, 2, 3, 1]


def test_clamp_keeps_other_fields():
    clean = clamp_levels([{"block_id": "a", "level": 4, "title": "T", "extra": 1}])
    assert clean[0]["title"] == "T"
    assert clean[0]["extra"] == 1


# ─────────────────────────────────────────────
# 子树范围（删除/移动都以子树为单位）
# ─────────────────────────────────────────────

def test_subtree_range_covers_children_only():
    nodes = [_node("a", 1), _node("a1", 2), _node("a1x", 3), _node("b", 1), _node("b1", 2)]
    assert subtree_range(nodes, "a") == (0, 3)
    assert subtree_range(nodes, "a1") == (1, 3)
    assert subtree_range(nodes, "b") == (3, 5)


def test_subtree_range_leaf_is_single_slot():
    nodes = [_node("a", 1), _node("a1", 2)]
    assert subtree_range(nodes, "a1") == (1, 2)


def test_subtree_range_missing_id():
    assert subtree_range([_node("a", 1)], "nope") == (-1, -1)


def test_outline_matrix_consistency_flags_only_stale():
    """**判据是"没有陈旧键"，不是"两边相等"**。

    陈旧（矩阵里有、目录里没有）危险：下游 generate 的"toc 之外兜底"会照着它
    把用户删掉的章节又生成回来。
    缺失（目录里有、矩阵里没有）正常：用户手写的章节本来就没有派生行。
    """
    from domain.spec.structure import outline_matrix_consistency

    # 目录 3 项，矩阵只有其中 2 项 + 1 个已删章节 → 陈旧 1 个，缺失 1 个（正常）
    r = outline_matrix_consistency(["s1", "s2", "s3"], {"s1": {}, "s2": {}, "s9": {}})
    assert r["stale"] == ["s9"]
    assert r["missing"] == ["s3"]

    # 有陈旧 → 必须裁；裁完 stale 为空即视为一致
    assert outline_matrix_consistency(["s1", "s2", "s3"], {"s1": {}, "s2": {}})["stale"] == []

    # 空矩阵：没有任何陈旧键 → 一致（只是下游拿不到派生信息，属于降级不报错）
    r2 = outline_matrix_consistency(["s1", "s2"], {})
    assert r2["stale"] == []
    assert r2["missing"] == ["s1", "s2"]

    # None 也当空处理
    assert outline_matrix_consistency(["s1"], None)["stale"] == []
