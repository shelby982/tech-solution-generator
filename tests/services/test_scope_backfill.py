"""标的回填：把评分表解析出的「标的」补进包记录。

实测 project 29：packages.target_code / project_packages.target / workflow_runs.target 都为空，
而评分表解析出 target='标的1'；前端按 target+package_no 匹配失败 →
页面显示「该标包没有解析到技术评分点」（其实评分点都在）。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.scope_backfill import backfill_package_targets, target_code_of


def test_target_code_of():
    assert target_code_of("标的1") == "1"
    assert target_code_of("标包 2") == "2"
    assert target_code_of("1") == "1"
    assert target_code_of("") == ""


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "scope.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name) VALUES (1, '', '2', '某标包')")
    await conn.execute(
        "INSERT INTO project_packages (project_id, target, package_no, package_name) VALUES (1, '', '2', '某标包')")
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no) VALUES ('t1', 1, 'points_review', '', '2')")
    await conn.commit()
    yield conn
    await conn.close()


async def test_backfills_only_missing_and_is_idempotent(db):
    report = {"package_matrix": [{"package_no": "2", "target": "标的1"}]}
    assert await backfill_package_targets(db, 1, report) >= 1

    pkg = await (await db.execute("SELECT target_code FROM packages WHERE project_id=1")).fetchone()
    pp = await (await db.execute("SELECT target FROM project_packages WHERE project_id=1")).fetchone()
    run = await (await db.execute("SELECT target FROM workflow_runs WHERE thread_id='t1'")).fetchone()
    assert pkg["target_code"] == "1"          # 包实体用编号
    assert pp["target"] == "标的1"            # 投影保留原文标签
    assert run["target"] == "1"

    assert await backfill_package_targets(db, 1, report) == 0, "幂等：已填过就不再写"


async def test_ambiguous_target_not_written(db):
    """同一包号对应多个标的时不猜（宁可不填，也不写错）。"""
    report = {"package_matrix": [
        {"package_no": "2", "target": "标的1"},
        {"package_no": "2", "target": "标的2"},
    ]}
    assert await backfill_package_targets(db, 1, report) == 0
    pkg = await (await db.execute("SELECT target_code FROM packages WHERE project_id=1")).fetchone()
    assert pkg["target_code"] == ""
