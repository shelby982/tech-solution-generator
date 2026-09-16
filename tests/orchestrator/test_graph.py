"""orchestrator/graph.py 单测。

覆盖 plan §4.5 验收点：
- 闸门暂停在正确 stage
- Fan-out 王安石/包拯 真正并行（启动时间差 < 100ms）
- 续跑：在 extract 之后续跑（从下一个 super-step 继续）
- 回修循环：闸门 3 提交 regenerate_targets → 只重跑这些 block → 再次完整评审

策略：mock 5 个 agent 把业务挖空，专门验图的拓扑、interrupt、并发、条件路由。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import pytest

from agents.zhang_heng import SpecParseResult
from domain.proposal import BlockOutput
from domain.review import Finding
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection
from infra.retrieval import Match
from orchestrator import graph as graph_mod
from orchestrator.checkpointer import checkpointer_from_path
from orchestrator.events import EventEmitter
from orchestrator.graph import (
    GATE_MATERIALS,
    GATE_OUTLINE,
    GATE_PAUSE,
    GATE_REPORT,
    NODE_AGGREGATE,
    NODE_COLLECT_GAPS,
    NODE_COMP_REVIEW,
    NODE_GENERATE,
    NODE_MATCH,
    NODE_OUTLINE_DRAFT,
    NODE_TECH_REVIEW,
    GraphDeps,
    build_graph,
)


_FRAME_RE = re.compile(r"^event: (?P<event>[^\n]+)\ndata: (?P<data>.+)\n\n$", re.S)


def _parse_frame(frame: str) -> tuple[str, dict]:
    m = _FRAME_RE.match(frame)
    assert m, f"非法 SSE 帧：{frame!r}"
    return m.group("event"), json.loads(m.group("data"))


# ─────────────────────────────────────────────
# Stub agents
# ─────────────────────────────────────────────

@dataclass
class _StubZhangHeng:
    async def parse(self, file_source, *, suffix, filename, progress_callback=None):
        return SpecParseResult(
            doc_id="d1", doc_title="规范书", doc_summary="摘要",
            toc=[
                DomainSection(id="s1", level=1, title="技术方案",
                              raw_content="", special_marks=[]),
                DomainSection(id="s2", level=1, title="实施计划",
                              raw_content="", special_marks=[]),
            ],
        )

    async def draft_outline(self, source_toc, *, instruction="", doc_summary="",
                            previous_toc=None):
        """stub 沿用规范书目录（等价降级路径），保持既有 block_id 断言不变。"""
        from agents.zhang_heng import OutlineDraftResult
        return OutlineDraftResult(
            sections=[DomainSection(**s.to_dict()) for s in source_toc],
            degraded=True, error="stub 未派生",
        )

    async def extract(self, toc, **kw):
        return {
            s.id: OutlineMatrixRow(block_id=s.id, title=s.title, requirement="r")
            for s in toc
        }


class _StubShenKuo:
    async def match(self, toc, outline_matrix, chunks):
        return {s.id: [] for s in toc}


class _StubZhugeLiang:
    """记录每次 generate 时收到的 outline_matrix keys，便于回修循环测试。"""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.received_targets: list[list[str] | None] = []

    async def generate(self, *, outline_matrix, materials,
                       regenerate_targets=None, emitter=None):
        self.calls.append(list(outline_matrix.keys()))
        self.received_targets.append(
            list(regenerate_targets) if regenerate_targets is not None else None
        )
        results = {
            bid: BlockOutput(
                block_id=bid, kind="tech",
                content=f"v{len(self.calls)}-{bid}",
                outline="", sources=[],
            )
            for bid in outline_matrix
        }
        return results, list(regenerate_targets or [])


class _DelayingReviewer:
    """评审 agent stub，记录每次 review 进入时刻 — 用于 fan-out 时间差校验。"""

    def __init__(self, name: str, delay: float = 0.2):
        self.name = name
        self.delay = delay
        self.entered_at: float | None = None

    async def review(self, *, blocks, outline_matrix, emitter=None):
        self.entered_at = time.perf_counter()
        await asyncio.sleep(self.delay)
        return {
            bid: Finding(block_id=bid, agent=self.name, score=80)
            for bid in blocks
        }


async def _spec_loader(_pid: int):
    return (b"x", ".pdf", "spec.pdf")


def _build_deps(*, zhuge=None, wang=None, bao=None) -> GraphDeps:
    return GraphDeps(
        zhang_heng=_StubZhangHeng(),
        shen_kuo=_StubShenKuo(),
        zhuge_liang=zhuge or _StubZhugeLiang(),
        wang_anshi=wang or _DelayingReviewer("wang_anshi", delay=0.0),
        bao_zheng=bao or _DelayingReviewer("bao_zheng", delay=0.0),
        spec_loader=_spec_loader,
    )


# ─────────────────────────────────────────────
# 闸门测试：暂停在正确 stage
# ─────────────────────────────────────────────

async def test_graph_pauses_at_outline_gate(tmp_path):
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid"}}

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(_build_deps(), checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)

        snap = await graph.aget_state(config)
        # interrupt_after 配置在 GATE_OUTLINE，闸门已跑完，next 是下一个节点
        assert snap.next == (NODE_MATCH,)
        assert snap.values.get("stage") == "outline_review"
        assert snap.values.get("spec", {}).get("doc_title") == "规范书"
        assert "outline_matrix" in snap.values.get("spec", {})


async def test_graph_pauses_at_each_gate_in_sequence(tmp_path):
    """gate1 → gate2 → gate3，每次 resume 走到下一个闸门后的节点暂停。"""
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid"}}

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(_build_deps(), checkpointer=saver)

        await graph.ainvoke({"project_id": 1}, config=config)
        snap = await graph.aget_state(config)
        assert snap.next == (NODE_MATCH,)
        assert snap.values["stage"] == "outline_review"

        await graph.ainvoke(None, config=config)
        snap = await graph.aget_state(config)
        # 闭环落地后 gate2 的下一个节点是 collect_gaps（generate 前的补料空操作），
        # 不再是 generate 本身。
        assert snap.next == (NODE_COLLECT_GAPS,)
        assert snap.values["stage"] == "materials_review"

        await graph.ainvoke(None, config=config)
        snap = await graph.aget_state(config)
        # gate3 后是条件路由，next 为空（等待 user_choice 后再 invoke）
        assert snap.next == ()
        assert snap.values["stage"] == "report_review"


# ─────────────────────────────────────────────
# Fan-out 并行
# ─────────────────────────────────────────────

async def test_fan_out_reviewers_start_within_100ms(tmp_path):
    """王安石/包拯应在同一 super-step 启动；时间差 < 100ms。"""
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-fanout"}}
    wang = _DelayingReviewer("wang_anshi", delay=0.3)
    bao = _DelayingReviewer("bao_zheng", delay=0.3)

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(
            _build_deps(wang=wang, bao=bao), checkpointer=saver,
        )
        # 跑到 gate1
        await graph.ainvoke({"project_id": 1}, config=config)
        # 过 gate1 → gate2
        await graph.ainvoke(None, config=config)
        # 过 gate2 → gate3（含 fan-out）
        await graph.ainvoke(None, config=config)

    assert wang.entered_at is not None
    assert bao.entered_at is not None
    diff = abs(wang.entered_at - bao.entered_at)
    assert diff < 0.1, f"fan-out 启动时间差 {diff:.3f}s 过大"


# ─────────────────────────────────────────────
# 续跑（保留 spec.outline_matrix，从下一个 super-step 继续）
# ─────────────────────────────────────────────

async def test_resume_after_close_continues_from_checkpoint(tmp_path):
    """跑到 gate1 → 关 saver → 重开 saver → 续跑应直接走 gate2。"""
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-resume"}}

    async with checkpointer_from_path(db) as saver1:
        graph1 = build_graph(_build_deps(), checkpointer=saver1)
        await graph1.ainvoke({"project_id": 1}, config=config)
        snap = await graph1.aget_state(config)
        assert snap.next == (NODE_MATCH,)
        # 记录 outline_matrix 以便比对
        matrix_before = snap.values["spec"]["outline_matrix"]

    # 第二次打开同一文件，state 应被重新载入
    async with checkpointer_from_path(db) as saver2:
        graph2 = build_graph(_build_deps(), checkpointer=saver2)
        snap = await graph2.aget_state(config)
        assert snap.next == (NODE_MATCH,)
        assert snap.values["spec"]["outline_matrix"] == matrix_before
        # 续跑：经 match 节点后落在 gate2 之后，等 collect_gaps → generate
        await graph2.ainvoke(None, config=config)
        snap = await graph2.aget_state(config)
        assert snap.next == (NODE_COLLECT_GAPS,)


# ─────────────────────────────────────────────
# 回修循环
# ─────────────────────────────────────────────

async def test_regen_blocks_loop(tmp_path):
    """gate3 提交 regenerate_targets=['s1'] → 只重跑 s1 → 再次完整评审。"""
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-regen"}}
    zhuge = _StubZhugeLiang()
    wang = _DelayingReviewer("wang_anshi", delay=0.0)
    bao = _DelayingReviewer("bao_zheng", delay=0.0)

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(
            _build_deps(zhuge=zhuge, wang=wang, bao=bao),
            checkpointer=saver,
        )

        # 跑到 gate3
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # gate3

        snap = await graph.aget_state(config)
        assert snap.next == ()
        assert snap.values["stage"] == "report_review"
        assert zhuge.calls == [["s1", "s2"]]  # 第一次跑全部
        assert wang.entered_at is not None    # 第一次评审跑过

        # gate3 提交：选择回修 s1
        wang.entered_at = None
        bao.entered_at = None
        await graph.aupdate_state(config, {
            "user_choice": "regen_blocks",
            "proposal": {"regenerate_targets": ["s1"]},
        })

        # 续跑：经过条件路由 → generate（targets=[s1]）→ fan-out 评审 → gate3
        await graph.ainvoke(None, config=config)
        snap = await graph.aget_state(config)
        # interrupt_after 在 GATE_REPORT 触发后停下；stage 已写回 report_review
        assert snap.values["stage"] == "report_review"

        # 第二次 generate 只跑 s1
        assert len(zhuge.calls) == 2
        assert zhuge.calls[1] == ["s1"]
        assert zhuge.received_targets[1] == ["s1"]

        # 重生后再次完整评审（fan-out 真正运行）
        assert wang.entered_at is not None
        assert bao.entered_at is not None


# ─────────────────────────────────────────────
# approve / abort 路由
# ─────────────────────────────────────────────

async def test_approve_at_gate3_ends(tmp_path):
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-approve"}}

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(_build_deps(), checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)
        await graph.ainvoke(None, config=config)
        await graph.ainvoke(None, config=config)

        await graph.aupdate_state(config, {"user_choice": "approve"})
        await graph.ainvoke(None, config=config)

        snap = await graph.aget_state(config)
        assert snap.next == ()


async def test_abort_at_gate3_marks_aborted(tmp_path):
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-abort"}}

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(_build_deps(), checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)
        await graph.ainvoke(None, config=config)
        await graph.ainvoke(None, config=config)

        await graph.aupdate_state(config, {"user_choice": "abort"})
        await graph.ainvoke(None, config=config)

        snap = await graph.aget_state(config)
        assert snap.next == ()
        assert snap.values.get("stage") == "aborted"


async def test_pause_during_generate_stops_at_gate_pause(tmp_path):
    """generate 中调 should_cancel → 节点返回 stage='paused' → graph 走 GATE_PAUSE。

    核心验证：暂停后 graph 不会继续到评审节点，stage 停在 'paused'。
    """
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-pause"}}

    # 跑过 1 个 block 后翻 cancel_flag
    cancelled = {"flag": False}

    class _PausingZhuge:
        async def generate(self, *, outline_matrix, materials,
                           regenerate_targets=None, emitter=None,
                           should_cancel=None):
            results = {}
            for bid in outline_matrix:
                if should_cancel and should_cancel():
                    continue
                results[bid] = BlockOutput(
                    block_id=bid, kind="tech",
                    content=f"v1-{bid}",
                    outline="", sources=[],
                )
                # 跑完第一个 block 翻 flag，让节点末尾 should_cancel 返回真
                cancelled["flag"] = True
            return results, list(regenerate_targets or [])

    wang = _DelayingReviewer("wang_anshi", delay=0.0)
    bao = _DelayingReviewer("bao_zheng", delay=0.0)
    deps = _build_deps(zhuge=_PausingZhuge(), wang=wang, bao=bao)

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(
            deps, checkpointer=saver,
            should_cancel=lambda: cancelled["flag"],
        )
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # generate → GATE_PAUSE

        snap = await graph.aget_state(config)
        assert snap.values.get("stage") == "paused"
        # 评审节点不应被触发
        assert wang.entered_at is None
        assert bao.entered_at is None


# ─────────────────────────────────────────────
# 协同闭环：收敛判定 → 回边 → 闸门
# ─────────────────────────────────────────────

class _ScoreReviewer:
    """恒定打分的评审 stub —— 用来驱动收敛判定的两个分支。"""

    def __init__(self, name: str, score: int):
        self.name = name
        self.score = score
        self.reviewed: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        self.reviewed.append(sorted(blocks.keys()))
        return {
            bid: Finding(block_id=bid, agent=self.name, score=self.score)
            for bid in blocks
        }


class _RetrieveStubShenKuo(_StubShenKuo):
    """补上闭环所需的按需检索入口。"""

    def __init__(self):
        self.retrieve_calls: list[dict] = []

    async def retrieve_for(self, requests_by_block, chunks):
        self.retrieve_calls.append(dict(requests_by_block))
        return {}


async def test_converged_goes_straight_to_gate_report(tmp_path):
    """全部达标 → check_convergence 判 converged → 直达闸门 3，不回炉。"""
    from orchestrator.nodes import MAX_ITERATIONS  # noqa: F401  (确认常量存在)

    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-converged"}}
    shen = _RetrieveStubShenKuo()
    wang = _ScoreReviewer("wang_anshi", score=95)
    bao = _ScoreReviewer("bao_zheng", score=95)

    deps = _build_deps(wang=wang, bao=bao)
    deps.shen_kuo = shen

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # generate+review+判定

        snap = await graph.aget_state(config)

    assert snap.values["stage"] == "report_review"
    assert snap.values["review"]["convergence"]["status"] == "converged"
    assert snap.values.get("iteration", 0) == 0
    # 达标就不该转译任何修订指令
    assert not snap.values["review"].get("feedback")
    # 更强的断言：达标时 build_feedback 必须压根没跑过。它即使"无事可做"也会写下
    # 空的 feedback / material_requests —— 键存在即说明绕了远路（前端据此显示
    # "已转译 0 条意见"，且 errors 被清空）。
    assert "feedback" not in snap.values["review"]
    assert "material_requests" not in (snap.values.get("proposal") or {})
    # 两位评审各只跑一次，没有重复评审
    assert wang.reviewed == [["s1", "s2"]]
    assert bao.reviewed == [["s1", "s2"]]


async def test_unconverged_loops_until_max_iterations_then_gate_report(tmp_path):
    """持续未达标 → 回炉直到迭代上限 → 停在闸门 3 等人工终审。"""
    from orchestrator.nodes import MAX_ITERATIONS

    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-unconverged"}}
    zhuge = _StubZhugeLiang()
    shen = _RetrieveStubShenKuo()
    wang = _ScoreReviewer("wang_anshi", score=50)
    bao = _ScoreReviewer("bao_zheng", score=50)

    deps = _build_deps(zhuge=zhuge, wang=wang, bao=bao)
    deps.shen_kuo = shen

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # 回炉直到上限

        snap = await graph.aget_state(config)

    assert snap.values["stage"] == "report_review"
    assert snap.values["iteration"] == MAX_ITERATIONS
    assert snap.values["review"]["convergence"]["status"] == "max_iterations"
    # 达上限后仍补跑一次 build_feedback，把意见留给人工终审参考
    assert snap.values["review"]["feedback"]
    # 首轮全量 + MAX_ITERATIONS 轮回炉
    assert len(zhuge.calls) == MAX_ITERATIONS + 1
    assert zhuge.calls[0] == ["s1", "s2"]
    assert zhuge.calls[1] == ["s1", "s2"]   # 两个 block 都未达标


class _PerBlockReviewer:
    """按 block 给不同分数的评审 stub —— 用来制造"部分收敛"。"""

    def __init__(self, name: str, scores: dict[str, int]):
        self.name = name
        self.scores = scores
        self.reviewed: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        self.reviewed.append(sorted(blocks.keys()))
        return {
            bid: Finding(
                block_id=bid, agent=self.name,
                score=self.scores.get(bid, 50),
            )
            for bid in blocks
        }


async def test_partially_converged_narrows_review_scope(tmp_path):
    """s1 达标 / s2 不达标 → 只回炉 s2 → 后续复审范围也应只剩 s2。

    若复审范围未收窄，每轮都会全量重评；未改动的 s1 会因 LLM 采样随机性拿到
    不同分数，可能被误判为未达标而额外回炉，甚至在两轮之间振荡。
    """
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-scope"}}
    wang = _PerBlockReviewer("wang_anshi", {"s1": 95, "s2": 50})
    bao = _PerBlockReviewer("bao_zheng", {"s1": 95, "s2": 50})

    deps = _build_deps(wang=wang, bao=bao)
    # 闭环会真实走到 collect_gaps；裸 _StubShenKuo 没有 retrieve_for，
    # 一旦有补料请求就会被 collect_gaps 内部的 try/except 吞成 errors（测试仍绿，
    # 但已悄悄丧失覆盖）。显式注入带检索入口的 stub 消除这个潜伏陷阱。
    deps.shen_kuo = _RetrieveStubShenKuo()

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # 回炉直到上限

        snap = await graph.aget_state(config)

    assert snap.values["review"]["convergence"]["status"] == "max_iterations"
    assert snap.values["review"]["convergence"]["unconverged_blocks"] == ["s2"]

    # 首轮全量，此后每轮只复审仍在回炉的 s2
    assert wang.reviewed[0] == ["s1", "s2"]
    assert all(scope == ["s2"] for scope in wang.reviewed[1:]), (
        f"复审范围应收窄到本轮更新的 block，实际 {wang.reviewed}"
    )

    # s1 的历史高分必须保留 —— 被抹掉的话下一轮判定会把它读成 0 分，
    # 于是 s1 又被拖回回炉，两个 block 交替清空、永久振荡。
    assert snap.values["review"]["tech_findings"]["s1"]["score"] == 95


# ─────────────────────────────────────────────
# 目录派生节点接线
# ─────────────────────────────────────────────

def _edges(deps) -> set[tuple[str, str]]:
    compiled = build_graph(deps)
    return {(e.source, e.target) for e in compiled.get_graph().edges}


def test_outline_draft_inserted_between_parse_and_extract():
    """parse → outline_draft → extract：目录派生必须在正则解析之后、8 字段提炼之前。"""
    edges = _edges(_build_deps())

    assert ("zhang_heng_parse", NODE_OUTLINE_DRAFT) in edges
    assert (NODE_OUTLINE_DRAFT, "zhang_heng_extract") in edges
    # extract 仍直连闸门 1；parse 不再直连 extract（插节点后旧边必须断掉，
    # 否则派生目录会被 extract 无视）
    assert ("zhang_heng_extract", GATE_OUTLINE) in edges
    assert ("zhang_heng_parse", "zhang_heng_extract") not in edges


def test_interrupt_after_set_unchanged_by_outline_draft():
    """新节点不得成为暂停点：闸门集合仍是原来那四个。"""
    compiled = build_graph(_build_deps())

    assert list(compiled.interrupt_after_nodes) == [
        GATE_OUTLINE, GATE_MATERIALS, GATE_PAUSE, GATE_REPORT,
    ]


async def test_outline_gate_emits_draft_frames_and_snapshot_fields(tmp_path):
    """跑到闸门 1：先推 outline_draft 两帧，闸门快照带回提炼要求与版本号。"""
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid"}}
    emitter = EventEmitter()

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(_build_deps(), emitter=emitter, checkpointer=saver)
        await graph.ainvoke(
            {"project_id": 1, "config": {"outline_instruction": "按评分项拆章"}},
            config=config,
        )
        snap = await graph.aget_state(config)
        assert snap.next == (NODE_MATCH,)

    await emitter.aclose()
    frames = [_parse_frame(f) async for f in emitter.stream()]
    names = [n for n, _ in frames]
    assert "outline_draft_start" in names
    assert names.index("outline_draft_start") < names.index("outline_draft")
    assert names.index("outline_draft") < names.index("gate_open")

    draft = dict(frames)["outline_draft"]
    assert draft["revision"] == 1
    assert [s["id"] for s in draft["toc"]] == ["s1", "s2"]

    gate = dict(frames)["gate_open"]
    assert gate["gate"] == "review_outline"
    assert gate["snapshot"]["outline_instruction"] == "按评分项拆章"
    assert gate["snapshot"]["outline_revision"] == 1
    assert gate["snapshot"]["outline_error"] == "stub 未派生"
    assert [s["id"] for s in gate["snapshot"]["toc"]] == ["s1", "s2"]
