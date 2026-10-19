"""官方源引用（P2）：抓取核验、检索适配、接受/拒绝。

原则：外部信息必须可核验（官方原文），二手/无来源一律不采；检索能力未配置时明确告知。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from services.citations import (fetch_candidate, list_citations, reject_citation,
                                save_citation, search_official, search_configured)

router = APIRouter(prefix="/projects/{project_id}/citations", tags=["citations"])


class FetchBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class SaveBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = ""
    publisher: str = ""
    published_at: str = ""
    excerpt: str = ""
    query: str = ""
    thread_id: str = ""
    block_id: str = ""


@router.get("")
async def read_citations(project_id: int, status: str | None = Query(default=None)):
    async with get_db() as db:
        return {"citations": await list_citations(db, project_id, status),
                "search_configured": search_configured()}


@router.get("/search")
async def search(project_id: int, q: str = Query(min_length=2, max_length=120)):
    """官方源检索：未配置检索 API 时返回 configured=false（不假装检索过）。"""
    try:
        return await search_official(q)
    except Exception as exc:                      # 网络/配额错误 → 明确告知，不静默
        return {"configured": True, "results": [], "message": f"检索失败：{exc}"}


@router.post("/fetch")
async def fetch(project_id: int, body: FetchBody):
    """抓取并核验一个官方链接，返回待接受的引用候选（不落库）。"""
    try:
        candidate = await fetch_candidate(body.url)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"抓取失败：{exc}")
    return candidate


@router.post("")
async def create_citation(project_id: int, body: SaveBody):
    async with get_db() as db:
        try:
            return await save_citation(db, project_id, body.model_dump(),
                                       thread_id=body.thread_id, block_id=body.block_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.post("/{citation_id}/reject")
async def reject(project_id: int, citation_id: str):
    async with get_db() as db:
        if not await reject_citation(db, project_id, citation_id):
            raise HTTPException(404, "引用不存在")
        return {"rejected": True}
