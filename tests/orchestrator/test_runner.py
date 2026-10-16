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
    async def parse(self, sources, *, progress_callback=None):
        return SpecParseResult(
            doc_id="d1", doc_title="规范书", doc_summary="摘要",
            toc=[DomainSection(id="s1", level=1, title="技术方案",
                               raw_content="", special_marks=[])],
        )

    async def draft_outline(self, source_toc, *, instruction="", doc_summary="",
                            previous_toc=None, project_id=None, thread_id="", points_only=False):
        """stub 直接沿用规范书目录（等价于「模型不可用」的降级路径），
        这样现有用例断言的 block_id 仍是 s1，不受目录派生影响。"""
        from agents.zhang_heng import OutlineDraftResult
        return OutlineDraftResult(
            sections=[DomainSection(**s.to_dict()) for s in source_toc],
            error="stub 未派生",
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

    async def review(self, *, blocks, outline_matrix, emitter=None, chain_by_block=None):
        return {bid: Finding(block_id=bid, agent=self.name, score=80)
                for bid in blocks}


async def _spec_loader(_pid):
    return [(b"x", ".pdf", "spec.pdf")]


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


async def test_response_plans_reuse_parse_and_preserve_other_runs(runner_env):
    from services.split_answers import get_answers
    from services.block_store import list_blocks
    from db import get_db
    runner, pid, _ = runner_env
    source = await runner.start(pid, {})
    await _wait_task(runner._runs[source])
    before = await runner.state(source)
    async with get_db() as db:
        original_blocks = await list_blocks(db, pid, source)
    commerce = await runner.create_response_plan(source, 'commerce')
    await _wait_task(runner._runs[commerce])
    tech = await runner.create_response_plan(commerce, 'tech')
    await _wait_task(runner._runs[tech])
    assert len({source, commerce, tech}) == 3
    assert await runner.create_response_plan(tech, 'commerce') == commerce
    assert (await runner.state(source))['spec'] == before['spec']
    async with get_db() as db:
        assert await list_blocks(db, pid, source) == original_blocks
        for tid, kind in [(commerce, 'commerce'), (tech, 'tech')]:
            answers = await get_answers(db, pid, tid)
            assert answers['categories']['categories'] == [kind]
            assert answers['response_plan'] == {'type': kind, 'source_thread_id': source, 'confirmed': False}
            assert await list_blocks(db, pid, tid)


async def test_response_plan_rejects_mixed_categories(runner_env):
    runner, _, _ = runner_env
    with pytest.raises(ValueError, match='商务或技术'):
        await runner.create_response_plan('unknown', 'commerce,tech')


async def test_point_to_point_source_titles_and_final_position_are_locked(runner_env):
    runner, pid, _ = runner_env
    tid = await runner.start(pid, {})
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    toc = state['spec']['toc']
    toc[0]['chain'] = {'response_part':'point_to_point'}
    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        await graph.aupdate_state({'configurable':{'thread_id':tid}}, {'spec':{'toc':toc}})
    source = dict(block_id=toc[0]['id'],level=toc[0]['level'],title=toc[0]['title'])
    with pytest.raises(ValueError, match='保持原文'):
        await runner.update_outline_structure(tid, [{**source,'title':'改写原文'}])
    with pytest.raises(ValueError, match='固定在最后'):
        await runner.update_outline_structure(tid, [source,dict(block_id='new',level=1,title='尾部插入评分项')])


async def test_response_confirm_and_export_use_explicit_response_run(runner_env, monkeypatch):
    from routes import workflow as workflow_route
    from routes.export import export_docx
    from services.split_answers import get_answers
    from urllib.parse import unquote
    from db import get_db
    from fastapi import HTTPException
    runner, pid, _ = runner_env
    monkeypatch.setattr(workflow_route, '_runner', runner)
    source = await runner.start(pid, {})
    await _wait_task(runner._runs[source])
    with pytest.raises(HTTPException, match='') as invalid:
        await workflow_route.confirm_response_plan(source)
    assert invalid.value.status_code == 409
    commerce = await runner.create_response_plan(source, 'commerce')
    await _wait_task(runner._runs[commerce])
    assert (await workflow_route.confirm_response_plan(commerce))['confirmed']
    async with get_db() as db:
        plan = (await get_answers(db, pid, commerce))['response_plan']
        assert plan['confirmed']
        await db.execute('UPDATE blocks SET content=?, status=? WHERE run_thread_id=?', ('商务正文', 'done', commerce))
        await db.commit()
    response = await export_docx(pid, package_id=None, thread_id=commerce)
    assert '商务应答' in unquote(response.headers['content-disposition'])
    state = await runner.state(commerce)
    await runner.update_outline_structure(commerce, [
        {'block_id': s['id'], 'level': s['level'], 'title': s['title'] + '修订'}
        for s in state['spec']['toc']])
    async with get_db() as db:
        assert not (await get_answers(db, pid, commerce))['response_plan']['confirmed']
    with pytest.raises(HTTPException) as wrong_project:
        await export_docx(pid + 100, package_id=None, thread_id=commerce)
    assert wrong_project.value.status_code == 404


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
        # workspace_action 按 run 归属取行，这些行要归到本次 run 才读得到
        await db.execute(
            "UPDATE blocks SET run_thread_id = ? WHERE project_id = ?", (tid, pid),
        )
        await db.commit()
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
        async def review(self, *, blocks, outline_matrix, emitter=None, chain_by_block=None):
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


# ─────────────────────────────────────────────
# redraft_outline：整版重出目录
# ─────────────────────────────────────────────

class _RecordingZhang(_StubZhang):
    """每次 draft_outline 换一份目录，用来证明「整版替换」而非原地保留。"""

    def __init__(self):
        self.draft_calls = []

    async def draft_outline(self, source_toc, *, instruction="", doc_summary="",
                            previous_toc=None, project_id=None, thread_id="", points_only=False):
        from agents.zhang_heng import OutlineDraftResult
        self.draft_calls.append({
            "instruction": instruction,
            "previous_titles": (
                [s.title for s in previous_toc] if previous_toc else None
            ),
        })
        n = len(self.draft_calls)
        return OutlineDraftResult(
            sections=[
                DomainSection(id=f"v{n}-{i}", level=1, title=f"第{n}版第{i}章",
                              raw_content="", special_marks=[])
                for i in range(1, n + 1)
            ],
            error="",
        )


async def test_redraft_outline_replaces_toc_and_bumps_revision(runner_env):
    runner, pid, _ = runner_env
    zhang = _RecordingZhang()
    deps = _deps_factory()
    deps.zhang_heng = zhang
    runner._deps_factory = lambda: deps

    tid = await runner.start(pid, config={"outline_instruction": "第一版要求"})
    await _wait_task(runner._runs[tid])
    state = await runner.state(tid)
    assert [s["id"] for s in state["spec"]["toc"]] == ["v1-1"]
    assert state["spec"]["outline_revision"] == 1
    assert state["stage"] == "outline_review"

    old_emitter = runner._runs[tid].emitter
    await runner.redraft_outline(tid, "第二版要求：拆到三级")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    # 整版替换：旧目录一个不留，版本号自增，回到同一个闸门
    assert [s["id"] for s in state["spec"]["toc"]] == ["v2-1", "v2-2"]
    assert state["spec"]["outline_revision"] == 2
    assert state["config"]["outline_instruction"] == "第二版要求：拆到三级"
    assert state["stage"] == "outline_review"
    # 旧 emitter 可能已 close，必须换新的，否则前端收不到新一版的帧
    assert runner._runs[tid].emitter is not old_emitter

    # 第二轮把上一版目录喂回模型，迭代才有连续性
    assert zhang.draft_calls[0]["previous_titles"] is None
    assert zhang.draft_calls[1]["instruction"] == "第二版要求：拆到三级"
    assert zhang.draft_calls[1]["previous_titles"] == ["第1版第1章"]


async def test_outline_gate_stops_before_extract(runner_env):
    """点「提炼大纲」只拆目录：停在闸门1 时矩阵还是空的，放行后才提炼。"""
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "outline_review"
    assert state["spec"]["toc"], "目录应已派生"
    assert state["spec"]["outline_matrix"] == {}, "闸门1 之前不该跑逐节提炼"

    # 放行（前端「进入匹配」走的就是这条）→ extract 才跑
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "materials_review"
    assert state["spec"]["outline_matrix"], "放行后 extract 应已产出矩阵"


async def test_rerun_match_skips_extract(runner_env, monkeypatch):
    """「重新匹配」不得重跑逐节提炼。

    图改成「闸门1 先于 extract」之后，rerun_match 的 as_node 必须跟着从 GATE_OUTLINE
    挪到 NODE_EXTRACT —— 停在 GATE_OUTLINE 的 next 已经是 extract 了，再拨回去等于
    每次重匹配白烧 N 次模型调用。
    """
    runner, pid, _ = runner_env
    calls: list[int] = []
    original = _StubZhang.extract

    async def _counting_extract(self, toc, **kw):
        calls.append(1)
        return await original(self, toc, **kw)

    monkeypatch.setattr(_StubZhang, "extract", _counting_extract)

    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])
    assert calls == [], "闸门1 之前不该提炼"

    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    assert len(calls) == 1, "放行闸门1 时提炼一次"
    assert (await runner.state(tid))["stage"] == "materials_review"

    await runner.rerun_match(tid)
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "materials_review"
    assert len(calls) == 1, "重新匹配不该重跑提炼"


async def test_redraft_outline_clears_outline_matrix(runner_env):
    """目录换了，上一版的 8 字段要求必须整体清空，否则闸门 1 会显示错章节的要求。"""
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])
    # 先放行一次让 extract 把矩阵填上 —— 新版图里闸门1 停在提炼之前
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    before = await runner.state(tid)
    assert before["spec"]["outline_matrix"], "首轮 extract 应已产出矩阵"

    await runner.redraft_outline(tid, "重来")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "outline_review", "重出后应回到闸门1 等用户确认"
    assert state["spec"]["outline_matrix"] == {}, "上一版的要求必须清掉，否则会串章节"


async def test_redraft_outline_keeps_source_toc(runner_env):
    """source_toc 是 grounding 输入，重出不得把它清掉。"""
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])

    await runner.redraft_outline(tid, "换要求")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert [s["title"] for s in state["spec"]["source_toc"]] == ["技术方案"]


async def test_redraft_outline_unknown_thread_raises_key_error(runner_env):
    runner, _, _ = runner_env
    with pytest.raises(KeyError):
        await runner.redraft_outline("不存在", "随便")


async def test_redraft_outline_rejects_wrong_stage(runner_env):
    """闸门 1 之前/之后都不能重出：还没派生过，或已经进生成阶段。"""
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])

    # 推进到生成阶段（gate1 → match → gate2）
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        await graph.aupdate_state(
            {"configurable": {"thread_id": tid}}, {"stage": "generating"},
        )

    # 报错必须能指导下一步：说清"目录不能再改"并指向「重新提炼」
    # （改要求文件不靠重出目录 —— 重出只重跑派生，用的是 checkpoint 里的 source_toc）
    with pytest.raises(ValueError, match="重新提炼"):
        await runner.redraft_outline(tid, "来不及了")


async def test_redraft_outline_rejects_while_running(runner_env):
    runner, pid, _ = runner_env
    tid = await runner.start(pid, config={})
    await _wait_task(runner._runs[tid])

    runner._runs[tid].task = asyncio.create_task(asyncio.sleep(30))
    try:
        with pytest.raises(ValueError, match="仍在运行"):
            await runner.redraft_outline(tid, "并发调用")
    finally:
        runner._runs[tid].task.cancel()


# ─────────────────────────────────────────────
# 「应标要求」全量文件 → 大纲派生（真实张衡 + 真实 loader，mock LLM）
# ─────────────────────────────────────────────

def _two_section_docx(title: str):
    """两份章节不同的最小 docx，用来验证多文档拼接。"""
    import io

    from docx import Document

    doc = Document()
    doc.add_heading(f"第一章 {title}", level=1)
    doc.add_paragraph(f"{title}的正文说明。")
    doc.add_heading(f"第二章 {title}细则", level=1)
    doc.add_paragraph("细则内容。")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


async def test_outline_derives_from_every_requirement_file(tmp_path, monkeypatch):
    """项目里一份 role='spec' 都没有，只有主招标文件 + 评分表，仍要走到闸门 1。

    改造前 spec_loader 只认 role='spec'，这种项目会在 parse 节点
    抛 FileNotFoundError（「未上传规范书」）整条工作流出错。
    """
    import main as main_module
    from agents.zhang_heng import ZhangHengAgent
    from services.config_store import LLMConfig

    monkeypatch.setenv("LLM_MODE", "mock")

    db_path = str(tmp_path / "multi_req.db")
    data_dir = tmp_path / "data"
    (data_dir / "uploads" / "1").mkdir(parents=True)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("多要求文件",))
    project_id = cursor.lastrowid
    for role, name in (("main_rfp", "招标文件.docx"), ("scoring", "评分表.docx")):
        (data_dir / "uploads" / str(project_id) / name).write_bytes(
            _two_section_docx(name).getvalue()
        )
        await conn.execute(
            "INSERT INTO materials (project_id, filename, file_path, role) "
            "VALUES (?, ?, ?, ?)",
            (project_id, name, f"uploads/{project_id}/{name}", role),
        )
    await conn.commit()
    await conn.close()

    def _fake_configs():
        return ([LLMConfig(
            provider="openai", api_key="sk-fake",
            base_url="https://example.invalid/v1", model="gpt-test",
        )], 0)

    def deps_factory():
        return GraphDeps(
            zhang_heng=ZhangHengAgent(configs_provider=_fake_configs),
            shen_kuo=_StubShenKuo(),
            zhuge_liang=_StubZhugeLiang(),
            wang_anshi=_StubReviewer("wang_anshi"),
            bao_zheng=_StubReviewer("bao_zheng"),
            spec_loader=main_module._load_spec_for_project,
        )

    @asynccontextmanager
    async def _saver():
        async with checkpointer_from_path(db_path) as s:
            yield s

    runner = WorkflowRunner(deps_factory=deps_factory, checkpointer_provider=_saver)
    tid = await runner.start(project_id, config={"outline_instruction": "按评分项拆章"})
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "outline_review"
    # 两份要求文件的目录都进了 source_toc（各 2 章）
    assert len(state["spec"]["source_toc"]) == 4
    # 派生目录是模型产出（mock fixture 出 11 个节点），不是原目录的镜像
    assert len(state["spec"]["toc"]) == 11
    assert state["spec"]["outline_revision"] == 1


# ─────────────────────────────────────────────
# LLM 用量归属：图执行期间设置 run_context
# ─────────────────────────────────────────────

async def test_run_context_attributes_llm_usage_to_run(runner_env, monkeypatch):
    """图执行期间 LLM 用量归属到本次运行 —— 「按运行算费用」的前置条件。

    用假 graph 捕获 invoke 时刻的上下文，绕开「mock 模式不记账」的限制：
    要验的是 runner 有没有在 invoke 之前把 contextvar 设好，不是记账本身。
    """
    from infra.llm import usage as llm_usage

    runner, project_id, _ = runner_env
    seen: list[dict] = []

    class _Snap:
        next = ()

        def __init__(self, values):
            self.values = values

    class _FakeGraph:
        async def ainvoke(self, *a, **kw):
            seen.append(dict(llm_usage.current_run()))

        async def aupdate_state(self, *a, **kw):
            seen.append(dict(llm_usage.current_run()))

        async def aget_state(self, *a, **kw):
            return _Snap({"project_id": project_id, "stage": "outline_review"})

    monkeypatch.setattr(runner, "_build", lambda *a, **kw: _FakeGraph())

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    assert seen, "初次跑应至少 invoke 一次"
    assert seen[-1] == {"project_id": project_id, "thread_id": tid}

    seen.clear()
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    assert seen, "续跑应至少 invoke 一次"
    assert all(s == {"project_id": project_id, "thread_id": tid} for s in seen), seen


async def test_run_context_cleared_after_run(runner_env, monkeypatch):
    """跑完退出 with 块后归属清空，不泄漏给后续无关调用。"""
    from infra.llm import usage as llm_usage

    runner, project_id, _ = runner_env

    class _Snap:
        next = ()

        def __init__(self, values):
            self.values = values

    class _FakeGraph:
        async def ainvoke(self, *a, **kw):
            pass

        async def aget_state(self, *a, **kw):
            return _Snap({"project_id": project_id, "stage": "idle"})

    monkeypatch.setattr(runner, "_build", lambda *a, **kw: _FakeGraph())

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    assert llm_usage.current_run() == {}


# ─────────────────────────────────────────────
# 目录结构编辑（闸门 1 增删改 / 排序）
# ─────────────────────────────────────────────

async def test_update_outline_structure_rewrites_state_and_blocks(runner_env):
    """结构编辑要同时改 blocks 表与图里的 spec.toc —— extract 逐节提炼读的是后者。"""
    import aiosqlite

    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    original = state["spec"]["toc"][0]

    # ① 新增两节 + 把原节点降为子级并改名 + 放到中间（一次请求覆盖增/改/排序）
    nodes = [
        {"block_id": "nuser1", "level": 1, "title": "用户新增一章"},
        {"block_id": original["id"], "level": 2, "title": original["title"] + "（改）"},
        {"block_id": "nuser2", "level": 2, "title": "用户新增子章"},
    ]
    stats = await runner.update_outline_structure(tid, nodes)

    assert stats["created"] == 2        # 新增节点建了占位行
    assert stats["updated"] == 1        # 已有节点只改 title/level/order_idx
    assert stats["removed"] == 0

    new_state = await runner.state(tid)
    assert [d["id"] for d in new_state["spec"]["toc"]] == ["nuser1", original["id"], "nuser2"]
    assert new_state["spec"]["toc"][1]["title"] == original["title"] + "（改）"
    assert new_state["spec"]["toc"][1]["level"] == 2
    # source_toc 是只读溯源（grounding 用），结构编辑不能动它
    assert new_state["spec"]["source_toc"] == state["spec"]["source_toc"]

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT block_id, order_idx, level FROM blocks WHERE project_id = ? ORDER BY order_idx",
            (project_id,),
        )
        rows = await cur.fetchall()
    assert [(r["block_id"], r["order_idx"], r["level"]) for r in rows] == [
        ("nuser1", 0, 1), (original["id"], 1, 2), ("nuser2", 2, 2),
    ]

    # ② 删掉一节（无正文）→ 行被清理
    stats2 = await runner.update_outline_structure(tid, nodes[:2])
    assert stats2["removed"] == 1
    assert [d["id"] for d in (await runner.state(tid))["spec"]["toc"]] == ["nuser1", original["id"]]


async def test_update_outline_structure_rejects_busy_stage(runner_env, monkeypatch):
    """运行中的 stage 不接受结构编辑：一边写一边被读会打架。"""
    runner, project_id, _ = runner_env

    async def _busy(_tid):
        return {"project_id": project_id, "stage": "extracting", "spec": {}}

    monkeypatch.setattr(runner, "state", _busy)

    with pytest.raises(ValueError, match="仍在运行"):
        await runner.update_outline_structure(
            "tid-x", [{"block_id": "s1", "level": 1, "title": "A"}],
        )


async def test_update_outline_structure_rejects_invalid(runner_env, monkeypatch):
    """空目录 / 空标题在 runner 层就被挡下（路由再转成 400）。"""
    from domain.spec.structure import StructureError

    runner, project_id, _ = runner_env

    async def _state(_tid):
        return {"project_id": project_id, "stage": "outline_review", "spec": {}}

    monkeypatch.setattr(runner, "state", _state)

    with pytest.raises(StructureError):
        await runner.update_outline_structure("tid-x", [])
    with pytest.raises(StructureError):
        await runner.update_outline_structure(
            "tid-x", [{"block_id": "s1", "level": 1, "title": "   "}],
        )


async def test_redraft_outline_rejects_aborted_thread(runner_env, monkeypatch):
    """已作废的 run 不能重出目录：抛专用异常（路由层转成可判别的 workflow_aborted）。"""
    from orchestrator.runner import WorkflowAbortedError

    runner, project_id, _ = runner_env

    async def _aborted(_tid):
        return {"project_id": project_id, "stage": "aborted", "spec": {}}

    monkeypatch.setattr(runner, "state", _aborted)

    with pytest.raises(WorkflowAbortedError):
        await runner.redraft_outline("tid-x", "重出")


async def test_outline_structure_rejects_aborted_thread(runner_env, monkeypatch):
    """作废的 run 也不接受目录结构编辑（它不会再往下跑，改了只会让页面与库发散）。"""
    from orchestrator.runner import WorkflowAbortedError

    runner, project_id, _ = runner_env

    async def _aborted(_tid):
        return {"project_id": project_id, "stage": "aborted", "spec": {"toc": []}}

    monkeypatch.setattr(runner, "state", _aborted)

    with pytest.raises(WorkflowAbortedError):
        await runner.update_outline_structure(
            "tid-x", [{"block_id": "s1", "level": 1, "title": "A"}],
        )


# ─────────────────────────────────────────────
# 派生失败：抛异常 → 运行落 failed 终态（不再降级沿用原始目录）
# ─────────────────────────────────────────────

async def test_graph_failure_marks_run_failed(runner_env, monkeypatch):
    """目录派生失败 → 运行标 failed（不是停在「跑着」的阶段）。

    停在 parsing 的话，刷新页面时前端会以为它还在跑、继续等帧（SSE 无回放），
    于是变成「卡住」。标 failed 之后 boot / 兜底轮询都会立刻按终态处理并给重试入口。
    """
    import aiosqlite
    from agents.zhang_heng import OutlineDraftError

    runner, project_id, db_path = runner_env

    async def _boom(self, *a, **kw):
        raise OutlineDraftError("目录派生失败：模型调用失败：Connection error.")

    monkeypatch.setattr(_StubZhang, "draft_outline", _boom)

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state.get("stage") == "failed"
    errors = state.get("errors") or []
    assert errors and "Connection error" in str(errors[-1].get("message", ""))

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (await conn.execute(
            "SELECT stage FROM workflow_runs WHERE thread_id = ?", (tid,),
        )).fetchone()
    assert row["stage"] == "failed"


async def test_redraft_outline_allowed_on_failed_stage(runner_env, monkeypatch):
    """failed 之后必须还能重出目录（要求文件已解析好，重试派生是用户的唯一自救入口）。"""
    runner, project_id, _ = runner_env

    async def _generating(_tid):
        return {"project_id": project_id, "stage": "generating", "spec": {}}

    monkeypatch.setattr(runner, "state", _generating)
    with pytest.raises(ValueError, match="重新提炼"):
        await runner.redraft_outline("tid-x", "")

    async def _failed(_tid):
        return {"project_id": project_id, "stage": "failed", "spec": {}}

    monkeypatch.setattr(runner, "state", _failed)
    await runner.redraft_outline("tid-x", "")     # 不该因阶段被拒
    await _wait_task(runner._runs["tid-x"])


# ─────────────────────────────────────────────
# 运行中互斥 + 运行期 stage（提炼/匹配要跑十几分钟，不能靠节点结束时才写 stage）
# ─────────────────────────────────────────────

async def test_resume_rejected_while_task_running(runner_env):
    """同一 thread 已有后台 task 时，resume 必须直接拒绝。

    没有这层保护，刷新页面 /开第二个标签页重复点「确认目录」会并发跑第二遍逐节提炼
    （每章一次模型调用，费用翻倍、SSE 帧双份）。
    """
    runner, project_id, _ = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])          # 停在闸门 1

    async def _still_running():
        await asyncio.sleep(5)

    run = runner._runs[tid]
    original = run.task
    run.task = asyncio.create_task(_still_running())
    try:
        with pytest.raises(ValueError, match="仍在运行"):
            await runner.resume(tid, user_choice="approve")
    finally:
        run.task.cancel()
        run.task = original

    # 任务跑完后同一个 resume 正常放行（保护不能把正常路径也堵死）
    await runner.resume(tid, user_choice="approve")
    await _wait_task(run)


class _FakeSnap:
    def __init__(self, nxt):
        self.next = nxt


class _FakeGraph:
    """只实现 _mark_next_phase 要用的 aget_state（返回下一个节点）。"""

    def __init__(self, nxt):
        self._next = nxt

    async def aget_state(self, config):
        return _FakeSnap(self._next)


async def _db_stage(db_path, tid):
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (await conn.execute(
            "SELECT stage FROM workflow_runs WHERE thread_id = ?", (tid,),
        )).fetchone()
    return row["stage"] if row else None


async def _drain_frames(runner, tid, limit=60, timeout=0.5):
    """把 emitter 队列里的帧取空（含上一段跑的残余帧），返回按序的列表。"""
    frames: list[str] = []
    agen = runner.stream(tid)
    try:
        for _ in range(limit):
            try:
                frames.append(await asyncio.wait_for(agen.__anext__(), timeout))
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
    finally:
        await agen.aclose()
    return frames


async def test_mark_next_phase_writes_runtime_stage(runner_env):
    """续跑前按下一个节点写运行期 stage：extract → extracting / match → matching。

    节点只能在**结束时**返回 stage，所以「正在提炼/生成」的这十几分钟必须由 runner 先写
    workflow_runs —— 否则页面刷新读到的仍是上一版 stage（实测：检查点 generating，
    表里还写着 paused，界面显示的阶段是错的）。
    这份映射是**单一来源**（orchestrator.runner._NEXT_PHASE_STAGE + next_phase_stage）。
    """
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])
    assert await _db_stage(db_path, tid) == "outline_review"

    config = {"configurable": {"thread_id": tid}}
    from orchestrator.graph import NODE_EXTRACT, NODE_MATCH, NODE_PARSE

    await runner._mark_next_phase(_FakeGraph((NODE_EXTRACT,)), config, tid)
    assert await _db_stage(db_path, tid) == "extracting"
    # 同时要推一帧 checkpoint，页面接上后能立刻校准进度条
    frames = await _drain_frames(runner, tid)
    checkpoints = [f for f in frames if "event: checkpoint" in f]
    assert any('"stage": "extracting"' in f for f in checkpoints), checkpoints

    await runner._mark_next_phase(_FakeGraph((NODE_MATCH,)), config, tid)
    assert await _db_stage(db_path, tid) == "matching"

    # parse 也是长阶段（解析 164 页要几分钟）→ 同样要写 parsing（旧实现只认 extract/match，
    # 于是"回修生成"期间表里一直停在上一版 stage）
    await runner._mark_next_phase(_FakeGraph((NODE_PARSE,)), config, tid)
    assert await _db_stage(db_path, tid) == "parsing"
    # 评审循环里的任意节点都显示 reviewing
    from orchestrator.graph import NODE_TECH_REVIEW, NODE_AGGREGATE
    await runner._mark_next_phase(_FakeGraph((NODE_TECH_REVIEW,)), config, tid)
    assert await _db_stage(db_path, tid) == "reviewing"
    # 生成与评审同时出现在 next 时，取图上更早的 generating（不能按字典序选 reviewing）
    await runner._mark_next_phase(_FakeGraph((NODE_AGGREGATE, "zhuge_liang_generate")), config, tid)
    assert await _db_stage(db_path, tid) == "generating"
    # 读不到 next（图已收尾）不动 stage —— 保持上一步写下的值
    await runner._mark_next_phase(_FakeGraph(()), config, tid)
    assert await _db_stage(db_path, tid) == "generating"


async def test_resume_marks_extracting_during_extract(runner_env, monkeypatch):
    """闸门 1 放行后、提炼真正跑起来之前，workflow_runs 已被标成 extracting。"""
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    seen: list[str] = []
    real = runner._mark_next_phase

    async def _spy(graph, config, thread_id):
        await real(graph, config, thread_id)
        seen.append(await _db_stage(db_path, thread_id))

    monkeypatch.setattr(runner, "_mark_next_phase", _spy)
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    assert seen == ["extracting"]


async def test_resume_approve_clears_cancel_and_aborted_stage(runner_env):
    """取消之后「确认 / 继续」必须能解冻 —— 这是「取消后重新开始、白烧 token」的根因之一。

    abort 只在 checkpoint 写 cancel_requested=True、把 workflow_runs 写 'aborted'；
    此后任何 resume(approve) 若不清理它，extract 节点开头的 _check_cancel 会立刻再抛
    WorkflowCancelled（用户看到「点了确认什么都没发生」），只能退化成「重新提炼」——
    重新解析 + 重新派生目录 + 全量重算。前端还需要保留 thread 引用才能复用这条 run。
    """
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])                  # 停在闸门 1

    await runner.abort(tid)
    assert (await runner.state(tid))["cancel_requested"] is True
    assert await _db_stage(db_path, tid) == "aborted"
    assert runner._runs[tid].cancel_flag is True

    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["cancel_requested"] is False, "放行后必须清掉取消标记，否则提炼会立刻再中断"
    assert state["stage"] == "materials_review"           # 全链路真的跑下去了
    assert await _db_stage(db_path, tid) == "materials_review"
    assert runner._runs[tid].cancel_flag is False


async def test_resume_extract_reenters_extract_and_skips_done(runner_env, monkeypatch):
    """取消后「继续提炼」：复用同一条 run，只补没做完的章节，且不重新派生目录。

    图在取消时已收尾（next=()），普通 ainvoke(None) 是空跑 —— resume_extract 借
    闸门 1 的出边把指针拨回 extract 之前，extract 自己按 status='outline_done'
    跳过已完成的章节（skip_ids）。这是"取消后重新开始、白烧 token"的正面锚点。
    """
    runner, project_id, db_path = runner_env

    import inspect as _inspect

    seen: list[list[str]] = []

    async def _spy(self, toc, *, on_section=None, on_section_start=None, should_cancel=None):
        """按真实 agent 的方式逐节回调：测试替身原来直接返回结果、不触发 on_section，
        于是 blocks 表里根本没有 outline_done 行，skip_ids 永远是空的（测不出跳过）。"""
        seen.append([s.id for s in toc])
        out = {}
        for s in toc:
            if should_cancel and should_cancel():
                break
            row = OutlineMatrixRow(block_id=s.id, title=s.title, requirement="r")
            out[s.id] = row
            if on_section:
                rv = on_section(s, row)
                if _inspect.isawaitable(rv):
                    await rv
        return out

    monkeypatch.setattr(_StubZhang, "extract", _spy)

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    # 第一轮：闸门 1 放行 → 全部章节提炼完（stub 立刻返回）
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])
    assert seen and seen[0] == ["s1"], seen      # stub 目录只有一节，先提炼它
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (await conn.execute(
            "SELECT status FROM blocks WHERE block_id = 's1'")).fetchone()
    assert row["status"] == "outline_done", "逐节落库是跳过的前提（skip_ids 读这张表）"

    before = await runner.state(tid)
    toc_ids = [s["id"] for s in before["spec"]["toc"]]

    # 再跑一次「继续提炼」：全部章节都已是 outline_done → 一节都不该再调用模型
    await runner.resume_extract(tid)
    await _wait_task(runner._runs[tid])

    assert seen[-1] == [], f"已提炼的章节不该重复调用模型，实际收到 {seen[-1]}"
    after = await runner.state(tid)
    # 目录（用户审过的那版）原样保留：没有重新派生
    assert [s["id"] for s in after["spec"]["toc"]] == toc_ids
    assert after["cancel_requested"] is False
    assert await _db_stage(db_path, tid) != "aborted"


async def test_sync_stage_keeps_paused_when_graph_ended_for_pause(runner_env):
    """graph_done 不等于「跑完」：提炼被取消时图也是收尾，但 stage 是 paused。

    标成 'done' 会让页面显示「提炼完成」而其实还有章节没提炼。
    """
    runner, project_id, db_path = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])

    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        await graph.aupdate_state(
            {"configurable": {"thread_id": tid}},
            {"stage": "paused"},
        )
    await runner._sync_stage(tid, graph_done=True)
    assert await _db_stage(db_path, tid) == "paused"

    # **闸门态也不等于跑完**：report_review 表示"在等用户确认报告"，
    # 标成 done 会让页面显示完成（实测出现过：表里 done，检查点停在报告闸门）。
    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        await graph.aupdate_state(
            {"configurable": {"thread_id": tid}},
            {"stage": "report_review"},
        )
    await runner._sync_stage(tid, graph_done=True)
    assert await _db_stage(db_path, tid) == "report_review"

    # 正常跑完（既不是暂停、也不是闸门）仍然收尾为 done
    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        await graph.aupdate_state(
            {"configurable": {"thread_id": tid}},
            {"stage": "reviewing"},
        )
    await runner._sync_stage(tid, graph_done=True)
    assert await _db_stage(db_path, tid) == "done"


async def test_resume_extract_after_paused_extract_runs_only_remaining(runner_env, monkeypatch):
    """暂停收尾（图 next 为空）后再点确认 —— 这正是"点了没反应"的那个 case。

    取消 = 优雅暂停：extract 节点返回 stage='paused'，图随即收尾。此时普通
    resume(approve) 的 ainvoke(None) 是**空跑**（实测点击后 0 次模型调用、进度不动），
    必须走 resume_extract（as_node=GATE_OUTLINE 把指针拨回 extract 之前）；而且只能
    跑剩下的章节，已完成的既不重跑也不能被跳过成"啥也没干"。
    """
    runner, project_id, db_path = runner_env

    seen: list[list[str]] = []
    cancel_after = {"n": 1}          # 第 1 节跑完就让 _Run.cancel_flag 为真

    async def _spy(self, toc, *, on_section=None, on_section_start=None, should_cancel=None):
        import inspect as _inspect
        seen.append([s.id for s in toc])
        out = {}
        for i, s in enumerate(toc):
            if should_cancel and should_cancel():
                break
            row = OutlineMatrixRow(block_id=s.id, title=s.title, requirement="r")
            out[s.id] = row
            if on_section:
                rv = on_section(s, row)
                if _inspect.isawaitable(rv):
                    await rv
            if i + 1 >= cancel_after["n"]:
                cancel_after["real"] = True      # 触发下一节的取消检查
                runner._runs[tid].cancel_flag = True
        return out

    # 先把 stub 目录做成 3 节
    original_parse = _StubZhang.parse

    async def _parse3(self, sources, *, progress_callback=None):
        from agents.zhang_heng import SpecParseResult
        return SpecParseResult(
            doc_id="d1", doc_title="规范书", doc_summary="摘要",
            toc=[DomainSection(id=f"s{i}", level=1, title=f"章节{i}", raw_content="x",
                               special_marks=[]) for i in (1, 2, 3)],
        )

    monkeypatch.setattr(_StubZhang, "extract", _spy)
    monkeypatch.setattr(_StubZhang, "parse", _parse3)

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])
    assert len((await runner.state(tid))["spec"]["toc"]) == 3

    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    state = await runner.state(tid)
    assert state["stage"] == "paused", "取消后应停在 paused（而不是 done/outline_review）"
    assert await _db_stage(db_path, tid) == "paused"
    done_ids = [s["id"] for s in state["spec"]["toc"]
                if (state["spec"]["outline_matrix"].get(s["id"]) or {}).get("requirement")]
    assert done_ids == ["s1"], done_ids

    # 图已收尾 → 普通 resume 是空跑（这是"点了没反应"的根因），先锚住这个事实
    import orchestrator.runner as _runner_mod
    snap_next = None
    async with runner._checkpointer_provider() as saver:
        g = runner._build(saver, None, thread_id=tid)
        snap_next = (await g.aget_state({"configurable": {"thread_id": tid}})).next
    assert snap_next == (), f"暂停收尾后 next 应为空，实际 {snap_next}"

    # 再点确认 = resume_extract：只跑剩下的 s2/s3
    before_calls = len(seen)
    runner._runs[tid].cancel_flag = False
    await runner.resume_extract(tid)
    await _wait_task(runner._runs[tid])

    assert len(seen) > before_calls, "resume_extract 必须真的再次进入 extract（不是空跑）"
    assert seen[-1] == ["s2", "s3"], f"只该跑剩余章节，实际 {seen[-1]}"
    assert (await runner.state(tid))["cancel_requested"] is False


async def test_failed_section_is_retried_but_no_source_is_skipped(runner_env, monkeypatch):
    """失败章节必须能重试；无原文章节跳过即可。

    以前两者落库后都是 status='outline_done'（8 字段全空），无法区分 —— 界面把失败
    当成"已提炼"，页面显示「提炼完成」而字段是空的，用户也没法重试。
    """
    runner, project_id, db_path = runner_env
    import inspect as _inspect

    rounds: list[list[str]] = []

    async def _parse3(self, sources, *, progress_callback=None):
        from agents.zhang_heng import SpecParseResult
        return SpecParseResult(
            doc_id="d1", doc_title="规范书", doc_summary="摘要",
            toc=[DomainSection(id=f"s{i}", level=1, title=f"章节{i}", raw_content="x",
                               special_marks=[]) for i in (1, 2, 3)],
        )

    async def _extract(self, toc, *, on_section=None, on_section_start=None, should_cancel=None):
        rounds.append([s.id for s in toc])
        out = {}
        for s in toc:
            if s.id == "s2":
                row = OutlineMatrixRow(block_id=s.id, title=s.title,
                                       error="全部 API 失败：测试", error_kind="failed")
            elif s.id == "s3":
                row = OutlineMatrixRow(block_id=s.id, title=s.title,
                                       error="本章节没有可提炼的原文", error_kind="no_source")
            else:
                row = OutlineMatrixRow(block_id=s.id, title=s.title, requirement="r")
            out[s.id] = row
            if on_section:
                rv = on_section(s, row)
                if _inspect.isawaitable(rv):
                    await rv
        return out

    monkeypatch.setattr(_StubZhang, "parse", _parse3)
    monkeypatch.setattr(_StubZhang, "extract", _extract)

    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = {r["block_id"]: dict(r) for r in await (await conn.execute(
            "SELECT block_id, status, error, error_kind FROM blocks ORDER BY order_idx")).fetchall()}
    assert rows["s1"]["error_kind"] in ("", None)
    assert rows["s2"]["error_kind"] == "failed" and "全部 API 失败" in (rows["s2"]["error"] or "")
    assert rows["s3"]["error_kind"] == "no_source"

    # 失败要写进 run 级 errors（刷新后仍能看到信号）
    errs = (await runner.state(tid)).get("errors") or []
    assert any("提炼失败" in (e.get("message") or "") for e in errs), errs

    # 再「继续提炼」：只重试失败的那节（无原文/已成功的都跳过）
    rounds.clear()
    await runner.resume_extract(tid)
    await _wait_task(runner._runs[tid])
    assert rounds and rounds[-1] == ["s2"], f"只该重试失败章节，实际 {rounds}"


async def test_structure_edit_prunes_matrix(runner_env):
    """删掉章节后，spec.outline_matrix 里的对应条目要一起消失。

    矩阵是整版替换的；残留条目会被 generate 的"toc 之外兜底"当成章节生成正文 ——
    用户删掉的章节又长回来。
    """
    runner, project_id, _ = runner_env
    tid = await runner.start(project_id, config={})
    await _wait_task(runner._runs[tid])
    await runner.resume(tid, user_choice="approve")
    await _wait_task(runner._runs[tid])          # stub 只有 s1，矩阵里就是它

    # 再补一条"目录里没有"的矩阵条目（模拟历史残留）
    async with runner._checkpointer_provider() as saver:
        graph = runner._build(saver, None, thread_id=tid)
        cfg = {"configurable": {"thread_id": tid}}
        snap = await graph.aget_state(cfg)
        matrix = dict((snap.values.get("spec") or {}).get("outline_matrix") or {})
        matrix["s-ghost"] = {"block_id": "s-ghost", "title": "已删除的章节", "requirement": "r"}
        await graph.aupdate_state(cfg, {"spec": {"outline_matrix": matrix}})

    # 目录结构编辑：只保留 s1（等价于把 s-ghost 从目录里去掉）
    await runner.update_outline_structure(
        tid, [{"block_id": "s1", "level": 1, "title": "技术方案"}],
    )

    state = await runner.state(tid)
    matrix2 = (state.get("spec") or {}).get("outline_matrix") or {}
    assert "s-ghost" not in matrix2, "目录外的矩阵条目必须被裁掉"
    assert "s1" in matrix2, "目录内的条目不能误删"


# ─────────────────────────────────────────────
# 要求文件变更守卫：重出目录带不上新文件
# ─────────────────────────────────────────────

async def test_redraft_outline_rejects_when_requirement_files_changed(runner_env, monkeypatch):
    """要求文件换过之后，重出目录必须被拒绝并指向「重新提炼」。

    实测（项目 18，2026-10-12）：用户删掉旧文件、传了新文件，然后连点「重新拆分目录」，
    每次都按 checkpoint 里**上一次解析**的 source_toc 出目录 → 页面看起来毫无变化，
    而用户以为新文件已生效（连点 8 次）。重出无法修复这一点（它不重跑解析）。
    """
    runner, project_id, _ = runner_env

    state = {
        "project_id": project_id,
        "stage": "outline_review",
        "spec": {"material_sig": "spec:旧文件.docx:100"},
    }
    monkeypatch.setattr(runner, "state", _state_stub(state))

    import orchestrator.nodes as nodes
    monkeypatch.setattr(nodes, "materials_signature",
                        _state_stub("spec:新文件.docx:200"))   # 原料已换

    with pytest.raises(ValueError, match="要求文件已变更"):
        await runner.redraft_outline("tid-x", "")


async def test_redraft_outline_allows_when_requirement_files_unchanged(runner_env, monkeypatch):
    """原料没变时不该被守卫拦住（不能把正常重出也拒了）。"""
    runner, project_id, _ = runner_env

    state = {
        "project_id": project_id,
        "stage": "outline_review",
        "spec": {"material_sig": "spec:同一份.docx:100"},
    }
    monkeypatch.setattr(runner, "state", _state_stub(state))

    import orchestrator.nodes as nodes
    monkeypatch.setattr(nodes, "materials_signature",
                        _state_stub("spec:同一份.docx:100"))

    await runner.redraft_outline("tid-y", "")
    await _wait_task(runner._runs["tid-y"])      # 不该因守卫被拒


def test_recorded_requirement_filenames_prefers_material_files():
    """优先 material_files；没有则从 source_toc 的 source_file 反推。

    为什么需要兜底：改动前创建的 run 没有 material_files，但 **source_toc 的每一节都带
    source_file** —— 靠它才能判定"这次解析用的是哪批文件"。原守卫只看 material_sig，
    旧 run 直接跳过，于是"删旧文件 + 传新文件"后点重拆照样跑（页面毫无变化，实测连点 8 次）。
    """
    from orchestrator.runner import recorded_requirement_filenames

    assert recorded_requirement_filenames(
        {"material_files": ["a.pdf", "b.docx"]}
    ) == {"a.pdf", "b.docx"}

    # 旧 run：只有 source_toc（每节带 source_file）
    assert recorded_requirement_filenames({
        "source_toc": [
            {"source_file": "旧.pdf"}, {"source_file": "旧.pdf"}, {"source_file": "旧规范.docx"},
        ]
    }) == {"旧.pdf", "旧规范.docx"}

    # 两者都没有 → 空集（调用方放行，不把正常重出拦死）
    assert recorded_requirement_filenames({}) == set()
    assert recorded_requirement_filenames({"source_toc": [{"title": "无来源"}]}) == set()


async def test_redraft_check_reports_reason_and_action(runner_env, monkeypatch):
    """预检接口要把"能不能重拆、不能的话该做什么"明确说出来。

    前端拿它决定走"重拆"还是"必须重新提炼" —— 没有这道预检时，用户只会拿到
    一个 200 却什么都变不了，只能反复点。
    """
    import orchestrator.nodes as nodes

    runner, project_id, _ = runner_env
    state = {
        "project_id": project_id,
        "stage": "outline_review",
        # source_toc 是"有解析结果"的凭据：重拆就是从它派生应答目录。
        # 没有它就不该放行（见 no_source_toc 分支）。
        "spec": {"material_sig": "spec:旧.docx:1",
                 "source_toc": [{"title": "招标文件", "source_file": "旧.docx"}]},
    }
    monkeypatch.setattr(runner, "state", _state_stub(state))
    monkeypatch.setattr(nodes, "materials_signature", _state_stub("spec:新.docx:2"))

    result = await runner.redraft_check("tid-c")
    assert result["allowed"] is False
    assert "requirement_files_changed" in result["reasons"]
    assert "重新提炼" in result["message"]      # 指导下一步，而不是只说"不许"

    # 原料一致 + 有解析结果 → 放行
    monkeypatch.setattr(nodes, "materials_signature", _state_stub("spec:旧.docx:1"))
    ok = await runner.redraft_check("tid-c")
    assert ok["allowed"] is True and ok["message"] == ""


async def test_redraft_check_allows_when_aborted_during_parsing(runner_env, monkeypatch):
    """在 **parsing 阶段被 abort** 的 run 必须允许重拆。

    实测（项目 19 run efe2ac4c）：checkpoint stage='parsing'、source_toc=531 节，
    但 outline_revision=None —— 派生目录从未跑过，而 blocks 里躺的是 parse 写的
    **原始目录占位**（1.招标条件 / 2.项目概况…），页面于是把招标文件原始目录
    当成"应答目录"显示给用户。

    旧判据按 stage 判定，把这个 run 归为"已推进到撰写/评审之后"，既说错原因，
    又**挡住唯一能救的路径** —— 重拆本来就是从 source_toc 派生应答目录。
    """
    import orchestrator.nodes as nodes

    runner, project_id, _ = runner_env
    state = {
        "project_id": project_id,
        "stage": "parsing",                       # abort 后就停在解析阶段
        "spec": {
            "material_sig": "spec:同一批.docx:1",
            "source_toc": [{"title": "1.招标条件", "source_file": "同一批.docx"}],
            "toc": [{"title": "1.招标条件"}],       # parse 写的原始目录占位
            "outline_revision": None,             # 派生从没跑过
        },
    }
    monkeypatch.setattr(runner, "state", _state_stub(state))
    monkeypatch.setattr(nodes, "materials_signature", _state_stub("spec:同一批.docx:1"))

    result = await runner.redraft_check("tid-parsing")
    assert result["allowed"] is True, result
    assert result["reasons"] == []
    assert result["message"] == ""

    # 真的能跑起来（不是只放行但不干活）
    await runner.redraft_outline("tid-parsing", "")
    await _wait_task(runner._runs["tid-parsing"])


async def test_redraft_check_blocks_when_run_still_running(runner_env, monkeypatch):
    """还在跑的 run 不能重拆 —— 判据是"有没有东西在动"，不是 stage。"""
    import orchestrator.nodes as nodes

    runner, project_id, _ = runner_env
    state = {
        "project_id": project_id,
        "stage": "parsing",
        "spec": {"material_sig": "spec:x:1", "source_toc": [{"title": "x"}]},
    }
    monkeypatch.setattr(runner, "state", _state_stub(state))
    monkeypatch.setattr(nodes, "materials_signature", _state_stub("spec:x:1"))

    class _AliveTask:
        def done(self): return False

    class _AliveRun:
        task = _AliveTask()

    runner._runs["tid-running"] = _AliveRun()
    result = await runner.redraft_check("tid-running")
    assert result["allowed"] is False
    assert "run_in_progress" in result["reasons"]
    assert "还在跑" in result["message"]


def _state_stub(value):
    """runner.state 是 async 的 —— 桩要返回 coroutine，不能返回 coroutine 对象。"""
    async def _inner(*_a, **_k):
        return value
    return _inner
