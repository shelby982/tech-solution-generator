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

# 文件源加载回调（routes 在 start 时注入）：返回 [(binary_io, suffix, filename[, material_id]), ...]
# 列表 = 「应标要求」面板下的全部文件，提炼不绑定技术规范书。
# 第 4 项 material_id 是**本次运行绑定哪几份文件**的依据：解析时记进 checkpoint，
# 重跑派生按 id 原样取回，与项目当前材料表无关。
SpecLoader = Callable[..., Awaitable[list[tuple]]]


async def materials_signature(project_id: int) -> str:
    """「应标要求」文件的指纹：角色 + 文件名 + 大小，排序后拼接。

    为什么要它：`redraft_outline` 只重跑"目录派生"，原料是 checkpoint 里的
    ``spec.source_toc``（上一次解析的结果）。用户删掉旧文件、传了新文件之后点重拆，
    目录仍然按**旧解析结果**生成 —— 实测因此出现"页面还是之前的目录"，
    而用户以为新文件已经生效。这里给解析结果盖一个指纹，重拆前比对，
    不一致就明确要求重新提炼（重跑解析）。
    """
    from db import get_db

    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT role, filename, COALESCE(file_size, 0) AS sz "
            "FROM materials WHERE project_id = ? "
            "ORDER BY role, filename, sz",
            (project_id,),
        )
        rows = await cursor.fetchall()
    return "|".join(f"{r['role']}:{r['filename']}:{r['sz']}" for r in rows)


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
    # 优先带 material_id 取（新契约）：解析结果里就能记住"这次用了哪几份文件"。
    # 旧 loader 只接受一个位置参数 → 退回旧调用，行为不变。
    try:
        selected_ids = (state.get('config') or {}).get('material_ids')
        if selected_ids is not None:
            sources = await spec_loader(project_id, with_ids=True, material_ids=selected_ids)
        else:
            sources = await spec_loader(project_id, with_ids=True)
    except TypeError:
        sources = await spec_loader(project_id)

    def _material_id_of(src) -> int | None:
        if isinstance(src, (tuple, list)) and len(src) >= 4:
            try:
                return int(src[3])
            except (TypeError, ValueError):
                return None
        return None

    material_ids = [mid for mid in (_material_id_of(s) for s in sources) if mid]

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
        [tuple(s)[:3] for s in sources],      # parse 只吃 (io, suffix, filename)
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
    # 有哪些要求文件没能参与（解析失败 / 0 章节）：闸门 1 要能告诉用户，
    # 否则用户以为所有上传的文件都被用来派生目录了。
    failed_files = list(getattr(parsed, "failed_files", []) or [])
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
            "parse_failed_files": failed_files,
            # 本次解析所依据的材料指纹：重出目录前比对，原料换了就必须重跑解析。
            # material_files 是同一事实的文件名视图 —— checklist 接口用它做兜底比较，
            # 也能让**改动前创建的旧 run**（没有 material_sig）被正确判定为"原料已变"。
            "material_sig": await materials_signature(project_id),
            "material_files": [s.get("source_file") or "" for s in toc_dicts],
            # 本次运行**绑定**的材料 id：重跑派生按它取回原批文件（含已软删的），
            # 于是"重新上传"不会影响旧 run，旧 run 也不会拿到新文件。
            "material_ids": material_ids,
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

    ``spec.source_toc`` 只读不动 —— 它是本节点的输入（也是「重新拆分」时再次派生的
    依据），但**不再作为失败兜底**。

    **失败即抛**：模型不可用 / 输出不可解析 / 一级章节太少时，``agent.draft_outline``
    抛 ``OutlineDraftError``，本节点不兜底、原样上抛；由 runner 把这次运行标成
    ``stage="failed"`` 并把原因写给前端。静默改用规范书原始目录会让用户以为派生成功
    —— 那份目录并没有按他的提炼要求拆。
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
        project_id=project_id,
        thread_id=str(state.get("thread_id") or ""),
        points_only=bool(config.get("points_only")),
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
        error=result.error,
    ))

    # ── 只拆评分点：不产出目录，停在「评分点」这一步 ──
    # 用户要求的两步：先拆评分点 → 用户选择类别 → 再生成目录。
    if getattr(result, "points_only", False):
        new_spec = dict(spec)
        new_spec.update({
            "points_analyzed": True,
            "outline_pending": True,          # 图路由据此直接结束，不去闸门 1
            "outline_coverage": dict(result.coverage or {}),
            "outline_instruction": str(config.get("outline_instruction") or ""),
            "points_note": result.error,
        })
        await _emit_frame(emitter, events.outline_draft_start(revision - 1))  # 撤回"派生中"
        return {"stage": "points_review", "spec": new_spec}

    return {
        "stage": "parsing",
        "spec": {
            "toc": toc_dicts,
            "outline_matrix": {},
            "outline_revision": revision,
            "outline_error": result.error,
            # **本次实际用于派生的提炼要求**。前端靠比对它与输入框内容来判断
            # 「这段提炼要求有没有用上」——此前从不写它，于是用过之后页面仍然提示
            # "这段提炼要求还没有用于生成目录"，用户以为点了没生效（实测反馈）。
            "outline_instruction": str(config.get("outline_instruction") or ""),
            # 覆盖率自检：评分点/规范要求的落点与分值合计（闸门展示，缺什么一眼可见）
            "outline_coverage": dict(result.coverage or {}),
            # 真正生成目录后清掉"待生成"标记（否则图会立刻结束、到不了闸门 1）
            "outline_pending": False,
        },
    }


# ─────────────────────────────────────────────
# 张衡 extract 节点
# ─────────────────────────────────────────────

# 「要求与大纲」分两步：① 按提炼要求拆分章节目录，② 逐节 8 字段提炼。
# 第二步由闸门 1 的「确认目录，开始提炼要点」触发，用户认可目录结构后才付提炼的账
# —— 提炼是每个章节一次模型调用（一份 400+ 章的文档就是 400+ 次），目录还得先让人改。
#
# 关闭本开关：NODE_EXTRACT 出边自动从 NODE_MATCH 切回 END，闸门 1 放行后图直接收尾。
ENABLE_SECTION_EXTRACT = True


async def zhang_heng_extract_node(
    state: WorkflowState,
    *,
    agent: ZhangHengAgent,
    emitter: EmitterArg = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> WorkflowState:
    """张衡 extract：8 字段提炼，逐章节流式推 outline_extract 事件。

    ``should_cancel``：runner 传进来的 _Run.cancel_flag 闭包。**必须逐节检查** ——
    否则用户点了「取消」，这一节点仍会把 400 章全部提炼完（每章一次模型调用），
    取消等于没点（实测：abort 之后又跑了 8 节）。真取消时节点把已完成的部分写回
    state 并标 ``stage='paused'``，由 runner 收尾成 paused（不是 done）。

    state 写入：``spec.outline_matrix``。

    第二步暂时关闭（见 ``ENABLE_SECTION_EXTRACT``）：关闭时本节点只清空矩阵并
    立刻返回，闸门 1 放行后图随即收尾，不再往下走到素材匹配 —— 匹配依赖这里
    产出的矩阵，空矩阵匹配不出东西。
    """
    _check_cancel(state)

    if not ENABLE_SECTION_EXTRACT:
        logger.info("extract 跳过：当前只做「按提炼要求拆分章节目录」")
        return {"spec": {"outline_matrix": {}}}

    # 逐节取消信号：优先用 runner 的实时 flag（节点拿到的 state 是快照，
    # cancel_requested 在节点跑的过程中不会变），测试里没有就退回快照值。
    def _cancelled() -> bool:
        if should_cancel is not None:
            return bool(should_cancel())
        return bool(state.get("cancel_requested"))

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
                bid = str(b.get("block_id"))
                kind = b.get("error_kind") or ""
                # 失败的行**不跳过** → 下一轮（「继续提炼」）会重试它。以前它也是
                # outline_done，于是"继续提炼"永远不重试、界面还显示已提炼。
                if kind == "failed":
                    continue
                if b.get("status") != "outline_done":
                    continue
                # 'no_source'（无原文）与正常成功的行都跳过；无原文重试也只是再走一次
                # 空短路，没有模型调用，跳过纯粹是省掉一次往返。
                if kind == "no_source" or b.get("key_points") or b.get("requirement"):
                    skip_ids.add(bid)
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
                        "error": b.get("error", "") or "",
                        "error_kind": b.get("error_kind", "") or "",
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
                        # row_dict 里带 error / error_kind：失败章节的**原因**要落库，
                        # 否则刷新后只剩一张空卡片，用户无从判断（以前就是这样）
                        matrix=row_dict,
                        run_thread_id=state.get("thread_id"),
                    )
            except Exception as e:
                logger.warning(f"extract_node：block 落库失败（{section.id} / {e}），仅缓存到 state")

    # stub agent 可能没声明 should_cancel → 签名探测降级（同 generate 节点的做法）
    import inspect as _inspect
    try:
        _sig = _inspect.signature(agent.extract)
        _supports_cancel = "should_cancel" in _sig.parameters
    except (TypeError, ValueError):
        _supports_cancel = False
    _extract_kwargs: dict = {"on_section": _on_section, "on_section_start": _on_section_start}
    if _supports_cancel:
        _extract_kwargs["should_cancel"] = _cancelled
    matrix = await agent.extract(toc, **_extract_kwargs)

    # 回调可能因极端异常未触发，这里兜底一次：保证 matrix_dict 与 matrix 一致
    for block_id, row in matrix.items():
        if block_id not in matrix_dict:
            row_dict = _row_to_dict(row)
            matrix_dict[block_id] = row_dict
            await _emit_frame(emitter, events.outline_extract(
                block_id=block_id, title=row.title, matrix=row_dict,
            ))

    # 清掉匹配触发标志：extract 的出边是条件边，读它决定要不要接 match。走到这里
    # 说明用户只要求提炼，收尾停在「要求与大纲」；素材匹配由素材库的「素材匹配」
    # 按钮（rerun_match 重新置真）触发。
    failed_ids = [bid for bid, r in matrix_dict.items() if (r.get("error_kind") or "") == "failed"]
    patch: WorkflowState = {"spec": {"outline_matrix": matrix_dict}, "match_requested": False}
    if failed_ids:
        # run 级信号：失败章节数写进 errors，状态栏/排查都能看到（以前只在单帧里出现一次）
        patch["errors"] = [{
            "message": f"{len(failed_ids)} 个章节提炼失败（可点「继续提炼」重试）："
                       + "、".join(failed_ids[:5]) + ("…" if len(failed_ids) > 5 else ""),
            "retryable": True,
        }]
        logger.warning(f"extract：{len(failed_ids)} 个章节提炼失败：{failed_ids}")
    if _cancelled():
        # 用户点了取消：已完成的部分照常写回（blocks 表里逐节的落库也在），
        # 但**不能**标成跑完 —— 否则页面显示「提炼完成」而后面还有章节没提炼。
        logger.info(f"extract 被取消：已完成 {len(matrix_dict)} 个章节，收尾为 paused")
        patch["stage"] = "paused"
    return patch


# ─────────────────────────────────────────────
# 沈括 match 节点
# ─────────────────────────────────────────────

# 匹配模式：
#   assign = **素材 → 章节 归属**（默认）。规则（片段首行标题 ↔ 章节标题）+ 语义（BM25 反向分配），
#            **零模型调用**，产出带素材原文/出处的依据（见 domain/material/models.py 的 MaterialEvidence）。
#   rerank = 旧的逐章 BM25 top-k + LLM 重排（调用数 = O(章节数)，且只留评语不留原文）。
#            保留用于回滚/对照，默认不用。
MATCH_MODE = "assign"

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
    project_id = state.get("project_id")
    chunks: list[dict] = []
    if project_id is not None:
        # **根因修复**：标准流程（闸门1 → 提炼 → 素材匹配）里从来没人往 state 放 chunks
        # （只有工作台局部修订的 workspace_action 会放），于是本节点一直走 "not chunks" 短路：
        # 每章都返回空匹配、零调用，界面却显示"33/33 章节已完成" —— 用户以为匹配过了。
        # 这里改为**从库里按来源分池取**：我方素材 + 招标文件（后者用途受限，见 assign.py）。
        try:
            from db import get_db
            from services.block_store import (
                OURS_MATERIAL_ROLES,
                TENDER_MATERIAL_ROLES,
                list_chunks_with_filename,
            )

            async with get_db() as db:
                ours = await list_chunks_with_filename(
                    db, int(project_id), roles=OURS_MATERIAL_ROLES)
                tender = await list_chunks_with_filename(
                    db, int(project_id), roles=TENDER_MATERIAL_ROLES)
            chunks = ours + tender
            logger.info(
                f"match_node：从库里取素材片段 {len(chunks)} 条"
                f"（我方 {len(ours)} / 招标 {len(tender)}）"
            )
        except Exception as e:
            logger.warning(f"match_node：取素材片段失败（{e}），回退用 state 缓存")
            chunks = []
    if not chunks:
        # 兜底：没有 project_id（单测）或读库失败时用 state 缓存。
        # **注意不能反过来**：素材是图外上传/解析的，state 里的 chunks 是上一轮的快照，
        # 优先用它会导致"新上传的素材永远进不了池子"（实测踩过：增量匹配只看到旧的 16 条）。
        chunks = list(materials.get("chunks") or [])

    matches_dict: dict[str, list[dict]] = {}

    async def _on_section_start(section: DomainSection) -> None:
        await _emit_frame(emitter, events.match_start(
            block_id=section.id, title=section.title or section.id,
        ))

    async def _on_section(section: DomainSection, rows: list[dict]) -> None:
        """section 完成：写 state、推帧、落库。rows 是**已序列化**的行（两条路径共用）。"""
        serialized = rows
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

    if MATCH_MODE == "assign":
        return await _match_by_assignment(
            state, toc=toc, outline_matrix=outline_matrix, chunks=chunks,
            matches_dict=matches_dict, emitter=emitter,
            on_section=_on_section, on_section_start=_on_section_start,
        )

    # ── 旧路径：逐章 BM25 + LLM 重排（MATCH_MODE="rerank" 时启用）──
    if not chunks:
        for section in toc:
            await _on_section_start(section)
            await _on_section(section, [])

    # 测试 stub 可能不接受新 callback 参数 —— 用签名探测降级
    import inspect as _inspect
    try:
        sig = _inspect.signature(agent.match)
        supports_callbacks = "on_section" in sig.parameters
    except (TypeError, ValueError):
        supports_callbacks = False

    async def _bridge(section, mlist):
        await _on_section(section, [_match_to_dict(m) for m in mlist])

    if supports_callbacks:
        matches = await agent.match(
            toc, outline_matrix, chunks,
            on_section=_bridge, on_section_start=_on_section_start,
        )
    else:
        matches = await agent.match(toc, outline_matrix, chunks)

    # 兜底：极端情况回调没触发，确保 state 完整
    for block_id, mlist in matches.items():
        if block_id not in matches_dict:
            serialized = [_match_to_dict(m) for m in mlist]
            await _on_section(
                next((s for s in toc if s.id == block_id), DomainSection(
                    id=block_id, level=1, title=block_id, raw_content="", special_marks=[])),
                serialized,
            )

    return {"materials": {"matches": matches_dict, "chunks": chunks}}


async def _match_by_assignment(
    state: WorkflowState,
    *,
    toc: list[DomainSection],
    outline_matrix: dict,
    chunks: list[dict],
    matches_dict: dict[str, list[dict]],
    emitter: EmitterArg,
    on_section,
    on_section_start,
) -> WorkflowState:
    """素材 → 章节 归属（匹配阶段 P0 主线，零模型调用）。

    增量：只处理"池里还没有被任何章节引用过"的片段（新上传的素材），
    已有依据（含人工挂接的 locked 行）原样保留 —— 补一份素材不再全量重跑。
    """
    from domain.spec.response_items import expand_all_items
    from infra.retrieval.assign import assign_evidence

    existing: dict[str, list[dict]] = {
        sid: list(rows or [])
        for sid, rows in ((state.get("materials") or {}).get("matches") or {}).items()
    }
    # 开启新一轮"全部重匹配"时由 runner 清空 matches；这里以 state 为准
    # id 类型不做强制转换：库里是 int，测试/旧数据可能是 str，混用会直接抛 ValueError
    already_ids = {
        ev["chunk_id"]
        for rows in existing.values() for ev in rows
        if isinstance(ev, dict) and ev.get("chunk_id") is not None
    }
    pool_ids = {c["id"] for c in chunks if c.get("id") is not None}
    only_chunk_ids = (pool_ids - already_ids) if already_ids else None

    matrix_dict = {
        bid: (row.to_dict() if hasattr(row, "to_dict") else dict(row))
        for bid, row in (outline_matrix or {}).items()
    }
    # 条目级挂载：把每段素材挂到该章的具体要求条目上（"并发 ≥2000"这条依据是哪段素材），
    # 撰写阶段要按条目逐条作答 + 做覆盖度自检。挂不上就留空 = 只作章节级依据。
    items_by_section = expand_all_items(matrix_dict)
    result = assign_evidence(
        chunks, toc, matrix_dict,
        only_chunk_ids=only_chunk_ids,
        items_by_section=items_by_section,
    )
    logger.info(f"match_node：归属完成 {result.stats()}（增量 {only_chunk_ids is not None}）")

    for section in toc:
        await on_section_start(section)
        rows = list(existing.get(section.id, []))
        rows.extend(ev.to_dict() for ev in result.evidence.get(section.id, []))
        await on_section(section, rows)

    return {"materials": {"matches": matches_dict, "chunks": chunks}}


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
        if toc_for_order:
            ordered_ids = [s.get("id") for s in toc_for_order if s.get("id") in outline_matrix]
            # toc 之外的矩阵条目：**告警并跳过**，不要静默生成。目录是用户的权威结构，
            # 矩阵里多出来的 id 只可能来自"目录被删改后的残留"（update_outline_structure
            # 现在会同步裁剪，这里是双保险）。以前会把它们追加进生成目标 —— 用户删掉的
            # 章节又会以正文的形式长回来。
            ghosts = [bid for bid in outline_matrix.keys() if bid not in set(ordered_ids)]
            if ghosts:
                logger.warning(
                    "generate：矩阵里有 %d 个章节不在当前目录中，已跳过：%s",
                    len(ghosts), ghosts[:10],
                )
        else:
            # 没有目录可比对（老 checkpoint / toc 缺失）：只能按矩阵生成，不做"目录外"判断
            logger.warning("generate：state 里没有 spec.toc，退回按矩阵顺序生成 %d 节",
                           len(outline_matrix))
            ordered_ids = list(outline_matrix.keys())
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

    # 链条数据：本章的**评分口径**与应响应的**规范要求** —— 审查据此判断
    # "是否真正满足要求"，而不是泛泛评"写得好不好"。
    chain_by_block: dict[str, dict] = {}
    try:
        from db import get_db
        from services.block_store import list_blocks as _list_blocks
        from services.point_store import list_spec_requirements as _list_reqs

        thread_id_for_chain = state.get("thread_id")
        project_id_for_chain = state.get("project_id")
        if thread_id_for_chain:
            async with get_db() as db:
                rows = {b.get("block_id"): b
                        for b in await _list_blocks(db, int(project_id_for_chain or 0),
                                                   thread_id_for_chain)}
                reqs = {r.get("requirement_id"): r
                        for r in await _list_reqs(db, run_thread_id=thread_id_for_chain)}
            for bid in blocks:
                row = rows.get(bid) or {}
                import json as _json
                try:
                    req_ids = _json.loads(row.get("spec_requirement_ids") or "[]")
                except (ValueError, TypeError):
                    req_ids = []
                chain_by_block[bid] = {
                    "criterion_text": str(row.get("criterion_text") or ""),
                    "spec_requirements": [
                        (reqs.get(rid, {}).get("text") or "") for rid in req_ids
                        if reqs.get(rid)
                    ],
                }
    except Exception as e:
        logger.warning(f"_run_review：链条数据读取失败（{e}），按无评分口径评审")

    findings = await agent.review(
        blocks=blocks,
        outline_matrix=outline_matrix,
        emitter=_review_bridge,
        chain_by_block=chain_by_block,
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
    needs_material_blocks: list[str] = []
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
        all_issues = list(t.get("issues") or []) + list(c.get("issues") or [])
        has_critical = any(str(i.get("severity") or "").lower() == "critical" for i in all_issues)
        # needs_material 的问题"重写"修不好（要补料）→ 不进回炉，单列交补料/人工
        material_only = bool(all_issues) and all(bool(i.get("needs_material")) for i in all_issues)
        if material_only and (tech_score < REVIEW_SCORE_THRESHOLD or comp_score < REVIEW_SCORE_THRESHOLD):
            needs_material_blocks.append(block_id)
            continue

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
        "needs_material_blocks": needs_material_blocks,
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
        "source_file": s.source_file,
        "chain": dict(getattr(s, "chain", {}) or {}),
    }


def _dict_to_section(d: dict) -> DomainSection:
    return DomainSection(
        id=d.get("id", ""),
        level=int(d.get("level", 0) or 0),
        title=d.get("title", ""),
        raw_content=d.get("raw_content", ""),
        special_marks=list(d.get("special_marks", []) or []),
        source_file=d.get("source_file", "") or "",
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
        "error_kind": getattr(r, "error_kind", "") or "",
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
        # 契约字段：素材原文 + 出处 + 性质/来源/归属方式（撰写阶段与界面都要用）
        "content": getattr(m, "content", "") or "",
        "filename": getattr(m, "filename", "") or "",
        "locator": getattr(m, "locator", "") or "",
        "kind": getattr(m, "kind", "") or "",
        "source_kind": getattr(m, "source_kind", "") or "",
        "by": getattr(m, "by", "") or "",
        "confidence": float(getattr(m, "confidence", 0.0) or 0.0),
        "locked": bool(getattr(m, "locked", False)),
    }


def _dict_to_match(d: dict) -> Match:
    return Match(
        chunk_id=d.get("chunk_id"),
        score=float(d.get("score", 0.0) or 0.0),
        reason=d.get("reason", ""),
        hit_points=list(d.get("hit_points") or []),
        content=d.get("content", "") or "",
        filename=d.get("filename", "") or "",
        locator=d.get("locator", "") or "",
        kind=d.get("kind", "") or "",
        source_kind=d.get("source_kind", "") or "",
        by=d.get("by", "") or "",
        confidence=float(d.get("confidence") or 0.0),
        locked=bool(d.get("locked")),
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
