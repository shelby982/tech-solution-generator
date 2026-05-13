"""
下载路由

GET /api/download/{task_id}       — Markdown 格式下载
GET /api/download/{task_id}/docx  — Word (.docx) 格式下载（新增）
GET /api/download/{task_id}/json  — JSON 格式（前端二次渲染用）
"""

import logging
from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from services.task_store import task_store, TaskStatus
from utils.validators import validate_uuid

logger = logging.getLogger(__name__)
router = APIRouter(tags=["download"])


# ─────────────────────────────────────────────
# 公共：获取任务并校验
# ─────────────────────────────────────────────

def _get_ready_task(task_id: str):
    validate_uuid(task_id, "task_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    if task.status == TaskStatus.PENDING:
        raise HTTPException(status_code=425, detail="任务尚未开始生成，请稍后再试")
    return task


def _content_disposition(filename: str) -> str:
    """生成符合 RFC 5987 的 Content-Disposition 首部值"""
    encoded = quote(filename, safe="")
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


# ─────────────────────────────────────────────
# Markdown 下载
# ─────────────────────────────────────────────

@router.get("/download/{task_id}")
async def download_markdown(task_id: str):
    """下载已生成方案（Markdown 格式）"""
    task = _get_ready_task(task_id)

    content = task.get_all_content()
    if not content:
        raise HTTPException(status_code=404, detail="暂无可下载内容，可能所有章节均未完成生成")

    filename = f"技术方案_{task_id[:8]}.md"
    logger.info(f"Markdown 下载：task_id={task_id}，{len(content)} 字符")

    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# ─────────────────────────────────────────────
# Word (.docx) 下载
# ─────────────────────────────────────────────

@router.get("/download/{task_id}/docx")
async def download_docx(task_id: str):
    """
    下载已生成方案（Word .docx 格式）。
    将各章节 Markdown 内容转换为格式规范的 Word 文档。
    """
    task = _get_ready_task(task_id)

    # 收集已完成章节数据（含 level / title / content）
    # done=True 且 content 为空的章节（被跳过）仍保留标题，确保目录结构完整
    sections_data = []
    for sec in task.sections:
        result = task.results.get(sec["id"])
        if result and result.done:
            sections_data.append({
                "id":      sec["id"],
                "title":   sec["title"],
                "level":   sec.get("level", 1),
                "content": result.content,
                "done":    True,
            })

    if not sections_data:
        raise HTTPException(status_code=404, detail="暂无可下载内容，可能所有章节均未完成生成")

    try:
        from services.docx_generator import sections_to_docx
        # 尝试用第一个章节的标题作文档名
        doc_title = sections_data[0]["title"] if sections_data else "技术方案"
        docx_bytes = sections_to_docx(sections_data, doc_title=doc_title)
    except Exception as e:
        logger.exception(f"DOCX 生成失败：{e}")
        raise HTTPException(status_code=500, detail=f"Word 文档生成失败：{str(e)}")

    filename = f"技术方案_{task_id[:8]}.docx"
    logger.info(f"DOCX 下载：task_id={task_id}，{len(docx_bytes)} bytes")

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# ─────────────────────────────────────────────
# JSON 下载（调试 / 二次渲染）
# ─────────────────────────────────────────────

@router.get("/download/{task_id}/json")
async def download_json(task_id: str):
    """以 JSON 格式返回每个章节的详细生成结果"""
    task = _get_ready_task(task_id)

    sections_data = []
    for sec in task.sections:
        result = task.results.get(sec["id"])
        sections_data.append({
            "section_id": sec["id"],
            "title":      sec["title"],
            "level":      sec.get("level", 1),
            "content":    result.content if result else "",
            "done":       result.done    if result else False,
        })

    return JSONResponse(content={
        "task_id":  task.task_id,
        "status":   task.status,
        "progress": round(task.progress, 2),
        "sections": sections_data,
    })
