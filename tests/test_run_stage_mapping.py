"""运行期 stage 的**单一来源**：图的"下一个节点" → workflow_runs.stage。

为什么值得单独钉：
前端 boot / 状态行 / 阶段导航都读 `workflow_runs.stage`。以前只有 extract / match
两个节点会写这张表，于是"回修生成"（workspace_action）期间库里一直停在上一版 stage
—— 实测：检查点是 generating，表里还是 paused，界面显示的阶段是错的。
"""
from __future__ import annotations

from orchestrator.runner import next_phase_stage


def test_maps_every_phase_node():
    assert next_phase_stage({"zhang_heng_parse"}) == "parsing"
    assert next_phase_stage({"zhang_heng_outline_draft"}) == "outline_review"
    assert next_phase_stage({"gate_outline"}) == "outline_review"
    assert next_phase_stage({"zhang_heng_extract"}) == "extracting"
    assert next_phase_stage({"shen_kuo_match"}) == "matching"
    assert next_phase_stage({"gate_materials"}) == "materials_review"
    assert next_phase_stage({"zhuge_liang_generate"}) == "generating"
    assert next_phase_stage({"gate_report"}) == "report_review"


def test_review_nodes_all_report_reviewing():
    """评审是个多节点循环，任何一个在跑都应显示 reviewing（否则界面在这段时间停住）。"""
    for node in ("wang_anshi_review", "bao_zheng_review", "aggregate_review",
                 "collect_gaps", "check_convergence", "build_feedback"):
        assert next_phase_stage({node}) == "reviewing", node


def test_no_next_or_unknown_node_returns_none():
    """图已收尾 / 不认识的节点：不写表（交给 _sync_stage 按 checkpoint 决定），返回 None。"""
    assert next_phase_stage(set()) is None
    assert next_phase_stage(None) is None
    assert next_phase_stage({"some_future_node"}) is None


def test_all_mapped_stages_are_valid_for_the_table():
    """映射出的每个值都必须能写进 workflow_runs（否则 update_stage 抛 ValueError）。"""
    from orchestrator.runner import _NEXT_PHASE_STAGE
    from domain.review.repository import _VALID_STAGES

    bad = {v for v in _NEXT_PHASE_STAGE.values() if v not in _VALID_STAGES}
    assert not bad, f"以下 stage 不在 _VALID_STAGES 里，写库会失败：{bad}"


def test_multi_node_next_picks_earliest_in_graph_order():
    """生成与评审同时出现在 next 时，必须选 generating（字典序会误选 reviewing）。"""
    assert next_phase_stage({"zhuge_liang_generate", "aggregate_review"}) == "generating"
    assert next_phase_stage({"wang_anshi_review", "zhang_heng_extract"}) == "extracting"
    assert next_phase_stage({"gate_materials", "zhuge_liang_generate"}) == "materials_review"


def test_resume_auto_only_acts_on_preparation_stages():
    """自动续跑只处理"准备/生成/评审"这类运行期阶段；paused 与空 next 一律不动。

    新 IA 里"准备"不是用户动作 → 断点由系统自己接上；但用户主动取消（paused）
    必须尊重，不能擅自续跑（那会变成"取消不掉"，还会白烧模型额度）。
    """
    import inspect
    from orchestrator.runner import WorkflowRunner

    src = inspect.getsource(WorkflowRunner.resume_auto)
    # 只在没有 task 在跑时生效
    assert "run.task and not run.task.done()" in src
    # paused 直接返回，不续跑
    assert 'state.get("stage") == "paused"' in src
    # 四个可续跑阶段各有一条分支
    for stage in ("extracting", "matching", "generating", "reviewing"):
        assert f'"{stage}"' in src, stage
