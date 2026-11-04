"""批量章节评审（后台任务）：把"逐章评审"跑成全项目/全章节，带进度。

为什么需要：单章评审要 30~60 秒，96 章不可能让用户逐个点。
任务模式与导出/生图一致：先建 job（202），后台 asyncio 任务跑，前端轮询进度。
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
import uuid

from db import get_db

logger = logging.getLogger(__name__)

_running: dict[str, asyncio.Task] = {}


async def create_job(db, project_id: int, thread_id: str = "", only_unreviewed: bool = True,
                     limit: int = 0) -> dict:
    """建批量评审任务，返回 job 行。"""
    job_id = uuid.uuid4().hex[:16]
    await db.execute(
        "INSERT INTO review_jobs (id, project_id, thread_id, status, only_unreviewed, limit_n, "
        "total, done, failed, current_block, created_at, updated_at) "
        "VALUES (?,?,?,'queued',?,?,0,0,0,'',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        (job_id, project_id, thread_id or "", 1 if only_unreviewed else 0, int(limit or 0)))
    await db.commit()
    return await get_job(db, job_id)


async def get_job(db, job_id: str) -> dict | None:
    row = await (await db.execute("SELECT * FROM review_jobs WHERE id=?", (job_id,))).fetchone()
    return dict(row) if row else None


async def latest_job(db, project_id: int, thread_id: str = "") -> dict | None:
    row = await (await db.execute(
        "SELECT * FROM review_jobs WHERE project_id=? AND (?='' OR thread_id=?) "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id, thread_id or "", thread_id or ""))).fetchone()
    return dict(row) if row else None


async def _targets(db, project_id: int, thread_id: str, only_unreviewed: bool, limit: int) -> list[dict]:
    rows = await (await db.execute(
        "SELECT id, block_id, title, content, run_thread_id FROM blocks "
        "WHERE project_id=? AND COALESCE(block_id,'')<>'' AND COALESCE(content,'')<>'' "
        "AND (?='' OR run_thread_id=?) ORDER BY id",
        (project_id, thread_id or "", thread_id or ""))).fetchall()
    blocks = [dict(r) for r in rows]
    if only_unreviewed:
        reviewed = {int(r[0]) for r in await (await db.execute(
            "SELECT block_id FROM chapter_reviews")).fetchall()}
        blocks = [b for b in blocks if int(b["id"]) not in reviewed]
    if limit:
        blocks = blocks[:limit]
    return blocks


async def _update(db, job_id: str, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key}=?" for key in fields)
    await db.execute(f"UPDATE review_jobs SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     [*fields.values(), job_id])
    await db.commit()


async def run_job(job_id: str) -> None:
    """后台执行：逐章评审（串行，避免打爆模型并发），实时写进度。"""
    from services.chapter_review import review_block

    async with get_db() as db:
        job = await get_job(db, job_id)
    if not job:
        return
    project_id = int(job["project_id"])
    thread_id = str(job.get("thread_id") or "")
    only_unreviewed = bool(job.get("only_unreviewed"))
    limit = int(job.get("limit_n") or 0)

    async with get_db() as db:
        targets = await _targets(db, project_id, thread_id, only_unreviewed, limit)
        await _update(db, job_id, status="running", total=len(targets))

    done = failed = 0
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, int(os.getenv("REVIEW_BATCH_CONCURRENCY", "2"))))

    async def run_one(block: dict) -> None:
        nonlocal done, failed
        async with semaphore:
            async with get_db() as db:
                await _update(db, job_id, current_block=str(block.get("block_id") or ""))
            try:
                await review_block(int(block["id"]))
                async with lock:
                    done += 1
            except Exception as exc:  # noqa: BLE001
                async with lock:
                    failed += 1
                logger.warning(f"批量评审：{block.get('block_id')} 失败 {exc}")
            async with get_db() as db:
                await _update(db, job_id, done=done, failed=failed)

    await asyncio.gather(*(run_one(block) for block in targets))

    async with get_db() as db:
        status = "done" if failed == 0 else ("partial" if done else "failed")
        await _update(db, job_id, status=status, current_block="")


def start(job_id: str) -> None:
    """启动后台任务（进程内）。"""
    if job_id in _running and not _running[job_id].done():
        return
    task = asyncio.create_task(run_job(job_id))
    _running[job_id] = task


async def recover_stale_jobs() -> None:
    """服务重启后把 running/queued 的任务标为中断（避免一直转圈）。"""
    try:
        async with get_db() as db:
            await db.execute("UPDATE review_jobs SET status='failed', current_block='服务重启，任务中断' "
                             "WHERE status IN ('running','queued')")
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"批量评审恢复失败：{exc}")
