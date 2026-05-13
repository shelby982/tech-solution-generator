"""
文件上传与文档解析路由

改造为两步式异步流程：
  POST /api/upload          → 接收文件，立即返回 { parse_job_id }
  GET  /api/parse/stream/{id} → SSE 实时推送解析进度，最终推送完整目录

这样前端不再阻塞等待：上传完成即可开始监听进度事件，给用户实时反馈。
"""

import io
import uuid
import asyncio
import threading
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from services.parser import parse_document
from utils.validators import validate_uuid
from utils.sse import (
    sse_parse_start, sse_parse_progress, sse_parse_section,
    sse_parse_done, sse_error,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["upload"])

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB


# ─────────────────────────────────────────────
# 解析任务状态存储（内存，轻量级）
# ─────────────────────────────────────────────

class ParseJobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    ERROR     = "error"


@dataclass
class ParseJob:
    """一次文档解析任务的状态"""
    job_id: str
    filename: str
    file_data: bytes
    suffix: str
    status: ParseJobStatus = ParseJobStatus.PENDING
    error_msg: str = ""
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    result: Optional[dict] = None


class ParseJobStore:
    """轻量级内存存储，保留最近 50 个解析任务"""
    MAX_JOBS = 50

    def __init__(self):
        self._jobs: dict[str, ParseJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def create(self, filename: str, file_data: bytes, suffix: str) -> ParseJob:
        job_id = str(uuid.uuid4())
        job = ParseJob(job_id=job_id, filename=filename, file_data=file_data, suffix=suffix)
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._evict()
        return job

    def get(self, job_id: str) -> Optional[ParseJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def _evict(self):
        while len(self._order) > self.MAX_JOBS:
            old_id = self._order.pop(0)
            old_job = self._jobs.pop(old_id, None)
            if old_job:
                old_job.file_data = b""
                old_job.result = None


parse_job_store = ParseJobStore()


# ─────────────────────────────────────────────
# 路由 1：POST /api/upload — 接收文件，立即返回 job_id
# ─────────────────────────────────────────────

@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传技术规范书（PDF / DOCX / DOC）。
    文件验证通过后立即返回 parse_job_id，解析在后台进行。

    返回格式：
    {
        "parse_job_id": "uuid",
        "filename": "原始文件名",
        "stream_url": "/api/parse/stream/{parse_job_id}"
    }
    """
    # ── 1. 文件名校验 ───────────────────────────
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式「{suffix}」，请上传 PDF 或 DOCX 文件",
        )

    # ── 2. 文件大小校验 ─────────────────────────
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content) // 1024 // 1024} MB），请上传 50 MB 以内的文件",
        )
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # ── 3. 创建解析任务（文件内容保留在内存中）──
    logger.info(f"文件头部字节：{content[:8].hex()}，大小={len(content)} bytes")
    job = parse_job_store.create(filename=file.filename, file_data=content, suffix=suffix)
    logger.info(f"文件已接收：{file.filename}，job_id={job.job_id}，大小={len(content)} bytes")

    return JSONResponse({
        "parse_job_id": job.job_id,
        "filename": file.filename,
        "stream_url": f"/api/parse/stream/{job.job_id}",
    })


# ─────────────────────────────────────────────
# 路由 2：GET /api/parse/stream/{job_id} — SSE 进度流
# ─────────────────────────────────────────────

@router.get("/parse/stream/{job_id}")
async def parse_stream(job_id: str):
    """
    SSE 端点，实时推送文档解析进度。

    事件类型：
      parse_start    — 开始解析
      parse_progress — 阶段进度（页面扫描、格式转换等）
      parse_section  — 扫描到一个章节标题
      parse_done     — 解析完成，携带完整目录
      error          — 解析失败
    """
    validate_uuid(job_id, "job_id")
    job = parse_job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"解析任务不存在：{job_id}")

    # 如果已经完成，直接推送结果后关闭
    if job.status == ParseJobStatus.DONE and job.result is not None:
        async def _replay():
            yield sse_parse_done(**job.result)
        return StreamingResponse(_replay(), media_type="text/event-stream")

    if job.status == ParseJobStatus.ERROR:
        async def _err():
            yield sse_error(job.error_msg)
        return StreamingResponse(_err(), media_type="text/event-stream")

    # ── 启动后台解析线程 ────────────────────────
    # 每次 SSE 连接创建一个新 Queue（避免多客户端干扰）
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    job.event_queue = queue

    if job.status == ParseJobStatus.PENDING:
        job.status = ParseJobStatus.RUNNING
        thread = threading.Thread(
            target=_run_parse_in_thread,
            args=(job, loop),
            daemon=True,
        )
        thread.start()

    # ── SSE 生成器 ──────────────────────────────
    async def event_generator():
        try:
            while True:
                try:
                    event_str: str = await asyncio.wait_for(queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    # 保活心跳
                    yield ": ping\n\n"
                    continue

                yield event_str

                # parse_done 或 error 是终止信号
                # SSE 格式：event: parse_done\ndata: {...}\n\n
                # 必须匹配 "event: " 前缀，不能用带引号的 '"parse_done"'
                if event_str.startswith('event: parse_done') or event_str.startswith('event: error'):
                    break
        except asyncio.CancelledError:
            logger.info(f"SSE 客户端断开：job_id={job_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────
# 后台解析线程
# ─────────────────────────────────────────────

def _run_parse_in_thread(job: ParseJob, loop: asyncio.AbstractEventLoop):
    """
    在独立线程中运行文档解析，通过 asyncio.Queue 将 SSE 事件传回主线程。
    """
    def emit(event_str: str):
        """线程安全地将事件放入 asyncio Queue"""
        asyncio.run_coroutine_threadsafe(job.event_queue.put(event_str), loop)

    def progress_callback(stage: str, current: int, total: int):
        """解析器进度回调 → 转发为 SSE 事件"""
        if current > 0 and total > 0:
            emit(sse_parse_section(stage, current, total))
        else:
            emit(sse_parse_progress(stage, current, total))

    try:
        emit(sse_parse_start(job.filename))

        file_obj = io.BytesIO(job.file_data)
        logger.info(f"解析开始：BytesIO 大小={len(job.file_data)}，头部={job.file_data[:8].hex()}")
        parsed = parse_document(file_obj, job.suffix, job.filename, progress_callback=progress_callback)
        job.file_data = b""

        if not parsed.sections:
            raise ValueError(
                "未能从文档中识别出任何章节。"
                "请确保文档使用了标准 Heading 样式（DOCX）或包含可识别的编号标题（PDF）。"
            )

        result_payload = {
            "doc_id": parsed.doc_id,
            "title": parsed.title,
            "section_count": len(parsed.sections),
            "sections": [s.to_dict() for s in parsed.sections],
        }

        # 缓存结果（供断线重连）
        job.result = result_payload
        job.status = ParseJobStatus.DONE

        emit(sse_parse_done(**result_payload))
        logger.info(f"解析完成：job_id={job.job_id}，章节数={len(parsed.sections)}")

    except Exception as e:
        job.status = ParseJobStatus.ERROR
        job.error_msg = str(e)
        emit(sse_error(str(e)))
        logger.exception(f"解析失败：job_id={job.job_id}，{e}")

    finally:
        job.file_data = b""


# ─────────────────────────────────────────────
# 辅助路由
# ─────────────────────────────────────────────

@router.get("/upload/supported-formats")
async def get_supported_formats():
    """返回支持的文件格式列表"""
    return {
        "formats": list(ALLOWED_EXTENSIONS),
        "max_size_mb": MAX_FILE_SIZE // 1024 // 1024,
        "description": "支持 PDF、DOCX 和旧版 DOC 格式的技术规范书",
    }
