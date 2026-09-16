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


def outline_draft_start(revision: int) -> str:
    """张衡 outline_draft 阶段开始：模型正在派生应答文件目录。"""
    return format_sse_event("outline_draft_start", {"revision": revision})


def outline_draft(
    toc: list[dict[str, Any]],
    revision: int,
    degraded: bool = False,
    error: str = "",
) -> str:
    """张衡 outline_draft 阶段：应答目录已确定（一次推全量）。

    目录是整体替换的，不存在逐条增量，所以一次性推 toc 全量。
    degraded=True 表示模型没能派生、沿用了规范书原始目录，error 写明原因。
    """
    return format_sse_event("outline_draft", {
        "toc": toc,
        "revision": revision,
        "degraded": degraded,
        "error": error,
    })


def outline_extract(block_id: str, title: str, matrix: dict[str, Any]) -> str:
    """张衡 extract 阶段：每提炼完一章节推一次。"""
    return format_sse_event("outline_extract", {
        "block_id": block_id,
        "title": title,
        "matrix": matrix,
    })


def outline_extract_start(block_id: str, title: str) -> str:
    """张衡 extract 阶段：开始处理一个章节（worker 拿到并发槽 + 开始调 LLM）。

    前端用它把对应卡片切到"提炼中"骨架屏，避免用户在并发等待期看不到反馈。
    """
    return format_sse_event("outline_extract_start", {
        "block_id": block_id,
        "title": title,
    })


def match_progress(block_id: str, matches: list[dict[str, Any]]) -> str:
    """沈括：单 block 匹配结果。"""
    return format_sse_event("match_progress", {
        "block_id": block_id,
        "matches": matches,
    })


def match_start(block_id: str, title: str) -> str:
    """沈括：开始匹配某章节（前端用它把卡片切到"匹配中"骨架屏）。"""
    return format_sse_event("match_start", {
        "block_id": block_id,
        "title": title,
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


def review_block_start(block_id: str, agent: str, title: str = "") -> str:
    """王安石/包拯：开始评审某 block（前端用以显示"正在评审"状态）。"""
    return format_sse_event("review_block_start", {
        "block_id": block_id,
        "agent": agent,
        "title": title,
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


def paused(reason: str, generated: int, total: int) -> str:
    """生成阶段用户主动暂停：保留已生成的章节内容，graph 状态停在 generate 节点之前。

    前端据此显示"暂停态"提示卡，用户可选择继续生成 / 仅评审已生成 / 放弃。
    """
    return format_sse_event("paused", {
        "reason": reason,
        "generated": generated,
        "total": total,
    })


# ─────────────────────────────────────────────
# 协同闭环事件（spec §10）
# ─────────────────────────────────────────────

def iteration_start(iteration: int) -> str:
    """进入第 N 轮迭代（collect_gaps 发出）。"""
    return format_sse_event("iteration_start", {"iteration": int(iteration)})


def gaps_collecting(block_id: str, query: str) -> str:
    """正在为某 block 检索补充素材。"""
    return format_sse_event("gaps_collecting", {
        "block_id": block_id,
        "query": query,
    })


def gaps_done(block_id: str, new_matches: int, total_matches: int) -> str:
    """某 block 补料完成。"""
    return format_sse_event("gaps_done", {
        "block_id": block_id,
        "new_matches": int(new_matches),
        "total_matches": int(total_matches),
    })


def convergence(payload: dict[str, Any]) -> str:
    """收敛判定结果。"""
    return format_sse_event("convergence", dict(payload))


def feedback_ready(payload: dict[str, Any]) -> str:
    """修订指令与补料请求已生成。"""
    return format_sse_event("feedback_ready", dict(payload))


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
    "outline_extract_start",
    "match_progress",
    "match_start",
    "block_start",
    "block_token",
    "block_done",
    "review_finding",
    "review_block_start",
    "report_ready",
    "gate_open",
    "error",
    "checkpoint",
    "done",
    "aborted",
    "paused",
    "iteration_start",
    "gaps_collecting",
    "gaps_done",
    "convergence",
    "feedback_ready",
    # 异步管道
    "EventEmitter",
]
