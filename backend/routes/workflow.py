"""routes/workflow.py — 工作流 7 端点（spec §7）。

端点：
  POST  /api/workflow/start           — 创建 thread_id，启动 graph
  POST  /api/workflow/{tid}/resume    — 闸门处提交（user_choice + edits）
  POST  /api/workflow/{tid}/regen     — 闸门 3 选回修
  POST  /api/workflow/{tid}/abort     — 取消
  POST  /api/workflow/{tid}/recover   — 崩溃恢复
  GET   /api/workflow/{tid}/state     — 读 state 快照
  GET   /api/workflow/{tid}/stream    — SSE 事件流

业务编排全部委托给 ``WorkflowRunner``；本模块只做参数校验、调 runner、把 emitter
帧转 SSE。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workflow", tags=["workflow"])


# ─────────────────────────────────────────────
# 全局 runner（main.py lifespan 注入）
# ─────────────────────────────────────────────

# main.py 在 lifespan 启动时调用 ``set_runner(...)`` 注入 ``WorkflowRunner``。
# 测试可直接在 router 模块属性上替换 runner 实例。
_runner = None


def set_runner(runner) -> None:
    """注入 WorkflowRunner（main.py / 测试用）。"""
    global _runner
    _runner = runner


def get_runner():
    if _runner is None:
        raise HTTPException(
            status_code=503,
            detail="WorkflowRunner 未初始化",
        )
    return _runner


# ─────────────────────────────────────────────
# Pydantic 模型
# ─────────────────────────────────────────────

class StartReq(BaseModel):
    project_id: int = Field(..., gt=0)
    config: dict = Field(default_factory=dict)


class ResumeReq(BaseModel):
    user_choice: str = Field(default="")
    edits: dict = Field(default_factory=dict)


class RegenReq(BaseModel):
    block_ids: list[str] = Field(default_factory=list)


class WorkspaceActionReq(BaseModel):
    action: Literal["revise", "review"]
    block_ids: list[str] = Field(min_length=1, max_length=2000)


class RedraftOutlineReq(BaseModel):
    instruction: str = Field(default="", max_length=10000)


class ConfirmOutlineReq(BaseModel):
    """block_id → 该章节的 8 字段编辑（前端 collectOutlineEdits 的产物）。"""
    matrix: dict[str, dict] = Field(default_factory=dict)


class OutlineStructureReq(BaseModel):
    """目录结构编辑：**有序**节点列表，顺序即目录顺序。

    每项 ``{"block_id": str, "level": int, "title": str}``；层级由顺序推导
    （见 domain/spec/structure.py），所以没有 parent 字段。
    """
    nodes: list[dict] = Field(default_factory=list)
    # **客户端明确删除**的章节 id。服务端据此校验"少了但没声明"的提交：
    # 那说明提交方的列表是陈旧的（实测：面板列表比库少 → 保存把差额删掉，
    # 还写回 spec.toc，图恢复时再删一轮 —— 目录被静默削掉 8 行 / 33 行）。
    removed_ids: list[str] = Field(default_factory=list)
    # 用户主动删除章节时可选「连正文一起删」：默认 False 沿用"保留用户内容"的约定，
    # 但保留的行仍会出现在导出里，所以要在确认框里让用户明确选择。
    purge: bool = False


@router.post("/{thread_id}/workspace-action")
async def workspace_action(thread_id: str, req: WorkspaceActionReq):
    from orchestrator.runner import WorkflowAbortedError

    try:
        await get_runner().workspace_action(thread_id, req.block_ids, req.action)
    except KeyError:
        raise HTTPException(status_code=404, detail="工作流不存在")
    except WorkflowAbortedError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"X-Error-Code": "workflow_aborted"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "action": req.action, "block_ids": req.block_ids}


# ─────────────────────────────────────────────
# 端点
# ─────────────────────────────────────────────

@router.post("/start")
async def start(req: StartReq):
    runner = get_runner()
    tid = await runner.start(req.project_id, req.config)
    return JSONResponse({"thread_id": tid})


@router.post("/{thread_id}/resume")
async def resume(thread_id: str, req: ResumeReq):
    runner = get_runner()
    try:
        await runner.resume(thread_id, req.user_choice, req.edits)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        # 同一 thread 已有后台 task 在跑（刷新页面 / 第二个标签页重复确认）。
        # 必须让前端拿到 409 + 可展示的原因，而不是 500。
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "resumed"})


@router.post("/{thread_id}/regen")
async def regen(thread_id: str, req: RegenReq):
    runner = get_runner()
    try:
        await runner.regen(thread_id, req.block_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "regen_targets": req.block_ids})


@router.post("/{thread_id}/rerun-match")
async def rerun_match(thread_id: str, full: bool = False):
    """触发素材匹配：把状态拨回 NODE_EXTRACT 后 → graph 自动跑 match → gate_materials。

    ``full=false``（默认）增量：只归属新上传的素材；``full=true`` 清空重来（代价大）。
    """
    runner = get_runner()
    try:
        await runner.rerun_match(thread_id, full=full)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "rerunning_match"})


@router.get("/{thread_id}/redraft-check")
async def redraft_check(thread_id: str):
    """重出目录前的预检：能不能重拆、不能的话该做什么。

    前端点「重新拆分目录」前先问这里。此前没有这道预检，用户点下去拿到 200，
    但重拆用的是 checkpoint 里的**旧解析结果** —— 页面看起来"没反应"，
    只能反复点（实测连点 8 次）。
    """
    runner = get_runner()
    try:
        return await runner.redraft_check(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")


@router.post("/{thread_id}/redraft-outline")
async def redraft_outline(thread_id: str, req: RedraftOutlineReq | None = None):
    """整版重出应答文件目录：换一份提炼要求，跳过 parse 直接重跑张衡的目录派生。"""
    runner = get_runner()
    from orchestrator.runner import WorkflowAbortedError

    try:
        await runner.redraft_outline(thread_id, (req.instruction if req else "") or "")
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except WorkflowAbortedError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"X-Error-Code": "workflow_aborted"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "redrafting_outline"})


@router.post("/{thread_id}/analyze-points")
async def analyze_points(thread_id: str):
    """**只拆评分点**（不生成目录）。

    用户要求的第①步：先把各标包的商务/技术/报价评分点拆出来；
    第②步再由用户在「拆解结果」里选择类别并生成对应目录（走 redraft-outline）。
    """
    runner = get_runner()
    from orchestrator.runner import WorkflowAbortedError

    try:
        await runner.redraft_outline(thread_id, "", points_only=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except WorkflowAbortedError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"X-Error-Code": "workflow_aborted"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "analyzing_points"})


@router.post("/{thread_id}/confirm-outline")
async def confirm_outline(thread_id: str, req: ConfirmOutlineReq):
    """「确认内容大纲及要求」：把提炼结果上的编辑写回 blocks 表与图里的矩阵。"""
    runner = get_runner()
    try:
        await runner.confirm_outline(thread_id, req.matrix)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({
        "thread_id": thread_id, "status": "confirmed", "blocks": len(req.matrix),
    })


@router.post("/{thread_id}/outline-structure")
async def update_outline_structure(thread_id: str, req: OutlineStructureReq):
    """闸门 1 的目录结构编辑：增删章节 / 改标题 / 上下移动 / 缩进。

    请求体是有序节点列表（顺序即目录顺序，层级由顺序推导）：

        {"nodes": [{"block_id": "s1", "level": 1, "title": "技术方案"}, ...]}

    结构非法（空目录 / id 重复 / 标题为空 / 节点过多）→ 400，消息可直接展示给用户；
    任务正在运行（解析、提炼、匹配、生成中）→ 409。
    """
    runner = get_runner()
    from domain.spec.structure import StructureError

    from orchestrator.runner import WorkflowAbortedError

    try:
        stats = await runner.update_outline_structure(
            thread_id, req.nodes, purge=req.purge, removed_ids=req.removed_ids,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except StructureError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except WorkflowAbortedError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"X-Error-Code": "workflow_aborted"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "structure_updated", **stats})


@router.post("/{thread_id}/pause")
async def pause(thread_id: str):
    """生成阶段暂停：保留已生成 block，graph 跳到 GATE_PAUSE 闸门停止。"""
    runner = get_runner()
    try:
        await runner.pause(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({"thread_id": thread_id, "status": "pausing"})


@router.post("/{thread_id}/skip-to-review")
async def skip_to_review(thread_id: str):
    """从 GATE_PAUSE 出来跳到评审：用已生成 block 内容跑 review。"""
    runner = get_runner()
    try:
        await runner.skip_to_review(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({"thread_id": thread_id, "status": "skipping_to_review"})


@router.post("/{thread_id}/resume-generation")
async def resume_generation(thread_id: str):
    """从 GATE_PAUSE 出来回到 generate 节点继续生成。"""
    runner = get_runner()
    try:
        await runner.resume_generation(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "resuming_generation"})


@router.post("/{thread_id}/resume-auto")
async def resume_auto(thread_id: str):
    """**自动续跑**被中断的准备步骤（新 IA 下"准备"不是用户动作）。

    页面加载时发现上一次没跑完就调它一次；后端按图的 ``next`` 决定接着跑哪一步。
    用户主动取消（paused）不在此列。
    """
    runner = get_runner()
    try:
        action = await runner.resume_auto(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "resumed": action})


@router.post("/{thread_id}/resume-extract")
async def resume_extract(thread_id: str):
    """取消/暂停后接着逐节提炼：复用同一条 run，跳过已完成的章节。"""
    runner = get_runner()
    try:
        await runner.resume_extract(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "resuming_extract"})


@router.post("/{thread_id}/abort")
async def abort(thread_id: str):
    runner = get_runner()
    try:
        await runner.abort(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({"thread_id": thread_id, "status": "aborted"})


@router.post("/{thread_id}/recover")
async def recover(thread_id: str):
    runner = get_runner()
    try:
        await runner.recover(thread_id)
    except ValueError as exc:
        # 已有后台 task 在跑：recover 不该再起一个（否则并发跑同一个 thread）
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "recovering"})


@router.get("/{thread_id}/state")
async def state(thread_id: str):
    runner = get_runner()
    snap = await runner.state(thread_id)
    from orchestrator.nodes import REVIEW_SCORE_THRESHOLD
    # runner.state() 只读 checkpoint：后端重启后 _runs 内存清空，但 checkpoint 还停在
    # 「跑了一半」的 stage 上，快照看起来仍然正常。前端要靠 alive 才能区分「正常停在闸门」
    # 和「进程已经不认得这个 thread 了」——否则页面会把中断的 run 当成待继续的任务。
    return JSONResponse({
        **snap,
        "alive": runner.has_run(thread_id),
        "ui": {"review_threshold": REVIEW_SCORE_THRESHOLD},
    })


@router.get("/{thread_id}/stream")
async def stream(thread_id: str):
    runner = get_runner()
    # runner.stream() 是 async generator function：调用它仅创建 generator 对象，
    # 真正抛 KeyError 是在 StreamingResponse 第一次迭代时——HTTP header 已 200 发出。
    # 所以这里要先用 has_run 同步早判，否则 404 退化成 500。
    if not runner.has_run(thread_id):
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return StreamingResponse(runner.stream(thread_id), media_type="text/event-stream")


__all__ = ["router", "set_runner", "get_runner"]
