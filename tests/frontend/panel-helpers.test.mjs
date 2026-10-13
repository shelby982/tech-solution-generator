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
    'openSplitReport',        // 拆分清单面板
    'bindScopeSelector',      // 投标范围选择器（事故函数）
    'showConfirmedBanner',    // 两阶段提示
    'maybeAutoOpenSplitReport', // 拆分完自动展示
    'markRevisionConfirmed',
    'getConfirmedRevision',
    'resolvePanelThreadId',
    'renderOutlineCoverage',
    'refreshOutlineCoverage',
    'toggleScoringRisk',
    'loadExistingBlocks',
  ];
  const missing = critical.filter(n => !defined.has(n));
  assert.deepEqual(missing, [], `缺少关键函数：${missing.join(', ')}`);
});
