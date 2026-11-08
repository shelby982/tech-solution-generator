"""目录自愈：已确认目录但章节缺大纲时，用确定性 fallback 补齐，不阻断编写。"""
from __future__ import annotations

import json


async def heal_block_outline(db, block: dict) -> list[dict]:
    from services.section_outline import fallback_outline
    from services.writing_plans import plan_of_block
    project_id = int(block.get("project_id") or 0)
    thread_id = str(block.get("run_thread_id") or "")
    block_id = str(block.get("block_id") or "")
    found = await plan_of_block(db, project_id, thread_id, block_id)
    # 计划不存在/章节没进计划时，先按一级目录补建计划，再做大纲修复。
    if not found:
        try:
            from services.writing_plans import build_plan
            await build_plan(db, project_id, thread_id, block_id.split(".")[0])
        except Exception:
            pass
        found = await plan_of_block(db, project_id, thread_id, block_id)
    plan_id, plan = found if found else ("", {})
    chapter = next((item for item in plan.get("chapters") or []
                    if str(item.get("block_id")) == block_id), None)
    if not chapter and plan_id:
        try:
            from services.writing_plans import build_plan
            await build_plan(db, project_id, thread_id, block_id.split(".")[0])
            found = await plan_of_block(db, project_id, thread_id, block_id)
            plan_id, plan = found if found else ("", {})
            chapter = next((item for item in plan.get("chapters") or []
                            if str(item.get("block_id")) == block_id), None)
        except Exception:
            chapter = None
    if not chapter or not plan_id:
        return []
    if chapter.get("outline"):
        return list(chapter["outline"])
    sections = fallback_outline(chapter)
    chapter["outline"] = sections
    chapter["outline_self_healed"] = True
    await db.execute("UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, "
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (json.dumps(plan, ensure_ascii=False), plan_id))
    await db.commit()
    return sections


async def heal_thread_outlines(db, project_id: int, thread_id: str) -> dict:
    """确认目录时兜底：给所有已存在于计划、但 outline 为空的章节补 fallback 大纲。"""
    from services.section_outline import fallback_outline
    rows = await (await db.execute(
        "SELECT id, plan FROM writing_plans WHERE project_id=? AND thread_id=?",
        (project_id, thread_id))).fetchall()
    healed = 0
    for row in rows:
        try:
            plan = json.loads(row["plan"] or "{}")
        except ValueError:
            continue
        changed = False
        for chapter in plan.get("chapters") or []:
            if chapter.get("outline"):
                continue
            chapter["outline"] = fallback_outline(chapter)
            chapter["outline_self_healed"] = True
            changed = True
            healed += 1
        if changed:
            await db.execute("UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, "
                             "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (json.dumps(plan, ensure_ascii=False), row["id"]))
    if healed:
        await db.commit()
    return {"healed": healed}
