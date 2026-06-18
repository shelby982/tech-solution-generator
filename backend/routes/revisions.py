"""
backend/routes/revisions.py — Block 版本历史查看与回溯

端点：
  GET  /api/blocks/{block_id}/revisions              — 版本历史列表
  GET  /api/projects/{project_id}/revisions          — 项目下全部 block 的修订
  POST /api/blocks/{block_id}/revisions/{rn}/restore — 回溯到指定版本
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from db import get_db
from services.block_store import (
    get_block,
    list_revisions,
    add_revision,
    update_block_content,
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
async def list_project_revisions(project_id: int):
    """
    返回项目下全部 block 的全部修订，按时间倒序。

    每行附带 block_title / order_idx / prev_content / char_delta，方便前端
    diff-review.html 直接渲染（按时间分组、显示前后对比）。
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT br.id            AS id,
                   br.block_id      AS block_id,
                   br.revision_no   AS revision_no,
                   br.content       AS content,
                   br.summary       AS summary,
                   br.source        AS source,
                   br.created_at    AS created_at,
                   b.title          AS block_title,
                   b.order_idx      AS order_idx
              FROM block_revisions br
              JOIN blocks b ON b.id = br.block_id
             WHERE b.project_id = ?
             ORDER BY br.created_at DESC, br.id DESC
            """,
            (project_id,),
        )
        rows = await cursor.fetchall()

        # 按 block 分组找上一版 content，计算 char_delta + prev_content
        prev_by_block: dict[int, dict[int, str]] = {}
        for r in rows:
            prev_by_block.setdefault(r["block_id"], {})[r["revision_no"]] = r["content"] or ""

    items: list[dict] = []
    for r in rows:
        bid = r["block_id"]
        rn = r["revision_no"]
        prev_content = prev_by_block.get(bid, {}).get(rn - 1, "")
        curr_content = r["content"] or ""
        items.append({
            "id":           r["id"],
            "block_id":     bid,
            "block_title":  r["block_title"] or "",
            "order_idx":    r["order_idx"],
            "revision_no":  rn,
            "content":      curr_content,
            "prev_content": prev_content,
            "char_delta":   len(curr_content) - len(prev_content),
            "summary":      r["summary"] or "",
            "source":       r["source"] or "",
            "created_at":   r["created_at"],
        })

    return JSONResponse(content={"revisions": items})


@router.post("/blocks/{block_id}/revisions/{rn}/restore")
async def restore_revision(block_id: int, rn: int):
    """
    回溯到指定版本号 rn：
    1. 找到 revision_no == rn 的记录
    2. 将旧 content 写回 blocks（status=done）
    3. 插入新 revision（source=restore，summary="回溯至 rX"）
    """
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")

        revisions = await list_revisions(db, block_id)
        target = next((r for r in revisions if r["revision_no"] == rn), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"revision_no={rn} 不存在")

        old_content = target["content"]
        await update_block_content(db, block_id, old_content, status="done")
        new_revision = await add_revision(
            db,
            block_id_int=block_id,
            content=old_content,
            source="restore",
            summary=f"回溯至 r{rn}",
        )

    logger.info(f"Block {block_id} 回溯至 r{rn}，新 revision_no={new_revision['revision_no']}")
    return JSONResponse(content={
        "block_id":        block_id,
        "restored_from":   rn,
        "new_revision_no": new_revision["revision_no"],
        "content":         old_content,
    })
