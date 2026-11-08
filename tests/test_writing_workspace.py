"""Regression checks for role, version and progress boundaries."""
import json

from db import get_db
from services.chapter_progress import progress_blocks
from services.writing_workspace import node_scopes, versions, review_freshness


def test_node_roles_keep_parent_content_and_subtree_boundaries():
    blocks = [dict(id=1, level=1, content=''), dict(id=2, level=2, content='已有总述'),
              dict(id=3, level=3, content=''), dict(id=4, level=1, content='')]
    scopes = node_scopes(blocks)
    assert scopes[1] == {'role': 'structure', 'unit_ids': [2, 3], 'pending_ids': [3]}
    assert scopes[2]['role'] == 'content'
    assert 4 not in scopes[1]['unit_ids']


def test_progress_keeps_original_order_and_chart_positions():
    base = '开篇。\n\n## 保留节\n\n已有内容。\n\n## 待写节\n\n'
    state = {'sections': [{'title': '待写节', 'text': '图前。[[FIGURE:架构]]图后。', 'state': 'done',
                           'charts': [{'title': '架构', 'role': 'diagram', 'state': 'failed', 'error': '重试'}]}]}
    blocks = progress_blocks(state, base)
    assert blocks[0]['content'].startswith('开篇。')
    assert '保留节' in blocks[0]['content']
    chart_index = next(index for index, block in enumerate(blocks) if block['kind'] == 'diagram')
    assert blocks[chart_index - 1]['content'] == '图前。'
    assert blocks[chart_index + 1]['content'] == '图后。'
    assert blocks[chart_index]['state'] == 'failed'
    state['sections'][0]['charts'][0].update(state='done', markdown='真实图示')
    updated = progress_blocks(state, base)
    assert updated[chart_index]['id'] == blocks[chart_index]['id']
    assert updated[chart_index]['content'] == '真实图示'


async def test_review_versions_track_body_adopted_evidence_and_project_facts():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('版本验收')")).lastrowid
        bid = (await db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) VALUES(?, 's1', '技术', '正文', 'versions')", (pid,))).lastrowid
        block = dict(await (await db.execute('SELECT * FROM blocks WHERE id=?', (bid,))).fetchone())
        baseline = await versions(db, block)
        assert await review_freshness(db, block, {}) == 'unknown'
        assert await review_freshness(db, block, baseline) == 'current'
        changed = {**block, 'content': '改过的正文'}
        assert await review_freshness(db, changed, baseline) == 'stale'
        mid = (await db.execute("INSERT INTO materials(project_id,filename,type,role) VALUES(?, '证明.docx', 'docx', 'source')", (pid,))).lastrowid
        cid = (await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,status) VALUES(?,0,'证书','selected')", (mid,))).lastrowid
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,chunk_kind,mode,requirement_id) VALUES(?,?,'本节','file','data','r1')", (bid,cid))
        adopted = await versions(db, block)
        assert adopted != baseline
        await db.execute("UPDATE material_chunks SET status='rejected' WHERE id=?", (cid,))
        assert await review_freshness(db, block, adopted) == 'stale'
        current = await versions(db, block)
        await db.execute('INSERT INTO project_writing_facts(project_id,facts) VALUES(?,?)', (pid,json.dumps({'项目经理':'待定'})))
        assert await review_freshness(db, block, current) == 'stale'
        await db.commit()
