import test from 'node:test';
import assert from 'node:assert/strict';
import {projectChapters,summarize,diffHtml,buildOutlineTree} from '../../frontend/assets/workspace-model.mjs';
const base={spec:{toc:[{id:'s1',title:'架构'}]},review:{tech_findings:{s1:{score:90}},compliance_findings:{s1:{score:90}},tech_contents:{s1:'原文'},compliance_contents:{s1:'原文'}}};
const chapter=(s=base,text='原文')=>projectChapters(s,[{id:1,block_id:'s1',content:text}])[0];
test('saved edits invalidate previous review even when scores passed',()=>assert.equal(chapter(base,'手改').status,'stale'));
test('empty saved text cannot inherit passing proposal/review',()=>assert.equal(chapter({...base,proposal:{blocks:{s1:{content:'旧文'}}}},'').status,'empty'));
test('historical scores without content snapshots cannot claim passed',()=>assert.equal(chapter({...base,review:{tech_findings:{s1:{score:99}},compliance_findings:{s1:{score:99}}}}).status,'historical'));
test('threshold from server controls pass',()=>assert.equal(chapter({...base,ui:{review_threshold:95}}).status,'revise'));
test('valid paired review passes; summary counts statuses',()=>{assert.equal(chapter().status,'passed');assert.equal(summarize([chapter(),chapter(base,'新文')]).needsAction,1);});
test('diff escapes untrusted text and highlights changed lines',()=>{const html=diffHtml('same\n<script>old</script>','same\nnew');assert.ok(html.includes('<del>&lt;script&gt;old&lt;/script&gt;</del>'));assert.ok(html.includes('<ins>new</ins>'));assert.ok(!html.includes('<script>'));});
// 目录树形状用「a(b, c(d))」表示：括号即子节点。断言的是父子关系而不是 level 数字
// —— 与后端 normalize_nodes 一致，文档顺序才是层级权威，level 只当提示。
const shape=sections=>buildOutlineTree(sections).map(function f(e){return e.node.id+(e.children.length?'('+e.children.map(f).join(', ')+')':'');});
test('buildOutlineTree nests deeper levels under the nearest shallower node',()=>assert.deepEqual(shape([{id:'a',level:1},{id:'b',level:2},{id:'c',level:3},{id:'d',level:4}]),['a(b(c(d)))']));
test('buildOutlineTree tolerates level jumps instead of inventing empty parents',()=>assert.deepEqual(shape([{id:'a',level:1},{id:'b',level:3},{id:'c',level:2}]),['a(b, c)']));
test('buildOutlineTree promotes orphans with no shallower predecessor to roots',()=>assert.deepEqual(shape([{id:'a',level:2},{id:'b',level:1}]),['a','b']));
test('buildOutlineTree keeps a single top-level node flat',()=>assert.deepEqual(shape([{id:'a',level:1}]),['a']));
test('buildOutlineTree treats missing level as top level and empty input as no tree',()=>{assert.deepEqual(shape([{id:'a'},{id:'b'}]),['a','b']);assert.deepEqual(shape([]),[]);});
test('outline provenance fields never leak into chapter projection',()=>{const spec={...base.spec,source_toc:[{id:'s9',title:'原目录'}],outline_revision:3,outline_error:'降级'};assert.deepEqual(projectChapters({...base,spec},[{id:1,block_id:'s1',content:'原文'}]),projectChapters(base,[{id:1,block_id:'s1',content:'原文'}]));});
