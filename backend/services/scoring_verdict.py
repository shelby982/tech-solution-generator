"""评分点级评审结论（verdict）—— 评审页的主视图数据源。

为什么需要它
------------
得分是**按评分点**给的（一条评审项 5 分 / 12 分 / 30 分），但评审结果目前是**按章节**存在
``reviews`` 里的（block_id + criterion_text/satisfied/covered_points/missing_points）。
于是报告只能回答"这一章写得怎么样"，回答不了"**这个评分点现在能拿几分、差在哪**"。

本模块把 ``summarize_scoring_risk`` 的评分点聚合结果再往上抬一层，给出：

- **verdict**：该评分点的满足度（满足 / 部分满足 / 不满足 / 未评审）——以它名下**最差**的
  章节为准（有一章不满足，这一项就悬）；
- **estimated_score**：预计可得分数（满足=满分；部分满足=半分；不满足=0；未评审=不计入）；
- **gaps**：缺失的得分要点（来自评审的 missing_points）；
- **evidence**：该评分点下**有举证要求**的章节数（``blocks.evidence_required`` 非空）。
  ⚠️ "是否真的引用了素材"目前**没有落库**（没有 章节↔素材 引用表），所以这里只给"需要举证
  的章节数"，并在响应里用 ``evidence_tracked=False`` 如实标注，不假装有引用数据。
- **summary**：预计得分 / 满分、缺口数、各档计数、本标段模型成本（``llm_usage``）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 满足度 → 得分系数（None = 未评审，不计入预估，单列"待评"）
SCORE_RATIO = {"ok": 1.0, "medium": 0.5, "high": 0.0, "unreviewed": None}
VERDICT_LABEL = {"ok": "满足", "medium": "部分满足", "high": "不满足", "unreviewed": "未评审"}


async def build_scoring_verdict(db, project_id: int, thread_id: str | None) -> dict:
    """评分点级 verdict：评审页的主数据（也是"得分预估/缺口"看板的数据源）。"""
    from services.block_store import list_blocks
    from services.scoring_risk import summarize_scoring_risk

    base = await summarize_scoring_risk(db, project_id, thread_id)

    # 哪些章节有举证要求（evidence_required 非空）—— 用于"举证"列
    blocks = [b for b in await list_blocks(db, project_id, thread_id) if b.get("block_id")]
    need_evidence = {
        str(b.get("block_id")): bool(str(b.get("evidence_required") or "").strip())
        for b in blocks
    }

    points: list[dict] = []
    for p in base.get("points") or []:
        pts = float(p.get("points") or 0)
        verdict = p.get("risk") or "unreviewed"
        ratio = SCORE_RATIO.get(verdict)
        chapters = p.get("chapters") or []
        ev_chapters = [c for c in chapters if need_evidence.get(str(c.get("block_id")))]
        points.append({
            "point_id": p.get("point_id"),
            "factor": p.get("factor") or "",
            "item": p.get("item") or "",
            "points": pts,
            "criterion_text": p.get("criterion_text") or "",
            "verdict": verdict,
            "verdict_label": VERDICT_LABEL.get(verdict, verdict),
            "estimated_score": None if ratio is None else round(pts * ratio, 2),
            "chapters_total": p.get("chapters_total") or 0,
            "chapters_reviewed": p.get("chapters_reviewed") or 0,
            "missing_points": p.get("missing_points") or [],
            "evidence_chapters": len(ev_chapters),
            "evidence_tracked": False,     # 引用素材尚未落库（如实标注）
            "chapters": chapters,
        })

    reviewed = [x for x in points if x["estimated_score"] is not None]
    score_max = round(sum(x["points"] for x in points), 2)
    score_est = round(sum(x["estimated_score"] for x in reviewed), 2)
    score_pending = round(sum(x["points"] for x in points if x["estimated_score"] is None), 2)

    chapters_total = sum(int(x["chapters_total"] or 0) for x in points)
    chapters_reviewed = sum(int(x["chapters_reviewed"] or 0) for x in points)
    unlinked_points = [{"point_id": x["point_id"], "factor": x["factor"], "item": x["item"]}
                       for x in points if not int(x["chapters_total"] or 0)]
    summary = {
        "points_total": len(points),
        "chapters_total": chapters_total,
        "chapters_reviewed": chapters_reviewed,
        "reviewed_ratio": round(chapters_reviewed / chapters_total * 100) if chapters_total else 0,
        "unlinked_points": len(unlinked_points),
        "unlinked_points_detail": unlinked_points[:10],
        "score_max": score_max,
        "score_estimated": score_est,
        "score_pending": score_pending,
        "ok": sum(1 for x in points if x["verdict"] == "ok"),
        "medium": sum(1 for x in points if x["verdict"] == "medium"),
        "high": sum(1 for x in points if x["verdict"] == "high"),
        "unreviewed": sum(1 for x in points if x["verdict"] == "unreviewed"),
        "gaps": sum(len(x["missing_points"]) for x in points),
        "evidence_chapters": sum(x["evidence_chapters"] for x in points),
        "cost_tokens": await _cost_tokens(db, project_id, thread_id),
    }
    return {"points": points, "summary": summary, "unlinked_chapters": base.get("unlinked_chapters") or []}


async def _cost_tokens(db, project_id: int, thread_id: str | None) -> int:
    """本标段累计模型 token（成本可量化，评审页要用）。"""
    try:
        if thread_id:
            cur = await db.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM llm_usage WHERE thread_id = ?",
                (thread_id,))
        else:
            cur = await db.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM llm_usage WHERE project_id = ?",
                (project_id,))
        return int((await cur.fetchone())[0] or 0)
    except Exception as e:      # 老库没有该表：成本只是看板信息，不该拖垮评审页
        logger.warning(f"scoring_verdict：读取模型成本失败（{e}）")
        return 0
