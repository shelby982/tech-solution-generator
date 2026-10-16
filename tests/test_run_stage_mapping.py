"""运行期 stage 的**单一来源**：图的"下一个节点" → workflow_runs.stage。

为什么值得单独钉：
前端 boot / 状态行 / 阶段导航都读 `workflow_runs.stage`。以前只有 extract / match
两个节点会写这张表，于是"回修生成"（workspace_action）期间库里一直停在上一版 stage
—— 实测：检查点是 generating，表里还是 paused，界面显示的阶段是错的。
"""
from __future__ import annotations

import pytest

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


@pytest.fixture
async def runs_db(tmp_path):
    """独立小库 + 用完必关。

    ⚠️ 必须用 fixture（yield + close）：断言失败时后面的 `await conn.close()` 不会执行，
    aiosqlite 的后台线程会吊住解释器 —— 实测表现为"整个测试套件卡住不退出"。
    """
    import aiosqlite
    from db import init_db

    conn = await aiosqlite.connect(str(tmp_path / "runs.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_runs_are_attributed_to_a_package(runs_db):
    """**每个标包是单独的内容**：运行要能按 (target, package_no) 归属与检索。

    否则前端无法"按标包找运行"，跑标包 1 时也会把标包 2 的目录清掉。
    """
    from domain.review.repository import WorkflowRunRepository

    conn = runs_db
    repo = WorkflowRunRepository(conn)
    await repo.create("t-pkg1", 1, stage="idle", target="标的1", package_no="1")
    await repo.create("t-pkg2", 1, stage="idle", target="标的1", package_no="2")
    await repo.create("t-pkg2b", 1, stage="idle", target="标的1", package_no="2")
    # created_at 同秒会踩平时间序（真机同一秒建多条很常见）→ 显式写成不同时刻
    for tid, ts in (("t-pkg1", "2026-01-01 00:00:01"),
                    ("t-pkg2", "2026-01-01 00:00:02"),
                    ("t-pkg2b", "2026-01-01 00:00:03")):
        await conn.execute("UPDATE workflow_runs SET created_at = ? WHERE thread_id = ?", (ts, tid))
    await conn.commit()

    assert (await repo.latest_for_scope(1, "标的1", "1"))["thread_id"] == "t-pkg1"
    # 同一标包取**最新**那条
    assert (await repo.latest_for_scope(1, "标的1", "2"))["thread_id"] == "t-pkg2b"
    # 没跑过的标包 → None（前端据此新建运行）
    assert await repo.latest_for_scope(1, "标的1", "3") is None

    # 确认标包时把运行归属写进去：改归属后应在**新**标包下被检索到
    await repo.set_scope("t-pkg1", target="标的2", package_no="2")
    assert (await repo.latest_for_scope(1, "标的2", "2"))["thread_id"] == "t-pkg1"
    # 原标包只剩原来那两条，最新仍是 t-pkg2b（归属没串）
    assert (await repo.latest_for_scope(1, "标的1", "2"))["thread_id"] == "t-pkg2b"
    assert await repo.latest_for_scope(1, "标的1", "1") is None


def test_needs_outline_recovery_predicate():
    """图在 outline_draft 之后直接收尾 + 应答目录未派生 + 有解析结果 → 需要补跑派生。

    实测数据（2026-10-16，thread fda5d747）：
      snap.next=() · outline_revision 无值 · source_toc=367 · toc=367（还是原文占位）
    对照正常线程（1602b0ca）：outline_revision 有值 · toc=165 · next=('zhang_heng_extract',)
    """
    from orchestrator.runner import needs_outline_recovery

    assert needs_outline_recovery(next_nodes=(), outline_revision=None, source_toc=["1", "2"]) is True
    assert needs_outline_recovery(next_nodes=(), outline_revision={"x": 1}, source_toc=["1"]) is False, "已派生就不该补跑"
    assert needs_outline_recovery(next_nodes=(), outline_revision=None, source_toc=[]) is False, "没有解析结果，无从派生"
    assert needs_outline_recovery(next_nodes=("zhang_heng_extract",), outline_revision=None, source_toc=["1"]) is False, "图还有下一步，不该插手"


def test_resume_auto_has_outline_recovery_branch():
    """resume_auto 必须接上"图已收尾但目录没派生"这条恢复路径。"""
    import inspect
    from orchestrator.runner import WorkflowRunner

    src = inspect.getsource(WorkflowRunner.resume_auto)
    assert "needs_outline_recovery" in src
    assert "resume_outline_draft" in src


def test_resume_outline_draft_mirrors_resume_extract():
    """补跑派生要照抄 resume_extract 的手法：借闸门身份 + user_choice=approve。"""
    import inspect
    from orchestrator.runner import WorkflowRunner

    src = inspect.getsource(WorkflowRunner.resume_outline_draft)
    assert "GATE_OUTLINE" in src and "as_node=" in src
    assert '"user_choice": "approve"' in src
    assert "_resume_until_pause" in src
