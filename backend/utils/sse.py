"""
SSE（Server-Sent Events）事件格式化工具
"""

import json
from typing import Any


def format_sse_event(event: str, data: Any) -> str:
    """
    格式化 SSE 事件消息。

    Args:
        event: 事件类型，如 'token', 'section_start', 'section_done', 'all_done', 'error'
        data: 事件数据（将被 JSON 序列化）

    Returns:
        符合 SSE 规范的字符串
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def sse_token(text: str) -> str:
    return format_sse_event("token", {"text": text})


def sse_section_start(section_id: str, title: str) -> str:
    return format_sse_event("section_start", {"section_id": section_id, "title": title})


def sse_section_done(section_id: str, content: str, progress: float) -> str:
    return format_sse_event("section_done", {
        "section_id": section_id,
        "content": content,
        "progress": round(progress, 2),
    })


def sse_all_done(task_id: str) -> str:
    return format_sse_event("all_done", {
        "task_id": task_id,
        "download_url": f"/api/download/{task_id}",
    })


def sse_error(message: str) -> str:
    return format_sse_event("error", {"message": message})


def sse_doc_summary(summary: str) -> str:
    """文档摘要事件：生成开始前先推送整体项目摘要"""
    return format_sse_event("doc_summary", {"summary": summary})


def sse_section_skip(section_id: str, title: str, progress: float) -> str:
    """章节跳过事件：原文为空，不调用 LLM，直接跳过"""
    return format_sse_event("section_skip", {
        "section_id": section_id,
        "title": title,
        "progress": round(progress, 2),
    })


# ── 文档解析进度事件 ──────────────────────────

def sse_parse_start(filename: str) -> str:
    """解析开始：告知前端文件已接收，开始处理"""
    return format_sse_event("parse_start", {"filename": filename})


def sse_parse_progress(stage: str, current: int, total: int) -> str:
    """
    解析进度更新。
    stage:   当前阶段描述，如"扫描第 3/70 页"或章节标题
    current: 当前进度值（-1 表示不定进度）
    total:   总量（-1 表示不定进度）
    """
    return format_sse_event("parse_progress", {
        "stage": stage,
        "current": current,
        "total": total,
        "percent": round(current / total * 100) if total > 0 else -1,
    })


def sse_parse_section(title: str, current: int, total: int) -> str:
    """扫描到一个新章节标题"""
    return format_sse_event("parse_section", {
        "title": title,
        "current": current,
        "total": total,
    })


def sse_parse_done(doc_id: str, title: str, section_count: int, sections: list) -> str:
    """解析全部完成，携带完整目录数据"""
    return format_sse_event("parse_done", {
        "doc_id": doc_id,
        "title": title,
        "section_count": section_count,
        "sections": sections,
    })
