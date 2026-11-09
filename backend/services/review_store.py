"""评审统一落库：一章一个当前态 + 轮次历史 + per-agent 明细。

产品口径（2026-11-09）：
  · 编写时章节评审 → stage='writing'
  · 终评（整篇）     → stage='final'
  · 终评权威；编写时评审保留在 history / reviews 明细，不覆盖
  · 终评复用"版本已对齐"的章评审，只重评未评审 / 内容或证据已改的章
"""
from __future__ import annotations

import json


def _json(value) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _issue_dicts(finding) -> list[dict]:
    if finding is None:
        return []
    issues = getattr(finding, "issues", None)
    if issues is None and isinstance(finding, dict):
        issues = finding.get("issues")
    out = []
    for issue in issues or []:
        if hasattr(issue, "to_dict"):
            out.append(issue.to_dict())
        elif isinstance(issue, dict):
            out.append(dict(issue))
    return out


def _get(finding, key, default=None):
    if isinstance(finding, dict):
        return finding.get(key, default)
    return getattr(finding, key, default)


def combine_findings(tech, comp) -> dict:
    """把技术/合规两侧 finding 合成章级结果（与 chapter_review._combine 同口径）。"""
    tech_score = int(_get(tech, "score", 0) or 0)
    comp_score = int(_get(comp, "score", 0) or 0)
    issues: list[dict] = []
    for agent_name, finding in (("wang_anshi", tech), ("bao_zheng", comp)):
        for issue in _issue_dicts(finding):
            item = dict(issue)
            item["agent"] = agent_name
            issues.append(item)
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    issues.sort(key=lambda item: rank.get(str(item.get("severity") or "").lower(), 0), reverse=True)
    needs_material = [item for item in issues if item.get("needs_material")]
    errors = [str(_get(tech, "error", "") or ""), str(_get(comp, "error", "") or "")]
    score = round((tech_score + comp_score) / 2)
    satisfied = str(_get(tech, "satisfied", "") or _get(comp, "satisfied", "") or "")
    if not satisfied:
        satisfied = "satisfied" if score >= 85 else ("partial" if score >= 70 else "unsatisfied")
    missing_points = list(_get(tech, "missing_points", []) or []) + list(_get(comp, "missing_points", []) or [])
    if not missing_points and issues:
        missing_points = [str(item.get("point") or "")[:80] for item in issues[:6] if item.get("point")]
    return {
        "tech_score": tech_score,
        "comp_score": comp_score,
        "score": score,
        "satisfied": satisfied,
        "criterion_text": str(_get(tech, "criterion_text", "") or ""),
        "covered_points": list(_get(tech, "covered_points", []) or []),
        "missing_points": missing_points,
        "issues": issues,
        "needs_material": needs_material,
        "fixable": [item for item in issues if not item.get("needs_material")],
        "error": "；".join([e for e in errors if e][:2]),
    }


async def upsert_chapter_review(
    db,
    *,
    block_pk: int,
    project_id: int,
    thread_id: str,
    stage: str,
    data: dict,
    versions: dict,
    tier: str,
) -> None:
    """写一章当前态；stage=final 会覆盖 writing（终评权威）。"""
    await db.execute(
        """INSERT INTO chapter_reviews
           (block_id, project_id, tech_score, comp_score, score, satisfied, criterion_text,
            covered_points, missing_points, issues, needs_material, tier,
            stage, thread_id, content_version, evidence_version, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(block_id) DO UPDATE SET
             project_id=excluded.project_id, tech_score=excluded.tech_score,
             comp_score=excluded.comp_score, score=excluded.score,
             satisfied=excluded.satisfied, criterion_text=excluded.criterion_text,
             covered_points=excluded.covered_points, missing_points=excluded.missing_points,
             issues=excluded.issues, needs_material=excluded.needs_material,
             tier=excluded.tier, stage=excluded.stage, thread_id=excluded.thread_id,
             content_version=excluded.content_version, evidence_version=excluded.evidence_version,
             updated_at=CURRENT_TIMESTAMP""",
        (
            block_pk, int(project_id or 0),
            int(data.get("tech_score") or 0), int(data.get("comp_score") or 0),
            int(data.get("score") or 0), str(data.get("satisfied") or ""),
            str(data.get("criterion_text") or ""),
            _json(data.get("covered_points")), _json(data.get("missing_points")),
            _json(data.get("issues")), _json(data.get("needs_material")),
            str(tier or data.get("tier") or ""), str(stage or "writing"), str(thread_id or ""),
            str((versions or {}).get("content_version") or ""),
            str((versions or {}).get("evidence_version") or ""),
        ),
    )
    await db.commit()


async def append_history(
    db,
    *,
    block_pk: int,
    project_id: int,
    thread_id: str,
    stage: str,
    data: dict,
    tier: str,
    versions: dict | None = None,
) -> None:
    await db.execute(
        """INSERT INTO review_history
           (block_id, project_id, score, tech_score, comp_score, satisfied,
            issues_count, needs_material_count, stage, thread_id, tier,
            content_version, evidence_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            block_pk, int(project_id or 0), int(data.get("score") or 0),
            int(data.get("tech_score") or 0), int(data.get("comp_score") or 0),
            str(data.get("satisfied") or ""),
            len(data.get("issues") or []), len(data.get("needs_material") or []),
            str(stage or "writing"), str(thread_id or ""), str(tier or ""),
            str((versions or {}).get("content_version") or ""),
            str((versions or {}).get("evidence_version") or ""),
        ),
    )
    await db.commit()


async def save_agent_finding(
    db,
    *,
    thread_id: str,
    project_id: int,
    block_code: str,
    agent: str,
    finding,
    stage: str,
    versions: dict,
    tier: str,
) -> None:
    """写/替换一条 per-agent 明细（同 thread+block+agent）。"""
    if not block_code or not thread_id or finding is None:
        # reviews.thread_id 有 FK；无 run 的老块不写 per-agent 明细。
        return
    await db.execute(
        "DELETE FROM reviews WHERE thread_id=? AND block_id=? AND agent=?",
        (str(thread_id or ""), block_code, agent),
    )
    await db.execute(
        """INSERT INTO reviews
           (thread_id, block_id, agent, score, issues, strengths, error,
            criterion_text, satisfied, covered_points, missing_points,
            stage, tier, content_version, evidence_version, project_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(thread_id or ""), block_code, agent, int(_get(finding, "score", 0) or 0),
            _json(_issue_dicts(finding)), _json(_get(finding, "strengths", []) or []),
            str(_get(finding, "error", "") or ""), str(_get(finding, "criterion_text", "") or ""),
            str(_get(finding, "satisfied", "") or ""),
            _json(_get(finding, "covered_points", []) or []),
            _json(_get(finding, "missing_points", []) or []),
            str(stage or "writing"), str(tier or ""),
            str((versions or {}).get("content_version") or ""),
            str((versions or {}).get("evidence_version") or ""),
            int(project_id or 0),
        ),
    )
    await db.commit()


async def promote_to_final(db, *, block_pk: int, thread_id: str) -> None:
    """终评复用编写时评审：不改分数，只把当前态标为 final。"""
    await db.execute(
        "UPDATE chapter_reviews SET stage='final', thread_id=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE block_id=?",
        (str(thread_id or ""), block_pk),
    )
    await db.commit()


async def load_current_for_thread(db, project_id: int, thread_id: str) -> list[dict]:
    """当前工程/线程下所有章评审（按 blocks.id 关联，返回业务编码）。"""
    if thread_id:
        rows = await (await db.execute(
            """SELECT cr.*, b.block_id AS block_code
               FROM chapter_reviews cr JOIN blocks b ON b.id = cr.block_id
               WHERE b.project_id=? AND b.run_thread_id=?""",
            (int(project_id or 0), str(thread_id)),
        )).fetchall()
    else:
        rows = await (await db.execute(
            """SELECT cr.*, b.block_id AS block_code
               FROM chapter_reviews cr JOIN blocks b ON b.id = cr.block_id
               WHERE b.project_id=?""",
            (int(project_id or 0),),
        )).fetchall()
    return [dict(r) for r in rows]


def finding_from_chapter_row(row: dict, agent: str) -> dict:
    """从章级当前态反推 per-agent finding（终评复用章节评审时给前端/state 用）。"""
    issues = []
    try:
        raw = json.loads(row.get("issues") or "[]")
        issues = [i for i in raw if isinstance(i, dict) and str(i.get("agent") or "") == agent]
    except (ValueError, TypeError):
        issues = []
    score = row.get("tech_score") if agent == "wang_anshi" else row.get("comp_score")
    return {
        "block_id": row.get("block_code") or "",
        "agent": agent,
        "score": int(score or 0),
        "issues": issues,
        "strengths": [],
        "error": "",
        "criterion_text": row.get("criterion_text") or "",
        "satisfied": row.get("satisfied") or "",
        "covered_points": json.loads(row.get("covered_points") or "[]") if isinstance(row.get("covered_points"), str) else (row.get("covered_points") or []),
        "missing_points": json.loads(row.get("missing_points") or "[]") if isinstance(row.get("missing_points"), str) else (row.get("missing_points") or []),
        "stage": row.get("stage") or "writing",
        "thread_id": row.get("thread_id") or "",
    }
