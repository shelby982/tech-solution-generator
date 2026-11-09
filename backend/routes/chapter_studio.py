"""按数据库章节主键隔离的编制工作台。"""
import asyncio
import base64
import io
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from db import get_db
from infra.llm.json_utils import extract_json_object
from services.block_store import get_block, list_blocks, create_material
from services.chapter_studio import (LAYOUTS, TEMPLATES, ids, content_hash, writing_units,
                                    recommend_layout, extract_material, material_key_points)

router = APIRouter(tags=['chapter-studio'])
TASKS = set()
EXPORT_LIMIT = asyncio.Semaphore(2)
PREVIEW_LIMIT = asyncio.Semaphore(1)
MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def require_block(db, block_id):
    block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, '章节不存在')
    return block


async def require_run(db, project_id, thread_id):
    row = await (await db.execute('SELECT * FROM workflow_runs WHERE project_id=? AND thread_id=?',
                                 (project_id, thread_id))).fetchone()
    if not row:
        raise HTTPException(404, '应答运行不属于当前项目')
    return dict(row)


async def source_for(block):
    if not block.get('run_thread_id'):
        return {}
    from routes.workflow import get_runner
    try:
        state = await get_runner().state(block['run_thread_id'])
    except (KeyError, RuntimeError, HTTPException):
        return {}
    section = next((s for s in (state.get('spec') or {}).get('toc', [])
                    if str(s.get('id')) == str(block['block_id'])), {})
    chain = section.get('chain') or {}
    context = []
    for original in (state.get('spec') or {}).get('source_toc', []):
        title = str(original.get('title') or '')
        if any(term in title for term in ('项目背景', '项目目标', '项目范围', '项目依据', '项目服务内容')):
            context.append({'title': title, 'text': original.get('raw_content') or '',
                            'source_file': original.get('source_file') or ''})
    return {**chain, 'raw_content': section.get('raw_content') or chain.get('raw_content') or '',
            'project_context': context}


async def chapter_context(db, block):
    from services.point_store import list_review_points, list_spec_requirements
    tid = block.get('run_thread_id')
    req_ids, point_ids = ids(block.get('spec_requirement_ids')), ids(block.get('review_point_ids'))
    requirements = [dict(r) for r in await list_spec_requirements(db, run_thread_id=tid)
                    if r.get('requirement_id') in req_ids] if tid else []
    points = [dict(p) for p in await list_review_points(db, run_thread_id=tid)
              if p.get('point_id') in point_ids] if tid else []
    return requirements, points


async def material_rows(db, block):
    rows = await (await db.execute(
        '''SELECT m.*, cm.confirmed FROM chapter_materials cm JOIN materials m ON m.id=cm.material_id
           WHERE cm.block_id=? AND m.project_id=? AND m.deleted_at IS NULL ORDER BY m.id''',
        (block['id'], block['project_id']))).fetchall()
    result = []
    for row in rows:
        material = dict(row)
        chunks = [dict(c) for c in await (await db.execute(
            'SELECT * FROM material_chunks WHERE material_id=? ORDER BY chunk_index', (material['id'],))).fetchall()]
        excerpts = extract_material(chunks, block)
        image = material['type'] in ('png', 'jpg', 'jpeg', 'webp')
        result.append({k: material.get(k) for k in ('id', 'filename', 'type', 'parse_status', 'confirmed')} | {
            'image_url': f"/api/studio/materials/{material['id']}/image" if image else None,
            'excerpts': excerpts,
            'key_points': material_key_points(excerpts),
            'adopted': (f"/api/studio/materials/{material['id']}/image" in (block.get('content') or '')) if image
                       else (f"摘自《{material['filename']}》" in (block.get('content') or '')),
            'mapping_reason': '由本章上传或人工关联；原文摘录按标题关键词排序，需核对适用性',
        })
    return result


@router.get('/blocks/{block_id}/studio')
async def get_studio(block_id: int):
    async with get_db() as db:
        block = await require_block(db, block_id)
        source = await source_for(block)
        profile = await (await db.execute('SELECT * FROM chapter_studio WHERE block_id=?', (block_id,))).fetchone()
        profile = dict(profile) if profile else {'layout': recommend_layout(block, source), 'writing_status': 'draft'}
        if profile.get('writing_status') == 'complete' and profile.get('confirmed_hash') != content_hash(block.get('content')):
            profile['writing_status'] = 'review'
        requirements, points = await chapter_context(db, block)
        materials = await material_rows(db, block)
        from services.writing_workspace import node_scopes
        siblings = await list_blocks(db, block['project_id'], block.get('run_thread_id'), fallback_all=False)
        scope = node_scopes(siblings).get(block_id, {'role': 'content', 'unit_ids': [], 'pending_ids': []})
        response_labels = {}
        root_title = ''
        for item in siblings:
            if int(item.get('level') or 1) == 1:
                root_title = item['title']
            response_labels[item['id']] = '点对点应答' if '点对点' in root_title else '重点要求应答' if '重点' in root_title else '评分方案应答'
        related = [dict(id=item['id'], block_id=item['block_id'], title=item['title'], response_label=response_labels[item['id']]) for item in siblings
                   if item['id'] != block_id and set(ids(item.get('spec_requirement_ids'))) & set(ids(block.get('spec_requirement_ids')))]
        from services.confirmed_directory import is_locked
        return {'directory_locked': await is_locked(db, block), 'block': block, 'related_responses': related, 'scope': scope, 'content_version': content_hash(block.get('content')), 'profile': profile, 'layouts': LAYOUTS, 'requirements': requirements, 'points': points,
                'source': source, 'materials': materials,
                'missing': ['请补充或核对本章要求的真实证明材料'] if block.get('evidence_required') and not any(m['confirmed'] for m in materials) else []}


class ProfileBody(BaseModel):
    layout: Literal['solution', 'understanding', 'implementation', 'evidence', 'point_to_point']
    layout_prompt: str | None = None
    writing_status: Literal['draft', 'review', 'complete'] = 'draft'
    acknowledge_open_items: bool = False
    expected_content_hash: str | None = None


async def completion_check(db,block):
    import re
    content=block.get('content') or ''
    job=await (await db.execute('SELECT status,artifacts FROM chapter_writing_jobs WHERE block_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(block['id'],))).fetchone()
    artifacts=json.loads(job['artifacts']) if job else {}
    reviews=artifacts.get('reviews',{})
    issues=[{'reviewer':role,**issue} for role,result in reviews.items() for issue in result.get('finding',{}).get('issues',[])]
    return {'content_hash':content_hash(content),'empty':not content.strip(),
            'running':bool(job and job['status'] in ('queued','running')),
            'gaps': await _completion_gaps(db, block, content),'issues':issues,
            'notice':'AI 意见基于生成时的版本，仅供核对，不等同于人工验收。'}


async def _completion_gaps(db, block, content: str) -> list[str]:
    """提交前的"缺项"：正文里不该再有占位符（已被净化），但净化记录要让人确认。

    返回：被净化掉的占位说明（去重、最多 12 条）+ 万一还有残留的占位。
    """
    import re as _re

    gaps: list[str] = []
    residual = _re.findall(r'【待[^】]{0,40}】', content or '')
    if residual:
        gaps.extend(residual[:6])
    try:
        rows = await (await db.execute(
            "SELECT DISTINCT placeholder, reason FROM block_purged_items WHERE block_id=? "
            "ORDER BY id DESC LIMIT 12", (int(block['id']),))).fetchall()
        for placeholder, reason in rows:
            text = (str(placeholder or "").strip() or str(reason or "").strip())
            if text and text not in gaps:
                gaps.append(text[:80])
    except Exception:
        pass
    return gaps


@router.get('/blocks/{block_id}/studio/completion-check')
async def get_completion_check(block_id:int):
    async with get_db() as db:
        return await completion_check(db,await require_block(db,block_id))


@router.put('/blocks/{block_id}/studio')
async def save_studio(block_id: int, body: ProfileBody):
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        block = await require_block(db, block_id)
        if body.writing_status == 'complete':
            check=await completion_check(db,block)
            if check['empty']:raise HTTPException(409,'正文为空，请先编写后确认完成')
            if check['running']:raise HTTPException(409,'AI 编制尚未结束，请完成或停止后再核对')
            if body.expected_content_hash and body.expected_content_hash!=check['content_hash']:
                raise HTTPException(409,'正文已变化，请重新核对后确认')
            if (check['gaps'] or check['issues']) and not body.acknowledge_open_items:
                raise HTTPException(409,'存在待补充内容或检查意见，请查看摘要并明确确认')
        existing = await (await db.execute(
            "SELECT layout_prompt FROM chapter_studio WHERE block_id=?", (block_id,))).fetchone()
        layout_prompt = (body.layout_prompt if body.layout_prompt is not None
                         else str(existing["layout_prompt"] or "") if existing else "")
        await db.execute('''INSERT INTO chapter_studio(block_id, layout, layout_prompt, writing_status, confirmed_hash) VALUES(?,?,?,?,?)
            ON CONFLICT(block_id) DO UPDATE SET layout=excluded.layout, layout_prompt=excluded.layout_prompt,
            writing_status=excluded.writing_status, confirmed_hash=excluded.confirmed_hash''',
            (block_id, body.layout, layout_prompt, body.writing_status, content_hash(block.get('content'))))
        await db.commit()
    return body.model_dump()


class LinkBody(BaseModel):
    material_id: int
    section: str = ''            # 绑定到哪一节（空=整章）
    confirmed: bool = False


@router.put('/blocks/{block_id}/studio/materials')
async def link_material(block_id: int, body: LinkBody):
    async with get_db() as db:
        block = await require_block(db, block_id)
        mat = await (await db.execute('SELECT * FROM materials WHERE id=? AND project_id=? AND role=\'source\' AND deleted_at IS NULL',
                                     (body.material_id, block['project_id']))).fetchone()
        if not mat:
            raise HTTPException(404, '仅可关联当前项目的自有素材')
        if body.confirmed and mat['parse_status'] != 'done':
            raise HTTPException(409, '请等待素材解析完成后再核对')
        section = str(getattr(body, 'section', '') or '').strip()[:60]
        await db.execute('''INSERT INTO chapter_materials(block_id, material_id, confirmed, section) VALUES(?,?,?,?)
            ON CONFLICT(block_id, material_id) DO UPDATE SET confirmed=excluded.confirmed,
              section=CASE WHEN excluded.section<>'' THEN excluded.section ELSE chapter_materials.section END''',
            (block_id, body.material_id, int(body.confirmed), section))
        await db.execute("UPDATE chapter_studio SET writing_status='review' WHERE block_id=? AND writing_status='complete'", (block_id,))
        await db.commit()
    return {'linked': True}


@router.delete('/blocks/{block_id}/studio/materials/{material_id}')
async def unlink_material(block_id: int, material_id: int):
    async with get_db() as db:
        await require_block(db, block_id)
        await db.execute('DELETE FROM chapter_materials WHERE block_id=? AND material_id=?', (block_id, material_id))
        await db.execute("UPDATE chapter_studio SET writing_status='review' WHERE block_id=? AND writing_status='complete'", (block_id,))
        await db.commit()
    return {'unlinked': True}


class DiagramBody(BaseModel):
    text: str = Field(min_length=10, max_length=8000)
    title: str = Field(default='', max_length=80)
    type: str = Field(default='auto', max_length=16)   # auto / flow / layered / timeline


def normalize_diagram_nodes(raw) -> list[str]:
    """把模型给的关系链节点规整：去空、去重、去箭头、截断到 60 字，3~8 个。"""
    nodes = []
    for item in raw or []:
        text = str(item or '').replace('->', ' ').replace('→', ' ').strip()
        if not text:
            continue
        text = text[:60].strip()
        if text not in nodes:
            nodes.append(text)
    return nodes[:8]


@router.post('/blocks/{block_id}/studio/diagram')
async def generate_diagram(block_id: int, body: DiagramBody, model: str = ''):
    """用户给一段文字 + 期望图型 → 生成结构化信息图（`:::diagram type=…`）。

    图型：flow（流程）/ layered（层次）/ timeline（时间线）/ auto（由模型判断）。
    产品口径：不生成位图架构图（不可审计）；只把文字里的**结构**抽出来，
    插进正文由用户核对，可继续手改。
    """
    from agents.chapter_team import call_role
    from services.diagrams import normalize_model_output, render_diagram_markdown, TYPE_LABELS

    async with get_db() as db:
        block = await require_block(db, block_id)
    wanted = (body.type or 'auto').strip().lower()
    if wanted not in ('auto', 'flow', 'layered', 'timeline'):
        wanted = 'auto'
    hint = ('先判断这段文字的结构：并列/分类 → layered；先后步骤 → flow；按年份演进 → timeline。'
            if wanted == 'auto' else f'请严格用 {wanted}（{TYPE_LABELS.get(wanted, wanted)}）表达。')
    system = (
        '你是图示 Agent。把用户提供的文字抽成 2~12 个节点，输出 JSON：'
        '{"type":"flow|layered|timeline","title":"不超过20字",'
        '"nodes":[{"label":"短语，≤24字","level":0,"when":"年份（仅 timeline）"}]}。'
        + hint +
        ' 节点是短语而不是整句；layered 必须给出 level（0 顶层，最多 3 层）；'
        'timeline 必须给 when（四位年份）。只输出 JSON。'
    )
    payload = {'text': body.text, 'prefer_type': wanted}
    try:
        wanted = (getattr(body, 'model', '') or model or '').strip()
        if not wanted:
            try:
                from services.writing_settings import resolve_model
                async with get_db() as db2:
                    resolved = await resolve_model(db2, int(block.get('project_id') or 0), int(block_id), 'diagram')
                wanted = resolved.get('used') or ''
            except Exception:
                wanted = ''
        raw, model = await call_role('diagram', system, payload, {'diagram': wanted} if wanted else {})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    structure = normalize_model_output(raw, fallback_title=body.title or block.get('title') or '')
    if not structure:
        raise HTTPException(422, '这段文字抽不出结构，建议补充层级或流程信息')
    structure['title'] = (body.title or structure.get('title') or block.get('title') or '结构关系图')[:80]
    markdown = render_diagram_markdown(structure)
    return {'type': structure['type'], 'title': structure['title'],
            'nodes': structure['nodes'], 'edges': structure.get('edges') or [],
            'markdown': markdown, 'model': model}


class TableBody(BaseModel):
    model: str = Field(default='', max_length=120)     # 本次指定模型（前端 body 传）
    title: str = Field(default='', max_length=120)
    requirement: str = Field(default='', max_length=2000)
    text: str = Field(default='', max_length=8000)
    columns: int = Field(default=3, ge=2, le=6)


def _normalize_markdown_table(raw: str, max_rows: int = 12, max_cols: int = 6) -> str:
    """把模型输出规整成合法 Markdown 表：只留表格行、补齐列、限制行列数。"""
    lines = [line.strip() for line in str(raw or '').splitlines()]
    rows = [line for line in lines if line.startswith('|') and line.endswith('|')]
    if len(rows) < 2:
        return ''
    parsed = []
    for row in rows:
        cells = [c.strip() for c in row.strip('|').split('|')]
        if all(set(c) <= set('-: ') for c in cells):
            continue
        parsed.append(cells[:max_cols])
        if len(parsed) > max_rows + 1:
            break
    if len(parsed) < 2:
        return ''
    width = max(len(r) for r in parsed)
    parsed = [r + [''] * (width - len(r)) for r in parsed]
    out = ['| ' + ' | '.join(parsed[0]) + ' |',
           '| ' + ' | '.join(['---'] * width) + ' |']
    out += ['| ' + ' | '.join(r) + ' |' for r in parsed[1:]]
    return chr(10).join(out)


class OutlineBody(BaseModel):
    thread_id: str = Field(min_length=1)
    regenerate: bool = Field(default=False)
    allow_locked: bool = Field(default=False)


class SectionBody(BaseModel):
    thread_id: str = Field(min_length=8)
    title: str = Field(min_length=1, max_length=60)
    points: list[str] = Field(default_factory=list)
    words: int = Field(default=900, ge=400, le=1600)


@router.get('/blocks/{block_id}/writing-readiness')
async def writing_readiness(block_id: int, thread_id: str = "", threadId: str = "", section: str = ""):
    """写作前置：大纲/证据覆盖/是否允许概括写法。"""
    from services.writing_readiness import chapter_readiness, section_readiness
    thread_id = thread_id or threadId
    async with get_db() as db:
        block = await require_block(db, block_id)
    block = dict(block)
    if section:
        from services.writing_plans import chapter_plan
        async with get_db() as db:
            plan = await chapter_plan(db, block["project_id"], thread_id or str(block.get("run_thread_id") or ""),
                                      str(block.get("block_id") or ""))
        target = next((item for item in (plan.get("outline") or [])
                       if str(item.get("title")) == section), {"title": section})
        return await section_readiness(block, target, thread_id)
    return await chapter_readiness(block, thread_id)


@router.post('/blocks/{block_id}/studio/outline')
async def build_outline(block_id: int, body: OutlineBody):
    """生成 / 重新生成章节大纲（两级：节 → 要点），并存进计划。"""
    from services.writing_plans import plan_of_block
    from services.section_outline import OUTLINE_SYSTEM, fallback_outline, normalize_outline
    from agents.chapter_team import call_role

    async with get_db() as db:
        block = await require_block(db, block_id)
        found = await plan_of_block(db, block['project_id'], body.thread_id, str(block['block_id']))
    if not found:
        # 目录已确认但缺计划/大纲：先按一级目录补建计划，再走正常生成。
        try:
            from services.writing_plans import build_plan
            async with get_db() as build_db:
                await build_plan(build_db, block['project_id'], body.thread_id, str(block['block_id']).split('.')[0])
        except Exception:
            pass
        async with get_db() as fresh_db:
            found = await plan_of_block(fresh_db, block['project_id'], body.thread_id, str(block['block_id']))
    if not found:
        raise HTTPException(404, '这一章还没有编写计划')
    plan_id, plan = found
    chapter = next((item for item in plan.get('chapters') or [] if str(item.get('block_id')) == str(block['block_id'])), {})
    # 目录已确认时仍允许“补缺失大纲”（数据修复）；只禁止重做已有大纲。
    async with get_db() as db:
        from services.confirmed_directory import is_locked
        if chapter.get('outline') and await is_locked(db, block) and not body.allow_locked:
            raise HTTPException(409, '目录已确认，请在应答策划中调整并重新确认；编写页仅编辑正文')
    if chapter.get('outline') and not body.regenerate:
        return {'outline': chapter['outline'], 'cached': True}

    sections = None
    model = ''
    try:
        req_rows = []
        for index, requirement in enumerate(chapter.get('requirements') or []):
            if not isinstance(requirement, dict):
                continue
            rid = str(requirement.get('spec_requirement_id') or requirement.get('id') or f'r{index + 1}')
            req_rows.append({'id': rid, 'text': str(requirement.get('text') or '')[:500]})
        from services.chapter_studio import LAYOUTS
        async with get_db() as layout_db:
            layout_row = await (await layout_db.execute(
                "SELECT layout, layout_prompt FROM chapter_studio WHERE block_id=?", (block['id'],))).fetchone()
        layout = str((layout_row['layout'] if layout_row else '') or chapter.get('layout') or 'solution')
        layout_prompt = str((layout_row['layout_prompt'] if layout_row else '') or '')
        payload = {
            'chapter': chapter.get('title') or block.get('title') or '',
            'objective': chapter.get('objective') or '',
            'pages': chapter.get('pages') or 0,
            'chapter_type': LAYOUTS.get(layout, {}).get('label') or layout,
            'chapter_type_structure': LAYOUTS.get(layout, {}).get('blocks') or [],
            'chapter_type_prompt': layout_prompt[:2000],
            'requirements': req_rows[:10],
            'tasks': [t.get('title') for t in (chapter.get('tasks') or [])],
        }
        raw, model = await call_role('chief', OUTLINE_SYSTEM, payload, {})
        sections = normalize_outline(raw, chapter)
        valid_ids = {item['id'] for item in req_rows}
        assigned = set()
        for section in sections:
            kept = [str(x) for x in (section.get('requirement_ids') or []) if str(x) in valid_ids]
            section['requirement_ids'] = kept
            assigned.update(kept)
        # 模型漏分配时，按关键词重合兜底，至少让每条要求有落点
        missing_ids = [item for item in req_rows if item['id'] not in assigned]
        for requirement in missing_ids:
            words = {w for w in str(requirement.get('text') or '') if '\u4e00' <= w <= '\u9fff'}
            best = None
            best_score = -1
            for section in sections:
                body = str(section.get('title') or '') + ' ' + ' '.join(section.get('points') or [])
                score = len(words & set(body))
                if score > best_score:
                    best, best_score = section, score
            if best is not None:
                best.setdefault('requirement_ids', []).append(requirement['id'])
                best['requirement_assignment'] = 'system_fallback'
                assigned.add(requirement['id'])
    except Exception:
        sections = fallback_outline(chapter)
    chapter['outline'] = sections
    async with get_db() as db:
        await db.execute("UPDATE writing_plans SET plan = ? WHERE id = ?", (json.dumps(plan, ensure_ascii=False), plan_id))
        await db.commit()
    return {'outline': sections, 'model': model, 'cached': False}


class AlignBody(BaseModel):
    thread_id: str = Field(min_length=8)


@router.get('/blocks/{block_id}/studio/section/stream')
async def stream_section(block_id: int, thread_id: str = '', title: str = '', points: str = '', words: int = 900, prompt: str = '', role: str = 'text', model: str = '', generic: bool = False):
    """按大纲写一节（SSE 推流）：delta 逐块下发，done 落库并回正文。"""
    import asyncio as _aio
    from services.writing_plans import plan_of_block
    from services.section_outline import (SECTION_REWRITE_SYSTEM, SECTION_SYSTEM,
                                          build_section_payload, replace_section)
    from services.section_context import build_section_context, render_context
    from services.material_cards import bound_cards, public_dto, usable_card
    from services.coverage_review import review_coverage
    from services.section_results import cited_sources, save_result
    from services.block_store import add_revision, update_block_content
    from services.config_store import config_store, OPENAI_COMPATIBLE_PROVIDERS
    from infra.llm.clients import stream_openai, stream_claude
    from agents.chapter_team import RULES
    from utils.sse import format_sse_event

    if not str(title or '').strip():
        raise HTTPException(422, '缺少本节标题')
    async with get_db() as db:
        block = await require_block(db, block_id)
        found = await plan_of_block(db, block['project_id'], thread_id, str(block['block_id']))
    chapter = {}
    if found:
        chapter = next((item for item in found[1].get('chapters') or [] if str(item.get('block_id')) == str(block['block_id'])), {})
    section = {'title': title.strip(), 'points': [p for p in str(points or '').split('||') if p.strip()], 'words': words}
    # 带上该节的三问（依据/写法/交付物）
    planned = next((x for x in (chapter.get('outline') or []) if str(x.get('title')) == title.strip()), {})
    section.update({'basis': planned.get('basis') or [], 'how': planned.get('how') or {},
                    'deliverables': planned.get('deliverables') or [],
                    'requirement_ids': planned.get('requirement_ids') or [], 'generic': bool(generic)})
    from services.writing_readiness import section_readiness
    _ready = await section_readiness(block, section, thread_id)
    if _ready.get("status") == "no_outline":
        async def _blocked():
            yield format_sse_event('error', {'message': '还没有章节大纲：请先在「本章计划」生成大纲', 'code': 'no_outline'})
        return StreamingResponse(_blocked(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})
    _coverage_before = {"total": _ready.get("needs_total", 0), "covered": _ready.get("covered", 0),
                        "missing": _ready.get("missing", 0)}
    try:
        _context = await build_section_context(block, section, thread_id)
        _context_text = render_context(_context)
    except Exception:  # noqa: BLE001
        _context = {"section_text": "", "materials": []}
        _context_text = ""
    try:
        async with get_db() as _db:
            _used_cards = [public_dto(item) for item in await bound_cards(_db, block_id, section["title"])
                           if usable_card(item)]
        for _item in (_context.get("materials") or []):
            _used_cards.append({"chunk_id": _item.get("material_id"), "chunk_kind": "file",
                                "source_title": _item.get("filename") or "素材",
                                "locator": (f"第 {_item.get('page')} 页" if _item.get("page") else ""),
                                "text": _item.get("content") or "", "authority": "自动检索",
                                "confidence_label": "中", "used": False, "section": title})
    except Exception:  # noqa: BLE001
        _used_cards = []
    payload = build_section_payload(chapter, section,
                                    str(_context.get("section_text") or block.get('content') or '')[:2500])
    extra_request = str(prompt or '').strip()
    # 用自然语言给模型（直接丢 JSON 会被模型"续写 JSON"）
    is_rewrite = str(role or '').strip() == 'rewrite'
    original_text = sectionText if False else ''  # 占位：原文在下面从正文抽取
    if is_rewrite:
        try:
            import re as _re2
            _body = str(block.get('content') or '')
            _pat = _re2.compile(r'^##[ \t]+' + _re2.escape(title.strip()) + r'[ \t]*\n([\s\S]*?)(?=^##[ \t]+|\Z)', _re2.M)
            _m = _pat.search(_body)
            original_text = (_m.group(1).strip() if _m else '')
        except Exception:
            original_text = ''
    from services.section_contract import contract_for as _contract_for
    _contract = _contract_for(title)
    _practice = ''
    if not extra_request:
        try:
            from services.section_practice import practice_of as _practice_of
            _practice = await _practice_of(block['project_id'], block['block_id'], title)
        except Exception:
            _practice = ''
    user_prompt = (
        f"所属章节：{payload.get('chapter') or '本章'}\n"
        f"本次只写这一节：{payload.get('section')}\n"
        + (f"{_context_text}\n\n" if _context_text else "")
        + f"本节要点：{'；'.join(payload.get('points') or []) or '（按节标题展开）'}\n"
        f"目标字数：约 {payload.get('words') or 900} 字\n"
        f"本章要求：{'；'.join(payload.get('requirements') or []) or '（无特别要求）'}\n"
        f"本节依据：{'；'.join(str((b or {}).get('ref') or '') for b in (payload.get('basis') or [])) or '（继承本章依据）'}\n"
        f"本节写法：{json.dumps(payload.get('how') or {}, ensure_ascii=False)}\n"
        f"本节交付物：{json.dumps(payload.get('deliverables') or [], ensure_ascii=False)}\n"
        + (f"【本节写法（按计划执行）】{_practice}\n" if _practice else "")
        + (f"【本节硬性要求】{_contract}\n" if _contract else "")
        + ("【用户选择】本节按概括写法生成：没有可用证据，不要编造具体项目事实；在结论处说明依据待补。\n" if generic else
           "硬性要求：如果上下文中有【本章技术规范条款】，正文必须逐条回应；\n"
           "每条要有「要求是什么 → 我们怎么做 → 证据/引用」的落点，不能只用「以招标文件为准」代替响应。\n")
        + (f"【本次额外要求】{extra_request}\n" if extra_request else '')
        + (f"【本节原文】\n{original_text[:4000]}\n" if is_rewrite and original_text else '')
        + ("这是**改写**：在【本节原文】基础上调整，保留引用与数字。" if is_rewrite and original_text
           else "请直接输出这一节的正文 Markdown，不要标题、不要 JSON。")
    )

    configs, index = config_store.get_configs_and_next_index()
    if not configs:
        raise HTTPException(409, '生成模型不可用')
    # 本次指定模型优先（用户在弹窗里选的），其次轮询默认
    config = next((c for c in configs if model and c.model == model), None) or configs[index % len(configs)]
    stream = stream_openai if config.provider in OPENAI_COMPATIBLE_PROVIDERS else stream_claude
    options = {'thinking': False} if getattr(config, 'provider', '') == 'deepseek' else {}
    system_prompt = RULES + '\n' + (SECTION_REWRITE_SYSTEM if is_rewrite else SECTION_SYSTEM)

    async def events():
        raw = ''
        try:
            async for chunk in stream(config, system_prompt, user_prompt,
                                      call_site='chapter_section_stream', **options):
                raw += chunk
                yield format_sse_event('delta', {'text': chunk})
        except Exception as exc:  # noqa: BLE001
            yield format_sse_event('error', {'message': str(exc)[:300]})
            return
        import re as _re
        text = _re.sub(r'^```[a-z]*\n|```$', '', str(raw).strip()).strip()
        from services.body_align import strip_invalid_diagrams
        from services.body_tables import ensure_table_captions
        text, _dropped = strip_invalid_diagrams(text)
        from services.body_tables import normalize_table_rows as _normalize_tables
        text = _normalize_tables(text)
        text, _captions = ensure_table_captions(text)
        if len(text) < 120:
            yield format_sse_event('error', {'message': '这一节没有生成出可用内容，请重试'})
            return
        before = str(block.get('content') or '')
        try:
            from services.placeholder_purge import purge
            text, _purged = purge(text)
        except Exception:
            _purged = []
        after, replaced = replace_section(before, section['title'], text)
        async with get_db() as db:
            await db.execute('BEGIN IMMEDIATE')
            current = await require_block(db, block_id)
            if (current.get('content') or '') != before:
                yield format_sse_event('error', {'message': '正文在生成期间被修改，本次结果保留供核对', 'proposal': after})
                return
            await add_revision(db, block_id, before, summary=f'分节生成：{section["title"]}', source='manual', manage_transaction=False)
            await update_block_content(db, block_id, after, status=block.get('status') or 'draft', commit=False)
            await db.commit()
        quality = {}
        if is_rewrite and original_text:
            import re as _re3
            before_cites = len(_re3.findall(r'【来源[:：]', original_text))
            after_cites = len(_re3.findall(r'【来源[:：]', text))
            before_words = len(_re3.sub(r'\s+', '', original_text))
            after_words = len(_re3.sub(r'\s+', '', text))
            warn = []
            if after_cites < before_cites:
                warn.append(f'引用少了 {before_cites - after_cites} 处')
            if before_words and after_words < before_words * 0.8:
                warn.append(f'字数缩水到 {int(after_words / before_words * 100)}%')
            quality = {'warnings': warn, 'words_before': before_words, 'words_after': after_words,
                       'citations_before': before_cites, 'citations_after': after_cites}
        try:
            _coverage_after = await review_coverage(block, section['title'], text, thread_id=thread_id)
        except Exception:  # noqa: BLE001
            _coverage_after = {}
        _result = {
            "section": section['title'], "model": getattr(config, 'model', ''),
            "context_evidence": _used_cards[:8],
            "cited_evidence": cited_sources(text),
            "coverage_before": _coverage_before, "coverage_after": _coverage_after,
            "generic": bool(generic), "words": len(_re.sub(r'\s+', '', text)),
        }
        try:
            await save_result(block_id, section['title'], _result)
        except Exception:  # noqa: BLE001
            pass
        yield format_sse_event('done', {'title': section['title'], 'content': after, 'quality': quality,
                                       'words': len(_re.sub(r'\s+', '', text)), 'replaced': replaced,
                                       'model': getattr(config, 'model', ''),
                                       'used_evidence': _used_cards[:8],
                                       'context_evidence': _used_cards[:8],
                                       'cited_evidence': _result["cited_evidence"],
                                       'coverage_before': _coverage_before,
                                       'coverage_after': _coverage_after,
                                       'generic': bool(generic)})

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


@router.post('/blocks/{block_id}/studio/table')
async def generate_table(block_id: int, body: TableBody, model: str = ''):
    """按本章要求生成一张**有内容的**表格草稿（Markdown）。

    产品口径：只输出结构与要点，**不编造具体数据**；缺数据处写【待补充：xx】，
    由用户核对或替换。产出落到正文，可继续手工编辑。
    """
    from agents.chapter_team import call_role

    async with get_db() as db:
        block = await require_block(db, block_id)
    title = (body.title or block.get('title') or '本章').strip()[:120]
    requirement = (body.requirement or '').strip()
    text = (body.text or '').strip()
    system = (
        '你是表格 Agent。根据章节标题、编写要求与正文要点，生成一张**可直接放进标书正文**的表格。'
        f'列数约 {body.columns} 列（2~6 列），行数不超过 12 行。'
        '第一行是表头，必须简洁（4~10 字）。'
        '**禁止编造具体数字、名称、日期**：缺数据处写【待补充：xx】。'
        '只输出 Markdown 表格本身，不要解释、不要代码块围栏。'
    )
    payload = {'title': title, 'requirement': requirement, 'text': text[:4000]}
    try:
        wanted = (getattr(body, 'model', '') or model or '').strip()
        if not wanted:
            try:
                from services.writing_settings import resolve_model
                async with get_db() as db2:
                    resolved = await resolve_model(db2, int(block.get('project_id') or 0), int(block_id), 'table')
                wanted = resolved.get('used') or ''
            except Exception:
                wanted = ''
        raw, model = await call_role('table', system, payload, {'table': wanted} if wanted else {})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    markdown = _normalize_markdown_table(raw if isinstance(raw, str) else str(raw))
    if not markdown:
        raise HTTPException(422, '这次没有生成出表格，请补充一句"表格要表达什么"再试')
    from services.body_tables import ensure_table_captions
    markdown, _ = ensure_table_captions(markdown if markdown.lstrip().startswith('**表') else f'**表：{title}**\n\n{markdown}')
    return {'markdown': markdown, 'title': title, 'model': model}


@router.post('/blocks/{block_id}/studio/materials')
async def upload_chapter_material(block_id: int, file: UploadFile = File(...)):
    from routes.materials import upload_material, UPLOAD_ROOT
    async with get_db() as db:
        block = await require_block(db, block_id)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix in ('.png', '.jpg', '.jpeg', '.webp'):
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, '图片不能超过 10 MB')
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 25_000_000:
                    raise ValueError('图片尺寸过大')
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(400, '图片无效或尺寸过大') from exc
        name = Path(file.filename).name
        relative = f"uploads/{block['project_id']}/{uuid.uuid4().hex}_{name}"
        path = UPLOAD_ROOT.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        async with get_db() as db:
            material = await create_material(db, block['project_id'], name, suffix[1:], relative, 'source', file_size=len(data))
            await db.execute("UPDATE materials SET parse_status='done' WHERE id=?", (material['id'],))
            await db.commit()
    else:
        response = await upload_material(block['project_id'], file, 'source', False)
        material = json.loads(response.body)
    await link_material(block_id, LinkBody(material_id=material['id']))
    return {'id': material['id'], 'filename': material['filename']}


async def image_bytes(db, material_id, project_id=None):
    from routes.materials import _abs_upload_path
    row = await (await db.execute("SELECT * FROM materials WHERE id=? AND role='source' AND deleted_at IS NULL", (material_id,))).fetchone()
    if not row or row['type'] not in ('png', 'jpg', 'jpeg', 'webp') or (project_id is not None and row['project_id'] != project_id):
        raise HTTPException(404, '图片不存在或不属于当前项目')
    path = _abs_upload_path(row['file_path'])
    if not path.is_file():
        raise HTTPException(404, '图片源文件已丢失')
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise HTTPException(413, '图片超过大小限制')
    return path.read_bytes(), row['type']


async def studio_image_bytes(db, url: str, project_id=None):
    """正文内部图片统一读取：素材图 / 设计版图表资产。"""
    if str(url).startswith('/api/studio/diagrams/'):
        asset_id = str(url).split('/')[-2]
        from services import diagram_assets
        asset = await diagram_assets.get_asset(asset_id)
        if not asset or (project_id is not None and int(asset["project_id"]) != int(project_id)):
            raise HTTPException(404, '图表不存在或不属于当前项目')
        try:
            return diagram_assets.read_png(asset), 'png'
        except FileNotFoundError as exc:
            raise HTTPException(404, '图表图片文件已丢失') from exc
    return await image_bytes(db, int(str(url).split('/')[-2]), project_id)


@router.get('/studio/materials/{material_id}/image')
async def get_image(material_id: int):
    async with get_db() as db:
        data, kind = await image_bytes(db, material_id)
    return Response(data, media_type='image/jpeg' if kind in ('jpg', 'jpeg') else f'image/{kind}',
                    headers={'X-Content-Type-Options': 'nosniff'})


class ContentPreviewBody(BaseModel):
    content: str = Field(max_length=500_000)


@router.post('/blocks/{block_id}/studio/content-preview')
async def preview_content(block_id: int, body: ContentPreviewBody):
    from infra.docx.document_model import parse_markdown, model_html, image_urls
    try:
        async with get_db() as db:
            block=await require_block(db,block_id)
            from services.confirmed_directory import is_locked, body_only
            content = body_only(body.content, block['title']) if await is_locked(db, block) else body.content
            model=parse_markdown(content)
            for url in image_urls(model):
                await studio_image_bytes(db,url,block['project_id'])
        return {'html':model_html(model), 'notice':'内容预览不模拟 Word 分页，请在导出后查看排版 PDF'}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


class DocumentBody(BaseModel):
    thread_id: str = Field(min_length=1)
    template: Literal['standard', 'formal', 'compact'] = 'standard'


@router.get('/studio/templates/{template}/sample')
async def template_sample(template: str, format: Literal['docx','pdf']='pdf'):
    from infra.docx.template_store import load_template
    from infra.docx.samples import sample_docx, sample_pdf
    if template not in TEMPLATES: raise HTTPException(404, '交付格式不存在')
    _, version=load_template(template)
    try:
        async with PREVIEW_LIMIT:
            data=await asyncio.to_thread(sample_pdf if format=='pdf' else sample_docx,template,version)
    except Exception as exc:
        raise HTTPException(503, '样张预览暂不可用，可先下载 Word 样张；'+str(exc)[:200]) from exc
    return Response(data, media_type='application/pdf' if format=='pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition':f'{"inline" if format=="pdf" else "attachment"}; filename="format-sample.{format}"'})


@router.get('/projects/{project_id}/studio/document')
async def get_document(project_id: int, threadId: str):
    async with get_db() as db:
        await require_run(db, project_id, threadId)
        row = await (await db.execute('SELECT template FROM response_document_settings WHERE project_id=? AND thread_id=?', (project_id, threadId))).fetchone()
        blocks = await list_blocks(db, project_id, threadId, fallback_all=False)
        profiles = {r['block_id']: dict(r) for r in await (await db.execute('''SELECT s.* FROM chapter_studio s JOIN blocks b ON b.id=s.block_id
             WHERE b.project_id=? AND b.run_thread_id=?''', (project_id, threadId))).fetchall()}
        units = writing_units(blocks)
        for b in units:
            p = profiles.get(b['id'])
            if p and p['writing_status'] == 'complete' and p['confirmed_hash'] != content_hash(b.get('content')):
                p['writing_status'] = 'review'
        jobs = [dict(r) for r in await (await db.execute('''SELECT id, status, progress, error, created_at FROM document_export_jobs
             WHERE project_id=? AND thread_id=? ORDER BY created_at DESC, rowid DESC LIMIT 5''', (project_id, threadId))).fetchall()]
        from infra.docx.template_store import load_template
        templates = {key: {**value, 'version': load_template(key)[1]} for key,value in TEMPLATES.items()}
        from services.writing_workspace import node_scopes, review_freshness
        review_rows = {row['block_id']: dict(row) for row in await (await db.execute(
            'SELECT r.* FROM chapter_reviews r JOIN blocks b ON b.id=r.block_id WHERE b.project_id=? AND b.run_thread_id=?',
            (project_id, threadId))).fetchall()}
        review_states = {}
        for block in blocks:
            review = review_rows.get(block['id'])
            if review:
                review_states[block['id']] = {'freshness': await review_freshness(db, block, review), 'satisfied': review['satisfied']}
        return {'review_states': review_states, 'template': row[0] if row else 'standard', 'templates': templates, 'profiles': profiles,
                'unit_ids': [b['id'] for b in units], 'jobs': jobs,
                'node_scopes': node_scopes(blocks)}


@router.put('/projects/{project_id}/studio/document')
async def set_document(project_id: int, body: DocumentBody):
    async with get_db() as db:
        await require_run(db, project_id, body.thread_id)
        await db.execute('''INSERT INTO response_document_settings(project_id, thread_id, template) VALUES(?,?,?)
            ON CONFLICT(project_id, thread_id) DO UPDATE SET template=excluded.template''', (project_id, body.thread_id, body.template))
        await db.commit()
    return body.model_dump()


class ExportBody(DocumentBody):
    scope: Literal['chapter', 'group', 'document'] = 'document'
    block_id: int | None = None


async def run_export(job_id):
    from infra.docx import sections_to_docx
    async with EXPORT_LIMIT:
        try:
            async with get_db() as db:
                row = await (await db.execute('SELECT snapshot FROM document_export_jobs WHERE id=?', (job_id,))).fetchone()
                snapshot = json.loads(row[0])
                await db.execute("UPDATE document_export_jobs SET status='rendering', progress=20, error='' WHERE id=?", (job_id,))
                await db.commit()
            images = {k: base64.b64decode(v) for k, v in snapshot['images'].items()}
            result = await asyncio.to_thread(sections_to_docx, snapshot['sections'], snapshot['title'],
                                             template=snapshot['template'], images=images,
                                             template_data=base64.b64decode(snapshot['template_data']) if snapshot.get('template_data') else None,
                                             include_toc=snapshot.get('scope') == 'document')
            async with get_db() as db:
                await db.execute("UPDATE document_export_jobs SET status='done', progress=100, result=? WHERE id=?", (result, job_id))
                await db.commit()
            await run_preview(job_id)
        except Exception as exc:
            async with get_db() as db:
                await db.execute("UPDATE document_export_jobs SET status='failed', error=? WHERE id=?", (str(exc)[:500], job_id))
                await db.commit()


def queue_export(job_id):
    task = asyncio.create_task(run_export(job_id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


async def run_preview(job_id):
    from infra.docx.preview import render_pdf
    async with PREVIEW_LIMIT:
        try:
            async with get_db() as db:
                row = await (await db.execute('SELECT result FROM document_export_jobs WHERE id=?', (job_id,))).fetchone()
                if not row or not row[0]: return
                await db.execute("UPDATE document_export_jobs SET preview_status='rendering',preview_error='' WHERE id=?", (job_id,))
                await db.commit()
            pdf = await asyncio.to_thread(render_pdf, row[0])
            async with get_db() as db:
                await db.execute("UPDATE document_export_jobs SET preview_pdf=?, preview_status='done' WHERE id=?", (pdf,job_id))
                await db.commit()
        except Exception as exc:
            async with get_db() as db:
                await db.execute("UPDATE document_export_jobs SET preview_status='failed',preview_error=? WHERE id=?", (str(exc)[:500],job_id))
                await db.commit()


@router.post('/projects/{project_id}/studio/exports', status_code=202)
async def create_export(project_id: int, body: ExportBody):
    import re
    async with get_db() as db:
        await require_run(db, project_id, body.thread_id)
        blocks = await list_blocks(db, project_id, body.thread_id, fallback_all=False)
        if body.scope != 'document':
            index = next((i for i, b in enumerate(blocks) if b['id'] == body.block_id), None)
            if index is None:
                raise HTTPException(404, '章节不属于当前应答')
            end = index + 1
            if body.scope == 'group':
                while end < len(blocks) and int(blocks[end].get('level') or 1) > int(blocks[index].get('level') or 1):
                    end += 1
            blocks = blocks[index:end]
        sections = [{'id': b['block_id'], 'title': b['title'], 'level': b.get('level') or 1,
                     'content': b.get('content') or '', 'done': True} for b in blocks]
        if not sections:
            raise HTTPException(409, '暂无可导出的章节')
        project = await (await db.execute('SELECT name FROM projects WHERE id=?', (project_id,))).fetchone()
        from services.split_answers import get_answers
        answer = (await get_answers(db, project_id, body.thread_id)).get('response_plan') or {}
        label = {'commerce': '商务应答', 'tech': '技术应答'}.get(answer.get('type'), '应答草稿')
        from infra.docx.document_model import parse_markdown, image_urls, MODEL_VERSION
        from infra.docx.template_store import load_template
        images = {}
        try:
            for section in sections:
                content = re.sub(r'\[/?[GME]\]', '', section['content'])
                if answer.get('confirmed'):
                    from services.confirmed_directory import body_only
                    content = body_only(content, section['title'])
                first = re.match(r'^\s*#{1,6}\s+([^\n]+)\n?', content)
                if first and first.group(1).strip() == section['title'].strip(): content = content[first.end():]
                section['document_blocks'] = parse_markdown(content)
                for url in image_urls(section['document_blocks']):
                    if url not in images:
                        data, _ = await studio_image_bytes(db, url, project_id)
                        images[url] = base64.b64encode(data).decode()
                        if sum(len(v) for v in images.values()) > 70_000_000:
                            raise HTTPException(413, '图片总量过大，请分组导出')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        snapshot = {'sections': sections, 'images': images, 'template': body.template,
                    'title': f'{project[0]} · {label}', 'scope': body.scope}
        template_data, template_version = load_template(body.template)
        snapshot.update({'template_data': base64.b64encode(template_data).decode(),
                         'template_version': template_version, 'document_model_version': MODEL_VERSION})
        job_id = uuid.uuid4().hex
        await db.execute('INSERT INTO document_export_jobs(id, project_id, thread_id, snapshot) VALUES(?,?,?,?)',
                         (job_id, project_id, body.thread_id, json.dumps(snapshot, ensure_ascii=False)))
        await db.commit()
    queue_export(job_id)
    return {'id': job_id, 'status': 'queued', 'empty_chapters': sum(not (b.get('content') or '').strip() for b in writing_units(blocks)),
            'notice': '已固定当前已保存正文、图片与模板版本；后续编辑不影响本次导出'}


@router.get('/projects/{project_id}/studio/exports/{job_id}')
async def export_status(project_id: int, job_id: str):
    async with get_db() as db:
        row = await (await db.execute('SELECT id, status, progress, error, created_at, preview_status, preview_error FROM document_export_jobs WHERE id=? AND project_id=?', (job_id, project_id))).fetchone()
    if not row:
        raise HTTPException(404, '导出任务不存在')
    return dict(row)


@router.get('/projects/{project_id}/studio/exports/{job_id}/preview')
async def download_preview(project_id: int, job_id: str):
    async with get_db() as db:
        row = await (await db.execute('SELECT preview_status,preview_pdf,preview_error FROM document_export_jobs WHERE id=? AND project_id=?', (job_id,project_id))).fetchone()
    if not row: raise HTTPException(404, '导出任务不存在')
    if row['preview_status'] != 'done': raise HTTPException(409, row['preview_error'] or '排版预览生成中，请稍后查看')
    return Response(row['preview_pdf'], media_type='application/pdf', headers={'Content-Disposition':'inline; filename="response-preview.pdf"'})


@router.post('/projects/{project_id}/studio/exports/{job_id}/preview',status_code=202)
async def retry_preview(project_id: int, job_id: str):
    async with get_db() as db:
        cur = await db.execute("UPDATE document_export_jobs SET preview_status='pending' WHERE id=? AND project_id=? AND status='done' AND preview_status='failed'", (job_id,project_id))
        if not cur.rowcount: raise HTTPException(409, '仅失败的排版预览可重试')
        await db.commit()
    task=asyncio.create_task(run_preview(job_id));TASKS.add(task);task.add_done_callback(TASKS.discard)
    return {'id':job_id}


@router.post('/projects/{project_id}/studio/exports/{job_id}/retry', status_code=202)
async def retry_export(project_id: int, job_id: str):
    async with get_db() as db:
        cur = await db.execute("UPDATE document_export_jobs SET status='queued', progress=0, error='' WHERE id=? AND project_id=? AND status='failed'", (job_id, project_id))
        if not cur.rowcount:
            raise HTTPException(409, '仅失败任务可重试')
        await db.commit()
    queue_export(job_id)
    return {'id': job_id, 'status': 'queued'}


@router.get('/projects/{project_id}/studio/exports/{job_id}/file')
async def download_export(project_id: int, job_id: str):
    from routes.export import _content_disposition
    async with get_db() as db:
        row = await (await db.execute('SELECT * FROM document_export_jobs WHERE id=? AND project_id=?', (job_id, project_id))).fetchone()
    if not row or row['status'] != 'done':
        raise HTTPException(409, '导出尚未完成')
    title = json.loads(row['snapshot'])['title']
    return Response(row['result'], media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition': _content_disposition(title + '.docx')})


async def recover_exports():
    """进程重启后恢复未完成任务，快照不变。"""
    async with get_db() as db:
        rows = await (await db.execute("SELECT id FROM document_export_jobs WHERE status IN ('queued','rendering')")).fetchall()
    for row in rows:
        queue_export(row[0])
    async with get_db() as db:
        rows = await (await db.execute("SELECT id FROM document_export_jobs WHERE status='done' AND preview_status IN ('pending','rendering')")).fetchall()
    for row in rows:
        task=asyncio.create_task(run_preview(row[0]));TASKS.add(task);task.add_done_callback(TASKS.discard)


class ReviewRunBody(BaseModel):
    tier: str = "auto"          # auto | fast | full（v3 分层调度）


@router.post('/blocks/{block_id}/studio/review')
async def review_chapter(block_id: int, body: ReviewRunBody | None = None):
    """章节评审（v3 分层）：auto 先跑 L0 规则，有硬伤走快评（单角色），否则完整双评审。"""
    from services.chapter_review import review_block

    try:
        result = await review_block(block_id, tier=(body.tier if body else "auto"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result


@router.get('/blocks/{block_id}/studio/review')
async def get_chapter_review(block_id: int):
    """读取最近一次章节评审（没有则 reviewed=false）。"""
    from services.chapter_review import load_review

    data = await load_review(block_id)
    return {"reviewed": bool(data and data.get("freshness") == "current"), "review": data}


class ReviewPatchBody(BaseModel):
    issue_index: int = 0


class ReviewPatchApplyBody(BaseModel):
    before: str = ""
    after: str = ""
    issue_index: int = -1


@router.post('/blocks/{block_id}/studio/review/patch')
async def review_patch(block_id: int, body: ReviewPatchBody):
    """评审 issue 级定点重写：只改问题对应的那一小段（返回补丁，不落库）。"""
    from services.review_rewrite import build_patch

    return await build_patch(block_id, int(body.issue_index))


@router.post('/blocks/{block_id}/studio/review/apply')
async def review_patch_apply(block_id: int, body: ReviewPatchApplyBody):
    """应用评审定点补丁（精确匹配替换 + 写 revision）。"""
    from services.review_rewrite import apply_patch

    try:
        return await apply_patch(block_id, body.before, body.after, issue_index=int(body.issue_index))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get('/blocks/{block_id}/studio/review/history')
async def chapter_review_history(block_id: int, limit: int = 10):
    """本节评审历史（分数变化曲线 + 与上轮分差）。"""
    from services.chapter_review import load_history

    return {"items": await load_history(block_id, limit)}


@router.get('/blocks/{block_id}/studio/review/issues')
async def chapter_review_issues(block_id: int):
    """本章评审 issue（状态机 + 证据链）：用于验证"是否真修好"，以及"凭什么这么说"。"""
    from db import get_db
    from services.block_store import get_block
    from services.chapter_review import load_review
    from services.review_evidence import attach_evidence
    from services.review_issues import list_issues

    async with get_db() as db:
        data = await list_issues(db, block_id)
        block = await get_block(db, block_id)
    review = await load_review(block_id)
    issues = (review or {}).get("issues") or []
    enriched = attach_evidence(str((block or {}).get("content") or ""), issues,
                               str((review or {}).get("criterion_text") or ""))
    by_point = {}
    for item in enriched:
        by_point[str(item.get("point") or "")[:80]] = item.get("evidence") or {}
    for item in data.get("items") or []:
        item["evidence"] = by_point.get(str(item.get("point") or "")[:80]) or {}
    return data


@router.get('/blocks/{block_id}/studio/review/precheck')
async def review_precheck(block_id: int):
    """L0 规则预检（零成本、秒级，不发 LLM）。"""
    from db import get_db
    from services.block_store import get_block
    from services.review_precheck import precheck

    async with get_db() as db:
        block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, "章节不存在")
    return precheck(str(block.get("content") or ""))


@router.get('/blocks/{block_id}/delivery-verdict')
async def delivery_verdict(block_id: int):
    """统一交付判断（一个数字 + 三条必办）——把规则分/评审/L0/否决风险收成一条结论。"""
    from db import get_db
    from services.block_store import get_block
    from services.chapter_review import load_review
    from services.delivery_verdict import build_delivery_verdict
    from services.review_precheck import precheck
    from services.writing_plans import chapter_plan

    async with get_db() as db:
        block = await get_block(db, block_id)
        if not block:
            raise HTTPException(404, "章节不存在")
        project_id = int(block.get("project_id") or 0)
        thread_id = str(block.get("run_thread_id") or "")

    plan = None
    try:
        async with get_db() as db:
            plan = await chapter_plan(db, project_id, thread_id, str(block.get('block_id') or ''))
    except Exception:
        plan = None
    summary = (plan or {}).get("summary") or {}
    tasks = ((plan or {}).get("delivery_tasks") or {})
    need_user = tasks.get("need_user") or []
    rule_score = int(summary.get("readiness_rule") or summary.get("readiness") or 100)
    review = await load_review(block_id)
    pre = precheck(str(block.get("content") or ""))

    fatal = []
    try:
        from services.scoring_verdict import collect_fatal_risks
        if review:
            fatal = collect_fatal_risks([{**item, "block_id": str(block.get("block_id") or "")}
                                         for item in (review.get("issues") or [])])
    except Exception:
        fatal = []

    return build_delivery_verdict(rule_score=rule_score, review=review, precheck=pre,
                                  fatal_risks=fatal, need_user=need_user)


@router.get('/blocks/{block_id}/studio/review/scoring-points')
async def scoring_point_review(block_id: int):
    """按本章评分点逐条核对关键词是否在正文出现；只做确定性检查。"""
    from services.scoring_point_review import review_block_points
    async with get_db() as db:
        block = await require_block(db, block_id)
        ids = json.loads(block.get("review_point_ids") or "[]")
        tid = str(block.get("run_thread_id") or "")
        from services.point_store import list_review_points
        points = await list_review_points(db, run_thread_id=tid)
    wanted = {str(x) for x in ids}
    picked = [p for p in points if str(p.get("point_id") or "") in wanted]
    result = review_block_points(dict(block), picked)
    result["block_id"] = str(block.get("block_id") or "")
    result["title"] = str(block.get("title") or "")
    return result


@router.get('/blocks/{block_id}/studio/review/rubric')
async def chapter_rubric(block_id: int):
    """章节达标线（按章类型给出满分要素 + 逐条核对）。"""
    from db import get_db
    from services.block_store import get_block
    from services.chapter_rubric import check_rubric, rubric_for

    async with get_db() as db:
        block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, "章节不存在")
    content = str(block.get("content") or "")
    rubric = rubric_for(str(block.get("title") or ""), content)
    return check_rubric(content, rubric)


@router.get('/blocks/{block_id}/studio/practice-candidates')
async def practice_candidates(block_id: int):
    """写法候选（来自评审 strengths），只读；采用需显式 POST。"""
    from db import get_db
    from services.chapter_review import load_review
    from services.practice_recycle import candidates_from_review

    review = await load_review(block_id)
    return {"candidates": candidates_from_review(review)}


class PracticeApplyBody(BaseModel):
    section: str = ""
    text: str = ""


@router.post('/blocks/{block_id}/studio/practice-candidates/apply')
async def practice_candidate_apply(block_id: int, body: PracticeApplyBody):
    """采用某条写法候选 → 写入该节写法（section_practices）。"""
    from db import get_db
    from services.block_store import get_block
    from services.practice_recycle import apply_candidate

    async with get_db() as db:
        block = await get_block(db, block_id)
        if not block:
            raise HTTPException(404, "章节不存在")
        try:
            return await apply_candidate(db, project_id=int(block.get("project_id") or 0),
                                         block_id=str(block.get("block_id") or ""),
                                         section=body.section, text=body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

class VerifyIssueBody(BaseModel):
    fingerprint: str = Field(min_length=1, max_length=100)


@router.post('/blocks/{block_id}/studio/review/verify-issue')
async def verify_review_issue(block_id: int, body: VerifyIssueBody):
    from agents.chapter_team import call_role
    from services.writing_workspace import versions
    async with get_db() as db:
        block = await require_block(db, block_id)
        issue = await (await db.execute('SELECT point,severity FROM review_issue_states WHERE block_id=? AND fingerprint=?',
            (block_id, body.fingerprint))).fetchone()
        if not issue:
            raise HTTPException(404, '问题不存在')
        baseline = await versions(db, block)
        from services.material_cards import bound_cards, usable_card
        evidence = [item for item in await bound_cards(db, block_id) if usable_card(item)]
        requirements, points = await chapter_context(db, block)
    raw, _ = await call_role('bao_zheng',
        '针对指定问题复核当前正文与证明。仅当正文及所需证据明确解决问题才通过。公开资料不能证明投标人的业绩、资质、人员、承诺。'
        '返回 JSON {"resolved":true或false,"reason":"具体依据","quote":"当前正文对应原文"}。',
        {'issue': issue['point'], 'content': block.get('content') or '', 'evidence': evidence,
         'requirements': requirements, 'points': points}, {})
    result = extract_json_object(raw)
    if not isinstance(result, dict):
        raise HTTPException(502, '复核未返回有效结果')
    quote = str(result.get('quote') or '')
    resolved = result.get('resolved') is True and bool(quote.strip()) and quote in (block.get('content') or '')
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        current = await require_block(db, block_id)
        if await versions(db, current) != baseline:
            raise HTTPException(409, '复核期间正文或依据发生变化，请重新复核')
        await db.execute("UPDATE review_issue_states SET status=?,verified_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,"
            "verified_content_version=?,verified_evidence_version=? WHERE block_id=? AND fingerprint=?",
            ('verified' if resolved else 'open', resolved, baseline['content_version'], baseline['evidence_version'], block_id, body.fingerprint))
        await db.commit()
    return {'resolved': resolved, 'reason': str(result.get('reason') or ''), 'quote': quote}


class ProjectFactsBody(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict, max_length=30)


@router.get('/projects/{project_id}/studio/facts')
async def get_project_facts(project_id: int):
    async with get_db() as db:
        row = await (await db.execute('SELECT facts FROM project_writing_facts WHERE project_id=?', (project_id,))).fetchone()
    return {'facts': json.loads(row['facts']) if row else {}}


@router.put('/projects/{project_id}/studio/facts')
async def put_project_facts(project_id: int, body: ProjectFactsBody):
    if any(not key.strip() or len(key) > 80 or len(value) > 2000 for key, value in body.facts.items()):
        raise HTTPException(422, '资料名称限 80 字，内容限 2000 字')
    async with get_db() as db:
        project = await (await db.execute('SELECT id FROM projects WHERE id=?', (project_id,))).fetchone()
        if not project:
            raise HTTPException(404, '项目不存在')
        await db.execute('INSERT INTO project_writing_facts(project_id,facts) VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET facts=excluded.facts',
            (project_id, json.dumps(body.facts, ensure_ascii=False)))
        await db.commit()
        blocks = await (await db.execute("SELECT id,title FROM blocks WHERE project_id=? AND length(trim(COALESCE(content,'')))>0", (project_id,))).fetchall()
    return {'facts': body.facts, 'affected': [dict(block) for block in blocks]}
