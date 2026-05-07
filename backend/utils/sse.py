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
