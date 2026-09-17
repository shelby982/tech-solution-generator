// Pure projections shared by navigation, chapter tree and quality review.
export const statusLabels = {
  empty: '待撰写', material: '待补料', revise: '待修订', stale: '待复审',
  reviewing: '复审中', generating: '修订中', passed: '已通过',
  unreviewed: '待评审', historical: '历史评审待核对', error: '评审异常',
};
export const stageLabels = {
  idle: '等待上传要求', parsing: '解析要求中', outline_review: '待确认大纲',
  matching: '匹配素材中', materials_review: '待确认素材', generating: '撰写 / 修订中',
  reviewing: '评审中', report_review: '等待人工检查', done: '本次运行已结束',
  paused: '已暂停', aborted: '已作废',
};
export const activeStages = new Set(['parsing', 'matching', 'generating', 'reviewing']);

// 后端重启会清空 runner 内存里的 _Run，但 SQLite 里的 checkpoint 还停在「跑了一半」的
// stage 上，/state 照样返回一份看起来正常的快照。要同时满足两条才算孤儿：进程已经不认得
// 这个 thread（alive === false），且图确实停在中途（stage 在 activeStages 里）。
// 闸门停留态（outline_review / materials_review / report_review / paused）和终态
// （done / aborted）都不在 activeStages 里，正常停等和已完成的 run 不会被误判。
// alive 缺失（老后端 / 缓存未刷新）时一律当正常：宁可不说，也不能误报。
export function isOrphanedRun(state = {}) {
  if (state.alive !== false) return false;
  return activeStages.has(state.stage);
}
export function projectChapters(state = {}, rows = []) {
  const proposal = state.proposal || {};
  const review = state.review || {};
  const matrix = state.spec?.outline_matrix || {};
  const db = new Map(rows.map(b => [String(b.block_id), b]));
  const toc = state.spec?.toc?.length ? state.spec.toc : rows.map(b => ({...b, id: b.block_id}));
  const updating = new Set(proposal.updated_blocks || []);
  const targets = new Set(proposal.regenerate_targets || []);
  return toc.map(section => {
    const bid = String(section.id);
    const row = db.get(bid);
    const output = proposal.blocks?.[bid];
    const content = row ? (row.content || '') : (output?.content || '');
    const tech = review.tech_findings?.[bid];
    const comp = review.compliance_findings?.[bid];
    const issues = [...(tech?.issues || []).map(i => ({...i, agent: '技术评审'})),
                    ...(comp?.issues || []).map(i => ({...i, agent: '合规评审'}))];
    const tContent = review.tech_contents?.[bid];
    const cContent = review.compliance_contents?.[bid];
    const bound = tContent !== undefined && cContent !== undefined;
    const stale = bound && (tContent !== content || cContent !== content);
    const threshold = state.ui?.review_threshold ?? 80;
    let status = 'unreviewed';
    if (!content.trim()) status = 'empty';
    else if (stale) status = 'stale';
    else if (tech?.error || comp?.error) status = 'error';
    else if (issues.some(i => i.needs_material)) status = 'material';
    else if (!tech || !comp) status = 'unreviewed';
    else if (!bound) status = 'historical';
    else if (Number(tech.score) < threshold || Number(comp.score) < threshold || issues.some(i => i.severity === 'critical')) status = 'revise';
    else status = 'passed';
    if (state.stage === 'reviewing' && updating.has(bid)) status = 'reviewing';
    if (state.stage === 'generating' && targets.has(bid)) status = 'generating';
    return { ...section, ...matrix[bid], ...row, block_id: bid, db_id: row?.id,
      title: section.title || row?.title || bid, content, tech, comp, issues,
      status, stale, bound, previous: tContent ?? cContent ?? '',
      hasSnapshot: tContent !== undefined || cContent !== undefined,
      materialRequests: proposal.material_requests?.[bid] || [] };
  });
}
// 目录树：把扁平 sections 按 level 折成 [{node, children}]。
// 与后端 domain/spec/outline_draft.normalize_nodes 同一套规则 —— 文档顺序是层级的
// 最终权威，level 只当提示：父节点必须是「前面出现过的、level 更小」的那个，
// 否则挂到最近一个 level 更小的前驱；一个都没有就落到顶层。
// 前端照抄规则而不是相信 level 的数字自洽，是因为模型输出可能给出
// [{level:1},{level:3}] 这类跳级序列，按 level 数字硬分组会造出空父层。
export function buildOutlineTree(sections = []) {
  const roots = [];
  const stack = []; // [{level, entry}]，level 严格递增
  for (const node of sections) {
    const level = Math.max(1, Math.min(4, Number(node?.level) || 1));
    while (stack.length && stack[stack.length - 1].level >= level) stack.pop();
    const entry = {node, children: []};
    if (!stack.length) roots.push(entry);
    else stack[stack.length - 1].entry.children.push(entry);
    stack.push({level, entry});
  }
  return roots;
}

export function summarize(chapters) {
  const counts = {};
  for (const b of chapters) counts[b.status] = (counts[b.status] || 0) + 1;
  return {counts, total: chapters.length, written: chapters.filter(b => b.content.trim()).length,
    issues: chapters.reduce((n, b) => n + (b.stale ? 0 : b.issues.length), 0),
    needsAction: chapters.filter(b => ['material','revise','stale','error','historical','unreviewed'].includes(b.status)).length};
}
export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Line-based diff with bounded memory for long bid documents.
export function diffHtml(before, after, leftTitle='上次评审正文', rightTitle='当前已保存正文') {
  const a=String(before).split('\n'), b=String(after).split('\n');
  let start=0,end=0;
  while(start<a.length && start<b.length && a[start]===b[start])start++;
  while(end<a.length-start && end<b.length-start && a[a.length-1-end]===b[b.length-1-end])end++;
  const side=(lines,tag)=>lines.map((line,i)=>i>=start && i<lines.length-end ? `<${tag}>${escapeHtml(line)||' '}</${tag}>` : escapeHtml(line)).join('\n');
  return `<div class="ws-diff"><section><h4>${escapeHtml(leftTitle)}</h4><pre>${side(a,'del')}</pre></section><section><h4>${escapeHtml(rightTitle)}</h4><pre>${side(b,'ins')}</pre></section></div>`;
}
