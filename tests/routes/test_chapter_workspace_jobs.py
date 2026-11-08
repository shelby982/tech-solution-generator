"""Workspace job scopes, isolated retries and backward-compatible read paths."""
import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from db import get_db
from routes.chapter_sse import router


@pytest.fixture
async def workspace(monkeypatch):
    from services import chapter_sse
    queued = []
    monkeypatch.setattr(chapter_sse, 'start_runner', queued.append)
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('范围验收')")).lastrowid
        rows = []
        for code, title, level, content in [('s1', '方案', 1, ''), ('s1.1', '已有章', 2, '人工正文'), ('s1.2', '待写章', 2, ''), ('s2', '范围外', 1, '')]:
            bid = (await db.execute('INSERT INTO blocks(project_id,block_id,title,level,content,order_idx,run_thread_id) VALUES(?,?,?,?,?,?,?)',
                (pid, code, title, level, content, len(rows), 'workspace-test'))).lastrowid
            rows.append(bid)
        await db.commit()
    app = FastAPI()
    app.include_router(router, prefix='/api')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, pid, rows, queued


async def test_batch_uses_only_empty_units_in_selected_subtree(workspace):
    client, pid, ids, queued = workspace
    payload = {'thread_id': 'workspace-test', 'root_block_id': 's1'}
    preview = (await client.post(f'/api/projects/{pid}/studio/batch-write', json=payload)).json()
    assert [item['id'] for item in preview['targets']] == [ids[2]]
    assert queued == []
    started = (await client.post(f'/api/projects/{pid}/studio/batch-write', json={**payload, 'preview': False})).json()
    assert len(started['created']) == 1 and len(queued) == 1
    async with get_db() as db:
        rows = await (await db.execute('SELECT content FROM blocks WHERE id IN (?,?) ORDER BY id', (ids[1],ids[3]))).fetchall()
    assert [row['content'] for row in rows] == ['人工正文', '']


async def test_job_rejects_other_thread_and_structure_root(workspace):
    client, pid, ids, _ = workspace
    wrong = await client.post(f'/api/projects/{pid}/studio/chapter-writes', json={'thread_id': 'other-thread', 'block_id': 's1.2'})
    assert wrong.status_code == 404
    root = await client.post(f'/api/projects/{pid}/studio/chapter-writes', json={'thread_id': 'workspace-test', 'block_id': 's1'})
    assert root.status_code == 409


async def test_chart_retry_changes_only_failed_chart(workspace, monkeypatch):
    client, pid, ids, _ = workspace
    live = {'sections': [{'title': '待写章', 'text': '图前。[[FIGURE:架构图]]图后。', 'state': 'done',
                         'charts': [{'title': '架构图', 'role': 'diagram', 'state': 'failed', 'error': '失败'},
                                    {'title': '指标表', 'role': 'table', 'state': 'done', 'markdown': '|指标|\n|---|\n|原值|'}]}]}
    async with get_db() as db:
        await db.execute("INSERT INTO chapter_writing_jobs(id,project_id,thread_id,block_id,status,snapshot,artifacts) VALUES('retry-chart',?,'workspace-test',?,'done',?,?)",
            (pid, ids[2], json.dumps({'mode': 'chapter_sse', 'base_content': ''}), json.dumps({'live': live})))
        await db.commit()
    async def fake_chart(*args):
        return {'ok': True, 'markdown': ':::diagram type=layered 架构图\n应用层\n数据层\n:::'}
    monkeypatch.setattr('services.chapter_sse._generate_chart', fake_chart)
    response = await client.post(f'/api/projects/{pid}/studio/chapter-writes/retry-chart/retry-chart', json={'index':0,'title':'架构图','role':'diagram'})
    assert response.status_code == 200
    data = response.json()
    assert data['artifacts']['live']['sections'][0]['text'] == live['sections'][0]['text']
    charts = data['artifacts']['live']['sections'][0]['charts']
    assert charts[0]['state'] == 'done'
    assert charts[1] == live['sections'][0]['charts'][1]
    assert any(block['kind'] == 'diagram' and '应用层' in block['content'] for block in data['blocks'])


async def test_selection_job_preserves_unselected_body(workspace, monkeypatch):
    client, pid, ids, _ = workspace
    from services import chapter_sse
    baseline = '开头🙂\n\n需要精修的句子。\n\n结尾保持。'
    async with get_db() as db:
        await db.execute('UPDATE blocks SET content=? WHERE id=?', (baseline, ids[2]))
        await db.commit()
    captured = []
    async def fake_stream(block, section, thread_id, model):
        captured.append(section)
        yield '修改后的'
        yield '句子。'
    monkeypatch.setattr(chapter_sse, '_stream_section', fake_stream)
    response = await client.post(f'/api/projects/{pid}/studio/chapter-writes', json={
        'thread_id':'workspace-test','block_id':'s1.2','expected_content':baseline,
        'selection':{'before':'开头🙂\n\n','selection':'需要精修的句子。','after':'\n\n结尾保持。'},'instruction':'精简措辞'})
    assert response.status_code == 200
    job_id = response.json()['job_id']
    await chapter_sse.run_job(job_id)
    async with get_db() as db:
        body = (await (await db.execute('SELECT content FROM blocks WHERE id=?',(ids[2],))).fetchone())[0]
    assert body == '开头🙂\n\n修改后的句子。\n\n结尾保持。'
    assert captured[0]['rewrite_original'] == '需要精修的句子。'


async def test_interrupted_selection_keeps_original_and_durable_proposal(workspace, monkeypatch):
    client, pid, ids, _ = workspace
    from services import chapter_sse
    async with get_db() as db:
        await db.execute('UPDATE blocks SET content=? WHERE id=?', ('原始正文。', ids[2]))
        await db.commit()
    async def fake_stream(*args):
        yield '尚未完成的'
        raise RuntimeError('模拟网络中断')
    monkeypatch.setattr(chapter_sse, '_stream_section', fake_stream)
    response = await client.post(f'/api/projects/{pid}/studio/chapter-writes', json={
        'thread_id':'workspace-test','block_id':'s1.2','expected_content':'原始正文。',
        'selection':{'before':'','selection':'原始正文。','after':''},'instruction':'精简'})
    job_id = response.json()['job_id']
    await chapter_sse.run_job(job_id)
    async with get_db() as db:
        body = (await (await db.execute('SELECT content FROM blocks WHERE id=?',(ids[2],))).fetchone())[0]
    job = (await client.get(f'/api/projects/{pid}/studio/chapter-writes/{job_id}')).json()
    assert body == '原始正文。'
    assert job['status'] == 'failed' and not job['saved']
    assert job['content'] == '尚未完成的'
    assert job['blocks'][0]['state'] == 'failed'


async def test_whole_revision_replaces_body_and_retains_complete_input(workspace, monkeypatch):
    client, pid, ids, _ = workspace
    from services import chapter_sse
    original = '引言。\n\n## 自定义标题\n\n引用【来源：规范书】及 99.9%。\n\n## 另一个标题\n\n已有表格。'
    async with get_db() as db:
        await db.execute('UPDATE blocks SET content=? WHERE id=?', (original,ids[2]))
        await db.commit()
    captured = []
    async def fake_stream(block, section, *args):
        captured.append(section['rewrite_original'])
        yield original.replace('引言。', '改写后的引言。')
    monkeypatch.setattr(chapter_sse, '_stream_section', fake_stream)
    job_id = await chapter_sse.create_job(pid,'workspace-test',ids[2],force_rewrite=True,instruction='重新编排全文')
    await chapter_sse.run_job(job_id)
    async with get_db() as db:
        body = (await (await db.execute('SELECT content FROM blocks WHERE id=?',(ids[2],))).fetchone())[0]
        versions = (await (await db.execute('SELECT content FROM block_revisions WHERE block_id=?',(ids[2],))).fetchall())
    assert captured == [original]
    assert body == original.replace('引言。', '改写后的引言。')
    assert body.count('## 自定义标题') == 1
    assert any(row[0] == original for row in versions)


async def test_confirmed_chapter_ignores_generated_internal_outline(workspace, monkeypatch):
    client, pid, ids, _ = workspace
    from services import chapter_sse
    from services.split_answers import set_answer
    from infra.docx.document_model import parse_markdown
    async with get_db() as db:
        await set_answer(db,pid,'workspace-test','response_plan',{'type':'tech','confirmed':True})
        await db.execute("INSERT INTO writing_plans(id,project_id,thread_id,root_block_id,status,plan) VALUES('temporary-outline',?,'workspace-test','s1','confirmed',?)",
            (pid,json.dumps({'chapters':[{'block_id':'s1.2','outline':[{'title':'AI 临时目录甲'},{'title':'AI 临时目录乙'}]}]})))
        await db.commit()
        before=[tuple(row) for row in await (await db.execute('SELECT id,title,level,order_idx FROM blocks WHERE project_id=? ORDER BY id',(pid,))).fetchall()]
    captured=[]
    async def fake_stream(block,section,*args):
        captured.append(section['title'])
        yield '# AI 新目录\n\n' + '拟依据本章已确认要求编写完整实施说明，交付物及对应验证方法均在本章正文中说明。'*4
    monkeypatch.setattr(chapter_sse,'_stream_section',fake_stream)
    job_id=await chapter_sse.create_job(pid,'workspace-test',ids[2])
    await chapter_sse.run_job(job_id)
    async with get_db() as db:
        after=[tuple(row) for row in await (await db.execute('SELECT id,title,level,order_idx FROM blocks WHERE project_id=? ORDER BY id',(pid,))).fetchall()]
        content=(await (await db.execute('SELECT content FROM blocks WHERE id=?',(ids[2],))).fetchone())[0]
    assert before==after
    assert captured==['AI 临时目录甲', 'AI 临时目录乙']
    assert not any(block['type']=='heading' for block in parse_markdown(content))
