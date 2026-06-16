"""LangGraph SQLite checkpointer 封装（spec §7 持久化）。

LangGraph ``AsyncSqliteSaver`` 与 LangGraph 的 ``StateGraph.compile(checkpointer=...)``
配合，把每个 super-step 后的 state 落盘到 SQLite。我们复用 ``backend/db.py`` 的
``DB_PATH``，把工作流 checkpoint 与业务表放在同一份 ``ge_solution.db`` 里 —
LangGraph 自管它的 ``langgraph_*`` 表，不会和现有 schema 冲突。

提供两个入口：

- ``checkpointer_from_default()`` —— 异步上下文管理器，按 ``db.DB_PATH`` 打开 saver
- ``checkpointer_from_path(path)`` —— 同上，但允许显式传入路径（测试用）

典型用法：

    from orchestrator import checkpointer

    async with checkpointer.checkpointer_from_default() as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke(state, config={"configurable": {"thread_id": tid}})
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from db import DB_PATH


@asynccontextmanager
async def checkpointer_from_path(path: str) -> AsyncIterator[AsyncSqliteSaver]:
    """按显式路径打开 ``AsyncSqliteSaver``。

    路径会作为 ``aiosqlite`` 连接串使用；``":memory:"`` 仅在同一连接生命期内有效，
    适合单测但不适合跨进程续跑。
    """
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        yield saver


@asynccontextmanager
async def checkpointer_from_default() -> AsyncIterator[AsyncSqliteSaver]:
    """复用 ``db.DB_PATH`` 打开 saver，与业务表共库。"""
    async with checkpointer_from_path(DB_PATH) as saver:
        yield saver


__all__ = ["checkpointer_from_default", "checkpointer_from_path"]
