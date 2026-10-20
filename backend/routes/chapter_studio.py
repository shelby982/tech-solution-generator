"""按数据库章节主键隔离的编制工作台。"""
import asyncio
import base64
import io
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from db import get_db
from infra.llm.json_utils import extract_json_object
from services.block_store import get_block, list_blocks, create_material
from services.chapter_studio import (LAYOUTS, TEMPLATES, ids, content_hash, writing_units,
                                    recommend_layout, extract_material, material_key_points, build_writing_prompt)

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
        return {'profile': profile, 'layouts': LAYOUTS, 'requirements': requirements, 'points': points,
                'source': source, 'materials': materials,
                'missing': ['请补充或核对本章要求的真实证明材料'] if block.get('evidence_required') and not any(m['confirmed'] for m in materials) else []}


class ProfileBody(BaseModel):
    layout: Literal['solution', 'understanding', 'implementation', 'evidence', 'point_to_point']
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
            'gaps':re.findall(r'【待补充[^】]*】',content),'issues':issues,
            'notice':'AI 意见基于生成时的版本，仅供核对，不等同于人工验收。'}


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
        await db.execute('''INSERT INTO chapter_studio(block_id, layout, writing_status, confirmed_hash) VALUES(?,?,?,?)
            ON CONFLICT(block_id) DO UPDATE SET layout=excluded.layout, writing_status=excluded.writing_status,
            confirmed_hash=excluded.confirmed_hash''',
            (block_id, body.layout, body.writing_status, content_hash(block.get('content'))))
        await db.commit()
    return body.model_dump()


class LinkBody(BaseModel):
    material_id: int
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
        await db.execute('''INSERT INTO chapter_materials(block_id, material_id, confirmed) VALUES(?,?,?)
            ON CONFLICT(block_id, material_id) DO UPDATE SET confirmed=excluded.confirmed''',
            (block_id, body.material_id, int(body.confirmed)))
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
async def generate_diagram(block_id: int, body: DiagramBody):
    """用户粘贴文字 → 生成一幅结构/关系图（`:::diagram`）。

    产品口径：不生成 Visio 级位图架构图（不可审计），只把**文字里的结构**抽成
    关系链图，插进正文由用户核对。作者=图示 Agent，提示词与它保持一致。
    """
    from agents.chapter_team import call_role

    async with get_db() as db:
        block = await require_block(db, block_id)
    system = ('你是图示 Agent。把用户提供的文字抽成 3~8 个节点的**关系链**（单向箭头连接），'
              '节点是短语而不是整段句子，每个节点不超过 60 字，不要出现箭头符号本身。'
              '只输出 JSON：{"nodes":["节点1","节点2","节点3"]}')
    try:
        raw, model = await call_role('diagram', system, {'text': body.text}, {})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    parsed = extract_json_object(raw) if isinstance(raw, str) else (raw or {})
    nodes = normalize_diagram_nodes((parsed or {}).get('nodes'))
    if len(nodes) < 2:
        raise HTTPException(422, '这段文字抽不出结构，建议补充层级或流程信息')
    title = (body.title or block.get('title') or '结构关系图').strip()[:80]
    markdown = f':::diagram {title}\n' + ' -> '.join(nodes) + '\n:::'
    return {'nodes': nodes, 'markdown': markdown, 'title': title, 'model': model}


class TableBody(BaseModel):
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
            continue                      # 分隔行后面重建
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
    return '\n'.join(out)


@router.post('/blocks/{block_id}/studio/table')
async def generate_table(block_id: int, body: TableBody):
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
        raw, model = await call_role('table', system, payload, {})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    markdown = _normalize_markdown_table(raw if isinstance(raw, str) else str(raw))
    if not markdown:
        raise HTTPException(422, '这次没有生成出表格，请补充一句"表格要表达什么"再试')
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


@router.get('/studio/materials/{material_id}/image')
async def get_image(material_id: int):
    async with get_db() as db:
        data, kind = await image_bytes(db, material_id)
    return Response(data, media_type='image/jpeg' if kind in ('jpg', 'jpeg') else f'image/{kind}',
                    headers={'X-Content-Type-Options': 'nosniff'})


class DraftBody(ProfileBody):
    model: str = Field(default='', max_length=200)


class ContentPreviewBody(BaseModel):
    content: str = Field(max_length=500_000)


@router.post('/blocks/{block_id}/studio/content-preview')
async def preview_content(block_id: int, body: ContentPreviewBody):
    from infra.docx.document_model import parse_markdown, model_html, image_urls
    try:
        model=parse_markdown(body.content)
        async with get_db() as db:
            block=await require_block(db,block_id)
            for url in image_urls(model):
                await image_bytes(db,int(url.split('/')[-2]),block['project_id'])
        return {'html':model_html(model), 'notice':'内容预览不模拟 Word 分页，请在导出后查看排版 PDF'}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.post('/blocks/{block_id}/studio/draft')
async def draft_chapter(block_id: int, body: DraftBody):
    from services.config_store import config_store, OPENAI_COMPATIBLE_PROVIDERS
    from infra.llm.clients import generate_oneshot_openai, generate_oneshot_claude
    async with get_db() as db:
        block = await require_block(db, block_id)
        requirements, points = await chapter_context(db, block)
        materials = [m for m in await material_rows(db, block) if m['confirmed']]
        source = await source_for(block)
    if not config_store.is_configured():
        raise HTTPException(409, '请先配置可用模型；可以先编写正文或插入编排框架')
    system, prompt = build_writing_prompt(block, requirements, points, materials, body.layout, source)
    if len(prompt) > 180_000:
        raise HTTPException(413, '本章依据过多，请拆分编写或减少关联素材')
    configs, index = config_store.get_configs_and_next_index()
    if not configs:
        raise HTTPException(409, '没有可用于生成正文的模型配置')
    config = configs[index % len(configs)]
    if body.model:
        config = next((c for c in configs if c.model == body.model), None)
        if config is None:
            raise HTTPException(409, '当前选择的模型不可用，请在顶部切换模型后重试')
    generate = generate_oneshot_openai if config.provider in OPENAI_COMPATIBLE_PROVIDERS else generate_oneshot_claude
    try:
        from infra.llm.usage import run_context
        kwargs = {'thinking': False} if config.provider == 'deepseek' else {}
        with run_context(project_id=block['project_id'], thread_id=block.get('run_thread_id')):
            suggestion = await generate(config, system, prompt, max_tokens=6500, timeout=150, raise_on_empty=True,
                                        call_site='chapter_studio_draft', **kwargs)
    except Exception as exc:
        raise HTTPException(502, '草稿生成失败，请检查模型配置后重试；原正文已保留') from exc
    return {'suggestion': suggestion, 'base_hash': content_hash(block.get('content')), 'material_ids': [m['id'] for m in materials]}


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
        return {'template': row[0] if row else 'standard', 'templates': templates, 'profiles': profiles,
                'unit_ids': [b['id'] for b in units], 'jobs': jobs}


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
                first = re.match(r'^\s*#{1,6}\s+([^\n]+)\n?', content)
                if first and first.group(1).strip() == section['title'].strip(): content = content[first.end():]
                section['document_blocks'] = parse_markdown(content)
                for url in image_urls(section['document_blocks']):
                    if url not in images:
                        data, _ = await image_bytes(db, int(url.split('/')[-2]), project_id)
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
