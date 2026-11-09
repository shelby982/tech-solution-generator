"""
backend/routes/blocks.py — Block CRUD + SSE 生成 + AI 功能

端点：
  GET  /api/projects/{id}/blocks   — Block 树（含所有元数据）
  GET  /api/blocks/{id}/matched-sources — Block 关联的素材片段
  PUT  /api/blocks/{id}            — 更新内容，自动写 revision
  POST /api/blocks/{id}/generate   — SSE 单块 AI 生成
  POST /api/blocks/{id}/ai         — AI 功能：polish/expand/check/search/style
"""

import json
import logging
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from db import get_db
from services.block_store import (
    get_block, update_block_content, update_block_status,
    list_blocks, add_revision, update_block_requirement, list_revisions,
    current_run_thread_id, update_block_matrix_fields,
)
from services.config_store import config_store, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm import dispatch_stream_generate
from infra.llm.clients import generate_oneshot_openai, generate_oneshot_claude
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["blocks"])


# ── GET /api/projects/{id}/blocks ────────────────────────

@router.get("/projects/{project_id}/blocks")
async def get_project_blocks(project_id: int, threadId: str | None = None):
    """取项目的 blocks。

    默认只返回**当前（最新一次）workflow run** 写的行：``block_id`` 是位置派生
    的，两次 run 的顶层 id 会撞车、行长期共存，不过滤就会把旧目录一起列出来。
    从未跑过 run 的老项目退回返回全部行，避免页面突然全空。
    """
    async with get_db() as db:
        tid = threadId or await current_run_thread_id(db, project_id)
        blocks = await list_blocks(db, project_id, tid, fallback_all=True)
    return JSONResponse(content=blocks)


@router.delete("/projects/{project_id}/blocks")
async def delete_project_blocks(project_id: int):
    """清空指定项目的所有 blocks（前端「重新提炼」入口用）。"""
    from services.block_store import delete_blocks_by_project

    async with get_db() as db:
        await delete_blocks_by_project(db, project_id)
    return JSONResponse(content={"project_id": project_id, "status": "cleared"})


# ── GET /api/blocks/{block_id}/matched-sources ───────────

@router.get("/blocks/{block_id}/matched-sources")
async def get_block_matched_sources(block_id: int):
    """
    返回 block.source 中存的素材片段。

    block.source 是 JSON 数组（在素材匹配阶段写入），元素含
    material_id / chunk_index / filename / content / score。
    若该列为空、非 JSON 或解析失败，返回空列表（前端容忍）。
    """
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")

        raw = block.get("source") or ""
        try:
            sources = json.loads(raw) if raw else []
            if not isinstance(sources, list):
                sources = []
        except (json.JSONDecodeError, TypeError):
            sources = []

    return JSONResponse(content={"matched_sources": sources})


# ── GET /api/blocks/{block_id}/material-search ───────────

# ── PUT /api/blocks/{block_id}/evidence ──────────────────
# 人工挂接 / 解绑素材依据。写库 + 写 checkpoint 都在 runner.set_chapter_evidence 里
# （两处都要写：库是页面与撰写读的权威，state 是下一轮匹配"已有依据"的来源）。
class EvidenceEditRequest(BaseModel):
    """人工挂接/解绑/改挂载条目。

    - ``action=mount``：把某段素材挂到本章（可带 item_ids 直接挂到具体应答条目）
    - ``action=unmount``：从本章解绑
    - ``action=set_items``：只改这段依据挂到哪些条目（不动归属本身）
    """
    action: Literal["mount", "unmount", "set_items"]
    chunk_id: int
    item_ids: list[str] | None = None
    threadId: str | None = None


@router.put("/blocks/{block_id}/evidence")
async def update_block_evidence(block_id: int, body: EvidenceEditRequest):
    """按**字符串 block_id**（s2.1 这类位置派生 id）操作：block_id 是路径里的主键行 id，
    先查出它的 block_id 字符串再交给 runner。"""
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        bid = str(block.get("block_id") or block_id)
        project_id = int(block.get("project_id") or 0)
        tid = body.threadId or await current_run_thread_id(db, project_id)

    if not tid:
        raise HTTPException(status_code=404, detail="该项目还没有运行记录，无法挂接素材")

    from orchestrator.runner import WorkflowAbortedError
    from routes.workflow import get_runner

    try:
        result = await get_runner().set_chapter_evidence(
            tid, bid, action=body.action,
            chunk_id=body.chunk_id, item_ids=body.item_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]) if exc.args else str(exc))
    except WorkflowAbortedError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"X-Error-Code": "workflow_aborted"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse(content=result)


# ── PUT /api/blocks/{id} ─────────────────────────────────

class BlockUpdateRequest(BaseModel):
    """工作台参考卡片 / 正文的局部更新。**只写调用方给的字段**，未给的不动。

    之前这里只有 content / requirement，于是参考卡片上改 key_points / veto_items /
    bonus_items / score_items / evidence_required / indicators 的 PUT **被静默丢弃**
    （pydantic 把多余字段丢掉，后端 200 返回原值，只有前端本地副本变了，刷新即回退）。
    """

    expected_content: str | None = Field(default=None, max_length=500_000)
    content: str | None = Field(default=None, max_length=500_000)
    requirement: str | None = Field(default=None, max_length=5000)
    key_points: str | None = Field(default=None, max_length=20_000)
    veto_items: str | None = Field(default=None, max_length=20_000)
    bonus_items: str | None = Field(default=None, max_length=20_000)
    score_items: str | None = Field(default=None, max_length=20_000)
    evidence_required: str | None = Field(default=None, max_length=20_000)
    indicators: str | None = Field(default=None, max_length=20_000)
    # 三选一枚举；非法值直接 422，别把脏值写进决定正文字数档的列
    constraint_level: Literal["mandatory", "recommended", "optional"] | None = None


VALID_TONES = {"official", "tech", "concise"}

class GenerateRequest(BaseModel):
    target_words: int = Field(default=800, ge=100, le=5000)
    tone: str = Field(default="official")

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, v: str) -> str:
        if v not in VALID_TONES:
            raise ValueError(f"tone 必须为 official / tech / concise，收到：{v}")
        return v


@router.put("/blocks/{block_id}")
async def update_block(block_id: int, body: BlockUpdateRequest):
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        existing = await get_block(db, block_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        if body.expected_content is not None and body.expected_content != (existing.get('content') or ''):
            raise HTTPException(409, '正文已被其他操作更新，本地修改已保留，请核对版本')
        updated = existing
        if body.content is not None and body.content != existing.get("content"):
            if existing.get("content") and not await list_revisions(db, block_id):
                await add_revision(db, block_id, existing["content"], "首次编辑前正文", "baseline", manage_transaction=False)
            # 交付门：内部语法残留 / 内部术语 —— 系统**自动清理**，不打扰用户
            from services.delivery_gate import auto_fix as gate_auto_fix
            cleaned, gate_changed, _ = gate_auto_fix(body.content)
            if gate_changed:
                body.content = cleaned
            # 表格规整：分隔行列数对齐表头（否则整张表在预览/导出里退化成一行文字）
            from services.body_tables import normalize_table_rows as normalize_tables
            body.content = normalize_tables(body.content)
            # 表格上方统一补表名（`**表：名**`），保证预览/导出都有表名
            from services.body_tables import ensure_table_captions
            content_with_captions, added = ensure_table_captions(body.content)
            if added:
                body.content = content_with_captions
            updated = await update_block_content(db, block_id, body.content, commit=False)
            await add_revision(db, block_id_int=block_id, content=body.content,
                               source="edit", summary="手动编辑", manage_transaction=False)
            await db.commit()
        if body.requirement is not None:
            updated = await update_block_requirement(db, block_id, body.requirement)
        # 其余参考卡片字段（提炼 8 字段里除 requirement 外）：**局部**更新，
        # 只写请求里显式给出的列 —— 一次改一个字段不该把别的字段清空。
        matrix_patch = {
            field: getattr(body, field)
            for field in ("key_points", "veto_items", "bonus_items", "score_items",
                          "evidence_required", "indicators", "constraint_level")
            if getattr(body, field) is not None
        }
        if matrix_patch:
            updated = await update_block_matrix_fields(db, block_id, matrix_patch)
    return JSONResponse(content=updated)


# ── POST /api/blocks/{id}/generate（SSE）────────────────

async def _generate_stream(block_id: int, db, target_words: int = 800, tone: str = "official") -> AsyncGenerator[str, None]:
    block = await get_block(db, block_id)
    if block is None:
        yield format_sse_event("error", {"message": f"Block 不存在：{block_id}"}); return

    if block.get("kind") != "content":
        yield format_sse_event("error", {"message": "仅 content 类型 block 支持生成"}); return

    project_id = block["project_id"]
    row = await (await db.execute("SELECT summary FROM projects WHERE id = ?", (project_id,))).fetchone()

    await update_block_status(db, block_id, "generating")

    configs, rr_index = config_store.get_configs_and_next_index()
    parts: list[str] = []

    try:
        async for token in dispatch_stream_generate(
            configs, rr_index, block["title"], block.get("content", ""),
            target_words=target_words,
            tone=tone,
        ):
            parts.append(token)
            yield format_sse_event("token", {"text": token})
    except Exception as exc:
        logger.exception(f"Block {block_id} 生成失败：{exc}")
        await update_block_status(db, block_id, "empty")
        yield format_sse_event("error", {"message": str(exc)}); return

    full_content = "".join(parts)
    await update_block_content(db, block_id, full_content)
    revision = await add_revision(db, block_id_int=block_id, content=full_content,
                                  source="generate", summary="AI 初稿")

    yield format_sse_event("block_done", {
        "block_id": block_id,
        "content": full_content,
        "revision_no": revision.get("revision_no", 1),
    })


@router.post("/blocks/{block_id}/generate")
async def generate_block(block_id: int, req: GenerateRequest = GenerateRequest()):
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        if not config_store.is_configured():
            raise HTTPException(status_code=400, detail="未配置 API Key")

    async def stream():
        async with get_db() as db:
            async for chunk in _generate_stream(block_id, db, req.target_words, req.tone):
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── POST /api/blocks/{id}/ai ─────────────────────────────

_AI_PROMPTS = {
    "polish": "你是专业方案润色助手，保留结构改善表达",
    "expand": "你是专业方案补充助手，基于 requirement 补齐缺失内容",
    "check":  "你是方案查漏助手，对照评分项列出未覆盖要求",
    "search": "你是需求检索助手，从材料内容中找隐含要求",
    "style":  "你是方案风格调整助手，调整至正式书面文风",
}


class AIActionRequest(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in _AI_PROMPTS:
            raise ValueError(f"不支持的 action：{v}，可选：{list(_AI_PROMPTS)}")
        return v


@router.post("/blocks/{block_id}/ai")
async def ai_action(block_id: int, body: AIActionRequest):
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        if not config_store.is_configured():
            raise HTTPException(status_code=400, detail="未配置 API Key")

        system_prompt = _AI_PROMPTS[body.action]
        user_prompt = (
            f"## 标题\n{block.get('title', '')}\n\n"
            f"## 当前内容\n{block.get('content', '（暂无）')}\n\n"
            f"## 评分要求\n{block.get('requirement', '（未填写）')}\n\n"
            f"请按指令处理，直接输出结果。"
        )

        configs, rr_index = config_store.get_configs_and_next_index()
        config = configs[rr_index % len(configs)]

        try:
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                suggestion = await generate_oneshot_openai(config, system_prompt, user_prompt, max_tokens=2000)
            else:
                suggestion = await generate_oneshot_claude(config, system_prompt, user_prompt, max_tokens=2000)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"AI 调用失败：{exc}")

    return JSONResponse(content={"action": body.action, "suggestion": suggestion, "block_id": block_id})
