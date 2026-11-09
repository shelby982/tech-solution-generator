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

import json
import logging

logger = logging.getLogger(__name__)

# 满足度 → 得分系数（None = 未评审，不计入预估，单列"待评"）
SCORE_RATIO = {"ok": 1.0, "medium": 0.5, "high": 0.0, "unreviewed": None}
VERDICT_LABEL = {"ok": "满足", "medium": "部分满足", "high": "不满足", "unreviewed": "未评审"}

# 否决/废标类关键词（fatal_risks 启发式；宁多勿漏，UI 标注"仅供参考"）
FATAL_KEYWORDS = (
    "废标", "否决", "无效投标", "实质性响应", "实质性条款", "★", "▲",
    "资格条件", "资格审查", "不满足招标文件", "未响应", "拒收",
    # 承诺位置留占位符 = 实质性响应缺失，评审实际会判废标
    "待补充", "待明确", "待提供",
)


def estimate_point(points: float, chapters: list[dict]) -> dict:
    """评分点预估（加权覆盖）：已评章节系数平均 × 分值，未评不计入分母。

    chapters: [{"risk": ok|medium|high|unreviewed}, ...]
    """
    from services.scoring_risk import RISK_ORDER  # noqa: F401  （保持口径一致）

    reviewed = [c for c in (chapters or []) if str(c.get("risk") or "unreviewed") != "unreviewed"]
    total = len(chapters or [])
    if not reviewed:
        return {"estimated_score": None,
                "basis": {"reviewed": 0, "total": total, "ratio": 0.0, "avg": None}}
    ratios = [SCORE_RATIO.get(str(c.get("risk") or ""), 0.0) or 0.0 for c in reviewed]
    avg = round(sum(ratios) / len(ratios), 4)
    return {
        "estimated_score": round(float(points or 0) * avg, 2),
        "basis": {"reviewed": len(reviewed), "total": total,
                  "ratio": round(len(reviewed) / total, 4) if total else 0.0, "avg": avg},
    }


# 评审自身/输入侧的元问题（不是投标风险，别当成废标）
# 只认"评审环节缺输入"这一种元问题；不要把"未响应应标要求"这类真风险误过滤
META_PATTERNS = ("评审输入缺失", "输入缺失", "无法开展", "无法评审", "无法进行评审",
                 "未配置", "没有评分点", "缺少评分口径",
                 "未提供应标要求", "无法逐条核验", "无法核验")


def collect_fatal_risks(issues: list[dict], limit: int = 20) -> list[dict]:
    """从 issues 里挑出否决/废标风险（severity=critical + 关键词，或明确指向资格否决）。"""
    risks: list[dict] = []
    seen: set[str] = set()
    for issue in issues or []:
        text = str(issue.get("point") or "") + " " + str(issue.get("suggestion") or "")
        severity = str(issue.get("severity") or "").lower()
        is_meta = any(pattern in text for pattern in META_PATTERNS)
        if is_meta:
            continue                                  # 评审输入元问题不算废标风险
        hit_keyword = any(keyword in text for keyword in FATAL_KEYWORDS)
        if not hit_keyword:
            continue                                  # 无否决关键词 → 不判废标（由 risk_level 的 yellow 兜）
        key = f"{issue.get('block_id')}|{str(issue.get('point'))[:40]}"
        if key in seen:
            continue
        seen.add(key)
        risks.append({
            "block_id": str(issue.get("block_id") or ""),
            "chapter": str(issue.get("chapter") or ""),
            "point": str(issue.get("point") or "")[:200],
            "severity": severity or "critical",
            "source": "review",
            "keyword_hit": hit_keyword,
        })
        if len(risks) >= limit:
            break
    return risks


def build_risk_level(fatal_risks: list[dict], issues: list[dict]) -> str:
    """三色风险：red（有否决风险）/ yellow（有 high 或覆盖不足）/ green。"""
    if fatal_risks:
        return "red"
    if any(str(i.get("severity") or "").lower() in ("critical", "high") for i in issues or []):
        return "yellow"
    return "green"


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
    all_review_issues: list[dict] = []
    chapter_titles = {str(b.get("block_id")): str(b.get("title") or "") for b in blocks}
    for p in base.get("points") or []:
        pts = float(p.get("points") or 0)
        verdict = p.get("risk") or "unreviewed"
        chapters = p.get("chapters") or []
        ev_chapters = [c for c in chapters if need_evidence.get(str(c.get("block_id")))]
        # A：加权覆盖（已评章节系数平均 × 分值；未评不计入分母）
        weighted = estimate_point(pts, chapters)
        for chapter in chapters:
            for raw in (chapter.get("missing_points") or []):
                all_review_issues.append({"severity": "high", "point": str(raw),
                                          "block_id": str(chapter.get("block_id") or "")})
        points.append({
            "point_id": p.get("point_id"),
            "factor": p.get("factor") or "",
            "item": p.get("item") or "",
            "points": pts,
            "criterion_text": p.get("criterion_text") or "",
            "verdict": verdict,
            "verdict_label": VERDICT_LABEL.get(verdict, verdict),
            "estimated_score": weighted["estimated_score"],
            "estimated_basis": weighted["basis"],
            "chapters_total": p.get("chapters_total") or 0,
            "chapters_reviewed": p.get("chapters_reviewed") or 0,
            "missing_points": p.get("missing_points") or [],
            "evidence_chapters": len(ev_chapters),
            "evidence_tracked": False,     # 引用素材尚未落库（如实标注）
            "chapters": chapters,
        })

    # B：否决风险（从 reviews 最近一轮 issues 里挑）
    recent_issues: list[dict] = []
    try:
        latest_rows = {}
        chapter_rows: list[dict] = []
        if thread_id:
            chapter_rows = [dict(r) for r in await (await db.execute(
                """SELECT b.block_id AS block_id, cr.issues AS issues,
                          cr.content_version AS content_version,
                          cr.evidence_version AS evidence_version
                   FROM chapter_reviews cr JOIN blocks b ON b.id=cr.block_id
                   WHERE b.project_id=? AND b.run_thread_id=?""",
                (project_id, thread_id))).fetchall()]
        else:
            chapter_rows = [dict(r) for r in await (await db.execute(
                """SELECT b.block_id AS block_id, cr.issues AS issues,
                          cr.content_version AS content_version,
                          cr.evidence_version AS evidence_version
                   FROM chapter_reviews cr JOIN blocks b ON b.id=cr.block_id
                   WHERE b.project_id=?""", (project_id,))).fetchall()]
        if chapter_rows:
            # 复用 scoring_risk 已经算好的 freshness，避免每章再查一次 block
            # 和一次 evidence_hash（100+ 章时是明显的 N+1）。
            freshness = base.get("chapter_freshness") or {}
            for row in chapter_rows:
                code = str(row["block_id"])
                if freshness.get(code) != "current":
                    continue
                latest_rows[code] = row["issues"]
        else:
            # 迁移前/老测试回退：仍从 reviews 明细取
            if thread_id:
                rows = await (await db.execute(
                    "SELECT block_id, issues FROM reviews WHERE thread_id=? ORDER BY id", (thread_id,))).fetchall()
            else:
                rows = await (await db.execute("SELECT block_id, issues FROM reviews ORDER BY id")).fetchall()
            for row in rows:
                latest_rows[str(row[0])] = row[1]          # 每章取最后一条
        for block_id, raw in latest_rows.items():
            try:
                items = json.loads(raw or "[]") or []
            except Exception:
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                item["block_id"] = block_id
                item["chapter"] = chapter_titles.get(block_id, "")
                recent_issues.append(item)
    except Exception as e:
        logger.warning(f"scoring_verdict：读取评审 issues 失败（{e}）")
    fatal_risks = collect_fatal_risks(recent_issues)
    risk_level = build_risk_level(fatal_risks, recent_issues)

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
        "risk_level": risk_level,
        "fatal_risk_count": len(fatal_risks),
        "issues_total": len(recent_issues),
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
    return {"points": points, "summary": summary, "fatal_risks": fatal_risks,
            "unlinked_chapters": base.get("unlinked_chapters") or []}


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
