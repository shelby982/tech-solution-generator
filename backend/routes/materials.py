"""
backend/routes/materials.py — 材料上传、列表、SSE 大纲生成

端点：
  POST /api/projects/{id}/materials  — 上传材料文件
  GET  /api/projects/{id}/materials  — 材料列表
  POST /api/projects/{id}/outline    — SSE：解析主材料，写入 blocks 表
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse

from db import get_db
from services.block_store import (
    create_material,
    list_materials,
    get_project,
    create_block,
    delete_blocks_by_project,
    update_material_parsed,
)
from services.parser import parse_document
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["materials"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc"}
UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"


@router.post("/projects/{project_id}/materials")
async def upload_material(
    project_id: int,
    file: UploadFile = File(...),
):
    """上传材料文件（multipart/form-data），写入 materials 表"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")

        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{suffix}")

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="文件内容为空")

        # 存盘
        dest_dir = UPLOAD_ROOT / str(project_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / file.filename).write_bytes(content)

        relative_path = f"uploads/{project_id}/{file.filename}"
        mat = await create_material(
            db,
            project_id=project_id,
            filename=file.filename,
            type_=suffix.lstrip("."),
            file_path=relative_path,
            role="main",
        )

    return JSONResponse(content=mat)


@router.get("/projects/{project_id}/materials")
async def list_project_materials(project_id: int):
    """返回项目下所有材料"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        materials = await list_materials(db, project_id)
    return JSONResponse(content=materials)


@router.post("/projects/{project_id}/outline")
async def generate_outline(project_id: int):
    """
    SSE：解析 role=main 材料，逐节写入 heading+content block。

    事件：outline_start / outline_block / outline_done / error
    """
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        materials = await list_materials(db, project_id)
        main_material = next((m for m in materials if m.get("role") == "main"), None)
        if main_material is None:
            raise HTTPException(status_code=404, detail="该项目没有主材料，请先上传文件")
        material_id = main_material["id"]
        file_path = main_material["file_path"]
        filename = main_material["filename"]

    abs_path = UPLOAD_ROOT.parent / file_path  # backend/data/ + uploads/...
    suffix = Path(filename).suffix.lower()

    async def event_generator():
        yield format_sse_event("outline_start", {
            "project_id": project_id,
            "material_id": material_id,
        })
        try:
            parsed = parse_document(str(abs_path), suffix=suffix, filename=filename)
            async with get_db() as db:
                await delete_blocks_by_project(db, project_id)
                block_count = 0
                for idx, section in enumerate(parsed.sections):
                    # heading block
                    h_id = f"{section.id}-H"
                    await create_block(
                        db,
                        project_id=project_id,
                        block_id=h_id,
                        kind="heading",
                        level=section.level,
                        title=section.title,
                        domain=section.title,
                        parent_title="",
                        requirement="",
                        score="",
                        source="",
                        order_idx=idx * 2,
                    )
                    yield format_sse_event("outline_block", {
                        "block_id": h_id, "kind": "heading",
                        "level": section.level, "title": section.title,
                    })
                    block_count += 1

                    # content block
                    c_id = f"{section.id}-C"
                    await create_block(
                        db,
                        project_id=project_id,
                        block_id=c_id,
                        kind="content",
                        level=section.level,
                        title=f"{section.title}（正文）",
                        domain=section.title,
                        parent_title=section.title,
                        requirement=section.content_hint[:500] if section.content_hint else "",
                        score="",
                        source="",
                        order_idx=idx * 2 + 1,
                    )
                    yield format_sse_event("outline_block", {
                        "block_id": c_id, "kind": "content",
                        "level": section.level, "title": None,
                    })
                    block_count += 1

                await update_material_parsed(db, material_id)

            yield format_sse_event("outline_done", {"block_count": block_count})

        except Exception as exc:
            logger.exception(f"大纲生成失败 project={project_id}: {exc}")
            yield format_sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
