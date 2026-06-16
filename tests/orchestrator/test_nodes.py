"""orchestrator/nodes.py 单测：mock 各 agent，验证 state_patch + emitter 帧。"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass

import pytest

from agents.zhang_heng import SpecParseResult
from domain.proposal import BlockOutput, Source
from domain.review import Finding, Issue
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection
from infra.retrieval import Match
from orchestrator import nodes
from orchestrator.events import EventEmitter


_FRAME_RE = re.compile(r"^event: (?P<event>[^\n]+)\ndata: (?P<data>.+)\n\n$", re.S)


def _parse_frame(frame: str) -> tuple[str, dict]:
    m = _FRAME_RE.match(frame)
    assert m, f"非法 SSE 帧：{frame!r}"
    return m.group("event"), json.loads(m.group("data"))


async def _drain(emitter: EventEmitter) -> list[tuple[str, dict]]:
    await emitter.aclose()
    return [_parse_frame(f) async for f in emitter.stream()]


# ─────────────────────────────────────────────
# cancel 路径
# ─────────────────────────────────────────────

async def test_cancel_check_raises_in_each_node():
    """所有节点开头都检查 cancel_requested 并抛 WorkflowCancelled。"""
    state = {"cancel_requested": True, "project_id": 1, "thread_id": "tid-1"}

    class _Stub:
        async def parse(self, *a, **kw): raise AssertionError("不该被调到")
        async def extract(self, *a, **kw): raise AssertionError("不该被调到")
        async def match(self, *a, **kw): raise AssertionError("不该被调到")
        async def generate(self, *a, **kw): raise AssertionError("不该被调到")
        async def review(self, *a, **kw): raise AssertionError("不该被调到")

    stub = _Stub()

    async def loader(_pid):
        raise AssertionError("不该被调到")

    for call in [
        lambda: nodes.zhang_heng_parse_node(state, agent=stub, spec_loader=loader),
        lambda: nodes.zhang_heng_extract_node(state, agent=stub),
        lambda: nodes.shen_kuo_match_node(state, agent=stub),
        lambda: nodes.zhuge_liang_generate_node(state, agent=stub),
        lambda: nodes.wang_anshi_review_node(state, agent=stub),
        lambda: nodes.bao_zheng_review_node(state, agent=stub),
        lambda: nodes.aggregate_review_node(state),
    ]:
        with pytest.raises(nodes.WorkflowCancelled):
            await call()


# ─────────────────────────────────────────────
# 张衡 parse
# ─────────────────────────────────────────────

class _ZhangHengParseStub:
    def __init__(self):
        self.calls = []

    async def parse(self, file_source, *, suffix, filename, progress_callback=None):
        self.calls.append({"suffix": suffix, "filename": filename})
        return SpecParseResult(
            doc_id="d1",
            doc_title="规范书",
            doc_summary="摘要",
            toc=[
                DomainSection(id="s1", level=1, title="技术方案",
                              raw_content="原文", special_marks=["★"]),
            ],
        )


async def test_zhang_heng_parse_node_writes_spec_fields():
    agent = _ZhangHengParseStub()

    async def loader(pid):
        assert pid == 99
        return (io.BytesIO(b"PDF"), ".pdf", "spec.pdf")

    state = {"project_id": 99, "thread_id": "tid"}
    patch = await nodes.zhang_heng_parse_node(
        state, agent=agent, spec_loader=loader,
    )

    assert patch["stage"] == "parsing"
    spec = patch["spec"]
    assert spec["doc_id"] == "d1"
    assert spec["doc_title"] == "规范书"
    assert spec["doc_summary"] == "摘要"
    assert spec["toc"] == [{
        "id": "s1", "level": 1, "title": "技术方案",
        "raw_content": "原文", "special_marks": ["★"],
    }]
    assert agent.calls == [{"suffix": ".pdf", "filename": "spec.pdf"}]


# ─────────────────────────────────────────────
# 张衡 extract
# ─────────────────────────────────────────────

class _ZhangHengExtractStub:
    async def extract(self, toc, **kw):
        return {
            s.id: OutlineMatrixRow(
                block_id=s.id, title=s.title, requirement="req-" + s.id,
            )
            for s in toc
        }


async def test_zhang_heng_extract_node_writes_matrix_and_emits():
    state = {
        "thread_id": "tid",
        "spec": {
            "toc": [
                {"id": "s1", "level": 1, "title": "技术方案",
                 "raw_content": "", "special_marks": []},
                {"id": "s2", "level": 1, "title": "实施计划",
                 "raw_content": "", "special_marks": []},
            ],
        },
    }
    emitter = EventEmitter()
    patch = await nodes.zhang_heng_extract_node(
        state, agent=_ZhangHengExtractStub(), emitter=emitter,
    )
    matrix = patch["spec"]["outline_matrix"]
    assert set(matrix.keys()) == {"s1", "s2"}
    assert matrix["s1"]["requirement"] == "req-s1"

    frames = await _drain(emitter)
    names = [n for n, _ in frames]
    assert names == ["outline_extract", "outline_extract"]
    assert {f["block_id"] for _, f in frames} == {"s1", "s2"}


# ─────────────────────────────────────────────
# 沈括 match
# ─────────────────────────────────────────────

class _ShenKuoStub:
    async def match(self, toc, outline_matrix, chunks):
        return {
            s.id: [Match(chunk_id=f"c-{s.id}", score=0.9,
                         reason="r", hit_points=["hp"])]
            for s in toc
        }


async def test_shen_kuo_match_node_writes_matches_and_emits():
    state = {
        "thread_id": "tid",
        "spec": {
            "toc": [{"id": "s1", "level": 1, "title": "T",
                     "raw_content": "", "special_marks": []}],
            "outline_matrix": {
                "s1": {"block_id": "s1", "title": "T", "requirement": "req"},
            },
        },
        "materials": {"chunks": [{"id": "c1", "content": "x"}]},
    }
    emitter = EventEmitter()
    patch = await nodes.shen_kuo_match_node(
        state, agent=_ShenKuoStub(), emitter=emitter,
    )

    matches = patch["materials"]["matches"]
    assert list(matches.keys()) == ["s1"]
    assert matches["s1"][0]["chunk_id"] == "c-s1"
    assert matches["s1"][0]["score"] == 0.9
    assert matches["s1"][0]["hit_points"] == ["hp"]

    frames = await _drain(emitter)
    assert [n for n, _ in frames] == ["match_progress"]
    assert frames[0][1]["block_id"] == "s1"


# ─────────────────────────────────────────────
# 诸葛亮 generate
# ─────────────────────────────────────────────

class _ZhugeLiangStub:
    """记录传入的 outline_matrix / regenerate_targets，返回固定 BlockOutput。"""

    def __init__(self):
        self.received_targets = None
        self.received_matrix_keys = None

    async def generate(self, *, outline_matrix, materials,
                       regenerate_targets=None, emitter=None):
        self.received_targets = regenerate_targets
        self.received_matrix_keys = list(outline_matrix.keys())
        results = {
            bid: BlockOutput(
                block_id=bid, kind="tech",
                content=f"正文-{bid}", outline=f"大纲-{bid}",
                sources=[Source(material_id=1, chunk_index=0, snippet="s")],
            )
            for bid in outline_matrix
        }
        return results, list(regenerate_targets or [])


def _matrix_state(*ids):
    return {
        "outline_matrix": {
            bid: {"block_id": bid, "title": f"T-{bid}", "requirement": ""}
            for bid in ids
        },
    }


async def test_zhuge_liang_generate_skips_existing_blocks():
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1", "s2"),
        "materials": {"matches": {}},
        "proposal": {
            # s1 已完成，节点应只跑 s2
            "blocks": {
                "s1": {"block_id": "s1", "kind": "tech", "content": "old",
                       "outline": "", "sources": [], "needs_diagram": False},
            },
        },
    }
    agent = _ZhugeLiangStub()
    patch = await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.received_matrix_keys == ["s2"]
    assert agent.received_targets is None  # 正向模式

    blocks = patch["proposal"]["blocks"]
    assert set(blocks.keys()) == {"s1", "s2"}
    assert blocks["s1"]["content"] == "old"          # 已存在的不被覆盖
    assert blocks["s2"]["content"] == "正文-s2"
    assert patch["proposal"]["regenerate_targets"] == []


async def test_zhuge_liang_generate_regenerate_targets_overrides_existing():
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1", "s2"),
        "materials": {"matches": {}},
        "proposal": {
            "blocks": {
                "s1": {"block_id": "s1", "kind": "tech", "content": "old1",
                       "outline": "", "sources": [], "needs_diagram": False},
                "s2": {"block_id": "s2", "kind": "tech", "content": "old2",
                       "outline": "", "sources": [], "needs_diagram": False},
            },
            "regenerate_targets": ["s2"],
        },
    }
    agent = _ZhugeLiangStub()
    patch = await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.received_matrix_keys == ["s2"]
    assert agent.received_targets == ["s2"]

    blocks = patch["proposal"]["blocks"]
    assert blocks["s1"]["content"] == "old1"          # 未在 targets 不变
    assert blocks["s2"]["content"] == "正文-s2"        # 被重生覆盖
    assert patch["proposal"]["regenerate_targets"] == []


async def test_zhuge_liang_generate_emits_block_done():
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1"),
        "materials": {"matches": {}},
    }
    emitter = EventEmitter()
    await nodes.zhuge_liang_generate_node(
        state, agent=_ZhugeLiangStub(), emitter=emitter,
    )
    frames = await _drain(emitter)
    names = [n for n, _ in frames]
    assert names == ["block_done"]
    assert frames[0][1]["block_id"] == "s1"
    assert frames[0][1]["content"] == "正文-s1"


# ─────────────────────────────────────────────
# 王安石 / 包拯 review
# ─────────────────────────────────────────────

class _ReviewerStub:
    def __init__(self, agent_name):
        self.agent_name = agent_name

    async def review(self, *, blocks, outline_matrix, emitter=None):
        return {
            bid: Finding(
                block_id=bid,
                agent=self.agent_name,
                score=80,
                issues=[Issue(severity="high", point="缺细节",
                              suggestion="补图")],
                strengths=["清晰"],
            )
            for bid in blocks
        }


def _blocks_state(*ids):
    return {
        "blocks": {
            bid: {"block_id": bid, "kind": "tech", "content": f"正文-{bid}",
                  "outline": "", "sources": [], "needs_diagram": False}
            for bid in ids
        },
    }


async def test_wang_anshi_review_node_writes_tech_findings():
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1"),
        "proposal": _blocks_state("s1"),
    }
    emitter = EventEmitter()
    patch = await nodes.wang_anshi_review_node(
        state, agent=_ReviewerStub("wang_anshi"), emitter=emitter,
    )

    findings = patch["review"]["tech_findings"]
    assert findings["s1"]["agent"] == "wang_anshi"
    assert findings["s1"]["score"] == 80

    frames = await _drain(emitter)
    name, payload = frames[0]
    assert name == "review_finding"
    assert payload["agent"] == "wang_anshi"
    assert payload["block_id"] == "s1"


async def test_bao_zheng_review_node_writes_compliance_findings():
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1"),
        "proposal": _blocks_state("s1"),
    }
    patch = await nodes.bao_zheng_review_node(
        state, agent=_ReviewerStub("bao_zheng"),
    )
    findings = patch["review"]["compliance_findings"]
    assert findings["s1"]["agent"] == "bao_zheng"


# ─────────────────────────────────────────────
# aggregate_review
# ─────────────────────────────────────────────

async def test_aggregate_review_builds_global_report():
    state = {
        "spec": {
            "outline_matrix": {
                "s1": {"block_id": "s1", "title": "T1",
                       "evidence_required": "证据", "bonus_items": ""},
                "s2": {"block_id": "s2", "title": "T2",
                       "evidence_required": "", "bonus_items": "加分"},
            },
        },
        "proposal": {
            "blocks": {
                "s1": {"block_id": "s1", "kind": "tech", "content": "  "},
                "s2": {"block_id": "s2", "kind": "tech", "content": ""},
            },
        },
        "review": {
            "tech_findings": {
                "s1": {"score": 80, "issues": [
                    {"severity": "critical", "point": "致命", "suggestion": ""},
                ]},
                "s2": {"score": 60, "issues": [
                    {"severity": "low", "point": "小事", "suggestion": ""},
                ]},
            },
            "compliance_findings": {
                "s1": {"score": 70, "issues": [
                    {"severity": "high", "point": "合规风险", "suggestion": ""},
                ]},
                "s2": {"score": 90, "issues": []},
            },
        },
    }

    emitter = EventEmitter()
    patch = await nodes.aggregate_review_node(state, emitter=emitter)
    report = patch["review"]["report"]

    assert patch["stage"] == "report_review"

    # per_block
    assert report["per_block"]["s1"]["tech_score"] == 80
    assert report["per_block"]["s1"]["comp_score"] == 70
    assert report["per_block"]["s1"]["severity_counts"]["critical"] == 1
    assert report["per_block"]["s1"]["severity_counts"]["high"] == 1
    assert report["per_block"]["s2"]["severity_counts"]["low"] == 1

    # total: ((80+70)/2 + (60+90)/2) / 2 = (75 + 75) / 2 = 75
    assert report["total_score"] == 75.0

    # top_risks：critical 排前
    assert report["top_risks"][0].startswith("[s1]")
    assert "致命" in report["top_risks"][0]
    assert any("合规风险" in r for r in report["top_risks"])

    # missing_evidence：s1 evidence_required 非空但 content 为空白
    assert report["missing_evidence"] == ["s1：T1"]
    # missing_bonus：s2 bonus_items 非空但 content 为空
    assert report["missing_bonus"] == ["s2：T2"]

    # 推送了 report_ready
    frames = await _drain(emitter)
    assert frames[0][0] == "report_ready"
    assert frames[0][1]["report"]["total_score"] == 75.0


async def test_aggregate_review_empty_findings_returns_zero_total():
    state = {"review": {"tech_findings": {}, "compliance_findings": {}}}
    patch = await nodes.aggregate_review_node(state)
    report = patch["review"]["report"]
    assert report["total_score"] == 0.0
    assert report["per_block"] == {}
    assert report["top_risks"] == []
