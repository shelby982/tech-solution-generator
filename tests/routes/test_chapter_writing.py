import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from db import get_db
from services.block_store import upsert_outline_block
from services import chapter_writing as service
from agents import chapter_team as team
from routes import chapter_writing as routes


@pytest.fixture
async def env(monkeypatch):
    from services.config_store import config_store
    from routes import workflow
    monkeypatch.setattr(workflow,'_runner',None)
    monkeypatch.setattr(config_store,'get_configs_and_next_index',lambda:([SimpleNamespace(model='test-model',provider='openai')],0))
    queued=[];monkeypatch.setattr(routes,'queue_job',queued.append)
    async with get_db() as db:
        pid=(await db.execute("INSERT INTO projects(name) VALUES('协作测试')")).lastrowid
        await db.execute('INSERT INTO workflow_runs(thread_id,project_id) VALUES(?,?)',('tech',pid))
        await db.execute('INSERT INTO workflow_runs(thread_id,project_id) VALUES(?,?)',('other',pid));await db.commit()
        await upsert_outline_block(db,pid,'s1',level=1,title='自主可控方案',order_idx=0,run_thread_id='tech')
        bid=(await (await db.execute("SELECT id FROM blocks WHERE run_thread_id='tech'")).fetchone())[0]
        await upsert_outline_block(db,pid,'s1',level=1,title='其他应答',order_idx=0,run_thread_id='other')
        other=(await (await db.execute("SELECT id FROM blocks WHERE run_thread_id='other'")).fetchone())[0]
    calls=[]
    async def call(role,system,payload,settings):
        calls.append(role)
        if role=='chief':data={'objective':'对照要求编制方案','tasks':[{'id':r,'kind':r,'title':r+'方案','purpose':'根据原文拟实施'} for r in ('text','table','diagram')],'gaps':[]}
        elif role=='text':data={'pieces':[{'task_id':'text','markdown':'拟采用分层适配方案。'}]}
        elif role=='table':data={'pieces':[{'task_id':'table','columns':['工作','方法'],'rows':[['适配','拟进行验证']]}]}
        elif role=='diagram':data={'pieces':[{'task_id':'diagram','nodes':['需求','适配','验证']}]}
        else:
            assert '当前编写单元' in system and '不得作为当前子章节缺失项' in system
            data={'score':90,'issues':[],'strengths':['结构完整'],'satisfied':'partial','covered_points':[],'missing_points':[]}
        return json.dumps(data,ensure_ascii=False),'test-model'
    monkeypatch.setattr(team,'call_role',call)
    app=FastAPI();app.include_router(routes.router,prefix='/api')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        yield SimpleNamespace(client=client,pid=pid,bid=bid,other=other,calls=calls,queued=queued,call=call)


async def start(env):
    response=await env.client.post(f'/api/projects/{env.pid}/studio/writing-jobs',json={'thread_id':'tech','block_ids':[env.bid],'models':{'default':'test-model'}})
    assert response.status_code==202,response.text
    return response.json()['jobs'][0]


async def detail(env,jid):
    return (await env.client.get(f'/api/projects/{env.pid}/studio/writing-jobs/{jid}')).json()


async def test_team_produces_text_table_diagram_reviews_and_autosaves_empty(env):
    jid=await start(env);await service.run_job(jid)
    result=await detail(env,jid)
    assert result['status']=='done' and result['saved']==1,result
    assert ':::diagram' in result['content'] and '| 工作 | 方法 |' in result['content']
    assert set(env.calls)=={'chief','text','table','diagram','wang_anshi','bao_zheng'}
    assert result['artifacts']['reviews']['bao_zheng']['finding']['score']==90
    async with get_db() as db:
        assert (await (await db.execute('SELECT content FROM blocks WHERE id=?',(env.bid,))).fetchone())[0]==result['content']
        assert (await (await db.execute('SELECT writing_status FROM chapter_studio WHERE block_id=?',(env.bid,))).fetchone())[0]=='review'
        assert (await (await db.execute('SELECT count(*) FROM block_revisions WHERE block_id=?',(env.bid,))).fetchone())[0]==1
    await service.run_job(jid)
    assert len(env.calls)==6


async def test_existing_content_is_never_overwritten(env):
    async with get_db() as db:await db.execute('UPDATE blocks SET content=? WHERE id=?',('人工正文',env.bid));await db.commit()
    jid=await start(env);await service.run_job(jid)
    result=await detail(env,jid);assert result['saved']==0 and result['content']
    async with get_db() as db:assert (await (await db.execute('SELECT content FROM blocks WHERE id=?',(env.bid,))).fetchone())[0]=='人工正文'


async def test_edit_then_clear_while_agents_work_still_protects_manual_revision(env,monkeypatch):
    jid=await start(env)
    async def call(role,*args):
        if role=='chief':
            async with get_db() as db:
                await db.execute("INSERT INTO block_revisions(block_id,revision_no,content,summary,source) VALUES(?,1,'','手动清空','edit')",(env.bid,));await db.commit()
        return await env.call(role,*args)
    monkeypatch.setattr(team,'call_role',call)
    await service.run_job(jid)
    assert (await detail(env,jid))['saved']==0


async def test_failed_specialist_reuses_successful_artifacts_on_retry(env,monkeypatch):
    jid=await start(env)
    async def fail(role,*args):
        if role=='table':raise ValueError('临时模型错误')
        return await env.call(role,*args)
    monkeypatch.setattr(team,'call_role',fail)
    await service.run_job(jid)
    result=await detail(env,jid)
    assert result['status']=='failed' and 'text' in result['artifacts'] and 'diagram' in result['artifacts']
    monkeypatch.setattr(team,'call_role',env.call)
    assert (await env.client.post(f'/api/projects/{env.pid}/studio/writing-jobs/{jid}/retry')).status_code==202
    await service.run_job(jid)
    assert (await detail(env,jid))['status']=='done'
    assert env.calls.count('chief')==1 and env.calls.count('text')==1 and env.calls.count('diagram')==1


async def test_duplicate_submit_returns_same_job_and_rejects_foreign_run(env):
    first=await start(env);second=await start(env);assert first==second
    response=await env.client.post(f'/api/projects/{env.pid}/studio/writing-jobs',json={'thread_id':'tech','block_ids':[env.bid,env.other]})
    assert response.status_code==404


async def test_review_error_does_not_save_incomplete_output(env,monkeypatch):
    jid=await start(env)
    async def fail(role,*args):
        if role=='bao_zheng':return '不是合法审校 JSON','test-model'
        return await env.call(role,*args)
    monkeypatch.setattr(team,'call_role',fail)
    await service.run_job(jid)
    result=await detail(env,jid)
    assert result['status']=='failed' and result['saved']==0


async def test_chief_cannot_omit_spec_requirements(env):
    with pytest.raises(ValueError,match='遗漏'):
        await team.ChapterChiefAgent().plan({'spec_requirements':[{'requirement_id':'req-1'}]}, {})


async def test_chief_receives_explicit_empty_id_list_and_rejects_invented_ids(env,monkeypatch):
    async def invented(role,system,payload,settings):
        assert payload['allowed_requirement_ids']==[]
        assert '清单为空' in system
        return json.dumps({'objective':'本章分析','tasks':[{'id':'t1','kind':'text','title':'背景',
            'purpose':'回应评分点','requirement_ids':['1.1']}]}),'test-model'
    monkeypatch.setattr(team,'call_role',invented)
    with pytest.raises(ValueError,match='不存在'):
        await team.ChapterChiefAgent().plan({'spec_requirements':[]}, {})


async def test_live_progress_persists_before_other_agents_finish(env,monkeypatch):
    import asyncio
    text_ready=asyncio.Event();release_table=asyncio.Event()
    async def call(role,*args):
        if role=='text':
            await team.STREAM_SINK.get()({'text':'正在流式写作','unknown':'不能落地'})
            text_ready.set()
        if role=='table':await release_table.wait()
        return await env.call(role,*args)
    monkeypatch.setattr(team,'call_role',call)
    jid=await start(env);task=asyncio.create_task(service.run_job(jid))
    try:
        await asyncio.wait_for(text_ready.wait(),2)
        result=await detail(env,jid)
        assert result['status']=='running'
        assert result['artifacts']['live_text']=={'text':'正在流式写作'}
        assert [b['kind'] for b in result['blocks']]==['text','table','diagram']
        assert result['preparation']['materials']==0
    finally:
        release_table.set();await task
    assert (await detail(env,jid))['status']=='done'


async def test_cancelled_queue_does_not_start_model_calls(env):
    jid=await start(env)
    response=await env.client.post(f'/api/projects/{env.pid}/studio/writing-jobs/{jid}/cancel')
    assert response.status_code==200
    await service.run_job(jid)
    assert not env.calls
    assert (await detail(env,jid))['status']=='cancelled'
