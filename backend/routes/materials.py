"""
backend/routes/materials.py — 材料上传、列表、SSE 大纲生成

端点：
  POST /api/projects/{id}/materials  — 上传材料文件
  GET  /api/projects/{id}/materials  — 材料列表
  POST /api/projects/{id}/outline    — SSE：解析主材料，写入 blocks 表
"""

import json as _json
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
    update_material_parse_status,
    create_chunk,
    delete_material,
    list_chunks_by_materials,
)
from infra.parser import parse_document
from infra.retrieval import keyword_search as retrieve_chunks, build_bm25_index, assign_chunks_to_sections
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["materials"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".ppt", ".pptx"}
UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"

# 全局 OCR 串行锁：PaddleOCR 推理 CPU 密集，且 mac arm64 上并发易段错误。
# 多份图片化材料同时上传时按 FIFO 排队跑，避免内存峰值导致进程崩溃。
import asyncio as _asyncio
_OCR_SEMAPHORE = _asyncio.Semaphore(1)


async def _parse_and_chunk_in_background(
    material_id: int,
    abs_path: str,
    suffix: str,
    filename: str,
    material_role: str = "source",
) -> None:
    """后台跑 parse_document + chunks 入库；保护事件循环不被同步 OCR 阻塞。
    scoring / evaluation 角色按行细粒度切片，确保 BM25 反向分配时能精准命中各章节。
    任何异常都吞掉，仅把 parse_status 标 failed，避免 unhandled task exception 触发进程退出。
    """
    async with _OCR_SEMAPHORE:
        try:
            async with get_db() as db:
                await update_material_parse_status(db, material_id, "parsing")
            parsed = await _asyncio.to_thread(
                parse_document, abs_path, suffix=suffix, filename=filename
            )
            async with get_db() as db:
                if material_role in ("scoring", "evaluation"):
                    chunks = _split_for_scoring_evaluation(parsed)
                    for idx, content in enumerate(chunks):
                        await create_chunk(db, material_id=material_id, chunk_index=idx, content=content)
                    logger.info(f"素材 {material_id}（{material_role}）细粒度切片：{len(chunks)} 条")
                else:
                    for idx, sec in enumerate(parsed.sections):
                        text = f"{sec.title}\n{sec.raw_content or ''}".strip()
                        if text:
                            await create_chunk(db, material_id=material_id, chunk_index=idx, content=text)
                    logger.info(f"素材 {material_id}（{material_role}）按章节切片：{len(parsed.sections)} 段")
                await update_material_parse_status(db, material_id, "done")
        except Exception as e:
            logger.warning(f"素材 {material_id} 解析失败：{e}")
            try:
                async with get_db() as db:
                    await update_material_parse_status(db, material_id, "failed")
            except Exception:
                pass


def _split_for_scoring_evaluation(parsed) -> list[str]:
    """评分表 / 评审要素的细粒度切片：把所有 section 文本按行汇总，
    用 4 行 + 1 行重叠的滑动窗口产出 chunks，避免 BM25 反向分配时一锅端。
    """
    all_lines: list[str] = []
    for sec in parsed.sections:
        full = f"{sec.title}\n{sec.raw_content or ''}".strip()
        for ln in full.split("\n"):
            ln = ln.strip()
            if ln:
                all_lines.append(ln)
    if not all_lines:
        return []
    window, stride = 4, 3  # 4 行/片，步长 3 → 相邻片重叠 1 行，提升召回
    chunks: list[str] = []
    i = 0
    while i < len(all_lines):
        piece = "\n".join(all_lines[i:i + window]).strip()
        if piece:
            chunks.append(piece)
        i += stride
    return chunks


@router.post("/projects/{project_id}/materials")
async def upload_material(
    project_id: int,
    file: UploadFile = File(...),
    material_role: str = Form(default="main_rfp"),
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
            file_size=len(content),
        )

    if material_role in ("source", "scoring", "evaluation"):
        abs_path = UPLOAD_ROOT / str(project_id) / file.filename
        suffix = Path(file.filename).suffix.lower()
        # 后台跑 OCR + 切片，立即返回上传成功；前端通过 GET /materials 轮询 parse_status 看进度。
        _asyncio.create_task(
            _parse_and_chunk_in_background(
                material_id=mat["id"],
                abs_path=str(abs_path),
                suffix=suffix,
                filename=file.filename,
                material_role=material_role,
            )
        )
    else:
        # 主招标文件不在上传时切片，直接置 done 让前端不显示"解析中"标签
        async with get_db() as db_done:
            await update_material_parse_status(db_done, mat["id"], "done")
        mat["parse_status"] = "done"

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


@router.delete("/materials/{material_id}")
async def delete_material_route(material_id: int):
    """删除指定材料"""
    async with get_db() as db:
        deleted = await delete_material(db, material_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="材料不存在")
    return JSONResponse(content={"ok": True})


@router.get("/materials/{material_id}/file")
async def view_material_file(material_id: int):
    """返回材料原始文件，PDF 内联预览，其它走附件下载"""
    from fastapi.responses import FileResponse

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT project_id, filename FROM materials WHERE id = ?", (material_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="材料不存在")
        project_id = row["project_id"]
        filename = row["filename"]

    abs_path = UPLOAD_ROOT / str(project_id) / filename
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = abs_path.suffix.lower()
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
    }.get(suffix, "application/octet-stream")

    headers = {}
    if suffix != ".pdf":
        from urllib.parse import quote
        encoded_name = quote(filename)
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_name}"
    return FileResponse(str(abs_path), media_type=media_type, headers=headers)


@router.put("/material-chunks/{material_id}/{chunk_index}")
async def update_chunk_route(material_id: int, chunk_index: int, body: dict):
    """更新某个 chunk 的内容"""
    from services.block_store import update_chunk_content

    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")

    async with get_db() as db:
        updated = await update_chunk_content(db, material_id, chunk_index, content)
    if updated is None:
        raise HTTPException(status_code=404, detail="chunk 不存在")
    return JSONResponse(content=updated)


@router.post("/projects/{project_id}/outline")
async def generate_outline(project_id: int, model: str | None = None):
    """
    SSE：读取主招标文件（main_rfp），按章节顺序提炼大纲；评分表（scoring）/评审要素（evaluation）切片
    作为 BM25 上下文注入到对应章节，逐条写入 blocks 表。

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
            # 角色分类（兼容旧值 requirement → main_rfp）
            main_rfp_materials = [m for m in materials if m.get("role") in ("main_rfp", "requirement")]
            scoring_materials = [m for m in materials if m.get("role") == "scoring"]
            evaluation_materials = [m for m in materials if m.get("role") == "evaluation"]

            if not main_rfp_materials:
                yield format_sse_event("error", {"message": "请先上传主招标文件（main_rfp 角色）"})
                return
            if len(main_rfp_materials) > 1:
                yield format_sse_event("error", {"message": "项目仅支持 1 份主招标文件，请删除多余的并重试"})
                return

            mat = main_rfp_materials[0]
            abs_path = UPLOAD_ROOT / str(project_id) / mat["filename"]
            suffix = Path(mat["filename"]).suffix.lower()
            # 同 upload_material：parse_document 含同步 OCR，必须放线程池，
            # 否则会阻塞 SSE 流式响应所在的事件循环
            import asyncio
            parsed = await asyncio.to_thread(
                parse_document, str(abs_path), suffix=suffix, filename=mat["filename"]
            )

            # 评分/评审 chunks 拉取
            async with get_db() as db:
                scoring_chunks = await list_chunks_by_materials(db, [m["id"] for m in scoring_materials])
                evaluation_chunks = await list_chunks_by_materials(db, [m["id"] for m in evaluation_materials])

            # 反向分配：每条 scoring/evaluation chunk → 最匹配的 section_idx，
            # 保证所有 chunk 都被分配（含 -1 未归类桶），避免静默丢失
            section_texts = [f"{s.title}\n{s.raw_content or ''}" for s in parsed.sections]
            scoring_assignment = assign_chunks_to_sections(scoring_chunks, section_texts)
            evaluation_assignment = assign_chunks_to_sections(evaluation_chunks, section_texts)

            sections = []
            sec_meta = []
            for i, sec in enumerate(parsed.sections):
                s_chunks = scoring_assignment.get(i, [])
                e_chunks = evaluation_assignment.get(i, [])
                scoring_ctx = "\n---\n".join(c["content"] for c in s_chunks)
                evaluation_ctx = "\n---\n".join(c["content"] for c in e_chunks)
                sections.append({
                    "title": sec.title,
                    "content": sec.raw_content or "",
                    "special_marks": ",".join(sec.special_marks) if sec.special_marks else "",
                    "scoring_context": scoring_ctx,
                    "evaluation_context": evaluation_ctx,
                    "_scoring_chunks": s_chunks,
                    "_evaluation_chunks": e_chunks,
                })
                sec_meta.append({"level": sec.level})

            unassigned_scoring = scoring_assignment.get(-1, [])
            unassigned_evaluation = evaluation_assignment.get(-1, [])

            from infra.llm import dispatch_outline_json
            from services.config_store import config_store
            configs, rr_index = config_store.get_configs_and_next_index()
            if model and configs:
                matched = [c for c in configs if c.model == model]
                if matched:
                    configs = matched
                    rr_index = 0

            total = len(sections)

            # 预加载所有 source 素材切片（含 filename），用于每章节完成后匹配正文素材
            async with get_db() as db:
                source_material_ids = [m["id"] for m in materials if m.get("role") == "source"]
                all_chunks = await list_chunks_by_materials(db, source_material_ids) if source_material_ids else []

            # 预构建 BM25 索引（一次构建，逐章复用）
            bm25_index = None
            if all_chunks:
                bm25_index, _ = build_bm25_index(all_chunks)

            # 先推送所有章节标题，前端渲染左侧大纲和右侧骨架卡片
            yield format_sse_event("outline_sections", {
                "sections": [
                    {"title": s["title"], "idx": i, "level": sec_meta[i]["level"]}
                    for i, s in enumerate(sections)
                ],
                "total": total,
            })

            # 逐章节提炼，流式推送
            async with get_db() as db:
                await delete_blocks_by_project(db, project_id)

            completed = 0
            async for item in dispatch_outline_json(configs, rr_index, sections):
                idx = completed
                block_id_str = f"outline-{idx}"
                key_points  = item.get("key_points", "")
                veto_items  = item.get("veto_items", "")
                bonus_items = item.get("bonus_items", "")
                # 后端透传：score_items 直接用本章命中的 scoring chunks 原文，
                # 不依赖模型从 scoring_context 里提炼，避免遗漏或改写
                sec_scoring_chunks = sections[idx].get("_scoring_chunks", [])
                if sec_scoring_chunks:
                    score_items = "\n---\n".join(c["content"] for c in sec_scoring_chunks)
                else:
                    score_items = item.get("score_items", "")

                # 短章节（≤ 800 字）requirement 直接用原文覆盖，避免 LLM 概括丢内容；
                # 保留模型在 requirement 里追加的【评分对应】/【评审对应】hint
                sec_content_raw = (sections[idx].get("content") or "").strip()
                if sec_content_raw and len(sec_content_raw) <= 800:
                    requirement = sec_content_raw
                    llm_req = (item.get("requirement") or "").strip()
                    for tag in ("【评分对应】", "【评审对应】"):
                        if tag in llm_req:
                            tag_idx = llm_req.find(tag)
                            requirement = f"{requirement}\n\n{llm_req[tag_idx:]}"
                            break
                else:
                    requirement = item.get("requirement", "")
                evidence_required = item.get("evidence_required", "")
                constraint_level = item.get("constraint_level", "recommended")
                indicators = item.get("indicators", "")
                sec_level = sec_meta[idx]["level"] if idx < len(sec_meta) else 1

                # 关键词匹配 Top-3 原始素材片段
                query = f"{item['title']} {requirement} {key_points}"
                top_chunks = retrieve_chunks(all_chunks, query=query, top_k=3, bm25_index=bm25_index) if all_chunks else []
                qw = set(query.lower().split())
                matched_sources = [
                    {
                        "material_id": c["material_id"],
                        "filename": c.get("filename", ""),
                        "chunk_index": c["chunk_index"],
                        "content": c["content"],
                        "score": len(qw & set(c["content"].lower().split())),
                    }
                    for c in top_chunks
                ]

                async with get_db() as db:
                    block = await create_block(
                        db, project_id=project_id, block_id=block_id_str,
                        kind="content", level=sec_level, title=item["title"],
                        domain=item["title"], parent_title="",
                        requirement=requirement,
                        key_points=key_points,
                        veto_items=veto_items,
                        bonus_items=bonus_items,
                        score_items=score_items,
                        evidence_required=evidence_required,
                        constraint_level=constraint_level,
                        indicators=indicators,
                        score="",
                        source=_json.dumps(matched_sources, ensure_ascii=False),
                        order_idx=idx,
                    )

                yield format_sse_event("outline_block", {
                    "id": block["id"],
                    "block_id": block_id_str,
                    "title": item["title"],
                    "requirement": requirement,
                    "key_points": key_points,
                    "veto_items": veto_items,
                    "bonus_items": bonus_items,
                    "score_items": score_items,
                    "evidence_required": evidence_required,
                    "indicators": indicators,
                    "constraint_level": constraint_level,
                    "order_idx": idx,
                    "matched_sources": matched_sources,
                    "error": item.get("error", ""),
                })
                completed += 1

            # 兜底：未匹配到任何章节的评分/评审条款，单独生成一个 block，避免静默丢失
            if unassigned_scoring or unassigned_evaluation:
                fb_score_items = "\n---\n".join(c["content"] for c in unassigned_scoring)
                fb_eval_ctx = "\n---\n".join(c["content"] for c in unassigned_evaluation)
                fb_requirement = "以下评分/评审条款未能匹配到任何招标章节，请人工归档。"
                if fb_eval_ctx:
                    fb_requirement += f"\n\n【未归类评审条款】\n{fb_eval_ctx}"
                fb_idx = completed
                fb_block_id = f"outline-{fb_idx}"
                async with get_db() as db:
                    fb_block = await create_block(
                        db, project_id=project_id, block_id=fb_block_id,
                        kind="content", level=1,
                        title="未归类评分/评审项",
                        domain="未归类评分/评审项", parent_title="",
                        requirement=fb_requirement,
                        key_points="",
                        veto_items="",
                        bonus_items="",
                        score_items=fb_score_items,
                        evidence_required="",
                        constraint_level="recommended",
                        indicators="",
                        score="",
                        source=_json.dumps([], ensure_ascii=False),
                        order_idx=fb_idx,
                    )
                yield format_sse_event("outline_block", {
                    "id": fb_block["id"],
                    "block_id": fb_block_id,
                    "title": "未归类评分/评审项",
                    "requirement": fb_requirement,
                    "key_points": "",
                    "veto_items": "",
                    "bonus_items": "",
                    "score_items": fb_score_items,
                    "evidence_required": "",
                    "indicators": "",
                    "constraint_level": "recommended",
                    "order_idx": fb_idx,
                    "matched_sources": [],
                    "error": "",
                })
                completed += 1

            # 标记主 RFP 材料为已解析
            async with get_db() as db:
                if main_rfp_materials:
                    await update_material_parsed(db, main_rfp_materials[0]["id"])

            yield format_sse_event("outline_done", {"block_count": completed})

        except Exception as exc:
            logger.exception(f"大纲生成失败 project={project_id}: {exc}")
            yield format_sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

        # 构建一次 BM25 索引
        bm25_idx, _ = build_bm25_index(chunks)

        mapped = 0
        for block in blocks:
            query = f"{block.get('title', '')} {block.get('requirement', '')} {block.get('key_points', '')}"
            top3 = retrieve_chunks(chunks, query=query, top_k=3, bm25_index=bm25_idx)
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
