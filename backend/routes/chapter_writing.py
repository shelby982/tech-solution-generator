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
            if len(json.dumps(package,ensure_ascii=False))>160000:raise HTTPException(413,'章节依据过多，需分拆任务后编制')
            prepared.append((block,package))
        jobs=[await create_job(db,block,package,body.models,body.auto_save_empty) for block,package in prepared]
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
