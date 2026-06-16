"""orchestrator/checkpointer.py 单测。

策略：跑一个最小 LangGraph，让 saver 落盘一份 checkpoint；关闭 saver、用同一文件
重新打开新的 saver，按 ``thread_id`` 读出 state，断言与写入时一致。
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from orchestrator.checkpointer import (
    checkpointer_from_default,
    checkpointer_from_path,
)


class _DemoState(TypedDict, total=False):
    counter: int
    note: str


def _bump(state: _DemoState) -> _DemoState:
    return {"counter": state.get("counter", 0) + 1, "note": "bumped"}


def _build_graph(saver):
    builder = StateGraph(_DemoState)
    builder.add_node("bump", _bump)
    builder.add_edge(START, "bump")
    builder.add_edge("bump", END)
    return builder.compile(checkpointer=saver)


async def _read_state(saver, thread_id: str) -> dict:
    """复刻 routes 层将来读 checkpoint 的方式。"""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await saver.aget_tuple(config)
    assert snapshot is not None, "未读到 checkpoint"
    return snapshot.checkpoint["channel_values"]


async def test_save_close_reopen_returns_same_state(tmp_path):
    """:memory: 跨连接不可用，因此必须用文件落盘。"""
    db_path = str(tmp_path / "wf.db")
    thread_id = "tid-roundtrip"
    config = {"configurable": {"thread_id": thread_id}}

    # 1) 第一次打开 saver，跑一步落盘
    async with checkpointer_from_path(db_path) as saver:
        graph = _build_graph(saver)
        result = await graph.ainvoke({"counter": 0}, config=config)
        assert result["counter"] == 1
        assert result["note"] == "bumped"

    # 2) 第二次打开同一文件，按 thread_id 读回
    async with checkpointer_from_path(db_path) as saver2:
        values = await _read_state(saver2, thread_id)
        assert values["counter"] == 1
        assert values["note"] == "bumped"


async def test_different_thread_ids_isolated(tmp_path):
    db_path = str(tmp_path / "wf.db")

    async with checkpointer_from_path(db_path) as saver:
        graph = _build_graph(saver)
        await graph.ainvoke({"counter": 10},
                            config={"configurable": {"thread_id": "tid-A"}})
        await graph.ainvoke({"counter": 100},
                            config={"configurable": {"thread_id": "tid-B"}})

    async with checkpointer_from_path(db_path) as saver2:
        a = await _read_state(saver2, "tid-A")
        b = await _read_state(saver2, "tid-B")
        assert a["counter"] == 11
        assert b["counter"] == 101


async def test_checkpointer_from_default_uses_db_path(monkeypatch, tmp_path):
    """``checkpointer_from_default`` 应当复用 ``db.DB_PATH``。"""
    db_path = str(tmp_path / "default.db")
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    # 同时刷新 orchestrator.checkpointer 内已绑定的 DB_PATH 引用
    import orchestrator.checkpointer as ckpt_module

    monkeypatch.setattr(ckpt_module, "DB_PATH", db_path)

    thread_id = "tid-default"
    config = {"configurable": {"thread_id": thread_id}}

    async with checkpointer_from_default() as saver:
        graph = _build_graph(saver)
        await graph.ainvoke({"counter": 0}, config=config)

    # 直接以路径打开同一文件应能读到刚才的写入
    async with checkpointer_from_path(db_path) as saver2:
        values = await _read_state(saver2, thread_id)
        assert values["counter"] == 1
