import {test} from 'node:test';
import assert from 'node:assert/strict';
import {representativeChapters} from '../../frontend/assets/chapter-team.mjs';
test('代表章节按架构、计划、自主可控顺序匹配实际编写单元，缺项不编造',()=>{
  const blocks=[{id:1,title:'技术方案'},{id:2,title:'技术实现方案'},{id:3,title:'进度计划分解'},{id:4,title:'3.4.2. ★自主可控要求'},{id:5,title:'自主可控要求'}];
  assert.deepEqual(representativeChapters(blocks,new Set([2,3,4,5])).map(x=>x.id),[2,3,4]);
  assert.deepEqual(representativeChapters(blocks,new Set([3])).map(x=>x.id),[3]);
});
