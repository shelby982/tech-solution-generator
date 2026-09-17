import test from 'node:test';
import assert from 'node:assert/strict';
import {isOrphanedRun} from '../../frontend/assets/workspace-model.mjs';

test('alive 缺失（老后端或旧缓存）时不报中断',()=>{
  assert.equal(isOrphanedRun({stage:'generating'}),false);
  assert.equal(isOrphanedRun({}),false);
  assert.equal(isOrphanedRun(),false);
});
test('alive 为 true 表示本次进程还认得它，跑到哪一步都不算中断',()=>{
  for(const stage of ['idle','parsing','generating','reviewing','outline_review','report_review','done'])
    assert.equal(isOrphanedRun({stage,alive:true}),false,stage);
});
test('进程不认得 + 图停在中途 = 孤儿',()=>{
  for(const stage of ['parsing','generating','reviewing','matching'])
    assert.equal(isOrphanedRun({stage,alive:false}),true,stage);
});
// 这一组是防误报的核心：后端在闸门处正常停下、或已经跑完，后端重启后 alive 同样是 false，
// 但它们不是中断——checkpoint 停在终态/闸门态，用户可以照常往下走。
test('闸门停留态与终态即使 alive=false 也不是中断',()=>{
  for(const stage of ['idle','outline_review','materials_review','report_review','paused','done','aborted'])
    assert.equal(isOrphanedRun({stage,alive:false}),false,stage);
});
test('未开始或 stage 缺失时不算中断',()=>{
  assert.equal(isOrphanedRun({alive:false}),false);
  assert.equal(isOrphanedRun({stage:undefined,alive:false}),false);
});
