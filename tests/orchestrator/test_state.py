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


def test_merge_state_merges_new_proposal_fields():
    """material_requests / updated_blocks 走 proposal 的浅 merge，不冲掉 blocks。"""
    from orchestrator.state import merge_state

    base = {"proposal": {"blocks": {"s1": {"content": "x"}}, "regenerate_targets": []}}
    patch = {
        "proposal": {
            "material_requests": {"s1": [{"query": "业绩证明", "reason": "缺证据"}]},
            "updated_blocks": ["s1"],
        }
    }

    merged = merge_state(base, patch)

    assert merged["proposal"]["blocks"] == {"s1": {"content": "x"}}
    assert merged["proposal"]["material_requests"] == {
        "s1": [{"query": "业绩证明", "reason": "缺证据"}]
    }
    assert merged["proposal"]["updated_blocks"] == ["s1"]


def test_merge_state_merges_new_review_fields():
    """feedback / convergence 走 review 的浅 merge，不冲掉 findings。"""
    from orchestrator.state import merge_state

    base = {"review": {"tech_findings": {"s1": {"score": 50}}}}
    patch = {
        "review": {
            "feedback": {"s1": {"issues": [], "scores": {"tech": 50, "comp": 40}}},
            "convergence": {"status": "refine", "unconverged_blocks": ["s1"]},
        }
    }

    merged = merge_state(base, patch)

    assert merged["review"]["tech_findings"] == {"s1": {"score": 50}}
    assert merged["review"]["feedback"]["s1"]["scores"]["tech"] == 50
    assert merged["review"]["convergence"]["status"] == "refine"


def test_iteration_is_scalar_and_overwrites():
    """iteration 是标量，patch 直接覆盖而非合并。"""
    from orchestrator.state import merge_state

    assert merge_state({"iteration": 1}, {"iteration": 2})["iteration"] == 2
    assert merge_state({}, {"iteration": 1})["iteration"] == 1


# ─────────────────────────────────────────────
# 闭环字段的声明本身
# ─────────────────────────────────────────────

def test_closed_loop_fields_are_declared():
    """协同闭环字段必须声明在 TypedDict 上。

    上面几个 merge_state 测试无法覆盖这一条：merge_state 只按字面 key 做浅
    union，不认识 TypedDict 声明；而 TypedDict 的注解在运行时被擦除，新字段
    加不加它都照常合并成功。所以那些测试在改动前也是绿的，给不出回归保护。
    字段一旦被误删，唯一的运行时报错会推迟到 Task 3-11 的消费方，本用例把
    它提前到声明处。
    """
    from orchestrator.state import ProposalState, ReviewState, WorkflowState

    assert "updated_blocks" in ProposalState.__annotations__
    assert "material_requests" in ProposalState.__annotations__
    assert "feedback" in ReviewState.__annotations__
    assert "convergence" in ReviewState.__annotations__
    assert "iteration" in WorkflowState.__annotations__
