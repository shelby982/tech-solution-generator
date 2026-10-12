import test from 'node:test';
import assert from 'node:assert/strict';
import {isOrphanedRun, activeStages, stageLabels, instructionDirty,
        CONSTRAINT_LEVELS, constraintLevelLabel,
        isExtractDone, extractBadgeState} from '../../frontend/assets/workspace-model.mjs';

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

// failed 是「图执行中途抛异常」的终态（如目录派生失败不再降级、直接抛）。
// 两条契约：① 它不能算「还在跑」——否则 extracting 期间的兜底轮询会一直等下去；
// ② 必须有中文标签，否则页面会显示原始的 stage 代码。
test('failed 是终态：不算「还在跑」，且有中文标签',()=>{
  assert.equal(activeStages.has('failed'),false);
  assert.equal(stageLabels.failed,'本次运行失败');
  assert.equal(isOrphanedRun({stage:'failed',alive:false}),false);
  assert.equal(isOrphanedRun({stage:'failed',alive:true}),false);
});

// 提炼要求「改过但没生效」的判定：决定闸门 1 的提示与确认前追问是否触发。
test('instructionDirty 按 trim 比较，两边都空视为未改',()=>{
  assert.equal(instructionDirty('按标包2拆', '按标包2拆'), false);
  assert.equal(instructionDirty('  按标包2拆  ', '按标包2拆'), false);
  assert.equal(instructionDirty('', ''), false);
  assert.equal(instructionDirty(null, undefined), false);
  assert.equal(instructionDirty('按标包2拆', ''), true);       // 新填了要求但目录不是按它生成的
  assert.equal(instructionDirty('', '按标包2拆'), true);       // 清空了要求但还没重拆
  assert.equal(instructionDirty('按标包3拆', '按标包2拆'), true);
});

// extracting 是闸门 1 放行后、逐节提炼期间的运行期 stage（写在 workflow_runs，
// 不是 checkpoint —— extract 节点收尾时图直接 END，checkpoint 永远停在 outline_review）。
// 两条契约：① 必须有中文标签；② **不能**进 activeStages —— 那个集合回答的是
// 「checkpoint 里停着 = 图真的停在中途」，把它塞进去会让「停在闸门 1」被误判成中断。
test('extracting 有中文标签，但不进 activeStages',()=>{
  assert.equal(stageLabels.extracting,'提炼要点中');
  assert.equal(activeStages.has('extracting'),false);
  assert.equal(isOrphanedRun({stage:'extracting',alive:false}),false);
});

// constraint_level 三档枚举：project-init 的提炼结果确认页与 workbench 的参考卡片共用，
// 缺一档 / 认不出值都会让第 8 个字段显示错（它决定正文字数档，进而决定生成费用）。
test('constraint_level 三档枚举与中文标签',()=>{
  assert.deepEqual(CONSTRAINT_LEVELS.map(o=>o.value),['mandatory','recommended','optional']);
  assert.equal(constraintLevelLabel('mandatory'),'必须响应');
  assert.equal(constraintLevelLabel('recommended'),'应当响应');
  assert.equal(constraintLevelLabel('optional'),'可选响应');
  assert.equal(constraintLevelLabel(''),'应当响应');        // 缺省按 recommended
  assert.equal(constraintLevelLabel('urgent'),'应当响应');  // 未知值不显示原始英文
});

// 逐节提炼的结果判定：「提炼失败」不能算完成。
// 以前失败与"无原文"落库后都是 status='outline_done' 且 8 字段全空，页面把失败当已提炼
// → 显示「提炼完成」而字段是空的，用户既看不出问题、也没法重试。
test('失败不算完成，无原文算完成（且两者徽标不同）',()=>{
  const ok = {status:'outline_done', requirement:'r'};
  const failed = {status:'outline_done', error_kind:'failed', error:'全部 API 失败：402'};
  const noSource = {status:'outline_done', error_kind:'no_source', error:'没有可提炼的原文'};
  const pending = {status:'empty'};

  assert.equal(isExtractDone(ok), true);
  assert.equal(isExtractDone(failed), false, '失败必须留在"还剩 N 节"里，才能被继续提炼重试');
  assert.equal(isExtractDone(noSource), true, '无原文没有可提炼内容，重试是空跑，算完成');
  assert.equal(isExtractDone(pending), false);

  assert.equal(extractBadgeState(ok), 'done');
  assert.equal(extractBadgeState(failed), 'error');
  assert.equal(extractBadgeState(noSource), 'no_source');
  assert.equal(extractBadgeState(pending), 'pending');
});
