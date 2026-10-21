import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mergeWritingBlocks} from '../../frontend/assets/chapter-live.mjs';

test('原位更新遵循主编顺序，人工修改和删除不会被后续流式覆盖',()=>{
  const blocks=[{id:'a',kind:'text',content:'新输出'},{id:'b',kind:'diagram',content:'图'},{id:'c',kind:'table',content:'表'}];
  assert.deepEqual(mergeWritingBlocks(blocks,{a:'人工正文'},['b']).map(b=>[b.id,b.content]),[['a','人工正文'],['c','表']]);
  assert.equal(mergeWritingBlocks(blocks,{a:''})[0].content,'');
});
