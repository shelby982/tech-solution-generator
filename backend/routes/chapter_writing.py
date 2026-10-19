import json
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from db import get_db
from services.chapter_writing import create_job,queue_job,writing_blocks,RUNNING_TASKS
from agents.chapter_team import ROLES

router=APIRouter(tags=['chapter-team'])


class TeamRequest(BaseModel):
    thread_id:str=Field(min_length=1)
    block_ids:list[int]=Field(min_length=1,max_length=3)
    models:dict[Literal['default','chief','text','table','diagram','wang_anshi','bao_zheng'],str]=Field(default_factory=dict)
    auto_save_empty:bool=True


class PlanRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    root_block_id: str = Field(min_length=1)


class RequirementItem(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    type: str = "text"


class RequirementsBody(BaseModel):
    block_id: str = Field(min_length=1)
    requirements: list[RequirementItem] = Field(default_factory=list)


class FigureItem(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    purpose: str = ""
    source: str = ""


class FiguresPlanBody(BaseModel):
    block_id: str = Field(min_length=1)
    tables: list[FigureItem] = Field(default_factory=list)
    figures: list[FigureItem] = Field(default_factory=list)


class BindBody(BaseModel):
    block_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    citation_id: str = Field(min_length=1)
    indices: list[int] = Field(default_factory=list)


class PlanConfirmRequest(BaseModel):
    leaf_block_ids: list[str] = Field(default_factory=list)   # 空 = 确认全部


@router.get('/projects/{project_id}/writing-plan-units')
async def writing_plan_units(project_id: int, threadId: str):
    """编写计划的「单位」：评分根 + 技术评分点的一级目录（编写页据此挂入口）。"""
    from services.writing_plans import plan_units
    async with get_db() as db:
        return await plan_units(db, project_id, threadId)


@router.post('/projects/{project_id}/writing-plans')
async def create_writing_plan(project_id: int, body: PlanRequest):
    """生成该一级目录的编写计划（确定性、不调模型），返回可确认的 draft。"""
    from services.writing_plans import build_plan
    async with get_db() as db:
        try:
            plan = await build_plan(db, project_id, body.thread_id, body.root_block_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    return plan


@router.get('/projects/{project_id}/writing-plans/{plan_id}')
async def read_writing_plan(project_id: int, plan_id: str):
    from services.writing_plans import load_plan
    async with get_db() as db:
        try:
            return await load_plan(db, project_id, plan_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.put('/projects/{project_id}/writing-plans/{plan_id}/requirements')
async def update_plan_requirements(project_id: int, plan_id: str, body: RequirementsBody):
    """编辑某章的逐条编写要求（要求是主线的锚点：素材/质检/进度都挂它）。"""
    from services.writing_plans import update_requirements
    async with get_db() as db:
        try:
            return await update_requirements(db, project_id, plan_id, body.block_id,
                                             [item.model_dump() for item in body.requirements])
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.get('/projects/{project_id}/chapters/{block_id}/plan')
async def read_chapter_plan(project_id: int, block_id: str, threadId: str):
    """**本章编写计划**：只包含这一章的要求依据 + 缺失素材（计划按章，不按大章节）。"""
    from services.writing_plans import chapter_plan
    async with get_db() as db:
        return await chapter_plan(db, project_id, threadId, block_id)


@router.put('/projects/{project_id}/writing-plans/{plan_id}/figures-plan')
async def put_figures_plan(project_id: int, plan_id: str, body: FiguresPlanBody):
    """编辑本章图表计划（每张表/图的用途与来源）。"""
    from services.writing_plans import update_figures_plan
    async with get_db() as db:
        try:
            return await update_figures_plan(db, project_id, plan_id, body.block_id,
                                             [item.model_dump() for item in body.tables],
                                             [item.model_dump() for item in body.figures])
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.get('/projects/{project_id}/writing-plans/{plan_id}/needs')
async def plan_needs(project_id: int, plan_id: str, threadId: str):
    """需求级充足度：每条要求 → 素材需求（已补 N 片段 / 待补）。"""
    from services.writing_plans import chapter_needs
    async with get_db() as db:
        return await chapter_needs(db, project_id, plan_id, threadId)


@router.post('/projects/{project_id}/writing-plans/{plan_id}/needs/bind')
async def bind_need(project_id: int, plan_id: str, body: BindBody):
    """把来源片段绑到某条要求上。"""
    from services.writing_plans import bind_snippets
    async with get_db() as db:
        try:
            return await bind_snippets(db, project_id, plan_id, body.block_id, body.requirement_id,
                                       body.citation_id, body.indices)
        except ValueError as exc:
            raise HTTPException(404, str(exc))


@router.get('/projects/{project_id}/writing-plans/{plan_id}/coverage')
async def plan_coverage(project_id: int, plan_id: str, threadId: str):
    """要求回应率：计划里每条要求是否已在正文有落点。"""
    from services.writing_plans import requirement_coverage
    async with get_db() as db:
        return await requirement_coverage(db, project_id, plan_id, threadId)


@router.post('/projects/{project_id}/writing-plans/{plan_id}/confirm', status_code=202)
async def confirm_writing_plan(project_id: int, plan_id: str, body: PlanConfirmRequest):
    """确认计划并起 job：确认过的计划会被预置进 job（主编不再重新规划）。"""
    from services.writing_plans import confirm_plan
    async with get_db() as db:
        try:
            return await confirm_plan(db, project_id, plan_id, body.leaf_block_ids)
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.post('/projects/{project_id}/studio/writing-jobs',status_code=202)
async def start_team(project_id:int,body:TeamRequest):
    from routes.chapter_studio import require_run,require_block,chapter_context,material_rows,source_for
    from services.chapter_studio import build_writing_prompt,recommend_layout
    from services.config_store import config_store
    configs,_=config_store.get_configs_and_next_index()
    if not configs:raise HTTPException(409,'请先配置可用的生成模型')
    if any(model not in {c.model for c in configs} for model in body.models.values()):raise HTTPException(409,'部分 Agent 所选模型不可用')
    prepared=[]
    async with get_db() as db:
        await require_run(db,project_id,body.thread_id)
        outlines=[dict(r) for r in await (await db.execute('SELECT block_id,title,level FROM blocks WHERE project_id=? AND run_thread_id=? ORDER BY order_idx',(project_id,body.thread_id))).fetchall()]
        for bid in dict.fromkeys(body.block_ids):
            block=await require_block(db,bid)
            if block['project_id']!=project_id or block['run_thread_id']!=body.thread_id:raise HTTPException(404,'章节不属于当前应答')
            reqs,points=await chapter_context(db,block)
            materials=[m for m in await material_rows(db,block) if m['confirmed']]
            source=await source_for(block);layout=recommend_layout(block,source)
            _,prompt=build_writing_prompt(block,reqs,points,materials,layout,source)
            package=json.loads(prompt)|{'block_id':block['block_id'],'layout':layout,'document_outline':outlines}
            # 编写规范（L0 全局 + L1 模块配方）注入生成上下文，并把版本记进 job（留痕）
            from services.writing_specs import spec_for
            from services.citations import citations_for_prompt, citation_line
            package['writing_spec']=await spec_for(db,layout)
            package['citations']=[citation_line(c) for c in await citations_for_prompt(db,project_id,block['block_id'])]
            from services.writing_plans import needs_for_prompt
            package['requirements']=await needs_for_prompt(db,project_id,body.thread_id,block['block_id'])
            if len(json.dumps(package,ensure_ascii=False))>160000:raise HTTPException(413,'章节依据过多，需分拆任务后编制')
            prepared.append((block,package))
        jobs=[]
        for block,package in prepared:
            job_id=await create_job(db,block,package,body.models,body.auto_save_empty)
            spec=package.get('writing_spec') or {}
            await db.execute('UPDATE chapter_writing_jobs SET artifacts=? WHERE id=?',
                (json.dumps({'spec':{'global':(spec.get('global') or {}).get('version'),
                                     'recipe':(spec.get('recipe') or {}).get('version')}},ensure_ascii=False),job_id))
            await db.commit()
            jobs.append(job_id)
    for job in jobs:queue_job(job)
    return {'jobs':jobs,'notice':'第一阶段每次最多试编三章；空章节自动保存初稿，已有正文保留为建议。'}


@router.get('/projects/{project_id}/studio/writing-jobs')
async def list_jobs(project_id:int,threadId:str):
    from routes.chapter_studio import require_run
    async with get_db() as db:
        await require_run(db,project_id,threadId)
        rows=await (await db.execute('''SELECT j.id,j.block_id,j.status,j.stage,j.saved,j.error,j.created_at,b.title
            FROM chapter_writing_jobs j LEFT JOIN blocks b ON b.id=j.block_id
            WHERE j.project_id=? AND j.thread_id=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT 20''',(project_id,threadId))).fetchall()
    return {'jobs':[dict(r) for r in rows],'roles':ROLES}


@router.get('/projects/{project_id}/studio/writing-jobs/{job_id}')
async def job_detail(project_id:int,job_id:str):
    async with get_db() as db:
        row=await (await db.execute('SELECT * FROM chapter_writing_jobs WHERE id=? AND project_id=?',(job_id,project_id))).fetchone()
    if not row:raise HTTPException(404,'编制任务不存在')
    data=dict(row);snapshot=json.loads(data.pop('snapshot'));data['artifacts']=json.loads(data['artifacts'])
    package=snapshot['package']
    data['blocks']=writing_blocks(data['artifacts'])
    if data['status']=='cancelled':
        for block in data['blocks']:
            if block['state']!='done':block['state']='stopped'
    data['preparation']={'criterion':package.get('criterion',''),'requirements':len(package.get('spec_requirements',[])),
        'materials':len(package.get('confirmed_materials',[])),'gaps':data['artifacts'].get('chief',{}).get('plan',{}).get('gaps',[])}
    data['base_content']=snapshot['base_content']
    return data


@router.post('/projects/{project_id}/studio/writing-jobs/{job_id}/cancel')
async def cancel_job(project_id:int,job_id:str):
    async with get_db() as db:
        cursor=await db.execute("UPDATE chapter_writing_jobs SET status='cancelled',stage='已停止编制' WHERE id=? AND project_id=? AND status IN ('queued','running')",(job_id,project_id))
        if not cursor.rowcount:raise HTTPException(409,'任务已结束，无需停止')
        await db.commit()
    task=RUNNING_TASKS.get(job_id)
    if task:task.cancel()
    return {'id':job_id,'status':'cancelled'}


@router.post('/projects/{project_id}/studio/writing-jobs/{job_id}/retry',status_code=202)
async def retry_job(project_id:int,job_id:str):
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        job=await (await db.execute("SELECT * FROM chapter_writing_jobs WHERE id=? AND project_id=? AND status IN ('failed','cancelled')",(job_id,project_id))).fetchone()
        if not job:raise HTTPException(409,'仅未完成任务可重试')
        active=await (await db.execute("SELECT id FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')",(job['block_id'],))).fetchone()
        if active:raise HTTPException(409,'该章节已有执行中的任务')
        await db.execute("UPDATE chapter_writing_jobs SET status='queued',error='' WHERE id=?",(job_id,));await db.commit()
    queue_job(job_id)
    return {'id':job_id}
