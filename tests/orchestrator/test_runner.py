"""orchestrator/runner.py 单测：start → resume，abort → aborted。

策略：
- monkeypatch ``db.DB_PATH`` 与 ``orchestrator.runner.DB_PATH`` 指向 tmp_path
- 注入 stub agents（同 test_graph.py 风格）
- 注入显式 checkpointer_provider 把 saver 也指向同一 tmp_path

验证：
- start 走到第一闸门后 stage="outline_review"，workflow_runs 表有同名记录
- resume(approve) → 全程跑完 → stage="done"
- abort → workflow_runs.stage="aborted" + 推送 aborted 帧
"""

from __future__ import annotations

import asyncio
import re

import aiosqlite
import pytest
import pytest_asyncio
from contextlib import asynccontextmanager

import db as db_module
import orchestrator.runner as runner_module
from agents.zhang_heng import SpecParseResult
from db import init_db
from domain.proposal import BlockOutput
from domain.review import Finding, WorkflowRunRepository
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection
from orchestrator.checkpointer import checkpointer_from_path
from orchestrator.graph import GraphDeps
from orchestrator.runner import WorkflowRunner


# ─────────────────────────────────────────────
# stub agents（同 test_graph 风格）
# ─────────────────────────────────────────────

class _StubZhang:
    async def parse(self, file_source, *, suffix, filename, progress_callback=None):
        return SpecParseResult(
            doc_id="d1", doc_title="规范书", doc_summary="摘要",
            toc=[DomainSection(id="s1", level=1, title="技术方案",
                               raw_content="", special_marks=[])],
        )

    async def extract(self, toc, **kw):
        return {s.id: OutlineMatrixRow(block_id=s.id, title=s.title,
                                        requirement="r")
                for s in toc}


class _StubShenKuo:
    async def match(self, toc, outline_matrix, chunks):
        return {s.id: [] for s in toc}


class _StubZhugeLiang:
    async def generate(self, *, outline_matrix, materials,
                       regenerate_targets=None, emitter=None):
        results = {
            bid: BlockOutput(block_id=bid, kind="tech", content="正文",
                             outline="", sources=[])
            for bid in outline_matrix
        }
        return results, list(regenerate_targets or [])


class _StubReviewer:
    def __init__(self, name): self.name = name

    async def review(self, *, blocks, outline_matrix, emitter=None):
        return {bid: Finding(block_id=bid, agent=self.name, score=80)
                for bid in blocks}


async def _spec_loader(_pid):
    return (b"x", ".pdf", "spec.pdf")


def _deps_factory():
    return GraphDeps(
        zhang_heng=_StubZhang(),
        shen_kuo=_StubShenKuo(),
        zhuge_liang=_StubZhugeLiang(),
        wang_anshi=_StubReviewer("wang_anshi"),
        bao_zheng=_StubReviewer("bao_zheng"),
        spec_loader=_spec_loader,
    )


# ─────────────────────────────────────────────
# 共用 fixture：tmp DB 路径 + 已建表 + 已有 project
# ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def runner_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wf.db")

    # init schema once
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("测试",))
    await conn.commit()
    project_id = cursor.lastrowid
    await conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    @asynccontextmanager
    async def _saver():
        async with checkpointer_from_path(db_path) as s:
            yield s

    runner = WorkflowRunner(
        deps_factory=_deps_factory,
        checkpointer_provider=_saver,
    )
    yield runner, project_id, db_path


async def _wait_task(run, timeout=2.0):
    """等待 runner 当前后台 task 跑完一段（到下一个闸门）。"""
    if run.task is not None:
        try:
            await asyncio.wait_for(run.task, timeout=timeout)
        except asyncio.CancelledError:
            pass


# ─────────────────────────────────────────────
# tests
# ─────────────────────────────────────────────

async def test_start_pauses_at_outline_review(runner_env):
    runner, project_id, _ = runner_env
    tid = await runner.start(project_id, config={"tone": "official"})
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "outline_review"
    assert state["spec"]["doc_title"] == "规范书"

    # workflow_runs 表
    async with aiosqlite.connect(runner_env[2]) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT stage FROM workflow_runs WHERE thread_id = ?", (tid,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["stage"] == "outline_review"


async def test_full_run_to_done(runner_env):
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    # gate1 → gate2
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert state["stage"] == "materials_review"

    # gate2 → gate3
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert state["stage"] == "report_review"

    # gate3 approve → END
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT stage FROM workflow_runs WHERE thread_id = ?", (tid,),
        )
        row = await cur.fetchone()
    assert row["stage"] == "done"


async def test_abort_marks_aborted(runner_env):
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    await runner.abort(tid)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT stage FROM workflow_runs WHERE thread_id = ?", (tid,),
        )
        row = await cur.fetchone()
    assert row["stage"] == "aborted"


async def test_stream_yields_frames(runner_env):
    runner, project_id, _ = runner_env
    tid = await runner.start(project_id, config={})

    received: list[str] = []

    async def consumer():
        async for frame in runner.stream(tid):
            received.append(frame)

    consumer_task = asyncio.create_task(consumer())
    await _wait_task(runner._runs[tid])

    # gate3 approve → done → emitter 关闭 → stream 结束
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    await asyncio.wait_for(consumer_task, timeout=2.0)

    # 至少应该看到几个关键事件
    text = "\n".join(received)
    assert "gate_open" in text
    assert "checkpoint" in text
    assert "done" in text


async def test_unknown_thread_id_raises(runner_env):
    runner, _, _ = runner_env
    with pytest.raises(KeyError):
        await runner.resume("不存在", user_choice="approve")


async def _ready_workspace(runner_env):
    from db import get_db
    from services.block_store import create_block
    from orchestrator.graph import GATE_REPORT
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    matrix = dict(state["spec"]["outline_matrix"])
    matrix["s2"] = {**matrix["s1"], "block_id": "s2", "title": "保护章节"}
    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None)
        await graph.aupdate_state({"configurable": {"thread_id": tid}}, {
            "spec": {"outline_matrix": matrix},
            "proposal": {"blocks": {**state["proposal"]["blocks"],
                         "s2": {"block_id": "s2", "kind": "tech", "content": "保护原文"}}},
        }, as_node=GATE_REPORT)
    async with get_db() as db:
        await db.execute("DELETE FROM block_revisions")
        await db.execute("DELETE FROM blocks")
        await db.commit()
        for i, text in enumerate(["人工修订正文", "保护原文"], 1):
            await create_block(db, pid, f"s{i}", "content", 1, f"章节{i}", "", "", "r", "", "[]", i, content=text)
    return runner, tid


async def test_workspace_review_uses_saved_text_and_never_generates(runner_env):
    from db import get_db
    from services.block_store import list_blocks, list_revisions
    runner, tid = await _ready_workspace(runner_env)
    await runner.workspace_action(tid, ["s1"], "review")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert state["stage"] == "report_review"
    assert state["review"]["tech_contents"]["s1"] == "人工修订正文"
    assert state["review"]["compliance_contents"]["s1"] == "人工修订正文"
    assert state["proposal"]["blocks"]["s2"]["content"] == "保护原文"
    async with get_db() as db:
        rows = await list_blocks(db, runner_env[1])
        assert rows[0]["content"] == "人工修订正文"
        assert await list_revisions(db, rows[0]["id"]) == []


async def test_workspace_revise_protects_unselected_and_records_versions(runner_env):
    from db import get_db
    from services.block_store import list_blocks, list_revisions
    runner, tid = await _ready_workspace(runner_env)
    # Simulate process restart: only checkpoint survives.
    runner._runs.clear()
    await runner.workspace_action(tid, ["s1"], "revise")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert state["stage"] == "report_review"
    assert state["review"]["tech_contents"]["s1"] == "正文"
    async with get_db() as db:
        rows = await list_blocks(db, runner_env[1])
        assert [r["content"] for r in rows] == ["正文", "保护原文"]
        revisions = await list_revisions(db, rows[0]["id"])
        assert [r["content"] for r in revisions] == ["人工修订正文", "正文"]
        assert await list_revisions(db, rows[1]["id"]) == []


async def test_workspace_rejects_invalid_or_empty_chapters(runner_env):
    from db import get_db
    from services.block_store import list_blocks, update_block_content
    runner, tid = await _ready_workspace(runner_env)
    with pytest.raises(ValueError, match="有效章节"):
        await runner.workspace_action(tid, ["missing"], "revise")
    async with get_db() as db:
        rows = await list_blocks(db, runner_env[1])
        await update_block_content(db, rows[0]["id"], "")
    with pytest.raises(ValueError, match="尚无正文"):
        await runner.workspace_action(tid, ["s1"], "review")

async def test_workspace_review_stops_even_when_scores_fail(runner_env):
    runner, tid = await _ready_workspace(runner_env)
    deps = _deps_factory()
    class LowReviewer:
        async def review(self, *, blocks, outline_matrix, emitter=None):
            return {bid: Finding(block_id=bid, agent="test", score=40) for bid in blocks}
    class NoGeneration:
        async def generate(self, **kwargs):
            raise AssertionError("仅复审不得触发生成")
    deps.wang_anshi = LowReviewer()
    deps.bao_zheng = LowReviewer()
    deps.zhuge_liang = NoGeneration()
    runner._deps_factory = lambda: deps
    await runner.workspace_action(tid, ["s1"], "review")
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert state["stage"] == "report_review"
    assert state["review"]["tech_findings"]["s1"]["score"] == 40
    assert state["proposal"]["blocks"]["s1"]["content"] == "人工修订正文"
