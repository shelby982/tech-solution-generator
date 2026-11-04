"""设计版图表 API：创建 job、查询状态、预览 HTML、取 PNG。

前端「插入内容 → AI 生成图表（设计版）」使用；生成 30–90s，所以走 job 轮询，
不阻塞请求。PNG 通过 /api/studio/diagrams/{id}/image 插入正文。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from db import get_db
from services import diagram_assets
from services.diagram_design import (
    SKILL_TYPES, normalize_purpose, normalize_size, normalize_type, queue_job, recommend_type,
)

router = APIRouter(tags=["diagram-design"])


QUALITY_OPTIONS = [
    {"key": "fast", "label": "快速结构图（默认）"},
    {"key": "design_architecture", "label": "架构/分层图用设计版"},
    {"key": "design_all", "label": "全部图用设计版（较慢）"},
]


class DiagramQualityBody(BaseModel):
    quality: str = Field(default="fast", max_length=40)


@router.get('/projects/{project_id}/diagram-settings')
async def diagram_settings(project_id: int):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT quality FROM diagram_settings WHERE project_id=?", (project_id,)
        )).fetchone()
    return {"quality": (row[0] if row else "fast"), "options": QUALITY_OPTIONS}


@router.put('/projects/{project_id}/diagram-settings')
async def save_diagram_settings(project_id: int, body: DiagramQualityBody):
    quality = body.quality if body.quality in {item["key"] for item in QUALITY_OPTIONS} else "fast"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO diagram_settings(project_id, quality, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(project_id) DO UPDATE SET quality=excluded.quality, updated_at=CURRENT_TIMESTAMP",
            (project_id, quality),
        )
        await db.commit()
    return {"quality": quality}


class DiagramMappingRow(BaseModel):
    requirement: str = Field(default="", max_length=200)
    response: str = Field(default="", max_length=200)
    evidence: str = Field(default="", max_length=200)


class DiagramDesignBody(BaseModel):
    title: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=8000)
    skill_type: str = Field(default="", max_length=40)
    size_preset: str = Field(default="doc-wide", max_length=40)
    section: str = Field(default="", max_length=120)
    elements: list[str] = Field(default_factory=list, max_length=12)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    caption: str = Field(default="", max_length=160)
    purpose: str = Field(default="", max_length=40)
    claim: str = Field(default="", max_length=300)
    score_refs: list[str] = Field(default_factory=list, max_length=8)
    mapping: list[DiagramMappingRow] = Field(default_factory=list, max_length=8)
    source_refs: list[str] = Field(default_factory=list, max_length=8)
    relations: str = Field(default="", max_length=40)


@router.get('/blocks/{block_id}/studio/diagram-design/types')
async def diagram_types(block_id: int):
    async with get_db() as db:
        block = await (await db.execute("SELECT title, content FROM blocks WHERE id=?", (block_id,))).fetchone()
    if not block:
        raise HTTPException(404, "章节不存在")
    recommended = recommend_type(str(block["title"] or ""), str(block["content"] or "")[:3000])
    return {"types": [{"key": key, "label": label} for key, label in SKILL_TYPES.items()],
            "recommended": recommended, "sizes": [{"key": key, "label": label} for key, label in {
                "doc-wide": "文档宽（推荐）", "doc-inline": "文档内",
                "print-a4-landscape": "A4 横向打印", "slide-16x9": "16:9 整页",
            }.items()]}


@router.post('/blocks/{block_id}/studio/diagram-assets', status_code=202)
async def create_diagram(block_id: int, body: DiagramDesignBody):
    async with get_db() as db:
        block = await (await db.execute("SELECT * FROM blocks WHERE id=?", (block_id,))).fetchone()
    if not block:
        raise HTTPException(404, "章节不存在")
    block = dict(block)
    text = (body.text or "").strip()
    title = (body.title or block.get("title") or "图表").strip()[:120]
    if not text:
        text = (str(block.get("content") or "")[:4000] or title)
    purpose = normalize_purpose(body.purpose, body.score_refs)
    if purpose in ("score_evidence", "compare") and not body.score_refs:
        raise HTTPException(422, "请先选择对应评分点/条款")
    skill_type = normalize_type(body.skill_type or recommend_type(title, text))
    size_preset = normalize_size(body.size_preset)
    brief = {
        "title": title,
        "section": (body.section or "").strip()[:120],
        "skill_type": skill_type,
        "size_preset": size_preset,
        "text": text,
        "elements": [str(x)[:80] for x in body.elements][:12],
        "evidence": [str(x)[:500] for x in body.evidence][:8],
        "caption": (body.caption or title)[:160],
        "purpose": purpose,
        "claim": (body.claim or "").strip()[:300],
        "score_refs": [str(x)[:160] for x in body.score_refs][:8],
        "mapping": [row.model_dump() for row in body.mapping][:8],
        "source_refs": [str(x)[:300] for x in body.source_refs][:8],
        "relations": (body.relations or "").strip()[:40],
    }
    job_id = await diagram_assets.create_job(int(block["project_id"]), int(block_id), brief)
    queue_job(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get('/blocks/{block_id}/studio/diagram-assets/{job_id}')
async def diagram_job(block_id: int, job_id: str):
    job = await diagram_assets.get_job(job_id)
    if not job or int(job["block_id"] or 0) != int(block_id):
        raise HTTPException(404, "图表任务不存在")
    data = {"job_id": job_id, "status": job["status"], "stage": job.get("stage") or "",
            "error": job.get("error") or "", "asset_id": job.get("asset_id") or ""}
    if job["status"] == "done" and job.get("asset_id"):
        asset = await diagram_assets.get_asset(job["asset_id"])
        if asset:
            data.update({
                "title": asset.get("title") or "",
                "type": asset.get("skill_type") or "",
                "size": asset.get("size_preset") or "",
                "image_url": f"/api/studio/diagrams/{asset['id']}/image",
                "preview_url": f"/api/studio/diagrams/{asset['id']}/preview",
                "width": asset.get("png_width"), "height": asset.get("png_height"),
                "checks": json.loads(asset.get("checks_json") or "{}"),
            })
    return data


@router.post('/studio/diagrams/{asset_id}/regenerate', status_code=202)
async def regenerate(asset_id: str, body: DiagramDesignBody | None = None):
    asset = await diagram_assets.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "图表不存在")
    brief = json.loads(asset.get("brief_json") or "{}")
    if body:
        brief.update({k: v for k, v in body.model_dump().items() if v not in (None, "", [])})
    brief["skill_type"] = normalize_type(brief.get("skill_type"))
    brief["size_preset"] = normalize_size(brief.get("size_preset"))
    brief["force"] = True  # 重新生成必须绕过同 prompt 缓存
    job_id = await diagram_assets.create_job(int(asset["project_id"]), int(asset["block_id"] or 0), brief)
    queue_job(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get('/studio/diagrams/{asset_id}/svg')
async def diagram_svg(asset_id: str):
    asset = await diagram_assets.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "图表不存在")
    svg = str(asset.get("svg") or "")
    if not svg:
        raise HTTPException(404, "该图表没有 SVG 源")
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})


@router.delete('/studio/diagrams/{asset_id}')
async def remove_diagram(asset_id: str):
    deleted = await diagram_assets.delete_asset(asset_id)
    if not deleted:
        raise HTTPException(404, "图表不存在")
    return {"deleted": True}


@router.get('/studio/diagrams/{asset_id}/image')
async def diagram_image(asset_id: str):
    asset = await diagram_assets.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "图表不存在")
    try:
        data = diagram_assets.read_png(asset)
    except FileNotFoundError as exc:
        raise HTTPException(404, "图表图片文件已丢失") from exc
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get('/studio/diagrams/{asset_id}/preview')
async def diagram_preview(asset_id: str):
    asset = await diagram_assets.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "图表不存在")
    html = diagram_assets.read_html(asset)
    # 生成的 HTML 已在前置校验里拒绝 script；这里再加一层 CSP，防止直接当页面打开。
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
    })
