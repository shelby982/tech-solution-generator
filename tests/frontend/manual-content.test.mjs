import {test} from 'node:test';
import assert from 'node:assert/strict';
import {withManualBlocks,mergeWritingBlocks} from '../../frontend/assets/chapter-live.mjs';

test('手动内容紧跟当前块，后续产物刷新不会丢失或移动手动块',()=>{
  const base=[{id:'a',content:'原段落'},{id:'b',content:'原表'}];
  const custom=[{id:'m1',after:'a',content:'补充文字'},{id:'m2',after:'m1',content:'补充表格'}];
  assert.deepEqual(withManualBlocks(base,custom).map(b=>b.id),['a','m1','m2','b']);
  const refreshed=withManualBlocks([{id:'a',content:'更新段落'},{id:'b',content:'更新表格'}],custom);
  assert.equal(refreshed[1].content,'补充文字');
  assert.deepEqual(mergeWritingBlocks(refreshed,{},['a']).map(b=>b.id),['m1','m2','b']);
});
