"""ReviewRepository / WorkflowRunRepository 集成测试 — 内存 SQLite 验证表 + 往返。"""

import asyncio

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from domain.review import (
    Finding,
    GlobalReport,
    Issue,
    ReviewRepository,
    WorkflowRunRepository,
)


# ─────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """每个测试一个内存 SQLite 连接 + 一个预建项目。"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute(
        "INSERT INTO projects (name) VALUES (?)", ("测试项目",)
    )
    await conn.commit()
    proj_id = cursor.lastrowid
    yield conn, proj_id
    await conn.close()


# ─────────────────────────────────────────────────────────
# WorkflowRunRepository
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_workflow_run(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    row = await repo.create("t-001", proj_id, stage="parsing")
    assert row["thread_id"] == "t-001"
    assert row["project_id"] == proj_id
    assert row["stage"] == "parsing"
    assert row["finished_at"] is None
    again = await repo.get("t-001")
    assert again is not None
    assert again["stage"] == "parsing"
    assert await repo.get("not-exist") is None


@pytest.mark.asyncio
async def test_create_with_invalid_stage_raises(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    with pytest.raises(ValueError):
        await repo.create("t-bad", proj_id, stage="not-a-stage")


@pytest.mark.asyncio
async def test_list_workflow_runs_by_project_orders_by_created_at_desc(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    await repo.create("t-1", proj_id, stage="idle")
    await asyncio.sleep(1.05)  # SQLite CURRENT_TIMESTAMP 精度为秒
    await repo.create("t-2", proj_id, stage="parsing")
    rows = await repo.list_by_project(proj_id)
    assert [r["thread_id"] for r in rows] == ["t-2", "t-1"]


@pytest.mark.asyncio
async def test_list_only_active_filters_done_and_aborted(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    await repo.create("t-active", proj_id, stage="generating")
    await repo.create("t-done", proj_id, stage="idle")
    await repo.update_stage("t-done", "done")
    await repo.create("t-aborted", proj_id, stage="idle")
    await repo.update_stage("t-aborted", "aborted")
    active = await repo.list_by_project(proj_id, only_active=True)
    assert {r["thread_id"] for r in active} == {"t-active"}
    all_rows = await repo.list_by_project(proj_id)
    assert {r["thread_id"] for r in all_rows} == {"t-active", "t-done", "t-aborted"}


@pytest.mark.asyncio
async def test_update_stage_writes_finished_at_when_done(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    await repo.create("t-fin", proj_id, stage="generating")
    before = await repo.get("t-fin")
    assert before["finished_at"] is None
    after = await repo.update_stage("t-fin", "done")
    assert after is not None
    assert after["stage"] == "done"
    assert after["finished_at"] is not None

    # 非 done/aborted 不写 finished_at
    await repo.create("t-mid", proj_id, stage="idle")
    mid = await repo.update_stage("t-mid", "matching")
    assert mid["finished_at"] is None


@pytest.mark.asyncio
async def test_update_stage_invalid_value_raises(db):
    conn, proj_id = db
    repo = WorkflowRunRepository(conn)
    await repo.create("t-x", proj_id, stage="idle")
    with pytest.raises(ValueError):
        await repo.update_stage("t-x", "garbage")


# ─────────────────────────────────────────────────────────
# ReviewRepository
# ─────────────────────────────────────────────────────────

async def _create_run(conn: aiosqlite.Connection, proj_id: int, thread_id: str) -> None:
    repo = WorkflowRunRepository(conn)
    await repo.create(thread_id, proj_id, stage="reviewing")


@pytest.mark.asyncio
async def test_add_finding_round_trip(db):
    conn, proj_id = db
    await _create_run(conn, proj_id, "t-r1")
    repo = ReviewRepository(conn)
    finding = Finding(
        block_id="s1",
        agent="wang_anshi",
        score=82,
        issues=[
            Issue(severity="high", point="缺少性能指标", suggestion="补充 SLA"),
            Issue(severity="low", point="表述冗余"),
        ],
        strengths=["架构图清晰", "技术栈成熟"],
        error="",
    )
    new_id = await repo.add_finding("t-r1", finding)
    assert new_id > 0
    rows = await repo.list_findings_by_thread("t-r1")
    assert len(rows) == 1
    got = rows[0]
    assert got.block_id == "s1"
    assert got.agent == "wang_anshi"
    assert got.score == 82
    assert [i.to_dict() for i in got.issues] == [i.to_dict() for i in finding.issues]
    assert got.strengths == ["架构图清晰", "技术栈成熟"]
    assert got.error == ""


@pytest.mark.asyncio
async def test_list_findings_by_block_filters_correctly(db):
    conn, proj_id = db
    await _create_run(conn, proj_id, "t-r2")
    repo = ReviewRepository(conn)
    await repo.add_finding("t-r2", Finding(block_id="s1", agent="wang_anshi", score=80))
    await repo.add_finding("t-r2", Finding(block_id="s1", agent="bao_zheng", score=70))
    await repo.add_finding("t-r2", Finding(block_id="s2", agent="wang_anshi", score=90))
    rows = await repo.list_findings_by_block("t-r2", "s1")
    assert [r.agent for r in rows] == ["bao_zheng", "wang_anshi"]
    assert all(r.block_id == "s1" for r in rows)


@pytest.mark.asyncio
async def test_clear_thread_removes_only_target_thread(db):
    conn, proj_id = db
    await _create_run(conn, proj_id, "t-keep")
    await _create_run(conn, proj_id, "t-clear")
    repo = ReviewRepository(conn)
    await repo.add_finding("t-keep", Finding(block_id="s1", agent="wang_anshi"))
    await repo.add_finding("t-clear", Finding(block_id="s1", agent="wang_anshi"))
    await repo.add_finding("t-clear", Finding(block_id="s2", agent="bao_zheng"))
    await repo.clear_thread("t-clear")
    assert await repo.list_findings_by_thread("t-clear") == []
    keep = await repo.list_findings_by_thread("t-keep")
    assert len(keep) == 1


@pytest.mark.asyncio
async def test_finding_handles_corrupted_issues_json(db):
    conn, proj_id = db
    await _create_run(conn, proj_id, "t-corrupt")
    # 直接插入一条 issues/strengths 是非法 JSON 的脏数据
    await conn.execute(
        """INSERT INTO reviews
           (thread_id, block_id, agent, score, issues, strengths, error)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("t-corrupt", "s1", "wang_anshi", 60, "not-json", "{also-bad", ""),
    )
    await conn.commit()
    repo = ReviewRepository(conn)
    rows = await repo.list_findings_by_thread("t-corrupt")
    assert len(rows) == 1
    assert rows[0].issues == []
    assert rows[0].strengths == []
    assert rows[0].score == 60


# ─────────────────────────────────────────────────────────
# Issue / Finding / GlobalReport dataclass round-trip
# ─────────────────────────────────────────────────────────

def test_issue_to_dict_from_dict_round_trip():
    src = Issue(severity="critical", point="缺少必填项", suggestion="补充 X")
    rebuilt = Issue.from_dict(src.to_dict())
    assert rebuilt == src
    # 容错默认值
    fallback = Issue.from_dict({})
    assert fallback.severity == "medium"
    assert fallback.point == ""
    assert fallback.suggestion == ""


def test_finding_to_dict_from_dict_round_trip():
    src = Finding(
        block_id="s3",
        agent="bao_zheng",
        score=75,
        issues=[Issue(severity="high", point="资质过期")],
        strengths=["历史业绩充分"],
        error="",
    )
    rebuilt = Finding.from_dict(src.to_dict())
    assert rebuilt.block_id == src.block_id
    assert rebuilt.agent == src.agent
    assert rebuilt.score == 75
    assert [i.to_dict() for i in rebuilt.issues] == [i.to_dict() for i in src.issues]
    assert rebuilt.strengths == src.strengths
    assert rebuilt.error == ""
    # score=None 兜底为 0
    none_score = Finding.from_dict({"block_id": "s4", "agent": "wang_anshi", "score": None})
    assert none_score.score == 0


def test_global_report_to_dict_from_dict_round_trip():
    src = GlobalReport(
        per_block={"s1": {"tech_score": 80, "comp_score": 75, "severity_counts": {"high": 1}}},
        total_score=78.5,
        top_risks=["风险 A", "风险 B"],
        missing_evidence=["资质 X"],
        missing_bonus=["奖项 Y"],
    )
    rebuilt = GlobalReport.from_dict(src.to_dict())
    assert rebuilt.per_block == src.per_block
    assert rebuilt.total_score == 78.5
    assert rebuilt.top_risks == src.top_risks
    assert rebuilt.missing_evidence == src.missing_evidence
    assert rebuilt.missing_bonus == src.missing_bonus
    # 空字典兜底
    empty = GlobalReport.from_dict({})
    assert empty.per_block == {}
    assert empty.total_score == 0.0
    assert empty.top_risks == []


def test_issue_new_material_fields_roundtrip():
    """Issue 的 needs_material / material_query 能完整往返。"""
    from domain.review import Issue

    issue = Issue(
        severity="critical",
        point="未响应否决项『必须提供 3 年内同类项目业绩证明』",
        suggestion="补充业绩证明材料",
        needs_material=True,
        material_query="近三年同类项目业绩证明合同",
    )

    d = issue.to_dict()
    assert d["needs_material"] is True
    assert d["material_query"] == "近三年同类项目业绩证明合同"
    assert Issue.from_dict(d) == issue


def test_issue_from_dict_tolerates_legacy_payload():
    """老 checkpoint / 老 reviews 表数据没有新字段，反序列化必须不炸。"""
    from domain.review import Issue

    legacy = {"severity": "high", "point": "p", "suggestion": "s"}
    issue = Issue.from_dict(legacy)

    assert issue.needs_material is False
    assert issue.material_query == ""


def test_issue_defaults_are_rewrite_problems():
    """新建 Issue 默认不是补料问题——避免误把重写类问题转成检索请求。"""
    from domain.review import Issue

    issue = Issue(severity="medium", point="表述冗余")
    assert issue.needs_material is False
    assert issue.material_query == ""
    assert issue.to_dict()["needs_material"] is False
