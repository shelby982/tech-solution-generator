import {test} from 'node:test';
import assert from 'node:assert/strict';
import {resolvePackageScope} from '../../frontend/assets/workspace-model.mjs';

test('标的编号与原文标签一致时展示唯一评分表',()=>{
  const row={target:'标的1',package_no:'1',groups:[{category:'tech',count:13}]};
  assert.equal(resolvePackageScope([row],{target:'1',package_no:''}),row);
  assert.equal(resolvePackageScope([row],{target:'1',package_no:'1'}),row);
});
test('标的下有多个标包时不猜测或混入其他标包',()=>{
  const rows=[{target:'标的1',package_no:'1'},{target:'标的1',package_no:'2'}];
  assert.equal(resolvePackageScope(rows,{target:'1',package_no:''}),null);
  assert.equal(resolvePackageScope(rows,{target:'1',package_no:'2'}),rows[1]);
  assert.equal(resolvePackageScope(rows,{target:'2',package_no:'1'}),null);
});

test('一侧缺“标的”时按 package_no 唯一回退匹配（实测 project 29）', () => {
  // packages.target_code 为空，而评分表解析出“标的1” → 严格匹配为 null，
  // 页面就会显示“对应关系尚未明确 / 没有解析到技术评分点”，而评分点其实都在。
  const rows = [
    {target: '标的1', package_no: '1', package_name: '关键业务场景研究与验证标包'},
    {target: '标的1', package_no: '2', package_name: '高可靠技术专题研究与验证标包'},
  ];
  assert.equal(resolvePackageScope(rows, {target: '', package_no: '2'}), rows[1]);
  // 有歧义（同 package_no 多行）时不猜，仍然返回 null
  const dup = [
    {target: '标的1', package_no: '1'},
    {target: '标的2', package_no: '1'},
  ];
  assert.equal(resolvePackageScope(dup, {target: '', package_no: '1'}), null);
});
