"""
backend/routes/revisions.py — Block 版本历史查看与回溯

端点：
  GET  /api/blocks/{block_id}/revisions              — 版本历史列表
  GET  /api/projects/{project_id}/revisions          — 项目下全部 block 的修订
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from db import get_db
from services.block_store import (
    get_block,
    list_revisions,
    list_project_revisions as _list_project_revisions,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["revisions"])


@router.get("/blocks/{block_id}/revisions")
async def list_block_revisions(block_id: int):
    """返回 block 版本历史，按 revision_no 升序"""
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        revisions = await list_revisions(db, block_id)
    revisions_sorted = sorted(revisions, key=lambda r: r["revision_no"])
    return JSONResponse(content=revisions_sorted)


@router.get("/projects/{project_id}/revisions")
async def list_project_revisions(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    """
    返回项目下全部 block 的全部修订，按时间倒序。

    每行附带 block_title / order_idx / prev_content / char_delta，方便前端
    前端「章节版本 / 与上次评审对比」（review.html）直接渲染（按时间分组、显示前后对比）。
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        run_thread_id = None
        if package_id is not None:
            from services.package_scope import package_run_thread
            run_thread_id = await package_run_thread(db, project_id, package_id)
            if run_thread_id is None:
                return JSONResponse(content={"revisions": []})
        rows = await _list_project_revisions(db, project_id, run_thread_id)

    return JSONResponse(content={"revisions": rows})
