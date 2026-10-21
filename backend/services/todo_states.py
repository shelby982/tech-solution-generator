"""待办状态（v3 §5.5）：忽略 / 稍后 / 已消除 —— 服务端化，跨设备生效。"""
from __future__ import annotations

import datetime as _dt

from db import get_db

VALID = ("open", "ignored", "snoozed", "resolved")


async def load_states(project_id: int, block_id: str = "") -> dict[str, dict]:
    """返回 {task_id: {status, reason, until_at}}。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT task_id, status, reason, until_at FROM todo_states WHERE project_id=? AND block_id=?",
            (project_id, block_id or ""))).fetchall()
    return {row[0]: {"status": row[1], "reason": row[2], "until_at": row[3]} for row in rows}


async def set_state(project_id: int, block_id: str, task_id: str, status: str,
                    reason: str = "", until_days: int = 0) -> dict:
    if status not in VALID:
        raise ValueError(f"非法状态：{status}")
    until_at = None
    if status == "snoozed" and until_days:
        until_at = (_dt.datetime.now() + _dt.timedelta(days=until_days)).isoformat(timespec="seconds")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO todo_states (project_id, block_id, task_id, status, reason, until_at, updated_at) "
            "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(project_id, block_id, task_id) DO UPDATE SET "
            "status=excluded.status, reason=excluded.reason, until_at=excluded.until_at, "
            "updated_at=CURRENT_TIMESTAMP",
            (project_id, block_id or "", task_id, status, reason, until_at))
        await db.commit()
    return {"task_id": task_id, "status": status, "until_at": until_at}


def merge_status(tasks: dict, states: dict[str, dict]) -> dict:
    """把服务端状态合并进任务卡（ignored/snoozed 的从待办里移出）。"""
    active: dict[str, list] = {"need_user": [], "system": [], "suggestion": []}
    dropped: list[str] = []
    for zone, items in (tasks or {}).items():
        if zone not in active:
            continue
        for task in items or []:
            state = states.get(task.get("id") or "")
            if state and state["status"] in ("ignored", "snoozed", "resolved"):
                dropped.append(task.get("id") or "")
                continue
            active[zone].append(task)
    summary = dict((tasks or {}).get("summary") or {})
    summary["need_user"] = len(active["need_user"])
    summary["system"] = len(active["system"])
    summary["suggestion"] = len(active["suggestion"])
    summary["ignored"] = len(dropped)
    return {**active, "summary": summary, "ignored_ids": dropped}
