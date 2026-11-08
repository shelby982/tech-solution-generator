"""目录已确认但缺大纲：确认时自愈 + 单章数据修复。"""
import json

from db import get_db
from services.block_store import upsert_outline_block
from services.outline_self_heal import heal_block_outline, heal_thread_outlines


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('大纲自愈')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("heal-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="建设内容", order_idx=0,
                                   run_thread_id="heal-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
        plan = {"chapters": [{"block_id": "s1", "title": "建设内容",
                              "requirements": [{"id": "r1", "text": "说明建设内容", "type": "text"}],
                              "outline": []}]}
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) "
                         "VALUES(?,?,?,?,?,?)",
                         ("heal-plan", pid, "heal-thread", "s1", "confirmed", json.dumps(plan)))
        await db.commit()
    return pid, block


async def test_heal_block_outline():
    pid, block = await _seed()
    async with get_db() as db:
        sections = await heal_block_outline(db, block)
    assert len(sections) >= 1
    assert sections[0]["title"]
    async with get_db() as db:
        plan = json.loads((await (await db.execute("SELECT plan FROM writing_plans WHERE id='heal-plan'")).fetchone())["plan"])
    assert plan["chapters"][0]["outline"]
    assert plan["chapters"][0]["outline_self_healed"] is True


async def test_heal_thread_outlines_idempotent():
    pid, block = await _seed()
    async with get_db() as db:
        first = await heal_thread_outlines(db, pid, "heal-thread")
        second = await heal_thread_outlines(db, pid, "heal-thread")
    assert first["healed"] == 1
    assert second["healed"] == 0


async def test_heal_block_builds_missing_plan():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('无计划自愈')")).lastrowid
        await db.execute("INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)", ("no-plan-thread", pid))
        await db.commit()
        await upsert_outline_block(db, pid, "s1", level=1, title="项目背景认识", order_idx=0,
                                   run_thread_id="no-plan-thread")
        await db.commit()
        block = dict(await (await db.execute("SELECT * FROM blocks WHERE project_id=?", (pid,))).fetchone())
    async with get_db() as db:
        sections = await heal_block_outline(db, block)
    assert sections
    async with get_db() as db:
        rows = await (await db.execute("SELECT plan FROM writing_plans WHERE project_id=?", (pid,))).fetchall()
    assert rows and any("outline" in (r["plan"] or "") for r in rows)
