"""项目进度与待办聚合（状态行「进度中心」）。

用户反馈：状态行有「处理待办 / 版本记录 / 运行详情」三个并列入口，编写进度又散在
编写页顶部、右栏、阶段头三处 —— 要一个"到哪了 / 还差什么 / 要我做什么"的答案。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.project_progress import project_progress, project_todos


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "progress.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name, status) "
        "VALUES (1, '1', '2', '某标包', 'confirmed')")
    pkg = (await (await conn.execute("SELECT id FROM packages WHERE project_id=1")).fetchone())["id"]
    # 解析运行（无 response_plan，按设计停在中断态）
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id, interrupted_at) "
        "VALUES ('parse', 1, 'points_review', '1', '2', ?, '2026-10-01')", (pkg,))
    # 技术草案：目录已确认 + 篇幅已确认
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id) "
        "VALUES ('tech', 1, 'outline_review', '1', '2', ?)", (pkg,))
    await conn.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value) VALUES (1, 'tech', 'response_plan', ?)",
        ('{"type": "tech", "source_thread_id": "parse", "confirmed": true}',))
    await conn.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value) VALUES (1, 'tech', 'page_budget', ?)",
        ('{"total": 100, "confirmed": true}',))
    # 商务草案：目录未确认
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id) "
        "VALUES ('biz', 1, 'outline_review', '1', '2', ?)", (pkg,))
    await conn.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value) VALUES (1, 'biz', 'response_plan', ?)",
        ('{"type": "commerce", "source_thread_id": "parse", "confirmed": false}',))
    # 目录：技术 3 节（父 + 2 子）、商务 2 节
    rows = [("tech", "s1", 1, "评分项", "", "done"),
            ("tech", "s1.1", 2, "子一", "已写" * 10, "done"),
            ("tech", "s1.2", 2, "子二", "已写" * 10, "done"),
            ("biz", "b1", 1, "商务一", "", "draft"),
            ("biz", "b2", 2, "商务二", "", "draft")]
    for i, (tid, block_id, level, title, content, status) in enumerate(rows):
        await conn.execute(
            "INSERT INTO blocks (project_id, block_id, title, level, content, order_idx, run_thread_id, status) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (block_id, title, level, content, i, tid, status))
    await conn.commit()
    yield conn
    await conn.close()


async def test_progress_counts_units_and_reuses_page_budget(db):
    """编写单元口径：父目录无正文不算单元；篇幅取 page_budget 的 total/allocated。"""
    data = await project_progress(db, 1, 1)
    by_type = {t["type"]: t for t in data["types"]}
    assert by_type["tech"]["directory_confirmed"] is True
    # 父节点 s1 没有正文 → 不是编写单元；两个子节才是
    assert by_type["tech"]["units"] == {"total": 2, "written": 2, "complete": 2, "percent": 100}
    assert by_type["tech"]["budget"] == {"total": 100, "allocated": 0, "confirmed": True}
    assert by_type["commerce"]["directory_confirmed"] is False
    # 商务的 b1 有子节点且无正文 → 不是编写单元，所以是 2 + 1 = 3
    assert data["units"]["total"] == 3
    assert data["current"]["id"] == "2"          # 商务目录未确认 → 停在应答策划
    assert data["runs"]["interrupted"] == 0      # 解析记录的中断不算待办噪音


async def test_todos_surface_unconfirmed_directory_only_for_drafts(db):
    data = await project_todos(db, 1, 1)
    kinds = [t["kind"] for t in data["todos"]]
    assert "directory" in kinds
    todo = next(t for t in data["todos"] if t["kind"] == "directory")
    assert todo["severity"] == "high"
    assert "商务" in todo["title"]
    assert "技术" not in todo["title"]           # 技术目录已确认，不该再提示
    assert data["counts"]["high"] >= 1


async def test_todos_include_material_gaps_from_latest_job(db):
    """素材缺口来自主编计划（job.artifacts.chief.plan.gaps），不是我们猜的。"""
    await db.execute(
        "INSERT INTO chapter_writing_jobs (project_id, thread_id, block_id, status, artifacts, snapshot) "
        "VALUES (1, 'tech', 's1.1', 'done', ?, '{}')",
        ('{"chief": {"plan": {"gaps": ["缺我方近三年业绩", "缺总体架构图"]}}}',))
    await db.commit()
    data = await project_todos(db, 1, 1)
    material = [t for t in data["todos"] if t["kind"] == "material"]
    assert material and material[0]["title"] == "素材缺口 2 项"
    assert "业绩" in material[0]["detail"]


async def test_stale_chapter_review_does_not_mark_review_stage_done(db):
    """正文改过但未复评的章，不应让「评审与交付」阶段显示 done。"""
    block = await (await db.execute(
        "SELECT * FROM blocks WHERE project_id=1 AND run_thread_id='tech' AND block_id='s1.1'")).fetchone()
    await db.execute(
        """INSERT INTO chapter_reviews
           (block_id, project_id, tech_score, comp_score, score, satisfied,
            issues, tier, stage, content_version, evidence_version)
           VALUES (?, 1, 80, 80, 80, 'satisfied', '[]', 'final', 'final', 'old', 'old')""",
        (block["id"],))
    await db.commit()

    data = await project_progress(db, 1, 1)
    review_stage = next(s for s in data["stages"] if s["id"] == "4")
    assert review_stage["state"] != "done"
