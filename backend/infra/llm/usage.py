"""LLM 调用用量上报 + 运行归属上下文。

``clients.py`` 是纯 I/O 层，不依赖数据库。用量通过可注入的 sink 上报：
应用启动时装上落库的 sink（见 ``services/usage_store.py`` 与 ``main.py``），
测试与未装配场景下默认丢弃，不产生副作用。

记账是旁路：任何失败都只记日志，绝不影响 LLM 调用本身。

**运行归属**：``llm_usage`` 原先只有 call_site/model/tokens，回答不了「这次运行
花了多少」——同一个 call_site 混着多个项目、多轮运行。这里用 contextvar 携带
当前运行的 ``project_id`` / ``thread_id``（由 runner 在 invoke 图之前设置，随
await 链自然传播），``clients._emit_usage`` 读取后落库。未设置时写 NULL，
语义是「不属于任何 workflow run」（脚本、测试直调）。
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

Sink = Callable[[dict], Awaitable[None]]

_sink: Optional[Sink] = None

# 当前运行上下文：{"project_id": int | None, "thread_id": str | None}
_run_ctx: ContextVar[dict] = ContextVar("llm_run_context", default={})


def current_run() -> dict:
    """当前运行上下文；未设置时返回空 dict（落库写 NULL）。"""
    return _run_ctx.get()


@contextmanager
def run_context(*, project_id=None, thread_id=None) -> Iterator[None]:
    """with 块内把后续 LLM 调用归属到某次运行。嵌套时内层覆盖外层。"""
    token = _run_ctx.set({"project_id": project_id, "thread_id": thread_id})
    try:
        yield
    finally:
        _run_ctx.reset(token)


def set_sink(sink: Optional[Sink]) -> None:
    """装配用量 sink；传 None 表示卸载（测试用）。"""
    global _sink
    _sink = sink


def get_sink() -> Optional[Sink]:
    return _sink


async def emit(record: dict) -> None:
    """上报一条用量。

    未装配 sink 时静默丢弃（默认行为）。sink 抛异常只记日志 —— 记账失败
    不能让一次成功的模型调用变成失败。
    """
    sink = _sink
    if sink is None:
        return
    try:
        await sink(record)
    except Exception as e:
        logger.warning(f"LLM 用量记账失败（{e}），已忽略")


# 当前调用的更细归属：{"block_id": int|None, "section": str, "role": str}
_call_ctx: ContextVar[dict] = ContextVar("llm_call_context", default={})


def current_call() -> dict:
    return _call_ctx.get()


@contextmanager
def call_context(*, block_id=None, section="", role="") -> Iterator[None]:
    """with 块内把后续 LLM 调用归属到"某章某节的某个角色"（留痕用）。"""
    token = _call_ctx.set({"block_id": block_id, "section": section, "role": role})
    try:
        yield
    finally:
        _call_ctx.reset(token)
