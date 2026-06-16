"""LangGraph 节点：把 5 个 agent 包装为图节点（spec §6 编排流程）。

每个节点函数接收完整 ``WorkflowState``，返回一个 ``state_patch`` dict（浅 merge 到 state）。

通用约束：
- 所有节点开头检查 ``state.cancel_requested``，触发则 raise ``WorkflowCancelled``
  （runner 外层捕获后把 stage 设为 ``aborted``）。spec §6 原本写 ``GraphInterrupt``，
  但 LangGraph 的 ``GraphInterrupt`` 是内部子图信号、不可直接 raise — 故引入轻量
  自定义异常承担相同语义。
- 跳过已完成 block：``zhuge_liang_generate_node`` 在 ``regenerate_targets`` 为空且
  ``proposal.blocks`` 已含某 block 时跳过该 block。
- 节点不直接发 SSE，事件帧由 emitter（runner 注入到节点闭包）推送。

依赖注入：节点签名是 ``async def node(state, agent=..., emitter=...)``，运行时
``functools.partial`` 把 agent 实例与 emitter 绑死，再注册进 LangGraph，方便单测把
任意 mock agent 注入进来验证 ``state_patch``。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Union

from agents.bao_zheng import BaoZhengAgent
from agents.shen_kuo import ShenKuoAgent
from agents.wang_anshi import WangAnshiAgent
from agents.zhang_heng import ZhangHengAgent
from agents.zhuge_liang import ZhugeLiangAgent
from domain.proposal import BlockOutput
from domain.review import Finding, GlobalReport
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection
from infra.retrieval import Match
from orchestrator import events
from orchestrator.events import EventEmitter
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 取消信号：runner 在外层捕获，转 stage="aborted"
# ─────────────────────────────────────────────

class WorkflowCancelled(Exception):
    """用户在节点开头检测到 ``cancel_requested`` 时抛出。"""

    def __init__(self, reason: str = "user_cancel") -> None:
        super().__init__(reason)
        self.reason = reason


def _check_cancel(state: WorkflowState) -> None:
    if state.get("cancel_requested"):
        raise WorkflowCancelled("user_cancel")


# ─────────────────────────────────────────────
# emitter 适配：节点 → SSE 帧
# ─────────────────────────────────────────────

EmitterArg = Optional[EventEmitter]


async def _emit_frame(emitter: EmitterArg, frame: str) -> None:
    if emitter is None:
        return
    await emitter.emit(frame)


def _make_agent_emitter_bridge(
    emitter: EmitterArg,
    *,
    block_token_event: str = "block_token",
) -> Optional[Callable[[str, dict[str, Any]], Awaitable[None]]]:
    """把 agent 内部的 (event_type, payload) 回调桥接到 SSE emitter。

    诸葛亮 agent emit 的事件类型是 ``block_start / token / block_done``；这里把
    ``token`` 转成 SSE ``block_token`` 事件，其它事件按 spec §7 转换。
    返回 None 表示外层 emitter 缺省，agent 不需要发事件。
    """
    if emitter is None:
        return None

    async def _bridge(event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "block_start":
            await emitter.emit(events.block_start(
                block_id=payload.get("block_id", ""),
                kind=payload.get("kind", "tech"),
                title=payload.get("title", ""),
            ))
        elif event_type == "token":
            await emitter.emit(events.block_token(
                block_id=payload.get("block_id", ""),
                token=payload.get("token", ""),
            ))
        elif event_type == "block_done":
            # block_done 由 generate_node 在拿到 BlockOutput 后统一发；
            # agent 内部的 block_done 仅作进度信号，这里忽略。
            return
        # 评审 agent 的 review_block_start / review_block_done 在 review_node 拼 finding 后再统一发 review_finding 事件，这里同样忽略
    return _bridge


# ─────────────────────────────────────────────
# 张衡 parse 节点
# ─────────────────────────────────────────────

# 文件源加载回调（routes 在 start 时注入）：返回 (binary_io, suffix, filename)
SpecLoader = Callable[[int], Awaitable[tuple[Any, str, str]]]


async def zhang_heng_parse_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    spec_loader: SpecLoader,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """张衡 parse：读规范书文件 → 解析 toc + doc_summary。

    state 写入：``stage="parsing"``，``spec.{doc_id, doc_title, doc_summary, toc}``。
    """
    _check_cancel(state)

    project_id = state["project_id"]
    file_source, suffix, filename = await spec_loader(project_id)

    async def _on_progress(step: str, current: int, total: int) -> None:
        await _emit_frame(emitter, events.parse_progress(step, current, total))

    def _sync_progress(step: str, current: int, total: int) -> None:
        # parse_document 用 sync 回调；emit 是 async，这里用 fire-and-forget
        # 简单实现：忽略 sync 阶段进度（runner 在外层另发 stage_change）。
        # 若要完整推送，需在外层 wrap parser 为 async。
        return None

    parsed = await agent.parse(
        file_source,
        suffix=suffix,
        filename=filename,
        progress_callback=_sync_progress,
    )

    return {
        "stage": "parsing",
        "spec": {
            "doc_id": parsed.doc_id,
            "doc_title": parsed.doc_title,
            "doc_summary": parsed.doc_summary,
            "toc": [_section_to_dict(s) for s in parsed.toc],
        },
    }


# ─────────────────────────────────────────────
# 张衡 extract 节点
# ─────────────────────────────────────────────

async def zhang_heng_extract_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """张衡 extract：8 字段提炼，逐章节流式推 outline_extract 事件。

    state 写入：``spec.outline_matrix``。
    """
    _check_cancel(state)

    spec = state.get("spec") or {}
    toc_raw = spec.get("toc") or []
    toc = [_dict_to_section(d) for d in toc_raw]

    matrix = await agent.extract(toc)

    matrix_dict: dict[str, dict] = {}
    for block_id, row in matrix.items():
        row_dict = _row_to_dict(row)
        matrix_dict[block_id] = row_dict
        await _emit_frame(emitter, events.outline_extract(
            block_id=block_id, title=row.title, matrix=row_dict,
        ))

    return {"spec": {"outline_matrix": matrix_dict}}


# ─────────────────────────────────────────────
# 沈括 match 节点
# ─────────────────────────────────────────────

async def shen_kuo_match_node(
    state: WorkflowState,
    *,
    agent: ShenKuoAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """沈括：素材匹配，逐 block 推 match_progress 事件。

    state 写入：``materials.matches``。
    """
    _check_cancel(state)

    spec = state.get("spec") or {}
    materials = state.get("materials") or {}
    toc = [_dict_to_section(d) for d in (spec.get("toc") or [])]
    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }
    chunks = list(materials.get("chunks") or [])

    matches = await agent.match(toc, outline_matrix, chunks)

    matches_dict: dict[str, list[dict]] = {}
    for block_id, mlist in matches.items():
        serialized = [_match_to_dict(m) for m in mlist]
        matches_dict[block_id] = serialized
        await _emit_frame(emitter, events.match_progress(
            block_id=block_id, matches=serialized,
        ))

    return {"materials": {"matches": matches_dict}}


# ─────────────────────────────────────────────
# 诸葛亮 generate 节点
# ─────────────────────────────────────────────

async def zhuge_liang_generate_node(
    state: WorkflowState,
    *,
    agent: ZhugeLiangAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """诸葛亮：逐 block 生成（流式 token），并在已完成 block 上跳过。

    跳过策略（spec Task 4.4）：
    - 若 ``proposal.regenerate_targets`` 非空 → 仅跑这些 block，跑完清空 targets
    - 否则跑 outline_matrix 全部，但 ``proposal.blocks`` 已存在的 block 跳过

    state 写入：``proposal.blocks``（merge）, ``proposal.regenerate_targets=[]``。
    """
    _check_cancel(state)

    spec = state.get("spec") or {}
    materials = state.get("materials") or {}
    proposal = state.get("proposal") or {}

    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }
    matches_state = materials.get("matches") or {}
    matches: dict[str, list[Match]] = {
        bid: [_dict_to_match(m) for m in mlist]
        for bid, mlist in matches_state.items()
    }

    existing_blocks = dict(proposal.get("blocks") or {})
    regen_targets = list(proposal.get("regenerate_targets") or [])

    if regen_targets:
        # 重生模式：把指定 block 从 existing 移除，让 agent 重跑
        target_for_agent: list[str] = [
            bid for bid in regen_targets if bid in outline_matrix
        ]
    else:
        # 正向模式：把已完成 block 从 outline_matrix 视图过滤掉
        target_for_agent = [
            bid for bid in outline_matrix.keys() if bid not in existing_blocks
        ]

    if not target_for_agent:
        # 全部已完成（resume 后无新 block）
        return {"proposal": {
            "blocks": existing_blocks,
            "regenerate_targets": [],
        }}

    # 视图过滤：传给 agent 的 outline_matrix 仅含待生成 block
    sub_matrix = {bid: outline_matrix[bid] for bid in target_for_agent}

    bridge = _make_agent_emitter_bridge(emitter)
    new_blocks, _consumed = await agent.generate(
        outline_matrix=sub_matrix,
        materials=matches,
        regenerate_targets=target_for_agent if regen_targets else None,
        emitter=bridge,
    )

    # 合并：重生覆盖已存在；正向只新增
    merged_blocks = dict(existing_blocks)
    for bid, output in new_blocks.items():
        merged_blocks[bid] = _block_output_to_dict(output)
        await _emit_frame(emitter, events.block_done(
            block_id=bid,
            content=output.content,
            sources=[s.to_dict() if hasattr(s, "to_dict") else s
                     for s in output.sources],
            outline={
                "outline_text": output.outline,
                "needs_diagram": output.needs_diagram,
            },
        ))

    return {
        "stage": "generating",
        "proposal": {
            "blocks": merged_blocks,
            "regenerate_targets": [],
        },
    }


# ─────────────────────────────────────────────
# 王安石 / 包拯：评审节点
# ─────────────────────────────────────────────

async def wang_anshi_review_node(
    state: WorkflowState,
    *,
    agent: WangAnshiAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """王安石：技术评审；逐 block 推 review_finding 事件。"""
    _check_cancel(state)
    return await _run_review(
        state, agent=agent, emitter=emitter,
        finding_field="tech_findings", agent_name="wang_anshi",
    )


async def bao_zheng_review_node(
    state: WorkflowState,
    *,
    agent: BaoZhengAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """包拯：合规评审；逐 block 推 review_finding 事件。"""
    _check_cancel(state)
    return await _run_review(
        state, agent=agent, emitter=emitter,
        finding_field="compliance_findings", agent_name="bao_zheng",
    )


async def _run_review(
    state: WorkflowState,
    *,
    agent: Union[WangAnshiAgent, BaoZhengAgent],
    emitter: EmitterArg,
    finding_field: str,
    agent_name: str,
) -> WorkflowState:
    spec = state.get("spec") or {}
    proposal = state.get("proposal") or {}

    blocks_state = proposal.get("blocks") or {}
    blocks: dict[str, BlockOutput] = {
        bid: _dict_to_block_output(d) for bid, d in blocks_state.items()
    }
    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }

    findings = await agent.review(
        blocks=blocks,
        outline_matrix=outline_matrix,
        emitter=None,  # agent 内部 emit 的 review_block_* 我们不直接转 SSE
    )

    findings_dict: dict[str, dict] = {}
    for block_id, finding in findings.items():
        d = finding.to_dict()
        findings_dict[block_id] = d
        await _emit_frame(emitter, events.review_finding(
            block_id=block_id,
            agent=agent_name,
            score=float(finding.score),
            issues=[i.to_dict() for i in finding.issues],
        ))

    return {
        "stage": "reviewing",
        "review": {finding_field: findings_dict},
    }


# ─────────────────────────────────────────────
# aggregate_review 节点：合成 GlobalReport
# ─────────────────────────────────────────────

# 严重度数值化，用于 top_risks 排序
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


async def aggregate_review_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """聚合王安石 + 包拯的 findings 为 GlobalReport（spec §5）。

    state 写入：``review.report``。
    """
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}

    # per_block：tech_score / comp_score / severity_counts（合并两侧 issues）
    per_block: dict[str, dict] = {}
    all_block_ids = set(tech.keys()) | set(comp.keys())
    for bid in sorted(all_block_ids):
        t = tech.get(bid) or {}
        c = comp.get(bid) or {}
        sev_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for issue in (t.get("issues") or []) + (c.get("issues") or []):
            sev = (issue.get("severity") or "medium").lower()
            if sev in sev_counts:
                sev_counts[sev] += 1
        per_block[bid] = {
            "tech_score": int(t.get("score") or 0),
            "comp_score": int(c.get("score") or 0),
            "severity_counts": sev_counts,
        }

    # 总分：所有 (tech + comp) / 2 的算术平均（block 数为 0 时为 0）
    if per_block:
        total = sum(
            (b["tech_score"] + b["comp_score"]) / 2.0 for b in per_block.values()
        ) / len(per_block)
    else:
        total = 0.0

    # top_risks：按 (severity_rank, block_id) 取前 5 条 critical/high issue.point
    risks: list[tuple[int, str, str]] = []
    for bid in sorted(all_block_ids):
        for finding in (tech.get(bid), comp.get(bid)):
            if not finding:
                continue
            for issue in (finding.get("issues") or []):
                rank = _SEVERITY_RANK.get(
                    (issue.get("severity") or "medium").lower(), 0,
                )
                if rank >= _SEVERITY_RANK["high"]:
                    risks.append((rank, bid, issue.get("point") or ""))
    risks.sort(key=lambda x: (-x[0], x[1]))
    top_risks = [f"[{bid}] {point}" for _, bid, point in risks[:5] if point]

    # missing_evidence / missing_bonus：从 spec.outline_matrix 与 proposal.blocks 对比
    missing_evidence: list[str] = []
    missing_bonus: list[str] = []
    spec = state.get("spec") or {}
    matrix = spec.get("outline_matrix") or {}
    proposal_blocks = (state.get("proposal") or {}).get("blocks") or {}
    for bid, row in matrix.items():
        body = (proposal_blocks.get(bid) or {}).get("content") or ""
        if (row.get("evidence_required") or "").strip() and not body.strip():
            missing_evidence.append(f"{bid}：{row.get('title', '')}")
        if (row.get("bonus_items") or "").strip() and not body.strip():
            missing_bonus.append(f"{bid}：{row.get('title', '')}")

    report = GlobalReport(
        per_block=per_block,
        total_score=round(float(total), 2),
        top_risks=top_risks,
        missing_evidence=missing_evidence,
        missing_bonus=missing_bonus,
    )
    report_dict = report.to_dict()

    await _emit_frame(emitter, events.report_ready(report_dict))

    return {
        "stage": "report_review",
        "review": {"report": report_dict},
    }


# ─────────────────────────────────────────────
# domain ↔ dict 互转
# ─────────────────────────────────────────────

def _section_to_dict(s: DomainSection) -> dict:
    return {
        "id": s.id,
        "level": s.level,
        "title": s.title,
        "raw_content": s.raw_content,
        "special_marks": list(s.special_marks),
    }


def _dict_to_section(d: dict) -> DomainSection:
    return DomainSection(
        id=d.get("id", ""),
        level=int(d.get("level", 0) or 0),
        title=d.get("title", ""),
        raw_content=d.get("raw_content", ""),
        special_marks=list(d.get("special_marks", []) or []),
    )


def _row_to_dict(r: OutlineMatrixRow) -> dict:
    return {
        "block_id": r.block_id,
        "title": r.title,
        "requirement": r.requirement,
        "key_points": r.key_points,
        "veto_items": r.veto_items,
        "bonus_items": r.bonus_items,
        "score_items": r.score_items,
        "evidence_required": r.evidence_required,
        "constraint_level": r.constraint_level,
        "indicators": r.indicators,
        "error": r.error,
    }


def _dict_to_row(d: dict) -> OutlineMatrixRow:
    return OutlineMatrixRow(
        block_id=d.get("block_id", ""),
        title=d.get("title", ""),
        requirement=d.get("requirement", ""),
        key_points=d.get("key_points", ""),
        veto_items=d.get("veto_items", ""),
        bonus_items=d.get("bonus_items", ""),
        score_items=d.get("score_items", ""),
        evidence_required=d.get("evidence_required", ""),
        constraint_level=d.get("constraint_level", "recommended"),
        indicators=d.get("indicators", ""),
        error=d.get("error", ""),
    )


def _match_to_dict(m: Match) -> dict:
    return {
        "chunk_id": m.chunk_id,
        "score": float(m.score),
        "reason": m.reason,
        "hit_points": list(m.hit_points or []),
    }


def _dict_to_match(d: dict) -> Match:
    return Match(
        chunk_id=d.get("chunk_id"),
        score=float(d.get("score", 0.0) or 0.0),
        reason=d.get("reason", ""),
        hit_points=list(d.get("hit_points") or []),
    )


def _block_output_to_dict(b: BlockOutput) -> dict:
    return {
        "block_id": b.block_id,
        "kind": b.kind,
        "needs_diagram": bool(b.needs_diagram),
        "outline": b.outline,
        "content": b.content,
        "sources": [s.to_dict() if hasattr(s, "to_dict") else s for s in b.sources],
    }


def _dict_to_block_output(d: dict) -> BlockOutput:
    from domain.proposal import Source

    sources_raw = d.get("sources") or []
    sources: list[Source] = []
    for s in sources_raw:
        if isinstance(s, Source):
            sources.append(s)
        elif isinstance(s, dict):
            sources.append(Source(
                material_id=int(s.get("material_id") or 0),
                chunk_index=int(s.get("chunk_index") or 0),
                snippet=s.get("snippet", ""),
            ))
    return BlockOutput(
        block_id=d.get("block_id", ""),
        kind=d.get("kind", "tech"),
        needs_diagram=bool(d.get("needs_diagram", False)),
        outline=d.get("outline", ""),
        content=d.get("content", ""),
        sources=sources,
    )


__all__ = [
    "WorkflowCancelled",
    "zhang_heng_parse_node",
    "zhang_heng_extract_node",
    "shen_kuo_match_node",
    "zhuge_liang_generate_node",
    "wang_anshi_review_node",
    "bao_zheng_review_node",
    "aggregate_review_node",
]
