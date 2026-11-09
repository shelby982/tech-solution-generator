"""
backend/routes/export.py — diff 计算、Word 导出

端点：
  GET  /api/projects/{id}/diff    — base_snapshot vs current 差异
  GET  /api/projects/{id}/export  — 导出 Word 文档
"""

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from db import get_db
from services.block_store import (
    get_project,
    list_blocks,
    current_run_thread_id,
    get_snapshot,
)
from infra.docx import sections_to_docx

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
async def get_diff(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        package = None
        if package_id is not None:
            from services.package_scope import package_run_thread, require_package
            package = await require_package(db, project_id, package_id)
            tid = await package_run_thread(db, project_id, package_id)
            current_blocks = await list_blocks(db, project_id, tid, fallback_all=False) if tid else []
            snapshot_row = await (await db.execute(
                """SELECT * FROM project_snapshots
                   WHERE project_id = ? AND package_id = ? ORDER BY id DESC LIMIT 1""",
                (project_id, package_id),
            )).fetchone()
            snapshot = dict(snapshot_row) if snapshot_row else None
        else:
            tid = await current_run_thread_id(db, project_id)
            current_blocks = await list_blocks(db, project_id, tid, fallback_all=True)
            snapshot = await get_snapshot(db, project["base_snapshot_id"]) if project.get("base_snapshot_id") else None

        if snapshot is None:
            added = [{"block_id": b["block_id"], "title": b.get("title"), "content": b.get("content")}
                     for b in current_blocks]
            return JSONResponse(content={"added": added, "removed": [], "changed": []})

    base_blocks: list[dict] = json.loads(snapshot["snapshot"])
    result = _compute_diff(base_blocks, current_blocks)
    return JSONResponse(content=result)


@router.get("/projects/{project_id}/export")
async def export_docx(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
    thread_id: str | None = Query(default=None, alias="threadId"),
):
    """导出 Word 文档，过滤 status=empty 的 blocks"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        package = None
        if thread_id:
            run = await (await db.execute(
                'SELECT package_id FROM workflow_runs WHERE thread_id=? AND project_id=?',
                (thread_id, project_id))).fetchone()
            if not run or (package_id is not None and run[0] != package_id):
                raise HTTPException(status_code=404, detail='应答运行不属于当前项目或标包')
            tid = thread_id
            all_blocks = await list_blocks(db, project_id, tid, fallback_all=False)
        elif package_id is not None:
            from services.package_scope import package_run_thread, require_package
            package = await require_package(db, project_id, package_id)
            tid = await package_run_thread(db, project_id, package_id)
            all_blocks = await list_blocks(db, project_id, tid, fallback_all=False) if tid else []
        else:
            tid = await current_run_thread_id(db, project_id)
            all_blocks = await list_blocks(db, project_id, tid, fallback_all=True)
        from services.split_answers import get_answers
        answers = await get_answers(db, project_id, tid) if tid else {}
        response_type = (answers.get('response_plan') or {}).get('type')
        response_label = {'commerce': '商务应答', 'tech': '技术应答'}.get(response_type, '')
        template_row = await (await db.execute('SELECT template FROM response_document_settings WHERE project_id=? AND thread_id=?',
                                              (project_id, tid or ''))).fetchone()
        template = template_row[0] if template_row else None
        # 旧导出入口也读取本应答模板及内部图片，避免不同入口丢图。
        import re
        from routes.chapter_studio import studio_image_bytes
        images = {}
        image_pattern = re.compile(r'!\[[^\]]*\]\((/api/studio/(?:materials/\d+|diagrams/[0-9a-fA-F]{16,})/image)\)')
        for b in all_blocks:
            for match in image_pattern.finditer(b.get('content') or ''):
                key = match.group(1)
                if key not in images:
                    images[key] = (await studio_image_bytes(db, key, project_id))[0]

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
        kwargs = {'template': template, 'images': images} if template or images else {}
        docx_bytes = sections_to_docx(sections_data, doc_title=project.get("name", "技术方案") + (f' · {response_label}' if response_label else ''), **kwargs)
    except Exception as exc:
        logger.exception(f"DOCX 生成失败：{exc}")
        raise HTTPException(status_code=500, detail=f"Word 文档生成失败：{exc}")

    package_name = (package or {}).get("display_name") or (package or {}).get("package_code")
    filename = f"{project.get('name', '技术方案')}{'-' + package_name if package_name else ''}{'-' + response_label if response_label else ''}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition(filename)},
    )
