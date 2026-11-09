/**
 * 统一转义检查（针对最危险的文本字段）：
 * innerHTML 模板里直接插 ${x.title}/${x.content}/... 且没有 esc/escapeHtml 的，
 * 必须是已知会内部转义的安全渲染函数（renderDiagramFigure/specContentHtml/diffHtml...）。
 *
 * 这不是完整 XSS 扫描：常量、数字、预构建 HTML 片段不在匹配范围内；目标是防止
 * "模型生成/上传文件名直接进 innerHTML" 的新回归。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '../../frontend');
const FIELD = /\b(?:title|content|point|message|filename|name|text|url|suggestion|reason|query)\b/;
const SAFE_CALL = /\b(?:esc|escHtml|escapeHtml|diffHtml|specContentHtml|renderDiagramFigure|issueHtml)\b/;

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return /\.(?:js|mjs)$/.test(entry.name) ? [full] : [];
  });
}

test('innerHTML 的文本字段插值必须转义', () => {
  const bad = [];
  for (const file of walk(ROOT)) {
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, index) => {
      if (!line.includes('innerHTML') || !line.includes('${')) return;
      for (const match of line.matchAll(/\$\{([^{}]*)\}/g)) {
        const expr = match[1];
        if (!FIELD.test(expr) || !expr.includes('.')) continue;
        if (SAFE_CALL.test(expr) || SAFE_CALL.test(line)) continue;
        bad.push(`${path.relative(ROOT, file)}:${index + 1}  ${expr.trim().slice(0, 100)}`);
      }
    });
  }
  assert.deepEqual(bad, [], `innerHTML 未转义文本字段：\n${bad.join('\n')}`);
});
