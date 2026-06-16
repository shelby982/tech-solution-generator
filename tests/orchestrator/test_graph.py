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
    GATE_REPORT,
    NODE_AGGREGATE,
    NODE_COMP_REVIEW,
    NODE_GENERATE,
    NODE_MATCH,
    NODE_TECH_REVIEW,
    GraphDeps,
    build_graph,
)


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
        assert snap.next == (NODE_GENERATE,)
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
        # 续跑：经 match 节点后落在 gate2 之后等 generate
        await graph2.ainvoke(None, config=config)
        snap = await graph2.aget_state(config)
        assert snap.next == (NODE_GENERATE,)


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
