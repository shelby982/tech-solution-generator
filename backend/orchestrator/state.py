"""LangGraph WorkflowState 定义（spec §5）。

5 个 agent 之间的唯一通信介质。设计原则：每个 agent 只读自己依赖的字段、只写自己产出的字段。

字段写入约定（spec §5）：
- 张衡：只写 spec.*
- 沈括：只写 materials.matches（materials.chunks 由 routes 注入）
- 诸葛亮：只写 proposal.blocks（regenerate_targets 由 routes 写、agent 读完清空）
- 王安石：只写 review.tech_findings
- 包拯：只写 review.compliance_findings
- Orchestrator 汇总节点：只写 review.report

state 是易失中间态（每次工作流跑产出的 outline_matrix/matches/findings 都进 checkpoint）；
最终 proposal.blocks 完成后由 routes 写回 blocks 表作为正式数据。
"""

from typing import Literal, TypedDict


# ─────────────────────────────────────────────
# 子结构（用 dict 表达，不强类型化以保持 LangGraph
# state merging 灵活性）
# ─────────────────────────────────────────────

class SpecState(TypedDict, total=False):
    """张衡产出。"""
    doc_id: str
    doc_title: str
    doc_summary: str
    toc: list[dict]                    # 列表元素是 Section.to_dict()
    outline_matrix: dict[str, dict]    # block_id → OutlineMatrixRow.to_dict()


class MaterialsState(TypedDict, total=False):
    """沈括产出（chunks 由 routes 注入）。"""
    chunks: list[dict]                 # Chunk.to_dict()
    matches: dict[str, list[dict]]     # block_id → list[Match.to_dict()-like]


class ProposalState(TypedDict, total=False):
    """诸葛亮产出。"""
    blocks: dict[str, dict]            # block_id → BlockOutput.to_dict()
    regenerate_targets: list[str]


class ReviewState(TypedDict, total=False):
    """两位评审 agent + aggregate 节点产出。"""
    tech_findings: dict[str, dict]         # block_id → Finding.to_dict()
    compliance_findings: dict[str, dict]
    report: dict                            # GlobalReport.to_dict()


class UserConfig(TypedDict, total=False):
    """用户配置（routes 在 start 时注入）。"""
    tone: Literal["official", "tech", "concise"]
    target_words: int
    doc_template: str


class ErrorEntry(TypedDict, total=False):
    """state.errors 的单项。"""
    agent: str
    block_id: str       # 可选
    message: str
    timestamp: str
    retryable: bool


# 合法 stage 集合（与 domain.review.repository._VALID_STAGES 同步）
StageLiteral = Literal[
    "idle", "parsing", "outline_review",
    "matching", "materials_review",
    "generating", "reviewing",
    "report_review", "done", "aborted",
]

UserChoiceLiteral = Literal[
    "approve", "edit", "regen_blocks", "abort", "",
]


# ─────────────────────────────────────────────
# 顶层 WorkflowState
# ─────────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    """LangGraph 编排的全局 state。

    所有字段均为可选（total=False），LangGraph 节点返回的"切片 dict"会浅 merge 到此。
    嵌套字段（如 spec.outline_matrix）的合并由 reducer / 节点自身负责，TypedDict 只
    描述结构，不强制 merge 语义。
    """

    # ── 元信息 ───────────────────────────────────
    project_id: int
    thread_id: str
    stage: StageLiteral
    user_choice: UserChoiceLiteral
    cancel_requested: bool

    # ── 各 agent 产出 ────────────────────────────
    spec: SpecState
    materials: MaterialsState
    proposal: ProposalState
    review: ReviewState

    # ── 用户配置与错误收集 ───────────────────────
    config: UserConfig
    errors: list[ErrorEntry]


# ─────────────────────────────────────────────
# Helper：state 浅 merge 工具（供 routes / 测试使用）
# ─────────────────────────────────────────────

def merge_state(base: dict, patch: dict) -> dict:
    """对两个 state dict 做"顶层 + 一级嵌套字段 dict"的浅 merge。

    规则：
    - 顶层 key 在 patch 中存在则覆盖 / 合并
    - 嵌套子结构（spec / materials / proposal / review / config）会做 dict union
      （以 patch 优先），其它顶层字段（含 list 类）直接以 patch 覆盖
    - errors 列表会做 append（base.errors + patch.errors），避免历史错误丢失

    嵌套不递归：spec.outline_matrix 这种二级 dict 会被 patch 整体替换，不会
    逐 block_id 合并。如需逐 block 合并，由调用方先准备好合并后的 patch。

    本函数不替代 LangGraph 自身的 reducer，仅用于：
    - 测试时拼装 expected state
    - routes 层在 update_state 前合并外部用户输入

    特别提醒：errors append 仅在本 helper 内生效；LangGraph 节点之间的 state
    merge 默认是覆盖式，节点返回 errors 列表不会自动 append。如需在
    LangGraph 内做 append 合并，需用 Annotated[list, operator.add] 等
    reducer 显式声明（Task 4.5 接入 graph 时再处理）。
    """
    result = dict(base)
    nested_keys = {"spec", "materials", "proposal", "review", "config"}
    for key, value in patch.items():
        if key == "errors":
            # 列表追加（容忍 base/patch 任一为空）
            base_errors = list(result.get("errors") or [])
            patch_errors = list(value or [])
            result["errors"] = base_errors + patch_errors
        elif key in nested_keys and isinstance(value, dict):
            existing = result.get(key)
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(value)
                result[key] = merged
            else:
                result[key] = dict(value)
        else:
            result[key] = value
    return result


__all__ = [
    "WorkflowState",
    "SpecState",
    "MaterialsState",
    "ProposalState",
    "ReviewState",
    "UserConfig",
    "ErrorEntry",
    "StageLiteral",
    "UserChoiceLiteral",
    "merge_state",
]
