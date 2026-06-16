"""WorkflowState 与 merge_state 单测。"""

from orchestrator.state import (
    WorkflowState,
    merge_state,
)


# ─────────────────────────────────────────────
# WorkflowState 结构（TypedDict total=False，全部可选）
# ─────────────────────────────────────────────

def test_workflow_state_accepts_partial_dict():
    """空 dict 也合法（total=False）。"""
    state: WorkflowState = {}
    assert state == {}


def test_workflow_state_with_minimal_fields():
    state: WorkflowState = {
        "project_id": 1,
        "thread_id": "tid-1",
        "stage": "idle",
    }
    assert state["project_id"] == 1
    assert state["stage"] == "idle"


def test_workflow_state_full_population():
    """每个子结构 dict 都能被 TypedDict 接受（运行时 TypedDict 不做检查，
    主要验证字段名稳定）。"""
    state: WorkflowState = {
        "project_id": 1,
        "thread_id": "tid-1",
        "stage": "generating",
        "user_choice": "",
        "cancel_requested": False,
        "spec": {
            "doc_id": "d1", "doc_title": "T", "doc_summary": "S",
            "toc": [{"id": "s1", "level": 1, "title": "X"}],
            "outline_matrix": {"s1": {"requirement": "R"}},
        },
        "materials": {
            "chunks": [{"id": 1, "content": "x"}],
            "matches": {"s1": [{"chunk_id": 1, "score": 9.0}]},
        },
        "proposal": {
            "blocks": {"s1": {"content": "已生成"}},
            "regenerate_targets": [],
        },
        "review": {
            "tech_findings": {},
            "compliance_findings": {},
            "report": {},
        },
        "config": {"tone": "official", "target_words": 600, "doc_template": ""},
        "errors": [],
    }
    assert state["materials"]["matches"]["s1"][0]["score"] == 9.0


# ─────────────────────────────────────────────
# merge_state 浅 merge
# ─────────────────────────────────────────────

def test_merge_state_top_level_overwrite():
    base: WorkflowState = {"stage": "idle", "thread_id": "old"}
    patch: WorkflowState = {"stage": "parsing"}
    result = merge_state(base, patch)
    assert result["stage"] == "parsing"
    assert result["thread_id"] == "old"


def test_merge_state_nested_dict_union_with_patch_priority():
    """spec.outline_matrix 是嵌套 dict，merge 后 base 中未覆盖的 key 保留，
    patch 中存在的 key 以 patch 为准。"""
    base: WorkflowState = {
        "spec": {
            "doc_id": "d1", "doc_title": "old",
            "outline_matrix": {"s1": {"requirement": "old"}},
        }
    }
    patch: WorkflowState = {
        "spec": {
            "doc_title": "new",
            "outline_matrix": {"s2": {"requirement": "new"}},
        }
    }
    result = merge_state(base, patch)
    assert result["spec"]["doc_id"] == "d1"  # base 保留
    assert result["spec"]["doc_title"] == "new"  # patch 覆盖
    # 注意：outline_matrix 整体被 patch 覆盖（merge_state 是浅 merge，不递归）
    assert result["spec"]["outline_matrix"] == {"s2": {"requirement": "new"}}


def test_merge_state_errors_appended_not_overwritten():
    base: WorkflowState = {
        "errors": [{"agent": "a", "message": "first"}],
    }
    patch: WorkflowState = {
        "errors": [{"agent": "b", "message": "second"}],
    }
    result = merge_state(base, patch)
    assert len(result["errors"]) == 2
    assert result["errors"][0]["message"] == "first"
    assert result["errors"][1]["message"] == "second"


def test_merge_state_does_not_mutate_base():
    base: WorkflowState = {"stage": "idle", "spec": {"doc_id": "d1"}}
    base_snapshot = {"stage": "idle", "spec": {"doc_id": "d1"}}
    patch: WorkflowState = {"stage": "parsing", "spec": {"doc_title": "T"}}
    merge_state(base, patch)
    assert base == base_snapshot, "merge_state 不应修改 base"


def test_merge_state_handles_missing_nested_dict_in_base():
    base: WorkflowState = {}
    patch: WorkflowState = {"spec": {"doc_id": "d1"}}
    result = merge_state(base, patch)
    assert result["spec"] == {"doc_id": "d1"}


def test_merge_state_handles_empty_patch():
    base: WorkflowState = {"stage": "idle"}
    result = merge_state(base, {})
    assert result == base


def test_merge_state_handles_empty_base():
    patch: WorkflowState = {"stage": "parsing"}
    result = merge_state({}, patch)
    assert result == patch
