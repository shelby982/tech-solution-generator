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
