import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MODES, MODE_LABEL, MODE_PANEL, DEFAULT_MODE,
  parseMode, modeUrl, resolveSelectedBlock, panelVisible, isValidMode,
} from '../../frontend/assets/workbench-modes.mjs';

// 背景：2026-10-13 用户拍板把 project-init（要求与大纲 / 素材库）与 workbench（方案编辑）
// 合成"一个页面三模式"。两页的内联脚本有 90 个同名顶层标识符、18 个重复 DOM id，
// 所以合并的关键不是拼 HTML，而是先把**共用的契约**定下来 —— 这个模块就是那层契约。

test('三个模式与顶栏顺序', () => {
  assert.deepEqual(MODES, ['outline', 'matching', 'editor']);
  for (const m of MODES) assert.ok(MODE_LABEL[m], `缺标签：${m}`);
  // 质量检查不在本页（它是评审结果 + diff 的独立页）
  assert.equal(MODES.includes('review'), false);
});

test('parseMode 认三个模式，其余一律回默认', () => {
  assert.equal(parseMode('?view=outline'), 'outline');
  assert.equal(parseMode('?view=matching'), 'matching');
  assert.equal(parseMode('?view=editor'), 'editor');
  // 旧链接兼容：合并前 review 也在顶栏，但那是独立页；本页遇到它回默认而不是崩
  assert.equal(parseMode('?view=review'), DEFAULT_MODE);
  assert.equal(parseMode('?view=bogus'), DEFAULT_MODE);
  assert.equal(parseMode(''), DEFAULT_MODE);
  assert.equal(parseMode(undefined), DEFAULT_MODE);
});

test('modeUrl 保留项目/线程/章节上下文', () => {
  const url = modeUrl('editor', { projectId: 18, threadId: 't-1', blockId: 's2.1' });
  const q = new URLSearchParams(url.split('?')[1]);
  assert.equal(q.get('projectId'), '18');
  assert.equal(q.get('threadId'), 't-1');
  assert.equal(q.get('blockId'), 's2.1');
  assert.equal(q.get('view'), 'editor');
  assert.ok(url.startsWith('/project-init?'));
});

test('modeUrl 对非法模式回落到默认，不产出坏链接', () => {
  const q = new URLSearchParams(modeUrl('nope', { projectId: 1 }).split('?')[1]);
  assert.equal(q.get('view'), DEFAULT_MODE);
});

test('resolveSelectedBlock：保持选中章节，失效或未选时兜底', () => {
  const blocks = [{ block_id: 's1' }, { block_id: 's2.2' }];
  // 切模式必须原地保持（合并的核心收益之一）
  assert.equal(resolveSelectedBlock('matching', { selectedBlockId: 's2.2', blocks }), 's2.2');
  // 章节已被删除 → 回到第一个，而不是空
  assert.equal(resolveSelectedBlock('editor', { selectedBlockId: 'gone', blocks }), 's1');
  // 还没选过 → 第一个
  assert.equal(resolveSelectedBlock('outline', { blocks }), 's1');
  // 没有章节 → 空串（页面据此显示空状态）
  assert.equal(resolveSelectedBlock('outline', { blocks: [] }), '');
  assert.equal(resolveSelectedBlock('outline', {}), '');
  // 兼容直接传字符串数组
  assert.equal(resolveSelectedBlock('outline', { blocks: ['a', 'b'] }), 'a');
});

test('panelVisible：只渲染该模式用得到的中/右栏', () => {
  // 要求与大纲：中栏=大纲，没有右栏
  assert.equal(panelVisible('outline', 'outline'), true);
  assert.equal(panelVisible('outline', 'evidence'), false);
  // 素材库：中栏=匹配，右栏=依据
  assert.equal(panelVisible('matching', 'match'), true);
  assert.equal(panelVisible('matching', 'evidence'), true);
  assert.equal(panelVisible('matching', 'editor'), false);
  // 方案编辑：中栏=编辑器，右栏=上下文
  assert.equal(panelVisible('editor', 'editor'), true);
  assert.equal(panelVisible('editor', 'context'), true);
  assert.equal(panelVisible('editor', 'outline'), false);
});

test('每个模式的面板职责声明完整且不串台', () => {
  for (const m of MODES) {
    const spec = MODE_PANEL[m];
    assert.ok(spec && spec.center, `${m} 缺 center`);
    const used = [spec.center, spec.right].filter(Boolean);
    assert.equal(new Set(used).size, used.length, `${m} 的面板重复`);
  }
  assert.equal(isValidMode('matching'), true);
  assert.equal(isValidMode('review'), false);
});
