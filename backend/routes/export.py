"""
backend/routes/export.py — Word 导出

端点：
  GET  /api/projects/{id}/export  — 导出 Word 文档
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from db import get_db
from services.block_store import (
    get_project,
    list_blocks,
    current_run_thread_id,
)
from infra.docx import sections_to_docx

logger = logging.getLogger(__name__)
router = APIRouter(tags=["export"])


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    ascii_fb = filename.encode("ascii", errors="replace").decode("ascii")
    return f'attachment; filename="{ascii_fb}"; filename*=UTF-8\'\'{encoded}'


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
