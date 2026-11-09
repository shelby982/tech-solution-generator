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


_PROGRESS = {}
_LAST_PROGRESS = {}


async def emit(job_id: str, event: str, payload: dict) -> None:
    state = _PROGRESS.get(job_id)
    if state is not None:
        sections = state['sections']
        index = int(payload.get('index') or 0)
        if index < len(sections):
            section = sections[index]
            if event == 'delta':
                section['text'] = section.get('text', '') + str(payload.get('text') or '')
                section['state'] = 'running'
            elif event == 'section':
                section['state'] = payload.get('state') or 'done'
            elif event == 'chart':
                chart = next((item for item in section['charts'] if item['title'] == payload.get('title') and item['role'] == payload.get('role')), None)
                if chart is not None:
                    chart.update(payload)
        if event in ('delta', 'chart', 'section') and (event != 'delta' or time.monotonic() - _LAST_PROGRESS.get(job_id, 0) > 0.15):
            _LAST_PROGRESS[job_id] = time.monotonic()
            row = await _job_row(job_id)
            artifacts = json.loads(row['artifacts'] or '{}') if row else {}
            artifacts['live'] = state
            await _set_job(job_id, artifacts=json.dumps(artifacts, ensure_ascii=False), **({'content': state['before'] + state['sections'][0].get('text', '') + state['after']} if state.get('scope') == 'revision' else {}))
            from services.chapter_progress import progress_blocks
            snapshot = json.loads(row['snapshot'] or '{}') if row else {}
            await _broadcast(job_id, 'snapshot', {'id': job_id, 'status': 'running', 'blocks': progress_blocks(state, snapshot.get('base_content') or '')})
    await _broadcast(job_id, event, payload)


async def _broadcast(job_id, event, payload):
    for queue in list(_BUS.get(job_id, ())):
        try:
            queue.put_nowait({'event': event, 'data': payload})
        except asyncio.QueueFull:
            pass


def _section_pattern(title: str) -> re.Pattern:
    return re.compile(r"^##[ \t]+" + re.escape(title.strip()) + r"[ \t]*\n([\s\S]*?)(?=^##[ \t]+|\Z)", re.M)


def section_text(content: str, title: str) -> str:
    match = _section_pattern(title).search(str(content or ""))
    return match.group(1).strip() if match else ""


def replace_section_text(content: str, title: str, text: str) -> str:
    """把某节内容替换成 text；节不存在则追加。

    统一走 services.section_outline.replace_section：它支持 `## 标题`、`**标题**`、`3. 标题`
    三种写法并做标题归一化 —— 只认 `## ` 会让加粗标题的真实正文章节"替换不到→追加"，产生重复。
    """
    from services.section_outline import replace_section

    merged, _replaced = replace_section(content, title, text)
    return merged


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


async def create_job(project_id: int, thread_id: str, block_pk: int, settings_override: dict | None = None,
                     force_rewrite: bool = False, generic: bool = False, section_titles: list[str] | None = None,
                     expected_content: str | None = None, instruction: str = "", selection: dict | None = None,
                     clean_rewrite: bool = False) -> str:
    """建作业（同 block 只允许一个 queued/running；已存在则复用）。"""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        block = await get_block(db, block_pk)
        if not block:
            raise ValueError("章节不存在")
        if block.get("run_thread_id") != thread_id:
            raise ValueError("章节不属于当前应答运行")
        if expected_content is not None and expected_content != (block.get("content") or ""):
            raise ValueError("正文版本已变化，请刷新后再编制")
        if selection is not None and (not selection.get('selection') or ''.join(selection.get(key, '') for key in ('before', 'selection', 'after')) != (block.get('content') or '')):
            raise ValueError('选区与当前正文不一致，请重新选择')
        from services.block_store import list_blocks
        from services.writing_workspace import node_scopes
        scopes = node_scopes(await list_blocks(db, project_id, thread_id, fallback_all=False))
        if scopes.get(block_pk, {}).get('role') == 'structure':
            raise ValueError('结构节点请使用编制未写子章')
        if int(block.get("project_id") or 0) != int(project_id):
            raise ValueError("章节不属于该项目")
        existing = await (await db.execute(
            "SELECT id,snapshot FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')",
            (block_pk,))).fetchone()
        if existing:
            running = json.loads(existing['snapshot'] or '{}')
            if any((running.get(key) or default) != value for key, default, value in (
                ('selection', None, selection), ('instruction', '', instruction),
                ('section_titles', [], section_titles or []), ('force_rewrite', False, bool(force_rewrite)),
                ('clean_rewrite', False, bool(clean_rewrite)))):
                raise ValueError('本章已有不同范围的修改任务，请等待完成或先停止')
            await db.commit()
            return existing[0]
        job_id = uuid.uuid4().hex
        from services.writing_workspace import evidence_hash
        snapshot = {
            "mode": "chapter_sse",
            "evidence_version": await evidence_hash(db, block),
            "section_titles": section_titles or [], "instruction": instruction,
            "selection": selection,
            "editing": bool(selection or force_rewrite),
            "base_content": block.get("content") or "",
            "settings_override": settings_override or {},
            "force_rewrite": bool(force_rewrite),
            "clean_rewrite": bool(clean_rewrite),
            "generic": bool(generic),
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


_WRITE_LIMIT = asyncio.Semaphore(2)


async def run_job(job_id: str) -> None:
    async with _WRITE_LIMIT:
        try:
            await _execute_job(job_id)
        finally:
            state = _PROGRESS.get(job_id)
            if state and state.get('scope') == 'revision':
                row = await _job_row(job_id)
                if row and row['status'] in ('failed', 'cancelled'):
                    state['sections'][0]['state'] = 'failed' if row['status'] == 'failed' else 'stopped'
                    artifacts = json.loads(row['artifacts'] or '{}')
                    artifacts['live'] = state
                    await _set_job(job_id, artifacts=json.dumps(artifacts, ensure_ascii=False),
                        content=state['before'] + state['sections'][0].get('text', '') + state['after'])
            _PROGRESS.pop(job_id, None)
            _LAST_PROGRESS.pop(job_id, None)


async def _execute_job(job_id: str) -> None:
    """执行作业：正文逐节流式生成 + 一次性落库。"""
    row = await _job_row(job_id)
    if not row or row["status"] == "cancelled":
        return
    snapshot = json.loads(row["snapshot"] or "{}")
    project_id, thread_id, block_pk = int(row["project_id"]), row["thread_id"], int(row["block_id"])
    try:
        await _set_job(job_id, status="running", stage="读取计划")
        await emit(job_id, "job", {"job_id": job_id, "status": "running", "stage": "读取计划", "progress": 0})

        async with get_db() as db:
            block = await get_block(db, block_pk)
            if not block or (block.get('content') or '') != snapshot.get('base_content', ''):
                raise ValueError('排队期间正文发生变化，任务已停止，请重新选择范围')
            if snapshot.get('selection') or (snapshot.get('force_rewrite') and not snapshot.get('section_titles')):
                revision_block = block
            else:
                revision_block = None
        if revision_block is not None:
            await _run_revision(job_id, snapshot, revision_block)
            return
        async with get_db() as db:
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
                raise ValueError('请先在「本章计划」制定并确认大纲，再按大纲生成内容')
            from services.confirmed_directory import is_locked
            locked = await is_locked(db, block)
            models = {}
            for role in ("chief", "text", "diagram", "table"):
                models[role] = (await resolve_model(db, project_id, block_pk, role)).get("used", "")

        await emit(job_id, "plan", {
            "source": source, "models": models,
            "sections": [{"index": i, "title": s.get("title"), "words": s.get("words"),
                          "deliverables": s.get("deliverables") or []} for i, s in enumerate(outline)],
        })

        if snapshot.get('section_titles'):
            wanted = set(snapshot['section_titles'])
            for title in wanted - {str(section.get('title')) for section in outline}:
                if not section_text(block.get('content') or '', title):
                    raise ValueError('指定小节不在当前正文中')
                outline.append({'title': title, 'points': [], 'deliverables': []})
            outline = [section for section in outline if section.get('title') in wanted]
        # 找出未写的节（实义字数 < 20 视为未写）
        base = str(block.get("content") or "")
        draft = base
        all_checks: list[dict] = []
        section_records: list[dict] = []
        force_rewrite = bool(snapshot.get("force_rewrite"))
        generic = bool(snapshot.get("generic"))
        pending = list(outline) if force_rewrite else [
            s for s in outline if len(re.sub(r"\s+", "", section_text(base, str(s.get("title"))))) < 20]
        _PROGRESS[job_id] = {'directory_locked': locked, 'sections': [dict(title=section['title'], text='', state='queued', charts=[
            {'title': item.get('title') or '', 'role': 'diagram' if item.get('kind') == 'figure' else 'table', 'state': 'queued'}
            for item in (section.get('deliverables') or []) if item.get('kind') in ('figure', 'table')][:2]) for section in pending]}
        if pending:
            await emit(job_id, 'section', {'index': 0, 'state': 'queued'})
        total = max(1, len(pending))
        for index, section in enumerate(pending):
            row_now = await _job_row(job_id)
            if row_now and row_now["status"] == "cancelled":
                await emit(job_id, "job", {"job_id": job_id, "status": "cancelled", "stage": "已取消",
                                           "progress": 0})
                return
            title = str(section.get("title"))
            section = dict(section)
            section["generic"] = generic
            section["instruction"] = snapshot.get("instruction") or ""
            if force_rewrite:
                section['rewrite_original'] = section_text(base, title)
                if not section['rewrite_original']:
                    raise ValueError('小节标题与正文不一致，请重新选择当前正文中的小节')
            await _set_job(job_id, stage=f"正在写：{title}")
            await emit(job_id, "job", {"job_id": job_id, "status": "running", "stage": f"正在写：{title}",
                                       "progress": round(index / total * 100)})
            await emit(job_id, "role", {"role": "text", "state": "working", "model": models.get("text", ""),
                                        "section_index": index})
            chart_deliverables = [] if force_rewrite else [d for d in (section.get("deliverables") or [])
                                  if d.get("kind") in ("figure", "table")][:2]
            chart_tasks = []
            for role in ("diagram", "table"):
                if (section.get("deliverables") or []):
                    await emit(job_id, "role", {"role": role, "state": "queued",
                                                "model": models.get(role, ""), "section_index": index})
            # 图表与正文并行（图表不等正文）
            for deliverable in chart_deliverables:
                role = "diagram" if deliverable.get("kind") == "figure" else "table"
                chart_tasks.append(asyncio.create_task(
                    _generate_chart(job_id, index, section, deliverable, thread_id, models.get(role, ""), block)))
            text = ""
            started_at = time.monotonic()
            from infra.llm.usage import call_context
            with call_context(block_id=block_pk, section=title, role="text"):
                async for chunk in _stream_section(block, section, thread_id, models.get("text", "")):
                    text += chunk
                    await emit(job_id, "delta", {"index": index, "text": chunk})
            if force_rewrite:
                from services.revision_contract import validate_revision
                validate_revision(section['rewrite_original'], text, section['instruction'])
            charts = [await task for task in chart_tasks] if chart_tasks else []
            for chart in charts:
                if not chart.get('ok'):
                    await emit(job_id, 'chart', {**chart, 'state': 'failed'})
            section_checks: list[dict] = []
            if charts:
                text, chart_missing = merge_charts(text, charts)
                for note in chart_missing:
                    placeholder_item = {
                        "group": "auto", "severity": "block",
                        "title": f"图表占位未闭环：{note[:40]}",
                        "evidence": f"第 {index + 1} 节「{title}」",
                        "source": "占位符检查", "action": "autofix",
                    }
                    all_checks.append(placeholder_item)
                    await emit(job_id, "todo", {"index": index, "items": [placeholder_item]})
                # M4：图表校验（依据可追溯 / 图文矛盾 / 表格列）
                try:
                    from services.chart_checks import chart_items
                    refs = [str((b or {}).get("ref")) for b in (section.get("basis") or []) if (b or {}).get("ref")]
                    for chart, deliverable in zip([c for c in charts if c.get("ok")], chart_deliverables):
                        if chart.get("kind") == "design":
                            continue
                        section_checks += chart_items(chart["role"], chart.get("markdown") or "",
                                                      deliverable, text, refs)
                except Exception:
                    pass
                if section_checks:
                    all_checks.extend(section_checks)
                    await emit(job_id, "todo", {"index": index, "items": section_checks})
            if len(re.sub(r"\s+", "", text)) < 60:
                await emit(job_id, "error", {"scope": "section", "index": index, "message": "本节没有生成出可用内容",
                                             "recoverable": True})
                await emit(job_id, "section", {"index": index, "title": title, "state": "failed", "words": 0})
                continue
            from services.coverage_review import review_coverage
            coverage = await review_coverage(block, title, text, thread_id=thread_id)
            try:
                from services.material_cards import bound_cards, public_dto
                from services.section_results import cited_sources, save_result
                async with get_db() as _db:
                    _cards = [public_dto(item) for item in await bound_cards(_db, int(block.get("id") or 0), title)]
                await save_result(int(block.get("id") or 0), title, {
                    "section": title, "context_evidence": _cards[:8],
                    "cited_evidence": cited_sources(text), "coverage_before": {},
                    "coverage_after": coverage, "generic": bool(section.get("generic")),
                })
            except Exception:
                pass
            if coverage.get("missing_detail"):
                await emit(job_id, "todo", {"index": index, "items": [{
                    "group": "proof", "severity": "suggest",
                    "title": f"规范条款未回应：{item.get('title') or item.get('requirement_id')}",
                    "evidence": f"第 {index + 1} 节「{title}」",
                    "source": "技术规范覆盖", "action": "locate",
                } for item in coverage["missing_detail"][:5]]})
            draft = replace_section_text(draft, title, text)[:_CONTENT_LIMIT]
            words = len(re.sub(r"\s+", "", text))
            section_records.append({"index": index, "title": title, "role": "text",
                                    "model": models.get("text", ""), "words": words,
                                    "ms": int((time.monotonic() - started_at) * 1000),
                                    "charts": [{"role": c.get("role"), "model": c.get("model"),
                                                "ok": bool(c.get("ok"))} for c in charts],
                                    "coverage": coverage})
            await emit(job_id, "section", {"index": index, "title": title, "state": "done",
                                           "words": words, "model": models.get("text", ""),
                                           "ms": section_records[-1]["ms"], "coverage": coverage})
            await emit(job_id, "role", {"role": "text", "state": "done", "model": models.get("text", ""),
                                        "section_index": index})
            if not chart_deliverables:
                for role in ("diagram", "table"):
                    await emit(job_id, "role", {"role": role, "state": "skipped", "model": models.get(role, ""),
                                                "section_index": index, "detail": "本节无图表交付物"})

        if locked:
            from services.confirmed_directory import body_only
            draft = body_only(draft, block['title'])
        # 落库：base 未被改动才写
        saved, reason = False, ""
        async with get_db() as db:
            await db.execute('BEGIN IMMEDIATE')
            latest_job = await (await db.execute('SELECT status FROM chapter_writing_jobs WHERE id=?', (job_id,))).fetchone()
            if latest_job and latest_job['status'] == 'cancelled':
                return
            current = await get_block(db, block_pk)
            from services.writing_workspace import evidence_hash
            evidence_changed = snapshot.get('evidence_version') != await evidence_hash(db, current)
            charts_failed = any(chart.get('state') != 'done' for section in (_PROGRESS.get(job_id) or {}).get('sections', []) for chart in section['charts'])
            if charts_failed:
                reason = '图表尚未完成，已保留正文与占位，请重试或移除未完成项后保存'
            elif evidence_changed:
                reason = '采用材料或招标依据已变化，生成结果已保留待核对'
            elif draft == base:
                reason = "内容与现有正文一致，未产生变更"
            elif (current.get("content") or "") != base:
                reason = "正文在生成期间被修改，已保留建议稿，不覆盖"
            elif draft != base:
                # 表格规整：分隔行列数对齐表头（否则预览/导出会退化成一行文字）
                from services.body_tables import ensure_table_captions as add_table_captions
                from services.body_tables import normalize_table_rows
                draft = normalize_table_rows(draft)
                draft, _captions_added = add_table_captions(draft)
                # 正文净化：不留任何"待补充"字眼（用户要求：可以浅，但必须完整）
                # 系统自动补齐：政策文号 / 等级保护标准号（确定性信息，不需要用户点）
                from services.citation_enrich import enrich as enrich_citations
                draft, cite_changes = enrich_citations(draft)
                # 内部语法/术语也一并清掉，交付版本就是干净版本
                from services.delivery_gate import auto_fix as gate_auto_fix
                draft, _gate_changed, _ = gate_auto_fix(draft)
                model_note = " ".join(f"{role}={model}" for role, model in (models or {}).items() if model)
                await add_revision(db, block_pk, base,
                                   summary=f"按大纲编写整章（chapter_sse）· {model_note}"[:200], source="ai", manage_transaction=False)
                await update_block_content(db, block_pk, draft, commit=False)
                await db.commit()
                from services.section_context import SECTION_CONTEXT_VERSION
                # 只有全章重写/新章才把整章标为当前版本；只补未写节时旧节仍是旧上下文
                if force_rewrite or not base.strip():
                    await db.execute("UPDATE blocks SET context_version=? WHERE id=?",
                                     (SECTION_CONTEXT_VERSION, block_pk))
                    await db.commit()
                saved = True
                reason = "已写入正文"
        await _set_job(job_id, status="done", stage="已完成", content=draft, saved=1 if saved else 0,
                       artifacts=json.dumps({"sections": section_records, "checks": all_checks,
                                             "models": models, "live": _PROGRESS.get(job_id)}, ensure_ascii=False))
        _PROGRESS.pop(job_id, None)
        _LAST_PROGRESS.pop(job_id, None)
        await emit(job_id, "done", {"content": draft, "saved": saved, "reason": reason,
                                    "models": models, "checks": all_checks,
                                    "sections": section_records})
        await emit(job_id, "job", {"job_id": job_id, "status": "done", "stage": "已完成", "progress": 100})
    except Exception as exc:  # noqa: BLE001
        await _set_job(job_id, status="failed", stage="失败", error=str(exc)[:300])
        await emit(job_id, "error", {"scope": "job", "message": str(exc)[:300], "recoverable": False})
        await emit(job_id, "job", {"job_id": job_id, "status": "failed", "stage": "失败", "progress": 0})


def _placeholder_hint(section: dict) -> str:
    """告诉正文 Agent 在需要图表的位置放占位符。"""
    lines = []
    for deliverable in section.get("deliverables") or []:
        kind = deliverable.get("kind")
        title = str(deliverable.get("title") or "").strip()
        if not title:
            continue
        if kind == "figure":
            lines.append(f"· 需要一张图「{title}」：在该图应出现的位置单独一行写 [[FIGURE:{title}]]")
        elif kind == "table":
            lines.append(f"· 需要一张表「{title}」：在该表应出现的位置单独一行写 [[TABLE:{title}]]")
    if not lines:
        return ""
    return ("图表占位符（必须保留，后端会替换成真实图表；正文要有一句引言指向它）：\n"
            + "\n".join(lines) + "\n")


async def _stream_section(block: dict, section: dict, thread_id: str, model: str):
    """流式写一节：标包规范/计划要求/本节正文/素材 + 分节提示词。"""
    from agents.chapter_team import RULES
    from services.config_store import OPENAI_COMPATIBLE_PROVIDERS, config_store
    from services.section_outline import SECTION_SYSTEM, build_section_payload
    from services.section_context import build_section_context, render_context
    from infra.llm.clients import stream_claude, stream_openai

    try:
        context = await build_section_context(block, section, thread_id)
        context_text = render_context(context)
    except Exception:  # noqa: BLE001
        if 'rewrite_original' in section:
            raise ValueError('无法读取当前章节依据，原文已保留')
        context = {"chapter_requirements": [], "spec_clauses": [], "materials": [],
                   "manual_materials": "", "section_text": ""}
        context_text = ""
    payload = build_section_payload(
        {"title": str(block.get("title") or ""),
         "requirements": [str(x.get("text") or "") for x in context.get("chapter_requirements") or []]},
        {"title": section.get("title"), "points": section.get("points") or [],
         "words": section.get("words") or 900,
         "basis": section.get("basis") or [],
         "how": section.get("how") or {},
         "deliverables": section.get("deliverables") or []},
        str(context.get("section_text") or "")[:2500],
    )
    from services.section_contract import contract_for
    contract = contract_for(section.get("title") or "")
    chapter_requirement = ""
    try:
        async with get_db() as _rc_db:
            row = await (await _rc_db.execute(
                "SELECT chapter_requirement FROM chapter_studio WHERE block_id=?",
                (int(block.get("id") or 0),))).fetchone()
        chapter_requirement = str((row["chapter_requirement"] if row else "") or "").strip()
    except Exception:
        chapter_requirement = ""
    from services.section_practice import practice_of
    practice = await practice_of(int(block.get("project_id") or 0), str(block.get("block_id") or ""),
                                 str(section.get("title") or ""))
    user_prompt = (
        f"所属章节：{payload.get('chapter') or '本章'}\n"
        f"本次只写这一节：{payload.get('section')}\n"
        + (f"{context_text}\n\n" if context_text else "")
        + (f"【本章统一要求（每节都必须遵守，优先级高于本节写法）】{chapter_requirement}\n" if chapter_requirement else "")
        + (f"【本节写法（按计划执行）】{practice}\n" if practice else "")
        + (f"【本节硬性要求】{contract}\n" if contract else "")
        + f"本节要点：{'；'.join(payload.get('points') or []) or '（按节标题展开）'}\n"
        f"目标字数：约 {payload.get('words') or 900} 字\n"
        f"本节依据：{'；'.join(str((b or {}).get('ref') or '') for b in (payload.get('basis') or [])) or '（继承本章依据）'}\n"
        f"本节交付物：{json.dumps(payload.get('deliverables') or [], ensure_ascii=False)}\n"
        + (f"用户修改要求：{section.get('instruction')}\n" if section.get("instruction") else "")
        + _placeholder_hint(section)
        + (f"【用户选择】本节按概括写法生成：没有可用证据，不要编造具体项目事实；在结论处说明依据待补。\n" if section.get("generic") else "")
        + "硬性要求：如果【本章技术规范条款】存在，正文必须逐条回应；每条都要有\n"
        "「要求是什么 → 我们怎么做 → 证据/引用」的落点，不能只用「以招标文件为准」代替响应。\n"
        + "请直接输出这一节的正文 Markdown，不要标题、不要 JSON。"
    )
    async with get_db() as db:
        from services.confirmed_directory import is_locked
        locked = await is_locked(db, block)
    if locked:
        user_prompt += '\n目录已在应答策划中确认。本次仅编写指定章节正文，禁止生成或修改章节标题、编号、层级和顺序。需要分项时用加粗段首、普通段落、列表、图或表表达。\n'
    editing = 'rewrite_original' in section
    system = SECTION_SYSTEM
    if editing:
        system = (
            '你是应标文档精修编辑。严格只修改用户指定范围，直接输出修改后的完整 Markdown。'
            '以原文为基础落实用户要求，不套用固定段落数和字数。保留原文的标题层级、'
            '引用、图表、数字和承诺，除非用户明确要求修改相应内容。'
            '事实与数字的修改必须有输入依据，不得编造证明材料。'
        )
        user_prompt = (
            f"修改要求：{section.get('instruction') or '完善表达，保留有依据的事实和原结构'}\n"
            f"关联依据（仅作核对，不要求把全部依据写入当前选区）：\n{context_text}\n"
            f"以下是本次修改范围的完整原文，输出仅替换这个范围：\n{section['rewrite_original']}"
        )
    if locked:
        system += '\n应答目录已确认且不可修改。只返回当前章节正文，不输出任何 Markdown 标题或新章节编号。段内小标题使用加粗文字。'
    configs = config_store.get_all() or []
    config = next((c for c in configs if model and str(c.model) == model), None) or (configs[0] if configs else None)
    if not config:
        return
    stream = stream_openai if config.provider in OPENAI_COMPATIBLE_PROVIDERS else stream_claude
    options = {"thinking": False} if getattr(config, "provider", "") == "deepseek" else {}
    async for chunk in stream(config, RULES + "\n" + system, user_prompt,
                              call_site="chapter_sse_text", **options):
        yield chunk


async def _run_revision(job_id: str, snapshot: dict, block: dict) -> None:
    from services.writing_workspace import evidence_hash
    from services.writing_settings import resolve_model
    selected = snapshot.get('selection')
    clean = bool(snapshot.get('clean_rewrite'))
    before = '' if clean else (selected['before'] if selected else '')
    after = '' if clean else (selected['after'] if selected else '')
    original = '' if clean else (selected['selection'] if selected else snapshot['base_content'])
    async with get_db() as db:
        model = (await resolve_model(db, block['project_id'], block['id'], 'text')).get('used', '')
    section = ({'title': block['title'], 'instruction': snapshot.get('instruction') or ''}
               if clean else
               {'title': block['title'], 'rewrite_original': original, 'instruction': snapshot.get('instruction') or ''})
    async with get_db() as db:
        from services.confirmed_directory import is_locked
        locked = await is_locked(db, block)
    state = {'scope': 'revision', 'directory_locked': locked, 'before': before, 'after': after,
             'sections': [{'title': block['title'], 'text': '', 'state': 'running', 'charts': []}]}
    _PROGRESS[job_id] = state
    await emit(job_id, 'section', {'index': 0, 'state': 'running'})
    await emit(job_id, 'role', {'role': 'text', 'state': 'working', 'model': model})
    text = ''
    async for delta in _stream_section(block, section, block.get('run_thread_id') or '', model):
        current_job = await _job_row(job_id)
        if not current_job or current_job['status'] == 'cancelled':
            return
        text += delta
        await emit(job_id, 'delta', {'index': 0, 'text': delta})
    from services.revision_contract import validate_revision
    if not clean:
        validate_revision(original, text, section['instruction'])
    proposal = text if clean else before + text + after
    async with get_db() as db:
        from services.confirmed_directory import is_locked, body_only
        if await is_locked(db, block):
            proposal = body_only(proposal, block['title'])
    if len(proposal) > _CONTENT_LIMIT:
        raise ValueError('修改结果超出正文长度限制，原文已保留')
    await emit(job_id, 'section', {'index': 0, 'state': 'done'})
    saved = False
    reason = '正文或依据已变化，修改结果已保留待核对'
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        current = await get_block(db, block['id'])
        job = await (await db.execute('SELECT status FROM chapter_writing_jobs WHERE id=?', (job_id,))).fetchone()
        if not job or job['status'] == 'cancelled':
            return
        if current and (current.get('content') or '') == snapshot['base_content'] and await evidence_hash(db, current) == snapshot['evidence_version']:
            await add_revision(db, block['id'], snapshot['base_content'], 'AI 修改前正文', 'ai', manage_transaction=False)
            await update_block_content(db, block['id'], proposal, status='draft', commit=False)
            if snapshot.get('force_rewrite') and not snapshot.get('section_titles'):
                from services.section_context import SECTION_CONTEXT_VERSION
                await db.execute('UPDATE blocks SET context_version=? WHERE id=?',
                                 (SECTION_CONTEXT_VERSION, block['id']))
            saved = True
            reason = ('旧正文已清理并写入新正文，可继续编辑或撤销本次修改' if clean
                      else '修改已写入正文，可继续编辑或撤销本次修改')
        await db.execute('UPDATE chapter_writing_jobs SET status=?,stage=?,content=?,saved=?,artifacts=? WHERE id=?',
                         ('done', '修改完成', proposal, int(saved), json.dumps({'live': state, 'models': {'text': model}}, ensure_ascii=False), job_id))
        await db.commit()
    await emit(job_id, 'done', {'content': proposal, 'saved': saved, 'reason': reason, 'editing': True})


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


# ── 图表生成（M3）：不读正文，只用节计划的要素/列名 + 依据 + 素材 ──────────────
_PLACEHOLDER_RE = re.compile(r"\[\[(FIGURE|TABLE):([^\]]+)\]\]")


async def _generate_chart(job_id: str, index: int, section: dict, deliverable: dict,
                          thread_id: str, model: str, block: dict | None = None) -> dict:
    role = "diagram" if deliverable.get("kind") == "figure" else "table"
    title = str(deliverable.get("title") or "").strip()
    basis = "；".join(str((b or {}).get("ref") or "") for b in (section.get("basis") or []))
    elements = deliverable.get("elements") or section.get("points") or []
    await emit(job_id, "role", {"role": role, "state": "working", "model": model,
                                "section_index": index, "detail": title})
    await emit(job_id, "chart", {"role": role, "index": index, "title": title, "state": "working",
                                 "model": model})
    # 设计版图表（项目设置 opt-in）：失败自动回退到下面的快速结构图，不阻断章节。
    if role == "diagram" and block:
        try:
            from services.diagram_design import design_section_figure, design_type_for, project_quality
            quality = await project_quality(int(block["project_id"]))
            design_type = design_type_for(deliverable)
            enabled = quality == "design_all" or (
                quality == "design_architecture" and design_type in ("architecture", "layers"))
            if enabled:
                await emit(job_id, "chart", {"role": role, "index": index, "title": title,
                                             "state": "designing", "model": model})
                block_no = str((block or {}).get("block_id") or "").replace("s", "", 1)
                figure_no = f"图 {block_no}-{index + 1}" if block_no else f"图 {index + 1}"
                designed = await design_section_figure(block, section, deliverable, figure_no=figure_no)
                markdown = designed["markdown"]
                await emit(job_id, "chart", {"role": role, "index": index, "title": title,
                                             "state": "done", "markdown": markdown,
                                             "checks": designed.get("checks") or {},
                                             "model": designed.get("model") or model,
                                             "kind": "design"})
                await emit(job_id, "role", {"role": role, "state": "done",
                                            "model": designed.get("model") or model,
                                            "section_index": index})
                return {"ok": True, "role": role, "index": index, "title": title,
                        "markdown": markdown, "model": designed.get("model") or model,
                        "checks": designed.get("checks") or {}, "kind": "design"}
        except Exception as exc:  # noqa: BLE001
            await emit(job_id, "chart", {"role": role, "index": index, "title": title,
                                         "state": "fallback", "model": model,
                                         "error": str(exc)[:160]})
    try:
        from agents.chapter_team import call_role
        if role == "diagram":
            payload = {
                "title": title,
                "type": deliverable.get("type") or "layered",
                "elements": [str(x) for x in elements][:8],
                "basis": basis,
                "requirement": "按上述要素组织成层次/流程/时间线示意图；只用给出的要素，不新增内容、不编数字",
            }
            system = ("你是图示 Agent。把给定要素组织成结构清晰的示意图，"
                      "只输出 `:::diagram type=<layered|flow|timeline> <标题>` + 每行一个节点 + `:::`。"
                      "节点 3~8 个、每个不超过 30 字，禁止新增要素之外的内容。")
            raw, used = await call_role("diagram", system, payload, {"diagram": model} if model else {})
            markdown = str(raw or "").strip()
            from services.diagrams import parse_diagram
            lines = markdown.splitlines()
            if lines and lines[0].startswith(":::diagram"):
                body = lines[1:-1] if lines[-1].strip() == ":::" else lines[1:]
                parsed = parse_diagram(lines[0].strip(), body)
                nodes = parsed.get("nodes") or []
                checks = {"nodes": len(nodes)}
                if not 2 <= len(nodes) <= 12:
                    return {"ok": False, "role": role, "index": index, "title": title,
                            "state": "failed", "error": f"节点数 {len(nodes)} 不在 2~12", "model": used}
                markdown = "\n".join([lines[0]] + [str(n.get("label") if isinstance(n, dict) else n) for n in nodes]
                                      + [":::"])
        else:
            cols = deliverable.get("cols") or []
            payload = {"title": title, "columns": len(cols) or 3, "cols": cols, "basis": basis,
                       "requirement": "列名按给定 cols；缺数据写【待补充：xx】，不编造数字"}
            system = ("你是表格 Agent。按给定列名生成一张可直接放进标书的表格；"
                      "第一行表头用给定列名，行数不超过 10；缺数据写【待补充：xx】；只输出 Markdown 表格。")
            raw, used = await call_role("table", system, payload, {"table": model} if model else {})
            markdown = str(raw or "").strip()
            if "|" not in markdown:
                return {"ok": False, "role": role, "index": index, "title": title,
                        "state": "failed", "error": "未生成出表格", "model": used}
            if not markdown.lstrip().startswith("**表"):
                markdown = f"**表：{title}**\n\n{markdown}"
            checks = {"cols": len(cols)}
        await emit(job_id, "chart", {"role": role, "index": index, "title": title, "state": "done",
                                     "markdown": markdown, "checks": checks, "model": used})
        await emit(job_id, "role", {"role": role, "state": "done", "model": used, "section_index": index})
        return {"ok": True, "role": role, "index": index, "title": title, "markdown": markdown,
                "model": used, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        # 失败重试一次（图示换图型；表格换更明确的指令），仍失败才报
        try:
            await emit(job_id, "chart", {"role": role, "index": index, "title": title,
                                         "state": "retrying", "model": model})
            retry_deliverable = dict(deliverable)
            if role == "diagram":
                retry_deliverable["type"] = "flow" if deliverable.get("type") != "flow" else "layered"
            retry = await _generate_chart(job_id, index, section, retry_deliverable, thread_id, model, block)
            if retry.get("ok"):
                retry["retried"] = True
                return retry
        except Exception:  # noqa: BLE001
            pass
        await emit(job_id, "chart", {"role": role, "index": index, "title": title, "state": "failed",
                                     "error": str(exc)[:200], "model": model})
        await emit(job_id, "role", {"role": role, "state": "failed", "model": model, "section_index": index})
        return {"ok": False, "role": role, "index": index, "title": title, "error": str(exc)[:200]}



def merge_charts(text: str, charts: list[dict]) -> tuple[str, list[str]]:
    """按原位替换成功图表；失败项保留可见的待处理标记。"""
    missing: list[str] = []
    used: set[str] = set()
    for chart in charts:
        if not chart.get("ok"):
            continue
        pattern = re.compile(r"\[\[(FIGURE|TABLE):" + re.escape(str(chart.get("title"))) + r"\]\]")
        if pattern.search(text):
            text = pattern.sub(lambda _m: "\n\n" + chart["markdown"] + "\n\n", text, count=1)
            used.add(str(chart.get("title")))
    # 失败图表保留缺口，待重试或人工处理。
    for match in _PLACEHOLDER_RE.finditer(text):
        kind = match.group(1)
        missing.append(f"{'图' if kind == 'FIGURE' else '表'}「{match.group(2)}」未能生成，请重试或核对后移除")
        text = text.replace(match.group(0), f"【待补充：{'图' if kind == 'FIGURE' else '表'}「{match.group(2)}」生成失败】")
    text = _PLACEHOLDER_RE.sub("", text)
    for chart in charts:
        if chart.get("ok") and str(chart.get("title")) not in used:
            kind = "图" if chart.get("role") == "diagram" else "表"
            text = text.rstrip() + "\n\n" + chart["markdown"] + "\n"
            missing.append(f"{kind}「{chart.get('title')}」正文未留占位（已附在节末）")
    return re.sub(r"\n{3,}", "\n\n", text), missing


async def write_sections(job_id: str, block: dict, block_pk: int, sections: list[dict],
                         thread_id: str, models: dict, draft: str,
                         on_section_done=None) -> tuple[str, list[dict]]:
    """逐节写正文（图表并行），返回 (新草稿, 节级留痕)。run_job 与测试共用。"""
    records: list[dict] = []
    for index, section in enumerate(sections):
        row_now = await _job_row(job_id)
        if row_now and row_now["status"] == "cancelled":
            break
        title = str(section.get("title"))
        await emit(job_id, "role", {"role": "text", "state": "working", "model": models.get("text", ""),
                                    "section_index": index})
        chart_deliverables = [d for d in (section.get("deliverables") or [])
                              if d.get("kind") in ("figure", "table")][:2]
        chart_tasks = []
        for deliverable in chart_deliverables:
            role = "diagram" if deliverable.get("kind") == "figure" else "table"
            chart_tasks.append(asyncio.create_task(
                _generate_chart(job_id, index, section, deliverable, thread_id, models.get(role, ""), block)))
        text = ""
        started_at = time.monotonic()
        async for chunk in _stream_section(block, section, thread_id, models.get("text", "")):
            text += chunk
            await emit(job_id, "delta", {"index": index, "text": chunk})
        charts = [await task for task in chart_tasks] if chart_tasks else []
        if charts:
            text, _missing = merge_charts(text, charts)
        from services.coverage_review import review_coverage
        coverage = await review_coverage(block, title, text, thread_id=thread_id)
        try:
            from services.material_cards import bound_cards, public_dto
            from services.section_results import cited_sources, save_result
            async with get_db() as _db:
                _cards = [public_dto(item) for item in await bound_cards(_db, int(block.get("id") or 0), title)]
            await save_result(int(block.get("id") or 0), title, {
                "section": title, "context_evidence": _cards[:8],
                "cited_evidence": cited_sources(text), "coverage_before": {},
                "coverage_after": coverage, "generic": bool(section.get("generic")),
            })
        except Exception:
            pass
        if coverage.get("missing_detail"):
            await emit(job_id, "todo", {"index": index, "items": [{
                "group": "proof", "severity": "suggest",
                "title": f"规范条款未回应：{item.get('title') or item.get('requirement_id')}",
                "evidence": f"第 {index + 1} 节「{title}」",
                "source": "技术规范覆盖", "action": "locate",
            } for item in coverage["missing_detail"][:5]]})
        draft = replace_section_text(draft, title, text)[:_CONTENT_LIMIT]
        records.append({"index": index, "title": title, "role": "text",
                        "model": models.get("text", ""), "words": len(re.sub(r"\s+", "", text)),
                        "ms": int((time.monotonic() - started_at) * 1000),
                        "charts": [{"role": c.get("role"), "model": c.get("model"), "ok": bool(c.get("ok"))}
                                   for c in charts],
                        "coverage": coverage})
        await emit(job_id, "section", {"index": index, "title": title, "state": "done",
                                      "words": records[-1]["words"], "model": models.get("text", ""),
                                      "ms": records[-1]["ms"], "coverage": coverage})
        if on_section_done:
            await on_section_done(index, section, text, charts)
    return draft, records


async def _write_sections_for_test(content: str, sections: list[dict], thread_id: str, models: dict):
    """测试辅助：不落库、不发真实事件，直接跑 write_sections。"""
    draft, records = await write_sections("test-job", {"content": content}, 1, sections,
                                          thread_id, models, content)
    return draft, records
