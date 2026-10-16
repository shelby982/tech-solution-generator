import {test} from 'node:test';
import assert from 'node:assert/strict';
import {specContentHtml} from '../../frontend/assets/spec-content.mjs';

test('原文表格保留空单元格、全部行与单元格换行',()=>{
  const html=specContentHtml('说明\n| 项目 | 要求 | 空列 |\n| --- | --- | --- |\n| 进度 | 一<br>二 | |\n| 质量 | 三 | 四 |\n结尾');
  assert.match(html,/<table/);
  assert.match(html,/<td>一<br>二<\/td><td><\/td>/);
  assert.match(html,/<td>质量<\/td><td>三<\/td><td>四<\/td>/);
  assert.match(html,/结尾/);
});
test('原文 HTML 不执行，仅允许换行标记',()=>{
  const html=specContentHtml('<script>alert(1)</script><br/>原文');
  assert.ok(!html.includes('<script>'));
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/<br>原文/);
});
