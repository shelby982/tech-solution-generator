"""LangGraph 节点：把 5 个 agent 包装为图节点（spec §6 编排流程）。

每个节点函数接收完整 ``WorkflowState``，返回一个 ``state_patch`` dict（浅 merge 到 state）。

通用约束：
- 所有节点开头检查 ``state.cancel_requested``，触发则 raise ``WorkflowCancelled``
  （runner 外层捕获后把 stage 设为 ``aborted``）。spec §6 原本写 ``GraphInterrupt``，
  但 LangGraph 的 ``GraphInterrupt`` 是内部子图信号、不可直接 raise — 故引入轻量
  自定义异常承担相同语义。
- 跳过已完成 block：``zhuge_liang_generate_node`` 在 ``regenerate_targets`` 为空且
  ``proposal.blocks`` 已含某 block 时跳过该 block。
- 节点不直接发 SSE，事件帧由 emitter（runner 注入到节点闭包）推送。

依赖注入：节点签名是 ``async def node(state, agent=..., emitter=...)``，运行时
``functools.partial`` 把 agent 实例与 emitter 绑死，再注册进 LangGraph，方便单测把
任意 mock agent 注入进来验证 ``state_patch``。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Optional, Union

from agents.bao_zheng import BaoZhengAgent
from agents.shen_kuo import ShenKuoAgent
from agents.wang_anshi import WangAnshiAgent
from agents.zhang_heng import ZhangHengAgent
from agents.zhuge_liang import ZhugeLiangAgent
from domain.proposal import BlockOutput
from domain.review import Finding, GlobalReport
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection
from infra.retrieval import Match
from orchestrator import events
from orchestrator.events import EventEmitter
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 取消信号：runner 在外层捕获，转 stage="aborted"
# ─────────────────────────────────────────────

class WorkflowCancelled(Exception):
    """用户在节点开头检测到 ``cancel_requested`` 时抛出。"""

    def __init__(self, reason: str = "user_cancel") -> None:
        super().__init__(reason)
        self.reason = reason


def _check_cancel(state: WorkflowState) -> None:
    if state.get("cancel_requested"):
        raise WorkflowCancelled("user_cancel")


# ─────────────────────────────────────────────
# 协同闭环：收敛阈值与迭代上限（spec §5.1）
# ─────────────────────────────────────────────

REVIEW_SCORE_THRESHOLD = int(os.getenv("REVIEW_SCORE_THRESHOLD", "80"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────
# emitter 适配：节点 → SSE 帧
# ─────────────────────────────────────────────

EmitterArg = Optional[EventEmitter]


async def _emit_frame(emitter: EmitterArg, frame: str) -> None:
    if emitter is None:
        return
    await emitter.emit(frame)


def _make_agent_emitter_bridge(
    emitter: EmitterArg,
    *,
    block_token_event: str = "block_token",
) -> Optional[Callable[[str, dict[str, Any]], Awaitable[None]]]:
    """把 agent 内部的 (event_type, payload) 回调桥接到 SSE emitter。

    诸葛亮 agent emit 的事件类型是 ``block_start / token / block_done``；这里把
    ``token`` 转成 SSE ``block_token`` 事件，其它事件按 spec §7 转换。
    返回 None 表示外层 emitter 缺省，agent 不需要发事件。
    """
    if emitter is None:
        return None

    async def _bridge(event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "block_start":
            await emitter.emit(events.block_start(
                block_id=payload.get("block_id", ""),
                kind=payload.get("kind", "tech"),
                title=payload.get("title", ""),
            ))
        elif event_type == "token":
            await emitter.emit(events.block_token(
                block_id=payload.get("block_id", ""),
                token=payload.get("token", ""),
            ))
        elif event_type == "block_done":
            # block_done 由 generate_node 在拿到 BlockOutput 后统一发；
            # agent 内部的 block_done 仅作进度信号，这里忽略。
            return
        # 评审 agent 的 review_block_start / review_block_done 在 review_node 拼 finding 后再统一发 review_finding 事件，这里同样忽略
    return _bridge


# ─────────────────────────────────────────────
# 张衡 parse 节点
# ─────────────────────────────────────────────

# 文件源加载回调（routes 在 start 时注入）：返回 [(binary_io, suffix, filename), ...]
# 列表 = 「应标要求」面板下的全部文件，提炼不绑定技术规范书。
SpecLoader = Callable[[int], Awaitable[list[tuple[Any, str, str]]]]


async def zhang_heng_parse_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    spec_loader: SpecLoader,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """张衡 parse：读「应标要求」全部文件 → 解析 toc + doc_summary。

    state 写入：``stage="parsing"``，``spec.{doc_id, doc_title, doc_summary, toc}``。
    """
    import asyncio

    _check_cancel(state)

    project_id = state["project_id"]
    sources = await spec_loader(project_id)

    loop = asyncio.get_running_loop()

    def _sync_progress(step: str, current: int, total: int) -> None:
        # parse_document 跑在 to_thread 中，回调是 sync。把 emit 调度回事件循环线程。
        if emitter is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                emitter.emit(events.parse_progress(step, current, total)),
                loop,
            )
        except Exception:
            # emit 失败不影响解析主流程
            return

    parsed = await agent.parse(
        sources,
        progress_callback=_sync_progress,
    )

    # 解析完成后把章节总数同步给前端，作为 extract 阶段进度的上界，
    # 否则前端 totalBlocks 在 extract 第一帧前一直是 0。
    toc_total = len(parsed.toc)
    if toc_total:
        await _emit_frame(emitter, events.parse_progress("解析完成", toc_total, toc_total))

    # 持久化占位 blocks：让用户取消 / 刷新仍能看到已解析章节。
    # 失败不抛（落库不成功不影响 graph 主流程，仅意味着取消保存能力降级）。
    # 清掉历史遗留的"不在当前 toc"的 blocks（典型：旧 materials.py 路径写入的
    # outline-N 与新的 s1/s1.1 命名共存，导致前端列出双份、新的那一份全空显示骨架屏）。
    try:
        from db import get_db
        from services.block_store import sync_outline_placeholders

        async with get_db() as db:
            stats = await sync_outline_placeholders(
                db, project_id, parsed.toc, run_thread_id=state.get("thread_id"),
            )
        if stats["removed"]:
            logger.info(f"parse_node：清理 {stats['removed']} 条遗留 block（项目 {project_id}）")
    except Exception as e:
        logger.warning(f"parse_node：占位 block 落库失败（{e}），不阻断")

    toc_dicts = [_section_to_dict(s) for s in parsed.toc]
    return {
        "stage": "parsing",
        "spec": {
            "doc_id": parsed.doc_id,
            "doc_title": parsed.doc_title,
            "doc_summary": parsed.doc_summary,
            "doc_summary_error": parsed.doc_summary_error,
            # source_toc：正则解析出的规范书原始目录，只读、供 outline_draft 做 grounding。
            # toc 会在 outline_draft 节点被模型派生的应答目录整体替换。
            "source_toc": toc_dicts,
            "toc": toc_dicts,
        },
    }


# ─────────────────────────────────────────────
# 张衡 outline_draft 节点
# ─────────────────────────────────────────────

async def zhang_heng_outline_draft_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """张衡 outline_draft：依据规范书 + 用户提炼要求派生「应答文件目录」。

    state 写入：``stage="parsing"``，``spec.{toc, outline_revision, outline_error}``，
    并显式清空 ``spec.outline_matrix``（目录换了，旧的章节要求全部作废）。

    ``spec.source_toc`` 只读不动 —— 它是本节点的输入，也是降级时的兜底目录。

    与 parse 一样不抛：模型不可用 / 输出不可解析时沿用规范书原始目录，把原因写进
    ``outline_error``，照常进闸门 1 让用户看到并决定是否重出。
    """
    _check_cancel(state)

    project_id = state.get("project_id")
    spec = state.get("spec") or {}
    config = state.get("config") or {}

    source_toc = [_dict_to_section(d) for d in (spec.get("source_toc") or [])]
    # source_toc 缺失（老 checkpoint）时退回当前 toc —— 等效于「不派生的现状」
    if not source_toc:
        source_toc = [_dict_to_section(d) for d in (spec.get("toc") or [])]

    # 上一版目录：只有「已经派生过一次」（revision ≥ 1）才喂给模型，迭代才有连续性。
    # 首轮 spec.toc 就是 parse 写的规范书目录，不是用户看过的草稿，不能当 previous。
    prev_revision = int(spec.get("outline_revision") or 0)
    previous_toc = (
        [_dict_to_section(d) for d in (spec.get("toc") or [])] or None
    ) if prev_revision >= 1 else None

    revision = prev_revision + 1
    await _emit_frame(emitter, events.outline_draft_start(revision))

    result = await agent.draft_outline(
        source_toc,
        instruction=str(config.get("outline_instruction") or ""),
        doc_summary=str(spec.get("doc_summary") or ""),
        previous_toc=previous_toc,
    )

    toc_dicts = [_section_to_dict(s) for s in result.sections]

    # 占位 blocks 落库：id 整体换了一轮，旧行由 sync_outline_placeholders 清理
    #（只删 content 为空的行，已有正文的宁可留脏也不删）。失败不阻断主流程。
    if project_id is not None and result.sections:
        try:
            from db import get_db
            from services.block_store import sync_outline_placeholders

            async with get_db() as db:
                stats = await sync_outline_placeholders(
                    db, int(project_id), result.sections,
                    run_thread_id=state.get("thread_id"),
                )
            if stats["removed"]:
                logger.info(
                    f"outline_draft_node：清理 {stats['removed']} 条目录外 block"
                    f"（项目 {project_id}）"
                )
        except Exception as e:
            logger.warning(f"outline_draft_node：占位 block 落库失败（{e}），不阻断")

    await _emit_frame(emitter, events.outline_draft(
        toc=toc_dicts,
        revision=revision,
        degraded=result.degraded,
        error=result.error,
    ))

    return {
        "stage": "parsing",
        "spec": {
            "toc": toc_dicts,
            "outline_matrix": {},
            "outline_revision": revision,
            "outline_error": result.error,
        },
    }


# ─────────────────────────────────────────────
# 张衡 extract 节点
# ─────────────────────────────────────────────

# 「要求与大纲」分两步：① 按提炼要求拆分章节目录，② 逐节 8 字段提炼。
# 第二步暂时关闭，只做第一步 —— 目录结构还在反复调整，提炼出的矩阵随时作废，
# 而提炼是每个章节一次模型调用（一份 400+ 章的文档就是 400+ 次）。
#
# 恢复第二步：把这个常量改回 True 即可，graph.py 里 NODE_EXTRACT 的出边会跟着
# 从 END 切回 NODE_MATCH，不必两处各改一遍。
ENABLE_SECTION_EXTRACT = False


async def zhang_heng_extract_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """张衡 extract：8 字段提炼，逐章节流式推 outline_extract 事件。

    state 写入：``spec.outline_matrix``。

    第二步暂时关闭（见 ``ENABLE_SECTION_EXTRACT``）：关闭时本节点只清空矩阵并
    立刻返回，闸门 1 放行后图随即收尾，不再往下走到素材匹配 —— 匹配依赖这里
    产出的矩阵，空矩阵匹配不出东西。
    """
    _check_cancel(state)

    if not ENABLE_SECTION_EXTRACT:
        logger.info("extract 跳过：当前只做「按提炼要求拆分章节目录」")
        return {"spec": {"outline_matrix": {}}}

    spec = state.get("spec") or {}
    toc_raw = spec.get("toc") or []
    toc_full = [_dict_to_section(d) for d in toc_raw]
    project_id = state.get("project_id")

    # 章节 → 顺序索引：on_section 落库要写 order_idx，避免后端目录乱序
    section_order = {s.id: i for i, s in enumerate(toc_full)}

    matrix_dict: dict[str, dict] = {}

    # 跳过已提炼章节（continue/恢复场景）：从 blocks 表读 status='outline_done' 集合
    skip_ids: set[str] = set()
    if project_id is not None:
        try:
            from db import get_db
            from services.block_store import list_blocks

            async with get_db() as db:
                existing = await list_blocks(
                    db, int(project_id), run_thread_id=state.get("thread_id"),
                )
            for b in existing:
                if (b.get("status") == "outline_done") and (b.get("key_points") or b.get("requirement")):
                    skip_ids.add(str(b.get("block_id")))
                    # 把已完成行回填到 matrix_dict / state，让后续闸门快照与 emit 完整
                    matrix_dict[str(b.get("block_id"))] = {
                        "block_id": str(b.get("block_id")),
                        "title": b.get("title", ""),
                        "requirement": b.get("requirement", "") or "",
                        "key_points": b.get("key_points", "") or "",
                        "veto_items": b.get("veto_items", "") or "",
                        "bonus_items": b.get("bonus_items", "") or "",
                        "score_items": b.get("score_items", "") or "",
                        "evidence_required": b.get("evidence_required", "") or "",
                        "constraint_level": b.get("constraint_level", "recommended") or "recommended",
                        "indicators": b.get("indicators", "") or "",
                        "error": "",
                    }
        except Exception as e:
            logger.warning(f"extract_node：读已提炼 blocks 失败（{e}），全量重跑")
            skip_ids = set()

    toc = [s for s in toc_full if s.id not in skip_ids]
    if skip_ids:
        logger.info(f"extract_node：跳过 {len(skip_ids)} 个已提炼章节，剩余 {len(toc)}")

    async def _on_section_start(section: DomainSection) -> None:
        # 章节真正开始处理（worker 抢到信号量 + 调 LLM 前）：让前端把对应卡片切到"提炼中"。
        await _emit_frame(emitter, events.outline_extract_start(
            block_id=section.id, title=section.title or section.id,
        ))

    async def _on_section(section: DomainSection, row: OutlineMatrixRow) -> None:
        # 张衡每完成一章节立即推一帧，保证前端实时看到模块卡片增长。
        row_dict = _row_to_dict(row)
        matrix_dict[section.id] = row_dict
        await _emit_frame(emitter, events.outline_extract(
            block_id=section.id, title=row.title, matrix=row_dict,
        ))
        # 同步落库：取消 / 刷新后已提炼内容仍可在主页恢复显示
        if project_id is not None:
            try:
                from db import get_db
                from services.block_store import upsert_outline_block

                async with get_db() as db:
                    await upsert_outline_block(
                        db,
                        project_id=int(project_id),
                        block_id=section.id,
                        level=int(section.level or 1),
                        title=row.title or section.title or section.id,
                        order_idx=section_order.get(section.id, 0),
                        matrix=row_dict,
                        run_thread_id=state.get("thread_id"),
                    )
            except Exception as e:
                logger.warning(f"extract_node：block 落库失败（{section.id} / {e}），仅缓存到 state")

    matrix = await agent.extract(toc, on_section=_on_section, on_section_start=_on_section_start)

    # 回调可能因极端异常未触发，这里兜底一次：保证 matrix_dict 与 matrix 一致
    for block_id, row in matrix.items():
        if block_id not in matrix_dict:
            row_dict = _row_to_dict(row)
            matrix_dict[block_id] = row_dict
            await _emit_frame(emitter, events.outline_extract(
                block_id=block_id, title=row.title, matrix=row_dict,
            ))

    return {"spec": {"outline_matrix": matrix_dict}}


# ─────────────────────────────────────────────
# 沈括 match 节点
# ─────────────────────────────────────────────

async def shen_kuo_match_node(
    state: WorkflowState,
    *,
    agent: ShenKuoAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """沈括：素材匹配，逐 block 推 match_start / match_progress 事件。

    state 写入：``materials.matches``。
    """
    _check_cancel(state)

    spec = state.get("spec") or {}
    materials = state.get("materials") or {}
    toc = [_dict_to_section(d) for d in (spec.get("toc") or [])]
    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }
    chunks = list(materials.get("chunks") or [])
    project_id = state.get("project_id")

    matches_dict: dict[str, list[dict]] = {}

    async def _on_section_start(section: DomainSection) -> None:
        await _emit_frame(emitter, events.match_start(
            block_id=section.id, title=section.title or section.id,
        ))

    async def _on_section(section: DomainSection, mlist: list[Match]) -> None:
        serialized = [_match_to_dict(m) for m in mlist]
        matches_dict[section.id] = serialized
        await _emit_frame(emitter, events.match_progress(
            block_id=section.id, matches=serialized,
        ))
        # 落库 source 字段（用于刷新页面恢复显示）
        if project_id is not None:
            try:
                from db import get_db
                from services.block_store import update_block_source

                async with get_db() as db:
                    await update_block_source(
                        db,
                        project_id=int(project_id),
                        block_id=section.id,
                        sources=serialized,
                    )
            except Exception as e:
                logger.warning(f"match_node：source 落库失败（{section.id} / {e}），仅缓存到 state")

    # 测试 stub 可能不接受新 callback 参数 —— 用签名探测降级
    import inspect as _inspect
    try:
        sig = _inspect.signature(agent.match)
        supports_callbacks = "on_section" in sig.parameters
    except (TypeError, ValueError):
        supports_callbacks = False

    if supports_callbacks:
        matches = await agent.match(
            toc, outline_matrix, chunks,
            on_section=_on_section, on_section_start=_on_section_start,
        )
    else:
        matches = await agent.match(toc, outline_matrix, chunks)

    # 兜底：极端情况回调没触发，确保 state 完整
    for block_id, mlist in matches.items():
        if block_id not in matches_dict:
            serialized = [_match_to_dict(m) for m in mlist]
            matches_dict[block_id] = serialized
            await _emit_frame(emitter, events.match_progress(
                block_id=block_id, matches=serialized,
            ))

    return {"materials": {"matches": matches_dict}}


# ─────────────────────────────────────────────
# 诸葛亮 generate 节点
# ─────────────────────────────────────────────

async def zhuge_liang_generate_node(
    state: WorkflowState,
    *,
    agent: ZhugeLiangAgent,
    emitter: EmitterArg = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> WorkflowState:
    """诸葛亮：逐 block 生成（流式 token），并在已完成 block 上跳过。

    跳过策略（spec Task 4.4）：
    - 若 ``proposal.regenerate_targets`` 非空 → 仅跑这些 block，跑完清空 targets
    - 否则跑 outline_matrix 全部，但 ``proposal.blocks`` 已存在的 block 跳过

    state 写入：``proposal.blocks``（merge）, ``proposal.regenerate_targets=[]``。
    """
    _check_cancel(state)

    spec = state.get("spec") or {}
    materials = state.get("materials") or {}
    proposal = state.get("proposal") or {}

    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }
    matches_state = materials.get("matches") or {}
    matches: dict[str, list[Match]] = {
        bid: [_dict_to_match(m) for m in mlist]
        for bid, mlist in matches_state.items()
    }

    existing_blocks = dict(proposal.get("blocks") or {})
    regen_targets = list(proposal.get("regenerate_targets") or [])

    feedback_state = (state.get("review") or {}).get("feedback") or {}
    feedback: dict[str, list[dict]] = {
        bid: list((f or {}).get("issues") or [])
        for bid, f in feedback_state.items()
    }

    if regen_targets:
        # 重生模式：把指定 block 从 existing 移除，让 agent 重跑
        target_for_agent: list[str] = [
            bid for bid in regen_targets if bid in outline_matrix
        ]
    else:
        # 正向模式：按 toc 顺序生成（spec.outline_matrix 字典 key 顺序由完成顺序决定，
        # 不再是 toc 顺序，直接迭代会让生成顺序与文档章节脱节）。
        toc_for_order = state.get("spec", {}).get("toc") or []
        ordered_ids = [s.get("id") for s in toc_for_order if s.get("id") in outline_matrix]
        # toc 之外的兜底（理论上不会有）
        for bid in outline_matrix.keys():
            if bid not in ordered_ids:
                ordered_ids.append(bid)
        target_for_agent = [
            bid for bid in ordered_ids if bid not in existing_blocks
        ]

    if not target_for_agent:
        # 全部已完成（resume 后无新 block）
        return {"proposal": {
            "blocks": existing_blocks,
            "regenerate_targets": [],
            "updated_blocks": [],
        }}

    # 视图过滤：传给 agent 的 outline_matrix 仅含待生成 block
    sub_matrix = {bid: outline_matrix[bid] for bid in target_for_agent}

    # 测试 stub agent 可能没声明 should_cancel / feedback 参数 → 用签名探测降级
    import inspect as _inspect
    try:
        sig = _inspect.signature(agent.generate)
        supports_should_cancel = "should_cancel" in sig.parameters
        supports_feedback = "feedback" in sig.parameters
    except (TypeError, ValueError):
        supports_should_cancel = False
        supports_feedback = False

    bridge = _make_agent_emitter_bridge(emitter)
    call_kwargs: dict = {
        "outline_matrix": sub_matrix,
        "materials": matches,
        "regenerate_targets": target_for_agent if regen_targets else None,
        "emitter": bridge,
    }
    if supports_feedback:
        call_kwargs["feedback"] = feedback
    if supports_should_cancel:
        call_kwargs["should_cancel"] = should_cancel

    new_blocks, _consumed = await agent.generate(**call_kwargs)

    # 合并：重生覆盖已存在；正向只新增
    merged_blocks = dict(existing_blocks)
    for bid, output in new_blocks.items():
        merged_blocks[bid] = _block_output_to_dict(output)
        await _emit_frame(emitter, events.block_done(
            block_id=bid,
            content=output.content,
            sources=[s.to_dict() if hasattr(s, "to_dict") else s
                     for s in output.sources],
            outline={
                "outline_text": output.outline,
                "needs_diagram": output.needs_diagram,
            },
        ))

    # 将每轮正文及修订写入工作台已有存储，导出/手改/历史使用同一份内容。
    if new_blocks and state.get("project_id") and state.get("thread_id"):
        from db import get_db
        from services.block_store import list_blocks, update_block_content, add_revision, list_revisions
        async with get_db() as db:
            db_rows = await list_blocks(
                db, state["project_id"], run_thread_id=state.get("thread_id"),
            )
            db_blocks = {b["block_id"]: b for b in db_rows}
            for bid, output in new_blocks.items():
                row = db_blocks.get(bid)
                if row is None or row.get("content") == output.content:
                    continue
                if row.get("content") and not await list_revisions(db, row["id"]):
                    await add_revision(db, row["id"], row["content"], "修订前正文", "baseline")
                await update_block_content(db, row["id"], output.content)
                await add_revision(db, row["id"], output.content,
                                   f"第 {int(state.get('iteration') or 0) + 1} 轮 AI 撰写/修订", "generate")

    # 检测用户暂停：should_cancel 真 → 标 stage='paused' + emit paused 事件
    paused = bool(should_cancel and should_cancel())
    if paused:
        await _emit_frame(emitter, events.paused(
            reason="user_pause",
            generated=len(merged_blocks),
            total=len(outline_matrix),
        ))
        return {
            "stage": "paused",
            "proposal": {
                "blocks": merged_blocks,
                "regenerate_targets": [],
            },
        }

    return {
        "stage": "generating",
        "proposal": {
            "blocks": merged_blocks,
            "regenerate_targets": [],
            "updated_blocks": list(new_blocks.keys()),
        },
    }


# ─────────────────────────────────────────────
# 王安石 / 包拯：评审节点
# ─────────────────────────────────────────────

async def wang_anshi_review_node(
    state: WorkflowState,
    *,
    agent: WangAnshiAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """王安石：技术评审；逐 block 推 review_finding 事件。"""
    _check_cancel(state)
    return await _run_review(
        state, agent=agent, emitter=emitter,
        finding_field="tech_findings", agent_name="wang_anshi",
    )


async def bao_zheng_review_node(
    state: WorkflowState,
    *,
    agent: BaoZhengAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """包拯：合规评审；逐 block 推 review_finding 事件。"""
    _check_cancel(state)
    return await _run_review(
        state, agent=agent, emitter=emitter,
        finding_field="compliance_findings", agent_name="bao_zheng",
    )


async def _run_review(
    state: WorkflowState,
    *,
    agent: Union[WangAnshiAgent, BaoZhengAgent],
    emitter: EmitterArg,
    finding_field: str,
    agent_name: str,
) -> WorkflowState:
    spec = state.get("spec") or {}
    proposal = state.get("proposal") or {}

    blocks_state = proposal.get("blocks") or {}
    blocks: dict[str, BlockOutput] = {
        bid: _dict_to_block_output(d) for bid, d in blocks_state.items()
    }
    # 只复审本轮真正重跑过的 block：不只是省 token —— 同一段未改动的正文
    # 重复评审会因 LLM 采样随机性给出不同分数，低分会被误判为未达标并触发
    # 无意义的回炉，甚至在两轮之间振荡。老 checkpoint 无该字段时复审全部。
    updated = proposal.get("updated_blocks")
    if updated:
        updated_set = set(updated)
        blocks = {bid: blk for bid, blk in blocks.items() if bid in updated_set}
    outline_matrix = {
        bid: _dict_to_row(d)
        for bid, d in (spec.get("outline_matrix") or {}).items()
    }

    # 桥接 agent 内部的 review_block_start/done 事件到 SSE：让前端实时看到逐 block
    # "正在评审 → 已完成"的过程。
    async def _review_bridge(event_type: str, payload: dict) -> None:
        if event_type == "review_block_start":
            row = outline_matrix.get(payload.get("block_id", ""))
            title = row.title if row else ""
            await _emit_frame(emitter, events.review_block_start(
                block_id=payload.get("block_id", ""),
                agent=agent_name,
                title=title,
            ))
        elif event_type == "review_block_done":
            await _emit_frame(emitter, events.review_finding(
                block_id=payload.get("block_id", ""),
                agent=agent_name,
                score=float(payload.get("score") or 0),
                issues=payload.get("issues") or [],
            ))

    findings = await agent.review(
        blocks=blocks,
        outline_matrix=outline_matrix,
        emitter=_review_bridge,
    )

    findings_dict: dict[str, dict] = {}
    for block_id, finding in findings.items():
        # review_finding 已在 _review_bridge 内逐 block emit，这里仅累计 state patch
        findings_dict[block_id] = finding.to_dict()

    # 持久化到 reviews 表：刷新 review 页面 / 跨进程恢复时仍能读到完整评审数据
    thread_id = state.get("thread_id")
    if thread_id and findings:
        try:
            from db import get_db
            from domain.review import ReviewRepository

            async with get_db() as db:
                repo = ReviewRepository(db)
                for finding in findings.values():
                    await repo.add_finding(thread_id, finding)
        except Exception as e:
            logger.warning(f"_run_review：finding 落库失败（{agent_name} / {e}），仅缓存 state")

    # 必须并入已有 findings，不能整体替换：review 的 reducer 是浅 merge，
    # 返回 {"tech_findings": findings_dict} 会把未复审 block 的历史 finding 一并
    # 抹掉。那样 check_convergence 读到缺失 block 的 score=0，误判未达标并再次
    # 回炉，两个 block 交替被清空 —— 收敛判定来回振荡直到迭代上限。
    existing = dict((state.get("review") or {}).get(finding_field) or {})
    existing.update(findings_dict)

    content_field = "tech_contents" if finding_field == "tech_findings" else "compliance_contents"
    reviewed_contents = dict((state.get("review") or {}).get(content_field) or {})
    reviewed_contents.update({bid: blocks[bid].content for bid in findings if bid in blocks})
    return {"review": {finding_field: existing, content_field: reviewed_contents}}


# ─────────────────────────────────────────────
# aggregate_review 节点：合成 GlobalReport
# ─────────────────────────────────────────────

# 严重度数值化，用于 top_risks 排序
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


async def aggregate_review_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """聚合王安石 + 包拯的 findings 为 GlobalReport（spec §5）。

    state 写入：``review.report``。
    """
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}

    # per_block：tech_score / comp_score / severity_counts（合并两侧 issues）
    per_block: dict[str, dict] = {}
    all_block_ids = set(tech.keys()) | set(comp.keys())
    for bid in sorted(all_block_ids):
        t = tech.get(bid) or {}
        c = comp.get(bid) or {}
        sev_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for issue in (t.get("issues") or []) + (c.get("issues") or []):
            sev = (issue.get("severity") or "medium").lower()
            if sev in sev_counts:
                sev_counts[sev] += 1
        per_block[bid] = {
            "tech_score": int(t.get("score") or 0),
            "comp_score": int(c.get("score") or 0),
            "severity_counts": sev_counts,
        }

    # 总分：所有 (tech + comp) / 2 的算术平均（block 数为 0 时为 0）
    if per_block:
        total = sum(
            (b["tech_score"] + b["comp_score"]) / 2.0 for b in per_block.values()
        ) / len(per_block)
    else:
        total = 0.0

    # top_risks：按 (severity_rank, block_id) 取前 5 条 critical/high issue.point
    risks: list[tuple[int, str, str]] = []
    for bid in sorted(all_block_ids):
        for finding in (tech.get(bid), comp.get(bid)):
            if not finding:
                continue
            for issue in (finding.get("issues") or []):
                rank = _SEVERITY_RANK.get(
                    (issue.get("severity") or "medium").lower(), 0,
                )
                if rank >= _SEVERITY_RANK["high"]:
                    risks.append((rank, bid, issue.get("point") or ""))
    risks.sort(key=lambda x: (-x[0], x[1]))
    top_risks = [f"[{bid}] {point}" for _, bid, point in risks[:5] if point]

    # missing_evidence / missing_bonus：从 spec.outline_matrix 与 proposal.blocks 对比
    missing_evidence: list[str] = []
    missing_bonus: list[str] = []
    spec = state.get("spec") or {}
    matrix = spec.get("outline_matrix") or {}
    proposal_blocks = (state.get("proposal") or {}).get("blocks") or {}
    for bid, row in matrix.items():
        body = (proposal_blocks.get(bid) or {}).get("content") or ""
        if (row.get("evidence_required") or "").strip() and not body.strip():
            missing_evidence.append(f"{bid}：{row.get('title', '')}")
        if (row.get("bonus_items") or "").strip() and not body.strip():
            missing_bonus.append(f"{bid}：{row.get('title', '')}")

    report = GlobalReport(
        per_block=per_block,
        total_score=round(float(total), 2),
        top_risks=top_risks,
        missing_evidence=missing_evidence,
        missing_bonus=missing_bonus,
    )
    report_dict = report.to_dict()

    await _emit_frame(emitter, events.report_ready(report_dict))

    return {
        "stage": "report_review",
        "review": {"report": report_dict},
    }


# ─────────────────────────────────────────────
# collect_gaps 节点：调度收集角色补料（spec §4.3）
# ─────────────────────────────────────────────

async def collect_gaps_node(
    state: WorkflowState,
    *,
    agent: ShenKuoAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """把补料请求交给沈括，新素材并回 materials.matches。

    位于 zhuge_liang_generate **之前**，因此两个来源的请求
    （编写主动要料 + build_feedback 的审核要料）都在下一轮生成前就位。

    无请求时空操作，不写任何字段。
    """
    _check_cancel(state)

    requests_by_block = (state.get("proposal") or {}).get("material_requests") or {}
    if not requests_by_block:
        return {}

    materials = state.get("materials") or {}
    chunks = materials.get("chunks") or []

    # 无语料：清空请求直接放行（否则请求会累积到下一轮重复触发）
    if not chunks:
        return {"proposal": {"material_requests": {}}}

    await _emit_frame(emitter, events.iteration_start(int(state.get("iteration") or 0)))
    for block_id, reqs in requests_by_block.items():
        for req in (reqs or []):
            await _emit_frame(emitter, events.gaps_collecting(
                block_id, req.get("query", ""),
            ))

    errors: list[dict] = []
    try:
        new_matches = await agent.retrieve_for(requests_by_block, chunks)
    except Exception as e:
        logger.warning(f"collect_gaps：补料检索失败，保留原 matches 继续：{e}")
        new_matches = {}
        errors.append({
            "agent": "collect_gaps",
            "message": f"补料检索失败：{e}",
            "timestamp": _now_iso(),
            "retryable": True,
        })

    existing = dict(materials.get("matches") or {})
    # 用 existing 打底，而不是只装新命中的 block：patch 会整体替换
    # materials.matches（_merge_dict 是浅合并，不递归）。只装新命中项的话，
    # 未提补料需求的 block 素材会被一并抹掉 —— 而这类 block 很常见：
    # build_feedback 只在存在 needs_material issue 时才登记 material_requests，
    # 于是"分数低但问题不属于缺材料"的 block 会进回炉名单却不进补料名单，
    # 结果下一轮在零参考素材下重新生成。
    merged: dict[str, list[dict]] = {bid: list(ms) for bid, ms in existing.items()}
    changed = False
    for block_id, matches in new_matches.items():
        old = list(existing.get(block_id) or [])
        seen = {m.get("chunk_id") for m in old}
        added = [m for m in matches if m.chunk_id not in seen]
        if not added:
            continue
        merged[block_id] = old + [_match_to_dict(m) for m in added]
        changed = True
        await _emit_frame(emitter, events.gaps_done(
            block_id, len(added), len(merged[block_id]),
        ))

    patch: dict = {"proposal": {"material_requests": {}}}
    # 关键：没有新命中时绝不能写 materials.matches —— _merge_dict 是浅合并。
    # 此时 merged 与 existing 等值，写回去语义无害，但会让"没找到新素材"在
    # state 上消失，且旧写法下写 {"matches": {}} 会直接清空全部已有匹配。
    if changed:
        patch["materials"] = {"matches": merged}
    if errors:
        patch["errors"] = errors
    return patch


# ─────────────────────────────────────────────
# check_convergence 节点：逐 block 收敛判定（spec §5.1）
# ─────────────────────────────────────────────

async def check_convergence_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """纯判定：不调 LLM、不改业务数据，只写 review.convergence。"""
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}
    iteration = int(state.get("iteration") or 0)

    unconverged: list[str] = []
    review_failed = False

    scope = (state.get("proposal") or {}).get("revision_scope")
    review_ids = set(tech) | set(comp)
    if state.get("user_choice") == "regen_blocks" and scope:
        review_ids &= set(scope)
    for block_id in sorted(review_ids):
        t = tech.get(block_id) or {}
        c = comp.get(block_id) or {}

        # 评审本身失败是基础设施故障，不是内容问题 —— 回炉解决不了，
        # 反而会烧光迭代次数。排除在未达标之外，直接交人工终审。
        if t.get("error") or c.get("error"):
            review_failed = True
            continue

        tech_score = int(t.get("score") or 0)
        comp_score = int(c.get("score") or 0)
        has_critical = any(
            str(i.get("severity") or "").lower() == "critical"
            for i in list(t.get("issues") or []) + list(c.get("issues") or [])
        )

        if (
            tech_score < REVIEW_SCORE_THRESHOLD
            or comp_score < REVIEW_SCORE_THRESHOLD
            or has_critical
        ):
            unconverged.append(block_id)

    if not unconverged:
        status = "converged"
        reason = "review_failed_ignored" if review_failed else ""
    elif iteration >= MAX_ITERATIONS:
        status = "max_iterations"
        reason = f"已达迭代上限 {MAX_ITERATIONS} 轮"
    else:
        status = "refine"
        reason = "存在未达标 block"

    convergence = {
        "status": status,
        "unconverged_blocks": unconverged,
        "reason": reason,
        "iteration": iteration,
        "review_failed": review_failed,
    }
    await _emit_frame(emitter, events.convergence(convergence))
    return {"review": {"convergence": convergence}}


# ─────────────────────────────────────────────
# build_feedback 节点：把审核意见转译成两路（spec §4.4）
# ─────────────────────────────────────────────

async def build_feedback_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """转译：给编写的修订指令 + 给收集的补料请求。纯转译，不调 LLM。

    refine 模式：写全部四个字段（feedback / material_requests /
                 regenerate_targets / iteration）。
    max_iterations 模式：只写前两个，不回炉、不递增，
                 由 graph 的条件边直连闸门 3（spec §5.3）。
    """
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}
    convergence = review.get("convergence") or {}
    status = convergence.get("status", "")
    targets = list(convergence.get("unconverged_blocks") or [])
    iteration = int(state.get("iteration") or 0)

    feedback: dict[str, dict] = {}
    material_requests: dict[str, list[dict]] = {}

    for block_id in targets:
        t = tech.get(block_id) or {}
        c = comp.get(block_id) or {}
        issues = list(t.get("issues") or []) + list(c.get("issues") or [])

        feedback[block_id] = {
            "issues": issues,
            "scores": {
                "tech": int(t.get("score") or 0),
                "comp": int(c.get("score") or 0),
            },
        }

        reqs: list[dict] = []
        for issue in issues:
            if not issue.get("needs_material"):
                continue
            query = (issue.get("material_query") or "").strip() or \
                    (issue.get("point") or "").strip()
            if not query:
                continue
            reqs.append({"query": query, "reason": issue.get("point") or ""})
        if reqs:
            material_requests[block_id] = reqs

    patch: dict = {
        "review": {"feedback": feedback},
        "proposal": {"material_requests": material_requests},
        "errors": [],
    }

    if status == "refine":
        if iteration >= MAX_ITERATIONS:
            # 断言式兜底：check_convergence 的判定若被改坏，最坏也只是重复一轮，
            # 不会无限回环。改写 status 让 graph 的 _route_feedback 直连闸门。
            patch["review"]["convergence"] = {
                **convergence,
                "status": "max_iterations",
                "reason": f"迭代已达上限 {MAX_ITERATIONS} 仍收到 refine，强制收敛",
            }
            patch["errors"].append({
                "agent": "build_feedback",
                "message": f"迭代已达上限 {MAX_ITERATIONS} 仍收到 refine，拒绝回炉",
                "timestamp": _now_iso(),
                "retryable": False,
            })
        else:
            patch["proposal"]["regenerate_targets"] = targets
            patch["iteration"] = iteration + 1

    await _emit_frame(emitter, events.feedback_ready({
        "targets": targets,
        "material_request_count": sum(len(v) for v in material_requests.values()),
        "iteration": iteration,
    }))
    return patch


# ─────────────────────────────────────────────
# domain ↔ dict 互转
# ─────────────────────────────────────────────

def _section_to_dict(s: DomainSection) -> dict:
    return {
        "id": s.id,
        "level": s.level,
        "title": s.title,
        "raw_content": s.raw_content,
        "special_marks": list(s.special_marks),
    }


def _dict_to_section(d: dict) -> DomainSection:
    return DomainSection(
        id=d.get("id", ""),
        level=int(d.get("level", 0) or 0),
        title=d.get("title", ""),
        raw_content=d.get("raw_content", ""),
        special_marks=list(d.get("special_marks", []) or []),
    )


def _row_to_dict(r: OutlineMatrixRow) -> dict:
    return {
        "block_id": r.block_id,
        "title": r.title,
        "requirement": r.requirement,
        "key_points": r.key_points,
        "veto_items": r.veto_items,
        "bonus_items": r.bonus_items,
        "score_items": r.score_items,
        "evidence_required": r.evidence_required,
        "constraint_level": r.constraint_level,
        "indicators": r.indicators,
        "error": r.error,
    }


def _dict_to_row(d: dict) -> OutlineMatrixRow:
    return OutlineMatrixRow(
        block_id=d.get("block_id", ""),
        title=d.get("title", ""),
        requirement=d.get("requirement", ""),
        key_points=d.get("key_points", ""),
        veto_items=d.get("veto_items", ""),
        bonus_items=d.get("bonus_items", ""),
        score_items=d.get("score_items", ""),
        evidence_required=d.get("evidence_required", ""),
        constraint_level=d.get("constraint_level", "recommended"),
        indicators=d.get("indicators", ""),
        error=d.get("error", ""),
    )


def _match_to_dict(m: Match) -> dict:
    return {
        "chunk_id": m.chunk_id,
        "score": float(m.score),
        "reason": m.reason,
        "hit_points": list(m.hit_points or []),
    }


def _dict_to_match(d: dict) -> Match:
    return Match(
        chunk_id=d.get("chunk_id"),
        score=float(d.get("score", 0.0) or 0.0),
        reason=d.get("reason", ""),
        hit_points=list(d.get("hit_points") or []),
    )


def _block_output_to_dict(b: BlockOutput) -> dict:
    return {
        "block_id": b.block_id,
        "kind": b.kind,
        "needs_diagram": bool(b.needs_diagram),
        "outline": b.outline,
        "content": b.content,
        "sources": [s.to_dict() if hasattr(s, "to_dict") else s for s in b.sources],
    }


def _dict_to_block_output(d: dict) -> BlockOutput:
    from domain.proposal import Source

    sources_raw = d.get("sources") or []
    sources: list[Source] = []
    for s in sources_raw:
        if isinstance(s, Source):
            sources.append(s)
        elif isinstance(s, dict):
            sources.append(Source(
                material_id=int(s.get("material_id") or 0),
                chunk_index=int(s.get("chunk_index") or 0),
                snippet=s.get("snippet", ""),
            ))
    return BlockOutput(
        block_id=d.get("block_id", ""),
        kind=d.get("kind", "tech"),
        needs_diagram=bool(d.get("needs_diagram", False)),
        outline=d.get("outline", ""),
        content=d.get("content", ""),
        sources=sources,
    )


__all__ = [
    "WorkflowCancelled",
    "zhang_heng_parse_node",
    "zhang_heng_extract_node",
    "shen_kuo_match_node",
    "zhuge_liang_generate_node",
    "wang_anshi_review_node",
    "bao_zheng_review_node",
    "aggregate_review_node",
    "collect_gaps_node",
    "check_convergence_node",
    "build_feedback_node",
]
