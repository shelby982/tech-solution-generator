"""批量评审路由（无 /review 前缀，挂在 /api 下）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db
from services.review_batch import create_job, estimate, get_job, latest_job, start

router = APIRouter(tags=["review-batch"])


class BatchBody(BaseModel):
    thread_id: str = ""
    only_unreviewed: bool = True
    limit: int = 0


@router.post('/projects/{project_id}/reviews/batch', status_code=202)
async def start_batch_review(project_id: int, body: BatchBody):
    """批量评审（后台任务）：默认只评"还没评审过"的有正文章节。"""
    async with get_db() as db:
        job = await create_job(db, project_id, body.thread_id, body.only_unreviewed, body.limit)
    start(job["id"])
    return {"job_id": job["id"], "total": job.get("total") or 0, "status": job.get("status")}


@router.get('/projects/{project_id}/reviews/batch')
async def batch_latest(project_id: int, thread_id: str = ""):
    async with get_db() as db:
        job = await latest_job(db, project_id, thread_id)
    return {"job": job}


@router.get('/projects/{project_id}/reviews/batch/{job_id}')
async def batch_progress(project_id: int, job_id: str):
    async with get_db() as db:
        job = await get_job(db, job_id)
    if not job or int(job["project_id"]) != project_id:
        raise HTTPException(404, "任务不存在")
    return job


@router.get('/projects/{project_id}/reviews/estimate')
async def batch_estimate(project_id: int, thread_id: str = "", only_unreviewed: bool = True,
                         limit: int = 0):
    """全量评审前的成本预估（章节数 / 调用次数 / token / 耗时）。"""
    async with get_db() as db:
        return await estimate(db, project_id, thread_id, only_unreviewed, limit)
