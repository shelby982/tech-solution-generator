"""按「标包 × 应答类型」解析目录草案。

用户 PM 反馈：应答策划里点「商务」/「技术」应该切到该类型的应答目录，
但页面一直显示「生成目录草案」——因为 (标包,类型) → 草案线程 这一层解析不存在。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.response_drafts import list_response_drafts


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "drafts.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name, status) "
        "VALUES (1, '1', '2', '某标包', 'confirmed')")
    pkg_id = (await (await conn.execute("SELECT id FROM packages WHERE project_id=1")).fetchone())["id"]
    # 解析运行（无 response_plan）
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id) "
        "VALUES ('parse', 1, 'points_review', '1', '2', ?)", (pkg_id,))
    # 技术草案
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id) "
        "VALUES ('draft-tech', 1, 'outline_review', '1', '2', ?)", (pkg_id,))
    await conn.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value) VALUES (1, 'draft-tech', 'response_plan', ?)",
        ('{"type": "tech", "source_thread_id": "parse", "confirmed": false}',))
    # 商务草案（已确认）
    await conn.execute(
        "INSERT INTO workflow_runs (thread_id, project_id, stage, target, package_no, package_id) "
        "VALUES ('draft-biz', 1, 'outline_review', '1', '2', ?)", (pkg_id,))
    await conn.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value) VALUES (1, 'draft-biz', 'response_plan', ?)",
        ('{"type": "commerce", "source_thread_id": "parse", "confirmed": true}',))
    # 两个草案各自有目录节点
    for tid, n in (("draft-tech", 3), ("draft-biz", 2), ("parse", 5)):
        for i in range(n):
            await conn.execute(
                "INSERT INTO blocks (project_id, block_id, title, content, order_idx, run_thread_id, status) "
                "VALUES (1, ?, ?, 'x', ?, ?, 'done')", (f"{tid}-{i}", f"节点{i}", i, tid))
    await conn.commit()
    yield conn
    await conn.close()


async def test_lists_both_drafts_and_source_run(db):
    result = await list_response_drafts(db, 1, 1)
    assert result["source_thread_id"] == "parse"
    assert result["package_no"] == "2" and result["target"] == "1"
    by_type = {d["type"]: d for d in result["drafts"]}
    assert set(by_type) == {"commerce", "tech"}
    assert by_type["tech"]["thread_id"] == "draft-tech"
    assert by_type["tech"]["confirmed"] is False
    assert by_type["tech"]["outline_count"] == 3      # 只数该 run 自己的 blocks
    assert by_type["commerce"]["thread_id"] == "draft-biz"
    assert by_type["commerce"]["confirmed"] is True


async def test_package_without_draft_returns_empty_list(db):
    """只有解析运行时，草案为空 —— 前端据此决定"点类型要先生成"。"""
    await db.execute("DELETE FROM split_answers")
    await db.commit()
    result = await list_response_drafts(db, 1, 1)
    assert result["drafts"] == []
    assert result["source_thread_id"] == "parse"


async def test_corrupt_plan_value_is_ignored(db):
    await db.execute(
        "UPDATE split_answers SET value='不是JSON' WHERE thread_id='draft-tech'")
    await db.commit()
    result = await list_response_drafts(db, 1, 1)
    types = [d["type"] for d in result["drafts"]]
    assert types == ["commerce"], "坏数据不参与，但不能影响另一份草案"
