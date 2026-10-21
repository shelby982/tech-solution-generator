"""章节级 SSE 作业接口（v3 §4.3，M2-a）。"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db import get_db
from services import chapter_sse

router = APIRouter(tags=["chapter-sse"])


class WriteBody(BaseModel):
    thread_id: str = Field(min_length=8)
    block_id: str = Field(min_length=1)          # 章节编码（s1.2.1.1）
    settings_override: dict[str, str] = Field(default_factory=dict)


async def _resolve_block(project_id: int, block_code: str) -> dict:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM blocks WHERE project_id=? AND block_id=?", (project_id, block_code))).fetchone()
    if not row:
        raise HTTPException(404, f"章节不存在：{block_code}")
    return dict(row)


@router.post('/projects/{project_id}/studio/chapter-writes')
async def create_chapter_write(project_id: int, body: WriteBody):
    block = await _resolve_block(project_id, body.block_id)
    try:
        job_id = await chapter_sse.create_job(project_id, body.thread_id, int(block["id"]), body.settings_override)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    chapter_sse.start_runner(job_id)
    return {"job_id": job_id}


@router.get('/projects/{project_id}/studio/chapter-writes/{job_id}')
async def get_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    return {
        "job_id": job_id,
        "status": row["status"],
        "stage": row["stage"],
        "saved": bool(row["saved"]),
        "error": row["error"],
        "content": row["content"],
        "artifacts": json.loads(row["artifacts"] or "{}"),
        "snapshot": json.loads(row["snapshot"] or "{}"),
    }


@router.post('/projects/{project_id}/studio/chapter-writes/{job_id}/cancel')
async def cancel_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    await chapter_sse._set_job(job_id, status="cancelled", stage="已取消")
    await chapter_sse.emit(job_id, "job", {"job_id": job_id, "status": "cancelled", "stage": "已取消", "progress": 0})
    return {"ok": True}


@router.post('/projects/{project_id}/studio/chapter-writes/{job_id}/retry')
async def retry_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    ok = await chapter_sse.retry_job(job_id)
    return {"ok": ok, "job_id": job_id}


@router.get('/projects/{project_id}/studio/chapter-writes/{job_id}/stream')
async def stream_chapter_write(project_id: int, job_id: str, thread_id: str = ''):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    queue = chapter_sse.subscribe(job_id)

    async def events():
        from utils.sse import format_sse_event
        try:
            # 先回放当前状态（SSE 无回放，状态以 job 详情为准）
            yield format_sse_event('job', {"job_id": job_id, "status": row["status"],
                                           "stage": row["stage"], "progress": 0})
            # 已结束的作业：SSE 无回放，直接补一条终态事件，避免前端等到"中断"
            if row["status"] in ("done", "failed", "cancelled"):
                if row["status"] == "done":
                    yield format_sse_event('done', {"content": row["content"], "saved": bool(row["saved"]),
                                                   "reason": "内容与现有正文一致，未产生变更" if not row["saved"] else "已写入正文",
                                                   "models": {}, "checks": []})
                elif row["status"] == "failed":
                    yield format_sse_event('error', {"scope": "job", "message": row["error"] or "作业失败",
                                                    "recoverable": True})
                return
            last = time.monotonic()
            while True:
                if row["status"] in ("done", "failed", "cancelled") and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield format_sse_event('heartbeat', {"ts": time.time()})
                    async with get_db() as db:
                        fresh = await (await db.execute(
                            "SELECT status FROM chapter_writing_jobs WHERE id=?", (job_id,))).fetchone()
                    if fresh and fresh[0] in ("done", "failed", "cancelled"):
                        break
                    continue
                yield format_sse_event(item["event"], item["data"])
                if item["event"] in ("done", "error") and item["data"].get("scope") == "job":
                    break
        finally:
            chapter_sse.unsubscribe(job_id, queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
