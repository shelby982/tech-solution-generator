"""WorkflowRunner —— 路由层与 LangGraph 之间的薄外观层。

职责：
- 创建 / 续跑 / 取消 / 恢复一次工作流
- 维护 workflow_runs 表（thread_id ↔ project_id ↔ stage）
- 把 graph 推出的 SSE 事件 + checkpoint 推流给前端

设计原则（spec §4 分层规则）：
- routes 不直接持有 LangGraph compile 结果；通过 ``WorkflowRunner`` 间接调用
- runner 不知道 SSE 字面格式，只知道 ``EventEmitter`` 流
- runner 把 ``WorkflowCancelled`` 抓住后转 stage="aborted"

并发模型：每个 thread_id 对应一份长存活的 ``EventEmitter`` 与一个 ``asyncio.Task``。
``stream(tid)`` 把 emitter 的帧反向传给 SSE 端点；``abort(tid)`` 标记取消标志，
graph 在下一个节点开头读取 ``state.cancel_requested`` 抛异常 → runner 转 aborted。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver

from db import get_db
from domain.review import WorkflowRunRepository
from orchestrator import events
from orchestrator.checkpointer import checkpointer_from_default
from orchestrator.events import EventEmitter
from orchestrator.graph import GraphDeps, build_graph
from orchestrator.nodes import WorkflowCancelled
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 单 thread 运行时上下文
# ─────────────────────────────────────────────

class _Run:
    """一次工作流的运行时句柄：emitter + 后台 task + cancel flag。"""

    def __init__(self, thread_id: str, emitter: EventEmitter):
        self.thread_id = thread_id
        self.emitter = emitter
        self.task: Optional[asyncio.Task] = None
        self.cancel_flag = False


# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────

# CheckpointerProvider：用于在 start 时拿到 saver 上下文管理器；默认走
# ``checkpointer_from_default``，单测可注入内存 / tmp_path 文件 saver
CheckpointerProvider = Callable[[], "_AsyncCM[BaseCheckpointSaver]"]


class _AsyncCM:
    """轻量类型注解占位：实际用 asynccontextmanager 装饰的工厂即可。"""
    async def __aenter__(self) -> Any: ...  # pragma: no cover
    async def __aexit__(self, *a) -> Any: ...  # pragma: no cover


class WorkflowRunner:
    """工作流外观。

    使用方式：

        runner = WorkflowRunner(deps_factory=...)
        tid = await runner.start(project_id=1, config={"tone": "official"})
        async for frame in runner.stream(tid):
            yield frame  # → SSE
    """

    def __init__(
        self,
        *,
        deps_factory: Callable[[], GraphDeps],
        checkpointer_provider: Optional[CheckpointerProvider] = None,
    ):
        """
        deps_factory: 调用时返回 GraphDeps（含 5 agents + spec_loader）。
                      每次 start 调用一次（保证 agent 拿到最新 config_store 状态）。
        checkpointer_provider: 返回一个 ``async with`` 的 saver 上下文。
                               缺省用 ``checkpointer_from_default``。
        """
        self._deps_factory = deps_factory
        self._checkpointer_provider = checkpointer_provider or checkpointer_from_default
        self._runs: dict[str, _Run] = {}

    # ── 公共 API ──────────────────────────────

    async def start(self, project_id: int, config: dict) -> str:
        """启动新工作流，返回 thread_id。后台 task 跑到第一个闸门后等续跑指令。"""
        thread_id = str(uuid.uuid4())
        async with get_db() as db:
            repo = WorkflowRunRepository(db)
            await repo.create(thread_id, project_id, stage="idle")

        run = _Run(thread_id, EventEmitter())
        self._runs[thread_id] = run

        initial_state: WorkflowState = {
            "project_id": project_id,
            "thread_id": thread_id,
            "stage": "idle",
            "user_choice": "",
            "cancel_requested": False,
            "config": dict(config or {}),
            "errors": [],
        }

        run.task = asyncio.create_task(
            self._run_until_pause(run, initial_state),
            name=f"wf-{thread_id}",
        )
        return thread_id

    async def resume(
        self,
        thread_id: str,
        user_choice: str = "",
        edits: Optional[dict] = None,
    ) -> None:
        """闸门处续跑：写入 user_choice + edits（patch dict），从下一个 super-step 继续。"""
        run = self._get_run(thread_id)
        patch: dict = dict(edits or {})
        if user_choice:
            patch["user_choice"] = user_choice

        # 起一个新的后台 task 接力（前一个 task 已在闸门处 ainvoke 返回）
        run.task = asyncio.create_task(
            self._resume_until_pause(run, patch),
            name=f"wf-{thread_id}-resume",
        )

    async def regen(self, thread_id: str, block_ids: list[str]) -> None:
        """闸门 3 选回修：把 ids 写入 proposal.regenerate_targets，user_choice=regen_blocks。"""
        await self.resume(
            thread_id,
            user_choice="regen_blocks",
            edits={"proposal": {"regenerate_targets": list(block_ids)}},
        )

    async def abort(self, thread_id: str) -> None:
        """请求取消：设 cancel_requested。下一节点开头会抛 WorkflowCancelled。"""
        run = self._get_run(thread_id)
        run.cancel_flag = True
        # 直接走 update_state 而非 resume —— 避免 graph 已暂停在闸门时再 invoke
        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, run.emitter)
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"cancel_requested": True, "user_choice": "abort"},
            )
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(thread_id, "aborted")
        await run.emitter.emit(events.aborted("user_abort"))
        await run.emitter.aclose()

    async def recover(self, thread_id: str) -> None:
        """显式恢复：用现有 thread_id 继续跑（崩溃恢复用）。"""
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        run.task = asyncio.create_task(
            self._resume_until_pause(run, {}),
            name=f"wf-{thread_id}-recover",
        )

    async def state(self, thread_id: str) -> WorkflowState:
        """读 checkpoint 当前 state；若无 checkpoint 则返回空 dict。"""
        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, emitter=None)
            snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return dict(snap.values) if snap else {}

    async def stream(self, thread_id: str) -> AsyncIterator[str]:
        """订阅 SSE 帧流；emitter 关闭后退出。"""
        run = self._get_run(thread_id)
        async for frame in run.emitter.stream():
            yield frame

    # ── 内部：跑到下一个 pause 点 ────────────

    async def _run_until_pause(self, run: _Run, state: WorkflowState) -> None:
        """初次跑：从 START 到第一个闸门。"""
        try:
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter)
                await graph.ainvoke(
                    state,
                    config={"configurable": {"thread_id": run.thread_id}},
                )
            await self._sync_stage(run.thread_id, graph_done=False)
        except WorkflowCancelled:
            await self._mark_aborted(run)
        except Exception as e:
            logger.exception(f"runner: thread {run.thread_id} 异常")
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def _resume_until_pause(self, run: _Run, patch: dict) -> None:
        """续跑：先 update_state(patch) 再 ainvoke(None)，跑到下一个 pause。"""
        try:
            config = {"configurable": {"thread_id": run.thread_id}}
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter)
                if patch:
                    await graph.aupdate_state(config, patch)
                await graph.ainvoke(None, config=config)
                snap = await graph.aget_state(config)
                graph_done = snap.next == ()
            await self._sync_stage(run.thread_id, graph_done=graph_done)
            if graph_done:
                await run.emitter.emit(events.done(
                    f"/api/workflow/{run.thread_id}/download",
                ))
                await run.emitter.aclose()
        except WorkflowCancelled:
            await self._mark_aborted(run)
        except Exception as e:
            logger.exception(f"runner: thread {run.thread_id} resume 异常")
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def _mark_aborted(self, run: _Run) -> None:
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(run.thread_id, "aborted")
        await run.emitter.emit(events.aborted("user_cancel"))
        await run.emitter.aclose()

    async def _sync_stage(self, thread_id: str, *, graph_done: bool) -> None:
        """从 checkpoint 读最新 stage，更新 workflow_runs 表 + 推送 checkpoint 事件。"""
        state = await self.state(thread_id)
        stage = state.get("stage", "idle") if not graph_done else "done"
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(thread_id, stage)
        run = self._runs.get(thread_id)
        if run is not None:
            await run.emitter.emit(events.checkpoint(thread_id, stage))

    def has_run(self, thread_id: str) -> bool:
        """是否存在对应运行时句柄（routes 用于 stream 端点的 404 早判）。"""
        return thread_id in self._runs

    # ── helper ────────────────────────────────

    def _get_run(self, thread_id: str) -> _Run:
        run = self._runs.get(thread_id)
        if run is None:
            raise KeyError(f"未知 thread_id：{thread_id}")
        return run

    def _build(self, saver: BaseCheckpointSaver, emitter: Optional[EventEmitter]):
        deps = self._deps_factory()
        return build_graph(deps, emitter=emitter, checkpointer=saver)


__all__ = ["WorkflowRunner"]
