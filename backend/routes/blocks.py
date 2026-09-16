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
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from db import get_db
from services.block_store import (
    get_block, update_block_content, update_block_status,
    list_blocks, add_revision, update_block_requirement, list_revisions,
)
from services.config_store import config_store, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm import dispatch_stream_generate
from infra.llm.clients import generate_oneshot_openai, generate_oneshot_claude
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["blocks"])


# ── GET /api/projects/{id}/blocks ────────────────────────

@router.get("/projects/{project_id}/blocks")
async def get_project_blocks(project_id: int):
    async with get_db() as db:
        blocks = await list_blocks(db, project_id)
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


# ── PUT /api/blocks/{id} ─────────────────────────────────

class BlockUpdateRequest(BaseModel):
    content: str | None = Field(default=None, max_length=500_000)
    requirement: str | None = Field(default=None, max_length=5000)


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
        existing = await get_block(db, block_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        updated = existing
        if body.content is not None and body.content != existing.get("content"):
            if existing.get("content") and not await list_revisions(db, block_id):
                await add_revision(db, block_id, existing["content"], "首次编辑前正文", "baseline")
            updated = await update_block_content(db, block_id, body.content)
            await add_revision(db, block_id_int=block_id, content=body.content,
                               source="edit", summary="手动编辑")
        if body.requirement is not None:
            updated = await update_block_requirement(db, block_id, body.requirement)
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
    doc_summary = (row["summary"] or "") if row else ""

    await update_block_status(db, block_id, "generating")

    configs, rr_index = config_store.get_configs_and_next_index()
    parts: list[str] = []

    try:
        async for token in dispatch_stream_generate(
            configs, rr_index, block["title"], block.get("content", ""),
            doc_summary=doc_summary,
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
