/**
 * ES 模块具名导入的静态守卫。
 *
 * 背景（真实事故）：把 `SELECTABLE_REQUIREMENT_ROLES` 加进了**从 workspace.js 的导入**，
 * 但它是从 material-roles.mjs 导出的。ES 模块的具名导入若在目标模块里不存在，
 * 是**链接期错误**：整个模块都不执行 → 页面脚本全哑、骨架永远转圈，控制台只有一条
 * 不易察觉的报错。这类错误本地 `node --check` 查不出来（它只看单文件语法）。
 *
 * 所以这里跨文件校验：`import { a, b } from './x.js'` 里的每个具名导入，
 * 都必须能在 `x` 里找到对应的导出（`export const/function/class`、
 * `export { a, b }`、`export { a as b }`、`export * from`）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const assets = join(here, '..', '..', 'frontend', 'assets');

/** 剥掉注释再解析：多行 `import {` 里常夹着 `// 说明`，
 *  不剥会把注释文字当成导入名（本测试第一版就踩了这个坑）。 */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** 一个模块导出的所有名字。 */
function exportedNames(file) {
  const src = stripComments(readFileSync(file, 'utf8'));
  const names = new Set();
  for (const m of src.matchAll(/export\s+(?:async\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(m[1]);
  }
  // export { a, b as c }
  for (const m of src.matchAll(/export\s*\{([^}]*)\}/g)) {
    m[1].split(',').forEach(part => {
      const t = part.trim();
      if (!t) return;
      names.add((t.split(/\s+as\s+/).pop() || t).trim());
    });
  }
  // export * from './x' → 递归并入
  for (const m of src.matchAll(/export\s*\*\s*from\s*['"]([^'"]+)['"]/g)) {
    const target = resolveTarget(file, m[1]);
    if (target) for (const n of exportedNames(target)) names.add(n);
  }
  return names;
}

/** 把 `./x.js?v=123` 解析成磁盘路径。 */
function resolveTarget(fromFile, spec) {
  const clean = spec.split('?')[0];
  const p = resolve(dirname(fromFile), clean);
  return existsSync(p) ? p : null;
}

/** 文件的具名导入：{ names: [...], from: './x.js' }[] */
function namedImports(file) {
  const src = stripComments(readFileSync(file, 'utf8'));
  const out = [];
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g)) {
    const names = m[1].split(',').map(x => x.trim()).filter(Boolean)
      .map(x => (x.split(/\s+as\s+/)[0] || '').trim());
    out.push({ names, from: m[2] });
  }
  return out;
}

const MODULES = ['mode-outline.js', 'mode-editor.js', 'workspace.js', 'mode-dispatch.js',
                 'workbench.js', 'context-panel.js'];

test('所有具名导入都能在目标模块里找到对应导出（防止整页脚本哑掉）', () => {
  const problems = [];
  for (const name of MODULES) {
    const file = join(assets, name);
    if (!existsSync(file)) continue;
    for (const { names, from } of namedImports(file)) {
      const target = resolveTarget(file, from);
      if (!target) { problems.push(`${name}: 找不到模块 ${from}`); continue; }
      const exported = exportedNames(target);
      for (const n of names) {
        if (n && !exported.has(n)) {
          problems.push(`${name} 从 ${from} 导入了 ${n}，但该模块没有这个导出`);
        }
      }
    }
  }
  assert.deepEqual(problems, [], problems.join('\n'));
});

test('本次事故的那个导出确实存在且可用', () => {
  const file = join(assets, 'material-roles.mjs');
  const exported = exportedNames(file);
  assert.ok(exported.has('SELECTABLE_REQUIREMENT_ROLES'));
  // workspace.js 是页面取角色常量的中转站：它也必须再导出
  assert.ok(exportedNames(join(assets, 'workspace.js')).has('SELECTABLE_REQUIREMENT_ROLES'),
    'workspace.js 未再导出 SELECTABLE_REQUIREMENT_ROLES，页面按名字导入会链接失败');
});
