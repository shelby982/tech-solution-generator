"""官方源引用（P2）：抓取核验、检索适配、接受/拒绝。

原则：外部信息必须可核验（官方原文），二手/无来源一律不采；检索能力未配置时明确告知。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from services.citations import OFFICIAL_PORTALS, discover_candidates, fetch_candidate, list_citations, remember_search, save_citation, refresh_segments, search_history, search_configured, select_segments, suggest_query

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


class ImageBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class LinkBody(BaseModel):
    block_id: str = Field(min_length=1, max_length=120)


@router.get("")
async def read_citations(project_id: int, status: str | None = Query(default=None),
                         blockId: str | None = Query(default=None)):
    async with get_db() as db:
        return {"citations": await list_citations(db, project_id, status, block_id=blockId),
                "search_configured": search_configured()}


class SelectBody(BaseModel):
    indices: list[int] = Field(default_factory=list)


@router.post("/{citation_id}/select")
async def select(project_id: int, citation_id: str, body: SelectBody):
    """选用来源里的片段：只有选中的段落/表格/图会进入本章编写上下文。"""
    async with get_db() as db:
        try:
            return await select_segments(db, project_id, citation_id, body.indices)
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.post("/{citation_id}/refresh")
async def refresh(project_id: int, citation_id: str):
    """重新抓取该来源并提取可用片段（老引用补片段用）。"""
    async with get_db() as db:
        try:
            return await refresh_segments(db, project_id, citation_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.post("/{citation_id}/image")
async def download_image(project_id: int, citation_id: str, body: "ImageBody"):
    """把来源里的图片**下载成本项目素材**（正文不放外链，导出才稳）。

    产品口径：外部图片只作"参考素材"，入库后仍标注来源，引用时由用户确认版权/可用性。
    """
    import io
    import uuid as _uuid
    from pathlib import Path

    from fastapi import File
    import httpx
    from PIL import Image, UnidentifiedImageError

    from routes.chapter_studio import MAX_IMAGE_BYTES
    from routes.materials import UPLOAD_ROOT
    from services.block_store import create_material

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT segments, selected FROM citations WHERE id=? AND project_id=?", (citation_id, project_id),
        )).fetchone()
    if not row:
        raise HTTPException(404, "引用不存在")
    import json as _json
    try:
        segments = _json.loads(row["segments"] or "[]")
    except ValueError:
        segments = []
    image_urls = [segment["content"] for segment in segments if segment.get("kind") == "image"]
    target = body.url.strip()
    if target not in image_urls:
        raise HTTPException(409, "这张图片不属于该来源，请从来源的图片片段里选")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(target, headers={"User-Agent": "Mozilla/5.0 (compatible; bid-assistant)"})
    data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片超过 10 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            suffix = (image.format or "png").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "不是有效图片") from exc
    name = f"来源图片_{_uuid.uuid4().hex[:6]}.{ 'jpg' if suffix in ('jpeg', 'jpg') else suffix }"
    relative = f"uploads/{project_id}/{_uuid.uuid4().hex}_{name}"
    path = UPLOAD_ROOT.parent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    async with get_db() as db:
        material = await create_material(db, project_id, name, suffix, relative, "source", file_size=len(data))
        await db.execute("UPDATE materials SET parse_status='done' WHERE id=?", (material["id"],))
        await db.commit()
    return {"material_id": material["id"], "filename": name, "image_url": f"/api/studio/materials/{material['id']}/image"}


class DiscoverBody(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    thread_id: str = ""
    block_id: str = ""


@router.get("/suggest")
async def suggest(project_id: int, threadId: str = Query(min_length=1), blockId: str = Query(min_length=1)):
    """按当前章节推导检索词（标题 + 素材缺口 + 配方骨架），并给出本章已有来源/材料数。

    产品口径：检索是为了"写这一章素材够不够"，用户不该盲搜。
    """
    async with get_db() as db:
        try:
            return await suggest_query(db, project_id, threadId, blockId)
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.get("/history")
async def read_history(project_id: int, threadId: str = Query(min_length=1), blockId: str = Query(min_length=1)):
    """本章的检索历史（上次找到的候选），避免重复搜。"""
    async with get_db() as db:
        return {"history": await search_history(db, project_id, threadId, blockId)}


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
        data = await discover_candidates(body.query)
        async with get_db() as db:
            await remember_search(db, project_id, body.thread_id, body.block_id, body.query, data.get("results") or [])
        return data
    except Exception as exc:
        return {"configured": search_configured(), "results": [], "portals": OFFICIAL_PORTALS,
                "message": f"自动找来源失败：{exc}"}


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
