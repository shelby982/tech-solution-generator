"""
backend/routes/projects.py — Vault CRUD + lock

端点：
  GET  /api/projects          — 项目列表（可选 ?status= 过滤）
  POST /api/projects          — 新建项目
  GET  /api/projects/{id}     — 项目详情
  PATCH /api/projects/{id}    — 更新项目信息
  POST /api/projects/{id}/lock — 锁定大纲，生成 base_snapshot
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import get_db
from domain.review import WorkflowRunRepository
from services.block_store import (
    create_project,
    get_project,
    list_projects,
    update_project,
    create_snapshot,
    list_blocks,
    current_run_thread_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["projects"])


# ── GET /api/projects ─────────────────────────────────────

@router.get("/projects")
async def get_projects(status: str | None = None):
    async with get_db() as db:
        projects = await list_projects(db, status=status)
    return JSONResponse(content=projects)


# ── POST /api/projects ────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    deadline: str | None = None
    target_pages: int | None = Field(default=None, ge=1, le=5000)


@router.post("/projects")
async def create_project_route(body: ProjectCreateRequest):
    async with get_db() as db:
        project = await create_project(
            db, name=body.name, deadline=body.deadline, target_pages=body.target_pages,
        )
    return JSONResponse(content=project)


# ── GET /api/projects/{id} ────────────────────────────────

@router.get("/projects/{project_id}")
async def get_project_route(project_id: int):
    async with get_db() as db:
        project = await get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return JSONResponse(content=project)


# ── PATCH /api/projects/{id} ──────────────────────────────

class ProjectPatchRequest(BaseModel):
    name: str | None = None
    deadline: str | None = None
    summary: str | None = None
    target_pages: int | None = Field(default=None, ge=1, le=5000)


@router.patch("/projects/{project_id}")
async def patch_project(project_id: int, body: ProjectPatchRequest):
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        updated = await update_project(db, project_id, **updates)
    return JSONResponse(content=updated)


# ── POST /api/projects/{id}/lock ──────────────────────────

@router.post("/projects/{project_id}/lock")
async def lock_project(project_id: int):
    """锁定大纲：序列化当前 blocks 为 snapshot，更新 project 状态为 authoring"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        tid = await current_run_thread_id(db, project_id)
        blocks = await list_blocks(db, project_id, tid, fallback_all=True)
        snapshot_json = json.dumps(blocks, ensure_ascii=False)
        snapshot = await create_snapshot(
            db,
            project_id=project_id,
            trigger="lock",
            snapshot_json=snapshot_json,
        )
        updated = await update_project(
            db,
            project_id,
            status="authoring",
            base_snapshot_id=snapshot["id"],
        )

    logger.info(f"项目 {project_id} 已锁定大纲，snapshot_id={snapshot['id']}")
    return JSONResponse(content=updated)


# ── GET /api/projects/{id}/workflow-runs ──────────────────

@router.get("/projects/{project_id}/workflow-runs")
async def list_project_workflow_runs(project_id: int, only_active: bool = False):
    """返回该项目的 workflow_runs（按 created_at DESC）。

    供前端项目列表点击时按最新 thread 的 stage 路由到对应页面。
    """
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        repo = WorkflowRunRepository(db)
        runs = await repo.list_by_project(project_id, only_active=only_active)
    return JSONResponse(content=runs)
