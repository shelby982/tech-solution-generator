"""评分点对齐：让"库里落库的评分点"与"解析出来的评分点事实"一致。

背景（本项目的真实事故）：
  界面第①步显示的是**解析事实**（商务 8 + 技术 8），而落库的 `review_points`
  只有 **24 条、全是 tech**（那次运行的 `selected_categories` 只有 tech）。
  于是"评分点拆分完对不上" —— 管线用的是库，界面用的是解析结果，两处真相。

本模块做两件事：
  · :func:`plan_point_sync` —— **纯函数**：算出差异（应有 / 已落 / 缺 / 多 / 分类别计数）
  · :func:`sync_points_from_parse` —— 把"应有的那批"按**与拆分同一套过滤口径**落库

过滤口径必须与 `zhang_heng` 的拆分一致（含**宽松回退**）：
  按 (标包, 类别) 过滤后若为空、而"该类别下有点"，就放宽标包（因为材料里常常没有标包编号）。
  否则"对齐"会算出一堆假差异。
"""
from __future__ import annotations

from typing import Iterable


def _cat(p) -> str:
    return str(getattr(p, "category", "") or "")


def _pkg(p) -> str:
    return str(getattr(p, "package_no", "") or "")


def select_points_for_scope(parsed_points: Iterable, *, package_no: str = "",
                             categories: Iterable[str] = ()) -> tuple[list, str]:
    """按 (标包, 类别) 从解析事实里选出"本次该落库的评分点"。

    返回 ``(points, note)``；``note`` 非空时说明放宽了什么（给界面如实展示）。
    口径与 `zhang_heng` 的拆分保持一致 —— 那里也是"过滤为空就放宽标包"。
    """
    pts = list(parsed_points or [])
    cats = [str(c) for c in (categories or []) if c]
    pkg = str(package_no or "")

    def by_cat(items):
        return [p for p in items if not cats or _cat(p) in cats]

    picked = [p for p in by_cat(pts) if not pkg or _pkg(p) == pkg]
    note = ""
    if not picked and pts:
        relaxed = by_cat(pts)
        if relaxed:
            picked = relaxed
            note = (f"材料里的评分点没有标包编号（无法对应「{pkg}」），"
                    f"已按「{'、'.join(cats) or '全部'}」类的全部评分点对齐")
        else:
            picked = list(pts)
            note = "材料里没有可识别的评分类别，已按全部评分点对齐"
    return picked, note


def plan_point_sync(parsed_points: Iterable, persisted_rows: Iterable, *,
                    package_no: str = "", categories: Iterable[str] = ()) -> dict:
    """算出"解析事实"与"已落库"的差异（**纯函数**，不碰库）。

    返回::

        {
          "expected": 12, "persisted": 24, "missing": [...], "extra": [...],
          "aligned": False,
          "by_category": {"tech": {"expected": 8, "persisted": 24}, ...},
          "note": "…"
        }

    ``missing`` = 解析有、库里没有（**拆分漏了**，界面该提示"重新拆分/一键对齐"）；
    ``extra``   = 库里有、解析没有（多为上一次范围的残留，或类别选择变过）。
    """
    from services.point_store import stable_review_point_id

    picked, note = select_points_for_scope(
        parsed_points, package_no=package_no, categories=categories)
    exp_ids = [stable_review_point_id(p) for p in picked]
    exp_set = set(exp_ids)
    rows = list(persisted_rows or [])
    got_set = {str(r.get("point_id") or "") for r in rows}

    by_cat: dict[str, dict] = {}
    for p in picked:
        c = _cat(p) or "未分类"
        by_cat.setdefault(c, {"expected": 0, "persisted": 0})["expected"] += 1
    for r in rows:
        c = str(r.get("category") or "") or "未分类"
        by_cat.setdefault(c, {"expected": 0, "persisted": 0})["persisted"] += 1

    return {
        "expected": len(exp_ids),
        "persisted": len(got_set - {""}),
        "missing": sorted(exp_set - got_set),
        "extra": sorted(got_set - exp_set - {""}),
        "aligned": exp_set == (got_set - {""}),
        "by_category": by_cat,
        "note": note,
    }


async def sync_points_from_parse(db, project_id: int, thread_id: str,
                                 parsed_points: Iterable, *,
                                 package_no: str = "",
                                 categories: Iterable[str] = ()) -> dict:
    """把"该落库的那批评分点"写进 ``review_points``（**不调用模型**）。

    解析事实已经在 state 里，所以"对齐"不该再花一次 LLM 调用 —— 直接按
    与拆分相同的过滤口径落库即可。返回 ``{"written": n, "plan": {...}}``。
    """
    from services.point_store import list_review_points, replace_review_points

    picked, note = select_points_for_scope(
        parsed_points, package_no=package_no, categories=categories)
    before = [dict(r) for r in await list_review_points(db, run_thread_id=thread_id)]
    plan_before = plan_point_sync(parsed_points, before,
                                  package_no=package_no, categories=categories)
    if picked:
        await replace_review_points(db, project_id, thread_id, picked)
    after = [dict(r) for r in await list_review_points(db, run_thread_id=thread_id)]
    plan_after = plan_point_sync(parsed_points, after,
                                 package_no=package_no, categories=categories)
    return {
        "written": len(picked),
        "note": note or plan_before.get("note", ""),
        "before": plan_before,
        "after": plan_after,
    }
