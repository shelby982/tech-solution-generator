"""节写法（= 该节的提示词）：计划面板里可编辑，批量编写时按此执行。"""
from __future__ import annotations

from db import get_db


async def load_practices(project_id: int, block_id: str) -> dict[str, str]:
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT section, practice FROM section_practices WHERE project_id=? AND block_id=?",
            (project_id, block_id or ""))).fetchall()
    return {row[0]: row[1] for row in rows if row[1]}


async def save_practice(project_id: int, block_id: str, section: str, practice: str) -> dict:
    async with get_db() as db:
        if str(practice or "").strip():
            await db.execute(
                "INSERT INTO section_practices (project_id, block_id, section, practice, updated_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(project_id, block_id, section) DO UPDATE SET "
                "practice=excluded.practice, updated_at=CURRENT_TIMESTAMP",
                (project_id, block_id or "", section, practice))
        else:
            await db.execute("DELETE FROM section_practices WHERE project_id=? AND block_id=? AND section=?",
                             (project_id, block_id or "", section))
        await db.commit()
    return {"section": section, "practice": practice}


async def practice_of(project_id: int, block_id: str, section: str) -> str:
    practices = await load_practices(project_id, block_id)
    return practices.get(section, "")
