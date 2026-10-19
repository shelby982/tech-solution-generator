"""官方源引用（P2）：抓取核验、检索适配、接受/拒绝。

原则：外部信息必须可核验（官方原文），二手/无来源一律不采；检索能力未配置时明确告知。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from services.citations import (OFFICIAL_PORTALS, discover_candidates, fetch_candidate, link_citation,
                                list_citations, reject_citation, save_citation, search_official,
                                search_configured, unlink_citation)

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


class LinkBody(BaseModel):
    block_id: str = Field(min_length=1, max_length=120)


@router.get("")
async def read_citations(project_id: int, status: str | None = Query(default=None),
                         blockId: str | None = Query(default=None)):
    async with get_db() as db:
        return {"citations": await list_citations(db, project_id, status, block_id=blockId),
                "search_configured": search_configured()}


@router.post("/{citation_id}/link")
async def link(project_id: int, citation_id: str, body: LinkBody):
    """把一条已接受的官方依据关联到某章（多对多，可复用）。"""
    async with get_db() as db:
        try:
            return await link_citation(db, project_id, citation_id, body.block_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.delete("/{citation_id}/link/{block_id}")
async def unlink(project_id: int, citation_id: str, block_id: str):
    async with get_db() as db:
        return await unlink_citation(db, project_id, citation_id, block_id)


class DiscoverBody(BaseModel):
    query: str = Field(min_length=2, max_length=200)


@router.get("/portals")
async def read_portals(project_id: int):
    """官方入口目录：没有检索 API 时，先给官方站点（用户可点开、也可让系统扫描）。"""
    return {"portals": OFFICIAL_PORTALS, "search_configured": search_configured()}


@router.post("/discover")
async def discover(project_id: int, body: DiscoverBody):
    """先找官方来源：配置了检索 API 就真检索；否则扫描官方入口页按关键词挑候选链接。

    候选都带 source（search / portal），用户点其中一个再「抓取核验」；也可以自己粘贴链接。
    """
    try:
        return await discover_candidates(body.query)
    except Exception as exc:
        return {"configured": search_configured(), "results": [], "portals": OFFICIAL_PORTALS,
                "message": f"自动找来源失败：{exc}"}


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
