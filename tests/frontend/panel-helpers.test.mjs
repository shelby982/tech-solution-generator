/**
 * 面板辅助函数的定义完整性守卫。
 *
 * 背景（真实事故）：重写 `openSplitReport()` 时整段替换，**连带删除了定义在同一范围内的
 * `bindScopeSelector()`**，而调用点还在。报错又被 `refreshOutlineCoverage` 的
 * try/catch 吞掉 —— 界面上只表现为"投标范围一直加载中"，查了很久才定位。
 *
 * 这里静态检查：模块内**调用了的 UI 辅助函数**必须能在模块内找到定义（或来自 import）。
 * 不做完整 JS 解析（那是 TS/ESLint 的活），只覆盖 bind / render / toggle / maybe / open /
 * show / refresh / mark 这几类以大写字母续名的辅助函数——正是本次事故的那一类。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'frontend', 'assets', 'mode-outline.js'), 'utf8');

function definedNames() {
  const names = new Set();
  for (const m of src.matchAll(/^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)) {
    names.add(m[1]);
  }
  // IIFE 命名函数：`(function bindRoleHint() { … })()` —— 也是合法定义，别误报
  for (const m of src.matchAll(/\((?:async\s+)?function\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(m[1]);
  }
  for (const m of src.matchAll(/^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=/gm)) {
    names.add(m[1]);
  }
  // import { a, b as c } from '…'
  for (const m of src.matchAll(/import\s*\{([^}]+)\}\s*from/g)) {
    m[1].split(',').forEach(part => {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name) names.add(name);
    });
  }
  return names;
}

const UI_HELPER_PREFIX = /^(bind|render|toggle|maybe|open|show|refresh|mark)[A-Z]/;

test('模块内调用的 UI 辅助函数都有定义（防止整段替换误删函数）', () => {
  const defined = definedNames();
  const called = new Set();
  for (const m of src.matchAll(/\b([A-Za-z_$][\w$]*)\s*\(/g)) {
    const name = m[1];
    if (UI_HELPER_PREFIX.test(name)) called.add(name);
  }
  const missing = [...called].filter(n => !defined.has(n)).sort();
  assert.deepEqual(missing, [],
    `这些函数被调用但没有定义（很可能是某次整段替换把它们删掉了）：${missing.join(', ')}`);
});

test('关键面板函数都存在（本会话新增的能力，逐个钉住）', () => {
  const defined = definedNames();
  const critical = [
    'openSplitReport',        // 拆解面板
    'maybeAutoOpenSplitReport', // 拆解完自动展示
    'markRevisionConfirmed',
    'getConfirmedRevision',
    'resolvePanelThreadId',
    'refreshOutlineCoverage',   // 只负责读状态（覆盖率渲染已按用户要求删除）
    'toggleScoringRisk',
    'loadExistingBlocks',
  ];
  const missing = critical.filter(n => !defined.has(n));
  assert.deepEqual(missing, [], `缺少关键函数：${missing.join(', ')}`);
});


test('已按用户要求删除的能力不得复活（覆盖率模块 / 确认横幅 / 展开收起操作）', () => {
  const src = readFileSync(new URL('../../frontend/assets/mode-outline.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('../../frontend/three-mode.html', import.meta.url), 'utf8');
  // 用户原话：「页面上"目录覆盖率"的模块直接删掉吧。目前还没有目录，留一个"目录覆盖率"是什么意思呢？」
  // 用户原话：「不保留"展开拆分清单"之类的操作」→ 面板默认展示全部内容。
  const banned = [
    ['目录覆盖率指标', src.includes('ov-panel-title')],
    ['覆盖率渲染函数 renderOutlineCoverage', src.includes('renderOutlineCoverage(')],
    ['覆盖率容器 #outline-coverage-panel', html.includes('outline-coverage-panel')],
    ['展开/收起拆分清单 toggleSplitPanel', src.includes('toggleSplitPanel')],
    ['收起面板 collapseSplitPanel', src.includes('collapseSplitPanel')],
    ['确认横幅 #split-confirm-banner', html.includes('split-confirm-banner') || src.includes('showConfirmedBanner')],
  ];
  const alive = banned.filter(([, hit]) => hit).map(([label]) => label);
  assert.deepEqual(alive, [], `这些能力已被用户要求删除，不要复活：${alive.join('、')}`);
});
