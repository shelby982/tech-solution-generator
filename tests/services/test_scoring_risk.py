"""审查对齐评分口径 + 得分风险聚合。

用户确定的核心数据关系最后一环：「审查结果解决**是否真正满足要求**」。
在此之前 reviews 只回答"写得好不好"（score/issues/strengths），
不回答"这一章的应答是否命中它该拿的那些分"。
"""
from __future__ import annotations

import json

import aiosqlite
import pytest

from db import init_db
from domain.review import Finding
from domain.review.repository import ReviewRepository


class _Agent:
    """拿真实助手的解析/落库路径，但不调模型。"""
    from agents.wang_anshi import WangAnshiAgent as _W

    _parse_finding = _W._parse_finding
    AGENT_NAME = "wang_anshi"


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "risk.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


# ── 审查解析：结构化判断 ──────────────────────────────────────

def test_parse_finding_extracts_satisfaction_judgement():
    """模型给的满足度/命中要点/缺失要点都要解析出来。

    这是"是否真正满足要求"的落点：缺什么是可执行的整改清单。
    """
    raw = json.dumps({
        "score": 72,
        "issues": [{"severity": "high", "point": "未提供证书", "suggestion": "补 PMP 证书",
                    "needs_material": True, "material_query": "PMP 证书"}],
        "strengths": ["结构清晰"],
        "satisfied": "partial",
        "covered_points": ["方案总体架构设计合理"],
        "missing_points": ["未提供 PMP 证书", "未体现信创适配"],
    }, ensure_ascii=False)
    f = _Agent()._parse_finding("s1", raw)
    assert f.satisfied == "partial"
    assert f.covered_points == ["方案总体架构设计合理"]
    assert f.missing_points == ["未提供 PMP 证书", "未体现信创适配"]
    assert f.score == 72


def test_parse_finding_tolerates_missing_and_bad_values():
    """老模型 / 老数据没有这些字段时不能报错，且非法满足度要归一为空。"""
    f = _Agent()._parse_finding("s1", json.dumps({"score": 80, "satisfied": "maybe"}))
    assert f.satisfied == "" and f.missing_points == [] and f.covered_points == []
    f2 = _Agent()._parse_finding("s1", json.dumps({"score": 80, "satisfied": "SATISFIED "}))
    assert f2.satisfied == "satisfied"          # 归一化大小写/空白


def test_finding_dict_roundtrip_keeps_judgement():
    f = Finding(block_id="s1", agent="wang_anshi", score=90, satisfied="satisfied",
                covered_points=["a"], missing_points=["b"], criterion_text="口径原文")
    back = Finding.from_dict(f.to_dict())
    assert (back.satisfied, back.covered_points, back.missing_points,
            back.criterion_text) == ("satisfied", ["a"], ["b"], "口径原文")


# ── 落库 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_repository_persists_judgement(db):
    repo = ReviewRepository(db)
    await repo.add_finding("tid-1", Finding(
        block_id="s1", agent="wang_anshi", score=88, satisfied="partial",
        covered_points=["架构合理"], missing_points=["未提供证书"], criterion_text="口径",
    ))
    cur = await db.execute("SELECT * FROM reviews WHERE thread_id='tid-1'")
    row = await cur.fetchone()
    assert row["satisfied"] == "partial"
    assert json.loads(row["covered_points"]) == ["架构合理"]
    assert json.loads(row["missing_points"]) == ["未提供证书"]
    assert row["criterion_text"] == "口径"


# ── 得分风险聚合 ──────────────────────────────────────────────

async def _seed(db, *, point_id="rp-a", factor="技术方案", points=30.0,
                chapters=(("s1", ["rp-a"], None),)):
    from services.point_store import replace_review_points
    from infra.parser.review_points import ReviewPoint

    p = ReviewPoint(package_no="2", category="tech", factor=factor, item=factor,
                    criterion="口径", points=points)
    await replace_review_points(db, 7, "tid-1", [p])
    # 落库会把 id 重算成内容派生的稳定 id —— 章节必须引用它，不能用手写的假 id
    point_id = p.point_id

    from services.block_store import sync_outline_placeholders
    secs = []
    for bid, ids, _ in chapters:
        s = type("S", (), {})()
        s.id, s.level, s.title = bid, 1, f"章节{bid}"
        # ids 非空即表示"这章属于该评分点"——引用词必须用落库后的真实稳定 id，
        # 否则章节挂在假 id 上，风险聚合会多出一个空评分点
        s.chain = {"review_point_id": point_id if ids else "", "stable_key": bid}
        secs.append(s)
    await sync_outline_placeholders(db, 7, secs, run_thread_id="tid-1")


@pytest.mark.asyncio
async def test_scoring_risk_aggregates_by_review_point(db):
    from services.scoring_risk import summarize_scoring_risk

    await _seed(db, chapters=(("s1", ["rp-a"], None), ("s2", ["rp-a"], None)))
    repo = ReviewRepository(db)
    # 一章不满足（高风险），一章正常
    await repo.add_finding("tid-1", Finding(block_id="s1", agent="wang_anshi", score=40,
                                            satisfied="unsatisfied",
                                            missing_points=["未提供 PMP 证书"]))
    await repo.add_finding("tid-1", Finding(block_id="s2", agent="wang_anshi", score=95,
                                            satisfied="satisfied"))

    data = await summarize_scoring_risk(db, 7, "tid-1")
    assert data["summary"]["points_total"] == 1
    pt = data["points"][0]
    # 评分点风险取名下最差的一章：有一章不满足，这一项就悬
    assert pt["risk"] == "high"
    assert pt["chapters_total"] == 2 and pt["chapters_reviewed"] == 2
    assert pt["missing_points"] == ["未提供 PMP 证书"]
    assert data["summary"]["high"] == 1


@pytest.mark.asyncio
async def test_scoring_risk_marks_unreviewed_and_partial(db):
    from services.scoring_risk import summarize_scoring_risk

    await _seed(db, chapters=(("s1", ["rp-a"], None), ("s2", ["rp-a"], None)))
    repo = ReviewRepository(db)
    await repo.add_finding("tid-1", Finding(block_id="s1", agent="wang_anshi", score=75,
                                            satisfied="partial", missing_points=["未体现信创适配"]))
    data = await summarize_scoring_risk(db, 7, "tid-1")
    pt = data["points"][0]
    assert pt["risk"] == "medium"                     # partial → 有缺口
    assert pt["chapters_reviewed"] == 1               # s2 没审
    assert "未体现信创适配" in pt["missing_points"]


@pytest.mark.asyncio
async def test_scoring_risk_without_reviews_is_unreviewed(db):
    from services.scoring_risk import summarize_scoring_risk

    await _seed(db, chapters=(("s1", ["rp-a"], None),))
    data = await summarize_scoring_risk(db, 7, "tid-1")
    assert data["points"][0]["risk"] == "unreviewed"
    assert data["summary"]["unreviewed"] == 1


@pytest.mark.asyncio
async def test_scoring_risk_lists_unlinked_chapters(db):
    """没有评分点归属的章节（老数据 / 附录板块）单列，不混进评分点风险。"""
    from services.scoring_risk import summarize_scoring_risk

    await _seed(db, chapters=(("s1", ["rp-a"], None), ("s9", [], None)))
    data = await summarize_scoring_risk(db, 7, "tid-1")
    assert data["summary"]["chapters_without_point"] == 1
    assert data["unlinked_chapters"][0]["block_id"] == "s9"


@pytest.mark.asyncio
async def test_scoring_risk_reads_chapter_reviews_primary(db):
    """统一模型后优先读 chapter_reviews（带版本 freshness），不再依赖 reviews 明细。"""
    from services.review_store import upsert_chapter_review
    from services.writing_workspace import versions

    await _seed(db, chapters=(("s1", ["rp-a"], None),))
    row = await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=7 AND block_id='s1'")).fetchone()
    vs = await versions(db, dict(row))
    await upsert_chapter_review(
        db, block_pk=int(row["id"]), project_id=7, thread_id="tid-1", stage="final",
        versions=vs, tier="final",
        data={"tech_score": 40, "comp_score": 40, "score": 40, "satisfied": "unsatisfied",
              "criterion_text": "口径", "covered_points": [],
              "missing_points": ["未提供 PMP 证书"], "issues": [], "needs_material": []},
    )

    from services.scoring_risk import summarize_scoring_risk
    data = await summarize_scoring_risk(db, 7, "tid-1")
    pt = data["points"][0]
    assert pt["risk"] == "high"
    assert pt["chapters_reviewed"] == 1
    assert "未提供 PMP 证书" in pt["missing_points"]
