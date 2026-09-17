"""LLM 调用用量上报。

``clients.py`` 是纯 I/O 层，不依赖数据库。用量通过可注入的 sink 上报：
应用启动时装上落库的 sink（见 ``services/usage_store.py`` 与 ``main.py``），
测试与未装配场景下默认丢弃，不产生副作用。

记账是旁路：任何失败都只记日志，绝不影响 LLM 调用本身。
"""

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

Sink = Callable[[dict], Awaitable[None]]

_sink: Optional[Sink] = None


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
