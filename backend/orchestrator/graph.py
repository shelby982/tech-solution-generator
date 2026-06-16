"""LangGraph StateGraph 构建（spec §6 编排流程）。

边布局（参考 plan §4.5）：

    START
      → zhang_heng_parse
      → zhang_heng_extract
      → gate_outline           # 闸门 1（interrupt_before）
      → shen_kuo_match
      → gate_materials         # 闸门 2（interrupt_before）
      → zhuge_liang_generate
      → fan-out: wang_anshi_review, bao_zheng_review
      → aggregate_review       # fan-in，等齐两侧 findings
      → gate_report            # 闸门 3（interrupt_before）
      → conditional:
            approve      → END
            regen_blocks → zhuge_liang_generate
            abort        → ABORT

闸门用 LangGraph 的 ``interrupt_before`` 实现：到达闸门节点前 graph 暂停，state 已
经持久化；前端按 ``stage`` 跳到对应编辑器，提交后 routes 调
``graph.aupdate_state(...) + graph.ainvoke(None, config)`` 续跑。

闸门节点本身只做：发 ``stage_change`` + ``gate_open`` 事件；不接触业务逻辑，
节点内不再调用 ``interrupt()``，让控制流完全可观测、可单测。

ABORT：图末端的纯标记节点，只发 ``aborted`` 事件并把 stage 改 ``aborted``。
路由层抓 ``WorkflowCancelled`` 异常时另走一条 abort 流程，不进图。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.bao_zheng import BaoZhengAgent
from agents.shen_kuo import ShenKuoAgent
from agents.wang_anshi import WangAnshiAgent
from agents.zhang_heng import ZhangHengAgent
from agents.zhuge_liang import ZhugeLiangAgent
from orchestrator import events, nodes
from orchestrator.events import EventEmitter
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 节点名（同步用作 SSE stage / interrupt_before key）
# ─────────────────────────────────────────────

NODE_PARSE = "zhang_heng_parse"
NODE_EXTRACT = "zhang_heng_extract"
GATE_OUTLINE = "gate_outline"
NODE_MATCH = "shen_kuo_match"
GATE_MATERIALS = "gate_materials"
NODE_GENERATE = "zhuge_liang_generate"
NODE_TECH_REVIEW = "wang_anshi_review"
NODE_COMP_REVIEW = "bao_zheng_review"
NODE_AGGREGATE = "aggregate_review"
GATE_REPORT = "gate_report"
NODE_ABORT = "abort_marker"


# ─────────────────────────────────────────────
# 依赖注入容器
# ─────────────────────────────────────────────

# spec 文件加载器签名：project_id → (binary_io, suffix, filename)
SpecLoader = Callable[[int], Awaitable[tuple[Any, str, str]]]


@dataclass
class GraphDeps:
    """build_graph 需要的全部外部依赖；routes / 单测显式注入。"""
    zhang_heng: ZhangHengAgent
    shen_kuo: ShenKuoAgent
    zhuge_liang: ZhugeLiangAgent
    wang_anshi: WangAnshiAgent
    bao_zheng: BaoZhengAgent
    spec_loader: SpecLoader


# ─────────────────────────────────────────────
# 闸门节点：纯标记节点 + emit 事件
# ─────────────────────────────────────────────

def _make_gate_node(
    *,
    stage: str,
    gate_name: str,
    emitter: Optional[EventEmitter],
):
    """生成闸门节点函数。state 写入 ``stage``；事件推送 ``stage_change`` + ``gate_open``。

    interrupt_before 配置在 compile 时；闸门节点本身只负责语义标记。
    """
    async def gate(state: WorkflowState) -> WorkflowState:
        # 即使未挂 emitter 也要回写 stage，让 checkpoint 在闸门处带正确状态。
        if emitter is not None:
            await emitter.emit(events.stage_change(stage, state.get("thread_id", "")))
            await emitter.emit(events.gate_open(gate_name, _gate_snapshot(state, gate_name)))
        return {"stage": stage}

    return gate


def _gate_snapshot(state: WorkflowState, gate_name: str) -> dict:
    """挑前端编辑器需要的最小快照（避免回传整个 state）。"""
    spec = state.get("spec") or {}
    if gate_name == "review_outline":
        return {
            "doc_title": spec.get("doc_title", ""),
            "doc_summary": spec.get("doc_summary", ""),
            "toc": list(spec.get("toc") or []),
            "outline_matrix": dict(spec.get("outline_matrix") or {}),
        }
    if gate_name == "review_materials":
        return {
            "matches": dict((state.get("materials") or {}).get("matches") or {}),
        }
    if gate_name == "review_report":
        return {
            "report": dict((state.get("review") or {}).get("report") or {}),
            "blocks": dict((state.get("proposal") or {}).get("blocks") or {}),
        }
    return {}


# ─────────────────────────────────────────────
# ABORT 节点
# ─────────────────────────────────────────────

def _make_abort_node(emitter: Optional[EventEmitter]):
    async def abort(state: WorkflowState) -> WorkflowState:
        if emitter is not None:
            await emitter.emit(events.aborted("user_abort"))
        return {"stage": "aborted"}
    return abort


# ─────────────────────────────────────────────
# 闸门 3 后的条件路由
# ─────────────────────────────────────────────

def _route_after_report(state: WorkflowState) -> str:
    """读取 ``user_choice`` 决定下一步。

    routes 在闸门 3 提交时把 ``user_choice`` 写进 state，再续跑。
    返回值是 LangGraph 节点名或 END。
    """
    choice = state.get("user_choice", "")
    if choice == "regen_blocks":
        return NODE_GENERATE
    if choice == "abort":
        return NODE_ABORT
    # approve / 空 / edit 都视作通过 → 终结
    return END


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def build_graph(
    deps: GraphDeps,
    *,
    emitter: Optional[EventEmitter] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
):
    """组装 LangGraph StateGraph 并 compile。

    Args:
        deps: 5 个 agent 实例 + spec_loader
        emitter: 节点事件出口；None 时图静默运行（适合非流式任务）
        checkpointer: AsyncSqliteSaver；None 时 graph 不持久化（适合单测）

    返回 ``CompiledStateGraph``，闸门节点已配置 interrupt_before。
    """
    builder = StateGraph(WorkflowState)

    # ── 业务节点：把 agent + emitter 闭包进去 ─────
    async def parse_node(state: WorkflowState) -> WorkflowState:
        return await nodes.zhang_heng_parse_node(
            state,
            agent=deps.zhang_heng,
            spec_loader=deps.spec_loader,
            emitter=emitter,
        )

    async def extract_node(state: WorkflowState) -> WorkflowState:
        return await nodes.zhang_heng_extract_node(
            state, agent=deps.zhang_heng, emitter=emitter,
        )

    async def match_node(state: WorkflowState) -> WorkflowState:
        return await nodes.shen_kuo_match_node(
            state, agent=deps.shen_kuo, emitter=emitter,
        )

    async def generate_node(state: WorkflowState) -> WorkflowState:
        return await nodes.zhuge_liang_generate_node(
            state, agent=deps.zhuge_liang, emitter=emitter,
        )

    async def tech_review_node(state: WorkflowState) -> WorkflowState:
        return await nodes.wang_anshi_review_node(
            state, agent=deps.wang_anshi, emitter=emitter,
        )

    async def comp_review_node(state: WorkflowState) -> WorkflowState:
        return await nodes.bao_zheng_review_node(
            state, agent=deps.bao_zheng, emitter=emitter,
        )

    async def aggregate_node(state: WorkflowState) -> WorkflowState:
        return await nodes.aggregate_review_node(state, emitter=emitter)

    builder.add_node(NODE_PARSE, parse_node)
    builder.add_node(NODE_EXTRACT, extract_node)
    builder.add_node(GATE_OUTLINE, _make_gate_node(
        stage="outline_review", gate_name="review_outline", emitter=emitter,
    ))
    builder.add_node(NODE_MATCH, match_node)
    builder.add_node(GATE_MATERIALS, _make_gate_node(
        stage="materials_review", gate_name="review_materials", emitter=emitter,
    ))
    builder.add_node(NODE_GENERATE, generate_node)
    builder.add_node(NODE_TECH_REVIEW, tech_review_node)
    builder.add_node(NODE_COMP_REVIEW, comp_review_node)
    builder.add_node(NODE_AGGREGATE, aggregate_node)
    builder.add_node(GATE_REPORT, _make_gate_node(
        stage="report_review", gate_name="review_report", emitter=emitter,
    ))
    builder.add_node(NODE_ABORT, _make_abort_node(emitter))

    # ── 边 ───────────────────────────────────────
    builder.add_edge(START, NODE_PARSE)
    builder.add_edge(NODE_PARSE, NODE_EXTRACT)
    builder.add_edge(NODE_EXTRACT, GATE_OUTLINE)
    builder.add_edge(GATE_OUTLINE, NODE_MATCH)
    builder.add_edge(NODE_MATCH, GATE_MATERIALS)
    builder.add_edge(GATE_MATERIALS, NODE_GENERATE)

    # fan-out：generate 完后两评审 agent 并发；LangGraph 看到从同一节点出多条边
    # 自动 schedule 在同一 super-step 内并行执行
    builder.add_edge(NODE_GENERATE, NODE_TECH_REVIEW)
    builder.add_edge(NODE_GENERATE, NODE_COMP_REVIEW)

    # fan-in：start_key 是 list 时，aggregate 等齐 list 中所有上游
    builder.add_edge([NODE_TECH_REVIEW, NODE_COMP_REVIEW], NODE_AGGREGATE)
    builder.add_edge(NODE_AGGREGATE, GATE_REPORT)

    # 闸门 3 后的条件路由
    builder.add_conditional_edges(
        GATE_REPORT,
        _route_after_report,
        {
            NODE_GENERATE: NODE_GENERATE,
            NODE_ABORT: NODE_ABORT,
            END: END,
        },
    )
    builder.add_edge(NODE_ABORT, END)

    return builder.compile(
        checkpointer=checkpointer,
        # interrupt_after：闸门节点先跑（写入正确 stage、发 gate_open 事件），
        # 跑完后再暂停 —— 这样 state.stage 已经反映了"正等待人工"的状态
        interrupt_after=[GATE_OUTLINE, GATE_MATERIALS, GATE_REPORT],
    )


__all__ = [
    "build_graph",
    "GraphDeps",
    "NODE_PARSE",
    "NODE_EXTRACT",
    "GATE_OUTLINE",
    "NODE_MATCH",
    "GATE_MATERIALS",
    "NODE_GENERATE",
    "NODE_TECH_REVIEW",
    "NODE_COMP_REVIEW",
    "NODE_AGGREGATE",
    "GATE_REPORT",
    "NODE_ABORT",
]
