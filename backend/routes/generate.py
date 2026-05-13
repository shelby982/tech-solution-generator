"""
方案生成路由 — SSE 流式生成引擎

端点列表：
  POST   /api/generate/start              启动生成任务，返回 task_id
  GET    /api/generate/stream/{task_id}   SSE 流（实时 token / 进度事件）
  GET    /api/generate/status/{task_id}   查询任务状态
  DELETE /api/generate/{task_id}          取消正在进行的任务

SSE 事件类型：
  section_start  开始生成某章节   {"section_id": "s1", "title": "..."}
  token          流式 token 片段  {"text": "..."}
  section_done   章节生成完成     {"section_id": "s1", "content": "...", "progress": 0.5}
  all_done       全部完成         {"task_id": "...", "download_url": "/api/download/..."}
  error          发生错误         {"message": "..."}

并发安全说明：
  - asyncio.Queue 和 asyncio.Event 在 async 路由函数内创建，确保绑定到正确的事件循环。
  - 后台生成协程（_run_generation）通过 asyncio.create_task 启动，与 SSE 流在同一事件循环中协作。
  - 取消通过 cancel_event（asyncio.Event）传播，生成循环每个 token 后检查一次。

晚连接处理：
  如果客户端连接到 SSE 流时任务已经完成，_sse_event_generator 会直接重播已保存的章节结果，
  避免客户端因时序问题错过事件。
"""

import asyncio
import logging
import uuid
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.config_store import config_store
from services.llm import dispatch_stream_generate, dispatch_doc_summary
from services.task_store import task_store, TaskStatus, GenerationTask
from utils.validators import validate_uuid
from utils.sse import (
    sse_token,
    sse_section_start,
    sse_section_done,
    sse_section_skip,
    sse_all_done,
    sse_error,
    sse_doc_summary,
    format_sse_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["generate"])


# ─────────────────────────────────────────────
# 请求 / 响应模型
# ─────────────────────────────────────────────

class SectionInput(BaseModel):
    id: str = Field(..., min_length=1, max_length=100, description="章节 ID，如 s1")
    title: str = Field(..., min_length=1, max_length=200, description="章节标题")
    content: str = Field(default="", description="章节原始内容（用于 LLM 上下文，可为空）", max_length=200_000)
    level: int = Field(default=1, ge=1, le=4, description="章节层级 1-4")


class GenerateStartRequest(BaseModel):
    sections: list[SectionInput] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="待生成章节列表（按顺序逐章节生成）",
    )
    target_words: int = Field(
        default=500,
        ge=100,
        le=10000,
        description="每章节目标字数（100～10000），默认 500",
    )
    doc_summary: str = Field(default="", max_length=5000, description="已提炼的摘要，非空时跳过内置提炼步骤")
    doc_template: str = Field(default="", max_length=10000, description="生成模板（Markdown），空=直接扩写")


class GenerateStartResponse(BaseModel):
    task_id: str
    stream_url: str
    status_url: str
    total_sections: int


# ─────────────────────────────────────────────
# 后台生成核心协程
# ─────────────────────────────────────────────

async def _run_generation(task: GenerationTask) -> None:
    """
    后台协程：逐章节调用 LLM 流式生成，将事件推入 task.queue。

    事件格式（tuple）：
        ("section_start", {"section_id": ..., "title": ...})
        ("token",         {"text": ...})
        ("section_done",  {"section_id": ..., "content": ..., "progress": ...})
        ("all_done",      {"task_id": ..., "download_url": ...})
        ("error",         "错误信息字符串")
        (None, None)      # sentinel，通知 SSE 生成器结束循环
    """
    if not config_store.is_configured():
        task.status = TaskStatus.ERROR
        task.error_message = "未配置 API Key，请先在「设置」中配置"
        await task.queue.put(("error", task.error_message))
        await task.queue.put((None, None))
        return

    task.status = TaskStatus.RUNNING

    try:
        sections = task.sections
        total = len(sections)

        # ── 第一步：生成文档摘要（若已提炼则跳过 LLM 调用）──
        if task.doc_summary:
            logger.info(f"[{task.task_id}] 使用已提炼摘要，跳过 LLM 提炼步骤（{len(task.doc_summary)} 字符）")
            await task.queue.put(("doc_summary", {"summary": task.doc_summary}))
        else:
            logger.info(f"[{task.task_id}] 开始生成文档摘要...")
            try:
                configs, rr_index = config_store.get_configs_and_next_index()
                summary = await dispatch_doc_summary(configs, rr_index, sections)
                task.doc_summary = summary
                await task.queue.put(("doc_summary", {"summary": summary}))
                logger.info(f"[{task.task_id}] 文档摘要生成完成（{len(summary)} 字符）")
            except Exception as e:
                logger.warning(f"[{task.task_id}] 文档摘要生成失败，继续生成章节：{e}")
                task.doc_summary = ""

        # ── 取消检查 ──────────────────────────────
        if task.cancel_event.is_set():
            task.status = TaskStatus.CANCELLED
            await task.queue.put(("error", "生成已被用户取消"))
            await task.queue.put((None, None))
            return

        # ── 第二步：逐章节生成 ────────────────────
        for idx, sec_input in enumerate(sections):
            sec_id    = sec_input["id"]
            sec_title = sec_input["title"]
            sec_content = sec_input.get("content", "")

            # ── 取消检查 ──────────────────────────
            if task.cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
                await task.queue.put(("error", "生成已被用户取消"))
                break

            # ── 原文为空则跳过，不调用 LLM ────────
            if not sec_content.strip():
                logger.info(f"[{task.task_id}] 章节「{sec_title}」原文为空，跳过生成 ({idx+1}/{total})")
                task.results[sec_id].done = True
                progress = (idx + 1) / total
                await task.queue.put(("section_skip", {
                    "section_id": sec_id,
                    "title": sec_title,
                    "index": idx,
                    "total": total,
                    "progress": round(progress, 2),
                }))
                continue

            # ── 通知章节开始 ──────────────────────
            logger.info(f"[{task.task_id}] 开始生成章节 ({idx+1}/{total})：{sec_title}")
            await task.queue.put(("section_start", {
                "section_id": sec_id,
                "title": sec_title,
                "index": idx,
                "total": total,
            }))

            # ── 流式生成（含重试）─────────────────
            full_content_parts: list[str] = []
            MAX_RETRIES = 2
            RETRY_DELAY = 3.0
            section_failed = False

            for attempt in range(MAX_RETRIES + 1):
                full_content_parts = []
                try:
                    configs, rr_index = config_store.get_configs_and_next_index()
                    async for token_text in dispatch_stream_generate(
                        configs, rr_index, sec_title, sec_content, task.target_words,
                        doc_summary=task.doc_summary,
                        doc_template=task.doc_template,
                    ):
                        if task.cancel_event.is_set():
                            break
                        full_content_parts.append(token_text)
                        await task.queue.put(("token", {"text": token_text}))
                    break  # 成功，退出重试循环

                except Exception as llm_err:
                    if attempt < MAX_RETRIES and not task.cancel_event.is_set():
                        logger.warning(
                            f"[{task.task_id}] 章节「{sec_title}」第 {attempt+1} 次失败，"
                            f"{RETRY_DELAY}s 后重试：{llm_err}"
                        )
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(task.cancel_event.wait()),
                                timeout=RETRY_DELAY,
                            )
                            break  # cancel_event 触发，提前退出重试
                        except asyncio.TimeoutError:
                            pass   # 正常超时，继续重试
                        continue
                    # 重试耗尽，跳过本章节继续后续
                    logger.exception(
                        f"[{task.task_id}] 章节「{sec_title}」重试 {MAX_RETRIES} 次后仍失败，跳过：{llm_err}"
                    )
                    await task.queue.put(("section_skip", {
                        "section_id": sec_id,
                        "title": sec_title,
                        "index": idx,
                        "total": total,
                        "progress": round((idx + 1) / total, 2),
                    }))
                    task.results[sec_id].done = True
                    section_failed = True
                    break

            if section_failed:
                continue

            # 再次检查取消（生成中途被取消）
            if task.cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
                await task.queue.put(("error", "生成已被用户取消"))
                break

            # ── 保存结果，通知章节完成 ────────────
            full_content = "".join(full_content_parts)
            task.results[sec_id].content = full_content
            task.results[sec_id].done = True

            progress = (idx + 1) / total
            logger.info(f"[{task.task_id}] 章节完成：{sec_title}（{len(full_content)} 字符，进度 {progress:.0%}）")

            await task.queue.put(("section_done", {
                "section_id": sec_id,
                "content": full_content,
                "progress": round(progress, 2),
            }))

        else:
            # for-else：所有章节正常完成（未被 break）
            task.status = TaskStatus.COMPLETED
            logger.info(f"[{task.task_id}] 所有章节生成完成")
            await task.queue.put(("all_done", {
                "task_id": task.task_id,
                "download_url": f"/api/download/{task.task_id}",
            }))

    except Exception as fatal_err:
        err_msg = f"生成任务发生未预期异常：{fatal_err}"
        logger.exception(f"[{task.task_id}] {err_msg}")
        task.status = TaskStatus.ERROR
        task.error_message = err_msg
        await task.queue.put(("error", err_msg))

    finally:
        # sentinel：无论正常结束还是异常，都通知 SSE 生成器退出
        await task.queue.put((None, None))


# ─────────────────────────────────────────────
# SSE 事件生成器
# ─────────────────────────────────────────────

async def _sse_event_generator(
    task: GenerationTask,
    request: Request,
) -> AsyncIterator[str]:
    """
    从 task.queue 读取事件，格式化为 SSE 字符串并 yield 给客户端。

    晚连接处理：
    - 若任务已完成（COMPLETED）：重播已保存的章节结果后结束。
    - 若任务已出错（ERROR/CANCELLED）：直接发送 error 事件后结束。
    - 否则：实时从队列消费事件。

    心跳：
    - 每 30 秒无事件时发送 SSE 注释行（": heartbeat"），防止代理/浏览器超时断连。
    """
    # ── 晚连接：任务已结束 ──────────────────────
    if task.status == TaskStatus.COMPLETED:
        if task.doc_summary:
            yield sse_doc_summary(task.doc_summary)
            await asyncio.sleep(0)
        for sec in task.sections:
            result = task.results.get(sec["id"])
            if result and result.done:
                if result.content:
                    yield sse_section_done(result.section_id, result.content, 1.0)
                else:
                    yield sse_section_skip(result.section_id, result.title, 1.0)
                await asyncio.sleep(0)
        yield sse_all_done(task.task_id)
        return

    if task.status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
        yield sse_error(task.error_message or "任务已失败或被取消")
        return

    # ── 实时消费队列 ────────────────────────────
    HEARTBEAT_INTERVAL = 30.0  # 秒

    while True:
        # 检查客户端是否已断开连接
        if await request.is_disconnected():
            logger.info(f"[{task.task_id}] 客户端已断开 SSE 连接")
            # 尝试触发取消，避免后台继续消耗 Token
            task_store.cancel(task.task_id)
            break

        try:
            item = await asyncio.wait_for(task.queue.get(), timeout=HEARTBEAT_INTERVAL)
        except asyncio.TimeoutError:
            # 发送心跳注释行（SSE 规范允许以 ":" 开头的注释）
            yield ": heartbeat\n\n"
            continue

        event_type, data = item

        # sentinel — 生成器结束
        if event_type is None:
            break

        # 根据事件类型格式化 SSE
        if event_type == "doc_summary":
            yield sse_doc_summary(data["summary"])

        elif event_type == "token":
            yield sse_token(data["text"])

        elif event_type == "section_start":
            yield sse_section_start(data["section_id"], data["title"])
            # 额外发送 index/total 信息（自定义扩展事件）
            yield format_sse_event("section_index", {
                "section_id": data["section_id"],
                "index": data.get("index", 0),
                "total": data.get("total", 0),
            })

        elif event_type == "section_done":
            yield sse_section_done(
                data["section_id"],
                data["content"],
                data["progress"],
            )

        elif event_type == "section_skip":
            yield sse_section_skip(
                data["section_id"],
                data["title"],
                data["progress"],
            )

        elif event_type == "all_done":
            yield sse_all_done(data["task_id"])
            break  # 正常结束，退出生成器

        elif event_type == "error":
            yield sse_error(data if isinstance(data, str) else str(data))
            break  # 出错后退出


# ─────────────────────────────────────────────
# 路由定义
# ─────────────────────────────────────────────

class ExtractRequest(BaseModel):
    sections: list[SectionInput] = Field(..., min_length=1, max_length=1000)


@router.post("/generate/extract")
async def extract_doc_summary(request_body: ExtractRequest):
    """
    独立提炼端点：接收章节列表，返回结构化项目概述。
    非流式，前端 loading 等待结果。
    """
    if not config_store.is_configured():
        raise HTTPException(status_code=400, detail="未配置 API Key，请先在「设置」页面完成配置")

    sections = [s.model_dump() for s in request_body.sections]
    try:
        configs, rr_index = config_store.get_configs_and_next_index()
        summary = await dispatch_doc_summary(configs, rr_index, sections)
    except Exception as e:
        logger.exception(f"提炼文档摘要失败：{e}")
        raise HTTPException(status_code=500, detail="规范书核心内容提炼失败，请稍后重试")

    return {"summary": summary}


@router.post("/generate/start", response_model=GenerateStartResponse)
async def start_generate(request_body: GenerateStartRequest):
    """
    启动生成任务。

    - 校验 API Key 已配置
    - 创建 GenerationTask（含 asyncio.Queue 和 asyncio.Event）
    - 通过 asyncio.create_task 启动后台生成协程
    - 立即返回 task_id 和 SSE 流 URL
    """
    if not config_store.is_configured():
        raise HTTPException(
            status_code=400,
            detail="未配置 API Key，请先在「设置」页面完成配置",
        )

    # 将 Pydantic 模型序列化为普通字典列表
    sections = [s.model_dump() for s in request_body.sections]

    # 创建任务（附带 target_words）
    task = task_store.create(
        sections,
        target_words=request_body.target_words,
        doc_summary=request_body.doc_summary,
        doc_template=request_body.doc_template,
    )

    # 在 async 上下文内创建 asyncio 原语（确保绑定到当前事件循环）
    task.queue = asyncio.Queue()
    task.cancel_event = asyncio.Event()

    # 启动后台生成协程（非阻塞）
    asyncio.create_task(
        _run_generation(task),
        name=f"generate-{task.task_id[:8]}",
    )

    logger.info(f"创建生成任务：task_id={task.task_id}，章节数={len(sections)}")

    return GenerateStartResponse(
        task_id=task.task_id,
        stream_url=f"/api/generate/stream/{task.task_id}",
        status_url=f"/api/generate/status/{task.task_id}",
        total_sections=len(sections),
    )


@router.get("/generate/stream/{task_id}")
async def stream_generate_sse(task_id: str, request: Request):
    """
    SSE 流：实时推送生成进度和 token。

    客户端应在收到 /generate/start 的响应后立即连接此端点。
    支持晚连接（任务已完成时重播结果）。

    响应头：
      Content-Type: text/event-stream
      Cache-Control: no-cache
      X-Accel-Buffering: no   （禁用 Nginx 缓冲）
    """
    validate_uuid(task_id, "task_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")

    # 任务还在 PENDING（queue 尚未注入），等待最多 5 秒
    if task.queue is None:
        for _ in range(50):
            await asyncio.sleep(0.1)
            if task.queue is not None:
                break
        if task.queue is None:
            raise HTTPException(
                status_code=503,
                detail="任务队列尚未就绪，请稍后重试",
            )

    return StreamingResponse(
        _sse_event_generator(task, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/generate/status/{task_id}")
async def get_task_status(task_id: str):
    """
    查询任务状态（轮询备用接口）。

    返回字段：
      task_id, status, progress (0~1), total_sections,
      completed_sections, error_message
    """
    validate_uuid(task_id, "task_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return task.to_dict()


@router.delete("/generate/{task_id}")
async def cancel_task(task_id: str):
    """
    取消正在进行的生成任务。

    - RUNNING 状态：发送取消信号，后台协程在下一个 token 循环时停止
    - 其他状态：返回当前状态说明，不报错
    """
    validate_uuid(task_id, "task_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")

    if task.status == TaskStatus.COMPLETED:
        return {"message": "任务已完成，无需取消", "task_id": task_id, "status": task.status}

    if task.status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
        return {"message": f"任务已处于终止状态：{task.status}", "task_id": task_id, "status": task.status}

    if task.status == TaskStatus.PENDING:
        # 还未开始，直接标记取消
        task.status = TaskStatus.CANCELLED
        return {"message": "任务已取消（尚未开始生成）", "task_id": task_id, "status": task.status}

    # RUNNING：发送取消信号
    sent = task_store.cancel(task_id)
    if sent:
        return {"message": "已发送取消信号，生成将在当前 token 完成后停止", "task_id": task_id}
    else:
        return {"message": "取消信号发送失败（任务可能刚刚结束）", "task_id": task_id, "status": task.status}


# ─────────────────────────────────────────────
# 单章节重新生成
# ─────────────────────────────────────────────

class RegenStartRequest(BaseModel):
    section_title: str = Field(..., min_length=1, max_length=200)
    section_content: str = Field(default="", description="章节原文（给 LLM 参考）", max_length=200_000)
    target_words: int = Field(default=500, ge=100, le=10000)
    extra_prompt: str = Field(default="", max_length=1000, description="追加给 LLM 的优化说明")
    doc_summary: str = Field(default="", max_length=5000, description="全局项目摘要（继承自主任务）")
    doc_template: str = Field(default="", max_length=10000, description="生成模板（继承自主任务）")
    config_id: Optional[str] = Field(default=None, description="指定 API 配置 id；None 表示按优先级轮询",
                                     pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


class PatchSectionRequest(BaseModel):
    content: str = Field(..., max_length=500_000)


class PatchSummaryRequest(BaseModel):
    summary: str = Field(..., max_length=5000)


@router.patch("/generate/{task_id}/summary")
async def patch_doc_summary(task_id: str, body: PatchSummaryRequest):
    """用户手动修改项目概述后，同步更新 task.doc_summary"""
    validate_uuid(task_id, "task_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    task.doc_summary = body.summary
    return {"ok": True}


async def _run_regen(task: GenerationTask, body: RegenStartRequest, config_id: Optional[str]) -> None:
    """单章节重新生成后台协程（轻量版，无文档摘要步骤）"""
    task.status = TaskStatus.RUNNING
    sec_id = task.sections[0]["id"]
    sec_title = body.section_title

    try:
        if config_id:
            cfg = config_store.get_by_id(config_id)
            if cfg is None:
                raise ValueError(f"指定的 API 配置不存在：{config_id}")
            configs, rr_index = [cfg], 0
        else:
            configs, rr_index = config_store.get_configs_and_next_index()

        await task.queue.put(("section_start", {
            "section_id": sec_id,
            "title": sec_title,
            "index": 0,
            "total": 1,
        }))

        full_parts: list[str] = []
        MAX_RETRIES = 2
        RETRY_DELAY = 3.0

        for attempt in range(MAX_RETRIES + 1):
            full_parts = []
            try:
                async for token_text in dispatch_stream_generate(
                    configs, rr_index, sec_title, body.section_content,
                    body.target_words, doc_summary=body.doc_summary,
                    extra_prompt=body.extra_prompt, doc_template=body.doc_template,
                ):
                    if task.cancel_event.is_set():
                        break
                    full_parts.append(token_text)
                    await task.queue.put(("token", {"text": token_text}))
                break
            except Exception as e:
                if attempt < MAX_RETRIES and not task.cancel_event.is_set():
                    logger.warning(f"[regen/{task.task_id}] 第 {attempt+1} 次失败，{RETRY_DELAY}s 后重试：{e}")
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(task.cancel_event.wait()),
                            timeout=RETRY_DELAY,
                        )
                        break  # cancel_event 触发，提前退出重试
                    except asyncio.TimeoutError:
                        pass   # 正常超时，继续重试
                    continue
                raise

        if task.cancel_event.is_set():
            task.status = TaskStatus.CANCELLED
            await task.queue.put(("error", "重新生成已被用户取消"))
        else:
            full_content = "".join(full_parts)
            task.results[sec_id].content = full_content
            task.results[sec_id].done = True
            task.status = TaskStatus.COMPLETED
            logger.info(f"[regen/{task.task_id}] 章节「{sec_title}」重新生成完成（{len(full_content)} 字符）")
            await task.queue.put(("section_done", {
                "section_id": sec_id,
                "content": full_content,
                "progress": 1.0,
            }))
            await task.queue.put(("all_done", {"task_id": task.task_id}))

    except Exception as err:
        err_msg = f"重新生成失败：{err}"
        logger.exception(f"[regen/{task.task_id}] {err_msg}")
        task.status = TaskStatus.ERROR
        task.error_message = err_msg
        await task.queue.put(("error", err_msg))
    finally:
        await task.queue.put((None, None))


@router.post("/generate/regen/start")
async def start_regen(body: RegenStartRequest):
    """启动单章节重新生成任务，返回 task_id 和 SSE 流 URL"""
    if not config_store.is_configured():
        raise HTTPException(status_code=400, detail="未配置 API Key，请先在「设置」页面完成配置")

    if body.config_id and config_store.get_by_id(body.config_id) is None:
        raise HTTPException(status_code=400, detail=f"指定的 API 配置不存在：{body.config_id}")

    sec_id = str(uuid.uuid4())
    task = task_store.create(
        [{"id": sec_id, "title": body.section_title, "content": body.section_content, "level": 1}],
        target_words=body.target_words,
    )
    task.queue = asyncio.Queue()
    task.cancel_event = asyncio.Event()

    asyncio.create_task(
        _run_regen(task, body, body.config_id),
        name=f"regen-{task.task_id[:8]}",
    )

    logger.info(f"创建重新生成任务：task_id={task.task_id}，章节=「{body.section_title}」")
    return {
        "task_id": task.task_id,
        "section_id": sec_id,
        "stream_url": f"/api/generate/stream/{task.task_id}",
    }


@router.patch("/generate/{task_id}/section/{section_id}")
async def patch_section_content(task_id: str, section_id: str, body: PatchSectionRequest):
    """手动编辑后同步更新主任务的章节内容（供下载时使用）"""
    validate_uuid(task_id, "task_id")
    validate_uuid(section_id, "section_id")
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    if section_id in task.results:
        task.results[section_id].content = body.content
    return {"ok": True}
