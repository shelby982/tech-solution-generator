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
    replace_materials_by_role,
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
                    # C1 片段质量：按章节切 → 尺寸归一 / 噪声过滤 / 去重
                    from services.chunk_quality import normalize as normalize_chunks
                    raw_chunks = []
                    for sec in parsed.sections:
                        text = f"{sec.title}\n{sec.raw_content or ''}".strip()
                        if text:
                            raw_chunks.append({"content": text,
                                               "page": int(getattr(sec, "page", 0) or 0)})
                    chunks = normalize_chunks(raw_chunks)
                    import json as _json
                    for idx, chunk in enumerate(chunks):
                        await create_chunk(db, material_id=material_id, chunk_index=idx,
                                           content=chunk["content"], page=chunk["page"])
                        await db.execute(
                            "UPDATE material_chunks SET tags=? WHERE material_id=? AND chunk_index=?",
                            (_json.dumps(chunk.get("tags") or [], ensure_ascii=False), material_id, idx))
                    logger.info(f"素材 {material_id}（{material_role}）切片：{len(raw_chunks)} → {len(chunks)} 段（已归一）")
                # R3：抽图片素材（DOCX 直接取媒体；PDF 按图片区域渲染）
                try:
                    from services.image_material import extract as extract_images
                    images = extract_images(abs_path, suffix=suffix)
                    for image in images:
                        await db.execute(
                            "INSERT INTO material_images (material_id, page, file_path, caption, source, size_bytes) "
                            "VALUES (?,?,?,?,?,?)",
                            (material_id, int(image.get("page") or 0), image.get("path") or "",
                             str(image.get("caption") or "")[:120], str(image.get("source") or ""),
                             int(image.get("size") or 0)))
                    await db.commit()
                    if images:
                        logger.info(f"素材 {material_id}：抽出图片 {len(images)} 张")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"素材 {material_id} 抽图失败：{exc}")
                await update_material_parse_status(db, material_id, "done")
        except Exception as e:
            logger.warning(f"素材 {material_id} 解析失败：{e}")
            try:
                async with get_db() as db:
                    await update_material_parse_status(db, material_id, "failed")
            except Exception:
                pass


def _extract_tables_from_file(abs_path: str, suffix: str) -> list[dict]:
    """按需抽取文件里的表格（用于"从素材插入表格"）。

    只做抽取，不落库：避免给 materials 加列 + 历史素材回填。
    - .docx：python-docx 的 document.tables（Word 无可靠页码 → page=0）
    - .xlsx/.xlsm：openpyxl 每个 sheet 的 used range
    - 其它（含 PDF）：不支持，返回空并给出原因
    """
    suffix = (suffix or "").lower()
    tables: list[dict] = []
    if suffix == ".docx":
        from docx import Document

        doc = Document(abs_path)
        for idx, table in enumerate(doc.tables, start=1):
            rows: list[list[str]] = []
            for row in table.rows:
                cells = [(cell.text or "").replace("\n", " ").strip() for cell in row.cells]
                if any(cells):
                    rows.append(cells)
            if len(rows) >= 2:
                tables.append({"index": idx, "name": f"表 {idx}", "rows": rows,
                               "cols": max(len(r) for r in rows), "page": 0,
                               "header": rows[0]})
        return tables
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(abs_path, data_only=True, read_only=True)
        for idx, sheet in enumerate(wb.worksheets, start=1):
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v).strip() for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append(cells)
            if len(rows) >= 2:
                tables.append({"index": idx, "name": sheet.title or f"表 {idx}", "rows": rows,
                               "cols": max(len(r) for r in rows), "page": 0,
                               "header": rows[0]})
        return tables
    return tables


@router.get("/materials/{material_id}/tables")
async def material_tables(material_id: int):
    """素材可插入的表格（按需解析，不落库）。"""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, filename, file_path, type FROM materials WHERE id = ? AND deleted_at IS NULL",
            (material_id,),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="素材不存在")
    path = _abs_upload_path(row["file_path"] or "")
    suffix = path.suffix.lower() or f".{str(row['type'] or '').lower()}"
    if not path.exists():
        return {"tables": [], "reason": "源文件已不存在（可能已清理）"}
    try:
        tables = await _asyncio.to_thread(_extract_tables_from_file, str(path), suffix)
    except Exception as exc:  # noqa: BLE001 - 抽取失败不应 500
        logger.warning("素材 %s 表格抽取失败：%s", material_id, exc)
        return {"tables": [], "reason": f"表格抽取失败：{exc}"}
    if not tables:
        hint = "PDF 表格暂不支持自动抽取，可复制内容后手动插入表格" if suffix == ".pdf" else "该文件里没有可识别的表格"
        return {"tables": [], "reason": hint}
    return {"tables": tables}


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


@router.get("/projects/{project_id}/scope")
async def get_project_scope(project_id: int):
    """项目级**投标范围**（投标人视角：我投哪个标的/标包）。"""
    from services.split_answers import get_project_scope as _get
    async with get_db() as db:
        return JSONResponse(content=await _get(db, project_id))


@router.put("/projects/{project_id}/scope")
async def put_project_scope(project_id: int, payload: dict):
    """设定投标范围。一次设定、贯穿拆分与报告 —— 不再每次靠提炼要求里的文字去猜。

    用户口径「**每个标包是单独的内容**」：确认标包时还会把**当前运行归属到该标包**
    （payload 带 threadId 就用它，否则用该项目最新一条运行），这样前端能按标包找/建运行，
    五阶段都只读该标包的内容。
    """
    from services.split_answers import set_project_scope as _set
    target = str(payload.get("target") or "")
    package_no = str(payload.get("package_no") or "")
    async with get_db() as db:
        out = await _set(
            db, project_id,
            target=target,
            package_no=package_no,
            package_name=str(payload.get("package_name") or ""),
        )
        tid = str(payload.get("threadId") or "")
        from domain.review.repository import WorkflowRunRepository
        repo = WorkflowRunRepository(db)
        if not tid:
            cur = await db.execute(
                "SELECT thread_id FROM workflow_runs WHERE project_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,))
            row = await cur.fetchone()
            tid = (row[0] if row else "") or ""
        if tid:
            await repo.set_scope(tid, target=target, package_no=package_no)
            out = {**(out or {}), "thread_id": tid}
    return JSONResponse(content=out)


@router.get("/projects/{project_id}/split-answers")
async def get_split_answers(project_id: int, threadId: str | None = None):
    """读拆分确认项的作答（run 级优先，缺省回落到项目级）。"""
    from services.split_answers import resolve_split_context
    async with get_db() as db:
        ctx = await resolve_split_context(db, project_id, threadId)
    return JSONResponse(content=ctx)


@router.post("/projects/{project_id}/split-answers")
async def post_split_answer(project_id: int, payload: dict):
    """作答某一项待确认内容：``{key, value, threadId?}``。

    key ∈ scope / categories / topic_placement / structure；
    作答在**下一次拆分**与报告中生效（例如把某个规范书主题指定挂到某一章）。
    """
    from services.split_answers import set_answer
    key = str(payload.get("key") or "")
    if not key:
        raise HTTPException(status_code=400, detail="缺少 key")
    try:
        async with get_db() as db:
            saved = await set_answer(db, project_id, payload.get("threadId") or None,
                                     key, payload.get("value") or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={**saved, "hint": "已保存，下次「重新拆分目录」生效"})


@router.get("/projects/{project_id}/split-report")
async def get_split_report(project_id: int, threadId: str | None = None):
    """**拆分报告**：拆目录的依据（待确认项）+ 拆完的四张清单。

    用户要求（固定流程）：
    - 拆分时把需要确认的内容与用户沟通（标包/类别识别、范围放宽、主题归属、分值合计）；
    - 拆分完成后列出：评分点及数量、评分单元与子检查项、技术要求矩阵、评分↔技术要求映射矩阵。
    """
    from services.block_store import current_run_thread_id
    from services.split_report import build_split_report

    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)

    # 评分地图需要 source_toc（checkpoint 里的解析结果）与提炼要求。
    # runner 由 main.py 在 lifespan 里注入（与 workflow 路由同一实例）。
    spec = None
    try:
        import routes.workflow as _wf
        runner = getattr(_wf, "_runner", None)
        if runner is not None and tid:
            spec = (await runner.state(tid)).get("spec") or None
    except Exception as exc:        # 地图缺失不影响其余清单
        logger.warning(f"split-report：读取 spec 失败（{exc}）")

    async with get_db() as db:
        report = await build_split_report(db, project_id, tid, spec=spec)
        # 顺手补齐包记录缺失的"标的"（只填空值、唯一命中才写、幂等）。
        # 实测 project 29：packages.target_code 为空而评分表解析出"标的1"，
        # 前端按 target+package_no 匹配失败 → 页面显示"没有解析到技术评分点"。
        try:
            from services.scope_backfill import backfill_package_targets
            await backfill_package_targets(db, project_id, report)
        except Exception as exc:
            logger.warning(f"split-report：标的回填跳过（{exc}）")
    return JSONResponse(content=report)


@router.get("/projects/{project_id}/scoring-verdict")
async def get_scoring_verdict(project_id: int, threadId: str | None = None):
    """**评分点级评审结论**：评审页主视图（得分预估 / 缺口 / 举证 / 章节下钻）。

    得分按评分点给，而 reviews 按章节存 —— 这个接口把两者对起来，
    回答"这个评分点现在能拿几分、差在哪"，而不是"这章写得好不好"。
    """
    from services.block_store import current_run_thread_id
    from services.scoring_verdict import build_scoring_verdict

    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)
    async with get_db() as db:
        data = await build_scoring_verdict(db, project_id, tid)
    return JSONResponse(content=data)


@router.get("/projects/{project_id}/scoring-risk")
async def get_scoring_risk(project_id: int, threadId: str | None = None):
    """**得分风险**：把逐章审查结论按评分点聚合。

    用户确定的核心数据关系里，审查要回答"是否真正满足要求"。
    对齐评分口径之后（reviews.satisfied / missing_points），这里回答决策问题：
    「这几个技术评分点里，哪些的应答还是不达标的？分别缺什么？」
    """
    from services.block_store import current_run_thread_id
    from services.scoring_risk import summarize_scoring_risk

    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)
        data = await summarize_scoring_risk(db, project_id, tid)
    return JSONResponse(content=data)


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
    replace: bool = Form(default=False),
):
    """上传材料文件（multipart/form-data），写入 materials 表。

    ``replace=True``：该角色已有文件时**先删旧的**（连同磁盘文件）。
    用户要求：「重新选择了上传的文件」就该以新文件为准 —— 否则同角色的旧文件会被
    解析一起读进去（旧招标文件 + 新招标文件同时生效），再点「拆解评分点」就是旧数据污染新结果。
    只对**要求类主文件**（招标文件 / 技术规范书）生效；「其他」与自有素材允许多份。
    """
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

        # 同角色替换：要求类主文件只保留最新一份（与 DELETE /materials/{id} 同一套语义：真删）
        if replace and role in ("main_rfp", "spec"):
            replaced = await replace_materials_by_role(db, project_id, role)
            for old_path in replaced:
                if not old_path:
                    continue
                try:
                    _abs_upload_path(old_path).unlink(missing_ok=True)
                except Exception as e:      # 删盘失败不影响入库：库已不指向它
                    logger.warning(f"替换材料：删除旧文件失败（{old_path}）：{e}")
            if replaced:
                logger.info(f"替换材料：项目 {project_id} 角色 {role} 替换旧文件 {len(replaced)} 份")

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
        if material_role != 'source':
            await db.execute(
                """UPDATE packages SET status='needs_review', updated_at=CURRENT_TIMESTAMP
                   WHERE project_id=? AND status='confirmed'""", (project_id,),
            )
            await db.commit()

    abs_path = _abs_upload_path(mat["file_path"])
    suffix = Path(file.filename).suffix.lower()
    # 所有材料都必须解析正文。标包识别首先依赖主招标文件/技术规范书，
    # 不能再把这两类文件直接标成 done 却不给 material_chunks 写入内容。
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
    """删除指定材料（库记录 + 盘上文件）。

    取舍说明：上一版曾改成"软删保留历史"（怕旧 run 取不回自己的文件），
    但用户口径明确 —— **旧版本文件产生的数据是无效数据，后续不会再用到**。
    现在换文件走"新 run 清旧 blocks"（``purge_blocks_except_run``），
    旧材料不再有被旧 run 取回的需要，所以这里真删，不留垃圾。
    """
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


@router.post("/materials/{material_id}/reparse")
async def reparse_material(material_id: int):
    from services.block_store import invalidate_material_packages
    async with get_db() as db:
        row = await (await db.execute("SELECT * FROM materials WHERE id=?", (material_id,))).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="材料不存在")
        if row["parse_status"] in ("pending", "parsing"):
            raise HTTPException(status_code=409, detail="文件正在解析，请稍候")
        await invalidate_material_packages(db, material_id)
        await db.execute("DELETE FROM material_chunks WHERE material_id=?", (material_id,))
        await update_material_parse_status(db, material_id, "pending")
    task = _asyncio.create_task(_parse_and_chunk_in_background(
        material_id, str(_abs_upload_path(row["file_path"])),
        Path(row["filename"]).suffix.lower(), row["filename"], row["role"],
    ))
    _PARSE_TASKS.add(task)
    task.add_done_callback(_PARSE_TASKS.discard)
    return {"ok": True}


@router.patch("/materials/{material_id}")
async def update_material_route(material_id: int, body: dict):
    """更新材料属性：当前仅支持 role 修改（spec/main_rfp/scoring/evaluation/source）"""
    from services.block_store import update_material_role
    new_role = (body or {}).get("role")
    allowed = ALLOWED_ROLES
    if not new_role or new_role not in allowed:
        raise HTTPException(status_code=400, detail=f"非法 role：{new_role!r}")
    async with get_db() as db:
        from services.block_store import invalidate_material_packages
        await invalidate_material_packages(db, material_id)
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


# ── 标包分析 ──────────────────────────────────────────────────────────────────
# 用户口径：「从招标文件里面去找到标的对应的标包或标段……以招标文件的事实为准，
# 模型不能去做额外的衍生或者提炼」+「一个文件可能会属于多个标包」。
@router.get("/projects/{project_id}/package-analysis")
async def get_package_analysis(project_id: int, refresh: int = 0, use_llm: int = 0):
    """标包清单（含**原文出处**）+ 文件↔标包的多对多关联。

    - 默认返回已落库结果；库里为空、或 ``refresh=1`` → 重新分析并覆盖
    - **没有解析出正文（chunks 为空）时返回空清单 + 一句原因**，不猜、不编造
    - ``package_name`` 允许为空：名字要用户确认，本接口不生成
    """
    from services.package_analysis import (
        load_analysis, load_material_packages, run_analysis,
    )

    # ?use_llm=1 时让模型**只做合并/命名**（结果经溯源校验，编造的丢弃）
    call_model = None
    if use_llm:
        try:
            from infra.llm import dispatch_package_merge_json
            from services.config_store import config_store
            _cfg, _rr = config_store.get_configs_and_next_index()

            async def call_model(payload, _c=_cfg, _r=_rr):
                return await dispatch_package_merge_json(_c, _r, payload)
        except Exception as e:      # 没配置模型就照旧走规则，不报错
            logger.info(f"标包分析：模型整理不可用（{e}），按规则结果返回")

    async with get_db() as db:
        packages = await load_analysis(db, project_id)
        note = ""
        if refresh or not packages:
            packages, note = await run_analysis(db, project_id, use_llm=bool(use_llm),
                                                call_model=call_model)
        links = await load_material_packages(db, project_id)
        chunk_n = (await (await db.execute(
            """SELECT COUNT(*) FROM material_chunks c JOIN materials m ON m.id = c.material_id
                WHERE m.project_id = ? AND m.deleted_at IS NULL""", (project_id,))).fetchone())[0]

    if note:
        pass                      # 模型整理已给出说明，保留它
    elif not chunk_n:
        note = "还没有解析出的正文，无法从招标文件里检索标包；请先上传并等待解析完成。"
    elif not packages:
        note = ("已检索全部正文，但**没有找到**「标的 / 标包 / 标段」的表述 —— "
                "按事实为准，这里不会猜测标包。")
    return JSONResponse({
        "packages": packages,
        "material_packages": links,     # {material_id: [{target, package_no}]}
        "chunks": chunk_n,
        "note": note,
    })


@router.post("/projects/{project_id}/sync-points")
async def sync_points(project_id: int, payload: dict | None = None):
    """**一键对齐**：把"解析出来的评分点"按已确认的范围/类别落库（**不调用模型**）。

    用途：界面第①步显示的是解析事实，管线用的是 `review_points`。两者不一致时
    （例如当次运行的类别选择与现在不同），用户在这里一次对齐，不必重跑拆分。
    """
    from routes.workflow import get_runner
    from services.point_sync import sync_points_from_parse
    from services.split_report import collect_parsed_points

    body = payload or {}
    thread_id = str(body.get("threadId") or body.get("thread_id") or "")
    if not thread_id:
        raise HTTPException(status_code=400, detail="缺少 threadId")

    runner = get_runner()
    state = await runner.state(thread_id)
    if not state:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    spec = state.get("spec") or {}
    # 用**全部**评分表的评分点（不是"选中的那一张"）——对齐要覆盖解析到的全部事实
    from services.split_report import collect_all_parsed_points
    parsed = collect_all_parsed_points(spec, body or {})
    if not parsed:
        return JSONResponse({"synced": 0, "note": "解析事实里没有评分点，无从对齐。"})

    async with get_db() as db:
        # 范围与类别：优先用请求里给的，其次用 state 里的 answers
        answers = await (await db.execute(
            """SELECT key, value FROM split_answers WHERE project_id = ? AND thread_id = ?""",
            (project_id, thread_id))).fetchall()
        ans = {}
        for k, v in answers:
            try:
                ans[k] = _json.loads(v) if v else {}
            except Exception:
                ans[k] = {"raw": v}
        scope = (body.get("scope") or ans.get("scope") or {}) or {}
        cats = (body.get("categories") or (ans.get("categories") or {}).get("categories") or [])
        package_no = str((scope or {}).get("package_no") or (body.get("packageNo") or ""))
        if not package_no:
            # 兜底：**运行的归属**才是权威（本会话新加的 workflow_runs.target/package_no）。
            # 老运行没保存过 scope 答案，若只读 answers 会退化成"不过滤"→ 把别的标包的
            # 评分点也写进这条运行（实测发生过：一次写了 37 条，含标包 1/3）。
            row = await (await db.execute(
                "SELECT package_no FROM workflow_runs WHERE thread_id = ?", (thread_id,))).fetchone()
            if row and row[0]:
                package_no = str(row[0])
        result = await sync_points_from_parse(
            db, project_id, thread_id, parsed,
            package_no=package_no, categories=cats,
        )
    return JSONResponse(result)


@router.put("/projects/{project_id}/materials/{material_id}/packages")
async def set_material_packages_route(project_id: int, material_id: int, payload: dict | None = None):
    """设置某份文件归属的标包（**多对多**：可以一个都不选，也可以选多个）。

    用户口径：「一个文件可能会属于多个标包，这块没有严格的对应关系」。
    """
    from services.package_analysis import set_material_packages

    body = payload or {}
    packages = body.get("packages") or []
    if not isinstance(packages, list):
        raise HTTPException(status_code=400, detail="packages 必须是数组")
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM materials WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
            (material_id, project_id))).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="材料不存在")
        n = await set_material_packages(db, project_id, material_id, packages)
    return JSONResponse({"material_id": material_id, "packages": n})


@router.get("/projects/{project_id}/materials/integrity")
async def materials_integrity(project_id: int):
    """素材文件**丢失检测**：记录在库里、但磁盘上的文件不见了（或为 0 字节）。

    这类问题不显式暴露的话，界面一切正常，直到真正要用原文时才发现素材是空的。
    """
    from services.material_integrity import check_materials

    async with get_db() as db:
        rows = await (await db.execute(
            """SELECT id, filename, role, file_path, parse_status
                 FROM materials WHERE project_id = ? AND deleted_at IS NULL
                ORDER BY id""", (project_id,))).fetchall()
        counts = {str(c[0]): c[1] for c in await (await db.execute(
            """SELECT m.id, COUNT(c.id) FROM materials m
                 LEFT JOIN material_chunks c ON c.material_id = m.id
                WHERE m.project_id = ? AND m.deleted_at IS NULL
                GROUP BY m.id""", (project_id,))).fetchall()}
    mats = [{"id": r[0], "filename": r[1], "role": r[2], "file_path": r[3],
             "parse_status": r[4]} for r in rows]
    result = check_materials(mats, chunk_counts=counts)
    return JSONResponse(result)
