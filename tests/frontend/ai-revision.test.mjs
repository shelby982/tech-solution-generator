import test from 'node:test';
import assert from 'node:assert/strict';
import {reverseAIChange,selectedRange,rebaseSelection} from '../../frontend/assets/ai-revision.mjs';

test('撤销选区修改保留范围外后续人工编辑',()=>{
  assert.deepEqual(reverseAIChange('前文\n旧句\n后文','前文\n新句\n后文','新增前言\n前文\n新句\n后文\n人工追加'),
    {content:'新增前言\n前文\n旧句\n后文\n人工追加'});
});

test('人工修改重叠及重复匹配均保留当前版本',()=>{
  assert.equal(reverseAIChange('甲旧文乙','甲新文乙','甲人工改过的新文乙').conflict,true);
  assert.equal(reverseAIChange('旧句','新句','新句与新句').conflict,true);
});

test('撤销删除与插入，并保留非重叠追加内容',()=>{
  assert.equal(reverseAIChange('前文删去的内容后文','前文后文','前文后文追加').content,'前文删去的内容后文追加');
  assert.equal(reverseAIChange('甲乙','甲新增乙','甲新增乙追加').content,'甲乙追加');
});

test('选区按 UTF16 正确保留 emoji 和范围外内容',()=>{
  const text='前🙂目标后';
  const start=text.indexOf('目标');
  assert.deepEqual(selectedRange(text,start,start+2),{before:'前🙂',selection:'目标',after:'后'});
  assert.equal(selectedRange(text,start,start),null);
});


test('保存规范化表格后重定位选区，重复选区要求重新选择',()=>{
  const range={before:'|列|\n|---|\n\n',selection:'唯一句子🙂',after:'\n末尾'};
  const saved='**表：指标**\n\n| 列 |\n| --- |\n\n唯一句子🙂\n末尾';
  const rebased=rebaseSelection(range,saved);
  assert.equal(rebased.before+rebased.selection+rebased.after,saved);
  assert.equal(rebased.selection,range.selection);
  assert.equal(rebaseSelection(range,'唯一句子🙂 和 唯一句子🙂'),null);
});
