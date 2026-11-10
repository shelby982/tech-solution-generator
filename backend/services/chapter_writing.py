"""持久化多 Agent 编制队列；快照、阶段产物、人工修改保护。"""
import asyncio
import hashlib
import json
import uuid

from db import get_db
from agents.chapter_team import ChapterChiefAgent,SpecialistAgent,assemble,review_chapter,ROLES,STREAM_SINK

TASKS=set()
RUNNING_TASKS={}
CHAPTER_LIMIT=asyncio.Semaphore(1)


async def evidence_version(db,block_id):
    rows=await (await db.execute('''SELECT m.id,m.parse_status,cm.confirmed,c.id,c.content
        FROM chapter_materials cm JOIN materials m ON m.id=cm.material_id
        LEFT JOIN material_chunks c ON c.material_id=m.id
        WHERE cm.block_id=? AND m.deleted_at IS NULL ORDER BY m.id,c.id''',(block_id,))).fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows],ensure_ascii=False).encode()).hexdigest()


async def revision_id(db,block_id):
    return (await (await db.execute('SELECT COALESCE(MAX(id),0) FROM block_revisions WHERE block_id=?',(block_id,))).fetchone())[0]


async def create_job(db,block,package,settings,auto_save):
    jid=uuid.uuid4().hex
    snapshot={'package':package,'settings':settings,'auto_save':auto_save,
              'base_content':block.get('content') or '', 'base_revision':await revision_id(db,block['id']),
              'evidence_version':await evidence_version(db,block['id'])}
    # 同一章节只允许一个待执行任务，事务内串行检查，避免重复提交烧模型额度。
    await db.execute('BEGIN IMMEDIATE')
    existing=await (await db.execute("SELECT id FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')",(block['id'],))).fetchone()
    if existing:
        await db.commit();return existing[0]
    await db.execute('INSERT INTO chapter_writing_jobs(id,project_id,thread_id,block_id,snapshot) VALUES(?,?,?,?,?)',
                     (jid,block['project_id'],block['run_thread_id'],block['id'],json.dumps(snapshot,ensure_ascii=False)))
    await db.commit()
    return jid


async def stage(job_id,label,artifacts=None):
    async with get_db() as db:
        if artifacts is None:await db.execute('UPDATE chapter_writing_jobs SET stage=? WHERE id=?',(label,job_id))
        else:await db.execute('UPDATE chapter_writing_jobs SET stage=?,artifacts=? WHERE id=?',(label,json.dumps(artifacts,ensure_ascii=False),job_id))
        await db.commit()


async def finish(job_id,snapshot,artifacts,content):
    from infra.docx.document_model import parse_markdown,image_urls
    # 结构必须能被交付链路解析；专业 Agent 不能引入未授权图片。
    model=parse_markdown(content)
    allowed={m['image_url'] for m in snapshot['package'].get('confirmed_materials',[]) if m.get('image_url')}
    if set(image_urls(model))-allowed:raise ValueError('产物包含任务包之外的图片')
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        job=await (await db.execute('SELECT * FROM chapter_writing_jobs WHERE id=?',(job_id,))).fetchone()
        if job['status']=='cancelled':return
        block=await (await db.execute('SELECT * FROM blocks WHERE id=?',(job['block_id'],))).fetchone()
        if not block or block['project_id']!=job['project_id'] or block['run_thread_id']!=job['thread_id']:
            raise ValueError('原章节已删除或归属已变化，未写入正文')
        can_save=(snapshot['auto_save'] and not snapshot['base_content'].strip() and not (block['content'] or '').strip()
                  and await revision_id(db,block['id'])==snapshot['base_revision']
                  and await evidence_version(db,block['id'])==snapshot['evidence_version'])
        # 生成时的事实缺口不进正文，沉淀为"依据核对"待办（右栏「待办」可见）
        try:
            from services.chapter_cleanup import record_generation_gaps
            await record_generation_gaps(db, block['project_id'], job['thread_id'], block['block_id'], plan.get('gaps') or [])
        except Exception:
            pass
        if can_save:
            await db.execute("UPDATE blocks SET content=?,status='draft' WHERE id=?",(content,block['id']))
            number=(await (await db.execute('SELECT COALESCE(MAX(revision_no),0)+1 FROM block_revisions WHERE block_id=?',(block['id'],))).fetchone())[0]
            await db.execute("INSERT INTO block_revisions(block_id,revision_no,content,summary,source) VALUES(?,?,?,?,?)",
                             (block['id'],number,content,'多 Agent 协作初稿','chapter_team'))
            await db.execute("INSERT INTO chapter_studio(block_id,layout,writing_status) VALUES(?,?,'review') ON CONFLICT(block_id) DO UPDATE SET writing_status='review'",
                             (block['id'],snapshot['package']['layout']))
        reason=('初稿已自动保存，待人工核对' if can_save else
                '建议已保留：本次关闭自动保存' if not snapshot['auto_save'] else
                '建议已保留：已有正文、版本或素材发生变化，未覆盖')
        await db.execute("UPDATE chapter_writing_jobs SET status='done',stage=?,content=?,saved=?,artifacts=?,error='' WHERE id=?",
                         (reason,content,int(can_save),json.dumps(artifacts,ensure_ascii=False),job_id))
        await db.commit()


async def run_job(job_id):
    from infra.llm.usage import run_context
    async with CHAPTER_LIMIT:
        async with get_db() as db:
            row=await (await db.execute('SELECT * FROM chapter_writing_jobs WHERE id=?',(job_id,))).fetchone()
            if not row or row['status']!='queued':return
            snapshot=json.loads(row['snapshot']);artifacts=json.loads(row['artifacts'])
            await db.execute("UPDATE chapter_writing_jobs SET status='running',error='' WHERE id=?",(job_id,));await db.commit()
        try:
            with run_context(project_id=row['project_id'],thread_id=row['thread_id']):
                package,settings=snapshot['package'],snapshot['settings']
                # 本章统一要求（用户在「本章计划」里填的）：整章编写必须遵守
                try:
                    async with get_db() as _rc:
                        row = await (await _rc.execute(
                            "SELECT chapter_requirement FROM chapter_studio WHERE block_id=?",
                            (row['block_id'],))).fetchone()
                    package['chapter_requirement'] = str((row['chapter_requirement'] if row else '') or '').strip()
                except Exception:
                    package['chapter_requirement'] = ''
                if 'chief' not in artifacts:
                    await stage(job_id,'章节主编制定编排计划')
                    artifacts['chief']=await ChapterChiefAgent().plan(package,settings)
                    await stage(job_id,'编排计划已形成',artifacts)
                plan=artifacts['chief']['plan']
                lock=asyncio.Lock()
                async def persist():
                    async with lock:await stage(job_id,'专业 Agent 协作编制',artifacts)
                async def text_progress(pieces):
                    allowed={t['id'] for t in plan['tasks'] if t['kind']=='text'}
                    artifacts['live_text']={k:v for k,v in pieces.items() if k in allowed}
                    await persist()
                async def produce(role):
                    artifacts.setdefault('role_states',{})[role]='running';await persist()
                    token=STREAM_SINK.set(text_progress if role=='text' else None)
                    try:
                        artifacts[role]=await SpecialistAgent(role).produce(package,plan,settings)
                        artifacts['role_states'][role]='done';await persist()
                    except Exception:
                        artifacts['role_states'][role]='failed';await persist();raise
                    finally:STREAM_SINK.reset(token)
                roles=[r for r in ('text','table','diagram') if any(t['kind']==r for t in plan['tasks']) and r not in artifacts]
                if roles:
                    outputs=await asyncio.gather(*(produce(r) for r in roles),return_exceptions=True)
                    errors=[f'{ROLES[r]}：{out}' for r,out in zip(roles,outputs) if isinstance(out,BaseException)]
                    if errors:raise ValueError('；'.join(errors))
                content=assemble(plan,artifacts)
                await stage(job_id,'按主编计划合稿',artifacts)
                if 'reviews' not in artifacts:
                    await stage(job_id,'王安石／包拯独立审校')
                    async def reviewed(role,result):
                        artifacts.setdefault('review_progress',{})[role]=result
                        await stage(job_id,'王安石／包拯独立审校',artifacts)
                    artifacts['reviews']=await review_chapter(package,content,settings,on_review=reviewed)
                    await stage(job_id,'审校结果已保存',artifacts)
                await finish(job_id,snapshot,artifacts,content)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with get_db() as db:
                await db.execute("UPDATE chapter_writing_jobs SET status='failed',error=? WHERE id=? AND status!='cancelled'",(str(exc)[:1500],job_id));await db.commit()


def queue_job(job_id):
    task=asyncio.create_task(run_job(job_id));TASKS.add(task);task.add_done_callback(TASKS.discard)
    RUNNING_TASKS[job_id]=task
    task.add_done_callback(lambda done: RUNNING_TASKS.pop(job_id,None) if RUNNING_TASKS.get(job_id) is done else None)


async def recover_jobs():
    async with get_db() as db:
        await db.execute("UPDATE chapter_writing_jobs SET status='queued' WHERE status='running'")
        rows=await (await db.execute("SELECT id FROM chapter_writing_jobs WHERE status='queued' ORDER BY created_at,rowid")).fetchall()
        await db.commit()
    for row in rows:queue_job(row[0])


def writing_blocks(artifacts):
    plan=artifacts.get('chief',{}).get('plan',{})
    blocks=[]
    for task in plan.get('tasks',[]):
        role=task['kind'];piece=next((p for p in artifacts.get(role,{}).get('delivery',{}).get('pieces',[]) if p['task_id']==task['id']),None)
        partial=artifacts.get('live_text',{}).get(task['id'],'')
        state='done' if piece else artifacts.get('role_states',{}).get(role,'queued')
        content=assemble({'tasks':[task]},artifacts) if piece else '### '+task['title']+'\n\n'+partial if partial else ''
        blocks.append({**task,'state':state,'content':content,'characters':len(partial)})
    return blocks
