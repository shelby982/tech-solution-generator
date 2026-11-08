"""章节级 SSE 作业接口（v3 §4.3，M2-a）。"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db import get_db
from services import chapter_sse

router = APIRouter(tags=["chapter-sse"])
_CHART_RETRIES: set[str] = set()


class RebuildBody(BaseModel):
    thread_id: str = Field(min_length=1)
    block_ids: list[str] = Field(default_factory=list)
    dry_run: bool = False


class SelectionRange(BaseModel):
    before: str = Field(max_length=500_000)
    selection: str = Field(min_length=1, max_length=50_000)
    after: str = Field(max_length=500_000)


class WriteBody(BaseModel):
    thread_id: str = Field(min_length=8)
    block_id: str = Field(min_length=1)          # 章节编码（s1.2.1.1）
    settings_override: dict[str, str] = Field(default_factory=dict)
    force_rewrite: bool = False
    clean_rewrite: bool = False
    generic: bool = False
    section_titles: list[str] = Field(default_factory=list, max_length=100)
    expected_content: str | None = Field(default=None, max_length=500_000)
    instruction: str = Field(default="", max_length=3000)
    selection: SelectionRange | None = None


async def _resolve_block(project_id: int, block_code: str, thread_id: str) -> dict:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM blocks WHERE project_id=? AND block_id=? AND run_thread_id=?", (project_id, block_code, thread_id))).fetchone()
    if not row:
        raise HTTPException(404, f"章节不存在：{block_code}")
    return dict(row)


@router.get('/projects/{project_id}/studio/context-rebuild')
async def context_rebuild_preview(project_id: int, thread_id: str = ''):
    """列出正文已存在但写作上下文版本过期的章节。"""
    from db import get_db
    from services.section_context import SECTION_CONTEXT_VERSION
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, block_id, title, length(content) AS words, COALESCE(context_version,0) AS context_version "
            "FROM blocks WHERE project_id=? AND (?='' OR run_thread_id=?) AND length(trim(COALESCE(content,'')))>0 "
            "AND COALESCE(context_version,0)<? ORDER BY order_idx, id",
            (project_id, thread_id, thread_id, SECTION_CONTEXT_VERSION),
        )).fetchall()
    stale = [dict(row) for row in rows]
    return {"current_version": SECTION_CONTEXT_VERSION, "stale": stale, "count": len(stale)}


@router.post('/projects/{project_id}/studio/context-rebuild')
async def context_rebuild(project_id: int, body: RebuildBody):
    """按新写作上下文重写旧章节；dryRun 只返回清单，不创建作业。"""
    from db import get_db
    from services.section_context import SECTION_CONTEXT_VERSION
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, block_id, title, length(content) AS words, COALESCE(context_version,0) AS context_version "
            "FROM blocks WHERE project_id=? AND (?='' OR run_thread_id=?) AND length(trim(COALESCE(content,'')))>0 "
            "AND COALESCE(context_version,0)<? ORDER BY order_idx, id",
            (project_id, body.thread_id, body.thread_id, SECTION_CONTEXT_VERSION),
        )).fetchall()
        stale = [dict(row) for row in rows]
        wanted = {str(x) for x in body.block_ids or []}
        if wanted:
            stale = [row for row in stale if str(row["block_id"]) in wanted]
        if body.dry_run:
            return {"current_version": SECTION_CONTEXT_VERSION, "stale": stale, "created": [], "skipped": []}
        created, skipped = [], []
        for row in stale:
            existing = await (await db.execute(
                "SELECT id FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')",
                (int(row["id"]),),
            )).fetchone()
            if existing:
                skipped.append({"block_id": row["block_id"], "reason": "已有运行中作业"})
                continue
            job_id = await chapter_sse.create_job(project_id, body.thread_id, int(row["id"]),
                                                  force_rewrite=True)
            chapter_sse.start_runner(job_id)
            created.append({"block_id": row["block_id"], "job_id": job_id})
    return {"current_version": SECTION_CONTEXT_VERSION, "stale": stale, "created": created, "skipped": skipped}


@router.post('/projects/{project_id}/studio/chapter-writes')
async def create_chapter_write(project_id: int, body: WriteBody):
    block = await _resolve_block(project_id, body.block_id, body.thread_id)
    try:
        job_id = await chapter_sse.create_job(project_id, body.thread_id, int(block["id"]),
                                              body.settings_override, body.force_rewrite, body.generic, body.section_titles,
                                              body.expected_content, body.instruction, body.selection.model_dump() if body.selection else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    chapter_sse.start_runner(job_id)
    return {"job_id": job_id}


@router.get('/projects/{project_id}/studio/chapter-writes/{job_id}')
async def get_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    from services.chapter_progress import progress_blocks
    artifacts = json.loads(row['artifacts'] or '{}')
    snapshot = json.loads(row['snapshot'] or '{}')
    return {
        "blocks": progress_blocks(artifacts['live'], snapshot.get('base_content') or '') if artifacts.get('live') else [],
        "id": job_id,
        "job_id": job_id,
        "status": row["status"],
        "stage": row["stage"],
        "saved": bool(row["saved"]),
        "error": row["error"],
        "content": row["content"],
        "artifacts": json.loads(row["artifacts"] or "{}"),
        "snapshot": json.loads(row["snapshot"] or "{}"),
    }


@router.post('/projects/{project_id}/studio/chapter-writes/{job_id}/cancel')
async def cancel_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    await chapter_sse._set_job(job_id, status="cancelled", stage="已取消")
    await chapter_sse.emit(job_id, "job", {"job_id": job_id, "status": "cancelled", "stage": "已取消", "progress": 0})
    task = chapter_sse._RUNNERS.get(job_id)
    if task and not task.done():
        task.cancel()
    return {"ok": True}


@router.post('/projects/{project_id}/studio/chapter-writes/{job_id}/retry')
async def retry_chapter_write(project_id: int, job_id: str):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    ok = await chapter_sse.retry_job(job_id)
    return {"ok": ok, "job_id": job_id}


@router.get('/projects/{project_id}/studio/chapter-writes/{job_id}/stream')
async def stream_chapter_write(project_id: int, job_id: str, thread_id: str = ''):
    row = await chapter_sse._job_row(job_id)
    if not row or int(row["project_id"]) != int(project_id):
        raise HTTPException(404, "作业不存在")
    queue = chapter_sse.subscribe(job_id)

    async def events():
        from utils.sse import format_sse_event
        try:
            # 先回放当前状态（SSE 无回放，状态以 job 详情为准）
            yield format_sse_event('job', {"job_id": job_id, "status": row["status"],
                                           "stage": row["stage"], "progress": 0})
            artifacts = json.loads(row['artifacts'] or '{}')
            snapshot = json.loads(row['snapshot'] or '{}')
            if artifacts.get('live'):
                from services.chapter_progress import progress_blocks
                yield format_sse_event('snapshot', {'id': job_id, 'status': row['status'],
                    'blocks': progress_blocks(artifacts['live'], snapshot.get('base_content') or '')})
            # 已结束的作业：SSE 无回放，直接补一条终态事件，避免前端等到"中断"
            if row["status"] in ("done", "failed", "cancelled"):
                if row["status"] == "done":
                    yield format_sse_event('done', {"content": row["content"], "saved": bool(row["saved"]),
                                                   "reason": "内容与现有正文一致，未产生变更" if not row["saved"] else "已写入正文",
                                                   "models": {}, "checks": []})
                elif row["status"] == "failed":
                    yield format_sse_event('error', {"scope": "job", "message": row["error"] or "作业失败",
                                                    "recoverable": True})
                return
            last = time.monotonic()
            while True:
                if row["status"] in ("done", "failed", "cancelled") and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield format_sse_event('heartbeat', {"ts": time.time()})
                    async with get_db() as db:
                        fresh = await (await db.execute(
                            "SELECT status FROM chapter_writing_jobs WHERE id=?", (job_id,))).fetchone()
                    if fresh and fresh[0] in ("done", "failed", "cancelled"):
                        break
                    continue
                yield format_sse_event(item["event"], item["data"])
                if item["event"] in ("done", "error") and item["data"].get("scope") == "job":
                    break
        finally:
            chapter_sse.unsubscribe(job_id, queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get('/blocks/{block_id}/studio/writing-job')
async def latest_chapter_write(block_id: int):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id,project_id FROM chapter_writing_jobs WHERE block_id=? AND snapshot LIKE '%chapter_sse%' ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (block_id,))).fetchone()
    return await get_chapter_write(row['project_id'], row['id']) if row else None


class BatchWriteBody(BaseModel):
    thread_id: str = Field(min_length=8)
    root_block_id: str = Field(min_length=1)
    preview: bool = True


@router.post('/projects/{project_id}/studio/batch-write')
async def batch_write(project_id: int, body: BatchWriteBody):
    from services.block_store import list_blocks
    from services.writing_workspace import node_scopes
    from services.writing_plans import plan_of_block
    async with get_db() as db:
        blocks = await list_blocks(db, project_id, body.thread_id, fallback_all=False)
        root = next((block for block in blocks if block['block_id'] == body.root_block_id), None)
        if not root:
            raise HTTPException(404, '目录不存在')
        scope = node_scopes(blocks)[root['id']]
        pending = [block for block in blocks if block['id'] in scope['pending_ids']]
        targets = []
        for block in pending:
            found = await plan_of_block(db, project_id, body.thread_id, block['block_id'])
            chapter = next((item for item in (found[1].get('chapters') or []) if item.get('block_id') == block['block_id']), {}) if found else {}
            targets.append({'id': block['id'], 'block_id': block['block_id'], 'title': block['title'],
                            'ready': True, 'auto_outline': not bool(chapter.get('outline')), 'evidence_required': bool(block.get('evidence_required'))})
    created, skipped = [], []
    if not body.preview:
        for target in targets:
            if not target['ready']:
                skipped.append({**target, 'reason': '请先在本章计划生成大纲'})
                continue
            try:
                job_id = await chapter_sse.create_job(project_id, body.thread_id, target['id'], expected_content='')
                chapter_sse.start_runner(job_id)
                created.append({**target, 'job_id': job_id})
            except ValueError as exc:
                skipped.append({**target, 'reason': str(exc)})
    return {'targets': targets, 'created': created, 'skipped': skipped}

class SelectionBody(BaseModel):
    expected_content: str = Field(max_length=500_000)
    before: str = Field(max_length=500_000)
    selection: str = Field(min_length=1, max_length=50_000)
    after: str = Field(max_length=500_000)
    instruction: str = Field(min_length=1, max_length=3000)


@router.post('/blocks/{block_id}/studio/rewrite-selection')
async def rewrite_selection(block_id: int, body: SelectionBody):
    from services.block_store import get_block
    async with get_db() as db:
        block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, '章节不存在')
    if body.before + body.selection + body.after != body.expected_content or (block.get('content') or '') != body.expected_content:
        raise HTTPException(409, '选区或正文版本已变化，请重新选择')
    try:
        job_id = await chapter_sse.create_job(block['project_id'], block.get('run_thread_id') or '', block_id,
            expected_content=body.expected_content, instruction=body.instruction,
            selection={'before': body.before, 'selection': body.selection, 'after': body.after})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    chapter_sse.start_runner(job_id)
    return await stream_chapter_write(block['project_id'], job_id)


class RetryChartBody(BaseModel):
    index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    role: str = Field(pattern='^(table|diagram)$')


@router.post('/projects/{project_id}/studio/chapter-writes/{job_id}/retry-chart')
async def retry_chart(project_id: int, job_id: str, body: RetryChartBody):
    row = await chapter_sse._job_row(job_id)
    if not row or row['project_id'] != project_id:
        raise HTTPException(404, '作业不存在')
    if row['status'] in ('running', 'queued'):
        raise HTTPException(409, '请等待当前编制结束')
    artifacts = json.loads(row['artifacts'] or '{}')
    live = artifacts.get('live') or {}
    sections = live.get('sections') or []
    if body.index >= len(sections):
        raise HTTPException(404, '小节不存在')
    section = sections[body.index]
    chart = next((item for item in section['charts'] if item['title'] == body.title and item['role'] == body.role), None)
    if not chart or chart.get('state') == 'done':
        raise HTTPException(409, '该图表无需重试')
    from services.block_store import get_block
    async with get_db() as db:
        block = await get_block(db, row['block_id'])
    if job_id in _CHART_RETRIES:
        raise HTTPException(409, '本章已有图表重试中，请稍候')
    _CHART_RETRIES.add(job_id)
    try:
        result = await chapter_sse._generate_chart(job_id, body.index, section,
            {'kind': 'figure' if body.role == 'diagram' else 'table', 'title': body.title},
            row['thread_id'], (artifacts.get('models') or {}).get(body.role, ''), block)
        chart.update(state='done' if result.get('ok') else 'failed', markdown=result.get('markdown') or '', error=result.get('error') or '')
        await chapter_sse._set_job(job_id, artifacts=json.dumps(artifacts, ensure_ascii=False))
    finally:
        _CHART_RETRIES.discard(job_id)

    return await get_chapter_write(project_id, job_id)
