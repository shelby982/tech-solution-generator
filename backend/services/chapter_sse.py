"""章节级 SSE 作业引擎（v3 §4，M2-a）。

一个作业 = 一次"按大纲编写整章"：
  ① 读计划（无大纲 → 主编生成一次）
  ② 正文串行逐节流式生成（每节替换进草稿）
  ③ 图表：本版只发事件、不生成（M3 接入并行与占位符）
  ④ 收尾：一次性落库（base 未变才写；否则返回建议稿）
事件通过 asyncio 队列广播，GET /stream 订阅即可（SSE 无回放，状态以 job 详情为准）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

from db import get_db
from services.block_store import add_revision, get_block, update_block_content

# job_id -> 订阅队列集合
_BUS: dict[str, set[asyncio.Queue]] = {}
_RUNNERS: dict[str, asyncio.Task] = {}
_CONTENT_LIMIT = 60_000


def subscribe(job_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _BUS.setdefault(job_id, set()).add(queue)
    return queue


def unsubscribe(job_id: str, queue: asyncio.Queue) -> None:
    _BUS.get(job_id, set()).discard(queue)


async def emit(job_id: str, event: str, payload: dict) -> None:
    for queue in list(_BUS.get(job_id, ())):
        try:
            queue.put_nowait({"event": event, "data": payload})
        except asyncio.QueueFull:
            pass


def _section_pattern(title: str) -> re.Pattern:
    return re.compile(r"^##[ \t]+" + re.escape(title.strip()) + r"[ \t]*\n([\s\S]*?)(?=^##[ \t]+|\Z)", re.M)


def section_text(content: str, title: str) -> str:
    match = _section_pattern(title).search(str(content or ""))
    return match.group(1).strip() if match else ""


def replace_section_text(content: str, title: str, text: str) -> str:
    """把某节内容替换成 text；节不存在则追加。"""
    pattern = _section_pattern(title)
    body = str(content or "")
    if pattern.search(body):
        return pattern.sub(lambda _m: f"## {title}\n\n{text}\n\n", body, count=1)
    return f"{body.rstrip()}\n\n## {title}\n\n{text}\n"


async def _job_row(job_id: str):
    async with get_db() as db:
        return await (await db.execute("SELECT * FROM chapter_writing_jobs WHERE id=?", (job_id,))).fetchone()


async def _set_job(job_id: str, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{key}=?" for key in fields)
    async with get_db() as db:
        await db.execute(f"UPDATE chapter_writing_jobs SET {keys} WHERE id=?", (*fields.values(), job_id))
        await db.commit()


async def create_job(project_id: int, thread_id: str, block_pk: int, settings_override: dict | None = None) -> str:
    """建作业（同 block 只允许一个 queued/running；已存在则复用）。"""
    async with get_db() as db:
        block = await get_block(db, block_pk)
        if not block:
            raise ValueError("章节不存在")
        if int(block.get("project_id") or 0) != int(project_id):
            raise ValueError("章节不属于该项目")
        await db.execute("BEGIN IMMEDIATE")
        existing = await (await db.execute(
            "SELECT id FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')",
            (block_pk,))).fetchone()
        if existing:
            await db.commit()
            return existing[0]
        job_id = uuid.uuid4().hex
        snapshot = {
            "mode": "chapter_sse",
            "base_content": block.get("content") or "",
            "settings_override": settings_override or {},
            "created_at": time.time(),
        }
        await db.execute(
            "INSERT INTO chapter_writing_jobs(id,project_id,thread_id,block_id,status,stage,snapshot) "
            "VALUES(?,?,?,?,?,?,?)",
            (job_id, project_id, thread_id, block_pk, "queued", "排队中",
             json.dumps(snapshot, ensure_ascii=False)))
        await db.commit()
        return job_id


def start_runner(job_id: str) -> None:
    if job_id in _RUNNERS and not _RUNNERS[job_id].done():
        return
    _RUNNERS[job_id] = asyncio.create_task(run_job(job_id))


async def run_job(job_id: str) -> None:
    """执行作业：正文逐节流式生成 + 一次性落库。"""
    row = await _job_row(job_id)
    if not row:
        return
    snapshot = json.loads(row["snapshot"] or "{}")
    project_id, thread_id, block_pk = int(row["project_id"]), row["thread_id"], int(row["block_id"])
    try:
        await _set_job(job_id, status="running", stage="读取计划")
        await emit(job_id, "job", {"job_id": job_id, "status": "running", "stage": "读取计划", "progress": 0})

        async with get_db() as db:
            block = await get_block(db, block_pk)
            from services.writing_plans import plan_of_block
            found = await plan_of_block(db, project_id, thread_id, str(block.get("block_id")))
            from services.writing_settings import resolve_model
            outline, source = [], "existing"
            if found:
                for item in found[1].get("chapters") or []:
                    if str(item.get("block_id")) == str(block.get("block_id")):
                        outline = item.get("outline") or []
                        break
            if not outline:
                await emit(job_id, "error", {"scope": "job", "message": "还没有章节大纲：请先在「本章计划」生成大纲",
                                             "recoverable": True})
                await _set_job(job_id, status="failed", stage="无大纲", error="无大纲")
                await emit(job_id, "job", {"job_id": job_id, "status": "failed", "stage": "无大纲", "progress": 0})
                return
            models = {}
            for role in ("chief", "text", "diagram", "table"):
                models[role] = (await resolve_model(db, project_id, block_pk, role)).get("used", "")

        await emit(job_id, "plan", {
            "source": source, "models": models,
            "sections": [{"index": i, "title": s.get("title"), "words": s.get("words"),
                          "deliverables": s.get("deliverables") or []} for i, s in enumerate(outline)],
        })

        # 找出未写的节（实义字数 < 20 视为未写）
        base = str(block.get("content") or "")
        draft = base
        pending = [s for s in outline if len(re.sub(r"\s+", "", section_text(base, str(s.get("title"))))) < 20]
        total = max(1, len(pending))
        for index, section in enumerate(pending):
            row_now = await _job_row(job_id)
            if row_now and row_now["status"] == "cancelled":
                await emit(job_id, "job", {"job_id": job_id, "status": "cancelled", "stage": "已取消",
                                           "progress": 0})
                return
            title = str(section.get("title"))
            await _set_job(job_id, stage=f"正在写：{title}")
            await emit(job_id, "job", {"job_id": job_id, "status": "running", "stage": f"正在写：{title}",
                                       "progress": round(index / total * 100)})
            await emit(job_id, "role", {"role": "text", "state": "working", "model": models.get("text", ""),
                                        "section_index": index})
            for role in ("diagram", "table"):
                if (section.get("deliverables") or []):
                    await emit(job_id, "role", {"role": role, "state": "queued",
                                                "model": models.get(role, ""), "section_index": index})
            text = ""
            async for chunk in _stream_section(block, section, thread_id, models.get("text", "")):
                text += chunk
                await emit(job_id, "delta", {"index": index, "text": chunk})
            if len(re.sub(r"\s+", "", text)) < 60:
                await emit(job_id, "error", {"scope": "section", "index": index, "message": "本节没有生成出可用内容",
                                             "recoverable": True})
                await emit(job_id, "section", {"index": index, "title": title, "state": "failed", "words": 0})
                continue
            draft = replace_section_text(draft, title, text)[:_CONTENT_LIMIT]
            await emit(job_id, "section", {"index": index, "title": title, "state": "done",
                                           "words": len(re.sub(r"\s+", "", text))})
            await emit(job_id, "role", {"role": "text", "state": "done", "model": models.get("text", ""),
                                        "section_index": index})
            for role in ("diagram", "table"):
                await emit(job_id, "role", {"role": role, "state": "skipped", "model": models.get(role, ""),
                                            "section_index": index, "detail": "本版图表生成在 M3 接入"})

        # 落库：base 未被改动才写
        saved, reason = False, ""
        async with get_db() as db:
            current = await get_block(db, block_pk)
            if draft == base:
                reason = "内容与现有正文一致，未产生变更"
            elif (current.get("content") or "") != base:
                reason = "正文在生成期间被修改，已保留建议稿，不覆盖"
            elif draft != base:
                await add_revision(db, block_pk, base, summary="按大纲编写整章（chapter_sse）", source="ai")
                await update_block_content(db, block_pk, draft)
                saved = True
                reason = "已写入正文"
        await _set_job(job_id, status="done", stage="已完成", content=draft, saved=1 if saved else 0)
        await emit(job_id, "done", {"content": draft, "saved": saved, "reason": reason, "models": models})
        await emit(job_id, "job", {"job_id": job_id, "status": "done", "stage": "已完成", "progress": 100})
    except Exception as exc:  # noqa: BLE001
        await _set_job(job_id, status="failed", stage="失败", error=str(exc)[:300])
        await emit(job_id, "error", {"scope": "job", "message": str(exc)[:300], "recoverable": False})
        await emit(job_id, "job", {"job_id": job_id, "status": "failed", "stage": "失败", "progress": 0})


async def _stream_section(block: dict, section: dict, thread_id: str, model: str):
    """流式写一节（复用分节提示词；模型来自设置解析）。"""
    from agents.chapter_team import RULES
    from services.config_store import OPENAI_COMPATIBLE_PROVIDERS, config_store
    from services.section_outline import SECTION_SYSTEM, build_section_payload
    from infra.llm.clients import stream_claude, stream_openai

    payload = build_section_payload({}, {"title": section.get("title"), "points": section.get("points") or [],
                                         "words": section.get("words") or 900,
                                         "basis": section.get("basis") or [],
                                         "how": section.get("how") or {},
                                         "deliverables": section.get("deliverables") or []},
                                    str(block.get("content") or "")[:2000])
    user_prompt = (
        f"所属章节：{payload.get('chapter') or '本章'}\n"
        f"本次只写这一节：{payload.get('section')}\n"
        f"本节要点：{'；'.join(payload.get('points') or []) or '（按节标题展开）'}\n"
        f"目标字数：约 {payload.get('words') or 900} 字\n"
        f"本节依据：{'；'.join(str((b or {}).get('ref') or '') for b in (payload.get('basis') or [])) or '（继承本章依据）'}\n"
        f"本节交付物：{json.dumps(payload.get('deliverables') or [], ensure_ascii=False)}\n"
        "请直接输出这一节的正文 Markdown，不要标题、不要 JSON。"
    )
    configs = config_store.get_all() or []
    config = next((c for c in configs if model and str(c.model) == model), None) or (configs[0] if configs else None)
    if not config:
        return
    stream = stream_openai if config.provider in OPENAI_COMPATIBLE_PROVIDERS else stream_claude
    options = {"thinking": False} if getattr(config, "provider", "") == "deepseek" else {}
    async for chunk in stream(config, RULES + "\n" + SECTION_SYSTEM, user_prompt,
                              call_site="chapter_sse_text", **options):
        yield chunk


async def retry_job(job_id: str) -> bool:
    """重试：把未完成/失败的作业重新排队并执行（runner 只补未写的节）。"""
    row = await _job_row(job_id)
    if not row:
        return False
    if row["status"] in ("queued", "running"):
        return True                                  # 已在跑，直接复用
    await _set_job(job_id, status="queued", stage="重新排队", error="", saved=0)
    start_runner(job_id)
    return True


async def recover_stale_jobs() -> int:
    """服务重启后：把遗留在 queued/running 的章节级作业标为失败（不可恢复）。"""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id FROM chapter_writing_jobs WHERE status IN ('queued','running') AND snapshot LIKE '%chapter_sse%'"
        )).fetchall()
        for row in rows:
            await db.execute(
                "UPDATE chapter_writing_jobs SET status='failed', stage='服务重启中断', error='服务重启' WHERE id=?",
                (row[0],))
        await db.commit()
    return len(rows)
