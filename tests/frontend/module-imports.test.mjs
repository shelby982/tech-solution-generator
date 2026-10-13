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

// ── 模块版本号必须**全局统一**（防"一半新一半旧"的缓存错配）────────────
// 真实事故：修复 workspace.js 的再导出后，用户在浏览器里仍然卡在骨架 ——
// 因为 `mode-outline.js` 里对 workspace.js 的导入还挂着**很早的** `?v=20261013-noconfirm`，
// 浏览器命中了缓存里的旧文件（没有那个再导出）→ 具名导入不存在 → 整页脚本不执行。
// 无头浏览器每次都是全新 profile（无缓存），所以本地测不出来。
// 结论：所有模块导入/脚本引用共用一个版本号；改代码时只 bump 这一个 token。
test('模块引用共用同一个版本号（避免缓存里新旧混用）', () => {
  const frontend = join(here, '..', '..', 'frontend');
  const files = [
    ...[ 'three-mode.html', 'projects.html', 'project-init.html', 'workbench.html',
         'review.html', 'diff-review.html' ].map(f => join(frontend, f)),
    ...[ 'mode-outline.js', 'mode-editor.js', 'mode-dispatch.js', 'workspace.js',
         'workbench.js', 'context-panel.js', 'review.js', 'generate-session.js' ]
        .map(f => join(frontend, 'assets', f)),
  ];
  const versions = new Map();
  for (const file of files) {
    if (!existsSync(file)) continue;
    const rel = file.slice(frontend.length + 1);
    const src = readFileSync(file, 'utf8');
    for (const m of src.matchAll(/\.(?:js|mjs|css)\?v=([A-Za-z0-9._-]+)/g)) {
      if (!versions.has(m[1])) versions.set(m[1], []);
      versions.get(m[1]).push(rel);
    }
  }
  assert.ok(versions.size > 0, '没有找到任何版本号，检查正则');
  assert.equal(versions.size, 1,
    `模块版本号不统一（共 ${versions.size} 种）：\n` +
    [...versions.entries()].map(([v, fs]) => `  ?v=${v} ← ${fs.length} 个文件，例如 ${fs[0]}`).join('\n'));
});
