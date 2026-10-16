"""稳定标包的数据边界校验。"""
from fastapi import HTTPException


async def require_package(db, project_id: int, package_id: int, *, confirmed: bool = True) -> dict:
    row = await (await db.execute(
        "SELECT * FROM packages WHERE id = ? AND project_id = ?",
        (package_id, project_id),
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="标包不存在或不属于该项目")
    item = dict(row)
    if confirmed and item.get("status") != "confirmed":
        raise HTTPException(status_code=409, detail="请先确认标包")
    return item


async def package_run_thread(db, project_id: int, package_id: int) -> str | None:
    await require_package(db, project_id, package_id)
    row = await (await db.execute(
        """SELECT thread_id FROM workflow_runs
           WHERE project_id = ? AND package_id = ?
           ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (project_id, package_id),
    )).fetchone()
    return row["thread_id"] if row else None
