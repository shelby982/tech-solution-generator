"""
backend/routes/materials.py — 材料上传、列表、SSE 大纲生成

端点：
  POST /api/projects/{id}/materials  — 上传材料文件
  GET  /api/projects/{id}/materials  — 材料列表
  POST /api/projects/{id}/outline    — SSE：解析主材料，写入 blocks 表
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse

from db import get_db
from services.block_store import (
    create_material,
    list_materials,
    get_project,
    create_block,
    delete_blocks_by_project,
    update_material_parsed,
    create_chunk,
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
    material_role: str = Form(default="requirement"),
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
            role=material_role,
        )

    if material_role == "source":
        abs_path = UPLOAD_ROOT / str(project_id) / file.filename
        suffix = Path(file.filename).suffix.lower()
        try:
            parsed = parse_document(str(abs_path), suffix=suffix, filename=file.filename)
            async with get_db() as db2:
                for idx, sec in enumerate(parsed.sections):
                    text = f"{sec.title}\n{sec.content_hint or ''}".strip()
                    if text:
                        await create_chunk(db2, material_id=mat["id"], chunk_index=idx, content=text)
        except Exception as e:
            logger.warning(f"素材切片失败 {file.filename}: {e}")

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
    SSE：读取 role=requirement 材料，调用 AI 生成结构化大纲，逐条写入 blocks 表。

    事件：outline_start / outline_block / outline_done / error
    """
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

    async def event_generator():
        yield format_sse_event("outline_start", {"project_id": project_id})
        try:
            async with get_db() as db:
                materials = await list_materials(db, project_id)
            req_materials = [m for m in materials if m.get("role") == "requirement"]
            if not req_materials:
                yield format_sse_event("error", {"message": "请先上传应标文件（应答文件/技术规范书）"})
                return

            combined_text = ""
            for mat in req_materials:
                abs_path = UPLOAD_ROOT / str(project_id) / mat["filename"]
                suffix = Path(mat["filename"]).suffix.lower()
                parsed = parse_document(str(abs_path), suffix=suffix, filename=mat["filename"])
                for sec in parsed.sections:
                    combined_text += f"\n## {sec.title}\n{sec.content_hint or ''}"

            from services.llm import dispatch_outline_json
            from services.config_store import config_store
            configs, rr_index = config_store.get_configs_and_next_index()
            outline_items = await dispatch_outline_json(configs, rr_index, combined_text)

            async with get_db() as db:
                await delete_blocks_by_project(db, project_id)
                for idx, item in enumerate(outline_items):
                    block_id_str = f"outline-{idx}"
                    await create_block(
                        db, project_id=project_id, block_id=block_id_str,
                        kind="content", level=1, title=item["title"],
                        domain=item["title"], parent_title="",
                        requirement=item.get("requirement", ""),
                        score="", source="", order_idx=idx,
                    )
                    yield format_sse_event("outline_block", {
                        "block_id": block_id_str,
                        "title": item["title"],
                        "requirement": item.get("requirement", ""),
                    })
                # 标记第一个 requirement 材料为已解析
                if req_materials:
                    await update_material_parsed(db, req_materials[0]["id"])

            yield format_sse_event("outline_done", {"block_count": len(outline_items)})

        except Exception as exc:
            logger.exception(f"大纲生成失败 project={project_id}: {exc}")
            yield format_sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


import json as _json

from services.block_store import list_chunks_by_project, list_blocks as _list_blocks


@router.post("/projects/{project_id}/map-sources")
async def map_sources(project_id: int):
    """为每个 block 匹配 Top-3 相关素材片段，写入 blocks.source"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        blocks = await _list_blocks(db, project_id)
        chunks = await list_chunks_by_project(db, project_id)

        if not chunks:
            return JSONResponse(content={"mapped": 0, "message": "暂无素材片段"})

        def _score(block_text: str, chunk_text: str) -> int:
            bw = set(block_text.lower().split())
            cw = set(chunk_text.lower().split())
            return len(bw & cw)

        mapped = 0
        for block in blocks:
            query = f"{block.get('title', '')} {block.get('requirement', '')}"
            scored = sorted(chunks, key=lambda c: _score(query, c["content"]), reverse=True)
            top3 = scored[:3]
            refs = [
                {"material_id": c["material_id"], "chunk_index": c["chunk_index"],
                 "snippet": c["content"][:120]}
                for c in top3
            ]
            await db.execute(
                "UPDATE blocks SET source = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_json.dumps(refs, ensure_ascii=False), block["id"]),
            )
            mapped += 1
        await db.commit()

    return JSONResponse(content={"mapped": mapped})
