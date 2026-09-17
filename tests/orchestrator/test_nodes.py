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
        async def draft_outline(self, *a, **kw): raise AssertionError("不该被调到")
        async def extract(self, *a, **kw): raise AssertionError("不该被调到")
        async def match(self, *a, **kw): raise AssertionError("不该被调到")
        async def generate(self, *a, **kw): raise AssertionError("不该被调到")
        async def review(self, *a, **kw): raise AssertionError("不该被调到")

    stub = _Stub()

    async def loader(_pid):
        raise AssertionError("不该被调到")

    for call in [
        lambda: nodes.zhang_heng_parse_node(state, agent=stub, spec_loader=loader),
        lambda: nodes.zhang_heng_outline_draft_node(state, agent=stub),
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

    async def parse(self, sources, *, progress_callback=None):
        self.calls.append(list(sources))
        return SpecParseResult(
            doc_id="d1",
            doc_title="要求文件",
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
        return [(io.BytesIO(b"PDF"), ".pdf", "spec.pdf")]

    state = {"project_id": 99, "thread_id": "tid"}
    patch = await nodes.zhang_heng_parse_node(
        state, agent=agent, spec_loader=loader,
    )

    assert patch["stage"] == "parsing"
    spec = patch["spec"]
    assert spec["doc_id"] == "d1"
    assert spec["doc_title"] == "要求文件"
    assert spec["doc_summary"] == "摘要"
    # 成功时不带降级原因，闸门 1 才会显示「无异常」。
    assert spec["doc_summary_error"] == ""
    assert spec["toc"] == [{
        "id": "s1", "level": 1, "title": "技术方案",
        "raw_content": "原文", "special_marks": ["★"],
    }]
    assert len(agent.calls) == 1 and len(agent.calls[0]) == 1
    assert agent.calls[0][0][1:] == (".pdf", "spec.pdf")


async def test_zhang_heng_parse_node_forwards_doc_summary_error():
    """项目概述生成失败的原因必须一路带到 spec，闸门 1 才有东西可显示。"""
    class _FailingStub(_ZhangHengParseStub):
        async def parse(self, sources, *, progress_callback=None):
            return SpecParseResult(
                doc_id="d1",
                doc_title="要求文件",
                doc_summary="",
                toc=[DomainSection(id="s1", level=1, title="技术方案",
                                   raw_content="原文", special_marks=[])],
                doc_summary_error="模型返回空内容",
            )

    async def loader(_pid):
        return [(io.BytesIO(b"PDF"), ".pdf", "spec.pdf")]

    patch = await nodes.zhang_heng_parse_node(
        {"project_id": 99, "thread_id": "tid"},
        agent=_FailingStub(),
        spec_loader=loader,
    )

    spec = patch["spec"]
    assert spec["doc_summary"] == ""
    assert spec["doc_summary_error"] == "模型返回空内容"


async def test_zhang_heng_parse_node_forwards_every_requirement_file():
    """「应标要求」面板下传了几份，load 出来的几份都要交给张衡 —— 提炼不只看规范书。"""
    agent = _ZhangHengParseStub()

    async def loader(_pid):
        return [
            (io.BytesIO(b"PDF"), ".pdf", "招标文件.pdf"),
            (io.BytesIO(b"XLSX"), ".docx", "评分表.docx"),
        ]

    patch = await nodes.zhang_heng_parse_node(
        {"project_id": 99, "thread_id": "tid"}, agent=agent, spec_loader=loader,
    )

    assert [c[1:] for c in agent.calls[0]] == [
        (".pdf", "招标文件.pdf"), (".docx", "评分表.docx"),
    ]
    assert patch["spec"]["toc"][0]["id"] == "s1"


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
        results = {}
        for bid in blocks:
            finding = Finding(
                block_id=bid,
                agent=self.agent_name,
                score=80,
                issues=[Issue(severity="high", point="缺细节",
                              suggestion="补图")],
                strengths=["清晰"],
            )
            # 与真实 agent 对齐：emit start + done 让 nodes 桥接转 review_block_start /
            # review_finding SSE 事件，前端能逐 block 看到进度。
            if emitter is not None:
                rv = emitter("review_block_start", {
                    "block_id": bid, "agent": self.agent_name,
                })
                if hasattr(rv, "__await__"): await rv
                rv = emitter("review_block_done", {
                    "block_id": bid, "agent": self.agent_name,
                    "score": finding.score,
                    "issues": [i.to_dict() for i in finding.issues],
                    "issues_count": len(finding.issues),
                    "error": "",
                })
                if hasattr(rv, "__await__"): await rv
            results[bid] = finding
        return results


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
    # 现在节点会 emit review_block_start + review_finding 双事件；过滤出 review_finding 验证
    finding_frames = [(n, p) for n, p in frames if n == "review_finding"]
    assert len(finding_frames) >= 1
    name, payload = finding_frames[0]
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


# ─────────────────────────────────────────────
# 协同闭环：check_convergence
# ─────────────────────────────────────────────

def _finding(score: int, issues=None, error: str = "") -> dict:
    return {
        "block_id": "s1", "agent": "x", "score": score,
        "issues": issues or [], "strengths": [], "error": error,
    }


async def test_check_convergence_all_pass():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(90)},
            "compliance_findings": {"s1": _finding(85)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "converged"
    assert patch["review"]["convergence"]["unconverged_blocks"] == []


async def test_check_convergence_low_score_triggers_refine():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50)},
            "compliance_findings": {"s1": _finding(85)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "refine"
    assert patch["review"]["convergence"]["unconverged_blocks"] == ["s1"]


async def test_check_convergence_critical_triggers_refine_even_with_high_score():
    """双分都高，但含 critical issue —— 仍判未达标。"""
    issue = {"severity": "critical", "point": "否决项未响应"}
    state = {
        "review": {
            "tech_findings": {"s1": _finding(95, [issue])},
            "compliance_findings": {"s1": _finding(95)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "refine"


async def test_check_convergence_stops_at_max_iterations():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50)},
            "compliance_findings": {"s1": _finding(50)},
        },
        "iteration": nodes.MAX_ITERATIONS,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "max_iterations"


async def test_check_convergence_ignores_review_failures():
    """评审本身报错（基础设施故障）不触发回炉。"""
    state = {
        "review": {
            "tech_findings": {"s1": _finding(0, error="全部 API 失败")},
            "compliance_findings": {"s1": _finding(0, error="全部 API 失败")},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "converged"
    assert patch["review"]["convergence"]["review_failed"] is True


# ─────────────────────────────────────────────
# 协同闭环：build_feedback
# ─────────────────────────────────────────────

async def test_build_feedback_splits_issues_into_two_channels():
    issues = [
        {"severity": "critical", "point": "缺业绩证明", "suggestion": "补充",
         "needs_material": True, "material_query": "近三年业绩证明合同"},
        {"severity": "low", "point": "表述冗余", "suggestion": "精简",
         "needs_material": False, "material_query": ""},
    ]
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50, issues)},
            "compliance_findings": {"s1": _finding(40)},
            "convergence": {"status": "refine", "unconverged_blocks": ["s1"]},
        },
        "proposal": {"blocks": {"s1": {}}},
        "iteration": 0,
    }

    patch = await nodes.build_feedback_node(state)

    assert len(patch["review"]["feedback"]["s1"]["issues"]) == 2
    assert patch["review"]["feedback"]["s1"]["scores"] == {"tech": 50, "comp": 40}
    assert patch["proposal"]["material_requests"]["s1"] == [
        {"query": "近三年业绩证明合同", "reason": "缺业绩证明"}
    ]
    assert patch["proposal"]["regenerate_targets"] == ["s1"]
    assert patch["iteration"] == 1


async def test_build_feedback_max_iterations_mode_writes_no_regen():
    """达上限模式只写反馈与补料请求，不回炉、不递增。"""
    issues = [{"severity": "high", "point": "p", "suggestion": "s",
               "needs_material": True, "material_query": "q"}]
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50, issues)},
            "compliance_findings": {"s1": _finding(40)},
            "convergence": {"status": "max_iterations", "unconverged_blocks": ["s1"]},
        },
        "proposal": {"blocks": {"s1": {}}},
        "iteration": nodes.MAX_ITERATIONS,
    }

    patch = await nodes.build_feedback_node(state)

    assert "feedback" in patch["review"]
    assert "material_requests" in patch["proposal"]
    assert "regenerate_targets" not in patch["proposal"]
    assert "iteration" not in patch


# ─────────────────────────────────────────────
# 协同闭环：collect_gaps
# ─────────────────────────────────────────────

class _CollectGapsStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def retrieve_for(self, requests_by_block, chunks):
        self.calls.append({"requests": requests_by_block, "chunks": chunks})
        return self.result


async def test_collect_gaps_no_requests_is_noop():
    """无补料请求时是空操作，不碰 materials.matches。"""
    stub = _CollectGapsStub({})
    state = {"proposal": {}, "materials": {"chunks": [{"id": 1}]}}

    patch = await nodes.collect_gaps_node(state, agent=stub)

    assert patch == {}
    assert stub.calls == []


async def test_collect_gaps_appends_without_overwriting():
    """新素材追加到已有 matches，同 chunk_id 去重，旧的不被冲掉。"""
    from infra.retrieval import Match

    stub = _CollectGapsStub({"s1": [
        Match(chunk_id=2, score=8.0, reason="新", hit_points=[]),
        Match(chunk_id=1, score=9.0, reason="重复", hit_points=[]),
    ]})
    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {
            "chunks": [{"id": 1}],
            "matches": {"s1": [{"chunk_id": 1, "score": 5.0, "reason": "旧"}]},
        },
    }

    patch = await nodes.collect_gaps_node(state, agent=stub)

    matches = patch["materials"]["matches"]["s1"]
    assert [m["chunk_id"] for m in matches] == [1, 2]
    assert matches[0]["reason"] == "旧"          # 原有匹配保留，不被覆盖
    assert patch["proposal"]["material_requests"] == {}


async def test_collect_gaps_keeps_matches_of_blocks_without_new_hits():
    """只有部分 block 拿到新素材时，其余 block 的原有匹配必须存活。

    上面那条用例只有 s1 一个 block，测不出跨 block 的擦除。而 materials 的
    reducer 是浅合并（_merge_dict 走 out.update(patch)），所以 patch 会整体
    替换 materials.matches —— 若 merged 只装新命中的 block，别的 block 素材
    就被抹掉了。

    这个场景在生产里很常见：build_feedback 只在 block 含 needs_material
    issue 时才登记 material_requests，于是"分数低但问题不属于缺材料"的 block
    会进回炉名单（regenerate_targets）却不进补料名单（material_requests），
    结果它下一轮在零参考素材下重新生成 —— 正是补料闭环要防的事。

    断言必须打在"合进 state 之后"的结果上：只看 patch 是看不出擦除的。
    """
    from infra.retrieval import Match
    from orchestrator.state import _merge_dict

    stub = _CollectGapsStub({"s1": [
        Match(chunk_id=2, score=8.0, reason="新", hit_points=[]),
    ]})
    state = {
        # 只有 s1 提了补料需求；s2 在回炉名单里但不缺材料
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {
            "chunks": [{"id": 1}, {"id": 2}],
            "matches": {
                "s1": [{"chunk_id": 1, "score": 5.0, "reason": "s1旧"}],
                "s2": [{"chunk_id": 9, "score": 7.0, "reason": "s2原有素材"}],
            },
        },
    }

    patch = await nodes.collect_gaps_node(state, agent=stub)
    merged_materials = _merge_dict(state["materials"], patch.get("materials") or {})

    assert [m["chunk_id"] for m in merged_materials["matches"]["s1"]] == [1, 2]
    # 关键断言：s2 没提补料需求，但它的素材不能被这次补料抹掉
    assert "s2" in merged_materials["matches"], (
        "s2 未提补料需求，其原有匹配被 collect_gaps 的浅合并擦除了"
    )
    assert merged_materials["matches"]["s2"][0]["reason"] == "s2原有素材"


async def test_collect_gaps_does_not_wipe_matches_when_nothing_found():
    """补料返回空时不得写 materials.matches，否则会把原匹配清空。"""
    stub = _CollectGapsStub({})
    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {"chunks": [{"id": 1}], "matches": {"s1": [{"chunk_id": 1}]}},
    }

    patch = await nodes.collect_gaps_node(state, agent=stub)

    assert "materials" not in patch
    assert patch["proposal"]["material_requests"] == {}


async def test_collect_gaps_retrieval_failure_does_not_block():
    """检索抛错时记 errors 并放行，不改 matches。"""
    class _Boom:
        async def retrieve_for(self, *a, **kw):
            raise RuntimeError("bm25 挂了")

    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {"chunks": [{"id": 1}], "matches": {"s1": [{"chunk_id": 1}]}},
    }

    patch = await nodes.collect_gaps_node(state, agent=_Boom())

    assert "materials" not in patch
    assert patch["errors"][0]["agent"] == "collect_gaps"


# ─────────────────────────────────────────────
# 协同闭环：_run_review 复审范围与 finding 合并
# ─────────────────────────────────────────────

class _RecordingReviewer:
    """记录每次复审到的 block_id 列表，返回固定 finding。"""

    def __init__(self, score: int = 80):
        self.score = score
        self.scopes: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        from domain.review import Finding
        self.scopes.append(sorted(blocks.keys()))
        return {
            bid: Finding(block_id=bid, agent="wang_anshi", score=self.score)
            for bid in blocks
        }


def _block(block_id: str) -> dict:
    from domain.proposal import BlockOutput
    return BlockOutput(
        block_id=block_id, kind="tech", content="正文", sources=[],
    ).to_dict()


async def test_run_review_narrows_scope_to_updated_blocks():
    """有 updated_blocks 时只复审本轮重跑过的 block。"""
    agent = _RecordingReviewer()
    state = {
        "proposal": {
            "blocks": {"s1": _block("s1"), "s2": _block("s2")},
            "updated_blocks": ["s2"],
        },
    }

    await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    assert agent.scopes == [["s2"]]


async def test_run_review_without_updated_blocks_reviews_all():
    """老 checkpoint 无 updated_blocks 字段 → 复审全部（不能退化成零 block）。"""
    agent = _RecordingReviewer()
    state = {"proposal": {"blocks": {"s1": _block("s1"), "s2": _block("s2")}}}

    await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    assert agent.scopes == [["s1", "s2"]]


async def test_run_review_merges_into_existing_findings():
    """部分复审时必须并入已有 findings —— 整体替换会抹掉未复审 block 的历史分数。

    若被抹掉，check_convergence 会把缺失 block 的 score 读成 0 判为未达标，
    反复回炉且两个 block 交替被清空，收敛判定永远无法稳定。
    """
    agent = _RecordingReviewer(score=60)
    state = {
        "review": {
            "tech_findings": {
                "s1": _finding(95),
                "s2": _finding(50),
            },
        },
        "proposal": {
            "blocks": {"s1": _block("s1"), "s2": _block("s2")},
            "updated_blocks": ["s2"],
        },
    }

    patch = await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    findings = patch["review"]["tech_findings"]
    assert findings["s1"]["score"] == 95     # 未复审，历史分数保留
    assert findings["s2"]["score"] == 60     # 已复审，被本轮结果覆盖


# ─────────────────────────────────────────────
# 协同闭环：zhuge_liang_generate 的 feedback 透传 / updated_blocks
# ─────────────────────────────────────────────

class _FeedbackAwareStub:
    """声明 feedback 形参的 stub：记录收到的 feedback，返回固定 BlockOutput。"""

    def __init__(self):
        self.received_feedback = "未调用"
        self.received_targets = None

    async def generate(self, *, outline_matrix, materials,
                       regenerate_targets=None, emitter=None, feedback=None):
        self.received_feedback = feedback
        self.received_targets = regenerate_targets
        return (
            {bid: BlockOutput(block_id=bid, kind="tech", content=f"正文-{bid}")
             for bid in outline_matrix},
            list(regenerate_targets or []),
        )


class _LegacyStub:
    """未声明 feedback 形参的老 stub：验证据签名探测的向后兼容降级。"""

    def __init__(self):
        self.called = False

    async def generate(self, *, outline_matrix, materials,
                       regenerate_targets=None, emitter=None):
        self.called = True
        return (
            {bid: BlockOutput(block_id=bid, kind="tech", content=f"正文-{bid}")
             for bid in outline_matrix},
            list(regenerate_targets or []),
        )


def _refine_state():
    issues = [{"severity": "critical", "point": "缺业绩证明"}]
    return {
        "thread_id": "tid",
        "spec": _matrix_state("s1"),
        "materials": {"matches": {}},
        "review": {"feedback": {"s1": {"issues": issues, "scores": {
            "tech": 50, "comp": 40}}}},
        "proposal": {"blocks": {}, "regenerate_targets": ["s1"]},
    }


async def test_zhuge_liang_generate_passes_feedback_as_block_to_issues_map():
    """review.feedback 必须以 {block_id: [issues]} 形状灌进 agent.generate。"""
    state = _refine_state()
    issues = [{"severity": "critical", "point": "缺业绩证明"}]
    agent = _FeedbackAwareStub()

    await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.received_feedback == {"s1": issues}


async def test_zhuge_liang_generate_without_feedback_state_passes_empty_map():
    """state 里没有 feedback 时仍传（空 dict），不能传 None 让下游崩。"""
    state = _refine_state()
    del state["review"]
    agent = _FeedbackAwareStub()

    await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.received_feedback == {}


async def test_zhuge_liang_generate_legacy_agent_without_feedback_param_works():
    """agent.generate 无 feedback 形参 → 不传该 kwarg，也不能报错。"""
    state = _refine_state()
    agent = _LegacyStub()

    patch = await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.called is True
    assert patch["proposal"]["blocks"]["s1"]["content"] == "正文-s1"


async def test_zhuge_liang_generate_writes_updated_blocks():
    """patch 的 updated_blocks 只含本轮实际产出的 block（供 _run_review 收窄）。"""
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1", "s2"),
        "materials": {"matches": {}},
        "proposal": {
            "blocks": {"s1": {"block_id": "s1", "kind": "tech", "content": "old",
                              "outline": "", "sources": [], "needs_diagram": False}},
            "regenerate_targets": ["s2"],
        },
    }
    patch = await nodes.zhuge_liang_generate_node(state, agent=_LegacyStub())

    assert patch["proposal"]["updated_blocks"] == ["s2"]


async def test_zhuge_liang_generate_early_return_writes_empty_updated_blocks():
    """全部已完成（resume）时早退分支必须写 updated_blocks=[]（falsy → 复审全部）。"""
    state = {
        "thread_id": "tid",
        "spec": _matrix_state("s1"),
        "materials": {"matches": {}},
        "proposal": {
            "blocks": {"s1": {"block_id": "s1", "kind": "tech", "content": "old",
                              "outline": "", "sources": [], "needs_diagram": False}},
        },
    }
    agent = _LegacyStub()
    patch = await nodes.zhuge_liang_generate_node(state, agent=agent)

    assert agent.called is False
    assert patch["proposal"]["updated_blocks"] == []


async def test_build_feedback_refine_at_max_iterations_refuses_regen():
    """check_convergence 若被改坏、在上限当轮仍返回 refine，兜底拒绝回炉。"""
    issues = [{"severity": "high", "point": "p", "suggestion": "s",
               "needs_material": False, "material_query": ""}]
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50, issues)},
            "compliance_findings": {"s1": _finding(40)},
            "convergence": {"status": "refine", "unconverged_blocks": ["s1"]},
        },
        "proposal": {"blocks": {"s1": {}}},
        "iteration": nodes.MAX_ITERATIONS,
    }

    patch = await nodes.build_feedback_node(state)

    # 兜底改写 status，让 graph 的条件边直连闸门而非回炉
    assert patch["review"]["convergence"]["status"] == "max_iterations"
    assert "regenerate_targets" not in patch["proposal"]
    assert "iteration" not in patch
    # 反馈本身照常写出，人工终审仍能看到问题
    assert len(patch["review"]["feedback"]["s1"]["issues"]) == 1
    assert patch["errors"][0]["agent"] == "build_feedback"
    assert patch["errors"][0]["retryable"] is False


# ─────────────────────────────────────────────
# 张衡 outline_draft
# ─────────────────────────────────────────────

class _OutlineDraftStub:
    """记录调用参数，返回给定 sections/degraded。"""

    def __init__(self, sections, *, degraded=False, error=""):
        from agents.zhang_heng import OutlineDraftResult
        self._result = OutlineDraftResult(
            sections=sections, degraded=degraded, error=error,
        )
        self.calls = []

    async def draft_outline(self, source_toc, *, instruction="", doc_summary="",
                            previous_toc=None):
        self.calls.append({
            "source_titles": [s.title for s in source_toc],
            "instruction": instruction,
            "doc_summary": doc_summary,
            "previous_titles": (
                [s.title for s in previous_toc] if previous_toc else None
            ),
        })
        return self._result


def _draft_state(*, toc=None, source_toc=None, revision=0, instruction=""):
    return {
        "project_id": None,
        "thread_id": "tid",
        "config": {"outline_instruction": instruction},
        "spec": {
            "doc_summary": "摘要",
            "source_toc": source_toc if source_toc is not None else [
                {"id": "s1", "level": 1, "title": "技术方案",
                 "raw_content": "原文", "special_marks": ["★"]},
            ],
            "toc": toc if toc is not None else [],
            "outline_revision": revision,
        },
    }


async def test_outline_draft_node_writes_toc_and_bumps_revision():
    sections = [
        DomainSection(id="s1", level=1, title="项目理解", raw_content="原文"),
        DomainSection(id="s2", level=1, title="技术响应", raw_content="原文"),
    ]
    emitter = EventEmitter()
    patch = await nodes.zhang_heng_outline_draft_node(
        _draft_state(), agent=_OutlineDraftStub(sections), emitter=emitter,
    )

    assert patch["stage"] == "parsing"
    spec = patch["spec"]
    assert [s["id"] for s in spec["toc"]] == ["s1", "s2"]
    assert spec["outline_revision"] == 1
    assert spec["outline_error"] == ""
    # 目录换了，旧的 8 字段矩阵必须整体作废（否则闸门 1 会显示上个版本的章节要求）
    assert spec["outline_matrix"] == {}
    # source_toc 是输入，节点不得回写
    assert "source_toc" not in spec

    frames = await _drain(emitter)
    assert [n for n, _ in frames] == ["outline_draft_start", "outline_draft"]
    assert frames[0][1] == {"revision": 1}
    assert frames[1][1]["revision"] == 1
    assert [s["title"] for s in frames[1][1]["toc"]] == ["项目理解", "技术响应"]
    assert frames[1][1]["degraded"] is False


async def test_outline_draft_node_degrades_to_source_toc_with_error():
    """模型不可用：节点照常写 toc（=原目录）并把原因写进 outline_error，不抛。"""
    source = [DomainSection(id="s1", level=1, title="技术方案",
                            raw_content="原文", special_marks=["★"])]
    patch = await nodes.zhang_heng_outline_draft_node(
        _draft_state(),
        agent=_OutlineDraftStub(source, degraded=True, error="未配置模型，沿用规范书目录"),
        emitter=EventEmitter(),
    )

    spec = patch["spec"]
    assert [s["title"] for s in spec["toc"]] == ["技术方案"]
    assert spec["outline_error"] == "未配置模型，沿用规范书目录"
    assert spec["outline_revision"] == 1


async def test_outline_draft_node_passes_instruction_and_revision_upstream():
    """用户提炼要求进 agent；第二版起才把上一版目录当 previous 喂回去。"""
    sections = [DomainSection(id="s1", level=1, title="新一级", raw_content="")]
    stub = _OutlineDraftStub(sections)

    await nodes.zhang_heng_outline_draft_node(
        _draft_state(revision=0, instruction="按评分项拆章"),
        agent=stub, emitter=EventEmitter(),
    )
    await nodes.zhang_heng_outline_draft_node(
        _draft_state(
            toc=[{"id": "s1", "level": 1, "title": "上一版",
                  "raw_content": "", "special_marks": []}],
            revision=1, instruction="再拆细一层",
        ),
        agent=stub, emitter=EventEmitter(),
    )

    first, second = stub.calls
    assert first["instruction"] == "按评分项拆章"
    assert first["doc_summary"] == "摘要"
    # 首轮 spec.toc 就是 parse 写的规范书目录，不是用户看过的草稿，不能当「上一版」
    assert first["previous_titles"] is None
    assert second["previous_titles"] == ["上一版"]
    # 无 source_toc（老 checkpoint）时退回当前 toc 当输入，而不是空目录降级
    assert second["source_titles"] == ["技术方案"]


async def test_outline_draft_node_prefers_source_toc_over_current_toc():
    sections = [DomainSection(id="s1", level=1, title="派生", raw_content="")]
    stub = _OutlineDraftStub(sections)
    await nodes.zhang_heng_outline_draft_node(
        _draft_state(
            toc=[{"id": "s9", "level": 1, "title": "当前 toc",
                  "raw_content": "", "special_marks": []}],
            source_toc=[{"id": "s1", "level": 1, "title": "规范书原文目录",
                         "raw_content": "原文", "special_marks": []}],
        ),
        agent=stub, emitter=EventEmitter(),
    )
    assert stub.calls[0]["source_titles"] == ["规范书原文目录"]


async def test_outline_draft_node_syncs_placeholder_blocks(tmp_path, monkeypatch):
    """落库：新目录建占位行、目录外空行清掉，但已有正文的行宁可留脏也不删。"""
    import aiosqlite

    import db as db_module
    from db import init_db
    from services.block_store import list_blocks

    db_path = str(tmp_path / "wf.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("测试",))
    await conn.commit()
    pid = cursor.lastrowid
    # 上一版目录：old-1 空占位（不在新 toc 里，应被清）、s9 已有正文（新 toc 里也没有，
    # 但宁可留脏也不删）
    # 这些行属于**上一版 run**：本次 sync 只动自己 run 的行，所以它们既不参与
    # 清理也不参与 upsert —— old-1 这个空占位在新语义下也留着（前端按当前 run
    # 过滤，看不到它）。同 run 内的清理由 test_block_store 覆盖。
    for block_id, content in [("old-1", ""), ("s9", "已写好的正文")]:
        await conn.execute(
            "INSERT INTO blocks (project_id, block_id, kind, level, title,"
            " content, order_idx, run_thread_id)"
            " VALUES (?, ?, 'outline', 1, ?, ?, 0, 'run-prev')",
            (pid, block_id, block_id, content),
        )
    await conn.commit()
    await conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    sections = [
        DomainSection(id="s1", level=1, title="项目理解", raw_content=""),
        DomainSection(id="s2", level=1, title="技术响应", raw_content=""),
    ]
    state = {**_draft_state(), "project_id": pid}
    patch = await nodes.zhang_heng_outline_draft_node(
        state, agent=_OutlineDraftStub(sections), emitter=EventEmitter(),
    )
    assert [s["id"] for s in patch["spec"]["toc"]] == ["s1", "s2"]

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    rows = await list_blocks(conn, pid)
    await conn.close()

    by_id = {r["block_id"]: r for r in rows}
    # 新目录逐节建占位；上一版 run 的行（old-1 空占位、s9 有正文）原样留着 ——
    # 本次 run 不碰别的 run 的行。
    assert set(by_id) == {"s1", "s2", "old-1", "s9"}
    assert by_id["s1"]["title"] == "项目理解"
    assert by_id["s2"]["title"] == "技术响应"
    assert by_id["s1"]["run_thread_id"] == state.get("thread_id")
    assert by_id["old-1"]["run_thread_id"] == "run-prev"
    assert by_id["s9"]["content"] == "已写好的正文"


async def test_outline_draft_node_skips_db_when_project_id_missing():
    """project_id 缺失（纯图单测）时不得尝试落库，避免污染默认 DB。"""
    sections = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]

    async def _boom(*a, **kw):
        raise AssertionError("project_id 为 None 时不该碰数据库")

    import db as db_module
    monkey = _boom
    original = db_module.get_db
    db_module.get_db = monkey
    try:
        patch = await nodes.zhang_heng_outline_draft_node(
            _draft_state(), agent=_OutlineDraftStub(sections), emitter=EventEmitter(),
        )
    finally:
        db_module.get_db = original

    assert [s["id"] for s in patch["spec"]["toc"]] == ["s1"]
