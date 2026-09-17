"""LangGraph StateGraph 构建（spec §6 编排流程）。

边布局（参考 plan §4.5）：

    START
      → zhang_heng_parse
      → zhang_heng_outline_draft   # ① 依据规范书 + 用户提炼要求拆分应答文件目录
      → gate_outline           # 闸门 1（interrupt_before）
      → zhang_heng_extract     # ② 8 字段逐节提炼：闸门 1 放行后才跑
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

「要求与大纲」分两步：① 拆分章节目录，② 逐节 8 字段提炼。**② 当前关闭**
（``nodes.ENABLE_SECTION_EXTRACT=False``）：闸门 1 放行后 extract 直接收尾，
match 及其下游保持注册但不可达 —— 匹配依赖提炼产出的矩阵，空矩阵匹配不出东西。

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
NODE_OUTLINE_DRAFT = "zhang_heng_outline_draft"
NODE_EXTRACT = "zhang_heng_extract"
GATE_OUTLINE = "gate_outline"
NODE_MATCH = "shen_kuo_match"
GATE_MATERIALS = "gate_materials"
NODE_GENERATE = "zhuge_liang_generate"
GATE_PAUSE = "gate_pause"
NODE_TECH_REVIEW = "wang_anshi_review"
NODE_COMP_REVIEW = "bao_zheng_review"
NODE_AGGREGATE = "aggregate_review"
GATE_REPORT = "gate_report"
NODE_ABORT = "abort_marker"
NODE_COLLECT_GAPS = "collect_gaps"
NODE_CHECK_CONVERGENCE = "check_convergence"
NODE_BUILD_FEEDBACK = "build_feedback"


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
            # 前端刷新后要回填提炼要求输入框、显示「第 N 版」与降级提示
            "outline_instruction": str(
                (state.get("config") or {}).get("outline_instruction") or ""
            ),
            "outline_revision": int(spec.get("outline_revision") or 0),
            "outline_error": str(spec.get("outline_error") or ""),
            # 项目概述生成失败的原因。失败时 doc_summary 是空串，后续每次章节
            # 生成都会少这段全局上下文，必须让用户在闸门 1 就看到。
            "doc_summary_error": str(spec.get("doc_summary_error") or ""),
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

def _route_after_generate(state: WorkflowState) -> str:
    """generate 节点后的条件路由：暂停态 → GATE_PAUSE，正常 → 评审 fan-out。

    user_choice='skip_to_review' 表示用户在暂停态选择"用已生成内容继续评审"，
    这种 case 也直接进评审；否则按 stage 决定。
    """
    if state.get("user_choice") == "skip_to_review":
        return "review"
    if state.get("stage") == "paused":
        return GATE_PAUSE
    return "review"


def _make_pause_gate_node(emitter: Optional[EventEmitter]):
    """暂停闸门：interrupt_after 让 graph 停下，等用户决定继续 / 跳评审 / 放弃。"""
    async def pause_gate(state: WorkflowState) -> WorkflowState:
        if emitter is not None:
            await emitter.emit(events.stage_change("paused", state.get("thread_id", "")))
        return {"stage": "paused"}
    return pause_gate


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
    should_cancel: Optional[Callable[[], bool]] = None,
):
    """组装 LangGraph StateGraph 并 compile。

    Args:
        deps: 5 个 agent 实例 + spec_loader
        emitter: 节点事件出口；None 时图静默运行（适合非流式任务）
        checkpointer: AsyncSqliteSaver；None 时 graph 不持久化（适合单测）
        should_cancel: 节点级取消信号探测，runner 通过它把 _Run.cancel_flag 闭包传进来；
                       generate 等长跑节点在每个 block 开始前调一次决定是否跳过。

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

    async def outline_draft_node(state: WorkflowState) -> WorkflowState:
        return await nodes.zhang_heng_outline_draft_node(
            state, agent=deps.zhang_heng, emitter=emitter,
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
            state, agent=deps.zhuge_liang, emitter=emitter, should_cancel=should_cancel,
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

    async def collect_gaps_node(state: WorkflowState) -> WorkflowState:
        return await nodes.collect_gaps_node(
            state, agent=deps.shen_kuo, emitter=emitter,
        )

    async def check_convergence_node(state: WorkflowState) -> WorkflowState:
        return await nodes.check_convergence_node(state, emitter=emitter)

    async def build_feedback_node(state: WorkflowState) -> WorkflowState:
        return await nodes.build_feedback_node(state, emitter=emitter)

    builder.add_node(NODE_PARSE, parse_node)
    builder.add_node(NODE_OUTLINE_DRAFT, outline_draft_node)
    builder.add_node(NODE_EXTRACT, extract_node)
    builder.add_node(GATE_OUTLINE, _make_gate_node(
        stage="outline_review", gate_name="review_outline", emitter=emitter,
    ))
    builder.add_node(NODE_MATCH, match_node)
    builder.add_node(GATE_MATERIALS, _make_gate_node(
        stage="materials_review", gate_name="review_materials", emitter=emitter,
    ))
    builder.add_node(NODE_GENERATE, generate_node)
    builder.add_node(GATE_PAUSE, _make_pause_gate_node(emitter))
    builder.add_node(NODE_TECH_REVIEW, tech_review_node)
    builder.add_node(NODE_COMP_REVIEW, comp_review_node)
    builder.add_node(NODE_AGGREGATE, aggregate_node)
    builder.add_node(GATE_REPORT, _make_gate_node(
        stage="report_review", gate_name="review_report", emitter=emitter,
    ))
    builder.add_node(NODE_ABORT, _make_abort_node(emitter))
    builder.add_node(NODE_COLLECT_GAPS, collect_gaps_node)
    builder.add_node(NODE_CHECK_CONVERGENCE, check_convergence_node)
    builder.add_node(NODE_BUILD_FEEDBACK, build_feedback_node)

    # ── 边 ───────────────────────────────────────
    builder.add_edge(START, NODE_PARSE)
    builder.add_edge(NODE_PARSE, NODE_OUTLINE_DRAFT)
    # 派生目录后**直接停闸门1**，逐节 8 字段提炼（每个派生节点一次模型调用）留到
    # 闸门1 放行后再跑。目录是用户要先看、先改的东西，不该每次都被提炼的账绑住。
    builder.add_edge(NODE_OUTLINE_DRAFT, GATE_OUTLINE)
    builder.add_edge(GATE_OUTLINE, NODE_EXTRACT)
    # 第二步（逐节提炼）关闭时 extract 直接收尾：匹配依赖它产出的矩阵，空矩阵
    # 匹配不出东西，往下走只会得到一个空闸门 2。开关与节点行为同源于
    # nodes.ENABLE_SECTION_EXTRACT，由它统一切换，避免两处各改一遍、漏掉一边。
    if nodes.ENABLE_SECTION_EXTRACT:
        builder.add_edge(NODE_EXTRACT, NODE_MATCH)
    else:
        builder.add_edge(NODE_EXTRACT, END)
    builder.add_edge(NODE_MATCH, GATE_MATERIALS)
    builder.add_edge(GATE_MATERIALS, NODE_COLLECT_GAPS)
    builder.add_edge(NODE_COLLECT_GAPS, NODE_GENERATE)

    # generate → 条件路由：暂停 → GATE_PAUSE（interrupt_after 让 graph 停下）
    #                    → 否则 fan-out 到两个评审 agent。
    # LangGraph add_conditional_edges 不直接支持 fan-out 到多个目标，所以暂停态走单条边，
    # 正常态用一个 sentinel 目标 'review'，再由 GATE_PAUSE 之外另起一段连到评审。
    # 这里用更简洁的方式：generate 出来分两条普通边到两个评审 + 一条到 PAUSE。LangGraph
    # 在 conditional_edges 内允许返回 list 表示多目标。
    def _generate_fanout(state: WorkflowState) -> list[str]:
        if state.get("user_choice") == "skip_to_review":
            return [NODE_TECH_REVIEW, NODE_COMP_REVIEW]
        if state.get("stage") == "paused":
            return [GATE_PAUSE]
        return [NODE_TECH_REVIEW, NODE_COMP_REVIEW]

    builder.add_conditional_edges(
        NODE_GENERATE,
        _generate_fanout,
        {
            NODE_TECH_REVIEW: NODE_TECH_REVIEW,
            NODE_COMP_REVIEW: NODE_COMP_REVIEW,
            GATE_PAUSE: GATE_PAUSE,
        },
    )

    # GATE_PAUSE 后的条件路由：
    #   user_choice='skip_to_review' → 直接评审（用户选择"用已生成内容继续评审"）
    #   user_choice='resume' → 回 generate 节点（已生成 block 在 generate_node 内会被跳过）
    #   其它 → END（用户没决定，graph 停在 GATE_PAUSE，由 interrupt_after 暂停）
    def _route_after_pause(state: WorkflowState) -> list[str] | str:
        choice = state.get("user_choice", "")
        if choice == "skip_to_review":
            return [NODE_TECH_REVIEW, NODE_COMP_REVIEW]
        if choice == "resume":
            return NODE_GENERATE
        return END

    builder.add_conditional_edges(
        GATE_PAUSE,
        _route_after_pause,
        {
            NODE_TECH_REVIEW: NODE_TECH_REVIEW,
            NODE_COMP_REVIEW: NODE_COMP_REVIEW,
            NODE_GENERATE: NODE_GENERATE,
            END: END,
        },
    )

    # fan-in：start_key 是 list 时，aggregate 等齐 list 中所有上游
    builder.add_edge([NODE_TECH_REVIEW, NODE_COMP_REVIEW], NODE_AGGREGATE)
    builder.add_edge(NODE_AGGREGATE, NODE_CHECK_CONVERGENCE)

    def _route_convergence(state: WorkflowState) -> str:
        """refine 与 max_iterations 都先去 build_feedback，由其出边再区分。"""
        # 人工选择“仅复审”时不自动改写正文，结果交回用户处理。
        if state.get("user_choice") == "review_only":
            return GATE_REPORT
        status = (
            (state.get("review") or {}).get("convergence") or {}
        ).get("status", "")
        return NODE_BUILD_FEEDBACK if status in ("refine", "max_iterations") \
            else GATE_REPORT

    builder.add_conditional_edges(
        NODE_CHECK_CONVERGENCE,
        _route_convergence,
        {
            NODE_BUILD_FEEDBACK: NODE_BUILD_FEEDBACK,
            GATE_REPORT: GATE_REPORT,
        },
    )

    def _route_feedback(state: WorkflowState) -> str:
        """refine 回环补料；max_iterations 直连闸门 3。"""
        status = (
            (state.get("review") or {}).get("convergence") or {}
        ).get("status", "")
        return NODE_COLLECT_GAPS if status == "refine" else GATE_REPORT

    builder.add_conditional_edges(
        NODE_BUILD_FEEDBACK,
        _route_feedback,
        {
            NODE_COLLECT_GAPS: NODE_COLLECT_GAPS,
            GATE_REPORT: GATE_REPORT,
        },
    )

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
        interrupt_after=[GATE_OUTLINE, GATE_MATERIALS, GATE_PAUSE, GATE_REPORT],
    )


__all__ = [
    "build_graph",
    "GraphDeps",
    "NODE_PARSE",
    "NODE_OUTLINE_DRAFT",
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
    "NODE_COLLECT_GAPS",
    "NODE_CHECK_CONVERGENCE",
    "NODE_BUILD_FEEDBACK",
]
