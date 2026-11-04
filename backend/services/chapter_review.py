"""章节级评审（编写 ↔ 评审结合）：单章跑"技术 + 合规"双评审，结果落库供编写页面直接使用。

与工作流里的评审口径一致（同一个 agent、同一套 Finding），区别只是：
  · 范围 = 一章（而不是整篇）
  · 触发 = 用户点「评审本章」
  · 产出 = 分数 + issues（含 needs_material）+ 得分点覆盖，直接进编写页面
"""
from __future__ import annotations

import asyncio
import json

from db import get_db
from domain.proposal.models import BlockOutput
from domain.spec.models import OutlineMatrixRow


async def _chapter_inputs(db, block: dict) -> tuple[BlockOutput, OutlineMatrixRow, dict]:
    """从 blocks 表 + 编写计划组装评审输入（不依赖工作流 state）。"""
    block_code = str(block.get("block_id") or "")
    content = str(block.get("content") or "")
    output = BlockOutput(block_id=block_code, kind="tech", content=content)

    matrix = OutlineMatrixRow(
        block_id=block_code,
        title=str(block.get("title") or ""),
        requirement=str(block.get("requirement") or ""),
        key_points=str(block.get("key_points") or ""),
        veto_items=str(block.get("veto_items") or ""),
        bonus_items=str(block.get("bonus_items") or ""),
        score_items=str(block.get("score_items") or ""),
        evidence_required=str(block.get("evidence_required") or ""),
        indicators=str(block.get("indicators") or ""),
    )

    # 评分口径：优先用章节挂的评分点（review_point_ids）
    criterion_text = ""
    spec_requirements: list[str] = []
    try:
        from services.point_store import list_review_points, list_spec_requirements
        ids = json.loads(block.get("review_point_ids") or "[]")
        tid = str(block.get("run_thread_id") or "")
        if ids:
            points = await list_review_points(db, run_thread_id=tid)
            picked = [p for p in points if str(getattr(p, "point_id", "")) in {str(i) for i in ids}]
            criterion_text = "；".join(
                str(getattr(p, "criterion", "") or getattr(p, "item", "") or "").strip()
                for p in picked if str(getattr(p, "criterion", "") or getattr(p, "item", "") or "").strip())
        req_ids = json.loads(block.get("spec_requirement_ids") or "[]")
        if req_ids:
            reqs = await list_spec_requirements(db, run_thread_id=tid)
            wanted = {str(i) for i in req_ids}
            spec_requirements = [str(getattr(r, "text", "") or "") for r in reqs
                                 if str(getattr(r, "requirement_id", "") or "") in wanted]
    except Exception:
        pass
    if not criterion_text:
        criterion_text = str(block.get("score_items") or "").strip()

    chain = {"criterion_text": criterion_text, "spec_requirements": spec_requirements[:6]}
    return output, matrix, chain


def _issue_dicts(finding) -> list[dict]:
    return [issue.to_dict() for issue in (getattr(finding, "issues", None) or [])]


def _combine(tech, comp) -> dict:
    tech_score = int(getattr(tech, "score", 0) or 0)
    comp_score = int(getattr(comp, "score", 0) or 0)
    issues = []
    for agent_name, finding in (("wang_anshi", tech), ("bao_zheng", comp)):
        for issue in _issue_dicts(finding):
            item = dict(issue)
            item["agent"] = agent_name
            issues.append(item)
    # 严重度排序：critical > high > medium > low
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    issues.sort(key=lambda item: rank.get(str(item.get("severity") or "").lower(), 0), reverse=True)
    needs_material = [item for item in issues if item.get("needs_material")]
    fixable = [item for item in issues if not item.get("needs_material")]
    errors = [getattr(tech, "error", ""), getattr(comp, "error", "")]
    return {
        "tech_score": tech_score,
        "comp_score": comp_score,
        "score": round((tech_score + comp_score) / 2),
        "satisfied": str(getattr(tech, "satisfied", "") or getattr(comp, "satisfied", "") or ""),
        "criterion_text": str(getattr(tech, "criterion_text", "") or ""),
        "covered_points": list(getattr(tech, "covered_points", []) or []),
        "missing_points": list(getattr(tech, "missing_points", []) or []),
        "issues": issues,
        "needs_material": needs_material,
        "fixable": fixable,
        "error": "；".join([e for e in errors if e][:2]),
    }


async def review_block(block_pk: int, *, thread_id: str = "") -> dict:
    """对一章跑双评审（并行），返回合并结果并落库。"""
    from agents.bao_zheng import BaoZhengAgent
    from agents.wang_anshi import WangAnshiAgent

    async with get_db() as db:
        row = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_pk,))).fetchone()
        if not row:
            raise ValueError("章节不存在")
        block = dict(row)
        output, matrix, chain = await _chapter_inputs(db, block)

    blocks = {output.block_id: output}
    outline_matrix = {output.block_id: matrix}
    chain_by_block = {output.block_id: chain}

    tech_agent, comp_agent = WangAnshiAgent(), BaoZhengAgent()
    tech_result, comp_result = await asyncio.gather(
        tech_agent.review(blocks=blocks, outline_matrix=outline_matrix, chain_by_block=chain_by_block),
        comp_agent.review(blocks=blocks, outline_matrix=outline_matrix, chain_by_block=chain_by_block),
        return_exceptions=True,
    )
    tech = tech_result.get(output.block_id) if isinstance(tech_result, dict) else None
    comp = comp_result.get(output.block_id) if isinstance(comp_result, dict) else None
    if tech is None and comp is None:
        raise ValueError("评审失败（两个评审 agent 都没有返回结果）")

    combined = _combine(tech, comp)
    combined["block_id"] = output.block_id

    async with get_db() as db:
        await db.execute(
            "INSERT INTO chapter_reviews (block_id, project_id, tech_score, comp_score, score, satisfied, "
            "criterion_text, covered_points, missing_points, issues, needs_material, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(block_id) DO UPDATE SET tech_score=excluded.tech_score, comp_score=excluded.comp_score, "
            "score=excluded.score, satisfied=excluded.satisfied, criterion_text=excluded.criterion_text, "
            "covered_points=excluded.covered_points, missing_points=excluded.missing_points, "
            "issues=excluded.issues, needs_material=excluded.needs_material, updated_at=CURRENT_TIMESTAMP",
            (block_pk, int(block.get("project_id") or 0), combined["tech_score"], combined["comp_score"],
             combined["score"], combined["satisfied"], combined["criterion_text"],
             json.dumps(combined["covered_points"], ensure_ascii=False),
             json.dumps(combined["missing_points"], ensure_ascii=False),
             json.dumps(combined["issues"], ensure_ascii=False),
             json.dumps(combined["needs_material"], ensure_ascii=False)))
        await db.commit()
    return combined


async def load_review(block_pk: int) -> dict | None:
    """读取最近一次评审结果（没有则 None）。"""
    async with get_db() as db:
        row = await (await db.execute("SELECT * FROM chapter_reviews WHERE block_id=?", (block_pk,))).fetchone()
    if not row:
        return None
    data = dict(row)

    def _loads(raw):
        try:
            return json.loads(raw or "[]")
        except Exception:
            return []

    issues = _loads(data.get("issues"))
    return {
        "block_id": data.get("block_id"),
        "tech_score": int(data.get("tech_score") or 0),
        "comp_score": int(data.get("comp_score") or 0),
        "score": int(data.get("score") or 0),
        "satisfied": data.get("satisfied") or "",
        "criterion_text": data.get("criterion_text") or "",
        "covered_points": _loads(data.get("covered_points")),
        "missing_points": _loads(data.get("missing_points")),
        "issues": issues,
        "needs_material": _loads(data.get("needs_material")),
        "fixable": [item for item in issues if not item.get("needs_material")],
        "updated_at": data.get("updated_at") or "",
    }
