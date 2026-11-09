import io
import json
import zipfile
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from db import get_db
from routes import chapter_studio as studio
from routes import blocks as block_routes
from services.block_store import upsert_outline_block, create_material, create_chunk


@pytest.fixture
async def env(monkeypatch, tmp_path):
    from routes import materials, workflow
    monkeypatch.setattr(materials, 'UPLOAD_ROOT', tmp_path / 'uploads')
    monkeypatch.setattr(workflow, '_runner', None)
    monkeypatch.setattr('infra.docx.preview.render_pdf', lambda data:b'%PDF-1.4\npreview')
    queued = []
    monkeypatch.setattr(studio, 'queue_export', queued.append)
    async with get_db() as db:
        project = (await db.execute("INSERT INTO projects(name) VALUES('编制测试')")).lastrowid
        other = (await db.execute("INSERT INTO projects(name) VALUES('其他项目')")).lastrowid
        for tid, pid in [('tech', project), ('commerce', project), ('other', other)]:
            await db.execute('INSERT INTO workflow_runs(thread_id, project_id) VALUES(?,?)', (tid, pid))
        await db.commit()
        for tid, pid in [('tech', project), ('commerce', project), ('other', other)]:
            await upsert_outline_block(db,pid,'s1',level=1,title='技术方案',order_idx=0,run_thread_id=tid)
        await upsert_outline_block(db,project,'s1.1',level=2,title='自主可控',order_idx=1,run_thread_id='tech')
        rows = await (await db.execute('SELECT * FROM blocks ORDER BY id')).fetchall()
        block = next(dict(r) for r in rows if r['block_id']=='s1.1')
        commerce = next(dict(r) for r in rows if r['run_thread_id']=='commerce')
        own = await create_material(db,project,'产品资料.docx','docx','uploads/a.docx','source')
        foreign = await create_material(db,other,'其他.docx','docx','uploads/b.docx','source')
        await create_chunk(db, own['id'],0,'支持国产数据库适配，须依据实际版本验证。',page=12)
        await db.execute("UPDATE materials SET parse_status='done' WHERE id=?",(own['id'],))
        await db.commit()
    app=FastAPI()
    app.include_router(studio.router,prefix='/api')
    app.include_router(block_routes.router,prefix='/api')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        yield SimpleNamespace(client=client,pid=project,block=block,commerce=commerce,own=own,foreign=foreign,queued=queued)


async def test_material_mapping_is_chapter_scoped_and_preserves_quotes(env):
    c=env.client; bid=env.block['id']
    result=await c.put(f'/api/blocks/{bid}/studio/materials',json={'material_id':env.foreign['id']})
    assert result.status_code==404
    assert (await c.put(f'/api/blocks/{bid}/studio/materials',json={'material_id':env.own['id']})).status_code==200
    data=(await c.get(f'/api/blocks/{bid}/studio')).json()
    assert data['materials'][0]['confirmed']==0
    assert data['materials'][0]['excerpts'][0]['page']==12
    assert '实际版本' in data['materials'][0]['excerpts'][0]['text']
    assert (await c.get(f"/api/blocks/{env.commerce['id']}/studio")).json()['materials']==[]
    await c.put(f'/api/blocks/{bid}/studio/materials',json={'material_id':env.own['id'],'confirmed':True})
    assert (await c.get(f'/api/blocks/{bid}/studio')).json()['materials'][0]['confirmed']==1
    await c.delete(f"/api/blocks/{bid}/studio/materials/{env.own['id']}")
    async with get_db() as db:
        assert await (await db.execute('SELECT id FROM materials WHERE id=?',(env.own['id'],))).fetchone()


async def test_complete_requires_real_content_and_content_change_invalidates_completion(env):
    c=env.client; bid=env.block['id']; body={'layout':'solution','writing_status':'complete'}
    assert (await c.put(f'/api/blocks/{bid}/studio',json=body)).status_code==409
    await c.put(f'/api/blocks/{bid}',json={'content':'【待补充：测试报告】'})
    assert (await c.put(f'/api/blocks/{bid}/studio',json=body)).status_code==409
    await c.put(f'/api/blocks/{bid}',json={'content':'拟按规范要求执行适配验证。'})
    assert (await c.put(f'/api/blocks/{bid}/studio',json=body)).status_code==200
    await c.put(f'/api/blocks/{bid}',json={'content':'修改后的实施方案。'})
    assert (await c.get(f'/api/blocks/{bid}/studio')).json()['profile']['writing_status']=='review'
    doc=(await c.get(f'/api/projects/{env.pid}/studio/document',params={'threadId':'tech'})).json()
    assert doc['unit_ids']==[bid]
    assert doc['profiles'][str(bid)]['writing_status']=='review'


async def test_complete_requires_explicit_acknowledgement_and_unchanged_content(env):
    c=env.client;bid=env.block['id']
    # 缺证据标记保留在编辑正文，提交前明确核对。
    response = await c.put(f'/api/blocks/{bid}',json={'content':'实施方案。\n【待补充：测试报告】'})
    assert response.status_code == 200
    # 产品红线：正文里不再出现占位符；但缺项要留痕（提交前需要确认）
    assert '待补充' not in response.json()['content']
    check=(await c.get(f'/api/blocks/{bid}/studio/completion-check')).json()
    assert not check['running'] and len(check['gaps'])==1
    request={'layout':'solution','writing_status':'complete','expected_content_hash':check['content_hash']}
    request['acknowledge_open_items']=True
    assert (await c.put(f'/api/blocks/{bid}/studio',json=request)).status_code==200
    await c.put(f'/api/blocks/{bid}',json={'content':'修改后的方案'})
    assert (await c.put(f'/api/blocks/{bid}/studio',json=request)).status_code==409


async def test_complete_is_blocked_by_active_generation_even_if_acknowledged(env):
    bid=env.block['id']
    await env.client.put(f'/api/blocks/{bid}',json={'content':'已保存正文'})
    async with get_db() as db:
        await db.execute("INSERT INTO chapter_writing_jobs(id,project_id,thread_id,block_id,status,snapshot) VALUES('busy',?,'tech',?,'running','{}')",(env.pid,bid));await db.commit()
    check=(await env.client.get(f'/api/blocks/{bid}/studio/completion-check')).json()
    assert check['running']
    response=await env.client.put(f'/api/blocks/{bid}/studio',json={'layout':'solution','writing_status':'complete','acknowledge_open_items':True})
    assert response.status_code==409


async def test_upload_image_validates_bytes_and_links_only_current_chapter(env):
    c=env.client;bid=env.block['id']
    invalid=await c.post(f'/api/blocks/{bid}/studio/materials',files={'file':('fake.png',b'not image','image/png')})
    assert invalid.status_code==400
    buf=io.BytesIO();Image.new('RGB',(60,30),'blue').save(buf,'PNG')
    result=await c.post(f'/api/blocks/{bid}/studio/materials',files={'file':('架构.png',buf.getvalue(),'image/png')})
    assert result.status_code==200,result.text
    mid=result.json()['id']
    data=(await c.get(f'/api/blocks/{bid}/studio')).json()['materials'][0]
    assert data['image_url']==f'/api/studio/materials/{mid}/image'
    assert data['confirmed']==0
    image=await c.get(data['image_url'])
    assert image.content==buf.getvalue()


async def test_export_snapshot_isolated_from_later_edits_and_other_runs(env):
    c=env.client;bid=env.block['id']
    await c.put(f'/api/blocks/{bid}',json={'content':'导出时正文\n\n| 要求 | 响应 |\n| --- | --- |\n| 适配 | 拟验证 |'})
    result=await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech','template':'formal','scope':'chapter','block_id':bid})
    assert result.status_code==202,result.text
    job=result.json()['id']
    assert env.queued==[job]
    await c.put(f'/api/blocks/{bid}',json={'content':'导出后新正文'})
    await studio.run_export(job)
    assert (await c.get(f'/api/projects/{env.pid}/studio/exports/{job}')).json()['status']=='done'
    response=await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/file')
    with zipfile.ZipFile(io.BytesIO(response.content)) as doc:
        xml=doc.read('word/document.xml').decode()
        assert '导出时正文' in xml and '导出后新正文' not in xml
        assert 'tblHeader' in xml
        assert '仿宋' in doc.read('word/styles.xml').decode()
    bad=await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'commerce','scope':'chapter','block_id':bid})
    assert bad.status_code==404
    bad=await c.put(f'/api/projects/{env.pid}/studio/document',json={'thread_id':'other','template':'formal'})
    assert bad.status_code==404


async def test_export_embeds_frozen_image_and_refuses_cross_project_image(env):
    c=env.client;bid=env.block['id']
    buf=io.BytesIO();Image.new('RGB',(80,40),'red').save(buf,'PNG')
    mid=(await c.post(f'/api/blocks/{bid}/studio/materials',files={'file':('图.png',buf.getvalue(),'image/png')})).json()['id']
    await c.put(f'/api/blocks/{bid}',json={'content':f'![架构图](/api/studio/materials/{mid}/image)'})
    job=(await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech','scope':'chapter','block_id':bid})).json()['id']
    # 删除素材与磁盘文件后，已有任务仍用固定图片快照。
    async with get_db() as db:
        await db.execute('DELETE FROM materials WHERE id=?',(mid,));await db.commit()
    await studio.run_export(job)
    response=await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/file')
    with zipfile.ZipFile(io.BytesIO(response.content)) as doc:
        assert any(name.startswith('word/media/') for name in doc.namelist())


async def test_failed_export_can_retry_same_snapshot_and_not_duplicate_running_job(env):
    result=await env.client.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech'})
    job=result.json()['id']
    retry=f'/api/projects/{env.pid}/studio/exports/{job}/retry'
    assert (await env.client.post(retry)).status_code==409
    async with get_db() as db:
        await db.execute("UPDATE document_export_jobs SET status='failed',error='temporary' WHERE id=?",(job,));await db.commit()
    assert (await env.client.post(retry)).status_code==202
    assert env.queued==[job,job]


async def test_document_upload_reuses_parser_and_persists_chapter_mapping(env):
    from routes.materials import _PARSE_TASKS
    fixture=Path(__file__).parents[1]/'fixtures/sample_materials.docx'
    response=await env.client.post(f"/api/blocks/{env.block['id']}/studio/materials",
                                  files={'file':('产品资料.docx',fixture.read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
    assert response.status_code==200,response.text
    await asyncio.gather(*list(_PARSE_TASKS))
    data=(await env.client.get(f"/api/blocks/{env.block['id']}/studio")).json()
    assert data['materials'][0]['parse_status']=='done'
    assert data['materials'][0]['excerpts']
    assert not data['materials'][0]['confirmed']


async def test_full_export_contains_toc_and_keeps_first_content_heading(env):
    c=env.client; bid=env.block['id']
    await c.put(f'/api/blocks/{bid}',json={'content':'### 适配策略\n\n拟按验证计划实施。'})
    job=(await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech'})).json()['id']
    await studio.run_export(job)
    file=await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/file')
    with zipfile.ZipFile(io.BytesIO(file.content)) as doc:
        xml=doc.read('word/document.xml').decode()
        assert 'TOC' in xml and '适配策略' in xml
        assert 'PAGE' in doc.read('word/footer1.xml').decode()


async def test_changing_material_requires_recheck_and_deleting_block_cleans_links(env):
    c=env.client;bid=env.block['id']
    await c.put(f'/api/blocks/{bid}',json={'content':'已核对的正文'})
    await c.put(f'/api/blocks/{bid}/studio',json={'layout':'solution','writing_status':'complete'})
    await c.put(f'/api/blocks/{bid}/studio/materials',json={'material_id':env.own['id'],'confirmed':True})
    assert (await c.get(f'/api/blocks/{bid}/studio')).json()['profile']['writing_status']=='review'
    async with get_db() as db:
        await db.execute('DELETE FROM blocks WHERE id=?',(bid,));await db.commit()
        assert not await (await db.execute('SELECT * FROM chapter_materials WHERE block_id=?',(bid,))).fetchone()
        assert not await (await db.execute('SELECT * FROM chapter_studio WHERE block_id=?',(bid,))).fetchone()


async def test_preview_failure_does_not_break_word_and_can_retry(env,monkeypatch):
    def fail(data): raise RuntimeError('LibreOffice 不可用')
    monkeypatch.setattr('infra.docx.preview.render_pdf',fail)
    c=env.client
    job=(await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech'})).json()['id']
    await studio.run_export(job)
    status=(await c.get(f'/api/projects/{env.pid}/studio/exports/{job}')).json()
    assert status['status']=='done' and status['preview_status']=='failed'
    assert (await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/file')).status_code==200
    assert (await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/preview')).status_code==409
    monkeypatch.setattr('infra.docx.preview.render_pdf',lambda data:b'%PDF-1.4\nverified')
    assert (await c.post(f'/api/projects/{env.pid}/studio/exports/{job}/preview')).status_code==202
    await asyncio.gather(*list(studio.TASKS))
    assert (await c.get(f'/api/projects/{env.pid}/studio/exports/{job}/preview')).content.startswith(b'%PDF')


async def test_job_freezes_model_and_template_and_preview_does_not_save_editor(env):
    c=env.client;bid=env.block['id']
    result=await c.post(f'/api/blocks/{bid}/studio/content-preview',json={'content':'### 子节\n\n**草稿**'})
    assert result.status_code==200 and '<strong>草稿</strong>' in result.json()['html']
    async with get_db() as db: assert not (await studio.require_block(db,bid)).get('content')
    await c.put(f'/api/blocks/{bid}',json={'content':'### 子节\n\n**草稿**'})
    job=(await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech'})).json()['id']
    async with get_db() as db:
        snapshot=json.loads((await (await db.execute('SELECT snapshot FROM document_export_jobs WHERE id=?',(job,))).fetchone())[0])
        assert snapshot['template_data'] and len(snapshot['template_version'])==64
        assert snapshot['document_model_version']==1
        assert snapshot['sections'][-1]['document_blocks'][0]['type']=='heading'


async def test_invalid_markdown_and_external_images_fail_before_export_queue(env):
    c=env.client;bid=env.block['id']
    # ① 内部语法残留：保存时**系统自动清理**（不再等到导出才报错）
    await c.put(f'/api/blocks/{bid}',json={'content':':::diagram 缺少结尾\nA -> B'})
    saved = (await c.get(f'/api/blocks/{bid}')).json()
    assert ':::diagram' not in (saved.get('content') or '')

    # ② 外部图片：仍然必须在导出前被拦住
    await c.put(f'/api/blocks/{bid}',json={'content':'![远程](https://example.com/a.png)'})
    assert (await c.post(f'/api/projects/{env.pid}/studio/exports',json={'thread_id':'tech'})).status_code==422
    assert env.queued==[]


async def test_save_conflict_preserves_current_body_and_revisions(env):
    bid = env.block['id']
    c = env.client
    first = await c.put(f'/api/blocks/{bid}', json={'content': '第一份正文', 'expected_content': ''})
    assert first.status_code == 200
    conflict = await c.put(f'/api/blocks/{bid}', json={'content': '迟到的正文', 'expected_content': ''})
    assert conflict.status_code == 409
    async with get_db() as db:
        block = await (await db.execute('SELECT content FROM blocks WHERE id=?', (bid,))).fetchone()
        revisions = await (await db.execute('SELECT content FROM block_revisions WHERE block_id=?', (bid,))).fetchall()
    assert block['content'] == '第一份正文'
    assert all(row['content'] != '迟到的正文' for row in revisions)


async def test_document_exposes_structure_scope_without_changing_directory(env):
    data = (await env.client.get(f'/api/projects/{env.pid}/studio/document', params={'threadId': 'tech'})).json()
    scopes = data['node_scopes']
    parent_id = next(key for key, value in scopes.items() if value['role'] == 'structure')
    assert scopes[parent_id]['pending_ids'] == [env.block['id']]
    assert scopes[str(env.block['id'])]['role'] == 'content'


async def test_targeted_verification_requires_explicit_quote_and_fresh_inputs(env, monkeypatch):
    from services.review_issues import sync_issues, fingerprint
    bid = env.block['id']
    await env.client.put(f'/api/blocks/{bid}', json={'content': '拟按规范完成兼容验证，形成测试报告。'})
    issue = {'point': '未说明兼容验证交付', 'severity': 'high'}
    async with get_db() as db:
        await sync_issues(db, bid, [issue])
    async def fake_review(*args):
        return json.dumps({'resolved': True, 'reason': '已明确交付测试报告', 'quote': '形成测试报告。'}), 'test'
    monkeypatch.setattr('agents.chapter_team.call_role', fake_review)
    response = await env.client.post(f'/api/blocks/{bid}/studio/review/verify-issue', json={'fingerprint': fingerprint(issue)})
    assert response.status_code == 200
    assert response.json()['resolved'] is True
    await env.client.put(f'/api/blocks/{bid}', json={'content': '正文已经改变。'})
    states = (await env.client.get(f'/api/blocks/{bid}/studio/review/issues')).json()
    assert states['items'][0]['status'] == 'fixed'


async def test_project_facts_are_reused_and_invalidate_review_version(env):
    from services.writing_workspace import versions, review_freshness
    response = await env.client.put(f'/api/projects/{env.pid}/studio/facts', json={'facts': {'系统名称': '客户档案系统'}})
    assert response.status_code == 200
    async with get_db() as db:
        block = dict(await (await db.execute('SELECT * FROM blocks WHERE id=?', (env.block['id'],))).fetchone())
        baseline = await versions(db, block)
    await env.client.put(f'/api/projects/{env.pid}/studio/facts', json={'facts': {'系统名称': '档案认证系统'}})
    async with get_db() as db:
        assert await review_freshness(db, block, baseline) == 'stale'


async def test_confirmed_directory_limits_writing_to_body(env):
    from services.split_answers import set_answer
    bid = env.block['id']
    async with get_db() as db:
        await set_answer(db, env.pid, 'tech', 'response_plan', {'type':'tech','confirmed':True})
        before = [tuple(row) for row in await (await db.execute('SELECT id,title,level,order_idx FROM blocks WHERE project_id=? ORDER BY id',(env.pid,))).fetchall()]
    saved = await env.client.put(f'/api/blocks/{bid}',json={'content':'# 自主可控\n\n## AI 新增章节\n完整正文。'})
    assert saved.status_code == 200
    assert saved.json()['content'] == '\n**AI 新增章节**\n完整正文。'
    info = (await env.client.get(f'/api/blocks/{bid}/studio')).json()
    assert info['directory_locked'] is True
    async with get_db() as db:
        after = [tuple(row) for row in await (await db.execute('SELECT id,title,level,order_idx FROM blocks WHERE project_id=? ORDER BY id',(env.pid,))).fetchall()]
    assert before == after
