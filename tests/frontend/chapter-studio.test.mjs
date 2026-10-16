import {test} from 'node:test';
import assert from 'node:assert/strict';
import {previewContent, exportScope} from '../../frontend/assets/chapter-studio.mjs';

test('图文表预览只接受内部图片，所有外部 HTML 和脚本被转义',()=>{
  const html=previewContent('# 响应\n<script>alert(1)</script>\n![图](/api/studio/materials/12/image)\n![外部](javascript:alert(1))\n\n| 要求 | 响应 |\n| --- | --- |\n| A | B |');
  assert.ok(html.includes('<img src="/api/studio/materials/12/image"'));
  assert.ok(html.includes('<table class="spec-source-table">'));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('src="javascript:'));
});
test('图示内容保持顺序且不执行节点中的 HTML',()=>{
  const html=previewContent(':::diagram 架构\n接入 -> <img onerror=alert(1)> -> 数据\n:::');
  assert.ok(html.indexOf('接入')<html.indexOf('数据'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img onerror'));
});
test('单章与分组导出严格保持选中章节的子树边界',()=>{
  const blocks=[{id:1,level:1},{id:2,level:2},{id:3,level:3},{id:4,level:2},{id:5,level:1}];
  assert.deepEqual(exportScope(blocks,2,'chapter').map(b=>b.id),[2]);
  assert.deepEqual(exportScope(blocks,2,'group').map(b=>b.id),[2,3]);
  assert.deepEqual(exportScope(blocks,1,'group').map(b=>b.id),[1,2,3,4]);
  assert.deepEqual(exportScope(blocks,99,'group'),[]);
});
