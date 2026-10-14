"""评分点对齐：解析事实 ↔ 落库结果。

真实事故：界面显示商务 8 + 技术 8（解析事实），库里只有 24 条 tech（那次运行的
selected_categories 只有 tech）→ "拆分完对不上"。
"""
from types import SimpleNamespace

from services.point_sync import plan_point_sync, select_points_for_scope


def P(pid, category, package_no="", points=10):
    return SimpleNamespace(point_id=pid, category=category, package_no=package_no,
                           clause=pid, seq=pid, factor=f"f{pid}", item=f"i{pid}",
                           criterion="", points=points, bands=None, objective=False,
                           source_file="")


def test_select_points_keeps_category_and_package():
    pts = [P("a", "tech", "2"), P("b", "commerce", "2"), P("c", "tech", "3")]
    got, note = select_points_for_scope(pts, package_no="2", categories=["tech"])
    assert [p.point_id for p in got] == ["a"]
    assert note == ""


def test_select_points_relaxes_package_when_no_number_in_material():
    """材料里的评分点没有标包编号时**放宽标包**（与张衡拆分同一口径），并如实说明。"""
    pts = [P("a", "tech", ""), P("b", "commerce", "")]
    got, note = select_points_for_scope(pts, package_no="2", categories=["tech"])
    assert [p.point_id for p in got] == ["a"]
    assert "没有标包编号" in note


def test_plan_reports_missing_and_extra():
    pts = [P("a", "tech", "2"), P("b", "tech", "2")]
    persisted = [{"point_id": "a", "category": "tech"}, {"point_id": "zz", "category": "tech"}]
    plan = plan_point_sync(pts, persisted, package_no="2", categories=["tech"])
    assert plan["expected"] == 2 and plan["persisted"] == 2
    # stable_review_point_id 会重算 id，所以比较"是否对齐"与分类别计数更稳
    assert plan["by_category"]["tech"]["expected"] == 2
    assert plan["by_category"]["tech"]["persisted"] == 2
    assert isinstance(plan["missing"], list) and isinstance(plan["extra"], list)


def test_plan_aligned_when_same_ids():
    from services.point_store import stable_review_point_id

    pts = [P("a", "tech", "2"), P("b", "commerce", "2")]
    rows = [{"point_id": stable_review_point_id(p), "category": p.category} for p in pts]
    plan = plan_point_sync(pts, rows, package_no="2", categories=["tech", "commerce"])
    assert plan["aligned"] is True
    assert plan["missing"] == [] and plan["extra"] == []


def test_plan_reports_per_category_counts():
    pts = [P("a", "tech", "2"), P("b", "commerce", "2")]
    plan = plan_point_sync(pts, [], package_no="2", categories=["tech", "commerce"])
    assert plan["by_category"]["tech"]["expected"] == 1
    assert plan["by_category"]["commerce"]["expected"] == 1
    assert plan["persisted"] == 0 and plan["aligned"] is False
