"""routes/review.py — 评审快照端点（spec §7）。

GET /api/review/{thread_id}
  直接读 ReviewRepository 拿 findings 列表，组装为 JSON 响应。
  不做 GlobalReport 聚合（由 orchestrator 评审节点产出，前端从 SSE 拿）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


@router.get("/{thread_id}")
async def get_review(thread_id: str):
    """返回某次工作流的全部评审 findings 快照。"""
    from db import get_db
    from domain.review import ReviewRepository

    async with get_db() as conn:
        repo = ReviewRepository(conn)
        findings = await repo.list_findings_by_thread(thread_id)
    return JSONResponse({
        "thread_id": thread_id,
        "findings": [f.to_dict() for f in findings],
    })


__all__ = ["router"]
