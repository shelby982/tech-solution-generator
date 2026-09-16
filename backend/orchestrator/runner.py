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
        self._workspace_locks: dict[str, asyncio.Lock] = {}

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
        current = await self.state(thread_id)
        if not current or not current.get("project_id"):
            raise KeyError(thread_id)
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        patch: dict = dict(edits or {})
        if current.get("stage") == "report_review" and user_choice == "approve":
            patch["stage"] = "done"
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

    async def workspace_action(self, thread_id: str, block_ids: list[str], action: str) -> None:
        """对用户选择的章节修订或仅复审；可从已结束/重启后的 checkpoint 再进入。"""
        from orchestrator.graph import GATE_MATERIALS, NODE_GENERATE
        from services.block_store import list_blocks, list_chunks_with_filename

        lock = self._workspace_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            run = self._runs.get(thread_id)
            if run and run.task and not run.task.done():
                raise ValueError("当前任务仍在运行，请等待完成后再操作")
            state = await self.state(thread_id)
            if not state or not state.get("project_id"):
                raise KeyError(thread_id)
            if state.get("cancel_requested") or state.get("stage") == "aborted":
                raise ValueError("该工作流已作废，请创建新的工作流")
            if state.get("stage") not in {"report_review", "done", "paused", "materials_review"}:
                raise ValueError("请先完成大纲和素材确认，或等待当前运行结束")
            if action not in {"review", "revise"}:
                raise ValueError("不支持的章节操作")
            ids = list(dict.fromkeys(block_ids))
            matrix = (state.get("spec") or {}).get("outline_matrix") or {}
            if not ids or any(bid not in matrix for bid in ids):
                raise ValueError("请选择当前工作流中的有效章节")
            async with get_db() as db:
                rows = await list_blocks(db, state["project_id"])
                chunks = await list_chunks_with_filename(db, state["project_id"])
            db_map = {b["block_id"]: b for b in rows}
            proposal = state.get("proposal") or {}
            blocks = dict(proposal.get("blocks") or {})
            review = state.get("review") or {}
            feedback = dict(review.get("feedback") or {})
            requests = {}
            for bid in ids:
                row = db_map.get(bid)
                if row is not None:
                    blocks[bid] = {**blocks.get(bid, {}), "block_id": bid,
                                   "content": row.get("content") or "", "kind": "tech"}
                if action == "review" and not (blocks.get(bid) or {}).get("content", "").strip():
                    raise ValueError("选中章节尚无正文，请先撰写或生成")
                tech = (review.get("tech_findings") or {}).get(bid) or {}
                comp = (review.get("compliance_findings") or {}).get(bid) or {}
                issues = list(tech.get("issues") or []) + list(comp.get("issues") or [])
                feedback[bid] = {"issues": issues, "scores": {
                    "tech": tech.get("score"), "comp": comp.get("score")}}
                requests[bid] = [{"query": i.get("material_query") or i.get("point", ""),
                                  "reason": i.get("point", "")}
                                 for i in issues if i.get("needs_material")]
            if run is None:
                run = _Run(thread_id, EventEmitter())
                self._runs[thread_id] = run
            elif run.emitter._closed:
                run.emitter = EventEmitter()
            run.cancel_flag = False
            patch = {
                "stage": "reviewing" if action == "review" else "generating",
                "user_choice": "review_only" if action == "review" else "regen_blocks",
                "proposal": {"blocks": blocks, "updated_blocks": ids, "revision_scope": ids,
                             "regenerate_targets": ids if action == "revise" else [],
                             "material_requests": requests if action == "revise" else {}},
                "review": {"feedback": feedback},
                "materials": {"chunks": chunks},
            }
            if action == "revise":
                patch["iteration"] = int(state.get("iteration") or 0) + 1
            # 使用确定的图入口，避免 done checkpoint 没有 next 导致空跑。
            entry = NODE_GENERATE if action == "review" else GATE_MATERIALS
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter, thread_id=thread_id)
                await graph.aupdate_state({"configurable": {"thread_id": thread_id}}, patch, as_node=entry)
            run.task = asyncio.create_task(self._resume_until_pause(run, {}), name=f"wf-{thread_id}-{action}")

    async def rerun_match(self, thread_id: str) -> None:
        """重新触发素材匹配：把 thread 状态指针拨回 GATE_OUTLINE 节点，再 ainvoke 让 graph
        重新跑 shen_kuo_match → gate_materials。

        典型场景：用户已停在 gate_materials 状态点击「素材匹配」，希望沈括按当前已有 outline
        重新匹配（无需再走 parse / extract）。

        实现：
        - aupdate_state(as_node=GATE_OUTLINE) 让 LangGraph 把这次 update 视作 GATE_OUTLINE 节点
          的产物 → next 自动指向 NODE_MATCH（按图边推断）。
        - 顺便把 materials.matches 清空，避免旧匹配混入。
        """
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        else:
            # 旧 emitter 可能已 close（前次 stream 已结束），重建一份让新 stream 收事件
            run.emitter = EventEmitter()
        run.task = asyncio.create_task(
            self._rerun_match_until_pause(run),
            name=f"wf-{thread_id}-rerun-match",
        )

    async def redraft_outline(self, thread_id: str, instruction: str) -> None:
        """整版重出「应答文件目录」：换一份提炼要求，让张衡重新派生目录。

        与 ``rerun_match`` 同为「把状态指针拨回上游节点后 ainvoke」：
        - ``aupdate_state(..., as_node=NODE_PARSE)`` → LangGraph 推断 next =
          NODE_OUTLINE_DRAFT，天然**跳过 parse**（不必为改一次要求重跑一遍 PDF OCR）。
        - ``spec.outline_matrix`` 清空（借 ``_merge_dict`` 浅合并整版替换），
          ``spec.source_toc`` **不动** —— 它是派生节点的 grounding 输入。
        - **刻意不清 ``spec.toc``**：派生节点要读它当「上一版目录」喂回模型，迭代才有
          连续性。清空属于多此一举 —— 节点返回值里的 ``toc`` 本就整版替换旧值，
          提前清掉只会让上一版目录丢失（上一版目录 = 用户在闸门 1 看过的那一版）。
        """
        from orchestrator.graph import NODE_PARSE

        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.task and not run.task.done():
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

        state = await self.state(thread_id)
        if not state or not state.get("project_id"):
            raise KeyError(thread_id)
        if state.get("cancel_requested") or state.get("stage") == "aborted":
            raise ValueError("该工作流已作废，请创建新的工作流")
        if state.get("stage") not in {"outline_review", "materials_review", "paused"}:
            raise ValueError("请在「要求与大纲」闸门处重出目录")

        # 旧 emitter 可能已 close（前次 stream 已结束），重建一份让新 stream 收事件
        run.emitter = EventEmitter()
        run.cancel_flag = False

        async def _redraft(r: _Run, text: str) -> None:
            try:
                config = {"configurable": {"thread_id": r.thread_id}}
                async with self._checkpointer_provider() as saver:
                    graph = self._build(saver, r.emitter, thread_id=r.thread_id)
                    await graph.aupdate_state(
                        config,
                        {
                            "spec": {"outline_matrix": {}},
                            "config": {"outline_instruction": text},
                            "user_choice": "",
                        },
                        as_node=NODE_PARSE,
                    )
                    await graph.ainvoke(None, config=config)
                await self._sync_stage(r.thread_id, graph_done=False)
            except WorkflowCancelled:
                await self._mark_aborted(r)
            except Exception as e:
                logger.exception(f"runner: thread {r.thread_id} redraft_outline 异常")
                await r.emitter.emit(events.error(str(e), retryable=False))

        run.task = asyncio.create_task(
            _redraft(run, instruction or ""),
            name=f"wf-{thread_id}-redraft-outline",
        )

    async def _rerun_match_until_pause(self, run: _Run) -> None:
        """rerun_match 的后台 task：as_node=GATE_OUTLINE 后 ainvoke 走 match → gate_materials。"""
        from orchestrator.graph import GATE_OUTLINE

        try:
            config = {"configurable": {"thread_id": run.thread_id}}
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter, thread_id=run.thread_id)
                # 清掉旧 matches，并把指针拨回 GATE_OUTLINE 之后（next = match_node）
                await graph.aupdate_state(
                    config,
                    {"materials": {"matches": {}}, "user_choice": "approve"},
                    as_node=GATE_OUTLINE,
                )
                await graph.ainvoke(None, config=config)
            await self._sync_stage(run.thread_id, graph_done=False)
        except WorkflowCancelled:
            await self._mark_aborted(run)
        except Exception as e:
            logger.exception(f"runner: thread {run.thread_id} rerun_match 异常")
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def abort(self, thread_id: str) -> None:
        """请求取消：设 cancel_requested。下一节点开头会抛 WorkflowCancelled。"""
        run = self._get_run(thread_id)
        run.cancel_flag = True
        # 直接走 update_state 而非 resume —— 避免 graph 已暂停在闸门时再 invoke
        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, run.emitter, thread_id=run.thread_id)
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"cancel_requested": True, "user_choice": "abort"},
            )
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(thread_id, "aborted")
        await run.emitter.emit(events.aborted("user_abort"))
        await run.emitter.aclose()

    async def pause(self, thread_id: str) -> None:
        """生成阶段用户主动暂停：设 _Run.cancel_flag=True，generate 节点在每个 block 开始
        前检测到后跳过剩余 block，把 stage 写为 'paused' 并 emit paused 事件，graph 在
        GATE_PAUSE 闸门停下，等用户决定继续 / 跳评审 / 放弃。"""
        run = self._get_run(thread_id)
        run.cancel_flag = True
        # 不动 user_choice，因为暂停期间用户还没做决定。

    async def skip_to_review(self, thread_id: str) -> None:
        """从 GATE_PAUSE 出来，跳到评审节点（用已生成内容继续评审）。"""
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.emitter._closed:
            run.emitter = EventEmitter()
        run.cancel_flag = False  # 进评审前清掉暂停标记
        run.task = asyncio.create_task(
            self._resume_until_pause(run, {"user_choice": "skip_to_review"}),
            name=f"wf-{thread_id}-skip-to-review",
        )

    async def resume_generation(self, thread_id: str) -> None:
        """从 GATE_PAUSE 出来，回到 generate 节点继续生成（已生成 block 会被跳过）。"""
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.emitter._closed:
            run.emitter = EventEmitter()
        run.cancel_flag = False
        run.task = asyncio.create_task(
            self._resume_until_pause(run, {"user_choice": "resume", "stage": "generating"}),
            name=f"wf-{thread_id}-resume-gen",
        )

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
                graph = self._build(saver, run.emitter, thread_id=run.thread_id)
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
                graph = self._build(saver, run.emitter, thread_id=run.thread_id)
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

    def _build(
        self,
        saver: BaseCheckpointSaver,
        emitter: Optional[EventEmitter],
        *,
        thread_id: Optional[str] = None,
    ):
        deps = self._deps_factory()
        # 把 _Run.cancel_flag 闭包传给 generate 节点：用户调 pause() 时设此 flag，
        # generate 节点在每个 block 开始前检查决定是否跳过。
        run = self._runs.get(thread_id) if thread_id else None
        should_cancel = (lambda: bool(run and run.cancel_flag)) if run else None
        return build_graph(deps, emitter=emitter, checkpointer=saver, should_cancel=should_cancel)


__all__ = ["WorkflowRunner"]
