import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../../frontend/assets/mode-outline.js', import.meta.url),'utf8');
const resolver = source.match(/async function resolvePanelThreadId\(tid\) \{[\s\S]*?\n\}/)[0];

test('应答报告读取 URL 选中的商务运行，即使最新运行是技术',async()=>{
  let latestCalls=0;
  const resolve = new Function('params','currentThreadId','workspace','getThread','projectId','api',`${resolver};return resolvePanelThreadId;`)(
    new URLSearchParams('threadId=commerce-run'),'tech-run',{threadId:'tech-run'},()=> 'tech-run',27,
    {workflow:{latestRunThreadId:async()=>{latestCalls++;return 'tech-run';}}});
  assert.equal(await resolve(null),'commerce-run');
  assert.equal(await resolve('explicit-run'),'explicit-run');
  assert.equal(latestCalls,0);
});

test('没有选中的应答运行时才读取最新运行',async()=>{
  const resolve = new Function('params','currentThreadId','workspace','getThread','projectId','api',`${resolver};return resolvePanelThreadId;`)(
    new URLSearchParams(),null,{},()=>null,27,{workflow:{latestRunThreadId:async()=> 'latest-run'}});
  assert.equal(await resolve(null),'latest-run');
});
