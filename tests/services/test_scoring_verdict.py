"""评分点级评审结论（verdict）：得分预估 / 缺口 / 举证 / 章节下钻。

为什么单独钉这层：得分是**按评分点**给的，而 `reviews` 按**章节**存。
`summarize_scoring_risk` 已经把章节聚到评分点，但没回答"这个评分点现在能拿几分"。
`build_scoring_verdict` 补的就是这一步（评审页主视图的数据源）。

口径（确定性，不引入模型判断）：
- 满足 = 满分；部分满足 = 半分；不满足 = 0；**未评审不计入预估**（单列"待评"分值）；
- 一个评分点的满足度取它名下**最差**的章节（有一章不满足，这一项就悬）。
"""
from __future__ import annotations

import json

import aiosqlite
import pytest

from db import init_db
from services.scoring_verdict import build_scoring_verdict


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "verdict.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


async def _seed(db, *, blocks, points, reviews, tokens=None):
    await db.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    for b in blocks:
        await db.execute(
            """INSERT INTO blocks (project_id, block_id, title, level, status,
                                   review_point_ids, evidence_required, run_thread_id)
               VALUES (1, ?, ?, 3, 'generating', ?, ?, 't1')""",
            (b["block_id"], b["title"], json.dumps(b.get("points_ids") or []),
             b.get("evidence_required") or ""))
    for p in points:
        await db.execute(
            """INSERT INTO review_points (project_id, run_thread_id, point_id, factor, item,
                                          criterion, points, category)
               VALUES (1, 't1', ?, ?, ?, ?, ?, 'tech')""",
            (p["point_id"], p.get("factor") or "", p.get("item") or "",
             p.get("criterion") or "", p["points"]))
    for r in reviews:
        await db.execute(
            """INSERT INTO reviews (thread_id, block_id, agent, score, satisfied,
                                    covered_points, missing_points, criterion_text)
               VALUES ('t1', ?, 'wang_anshi', 80, ?, ?, ?, ?)""",
            (r["block_id"], r.get("satisfied") or "",
             json.dumps(r.get("covered") or [], ensure_ascii=False),
             json.dumps(r.get("missing") or [], ensure_ascii=False),
             r.get("criterion") or ""))
    for t in tokens or []:
        await db.execute(
            "INSERT INTO llm_usage (call_site, model, total_tokens, project_id, thread_id) "
            "VALUES ('review', 'm', ?, 1, 't1')", (t,))
    await db.commit()


@pytest.mark.asyncio
async def test_verdict_maps_satisfaction_to_expected_score(db):
    await _seed(
        db,
        blocks=[
            {"block_id": "b1", "title": "技术方案可行性", "points_ids": ["P1"],
             "evidence_required": "项目合同关键页"},
            {"block_id": "b2", "title": "营业净利率", "points_ids": ["P2"]},
            {"block_id": "b3", "title": "项目业绩", "points_ids": ["P3"]},
        ],
        points=[
            {"point_id": "P1", "factor": "技术方案", "item": "技术方案可行性", "points": 12},
            {"point_id": "P2", "factor": "财务状况", "item": "营业净利率", "points": 10},
            {"point_id": "P3", "factor": "项目业绩", "item": "项目业绩", "points": 30},
        ],
        reviews=[
            {"block_id": "b1", "satisfied": "satisfied"},                     # → 满足 → 12
            {"block_id": "b2", "satisfied": "unsatisfied",
             "missing": ["缺审计报告"]},                                       # → 不满足 → 0
            {"block_id": "b3", "satisfied": "partial",
             "missing": ["仅 3 个合同"]},                                      # → 部分 → 15
        ],
        tokens=[1000, 2000],
    )
    out = await build_scoring_verdict(db, 1, "t1")
    by_id = {p["point_id"]: p for p in out["points"]}

    assert by_id["P1"]["verdict"] == "ok" and by_id["P1"]["estimated_score"] == 12
    assert by_id["P2"]["verdict"] == "high" and by_id["P2"]["estimated_score"] == 0
    assert by_id["P3"]["verdict"] == "medium" and by_id["P3"]["estimated_score"] == 15
    # 举证：P1 的章节有 evidence_required → 计 1；并如实标注"引用未落库"
    assert by_id["P1"]["evidence_chapters"] == 1 and by_id["P1"]["evidence_tracked"] is False
    assert by_id["P2"]["evidence_chapters"] == 0

    s = out["summary"]
    assert s["points_total"] == 3
    assert s["score_max"] == 52 and round(s["score_estimated"], 2) == 27
    assert s["score_pending"] == 0
    assert s["ok"] == 1 and s["medium"] == 1 and s["high"] == 1 and s["unreviewed"] == 0
    assert s["gaps"] == 2                       # 缺审计报告 + 仅 3 个合同
    assert s["cost_tokens"] == 3000             # 成本来自 llm_usage


@pytest.mark.asyncio
async def test_unreviewed_point_is_pending_not_zero(db):
    """**没审过 ≠ 不得分**：未评审的评分点单列"待评"，不能混进"预计得分"里当 0。"""
    await _seed(
        db,
        blocks=[{"block_id": "b1", "title": "x", "points_ids": ["P1"]}],
        points=[{"point_id": "P1", "item": "x", "points": 20}],
        reviews=[],                              # 一条评审都没有
    )
    out = await build_scoring_verdict(db, 1, "t1")
    p = out["points"][0]
    assert p["verdict"] == "unreviewed" and p["estimated_score"] is None
    assert out["summary"]["score_estimated"] == 0
    assert out["summary"]["score_pending"] == 20      # 20 分待评，而不是"丢了 20 分"


@pytest.mark.asyncio
async def test_point_verdict_worst_but_score_weighted(db):
    """评分点挂多章（评审 v2 A）：verdict 仍取最差（颜色语义），但预估分按加权覆盖，允许部分得分。"""
    await _seed(
        db,
        blocks=[
            {"block_id": "b1", "title": "a", "points_ids": ["P1"]},
            {"block_id": "b2", "title": "b", "points_ids": ["P1"]},
        ],
        points=[{"point_id": "P1", "item": "多章评分点", "points": 20}],
        reviews=[
            {"block_id": "b1", "satisfied": "satisfied"},
            {"block_id": "b2", "satisfied": "unsatisfied", "missing": ["缺证据"]},
        ],
    )
    out = await build_scoring_verdict(db, 1, "t1")
    p = out["points"][0]
    assert p["verdict"] == "high"                      # 颜色：有一章不满足 → 该项悬
    assert p["estimated_score"] == 10.0                 # 分数：20 × (1.0+0.0)/2 = 10
    assert p["estimated_basis"]["reviewed"] == 2 and p["estimated_basis"]["avg"] == 0.5
    assert p["chapters_total"] == 2 and p["chapters_reviewed"] == 2
    assert p["chapters"][1]["missing_points"] == ["缺证据"]     # 章节明细可下钻
