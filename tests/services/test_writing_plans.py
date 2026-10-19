"""技术评分点一级目录的编写计划：先规划 → 确认 → 再生成。"""
from __future__ import annotations

import json

import aiosqlite
import pytest

from db import init_db
from services.writing_plans import build_plan, confirm_plan, plan_units


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "plans.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name, status) "
        "VALUES (1, '1', '2', '某标包', 'confirmed')")
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, package_id) "
        "VALUES ('t1', 1, 'outline_review', 1)")
    # 评分根 s1 + 两个一级目录（对项目的理解 / 技术方案）
    rows = [
        ("s1", 1, "技术评分项应答"),
        ("s1.1", 2, "对项目的理解"),
        ("s1.1.1", 3, "对项目的理解正确、深入、全面"),
        ("s1.1.1.1", 4, "项目背景现状目标"),
        ("s1.1.1.2", 4, "建设范围"),
        ("s1.2", 2, "技术方案"),
        ("s1.2.1", 3, "方案总体架构设计"),
    ]
    for index, (block_id, level, title) in enumerate(rows):
        await conn.execute(
            "INSERT INTO blocks (project_id, block_id, title, level, content, order_idx, run_thread_id, status, evidence_required) "
            "VALUES (1, ?, ?, ?, '', ?, 't1', 'draft', ?)",
            (block_id, title, level, index, "需要我方近三年业绩" if block_id == "s1.1.1.1" else ""))
    await conn.commit()
    yield conn
    await conn.close()


async def test_plan_units_are_direct_children_of_scoring_root(db):
    data = await plan_units(db, 1, "t1")
    assert data["root"]["block_id"] == "s1"
    assert [u["block_id"] for u in data["units"]] == ["s1.1", "s1.2"]
    assert data["units"][0]["leaves"] == 2      # 子目录收到底层两个叶子
    assert data["total_leaves"] == 3


async def test_build_plan_covers_leaves_with_tasks_and_gaps(db):
    plan = await build_plan(db, 1, "t1", "s1.1")
    assert plan["root_title"] == "对项目的理解"
    assert [c["block_id"] for c in plan["chapters"]] == ["s1.1.1.1", "s1.1.1.2"]
    first = plan["chapters"][0]
    # 父章标题含「对项目的理解」→ 子节类型定为项目理解（不是按子节标题乱猜）
    assert first["layout"] == "understanding"
    kinds = {task["kind"] for task in first["tasks"]}
    assert "text" in kinds and "table" in kinds       # 有正文有表
    assert all(task["purpose"] for task in first["tasks"])
    # 有证据要求但没关联素材 → 记缺口
    assert plan["gaps"] and "业绩" in plan["gaps"][0]


async def test_confirm_plan_presets_chief_plan_and_creates_jobs(db, monkeypatch):
    """确认后：计划状态置 confirmed + 每个叶子一个 job，且 artifacts.chief 被预置。"""
    async def fake_context(conn, block):
        return [], []
    async def fake_materials(conn, block):
        return []
    async def fake_source(block):
        return {}

    from routes import chapter_studio as studio_routes
    from services import chapter_writing
    monkeypatch.setattr(studio_routes, "chapter_context", fake_context)
    monkeypatch.setattr(studio_routes, "material_rows", fake_materials)
    monkeypatch.setattr(studio_routes, "source_for", fake_source)
    monkeypatch.setattr(chapter_writing, "queue_job", lambda job_id: None)

    from services.config_store import config_store
    monkeypatch.setattr(config_store, "get_configs_and_next_index",
                        lambda: ([type("C", (), {"model": "m"})()], 0))

    plan = await build_plan(db, 1, "t1", "s1.1")
    result = await confirm_plan(db, 1, plan["id"])
    assert result["chapters"] == 2 and len(result["jobs"]) == 2
    status = (await (await db.execute(
        "SELECT status FROM writing_plans WHERE id=?", (plan["id"],))).fetchone())["status"]
    assert status == "confirmed"
    row = await (await db.execute(
        "SELECT artifacts FROM chapter_writing_jobs WHERE id=?", (result["jobs"][0],))).fetchone()
    plan_json = json.loads(row["artifacts"])["chief"]["plan"]
    assert plan_json["tasks"] and plan_json["tasks"][0]["kind"] in ("text", "table", "diagram")
    # 重复确认要拒绝
    with pytest.raises(ValueError):
        await confirm_plan(db, 1, plan["id"])
