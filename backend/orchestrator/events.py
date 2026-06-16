"""orchestrator SSE 事件协议（spec §7）。

LangGraph 节点向前端推事件的统一入口。两层：
- 14 个事件构造函数 —— 把节点内的状态变更格式化为 SSE 文本帧
- ``EventEmitter`` —— 节点持有的异步队列代理，节点 ``await emitter.emit(...)``
  把事件帧塞进队列；routes 层 ``async for frame in emitter.stream():`` 从队列读
  出后直接 yield 给 FastAPI ``StreamingResponse``

事件载荷字段以 spec §7 事件表为准；本模块不引入 LangGraph 依赖，纯 dict 进出。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from utils.sse import format_sse_event


# ─────────────────────────────────────────────
# 事件构造函数（spec §7 表，14 种）
# ─────────────────────────────────────────────

def stage_change(stage: str, thread_id: str) -> str:
    """工作流阶段切换。"""
    return format_sse_event("stage_change", {"stage": stage, "thread_id": thread_id})


def parse_progress(step: str, current: int, total: int) -> str:
    """张衡 parse 阶段进度。"""
    return format_sse_event("parse_progress", {
        "step": step,
        "current": current,
        "total": total,
    })


def outline_extract(block_id: str, title: str, matrix: dict[str, Any]) -> str:
    """张衡 extract 阶段：每提炼完一章节推一次。"""
    return format_sse_event("outline_extract", {
        "block_id": block_id,
        "title": title,
        "matrix": matrix,
    })


def match_progress(block_id: str, matches: list[dict[str, Any]]) -> str:
    """沈括：单 block 匹配结果。"""
    return format_sse_event("match_progress", {
        "block_id": block_id,
        "matches": matches,
    })


def block_start(block_id: str, kind: str, title: str) -> str:
    """诸葛亮：开始生成一个 block。"""
    return format_sse_event("block_start", {
        "block_id": block_id,
        "kind": kind,
        "title": title,
    })


def block_token(block_id: str, token: str) -> str:
    """诸葛亮：流式 token。"""
    return format_sse_event("block_token", {"block_id": block_id, "token": token})


def block_done(
    block_id: str,
    content: str,
    sources: list[Any],
    outline: dict[str, Any],
) -> str:
    """诸葛亮：单 block 生成完成。"""
    return format_sse_event("block_done", {
        "block_id": block_id,
        "content": content,
        "sources": sources,
        "outline": outline,
    })


def review_finding(
    block_id: str,
    agent: str,
    score: float,
    issues: list[Any],
) -> str:
    """王安石/包拯：单 block 评审结果。"""
    return format_sse_event("review_finding", {
        "block_id": block_id,
        "agent": agent,
        "score": score,
        "issues": issues,
    })


def report_ready(report: dict[str, Any]) -> str:
    """评审汇总节点：全局报告生成完成。"""
    return format_sse_event("report_ready", {"report": report})


def gate_open(gate: str, snapshot: dict[str, Any]) -> str:
    """到达人工闸门：携带当前 state 快照供前端渲染编辑器。"""
    return format_sse_event("gate_open", {"gate": gate, "snapshot": snapshot})


def error(
    message: str,
    *,
    agent: str | None = None,
    block_id: str | None = None,
    retryable: bool = False,
) -> str:
    """任意节点抛错。agent / block_id 可选。"""
    payload: dict[str, Any] = {"message": message, "retryable": retryable}
    if agent is not None:
        payload["agent"] = agent
    if block_id is not None:
        payload["block_id"] = block_id
    return format_sse_event("error", payload)


def checkpoint(thread_id: str, stage: str) -> str:
    """LangGraph 完成一次 super-step 持久化时推送。"""
    return format_sse_event("checkpoint", {
        "thread_id": thread_id,
        "stage": stage,
    })


def done(download_url: str) -> str:
    """END 节点：工作流走完，给前端下载链接。"""
    return format_sse_event("done", {"download_url": download_url})


def aborted(reason: str) -> str:
    """ABORT 节点：被用户取消或不可恢复错误终止。"""
    return format_sse_event("aborted", {"reason": reason})


# ─────────────────────────────────────────────
# EventEmitter：节点 → routes 的异步事件管道
# ─────────────────────────────────────────────

# 流终结哨兵（None 不能用，外部可能合法传 None）
_END = object()


class EventEmitter:
    """节点内向上发事件的回调对象，基于 ``asyncio.Queue``。

    生命周期：
    1. routes 层创建一份 emitter，放进 LangGraph config / 上下文
    2. 节点调用 ``await emitter.emit(events.block_token(...))`` 推帧
    3. routes 层 ``async for frame in emitter.stream():`` 读帧 yield 给 SSE
    4. graph 跑完或异常时，routes 层 ``await emitter.aclose()`` 关闭流

    ``maxsize=0`` 表示无界；如需背压可在构造时显式指定。
    """

    def __init__(self, *, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    async def emit(self, frame: str) -> None:
        """节点端：把已格式化的 SSE 帧塞进队列。

        关闭后再 emit 会被静默丢弃（节点不需要关心 routes 是否已断开）。
        """
        if self._closed:
            return
        await self._queue.put(frame)

    async def aclose(self) -> None:
        """关闭流：通知 ``stream()`` 的消费者结束循环。幂等。"""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(_END)

    async def stream(self) -> AsyncIterator[str]:
        """routes 端：消费帧直到 emitter 关闭。"""
        while True:
            frame = await self._queue.get()
            if frame is _END:
                return
            yield frame


__all__ = [
    # 事件构造函数
    "stage_change",
    "parse_progress",
    "outline_extract",
    "match_progress",
    "block_start",
    "block_token",
    "block_done",
    "review_finding",
    "report_ready",
    "gate_open",
    "error",
    "checkpoint",
    "done",
    "aborted",
    # 异步管道
    "EventEmitter",
]
