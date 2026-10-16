import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mergeWritingBlocks} from '../../frontend/assets/chapter-live.mjs';
import {collaborationHtml} from '../../frontend/assets/chapter-team.mjs';

test('原位更新遵循主编顺序，人工修改和删除不会被后续流式覆盖',()=>{
  const blocks=[{id:'a',kind:'text',content:'新输出'},{id:'b',kind:'diagram',content:'图'},{id:'c',kind:'table',content:'表'}];
  assert.deepEqual(mergeWritingBlocks(blocks,{a:'人工正文'},['b']).map(b=>[b.id,b.content]),[['a','人工正文'],['c','表']]);
  assert.equal(mergeWritingBlocks(blocks,{a:''})[0].content,'');
});

test('协作信息展示真实分工和交付数，并转义模型内容',()=>{
  const html=collaborationHtml({status:'running',stage:'专业协作',blocks:[{kind:'text',state:'running',title:'<script>',characters:12},{kind:'table',state:'done',title:'对照表'}],artifacts:{chief:{plan:{objective:'<img>',gaps:[]}},role_states:{text:'running',table:'done'}}});
  assert.ok(html.includes('正文 Agent')&&html.includes('表格 Agent')&&html.includes('合规审校 Agent'));
  assert.ok(html.includes('已收到 12 字符')&&html.includes('表格 1/1'));
  assert.ok(!html.includes('<script>')&&!html.includes('<img>'));
});
