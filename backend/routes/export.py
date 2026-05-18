"""
backend/routes/export.py — diff 计算、diff apply、Word 导出

端点：
  GET  /api/projects/{id}/diff         — base_snapshot vs current 差异
  POST /api/projects/{id}/diff/apply   — 接受/拒绝 diff，生成新 snapshot
  GET  /api/projects/{id}/export       — 导出 Word 文档
"""

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from db import get_db
from services.block_store import (
    get_project,
    list_blocks,
    get_snapshot,
    create_snapshot,
    add_revision,
    update_block_status,
)
from services.docx_generator import sections_to_docx

logger = logging.getLogger(__name__)
router = APIRouter(tags=["export"])


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    ascii_fb = filename.encode("ascii", errors="replace").decode("ascii")
    return f'attachment; filename="{ascii_fb}"; filename*=UTF-8\'\'{encoded}'


def _compute_diff(base_blocks: list[dict], current_blocks: list[dict]) -> dict:
    """计算 base_snapshot 与 current_blocks 的三路差异"""
    base_map    = {b["block_id"]: b for b in base_blocks}
    current_map = {b["block_id"]: b for b in current_blocks}

    domain_map: dict[str, list] = {}
    for b in current_blocks:
        domain_map.setdefault(b.get("domain", ""), []).append(b)

    added, removed, changed = [], [], []

    for bid, blk in current_map.items():
        if bid not in base_map:
            added.append({"block_id": bid, "title": blk.get("title"), "content": blk.get("content")})

    for bid, blk in base_map.items():
        if bid not in current_map:
            removed.append({"block_id": bid, "title": blk.get("title")})

    for bid, base_blk in base_map.items():
        if bid not in current_map:
            continue
        cur_blk = current_map[bid]
        if base_blk.get("content") != cur_blk.get("content"):
            affected = [
                {"block_id": b["block_id"], "title": b.get("title")}
                for b in domain_map.get(cur_blk.get("domain", ""), [])
                if b["block_id"] != bid
            ]
            changed.append({
                "block_id":        bid,
                "old_content":     base_blk.get("content"),
                "new_content":     cur_blk.get("content"),
                "affected_blocks": affected,
            })

    return {"added": added, "removed": removed, "changed": changed}


@router.get("/projects/{project_id}/diff")
async def get_diff(project_id: int):
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        current_blocks = await list_blocks(db, project_id)

        if not project.get("base_snapshot_id"):
            added = [{"block_id": b["block_id"], "title": b.get("title"), "content": b.get("content")}
                     for b in current_blocks]
            return JSONResponse(content={"added": added, "removed": [], "changed": []})

        snapshot = await get_snapshot(db, project["base_snapshot_id"])
        if snapshot is None:
            raise HTTPException(status_code=404, detail="base_snapshot 不存在")

    base_blocks: list[dict] = json.loads(snapshot["snapshot"])
    result = _compute_diff(base_blocks, current_blocks)
    return JSONResponse(content=result)


class DiffApplyRequest(BaseModel):
    accept: list[str] = []
    reject: list[str] = []


@router.post("/projects/{project_id}/diff/apply")
async def apply_diff(project_id: int, body: DiffApplyRequest):
    """接受/拒绝 diff：reject 的 block 写回 base 内容；清除 needs_review；生成新 snapshot"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        current_blocks = await list_blocks(db, project_id)
        current_map = {b["block_id"]: b for b in current_blocks}

        base_map: dict[str, dict] = {}
        if project.get("base_snapshot_id"):
            snapshot = await get_snapshot(db, project["base_snapshot_id"])
            if snapshot:
                base_map = {b["block_id"]: b for b in json.loads(snapshot["snapshot"])}

        for bid in body.reject:
            if bid in base_map and bid in current_map:
                base_content = base_map[bid].get("content", "")
                db_block_id = current_map[bid]["id"]
                await add_revision(
                    db,
                    block_id_int=db_block_id,
                    content=base_content,
                    source="restore",
                    summary=f"diff reject — 恢复 base 版本",
                )

        for blk in current_blocks:
            if blk.get("status") == "needs_review":
                await update_block_status(db, blk["id"], "done")

        new_snap = await create_snapshot(
            db,
            project_id=project_id,
            trigger="apply_diff",
            snapshot_json=json.dumps(current_blocks, ensure_ascii=False),
        )

    logger.info(f"diff apply 完成：project={project_id}, snapshot_id={new_snap['id']}")
    return JSONResponse(content={
        "snapshot_id": new_snap["id"],
        "accepted":    body.accept,
        "rejected":    body.reject,
    })


@router.get("/projects/{project_id}/export")
async def export_docx(project_id: int):
    """导出 Word 文档，过滤 status=empty 的 blocks"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        all_blocks = await list_blocks(db, project_id)

    sections_data = [
        {
            "id":      b["block_id"],
            "title":   b.get("title", ""),
            "level":   b.get("level", 1),
            "content": b.get("content", ""),
            "done":    True,
        }
        for b in all_blocks
        if b.get("status") != "empty"
    ]

    if not sections_data:
        raise HTTPException(status_code=404, detail="无可导出内容，所有章节均为空")

    try:
        docx_bytes = sections_to_docx(sections_data, doc_title=project.get("name", "技术方案"))
    except Exception as exc:
        logger.exception(f"DOCX 生成失败：{exc}")
        raise HTTPException(status_code=500, detail=f"Word 文档生成失败：{exc}")

    filename = f"{project.get('name', '技术方案')}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition(filename)},
    )
