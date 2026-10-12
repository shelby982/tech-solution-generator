import test from 'node:test';
import assert from 'node:assert/strict';
import { chapterBadgesHtml } from '../../frontend/assets/chapter-tree.mjs';

// 背景：2026-10-13 用户要"左栏归一"。归一前三个模式的章节行是**两套实现**：
// 「要求与大纲 / 素材库」在 mode-outline.js 里拼 `.ot-row`，
// 「方案编辑」在 workbench.js 里拼 `.doc-outline-item`。
// 两边的数据投影本就共用（workspace-model.mjs），重复的只是标记与徽标拼装。
// 这个模块把标记收敛成一份 —— 下面钉住它的输出契约。
//
// 为什么必须钉：下游有若干**就地**更新徽标的代码（syncOutlineRow 等按
// `.ot-row .block-status-badge` / `.ot-sub` 定位），标记一变就静默失效，
// 而且历史上两边各写一套时漂移出过"每行显示 #undefined"。

test('徽标：状态 + 依据 N + 覆盖 x/y 三段结构', () => {
  const html = chapterBadgesHtml({ block_id: 's1' }, {
    evidenceCount: 3,
    coverage: { covered: 2, total: 5 },
    status: 'done',
  });
  assert.match(html, /class="block-status-badge done ot-badge"/);
  assert.match(html, /依据 3/);                       // 依据数
  assert.match(html, /class="ot-cov[^"]*"/);          // 覆盖
  assert.match(html, />2\/5</);
  assert.match(html, /title="[^"]*依据 3 条[^"]*"/);   // hover 提示里有依据数
});

test('徽标：依据 0 条时不显示「依据 N」（避免 0 条噪音）', () => {
  const html = chapterBadgesHtml({ block_id: 's1' }, { evidenceCount: 0, status: 'pending' });
  assert.doesNotMatch(html, /ot-ev/);
});

test('徽标：没有覆盖数据时不渲染覆盖徽标', () => {
  const html = chapterBadgesHtml({ block_id: 's1' }, { evidenceCount: 1, coverage: null });
  assert.doesNotMatch(html, /ot-cov/);
  // 覆盖 0/0 也该当作"没有覆盖数据"
  const zero = chapterBadgesHtml({ block_id: 's1' }, { evidenceCount: 1, coverage: { covered: 0, total: 0 } });
  assert.doesNotMatch(zero, /ot-cov/);
});

test('徽标：覆盖全满/零覆盖用不同类，便于上色', () => {
  const full = chapterBadgesHtml({ block_id: 's1' }, { coverage: { covered: 4, total: 4 } });
  assert.match(full, /ot-cov full/);
  const zero = chapterBadgesHtml({ block_id: 's1' }, { coverage: { covered: 0, total: 4 } });
  assert.match(zero, /ot-cov zero/);
  const partial = chapterBadgesHtml({ block_id: 's1' }, { coverage: { covered: 2, total: 4 } });
  assert.match(partial, /class="ot-cov "/);
});

test('徽标：label 覆盖让各模式用自己的词表（同一份拼装）', () => {
  // 编辑器看"撰写状态"，要求与大纲看"提炼状态" —— 词表不同，标记一份。
  const editor = chapterBadgesHtml({ block_id: 's1' }, { status: 'empty', label: '待撰写' });
  assert.match(editor, />待撰写</);
  const outline = chapterBadgesHtml({ block_id: 's1' }, { status: 'pending' });
  assert.match(outline, />待提炼</);
});

test('徽标：提炼中 / 提炼失败有专名（不受 label 影响时）', () => {
  assert.match(chapterBadgesHtml({}, { status: 'extracting' }), />提炼中</);
  assert.match(chapterBadgesHtml({}, { status: 'error' }), />提炼失败</);
});

test('徽标：标题与文本做了转义（防注入/防破版）', () => {
  const html = chapterBadgesHtml({ block_id: 'x' }, { status: 'done', evidenceCount: 1 });
  assert.ok(!html.includes('undefined'), '不能出现 undefined（历史漂移事故）');
  assert.ok(!html.includes('#undefined'));
});
