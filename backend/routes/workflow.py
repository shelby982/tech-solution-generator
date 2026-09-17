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


@router.post("/{thread_id}/workspace-action")
async def workspace_action(thread_id: str, req: WorkspaceActionReq):
    try:
        await get_runner().workspace_action(thread_id, req.block_ids, req.action)
    except KeyError:
        raise HTTPException(status_code=404, detail="工作流不存在")
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
    return JSONResponse({"thread_id": thread_id, "status": "resumed"})


@router.post("/{thread_id}/regen")
async def regen(thread_id: str, req: RegenReq):
    runner = get_runner()
    try:
        await runner.regen(thread_id, req.block_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({"thread_id": thread_id, "regen_targets": req.block_ids})


@router.post("/{thread_id}/rerun-match")
async def rerun_match(thread_id: str):
    """重新触发素材匹配：把状态拨回 NODE_EXTRACT 后 → graph 自动跑 match → gate_materials。"""
    runner = get_runner()
    try:
        await runner.rerun_match(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    return JSONResponse({"thread_id": thread_id, "status": "rerunning_match"})


@router.post("/{thread_id}/redraft-outline")
async def redraft_outline(thread_id: str, req: RedraftOutlineReq | None = None):
    """整版重出应答文件目录：换一份提炼要求，跳过 parse 直接重跑张衡的目录派生。"""
    runner = get_runner()
    try:
        await runner.redraft_outline(thread_id, (req.instruction if req else "") or "")
    except KeyError:
        raise HTTPException(status_code=404, detail="thread_id 未知")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"thread_id": thread_id, "status": "redrafting_outline"})


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
    return JSONResponse({"thread_id": thread_id, "status": "resuming_generation"})


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
    await runner.recover(thread_id)
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
