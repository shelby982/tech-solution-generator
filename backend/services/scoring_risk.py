"""得分风险：把"逐章审查结论"按**评分点**聚合起来。

用户确定的核心数据关系里，最后一环是「审查结果解决**是否真正满足要求**」。
对齐评分口径之后（`reviews.satisfied` / `missing_points`），单个章节的结论还不够 ——
要回答的是决策问题：

    这 8 个技术评分点里，哪些的应答还是不达标的？分别缺什么？

本模块把 reviews 按「章节 → 评分点」（`blocks.review_point_ids`）卷起来，
给出每个评分点的风险等级与缺口清单。这样覆盖矩阵就从"有没有素材"升级成
"**得分风险**"。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# 风险等级（可解释、确定性，不引入模型判断）。
# 聚合时取"最差"的一章：high > medium > **unreviewed** > ok。
# unreviewed 必须排在 ok 之前 —— 没审过不等于达标（实测：初始化 worst="ok" 时
# "未审查"永远赢不了，评分点会被误报成"正常"）。
RISK_ORDER = {"ok": 0, "unreviewed": 1, "medium": 2, "high": 3}
RISK_LABEL = {"unreviewed": "未审查", "ok": "正常", "medium": "有缺口", "high": "不满足"}


def _load_ids(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _chapter_risk(finding: dict | None) -> tuple[str, list[str]]:
    """单章的得分风险：优先看审查给的满足度，其次看缺口清单。"""
    if not finding:
        return "unreviewed", []
    missing = _load_ids(finding.get("missing_points"))
    satisfied = str(finding.get("satisfied") or "").strip().lower()
    if satisfied == "unsatisfied":
        return "high", missing
    if satisfied == "partial":
        return "medium", missing
    if satisfied == "satisfied":
        return ("medium" if missing else "ok"), missing
    # 老数据（没有 satisfied）只能靠缺口清单判断
    return ("medium" if missing else "ok"), missing


async def summarize_scoring_risk(db, project_id: int, thread_id: str | None) -> dict:
    """返回 ``{"points": [...], "summary": {...}}``，按风险从高到低排序。"""
    from services.block_store import list_blocks

    blocks = [b for b in await list_blocks(db, project_id, thread_id) if b.get("block_id")]
    if thread_id:
        cur = await db.execute(
            "SELECT * FROM reviews WHERE thread_id = ? ORDER BY id", (thread_id,))
    else:
        cur = await db.execute("SELECT * FROM reviews ORDER BY id")
    reviews = [dict(r) for r in await cur.fetchall()]

    # 每个 block 取"最后一条"审查（同章可能被多轮 / 多 agent 评过）
    latest: dict[str, dict] = {}
    for r in reviews:
        latest[str(r.get("block_id") or "")] = r

    cur = await db.execute(
        "SELECT * FROM review_points WHERE project_id = ? AND (run_thread_id = ? OR ? IS NULL) "
        "ORDER BY id", (project_id, thread_id, thread_id))
    points = [dict(r) for r in await cur.fetchall()]

    by_point: dict[str, dict] = {}
    for p in points:
        by_point[p["point_id"]] = {
            "point_id": p["point_id"],
            "factor": p.get("factor") or "",
            "item": p.get("item") or "",
            "points": p.get("points"),
            "criterion_text": (p.get("criterion") or "")[:400],
            "chapters": [],
        }

    # 没有评分点数据时（老项目）：也要给出按章节的风险，不让功能不可用
    unlinked: list[dict] = []

    for b in blocks:
        bid = str(b.get("block_id"))
        risk, missing = _chapter_risk(latest.get(bid))
        entry = {
            "block_id": bid,
            "title": b.get("title") or "",
            "level": b.get("level"),
            "score_weight": b.get("score_weight"),
            "risk": risk,
            "missing_points": missing,
            "satisfied": (latest.get(bid) or {}).get("satisfied") or "",
        }
        ids = _load_ids(b.get("review_point_ids"))
        if not ids:
            unlinked.append(entry)
            continue
        for pid in ids:
            bucket = by_point.setdefault(pid, {
                "point_id": pid, "factor": "", "item": "", "points": None,
                "criterion_text": "", "chapters": [],
            })
            bucket["chapters"].append(entry)

    out_points = []
    for bucket in by_point.values():
        chapters = bucket["chapters"]
        # 评分点风险 = 它名下章节里最差的那个（有一章不满足，这一项就悬）
        worst = "ok"
        missing_all: list[str] = []
        for c in chapters:
            if RISK_ORDER[c["risk"]] > RISK_ORDER[worst]:
                worst = c["risk"]
            for m in c["missing_points"]:
                if m not in missing_all:
                    missing_all.append(m)
        if not chapters:
            worst = "unreviewed"
        reviewed = sum(1 for c in chapters if c["risk"] != "unreviewed")
        out_points.append({
            **{k: v for k, v in bucket.items() if k != "chapters"},
            "risk": worst,
            "risk_label": RISK_LABEL.get(worst, worst),
            "chapters_total": len(chapters),
            "chapters_reviewed": reviewed,
            "missing_points": missing_all[:12],
            "chapters": chapters,
        })

    out_points.sort(key=lambda x: (-RISK_ORDER.get(x["risk"], 0), -(x.get("points") or 0)))

    summary = {
        "points_total": len(out_points),
        "high": sum(1 for p in out_points if p["risk"] == "high"),
        "medium": sum(1 for p in out_points if p["risk"] == "medium"),
        "unreviewed": sum(1 for p in out_points if p["risk"] == "unreviewed"),
        "ok": sum(1 for p in out_points if p["risk"] == "ok"),
        "chapters_without_point": len(unlinked),
    }
    return {"points": out_points, "unlinked_chapters": unlinked, "summary": summary}
