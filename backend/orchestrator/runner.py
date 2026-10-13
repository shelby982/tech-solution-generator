"""WorkflowRunner —— 路由层与 LangGraph 之间的薄外观层。

职责：
- 创建 / 续跑 / 取消 / 恢复一次工作流
- 维护 workflow_runs 表（thread_id ↔ project_id ↔ stage）
- 把 graph 推出的 SSE 事件 + checkpoint 推流给前端

设计原则（spec §4 分层规则）：
- routes 不直接持有 LangGraph compile 结果；通过 ``WorkflowRunner`` 间接调用
- runner 不知道 SSE 字面格式，只知道 ``EventEmitter`` 流
- runner 把 ``WorkflowCancelled`` 抓住后转 stage="aborted"（节点开头就检查的那种）；
  逐节提炼（extract）改走**优雅暂停**：每节开始前查一次取消标志，停下并返回
  stage="paused"，已完成的章节照常保留 —— 取消必须真的省下后续模型调用

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
from infra.llm import usage as llm_usage
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

class WorkflowAbortedError(ValueError):
    """目标 thread 已作废（用户取消过，stage=aborted）。

    单独一个类型是为了让路由层给出**可判别的错误码**（``workflow_aborted``），
    前端据此自动切到最近一次可用运行 / 新建运行，而不是只把中文提示丢给用户 ——
    用户看到「请创建新的工作流」时并不知道该点哪里，页面会卡在这个死线程上。
    """


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


def recorded_requirement_filenames(spec: dict) -> set[str]:
    """这次解析到底用了哪些要求文件。

    优先 ``material_files``（本次改动后写入），其次从 ``source_toc`` 的
    ``source_file`` 反推 —— **更早的 run 也带着 source_file**，所以旧 run 同样能比。
    两者都没有则返回空集（调用方据此放行，避免把正常重出也拦死）。
    """
    spec = spec or {}
    files = {f for f in (spec.get("material_files") or []) if f}
    if files:
        return files
    return {
        (s.get("source_file") or "")
        for s in (spec.get("source_toc") or [])
        if isinstance(s, dict) and (s.get("source_file") or "")
    }


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
        """启动新工作流，返回 thread_id。后台 task 跑到第一个闸门后等续跑指令。

        **新 run 开始时清掉旧 run 的 blocks**：用户换掉要求文件之后，旧目录就是无效
        数据（不会再被用到），留着只会让页面上两套目录混着显示。清理发生在这里而不是
        前端，是为了不依赖某个按钮是否被点到 —— 只要开了新 run，项目里就只剩一套。
        """
        thread_id = str(uuid.uuid4())
        async with get_db() as db:
            repo = WorkflowRunRepository(db)
            await repo.create(thread_id, project_id, stage="idle")
            from services.block_store import purge_blocks_except_run

            dropped = await purge_blocks_except_run(db, project_id, keep_thread_id=None)
            if dropped:
                logger.info(
                    f"runner: 项目 {project_id} 新建 run，清理旧 run 的 {dropped} 条 blocks"
                )

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
        # 同一 thread 只允许一个后台 task 在跑。缺少这层保护时，用户刷新页面
        # （闸门 1 的确认按钮会重新出现，见 project-init 的 boot 恢复逻辑）或开第二个
        # 标签页再点一次，就会有两个 graph.ainvoke 并发跑同一个 thread —— 逐节提炼是
        # 每章一次模型调用，费用直接翻倍、SSE 帧也双份。workspace_action / redraft_outline
        # 一直有同样的检查，这里补齐其余入口。
        self._assert_idle(thread_id)
        current = await self.state(thread_id)
        if not current or not current.get("project_id"):
            raise KeyError(thread_id)
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.emitter._closed:
            # 上一段流已收尾（graph 跑完时 aclose 过）。复用它的话 emit 会被静默丢弃，
            # 前端一帧都收不到、只能刷新读库才看到结果 —— 续跑前换一份。
            run.emitter = EventEmitter()
        patch: dict = dict(edits or {})
        if current.get("stage") == "report_review" and user_choice == "approve":
            patch["stage"] = "done"
        if user_choice:
            patch["user_choice"] = user_choice
        if user_choice == "approve":
            # 「确认 / 继续」是明确的前进指令，必须顺带解冻一次取消：闸门 1 放行后用户点了
            # 取消，checkpoint 里留着 cancel_requested=True（abort 只写它），下一次放行
            # 若不清理，extract 节点开头的 _check_cancel 会立刻再抛一次 WorkflowCancelled
            # —— 用户看到的现象是「点了确认什么都没发生」。
            # 同时清 _Run.cancel_flag（生成阶段的中断用的是它，resume_generation 也这么清）。
            patch["cancel_requested"] = False
            if run is not None:
                run.cancel_flag = False
            # abort 还会把 workflow_runs.stage 写成 'aborted'。放行后要把它改回来，
            # 否则项目列表/进度条兜底会一直显示「已作废」，而任务其实正在跑。
            await self._unfreeze_run_stage(thread_id, current.get("stage") or "parsing")

        # 起一个新的后台 task 接力（前一个 task 已在闸门处 ainvoke 返回）
        run.task = asyncio.create_task(
            self._resume_until_pause(run, patch),
            name=f"wf-{thread_id}-resume",
        )

    async def resume_extract(self, thread_id: str) -> None:
        """取消/暂停后**接着**逐节提炼（不重新解析、不重新派生目录、已完成的章节不重跑）。

        实现要点：取消时图已经收尾（next=()），普通 ``ainvoke(None)`` 是空跑，所以借
        闸门 1 的出边（GATE_OUTLINE → NODE_EXTRACT）把指针拨回 extract 之前 —— 与
        ``rerun_match`` 借 NODE_EXTRACT 同一手法。extract 自己会跳过
        ``status='outline_done'`` 的章节（skip_ids），所以这里只补没做完的部分。
        """
        from orchestrator.graph import GATE_OUTLINE

        self._assert_idle(thread_id)
        current = await self.state(thread_id)
        if not current or not current.get("project_id"):
            raise KeyError(thread_id)
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.emitter._closed:
            run.emitter = EventEmitter()
        run.cancel_flag = False
        await self._unfreeze_run_stage(thread_id, "outline_review")
        run.task = asyncio.create_task(
            self._resume_until_pause(
                run,
                {"cancel_requested": False, "user_choice": "approve",
                 "stage": "outline_review"},
                as_node=GATE_OUTLINE,
            ),
            name=f"wf-{thread_id}-resume-extract",
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
                raise WorkflowAbortedError("该工作流已作废，请创建新的工作流")
            if state.get("stage") not in {"report_review", "done", "paused", "materials_review"}:
                raise ValueError("请先完成大纲和素材确认，或等待当前运行结束")
            if action not in {"review", "revise"}:
                raise ValueError("不支持的章节操作")
            ids = list(dict.fromkeys(block_ids))
            matrix = (state.get("spec") or {}).get("outline_matrix") or {}
            if not ids or any(bid not in matrix for bid in ids):
                raise ValueError("请选择当前工作流中的有效章节")
            async with get_db() as db:
                rows = await list_blocks(
                    db, state["project_id"], run_thread_id=state.get("thread_id"),
                )
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

    async def rerun_match(self, thread_id: str, *, full: bool = False) -> None:
        """触发素材匹配：把 thread 状态指针拨回 NODE_EXTRACT 节点，再 ainvoke 让 graph
        跑 shen_kuo_match → gate_materials。

        ``full=False``（默认）**增量**：保留已有匹配，只把新上传的素材归属到章节
        （匹配节点按"池里未被引用过的片段"筛）。补一份素材不再全量重跑 33 章。
        ``full=True``：清空全部匹配重新归属（用户显式要求，代价大）。

        典型场景：用户已停在 gate_materials 状态点击「素材匹配」，希望沈括按当前已有 outline
        重新匹配（无需再走 parse / extract）。

        实现：
        - aupdate_state(as_node=NODE_EXTRACT) 让 LangGraph 把这次 update 视作 NODE_EXTRACT 节点
          的产物 → next 自动指向 NODE_MATCH（按图边推断）。
          **不能用 GATE_OUTLINE**：图改成「闸门1 先于 extract」之后，GATE_OUTLINE 的 next 是
          NODE_EXTRACT，那样每次重匹配都要白烧 N 次逐节提炼。
        - 顺便把 materials.matches 清空，避免旧匹配混入。
        """
        run = self._runs.get(thread_id)
        self._assert_idle(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        else:
            # 旧 emitter 可能已 close（前次 stream 已结束），重建一份让新 stream 收事件
            run.emitter = EventEmitter()
        run.task = asyncio.create_task(
            self._rerun_match_until_pause(run, full=full),
            name=f"wf-{thread_id}-rerun-match",
        )

    async def _requirement_files_changed(self, state: WorkflowState) -> bool:
        """当前「应标要求」文件是否已不是这次运行解析时的那一批。

        两条判据，任一命中即算变更（**fail-closed**：判不出来时不放行）：
        1. ``spec.material_sig`` 有值 → 与当前材料指纹比对；
        2. 没有指纹（本改动之前创建的 run）→ 用 ``spec.material_files``
           （该次解析用到的文件名）与当前材料文件名比对。

        为什么要兜底：实测旧 run 没有指纹，守卫被跳过，用户在"删旧文件 + 传新文件"
        之后点「重新拆分目录」，它照旧用 checkpoint 里的旧 source_toc 出目录 ——
        页面看起来毫无变化，而用户以为新文件已生效（本项目连点 8 次）。
        """
        from db import get_db
        from orchestrator.nodes import materials_signature

        spec = state.get("spec") or {}
        project_id = int(state["project_id"])

        # ① 最硬的判据：本次运行绑定的 material_ids vs 项目当前未删材料。
        #    比文件名/大小都可靠（改名、换内容、同名替换都能识别）。
        recorded_ids = [int(i) for i in (spec.get("material_ids") or []) if i]
        if not recorded_ids:
            # 旧 run（本次改动前创建）没有 material_ids：按它 source_toc 里的文件名
            # 反查当前材料表，把"它自己那批文件"的 id 补出来。查不到就留空，
            # 走下面的指纹/文件名兜底。
            names = recorded_requirement_filenames(spec)
            if names:
                async with get_db() as conn:
                    cursor = await conn.execute(
                        "SELECT id, filename FROM materials WHERE project_id = ?",
                        (project_id,),
                    )
                    recorded_ids = [
                        int(r["id"]) for r in await cursor.fetchall()
                        if r["filename"] in names
                    ]
        if recorded_ids:
            from services.block_store import list_materials
            async with get_db() as conn:
                current_ids = [int(m["id"]) for m in await list_materials(conn, project_id)]
            return sorted(recorded_ids) != sorted(current_ids)

        # ② 兼容旧 run：按指纹 → 文件名兜底
        current_sig = await materials_signature(project_id)
        recorded = spec.get("material_sig") or ""
        if recorded:
            return current_sig != recorded

        recorded_files = recorded_requirement_filenames(spec)
        if not recorded_files:
            # 连一条文件记录都没有（空解析的 run / 测试夹具）：无法比较，
            # 此时拦下来只会让"正常重出"也失败 —— 放行，交由阶段守卫把关。
            return False

        async with get_db() as conn:
            cursor = await conn.execute(
                "SELECT filename FROM materials WHERE project_id = ?", (project_id,)
            )
            current = {r["filename"] for r in await cursor.fetchall()}
        return recorded_files != current

    def _is_running(self, thread_id: str) -> bool:
        """这条 run 的后台 task 是否还在跑。

        判"能不能重拆"用它而不是 stage：stage 是**上次落盘**的快照，abort 之后
        还会停在 parsing/outline_review 上（实测 efe2ac4c: stage=parsing 但其实早已
        被 abort）。真正决定能不能重拆的是"还有没有东西在动"。
        """
        run = self._runs.get(thread_id)
        task = getattr(run, "task", None) if run else None
        return bool(task and not task.done())

    async def redraft_check(self, thread_id: str) -> dict:
        """重出目录前的预检：告诉前端"现在能不能重拆、不能的话该做什么"。

        前端在点「重新拆分目录」前先问这里 —— 否则用户拿到的是一个 200 却
        "什么都没变"，只能反复点（实测 8 次）。
        """
        state = await self.state(thread_id)
        if not state or not state.get("project_id"):
            raise KeyError(thread_id)

        reasons: list[str] = []
        if await self._requirement_files_changed(state):
            reasons.append("requirement_files_changed")

        # 判据从"阶段"改成"**是否还在跑** + **有没有解析结果**"。
        # 为什么改：实测 run efe2ac4c 在 **parsing 阶段被 abort**，checkpoint 里
        # stage='parsing'、source_toc=531、但 outline_revision=None —— 派生目录从未跑过。
        # 旧判据把这个 run 归为"已推进到撰写/评审之后"，既**说错了原因**，又**挡住了唯一
        # 能救的路径**：重拆本来就是从 source_toc 派生应答目录，正是这时该做的事。
        running = self._is_running(thread_id)
        if running:
            reasons.append("run_in_progress")
        elif not (state.get("spec") or {}).get("source_toc"):
            reasons.append("no_source_toc")

        if "requirement_files_changed" in reasons:
            message = (
                "要求文件已变更（新增 / 删除 / 替换过），当前运行的目录是按**上一次解析**"
                "的结果生成的，「重新拆分目录」带不上新文件。\n"
                "请点「重新提炼」：会新建一次运行并重新解析全部要求文件。"
            )
        elif "run_in_progress" in reasons:
            message = "当前运行还在跑（解析 / 派生进行中），等它停下来再重新拆分目录。"
        elif "no_source_toc" in reasons:
            message = (
                "这次运行还没有可用的解析结果（要求文件没解析出来或解析中断）。\n"
                "请点「重新提炼」：会新建一次运行并重新解析全部要求文件。"
            )
        else:
            message = ""
        spec = state.get("spec") or {}
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "message": message,
            "stage": state.get("stage"),
            # 目录是否**已派生**：False 表示 blocks 里躺的是 parse 写的原始目录占位
            # （实测 531 条「1.招标条件 / 2.项目概况…」被页面当成应答目录显示）。
            # 前端据此提示"这是招标文件原始目录、尚未派生"，而不是让人去确认它。
            "has_outline": bool(spec.get("outline_revision")),
            "source_sections": len(spec.get("source_toc") or []),
        }

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
        if state.get("stage") == "aborted":
            raise WorkflowAbortedError("该工作流已作废，请创建新的工作流")
        # checkpoint 的 stage 与 workflow_runs.stage **可能不一致**：作废动作会写库，
        # 但 checkpoint 只在节点结束时更新。实测：库里是 aborted、checkpoint 还是
        # outline_review，于是这里放行、任务跑到某处静默结束 —— 用户看到"点了没反应"。
        # 所以两张表都要看。
        db_stage = await self._db_run_stage(thread_id)
        if db_stage == "aborted":
            raise WorkflowAbortedError(
                "该次运行已作废（workflow_runs=aborted），重新拆分不会生效。\n"
                "请点「重新提炼」新建一次运行并重新解析要求文件。"
            )
        # 注意：**不**因 cancel_requested 拒绝。「取消」现在是"暂停"（提炼逐节停下、
        # 内容保留），用户接着改要求、重出目录是完全合理的下一步。
        # failed 也允许重出：目录派生失败后，重试派生是用户唯一的自救入口
        # （source_toc 在 checkpoint 里，重新派发不需要重解析要求文件）。
        # 原料是否换过：重出目录用的是 checkpoint 里的 source_toc（上次解析结果），
        # 换了要求文件却只点重拆，目录会按旧解析结果生成 —— 页面看起来"没反应"。
        # 这里明确拒绝并指向「重新提炼」（重跑解析），而不是让用户反复点重拆。
        changed = await self._requirement_files_changed(state)
        if changed:
            raise ValueError(
                "要求文件已变更（新增 / 删除 / 替换过），当前运行的目录是按**上一次解析**"
                "的结果生成的，「重新拆分目录」带不上新文件。\n"
                "请点「重新提炼」：会新建一次运行并重新解析全部要求文件。"
            )
        if self._is_running(thread_id):
            raise ValueError("当前运行还在跑（解析 / 派生进行中），等它停下来再重新拆分目录。")

        # 保留原来的"阶段"限制：已经进到生成/评审之后，改目录会让正文对不上。
        # 但 parsing / aborted 放行 —— 那时还没有派生目录，重拆正是唯一出路。
        if state.get("stage") not in {"outline_review", "materials_review", "paused",
                                      "failed", "parsing", "aborted", "idle"}:
            # 说清「为什么不行」与「该怎么办」。原来只写"请在…闸门处重出目录"，
            # 而 run 跑到 done 之后根本回不到那个闸门（实测用户点了 3 次，每次都只拿到
            # 这句话、无从下手）。而且**改要求文件本身也不能靠重出目录** ——
            # 重出只重跑"目录派生"，用的是 checkpoint 里的 spec.source_toc（新上传的
            # 要求文件不在里面）；要让新文件参与派生必须重跑"解析"，即新起一次运行。
            raise ValueError(
                "当前运行已推进到「撰写/评审」之后，目录不能再改（改了正文就对不上了）。\n"
                "另：如果刚上传了新的要求文件（如技术规范书），「重新拆分目录」也不会带上它 —— "
                "它只重跑目录派生，原料是上一次解析的结果。\n"
                "请点「重新提炼」：会新建一次运行并**重新解析全部要求文件**。"
            )

        # 旧 emitter 可能已 close（前次 stream 已结束），重建一份让新 stream 收事件
        run.emitter = EventEmitter()
        run.cancel_flag = False

        async def _redraft(r: _Run, text: str) -> None:
            try:
                config = {"configurable": {"thread_id": r.thread_id}}
                async with self._checkpointer_provider() as saver:
                    graph = self._build(saver, r.emitter, thread_id=r.thread_id)
                    with llm_usage.run_context(
                        project_id=state.get("project_id"), thread_id=r.thread_id,
                    ):
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

    async def confirm_outline(self, thread_id: str, matrix: dict) -> None:
        """「确认内容大纲及要求」：把用户在提炼结果上的编辑写回。

        两处都要写，读者不同：
        - ``blocks`` 表：页面 / 工作台显示的权威数据。
        - 图里的 ``spec.outline_matrix``：素材匹配读的是它（``shen_kuo_match_node``
          从 state 取矩阵），不写回就会拿旧值去匹配。

        与闸门 ``resume`` 的区别：此时图已收尾（extract 的出边默认收尾到 END），
        没有下一跳可跑，所以只 ``aupdate_state`` 不 ``ainvoke``。
        """
        from services.block_store import update_outline_fields

        state = await self.state(thread_id)
        if not state or not state.get("project_id"):
            raise KeyError(thread_id)

        project_id = int(state["project_id"])
        async with get_db() as db:
            for block_id, row in matrix.items():
                await update_outline_fields(
                    db, project_id, block_id, row, run_thread_id=thread_id,
                )

        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, None, thread_id=thread_id)
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"spec": {"outline_matrix": matrix}},
            )

    # 结构编辑只在「目录待确认」与静止态允许：运行中的 stage 一边写一边被读会打架
    _STRUCTURE_BUSY_STAGES = {
        "parsing", "outline_drafting", "extracting", "matching",
        "generating", "reviewing",
    }

    async def set_chapter_evidence(
        self, thread_id: str, block_id: str, *,
        action: str, chunk_id: int | None = None, item_ids: list[str] | None = None,
    ) -> dict:
        """人工挂接 / 解绑 / 改挂载条目（写 `blocks.source`）。

        **两处都要写**（与 ``update_outline_structure``、``confirm_outline`` 同一道理，
        读者不同）：
        - ``blocks`` 表：页面与撰写阶段读的权威数据；
        - 图里的 ``materials.matches``：**匹配节点下一轮重算时的"已有依据"来源**
          （``_match_by_assignment`` 用 state 里的 matches 兜底旧行 + 追加新增）。
          只写库不写 state 的话，用户下一次点「素材匹配」时这条人工挂接会被
          "existing(来自 state) + new" 覆盖掉 —— 人工的判断被机器抹掉。

        只 ``aupdate_state`` 不 ``ainvoke``：挂接是编辑动作，不该顺手往下跑流程。
        """
        from domain.material.overview import evidence_from_chunk
        from domain.material.models import BY_HUMAN
        from services.block_store import (
            get_chunk_with_filename, list_blocks, update_block_source,
        )

        if action not in ("mount", "unmount", "set_items"):
            raise ValueError(f"未知操作：{action}")

        state = await self.state(thread_id)
        if not state or not state.get("project_id"):
            raise KeyError(thread_id)
        stage = str(state.get("stage") or "")
        if stage == "aborted":
            raise WorkflowAbortedError("该工作流已作废，请创建新的工作流")
        if stage in self._STRUCTURE_BUSY_STAGES:
            raise ValueError("当前任务仍在运行，请等这一步结束后再调整素材挂接")

        matches_state = dict((state.get("materials") or {}).get("matches") or {})
        rows = [dict(r) for r in (matches_state.get(block_id) or []) if isinstance(r, dict)]

        async with get_db() as db:
            # blocks.source 按**自增主键**写，而接口收的是字符串 block_id（s2.1 这种位置
            # 派生 id）→ 先按 (project, thread) 取回行做映射。
            project_id = int(state["project_id"])
            all_blocks = await list_blocks(db, project_id, thread_id, fallback_all=True)
            target = next(
                (b for b in all_blocks if str(b.get("block_id") or b.get("id")) == str(block_id)),
                None,
            )
            if target is None:
                raise KeyError(f"章节不存在：{block_id}")
            row_id = int(target["id"])

            if action == "mount":
                if chunk_id is None:
                    raise ValueError("挂接需要 chunk_id")
                chunk = await get_chunk_with_filename(db, int(chunk_id))
                if chunk is None:
                    raise KeyError(f"素材片段不存在：{chunk_id}")
                ev = evidence_from_chunk(chunk, item_ids=item_ids or []).to_dict()
                rows = [r for r in rows if r.get("chunk_id") != ev["chunk_id"]]
                rows.append(ev)
            elif action == "unmount":
                if chunk_id is None:
                    raise ValueError("解绑需要 chunk_id")
                before = len(rows)
                rows = [r for r in rows if r.get("chunk_id") != int(chunk_id)]
                if len(rows) == before:
                    raise KeyError(f"这一章没有挂载该素材片段：{chunk_id}")
            else:   # set_items：改这条依据挂到哪些应答条目
                if chunk_id is None:
                    raise ValueError("改挂载条目需要 chunk_id")
                hit = False
                for r in rows:
                    if r.get("chunk_id") == int(chunk_id):
                        r["item_ids"] = list(item_ids or [])
                        r["by"] = BY_HUMAN
                        r["locked"] = True
                        hit = True
                if not hit:
                    raise KeyError(f"这一章没有挂载该素材片段：{chunk_id}")

            await update_block_source(db, row_id, rows)

        # 同步 checkpoint：只 patch 这一章（materials 是一级 dict merge，matches 整体替换）
        matches_state[str(block_id)] = rows
        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, None, thread_id=thread_id)
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"materials": {"matches": matches_state}},
            )
        return {"block_id": str(block_id), "evidence": rows}

    async def update_outline_structure(
        self, thread_id: str, nodes: list[dict], *, purge: bool = False,
    ) -> dict:
        """闸门 1 的目录结构编辑：增删章节 / 改标题 / 上下移动 / 缩进。

        ``nodes`` 是有序列表 ``[{block_id, level, title}]``（层级由顺序推导）。

        两处都要写，读者不同 —— 与 ``confirm_outline`` 同一个道理：
        - ``blocks`` 表：页面渲染的权威数据；
        - 图里的 ``spec.toc``：**extract 逐节提炼读的是它**（``zhang_heng_extract_node``
          从 ``spec.toc`` 取章节列表），不写回 state 的话「用户看到的目录」与
          「被提炼的目录」立刻发散。

        只 ``aupdate_state`` 不 ``ainvoke``：此时图停在闸门 1，下一步由用户点
        「确认目录，开始提炼要点」触发。
        """
        from domain.spec.structure import StructureError, validate_structure
        from services.block_store import apply_outline_structure

        state = await self.state(thread_id)
        if not state or not state.get("project_id"):
            raise KeyError(thread_id)

        stage = str(state.get("stage") or "")
        # 只把真正作废（aborted）的 run 当死线程：取消 = 暂停，此时用户仍可以调目录
        if stage == "aborted":
            # 作废的 run 改目录没有意义（它不会再往下跑），且会让页面与「最近一次运行」发散
            raise WorkflowAbortedError("该工作流已作废，请创建新的工作流")
        if stage in self._STRUCTURE_BUSY_STAGES:
            raise ValueError("当前任务仍在运行，请等这一步结束后再调整目录")

        clean = validate_structure(nodes)   # 非法时抛 StructureError（路由转 400）

        project_id = int(state["project_id"])
        spec = state.get("spec") or {}
        # 保留原 toc 条目上的其它字段（raw_content / special_marks），只覆盖层级与标题；
        # 新节点没有原始内容，留空即可（它是用户手写的章节）。
        old_by_id = {d.get("id"): d for d in (spec.get("toc") or []) if isinstance(d, dict)}
        new_toc = []
        for node in clean:
            old = old_by_id.get(node["block_id"]) or {}
            new_toc.append({
                **old,
                "id": node["block_id"],
                "level": node["level"],
                "title": node["title"],
                # 用户新增的章节没有原文：键要补齐（toc 条目的形状与 parse 产出一致，
                # 下游 extract/导出按固定键读取）。raw_content 为空 → 该节提炼时没有
                # 可依据的原文，见 docs 的审查记录。
                "raw_content": old.get("raw_content", "") or "",
                "special_marks": list(old.get("special_marks") or []),
            })

        async with get_db() as db:
            stats = await apply_outline_structure(
                db, project_id, clean, run_thread_id=thread_id, purge=purge,
            )

        # spec.outline_matrix 是整版替换的（见 state.py 的 reducer 约定），必须**同步裁剪**：
        # 删掉/改名后目录外的章节若留在矩阵里，下游 generate 的"toc 之外兜底"会照着它
        # 生成一节正文 —— 用户删掉的章节又长回来了。
        new_ids = {d["id"] for d in new_toc}
        pruned_matrix = {
            bid: row for bid, row in (spec.get("outline_matrix") or {}).items()
            if bid in new_ids
        }
        if len(pruned_matrix) != len(spec.get("outline_matrix") or {}):
            logger.info(
                f"update_outline_structure：裁剪掉 %d 个目录外的矩阵条目",
                len(spec.get("outline_matrix") or {}) - len(pruned_matrix),
            )
        async with self._checkpointer_provider() as saver:
            graph = self._build(saver, None, thread_id=thread_id)
            await graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"spec": {"toc": new_toc, "outline_matrix": pruned_matrix}},
            )

        # 把**归一化后的**结构回给前端：服务端会压平跨级加深、clamp 到 1..4，
        # 前端若只按自己的入参重绘，就会与库里的权威结构发散（例如用户把某节点
        # 降到 4 级、实际存成 3 级，页面上却显示 4 级）。
        return {
            **stats,
            "total": len(clean),
            "nodes": [
                {"block_id": n["block_id"], "level": n["level"], "title": n["title"]}
                for n in clean
            ],
        }

    async def _rerun_match_until_pause(self, run: _Run, *, full: bool = False) -> None:
        """rerun_match 的后台 task：as_node=NODE_EXTRACT 后 ainvoke 走 match → gate_materials。"""
        from orchestrator.graph import NODE_EXTRACT

        try:
            config = {"configurable": {"thread_id": run.thread_id}}
            current = await self.state(run.thread_id)
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter, thread_id=run.thread_id)
                # 清掉旧 matches，并把指针拨回 NODE_EXTRACT 之后（next = match_node）。
                # match_requested 是 extract 出边条件边的开关：置真才继续往 match 走，
                # 否则提炼跑完就收尾停在「要求与大纲」。
                with llm_usage.run_context(
                    project_id=(current or {}).get("project_id"),
                    thread_id=run.thread_id,
                ):
                    patch: dict = {
                        "user_choice": "approve",
                        "match_requested": True,
                        # 取消过再点「素材匹配」= 明确前进，顺带解冻
                        "cancel_requested": False,
                    }
                    if full:
                        # 显式"全部重匹配"才清空；默认增量保留已有依据（含人工挂接）
                        patch["materials"] = {"matches": {}}
                    await graph.aupdate_state(config, patch, as_node=NODE_EXTRACT)
                    # 匹配同样要跑一阵：先标 'matching'，否则这段时间 /state 读到的
                    # checkpoint 还停在上一个阶段（提炼收尾后是 outline_review）。
                    await self._mark_next_phase(graph, config, run.thread_id)
                    await graph.ainvoke(None, config=config)
            await self._sync_stage(run.thread_id, graph_done=False)
        except WorkflowCancelled:
            await self._mark_aborted(run)
        except Exception as e:
            logger.exception(f"runner: thread {run.thread_id} rerun_match 异常")
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def abort(self, thread_id: str) -> None:
        """请求取消：设 cancel_requested + _Run.cancel_flag。

        - 节点**开头**检查 ``state.cancel_requested`` → 抛 WorkflowCancelled → aborted；
        - 长节点（extract / generate）逐节检查 ``cancel_flag`` → 立刻停在当前节边界，
          已完成的保留，收尾为 paused（可用 resume_extract / resume_generation 接着跑）。
        """
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
        self._assert_idle(thread_id)
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        elif run.emitter._closed:
            run.emitter = EventEmitter()
        run.cancel_flag = False
        run.task = asyncio.create_task(
            self._resume_until_pause(run, {"user_choice": "resume", "stage": "generating",
                                           "cancel_requested": False}),
            name=f"wf-{thread_id}-resume-gen",
        )

    async def recover(self, thread_id: str) -> None:
        """显式恢复：用现有 thread_id 继续跑（崩溃恢复用）。"""
        self._assert_idle(thread_id)
        run = self._runs.get(thread_id)
        if run is None:
            run = _Run(thread_id, EventEmitter())
            self._runs[thread_id] = run
        run.task = asyncio.create_task(
            self._resume_until_pause(run, {}),
            name=f"wf-{thread_id}-recover",
        )

    async def _db_run_stage(self, thread_id: str) -> str:
        """读 workflow_runs.stage（与 checkpoint 可能不一致，见 redraft 守卫注释）。"""
        try:
            from db import get_db
            async with get_db() as db:
                cur = await db.execute(
                    "SELECT stage FROM workflow_runs WHERE thread_id = ?", (thread_id,))
                row = await cur.fetchone()
            return str((row["stage"] if row else "") or "")
        except Exception:
            return ""

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
                # 把本次运行的 project/thread 写进 LLM 用量上下文：之后所有模型调用
                # 的 llm_usage 行都带上归属，才能回答「这次运行花了多少」。
                with llm_usage.run_context(
                    project_id=state.get("project_id"), thread_id=run.thread_id,
                ):
                    await graph.ainvoke(
                        state,
                        config={"configurable": {"thread_id": run.thread_id}},
                    )
            await self._sync_stage(run.thread_id, graph_done=False)
        except WorkflowCancelled:
            await self._mark_aborted(run)
        except Exception as e:
            logger.exception(f"runner: thread {run.thread_id} 异常")
            await self._mark_failed(run, str(e))
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def _resume_until_pause(self, run: _Run, patch: dict,
                                  *, as_node: str | None = None) -> None:
        """续跑：先 update_state(patch) 再 ainvoke(None)，跑到下一个 pause。

        ``as_node``：把这次 update 记在某个节点名下，LangGraph 据此推断 ``next``。
        图已经收尾（next=()）时普通 ainvoke(None) 是空跑，必须借某个节点的出边把
        指针拨回去（rerun_match 用 NODE_EXTRACT、resume_extract 用 GATE_OUTLINE）。
        """
        try:
            config = {"configurable": {"thread_id": run.thread_id}}
            # 续跑入口拿不到 state，先读一次 checkpoint —— 只在闸门/恢复时发生一次，
            # 不是每次模型调用，代价可忽略。
            current = await self.state(run.thread_id)
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, run.emitter, thread_id=run.thread_id)
                with llm_usage.run_context(
                    project_id=(current or {}).get("project_id"),
                    thread_id=run.thread_id,
                ):
                    if patch or as_node:
                        await graph.aupdate_state(
                            config, patch,
                            **({"as_node": as_node} if as_node else {}),
                        )
                    # 续跑前按下一个节点标运行期 stage（extract → extracting / match →
                    # matching）：提炼要跑几分钟，这期间 /state 读到的 checkpoint 仍是
                    # 闸门 1 的 outline_review，刷新页面会误导用户再确认一次。
                    await self._mark_next_phase(graph, config, run.thread_id)
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
            await self._mark_failed(run, str(e))
            await run.emitter.emit(events.error(str(e), retryable=False))

    async def _mark_failed(self, run: _Run, message: str) -> None:
        """图执行中途抛异常：把这次运行标成终态 ``failed``。

        不标的话 ``workflow_runs.stage`` 会停在「正在跑」的那个阶段（如 parsing），
        而 SSE 无回放 —— 刷新页面时前端会以为它还在跑、继续等帧（永远等不到）。
        标成 failed 之后前端的兜底轮询/boot 都会立刻按终态处理，并给出重试入口。
        """
        try:
            async with get_db() as db:
                await WorkflowRunRepository(db).update_stage(run.thread_id, "failed")
        except Exception as e:
            logger.warning(f"runner: 标记 failed 落库失败（{e}）")
        try:
            async with self._checkpointer_provider() as saver:
                graph = self._build(saver, None, thread_id=run.thread_id)
                await graph.aupdate_state(
                    {"configurable": {"thread_id": run.thread_id}},
                    {"stage": "failed",
                     "errors": [{"message": message, "retryable": False}]},
                )
        except Exception as e:
            # checkpoint 写失败不影响已经落库的 failed 标记
            logger.warning(f"runner: 标记 failed 写 checkpoint 失败（{e}）")

    async def _mark_aborted(self, run: _Run) -> None:
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(run.thread_id, "aborted")
        await run.emitter.emit(events.aborted("user_cancel"))
        await run.emitter.aclose()

    async def _sync_stage(self, thread_id: str, *, graph_done: bool) -> None:
        """从 checkpoint 读最新 stage，更新 workflow_runs 表 + 推送 checkpoint 事件。

        graph_done=True 通常是「跑完了」→ 'done'。唯一例外是**节点主动暂停**：用户
        取消逐节提炼时 extract 节点返回 stage='paused'，图随即收尾 —— 那不算跑完，
        标 'done' 会让页面显示「提炼完成」而其实还有章节没提炼。
        """
        state = await self.state(thread_id)
        stage = state.get("stage", "idle")
        if graph_done and stage != "paused":
            stage = "done"
        async with get_db() as db:
            await WorkflowRunRepository(db).update_stage(thread_id, stage)
        run = self._runs.get(thread_id)
        if run is not None:
            await run.emitter.emit(events.checkpoint(thread_id, stage))

    async def _mark_next_phase(self, graph, config: dict, thread_id: str) -> None:
        """按图的下一个节点标运行期 stage（extract → extracting / match → matching）。

        节点只能在**结束时**返回 stage（checkpoint 才落盘），所以「正在跑」的这十几分钟
        必须由 runner 自己写 workflow_runs —— 那是运行期唯一能改的权威来源，前端 boot
        也读它点亮进度条。读不到 checkpoint 时静默跳过：这只是进度提示，不该阻断续跑。
        """
        try:
            snap = await graph.aget_state(config)
        except Exception as e:
            logger.warning(f"runner: 读 checkpoint 失败（{e}），跳过运行期 stage 标注")
            return
        nxt = set(getattr(snap, "next", ()) or ())
        if not nxt:
            return
        from orchestrator.graph import NODE_EXTRACT, NODE_MATCH

        mapping = {NODE_EXTRACT: "extracting", NODE_MATCH: "matching"}
        for node, stage in mapping.items():
            if node in nxt:
                async with get_db() as db:
                    await WorkflowRunRepository(db).update_stage(thread_id, stage)
                run = self._runs.get(thread_id)
                if run is not None:
                    await run.emitter.emit(events.checkpoint(thread_id, stage))
                return

    def _assert_idle(self, thread_id: str) -> None:
        """同一 thread 同时只允许一个后台 task。

        没有这层保护时，刷新页面 / 开第二个标签页重复点「确认目录」会再起一个 task，
        两个 ``graph.ainvoke`` 在同一个 thread 上并发 —— 逐节提炼是每章一次模型调用，
        费用直接翻倍，SSE 帧也是双份。
        """
        run = self._runs.get(thread_id)
        if run is not None and run.task and not run.task.done():
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

    async def _unfreeze_run_stage(self, thread_id: str, stage: str) -> None:
        """把 abort 写下的 'aborted' 改回 checkpoint 的真实阶段（放行后调用）。

        只覆盖 'aborted'：其它阶段由 _sync_stage 负责，不能在这里乱改。
        """
        async with get_db() as db:
            repo = WorkflowRunRepository(db)
            row = await repo.get(thread_id)
            if not row or row.get("stage") != "aborted":
                return
            await repo.update_stage(thread_id, stage)

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
