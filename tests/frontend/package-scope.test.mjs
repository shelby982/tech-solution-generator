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
