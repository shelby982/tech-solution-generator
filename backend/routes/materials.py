"""
backend/routes/materials.py — 材料上传、列表、SSE 大纲生成

端点：
  POST /api/projects/{id}/materials  — 上传材料文件
  GET  /api/projects/{id}/materials  — 材料列表
  POST /api/projects/{id}/outline    — SSE：解析主材料，写入 blocks 表
"""

import json as _json
import logging
import uuid
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
    list_blocks,
    current_run_thread_id,
)
from infra.parser import parse_document
from infra.retrieval import keyword_search as retrieve_chunks, build_bm25_index, assign_chunks_to_sections
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["materials"])

# 只列**解析器真能读**的格式（infra/parser/__init__.py 只分发 pdf/docx/doc）。
# 曾经放过 .ppt/.pptx：上传返回 200 且显示 done，但解析器会 ValueError ——
# 到「拆分应答目录」才炸，表现为整次 run failed（要求类文件上传时还不解析，更难发现）。
ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc"}

# 材料角色白名单（上传与 PATCH 共用）：未知角色会写入一条两个面板都不显示的僵尸记录。
# `spec` = **技术规范书 / 技术招标文件**（招标人给的附件，属要求类）；`source` = 我方技术素材。
# 两者混用会让技术规范书漏出要求链路（实测踩到），口径与前端 material-roles.mjs 一致。
ALLOWED_ROLES = {"spec", "main_rfp", "requirement", "scoring", "evaluation", "source"}
UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"


def _abs_upload_path(rel_path: str) -> Path:
    """materials.file_path（形如 ``uploads/{pid}/{落盘名}``，相对 backend/data/）→ 绝对路径。

    **一律用 file_path 解析，不要用 filename 拼** —— 落盘名现在带 uuid 前缀（同名重传
    不再互相覆盖），filename 只是给用户看的原始名字。
    """
    return UPLOAD_ROOT.parent / rel_path

# 上传侧的后台解析排队：多份图片化材料同时上传时按 FIFO 跑，避免内存峰值。
# 注意 OCR 推理本身的并发保护在 infra/parser/_ocr.py（线程锁，覆盖工作流那条路径）。
import asyncio as _asyncio
_OCR_SEMAPHORE = _asyncio.Semaphore(1)

# 后台解析任务引用：asyncio 只持弱引用，任务可能在 to_thread 挂起期间被 GC，
# 那样 parse_status 会永久停在 parsing、chunks 永不入库。留强引用 + 完成即移除。
_PARSE_TASKS: set = set()


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
                            await create_chunk(
                                db, material_id=material_id, chunk_index=idx, content=text,
                                # 页码带进切片 → 依据出处可以精确到页（Word 无页码则为 0）
                                page=int(getattr(sec, "page", 0) or 0),
                            )
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


@router.get("/projects/{project_id}/response-items")
async def get_response_items(project_id: int, threadId: str | None = None):
    """该项目的「章节 → 应答要求条目」表（由 blocks 的 8 字段展开，零模型调用）。

    匹配阶段把素材挂到条目上（``blocks.source[].item_ids``），前端据此按条目分组展示依据；
    拆分规则只在后端一份（``domain/spec/response_items.py``），避免前端 JS 再抄一遍导致漂移。
    """
    from domain.spec.response_items import expand_response_items

    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)
        blocks = await list_blocks(db, project_id, tid, fallback_all=True)
    return JSONResponse(content={
        str(b.get("block_id") or b.get("id")): expand_response_items(
            str(b.get("block_id") or b.get("id")), b,
        )
        for b in blocks
    })


@router.get("/projects/{project_id}/match-overview")
async def get_match_overview(project_id: int, threadId: str | None = None):
    """匹配总览：章节 / 应答条目 / 依据 / 素材使用（零模型调用，纯聚合）。

    前端用它渲染顶部总览、章节树徽标与跨章节覆盖矩阵 —— 以前这些数字无处可查：
    匹配跑完只有章节行上一个「依据 N」小徽标，看不到"哪章缺依据、缺哪几条、
    哪份素材没用上"。聚合逻辑在 ``domain/material/overview.py``（纯函数，有测试）。
    """
    from domain.material.overview import build_match_overview

    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)
        blocks = await list_blocks(db, project_id, tid, fallback_all=True)
    return JSONResponse(content=build_match_overview(blocks))


@router.get("/projects/{project_id}/material-chunks")
async def list_project_material_chunks(
    project_id: int, materialId: int | None = None, q: str | None = None, limit: int = 200,
):
    """项目的素材切片列表（人工挂接的"找素材"选择器用）。

    只回**我方素材**（role='source'）：招标文件不该被我方挂成"我方素材依据"
    （见方案 §4.D 的 `allows_tender_evidence`）。``q`` 是大小写不敏感的子串过滤，
    ``limit`` 兜住极端情况（一份 300 页 PDF 可能切出几百段）。
    """
    from services.block_store import OURS_MATERIAL_ROLES, list_chunks_with_filename

    async with get_db() as db:
        rows = await list_chunks_with_filename(db, project_id, roles=OURS_MATERIAL_ROLES)
    if materialId is not None:
        rows = [r for r in rows if int(r.get("material_id") or 0) == int(materialId)]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in (r.get("content") or "").lower()]
    out = []
    for r in rows[: max(1, min(int(limit or 200), 500))]:
        content = r.get("content") or ""
        out.append({
            "chunk_id": r.get("id"),
            "material_id": r.get("material_id"),
            "filename": r.get("filename") or "",
            "chunk_index": r.get("chunk_index"),
            "page": r.get("page") or 0,
            "preview": content[:120],
        })
    return JSONResponse(content={"total": len(rows), "chunks": out})


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
            raise HTTPException(
                status_code=400,
                detail=f"不支持的格式：{suffix}（当前支持 PDF / DOCX / DOC）",
            )

        role = (material_role or "").strip()
        if role not in ALLOWED_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"未知的材料角色：{material_role}（可选：{'、'.join(sorted(ALLOWED_ROLES))}）",
            )

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="文件内容为空")

        # 存盘：只取 basename（防 `../../x.pdf` 写到上传根之外），并加短 uuid 前缀
        # ——同名重复上传此前会覆盖前一份字节，而 materials 表留下两条指向同一路径的
        # 记录（两份「不同」要求文件实际内容相同、第一份静默丢失）。
        safe_name = Path(file.filename).name
        stored_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        dest_dir = UPLOAD_ROOT / str(project_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / stored_name).write_bytes(content)

        relative_path = f"uploads/{project_id}/{stored_name}"
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
        abs_path = _abs_upload_path(mat["file_path"])
        suffix = Path(file.filename).suffix.lower()
        # 后台跑 OCR + 切片，立即返回上传成功；前端通过 GET /materials 轮询 parse_status 看进度。
        task = _asyncio.create_task(
            _parse_and_chunk_in_background(
                material_id=mat["id"],
                abs_path=str(abs_path),
                suffix=suffix,
                filename=file.filename,
                material_role=material_role,
            )
        )
        _PARSE_TASKS.add(task)
        task.add_done_callback(_PARSE_TASKS.discard)
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
    """删除指定材料（含盘上文件）。"""
    file_path = ""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT file_path FROM materials WHERE id = ?", (material_id,)
        )
        row = await cursor.fetchone()
        file_path = (row["file_path"] if row else "") or ""
        deleted = await delete_material(db, material_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="材料不存在")
    if file_path:
        try:
            _abs_upload_path(file_path).unlink(missing_ok=True)
        except Exception as e:      # 删盘失败不影响接口结果：库已经不指向它了
            logging.getLogger(__name__).warning(f"删除材料文件失败（{file_path}）：{e}")
    return JSONResponse(content={"ok": True})


@router.patch("/materials/{material_id}")
async def update_material_route(material_id: int, body: dict):
    """更新材料属性：当前仅支持 role 修改（spec/main_rfp/scoring/evaluation/source）"""
    from services.block_store import update_material_role
    new_role = (body or {}).get("role")
    allowed = ALLOWED_ROLES
    if not new_role or new_role not in allowed:
        raise HTTPException(status_code=400, detail=f"非法 role：{new_role!r}")
    async with get_db() as db:
        await update_material_role(db, material_id, new_role)
    return JSONResponse(content={"id": material_id, "role": new_role})


@router.get("/materials/{material_id}/file")
async def view_material_file(material_id: int):
    """返回材料原始文件，PDF 内联预览，其它走附件下载"""
    from fastapi.responses import FileResponse

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT project_id, filename, file_path FROM materials WHERE id = ?",
            (material_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="材料不存在")
        project_id = row["project_id"]
        filename = row["filename"]

    abs_path = _abs_upload_path(row["file_path"])
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
    """[DEPRECATED] 老 demo 路径，已被 LangGraph workflow 取代。

    保留路由占位，所有调用直接返回 410 Gone，避免再写入 `outline-N` 命名的 blocks
    与新 `s1`/`s1.1` 命名共存导致前端展示异常。新流程：POST /api/workflow/start。
    """
    raise HTTPException(
        status_code=410,
        detail="该接口已废弃，请改用 /api/workflow/start 启动 LangGraph 工作流",
    )


@router.post("/projects/{project_id}/map-sources")
async def map_sources(project_id: int):
    """为每个 block 匹配 Top-3 相关素材片段，写入 blocks.source"""
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        tid = await current_run_thread_id(db, project_id)
        blocks = await list_blocks(db, project_id, tid, fallback_all=True)
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
