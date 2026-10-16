import {test} from 'node:test';
import assert from 'node:assert/strict';
import {canDisplayResult,writingPhase} from '../../frontend/assets/chapter-team.mjs';

test('AI结果直接展示时保护人工修改、切章及正文版本变化',()=>{
  const initial={dirty:false,automatic:true,activeId:1,blockId:1,currentContent:'原正文',base:'原正文'};
  assert.equal(canDisplayResult(initial),true);
  assert.equal(canDisplayResult({...initial,dirty:true}),false);
  assert.equal(canDisplayResult({...initial,activeId:2}),false);
  assert.equal(canDisplayResult({...initial,currentContent:'新正文'}),false);
  assert.equal(canDisplayResult({...initial,automatic:false,currentContent:'已保存修改'}),true);
});

test('技术阶段映射为用户能理解的三步进度',()=>{
  assert.equal(writingPhase({status:'queued',stage:''}),0);
  assert.equal(writingPhase({status:'running',stage:'专业 Agent 协作：正文 Agent'}),1);
  assert.equal(writingPhase({status:'running',stage:'王安石／包拯独立审校'}),2);
  assert.equal(writingPhase({status:'done',stage:''}),3);
});
