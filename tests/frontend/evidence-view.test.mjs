import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseEvidence, evidenceSnippet, evidenceSliceTitle,
  evidenceByChapter, evidenceByFile, evidenceBySource, usageFor, groupEvidenceByItem,
  EVIDENCE_KIND_LABELS, EVIDENCE_BY_LABELS,
} from '../../frontend/assets/workspace-model.mjs';

const ev = (over = {}) => ({
  chunk_id: 41, content: '服务团队\n项目经理 1 名，PMP 认证。',
  filename: '产品规格书.pdf', locator: 'P12 · 第 8 段 · 服务团队',
  page: 12, kind: 'fact', source_kind: 'ours', by: 'rule',
  confidence: 0.9, item_ids: ['s1#R1'], ...over,
});
const blk = (id, title, source) => ({ block_id: id, title, source: JSON.stringify(source) });

test('parseEvidence 吃 JSON 字符串 / 数组 / 空值，坏数据不炸', () => {
  assert.deepEqual(parseEvidence(blk('s1', 't', [ev()])).length, 1);
  assert.deepEqual(parseEvidence({ block_id: 's1', source: [ev()] }).length, 1);
  assert.deepEqual(parseEvidence({ block_id: 's1', source: '' }), []);
  assert.deepEqual(parseEvidence({ block_id: 's1' }), []);
  assert.deepEqual(parseEvidence({ block_id: 's1', source: '{坏 JSON' }), []);
  assert.deepEqual(parseEvidence({ block_id: 's1', source: '{"a":1}' }), []);
  // 数组里混垃圾元素：只保留看着像依据的
  assert.deepEqual(parseEvidence({ block_id: 's1', source: [{}, null, { chunk_id: 1 }] }).length, 1);
});

test('摘要去掉首行标题，只给正文', () => {
  assert.equal(evidenceSliceTitle('服务团队\n项目经理 1 名'), '服务团队');
  assert.equal(evidenceSnippet('服务团队\n项目经理 1 名'), '项目经理 1 名');
  // 单行（没有标题行）：退回整行，不能显示空
  assert.equal(evidenceSnippet('只有一行正文内容'), '只有一行正文内容');
  assert.equal(evidenceSnippet('', 5), '');
  assert.equal(evidenceSnippet('标题\n' + 'x'.repeat(300)).length, 141);   // 140 + 省略号
});

test('章节视角：条目计数 + 来源文件去重', () => {
  const idx = evidenceByChapter([
    blk('s1', '服务方案', [ev(), ev({ chunk_id: 42, item_ids: ['s1#R1', 's1#I1'], filename: '架构方案.pptx' })]),
    blk('s2', '性能', [ev({ chunk_id: 43, item_ids: [] })]),
    { block_id: 's3', title: '空章', source: '' },
  ]);
  assert.equal(idx.s1.total, 2);
  assert.deepEqual(idx.s1.items, { 's1#R1': 2, 's1#I1': 1 });
  assert.deepEqual(idx.s1.files.sort(), ['产品规格书.pdf', '架构方案.pptx'].sort());
  assert.equal(idx.s2.total, 1);
  assert.equal(idx.s3.total, 0);
});

test('素材视角：供 N 章 M 条依据（章去重、条目去重）', () => {
  const byFile = evidenceByFile([
    blk('s1', '服务方案', [ev(), ev({ chunk_id: 42, item_ids: ['s1#R1'] })]),   // 同一章 2 条
    blk('s2', '性能', [ev({ chunk_id: 43, item_ids: ['s2#I1', 's2#I2'] })]),
  ]);
  const spec = byFile['产品规格书.pdf'];
  assert.equal(spec.count, 3);
  assert.equal(spec.chapters.length, 2);          // 两条同章只算一章
  assert.equal(spec.itemCount, 3);                // R1 去重后 s1#R1, s2#I1, s2#I2
  assert.deepEqual(spec.chapters.map(c => c.id), ['s1', 's2']);
  assert.deepEqual(spec.chapters[0], { id: 's1', title: '服务方案' });
});

test('条目视角：按条目分组，未挂条目的素材进 chapterLevel', () => {
  const items = [
    { item_id: 's1#R1', type: 'requirement', text: '支持国产数据库' },
    { item_id: 's1#I1', type: 'indicator', text: '并发 ≥2000' },
  ];
  const g = groupEvidenceByItem(blk('s1', 't', [
    ev({ chunk_id: 1, item_ids: ['s1#R1'] }),
    ev({ chunk_id: 2, item_ids: ['s1#R1', 's1#I1'] }),
    ev({ chunk_id: 3, item_ids: [] }),
    ev({ chunk_id: 4, item_ids: ['s1#X9'] }),     // 条目不存在的脏 id：不吞掉，进章节级
  ]), items);
  assert.equal(g.total, 4);
  assert.deepEqual(g.items[0].evidence.map(e => e.chunk_id), [1, 2]);
  assert.deepEqual(g.items[1].evidence.map(e => e.chunk_id), [2]);
  assert.deepEqual(g.chapterLevel.map(e => e.chunk_id), [3, 4]);
  // 条目顺序 = 传入顺序（后端展开顺序稳定 → 页面顺序才不会跳）
  assert.deepEqual(g.items.map(x => x.item.item_id), ['s1#R1', 's1#I1']);
  // 没有条目（未提炼 / 纯目录节点）时不炸，全部落 chapterLevel
  const g2 = groupEvidenceByItem(blk('s1', 't', [ev()]), []);
  assert.equal(g2.items.length, 0);
  assert.equal(g2.chapterLevel.length, 1);
});

test('徽标文案表覆盖后端枚举（后端加值时不静默显示英文）', () => {
  assert.deepEqual(Object.keys(EVIDENCE_KIND_LABELS).sort(), ['background', 'fact', 'solution']);
  assert.deepEqual(Object.keys(EVIDENCE_BY_LABELS).sort(), ['human', 'model', 'rule', 'semantic']);
});

// ── HTML 渲染（纯字符串，模型层）──
import { evidenceRowHtml, renderEvidenceSection, itemCode,
         chapterStage, chapterBadges, filterChapters, chapterFilterCounts,
         CHAPTER_STAGE_LABELS, expandVisibleWithAncestors } from '../../frontend/assets/workspace-model.mjs';

test('行内徽标：状态必显，有依据才显示「依据 N」', () => {
  // 徽标统一由 chapterBadges 产出（见下方"章节状态投影"）：这里锚住"有/无依据"的差异
  const none = chapterBadges(blk('s1', 't', []));
  assert.deepEqual(none.map(b => b.text), ['待提炼']);       // 无依据时不给「依据 0」噪声
  const two = chapterBadges({ block_id: 's1', title: 't', requirement: '要求' }, { evidenceCount: 2 });
  assert.deepEqual(two.map(b => b.text), ['已匹配', '依据 2']);
});

test('依据行必须带出处（文件名 + locator）与性质徽标，摘要不带首行标题', () => {
  const html = evidenceRowHtml(ev());
  assert.match(html, /产品规格书\.pdf/);
  assert.match(html, /P12 · 第 8 段 · 服务团队/);
  assert.match(html, /be-kind fact">事实</);
  assert.match(html, /项目经理 1 名，PMP 认证。/);
  assert.doesNotMatch(html, /be-snip">服务团队/);   // 摘要里不该再重复标题行
  // 出处缺失也不崩，仍能渲染
  assert.match(evidenceRowHtml(ev({ filename: '', locator: '' })), /be-kind/);
});

test('条目分组区：有依据按条目列、无依据只报数、整章组兜底', () => {
  const items = [
    { item_id: 's1#R1', text: '支持国产数据库' },
    { item_id: 's1#I1', text: '并发 ≥2000' },
  ];
  const html = renderEvidenceSection(blk('s1', 't', [
    ev({ chunk_id: 1, item_ids: ['s1#R1'] }),
    ev({ chunk_id: 2, item_ids: [] }),
  ]), items);
  assert.match(html, /素材依据 <b>2<\/b> 条/);
  assert.match(html, /覆盖 1 \/ 2 条要回应事项/);
  assert.match(html, /be-code">R1</);
  assert.match(html, /支持国产数据库/);
  assert.match(html, /1 条依据/);
  assert.match(html, /be-code">整章</);          // 未挂条目的进整章组
  assert.match(html, /无依据条目 <b>1<\/b> \/ 2：\s*I1/);
  assert.match(html, /不阻断/);
  assert.match(html, /title="I1：并发 ≥2000"/);   // 悬停能看回条目原文
  // 一个依据都没有 → 不渲染空区块
  assert.equal(renderEvidenceSection({ block_id: 's1', source: '' }, items), '');
});

test('条目未展开时退化为章节级列表（不把依据藏起来）', () => {
  const html = renderEvidenceSection(blk('s1', 't', [ev()]), []);
  assert.match(html, /该章未展开应答条目/);
  assert.match(html, /产品规格书\.pdf/);
  assert.doesNotMatch(html, /无依据条目/);
});

test('itemCode 取短码；HTML 注入被转义', () => {
  assert.equal(itemCode({ item_id: 's2.3.1#V1' }), 'V1');
  assert.equal(itemCode({}), '');
  const html = renderEvidenceSection(blk('s1', '<img onerror=x>', [
    ev({ content: '标题\n<script>alert(1)</script>', filename: 'a"b.pdf' }),
  ]), [{ item_id: 's1#R1', text: '<b>不该是粗体</b>' }]);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<b>不该是粗体/);
});

test('同一文件名传两份素材：按 material_id 分开计数，不合并到一行', () => {
  const blocks = [
    blk('s1', '服务方案', [ev({ material_id: 7, chunk_id: 1, item_ids: ['s1#R1'] })]),
    blk('s2', '性能', [ev({ material_id: 8, chunk_id: 2, item_ids: ['s2#I1', 's2#I2'] })]),
  ];
  const idx = evidenceBySource(blocks);
  assert.equal(usageFor(idx, { id: 7, filename: '同名.pdf' }).count, 1);
  assert.equal(usageFor(idx, { id: 8, filename: '同名.pdf' }).count, 1);
  assert.equal(usageFor(idx, { id: 8, filename: '同名.pdf' }).itemCount, 2);
  assert.deepEqual(usageFor(idx, { id: 7, filename: '同名.pdf' }).chapters.map(c => c.id), ['s1']);
  // 没匹配到 id 的素材（未挂载）→ null，页面显示「未挂载」
  assert.equal(usageFor(idx, { id: 99, filename: '别的.pdf' }), null);
  // 老数据没有 material_id：按文件名兜底仍可用
  const legacy = evidenceBySource([blk('s1', '服务方案', [ev({ material_id: undefined })])]);
  assert.equal(usageFor(legacy, { id: 7, filename: '产品规格书.pdf' }).count, 1);
  assert.equal(usageFor({}, { id: 7, filename: 'x.pdf' }), null);   // 空索引不炸
});

// ── 「素材匹配」入口判定（回归：见 matchEntryDecision 注释里的真实事故）──
import { matchEntryDecision, MATCH_ENTRY } from '../../frontend/assets/workspace-model.mjs';

test('提炼已完成但 checkpoint 仍写着 outline_review → 开始匹配（不再拽回要求与大纲）', () => {
  // 真实数据形态：workflow_runs='done'、33/33 节 outline_done、checkpoint stage='outline_review'
  assert.equal(matchEntryDecision({ stage: 'outline_review', totalBlocks: 33, doneBlocks: 33 }),
               MATCH_ENTRY.START);
  // 部分提炼（有失败/未完成章节）也应能匹配：匹配是增量的，跑早了没有代价
  assert.equal(matchEntryDecision({ stage: 'outline_review', totalBlocks: 33, doneBlocks: 4 }),
               MATCH_ENTRY.START);
});

test('闸门 1 真的没过（0 节提炼）→ 回要求与大纲', () => {
  assert.equal(matchEntryDecision({ stage: 'outline_review', totalBlocks: 11, doneBlocks: 0 }),
               MATCH_ENTRY.GATE_OUTLINE);
  assert.equal(matchEntryDecision({ stage: 'outline_review', totalBlocks: 0, doneBlocks: 0 }),
               MATCH_ENTRY.GATE_OUTLINE);
});

test('长阶段在跑 → 等它跑完（不发请求、不动视图）', () => {
  for (const running of ['extracting', 'matching']) {
    assert.equal(matchEntryDecision({ stage: 'outline_review', totalBlocks: 33, doneBlocks: 33, running }),
                 MATCH_ENTRY.WAIT, running);
    assert.equal(matchEntryDecision({ stage: 'materials_review', totalBlocks: 33, doneBlocks: 33, running }),
                 MATCH_ENTRY.WAIT, running);
  }
});

test('闸门 2 停留态 → 重新匹配；生成/评审阶段 → 去质量检查页', () => {
  assert.equal(matchEntryDecision({ stage: 'materials_review', totalBlocks: 33, doneBlocks: 33 }),
               MATCH_ENTRY.START);
  for (const stage of ['report_review', 'done', 'paused']) {
    assert.equal(matchEntryDecision({ stage, totalBlocks: 33, doneBlocks: 33 }), MATCH_ENTRY.REVIEW, stage);
  }
  for (const stage of ['parsing', 'idle', '', undefined]) {
    assert.equal(matchEntryDecision({ stage, totalBlocks: 33, doneBlocks: 33 }), MATCH_ENTRY.BLOCKED, String(stage));
  }
});

// ── 章节状态投影（共享给素材库 / 方案编辑 / 覆盖矩阵）──

test('章节状态单向推进：无原文 → 待提炼 → 已提炼 → 已匹配 → 已撰写', () => {
  assert.equal(chapterStage({ block_id: 's1', error_kind: 'no_source' }), 'no_source');
  assert.equal(chapterStage({ block_id: 's1' }), 'pending');
  assert.equal(chapterStage({ block_id: 's1', error_kind: 'failed' }), 'pending');   // 失败可重试
  assert.equal(chapterStage({ block_id: 's1', requirement: '要求' }), 'extracted');
  assert.equal(chapterStage({ block_id: 's1', requirement: '要求' }, { evidenceCount: 3 }), 'matched');
  assert.equal(chapterStage({ block_id: 's1', requirement: '要求', content: '正文' }), 'written');
  // 正文优先：已写正文的章节不该因为"没挂到证据"退回已提炼
  assert.equal(chapterStage({ block_id: 's1', requirement: '要求', content: '正文' },
                            { evidenceCount: 0 }), 'written');
  assert.equal(CHAPTER_STAGE_LABELS.matched, '已匹配');
});

test('行徽标：状态 + 有依据才显示「依据 N」', () => {
  assert.deepEqual(chapterBadges({ block_id: 's1', requirement: '要求' }).map(b => b.text),
                   ['已提炼']);
  assert.deepEqual(chapterBadges({ block_id: 's1', requirement: '要求' }, { evidenceCount: 2 }).map(b => b.text),
                   ['已匹配', '依据 2']);
});

test('筛选与计数：无依据/未撰写/待提炼', () => {
  const blocks = [
    { block_id: 's1', requirement: '甲要求' },                     // extracted（无依据）
    { block_id: 's2', requirement: '乙要求', content: '正文' },     // written
    { block_id: 's3' },                                            // pending
    { block_id: 's4', error_kind: 'no_source' },                   // no_source
  ];
  const ev = { s2: [{ chunk_id: 1 }, { chunk_id: 2 }] };
  assert.deepEqual(filterChapters(blocks, { mode: 'without_evidence', evidenceByBlock: ev })
                     .map(b => b.block_id), ['s1']);
  assert.deepEqual(filterChapters(blocks, { mode: 'unwritten', evidenceByBlock: ev })
                     .map(b => b.block_id), ['s1', 's3', 's4']);
  // no_source 是终态（本来就没原文可提炼），不进"待提炼"待办
  assert.deepEqual(filterChapters(blocks, { mode: 'pending', evidenceByBlock: ev })
                     .map(b => b.block_id), ['s3']);
  assert.deepEqual(chapterFilterCounts(blocks, ev),
                   { all: 4, without_evidence: 1, unwritten: 3, pending: 1 });
});

test('依据区支持动作回调：无依据条目也给「找素材」入口', () => {
  const items = [
    { item_id: 's1#R1', text: '支持国产数据库' },
    { item_id: 's1#I1', text: '并发 ≥2000' },
  ];
  const html = renderEvidenceSection(
    blk('s1', 't', [ev({ chunk_id: 1, item_ids: ['s1#R1'] })]), items,
    {
      itemActions: (it) => `<button data-find="${it.item_id}">找素材</button>`,
      rowActions: (e) => `<button data-unmount="${e.chunk_id}">解绑</button>`,
    },
  );
  // 无依据条目也出现在列表里（它才是补素材的待办），并带入口
  assert.match(html, /be-group-empty/);
  assert.match(html, /data-find="s1#I1"/);
  assert.match(html, /无依据/);
  assert.match(html, /data-unmount="1"/);
  // 给了 itemActions 就逐条列，不再另起"无依据条目 N 条"的汇总行（避免两处重复）
  assert.doesNotMatch(html, /无依据条目 <b>/);
  // 不给动作时（只读展示）行为不变：无依据条目不出现在分组里
  const ro = renderEvidenceSection(blk('s1', 't', [ev({ chunk_id: 1, item_ids: ['s1#R1'] })]), items);
  assert.doesNotMatch(ro, /be-group-empty/);
  assert.match(ro, /无依据条目 <b>1<\/b>/);
});

test('树筛选保留祖先链：命中的子节点不会因为父节点被隐藏而消失', () => {
  const nodes = [
    { id: 's1', level: 1 }, { id: 's1.1', level: 2 }, { id: 's1.2', level: 2 },
    { id: 's2', level: 1 }, { id: 's2.1', level: 2 }, { id: 's2.1.1', level: 3 },
    { id: 's3', level: 1 },
  ];
  // 只命中深层子节点 → 祖孙三代都要可见（否则中间栏整栏是空的）
  const vis = expandVisibleWithAncestors(nodes, ['s2.1.1']);
  assert.deepEqual([...vis].sort(), ['s2', 's2.1', 's2.1.1']);
  // 命中顶层 → 不需要带任何人
  assert.deepEqual([...expandVisibleWithAncestors(nodes, ['s1'])], ['s1']);
  // 空输入不炸
  assert.equal(expandVisibleWithAncestors([], []).size, 0);
  assert.equal(expandVisibleWithAncestors(nodes, []).size, 0);
});

test('无依据但有 itemActions：条目照样列出来（补素材的待办清单）', () => {
  const items = [{ item_id: 's1#R1', text: '支持国产数据库' }];
  const html = renderEvidenceSection({ block_id: 's1', source: '' }, items,
                                      { itemActions: (it) => `<button data-find="${it.item_id}">找素材</button>` });
  assert.match(html, /data-find="s1#R1"/);
  assert.match(html, /无依据/);
  // 只读展示仍然不渲染空区块
  assert.equal(renderEvidenceSection({ block_id: 's1', source: '' }, items), '');
});

test('open 选项：匹配工作区里默认展开（只看标题等于没看到依据）', () => {
  const block = blk('s1', 't', [ev({ chunk_id: 1, item_ids: ['s1#R1'] })]);
  const items = [{ item_id: 's1#R1', text: '支持国产数据库' }];
  assert.match(renderEvidenceSection(block, items), /class="block-evidence"/);
  assert.match(renderEvidenceSection(block, items, { open: true }), /class="block-evidence open"/);
});

// ── 让匹配结果能看懂：弱关联/疑似招标文件/说明性条目/未覆盖折叠 ──
import { isWeakEvidence, looksLikeTenderFile, evidenceCautionHtml,
         WEAK_EVIDENCE_CONFIDENCE } from '../../frontend/assets/workspace-model.mjs';

test('弱关联：语义命中且置信度低要标出来（实测 0.305 的名词定义被挂成了依据）', () => {
  assert.equal(isWeakEvidence(ev({ by: 'semantic', confidence: 0.305 })), true);
  assert.equal(isWeakEvidence(ev({ by: 'semantic', confidence: 0.9 })), false);
  assert.equal(isWeakEvidence(ev({ by: 'rule', confidence: 0.1 })), false);   // 规则命中不是猜的
  assert.equal(isWeakEvidence(ev({ by: 'human', confidence: 0 })), false);    // 人工挂接不用提示
  assert.equal(isWeakEvidence(ev({ by: 'semantic', confidence: undefined })), true);
  assert.equal(WEAK_EVIDENCE_CONFIDENCE, 0.5);
  assert.match(evidenceCautionHtml(ev({ by: 'semantic', confidence: 0.305 })), /弱关联·建议核对/);
  assert.equal(evidenceCautionHtml(ev({ by: 'semantic', confidence: 0.9 })), '');
});

test('疑似招标文件：文件名像招标方给的，却当成我方素材上传 → 提示但不拦截', () => {
  assert.equal(looksLikeTenderFile('4_高可靠技术专题研究与验证-技术招标文件.docx'), true);
  assert.equal(looksLikeTenderFile('产品规格书.pdf'), false);
  const html = evidenceCautionHtml(ev({ source_kind: 'ours',
    filename: '4_高可靠技术专题研究与验证-技术招标文件.docx', by: 'rule', confidence: 1 }));
  assert.match(html, /疑似招标文件/);
  // 已经按"招标文件"分池的（source_kind=tender）不用再提示
  assert.equal(evidenceCautionHtml(ev({ source_kind: 'tender', filename: '招标文件.pdf' })), '');
});

test('说明性条目（评分口径）单独成组，不混进"要回应的事项"', () => {
  const items = [
    { item_id: 's1#R1', text: '投标方需对招标技术条款作出响应', informative: false },
    { item_id: 's1#R3', text: '优良（7-10分）：响应度较高', informative: true },
    { item_id: 's1#I1', text: '技术评分标准满分10分', informative: true },
  ];
  const block = blk('s1', 't', [ev({ chunk_id: 1, item_ids: ['s1#R1'] })]);
  const html = renderEvidenceSection(block, items, {
    open: true, foldUncovered: true,
    itemActions: (it) => `<button data-find="${it.item_id}">找素材</button>`,
  });
  assert.match(html, /评分与量化指标 <b>2<\/b> 条/);
  assert.match(html, /不需要逐条找素材/);
  // 评分口径条目不给「找素材」
  assert.doesNotMatch(html, /data-find="s1#R3"/);
  assert.doesNotMatch(html, /data-find="s1#I1"/);
  // 头部覆盖度分母只算"要回应的事项"（这里 1 条，全都有依据）
  assert.match(html, /覆盖 1 \/ 1 条要回应事项/);
});

test('未覆盖的"要回应事项"默认收成一行，展开后才是逐条', () => {
  const items = Array.from({ length: 5 }, (_, i) => ({
    item_id: `s1#R${i + 1}`, text: `要求 ${i + 1}`, informative: false,
  }));
  const block = blk('s1', 't', [ev({ chunk_id: 1, item_ids: ['s1#R1'] })]);
  const folded = renderEvidenceSection(block, items, {
    itemActions: (it) => `<button data-find="${it.item_id}">找素材</button>`, foldUncovered: true,
  });
  assert.match(folded, /其余 <b>4<\/b> 条要回应的事项暂无素材依据/);
  assert.match(folded, /不阻断撰写/);
  assert.match(folded, /be-uncovered-body" hidden/);
  assert.match(folded, /data-find="s1#R2"/);          // 展开后还在，只是默认 hidden
  // 不折叠时保持原样（逐条铺开）
  const flat = renderEvidenceSection(block, items, {
    itemActions: (it) => `<button data-find="${it.item_id}">找素材</button>`,
  });
  assert.doesNotMatch(flat, /be-uncovered/);
});

test('章节状态投影带出评审失败原因（不能只显示"评审异常"却不给原因）', async () => {
  const { projectChapters } = await import('../../frontend/assets/workspace-model.mjs');
  const chapters = projectChapters(
    { proposal: { blocks: { s1: { content: '正文' } } },
      review: { tech_findings: { s1: { error: 'Connection error.' } },
                compliance_findings: { s1: { issues: [] } } },
      spec: { toc: [{ id: 's1', level: 2, title: '技术条款差异性响应' }] },
      ui: { review_threshold: 80 } },
    [{ block_id: 's1', title: '技术条款差异性响应', content: '正文' }],
  );
  const ch = chapters.find(c => c.block_id === 's1');
  assert.equal(ch.status, 'error');
  assert.equal(ch.reviewError, 'Connection error.');
  assert.deepEqual(ch.issues, []);   // 没有 issues 是真的，但原因要能显示
});
