// Pure projections shared by navigation, chapter tree and quality review.
// 材料角色体系（角色决定文件进哪条链路：要求类 vs 我方素材）——
// 从 material-roles.mjs 再导出，页面统一经本模块取，避免多处各写一套文案。
export {
  REQUIREMENT_ROLES, SOURCE_ROLES, ALL_MATERIAL_ROLES,
  ROLE_LABEL as materialRoleLabels, ROLE_HINT as materialRoleHints,
  isRequirementRole, inferRoleFromFilename, roleMismatchWarning,
} from './material-roles.mjs?v=20261016-v91';

export const statusLabels = {
  empty: '待撰写', material: '待补料', revise: '待修订', stale: '待复审',
  reviewing: '复审中', generating: '修订中', passed: '已通过',
  unreviewed: '待评审', historical: '历史评审待核对', error: '评审异常',
};
// 提炼响应矩阵第 8 字段 constraint_level 的三档（后端存英文，见
// domain/spec/models.py）。project-init 的提炼结果确认页与 workbench 的参考卡片共用
// 这份定义 —— 两处各写一份迟早漏掉一档（它决定正文字数档，写错会直接影响生成长度）。
export const CONSTRAINT_LEVELS = [
  { value: 'mandatory', label: '必须响应' },
  { value: 'recommended', label: '应当响应' },
  { value: 'optional', label: '可选响应' },
];
export function constraintLevelLabel(value) {
  const hit = CONSTRAINT_LEVELS.find(o => o.value === value);
  return hit ? hit.label : '应当响应';   // 缺省 / 未知值按 recommended 显示
}
// 逐节提炼的结果判定（project-init 的卡片计数与徽标都用它）。
// error_kind：'' 正常 | 'no_source' 无原文（正常，算完成）| 'failed' 需要重试（**不算**完成，
// 否则「继续提炼」永远不重试它，页面还会显示「提炼完成」而字段是空的）。
export function isExtractDone(block) {
  if ((block?.error_kind || '') === 'failed') return false;
  return block?.status === 'outline_done' || block?.status === 'done'
      || !!(block?.key_points || block?.requirement);
}

export function extractBadgeState(block) {
  const kind = block?.error_kind || '';
  if (kind === 'failed') return 'error';
  if (kind === 'no_source') return 'no_source';
  return isExtractDone(block) ? 'done' : 'pending';
}

export const stageLabels = {
  idle: '等待上传要求', parsing: '解析要求中',
  // points_review：第①步「只拆评分点」的停留态（评分点已拆出、目录还没生成）
  points_review: '评分点已拆出',
  outline_review: '待确认目录',
  // extracting：闸门 1 放行后的逐节提炼（后端运行期 stage，写在 workflow_runs）
  extracting: '提炼要点中',
  matching: '匹配素材中', materials_review: '待确认素材', generating: '撰写 / 修订中',
  reviewing: '评审中', report_review: '等待人工检查', done: '本次运行已结束',
  paused: '已暂停', aborted: '已作废', failed: '本次运行失败',
};
// 注意 extracting **不在这里**：这个集合回答的是「checkpoint 里停着 = 图真的还停在中途」，
// 而 extracting 只写 workflow_runs（extract 节点结束时图直接 END，checkpoint 永远停在
// outline_review）。把它塞进来会让「停在闸门 1」被误判成中断。
// 只在 workflow_runs 里出现的**运行期**阶段：checkpoint 要等节点结束才变，
// 所以 extract / match 期间 /state 读回的是闸门态 —— 判断"这一步到底在不在跑"、
// 以及给顶栏选词，都得看这两个值。
export const runtimeStages = new Set(['extracting', 'matching']);

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
    // 评审失败的原因：status='error' 时界面上必须**显示原因**，不能只显示"评审异常"
    // 却又在详情里写"暂无具体评审意见"（两者都"对"但用户看到的是自相矛盾）。
    const reviewError = String(tech?.error || comp?.error || '');
    return { ...section, ...matrix[bid], ...row, block_id: bid, db_id: row?.id,
      title: section.title || row?.title || bid, content, tech, comp, issues, reviewError,
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

// ─────────────────────────────────────────────
// 目录结构编辑（闸门 1：增删章节 / 改标题 / 上下移动 / 缩进）
// 纯函数，DOM 操作留在页面里 —— 便于 node --test 直接覆盖。
//
// 结构 = **有序数组** [{id, level, title}]，层级由顺序推导（与 buildOutlineTree
// 同一套规则），所以「上下移动」只是数组换位，不需要维护 parent 下标。
// 返回 null 表示该操作是空操作（UI 据此跳过请求，不要白发一次保存）。
// ─────────────────────────────────────────────

/** 生成不冲突的新节点 id。 */
export function newOutlineId(existingIds = []) {
  const used = new Set(existingIds);
  for (let attempt = 0; attempt < 100; attempt++) {
    const id = 'n' + Math.random().toString(36).slice(2, 8);
    if (!used.has(id)) return id;
  }
  let seq = used.size + 1;
  while (used.has('n' + seq)) seq++;
  return 'n' + seq;
}

/** 节点及其子树的下标区间 [start, end)；找不到返回 [-1, -1]。 */
export function outlineSubtreeRange(nodes, id) {
  const start = nodes.findIndex(n => n.id === id);
  if (start < 0) return [-1, -1];
  const level = Number(nodes[start].level) || 1;
  let end = start + 1;
  while (end < nodes.length && (Number(nodes[end].level) || 1) > level) end++;
  return [start, end];
}

function _shiftLevels(nodes, start, end, delta) {
  return nodes.map((n, i) => i >= start && i < end
    ? {...n, level: Math.max(1, Math.min(4, (Number(n.level) || 1) + delta))}
    : n);
}

/** 上/下移（带子树）。同一层级内与相邻同级节点交换；没有同级邻居时返回 null。 */
export function moveOutlineNode(nodes, id, dir) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start < 0) return null;
  const level = Number(nodes[start].level) || 1;
  const subtree = nodes.slice(start, end);

  if (dir < 0) {
    // 往前找同级前驱：跨过所有更深的后代，停在 level <= 本节点 的第一个节点
    let j = start - 1;
    while (j >= 0 && (Number(nodes[j].level) || 1) > level) j--;
    if (j < 0 || (Number(nodes[j].level) || 1) !== level) return null;   // 已是第一个同级
    return [...nodes.slice(0, j), ...subtree, ...nodes.slice(j, start), ...nodes.slice(end)];
  }

  let k = end;
  if (k >= nodes.length || (Number(nodes[k].level) || 1) !== level) return null;  // 已是最后一个同级
  const [ns, ne] = outlineSubtreeRange(nodes, nodes[k].id);
  return [...nodes.slice(0, start), ...nodes.slice(ns, ne), ...subtree, ...nodes.slice(ne)];
}

/** 降一级：成为「上一个同级章节」的子级（连带整棵子树一起降）。无前驱同级或已到 4 级 → null。 */
export function indentOutlineNode(nodes, id) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start <= 0) return null;
  const level = Number(nodes[start].level) || 1;
  if (level >= 4) return null;
  const prevLevel = Number(nodes[start - 1].level) || 1;
  if (prevLevel !== level) return null;   // 上一个节点不是同级（是上级或更深的后代）
  return _shiftLevels(nodes, start, end, +1);
}

/** 升一级（整棵子树一起升）。已是一级 → null。 */
export function outdentOutlineNode(nodes, id) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start < 0) return null;
  if ((Number(nodes[start].level) || 1) <= 1) return null;
  return _shiftLevels(nodes, start, end, -1);
}

/** 删除节点及其子树，返回 {nodes, removed}。 */
export function removeOutlineSubtree(nodes, id) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start < 0) return {nodes, removed: 0};
  return {nodes: [...nodes.slice(0, start), ...nodes.slice(end)], removed: end - start};
}

/** 在节点（含子树）之后插入同级章节。 */
export function insertSiblingAfter(nodes, id, node) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start < 0) return null;
  const level = Number(nodes[start].level) || 1;
  return [...nodes.slice(0, end), {...node, level}, ...nodes.slice(end)];
}

/** 作为最后一个子章节插到节点子树末尾。父节点已在 4 级 → null。 */
export function insertChildOf(nodes, id, node) {
  const [start, end] = outlineSubtreeRange(nodes, id);
  if (start < 0) return null;
  const level = Number(nodes[start].level) || 1;
  if (level >= 4) return null;
  return [...nodes.slice(0, end), {...node, level: level + 1}, ...nodes.slice(end)];
}

// 提炼要求「改过但没应用到目录」的判定：两边都按 trim 后比较。
// 提出成纯函数是为了能测 —— 这条判断决定了「输入框里的要求到底生效没有」的提示。
export function instructionDirty(current, applied) {
  return String(current ?? '').trim() !== String(applied ?? '').trim();
}

// ── 素材依据的双向视图（匹配阶段 §4.D）──────────────────────────
// blocks.source 是匹配节点写的依据数组（见 infra/retrieval/assign.py 的
// MaterialEvidence.to_dict）：{chunk_id, content, filename, locator, page, kind,
// source_kind, by, confidence, item_ids}。前端要能从两个方向看同一份数据：
//   素材 → 供哪些章 / 哪些条目；  章节条目 → 有哪些依据。
// 这些投影是纯函数（不碰 DOM），方便单测；页面只负责渲染。
export const EVIDENCE_KIND_LABELS = { fact: '事实', solution: '方案', background: '背景' };
export const EVIDENCE_SOURCE_LABELS = { ours: '我方素材', tender: '招标文件' };
export const EVIDENCE_BY_LABELS = {
  rule: '规则命中', semantic: '语义命中', model: 'AI 归属', human: '人工挂接',
};

/** blocks.source（可能是 JSON 字符串，老数据可能缺失）→ 依据数组。 */
export function parseEvidence(block) {
  const raw = block?.source;
  if (!raw) return [];
  let arr = raw;
  if (typeof raw === 'string') {
    try { arr = JSON.parse(raw); } catch { return []; }
  }
  if (!Array.isArray(arr)) return [];
  return arr.filter(e => e && typeof e === 'object' && (e.chunk_id != null || e.content));
}

/** 切片首行 = 切片标题（match 节点用 build_locator 取它当锚点）。 */
export function evidenceSliceTitle(content) {
  return String(content ?? '').split('\n').map(l => l.trim()).filter(Boolean)[0] || '';
}

/** 正文摘要：去掉首行标题后压平，超长截断（卡片里只给一眼能看懂的量）。 */
export function evidenceSnippet(content, max = 140) {
  const lines = String(content ?? '').split('\n').map(l => l.trim()).filter(Boolean);
  const body = (lines.length > 1 ? lines.slice(1).join(' ') : '') || lines[0] || '';
  return body.length > max ? `${body.slice(0, max)}…` : body;
}

/** 章节 → 依据统计：{ blockId: {total, items:{itemId:count}, files:[…]} }。 */
export function evidenceByChapter(blocks = []) {
  const out = {};
  for (const b of blocks || []) {
    const id = String(b?.block_id ?? b?.id ?? '');
    if (!id) continue;
    const evs = parseEvidence(b);
    const items = {};
    const files = new Set();
    for (const e of evs) {
      for (const iid of e.item_ids || []) items[iid] = (items[iid] || 0) + 1;
      if (e.filename) files.add(e.filename);
    }
    out[id] = { total: evs.length, items, files: [...files], evidence: evs };
  }
  return out;
}

/**
 * 素材 → 供了哪些章、几条依据。用于左侧素材列表的「供 N 章 M 条」。
 *
 * 两个索引：`byId`（material_id）与 `byFile`（文件名）。
 * **优先用 byId**：同一份文件被传两次时文件名相同，只看文件名会把两份素材的依据
 * 合并到一行上（实测：同一 PDF 上传 3 次，每行都显示全部 33 条）。
 * byFile 只作为老数据（没有 material_id）的兜底。
 */
export function evidenceBySource(blocks = []) {
  const byId = {};
  const byFile = {};
  const bump = (out, key) => out[key] || (out[key] = { chapters: [], items: new Set(), count: 0 });
  for (const b of blocks || []) {
    const id = String(b?.block_id ?? b?.id ?? '');
    const title = b?.title || '';
    for (const e of parseEvidence(b)) {
      const recs = [];
      if (e.material_id != null) recs.push(bump(byId, String(e.material_id)));
      if (e.filename) recs.push(bump(byFile, e.filename));
      for (const rec of recs) {
        rec.count += 1;
        for (const iid of e.item_ids || []) rec.items.add(iid);
        if (id && !rec.chapters.some(c => c.id === id)) rec.chapters.push({ id, title });
      }
    }
  }
  for (const out of [byId, byFile]) {
    for (const rec of Object.values(out)) rec.itemCount = rec.items.size;
  }
  return { byId, byFile };
}

/** 兼容入口：只要按文件名索引的那一份。 */
export function evidenceByFile(blocks = []) {
  return evidenceBySource(blocks).byFile;
}

/** 某份素材的用量记录：material_id 优先，文件名兜底。 */
export function usageFor(index, material) {
  return index?.byId?.[String(material?.id)] || index?.byFile?.[material?.filename] || null;
}

/**
 * 章节内按**应答要求条目**分组依据：条目 → [依据…]。
 * 没挂到任何条目的依据归入 `chapterLevel`（只有章节级归属，如整体方案/公司介绍）。
 * 返回的 items 顺序 = 传入条目顺序（后端展开顺序稳定）。
 */
export function groupEvidenceByItem(block, items = []) {
  const evs = parseEvidence(block);
  const known = new Map((items || []).map(it => [it.item_id, it]));
  const grouped = new Map((items || []).map(it => [it.item_id, []]));
  const chapterLevel = [];
  for (const e of evs) {
    const ids = (e.item_ids || []).filter(iid => grouped.has(iid));
    if (!ids.length) { chapterLevel.push(e); continue; }
    for (const iid of ids) grouped.get(iid).push(e);
  }
  return {
    items: (items || []).map(it => ({ item: it, evidence: grouped.get(it.item_id) || [] })),
    chapterLevel,
    total: evs.length,
  };
}

// ── 依据的 HTML 渲染（纯字符串，不碰 DOM）──────────────────────
// 放在模型层而不是页面里：这两块 HTML 决定"用户看到的依据是什么"，是最该被断言的部分
// （出处有没有、性质徽标对不对、无依据怎么说），页面只做插入。见 tests/frontend/evidence-view.test.mjs。
/**
 * 依据的**性质徽标**（事实/方案/背景）。抽出来是因为"这条依据是什么性质"以前在两个
 * 页面各写一份，一升级契约就漂移（工作台曾经整列显示 `#undefined`）。
 */
export function evidenceKindBadgeHtml(e) {
  const known = EVIDENCE_KIND_LABELS[e?.kind];
  return `<span class="be-kind ${known ? e.kind : 'fact'}">${known || EVIDENCE_KIND_LABELS.fact}</span>`;
}

/**
 * 依据的**出处行**：文件名 · locator（P12 · 第 8 段 · 切片标题）· 来源 · 归属方式。
 * 出处是"这条依据能不能被追溯"的全部信息，缺了它评审就没法核对。
 */
export function evidenceSourceLineHtml(e) {
  const srcKind = EVIDENCE_SOURCE_LABELS[e?.source_kind] || EVIDENCE_SOURCE_LABELS.ours;
  const by = EVIDENCE_BY_LABELS[e?.by] || '';
  const src = [e?.filename ? `<span class="be-file">${escapeHtml(e.filename)}</span>` : '',
               e?.locator ? escapeHtml(e.locator) : '',
               srcKind, by].filter(Boolean).join(' · ');
  return `<span class="be-src">${src}</span>`;
}

/**
 * 单条依据：性质徽标 + 文件名 + 出处（P12 · 第 8 段 · 切片标题）+ 原文摘要。
 * ``actions``：右侧动作按钮的 HTML（人工解绑/改挂载用）—— 由调用方给，
 * 因为"能对这个章节做什么"取决于页面（素材库能解绑，只读页不能）。
 */
export function evidenceRowHtml(e, { actions = '' } = {}) {
  const full = String(e.content || '');
  return `<div class="be-ev" title="${escapeHtml(full)}">
    ${evidenceKindBadgeHtml(e)}
    ${evidenceSourceLineHtml(e)}
    ${evidenceCautionHtml(e)}
    ${actions ? `<span class="be-acts">${actions}</span>` : ''}
    <span class="be-snip">${escapeHtml(evidenceSnippet(full))}</span>
  </div>`;
}

/** 条目短码：'s2.3.1#R1' → 'R1'（卡片里不重复整章 id）。 */
export function itemCode(item) {
  return String(item?.item_id ?? '').split('#').pop();
}

/**
 * 卡片内的「依据」区：按**应答要求条目**分组。
 * 无依据的条目只报数（信息性，不阻断撰写）；没挂到条目的依据进「整章」组；
 * 完全没依据 → 返回 ''（卡片里不出现空区块）。
 */
export function renderEvidenceSection(block, items = [], { rowActions = null, itemActions = null, open = false, foldUncovered = false } = {}) {
  const evs = parseEvidence(block);
  // 完全没有依据时：只读展示返回空（卡片里不该出现空区块），但**给了 itemActions
  // 就还要列条目** —— 匹配工作区里"这一章一条依据都没有"正是最需要逐条挂素材的时候。
  if (!evs.length && !itemActions) return '';
  // 评分口径/量化档位（informative）不参与"要回应的事项"：它们不可能有素材依据，
  // 混在一起就是满屏"无依据"（实测用户看不懂的正是这个）。单独归到说明性一组。
  const actionableItems = (items || []).filter(it => !it?.informative);
  const informativeItems = (items || []).filter(it => it?.informative);
  const g = groupEvidenceByItem(block, actionableItems);
  const covered = g.items.filter(x => x.evidence.length).length;
  const groups = [];

  if (g.chapterLevel.length) {
    groups.push(`<div class="be-group">
      <div class="be-group-head">
        <span class="be-code">整章</span>
        <span class="be-group-text">不针对具体条目的整章素材</span>
        <span class="be-count">${g.chapterLevel.length} 条</span>
      </div>
      ${g.chapterLevel.map(e => evidenceRowHtml(e, {
        actions: rowActions ? rowActions(e, null) : '',
      })).join('')}
    </div>`);
  }
  const uncoveredGroups = [];
  for (const { item, evidence } of g.items) {
    // 无依据的条目也要出现（并给「找素材」入口）—— 它们才是补素材时的待办；
    // 以前这里 `if (!evidence.length) continue`，用户只能看到"有几条没依据"的数字。
    if (!evidence.length && !itemActions) continue;
    const html = (`<div class="be-group${evidence.length ? '' : ' be-group-empty'}">
      <div class="be-group-head">
        <span class="be-code">${escapeHtml(itemCode(item))}</span>
        <span class="be-group-text">${escapeHtml(item.text)}</span>
        <span class="be-count">${evidence.length ? `${evidence.length} 条依据` : '无依据'}</span>
        ${itemActions ? `<span class="be-item-acts">${itemActions(item)}</span>` : ''}
      </div>
      ${evidence.map(e => evidenceRowHtml(e, {
        actions: rowActions ? rowActions(e, item) : '',
      })).join('')}
    </div>`);
    if (evidence.length) groups.push(html); else uncoveredGroups.push(html);
  }
  // 未覆盖的"要回应事项"默认收成一行：28 条里 25 条无依据时，逐条铺开只会淹没
  // 真正有依据的那几条（用户实测反馈"这部分没看懂"）。折叠后一句话说清：不阻断撰写。
  if (uncoveredGroups.length) {
    if (foldUncovered) {
      groups.push(`<div class="be-uncovered">
        <button class="be-uncovered-head" type="button">
          其余 <b>${uncoveredGroups.length}</b> 条要回应的事项暂无素材依据
          <span class="be-sum">不阻断撰写，撰写时按招标要求直接作答</span>
          <span class="be-caret">›</span>
        </button>
        <div class="be-uncovered-body" hidden>${uncoveredGroups.join('')}</div>
      </div>`);
    } else {
      groups.push(...uncoveredGroups);
    }
  }

  if (informativeItems.length) {
    groups.push(`<div class="be-informative">
      <div class="be-informative-head">
        评分与量化指标 <b>${informativeItems.length}</b> 条
        <span class="be-sum">说明性：评委会按这些口径打分，不需要逐条找素材</span>
      </div>
      <div class="be-informative-body">
        ${informativeItems.map(it => `<div class="be-informative-row">
          <span class="be-code">${escapeHtml(itemCode(it))}</span>
          <span class="be-group-text">${escapeHtml(it.text)}</span>
        </div>`).join('')}
      </div>
    </div>`);
  }

  const noEvidence = itemActions ? [] : g.items.filter(x => !x.evidence.length);
  // 无依据条目只列短码（正文在卡片上方的 8 字段里），鼠标悬停给出条目原文 —— 只看
  // "R1 · K1"用户对不回是哪条要求。
  const noneHint = noEvidence
    .map(x => `${itemCode(x.item)}：${x.item.text}`).join('\n');
  const noneHtml = noEvidence.length
    ? `<div class="be-none" title="${escapeHtml(noneHint)}">无依据条目 <b>${noEvidence.length}</b> / ${g.items.length}：
         ${escapeHtml(noEvidence.map(x => itemCode(x.item)).join(' · '))}
         —— 无匹配素材不阻断，撰写阶段直接撰写</div>`
    : '';
  // 条目还没拿到（未提炼 / 接口未返回）时退化成章节级列表，不能把依据藏起来
  const flat = (!g.items.length && g.chapterLevel.length)
    ? `<div class="be-none">该章未展开应答条目，以下为章节级依据</div>` : '';

  // 覆盖度分母 = 要回应的事项（不含评分口径）：否则"优良 7-10 分"这类条目会把
  // 分母撑大，覆盖率永远难看，用户也读不懂这个分数在说什么。
  const sum = [
    actionableItems.length ? `覆盖 ${covered} / ${actionableItems.length} 条要回应事项` : '',
    g.chapterLevel.length ? `整章 ${g.chapterLevel.length} 条` : '',
  ].filter(Boolean).join(' · ');

  return `<div class="block-evidence${open ? ' open' : ''}">
    <button class="be-head" type="button">
      素材依据 <b>${g.total}</b> 条 <span class="be-sum">${escapeHtml(sum)}</span>
      <span class="be-caret">›</span>
    </button>
    <div class="be-body">${noneHtml}${flat}${groups.join('')}</div>
  </div>`;
}

// ── 「素材匹配」按钮该走哪条路 ──────────────────────────────
// 抽成纯函数是因为它踩过一个真实的坑：判据用了 checkpoint 的 stage，而 extract 是
// 那条分支的最后一个节点、跑完图直接 END —— **checkpoint 只会停在闸门 1 留下的
// 'outline_review'**，于是"提炼已完成"和"闸门 1 还没放行"在 /state 里长得一模一样
// （真实数据：workflow_runs='done' + 33/33 节 outline_done + stage='outline_review'）。
// 结果点「素材匹配」被判成"还没提炼"，页面被拽回「要求与大纲」并让人去确认目录，
// 而那边其实无事可做 —— 用户卡死。真实判据是**已提炼章节数**。
export const MATCH_ENTRY = {
  WAIT: 'wait',                  // 长阶段（提炼/匹配）正在跑：等它跑完
  GATE_OUTLINE: 'gate_outline',  // 闸门 1 真的没过（一节都没提炼）：回要求与大纲
  START: 'start',                // 可以匹配（rerun-match）
  REVIEW: 'review',              // 已进入生成/评审阶段：去质量检查页
  BLOCKED: 'blocked',            // 其它阶段（如正在 parse / 派生目录）
};

/**
 * @param {{stage?:string, totalBlocks?:number, doneBlocks?:number,
 *          running?:('extracting'|'matching'|null)}} input
 *   running = workflow_runs 里的运行期阶段（长阶段在不在跑），由 longPhaseRunning() 取。
 */
export function matchEntryDecision({ stage, totalBlocks = 0, doneBlocks = 0, running = null } = {}) {
  if (running === 'extracting' || running === 'matching') return MATCH_ENTRY.WAIT;
  const total = Number(totalBlocks) || 0;
  const extracted = Number(doneBlocks) || 0;
  const hasExtract = total > 0 && extracted > 0;
  if (stage === 'outline_review') return hasExtract ? MATCH_ENTRY.START : MATCH_ENTRY.GATE_OUTLINE;
  if (stage === 'materials_review') return MATCH_ENTRY.START;
  if (stage === 'report_review' || stage === 'done' || stage === 'paused') return MATCH_ENTRY.REVIEW;
  return MATCH_ENTRY.BLOCKED;
}

// ── 章节状态投影（共享：素材库 / 方案编辑 / 覆盖矩阵都用这一份）──────
// 为什么要有：三个模块都在渲染"章节"，但各自算各的进度 —— 同一章在素材库显示
// "已提炼"、在方案编辑显示"待撰写"，用户不知道信哪个。状态判定必须只有一份。
//
// 状态顺序（单向推进，后面的蕴含前面的）：
//   no_source 无原文（本来就没东西可提炼，不是失败）
//   pending   待提炼
//   extracted 已提炼（8 字段有了）
//   matched   已匹配（本章有 n 条依据；没有依据是正常态，不等于没匹配过）
//   written   已撰写（有正文）
export const CHAPTER_STAGES = ['no_source', 'pending', 'extracted', 'matched', 'written'];

export const CHAPTER_STAGE_LABELS = {
  no_source: '无需提炼', pending: '待提炼', extracted: '已提炼',
  matched: '已匹配', written: '已撰写',
};

/** 只用到 8 字段与 error_kind 的那部分（与 isExtractDone 同一判据）。 */
export function chapterStage(block, { evidenceCount = 0 } = {}) {
  if (String(block?.content ?? '').trim()) return 'written';
  if ((block?.error_kind || '') === 'failed') return 'pending';   // 失败要留在"待提炼"里可重试
  if (!isExtractDone(block)) {
    return (block?.error_kind || '') === 'no_source' ? 'no_source' : 'pending';
  }
  return evidenceCount > 0 ? 'matched' : 'extracted';
}

/** 行徽标：状态 + 「依据 N」（有依据才显示数字，0 条不显示噪声）。 */
export function chapterBadges(block, { evidenceCount = 0 } = {}) {
  const stage = chapterStage(block, { evidenceCount });
  const badges = [{ kind: stage, text: CHAPTER_STAGE_LABELS[stage] }];
  if (evidenceCount > 0) badges.push({ kind: 'evidence', text: `依据 ${evidenceCount}` });
  return badges;
}

/**
 * 章节筛选（素材库/方案编辑左栏的筛选 chips）。
 * ``mode``：all | without_evidence | unwritten | pending
 *   without_evidence —— 已提炼但一条依据都没有（"要补素材的是这些章"）
 *   unwritten        —— 还没写正文
 *   pending          —— 还没提炼（含失败；`no_source` 是终态、不是待办，不算）
 */
export function filterChapters(blocks = [], { mode = 'all', evidenceByBlock = {} } = {}) {
  const evCount = (b) => {
    const id = String(b?.block_id ?? b?.id ?? '');
    const v = evidenceByBlock[id];
    return Array.isArray(v) ? v.length : (Number(v) || 0);
  };
  return (blocks || []).filter(b => {
    const stage = chapterStage(b, { evidenceCount: evCount(b) });
    if (mode === 'all') return true;
    if (mode === 'without_evidence') return stage === 'extracted';
    if (mode === 'unwritten') return stage !== 'written';
    if (mode === 'pending') return stage === 'pending';
    return true;
  });
}

/** 筛选 chips 的计数（"无依据 4"这类数字要有，否则用户不知道点了会剩几章）。 */
export function chapterFilterCounts(blocks = [], evidenceByBlock = {}) {
  const counts = { all: (blocks || []).length, without_evidence: 0, unwritten: 0, pending: 0 };
  for (const b of blocks || []) {
    const id = String(b?.block_id ?? b?.id ?? '');
    const v = evidenceByBlock[id];
    const n = Array.isArray(v) ? v.length : (Number(v) || 0);
    const stage = chapterStage(b, { evidenceCount: n });
    if (stage === 'extracted') counts.without_evidence += 1;
    if (stage !== 'written') counts.unwritten += 1;
    if (stage === 'pending') counts.pending += 1;
  }
  return counts;
}

/**
 * 树筛选：把命中节点的**祖先链**一起放进可见集合。
 *
 * 坑（实测）：树是嵌套的（子节点在父节点的 `.ot-children` 里），只按命中集合隐藏
 * 其它节点的话，父节点一隐藏，命中的子节点也跟着消失 —— "无依据 6"筛选点下去
 * 整个中间栏是空的（因为命中的 s1.1/s2.1 全在 s1/s2 底下）。
 *
 * ``nodes``：按树序排列的扁平列表，每项含 ``id`` 与 ``level``（与 buildOutlineTree
 * / 后端 normalize_nodes 同一套约定：层级由顺序 + level 推导，没有 parent 字段）。
 */
export function expandVisibleWithAncestors(nodes = [], visibleIds = []) {
  const out = new Set([...visibleIds].map(String));
  const stack = [];                       // 当前路径上的祖先（level 递增）
  for (const n of nodes || []) {
    const id = String(n?.id ?? n?.block_id ?? '');
    if (!id) continue;
    const level = Number(n?.level) || 1;
    while (stack.length && stack[stack.length - 1].level >= level) stack.pop();
    if (out.has(id)) for (const a of stack) out.add(a.id);
    stack.push({ id, level });
  }
  return out;
}

// ── 依据可信度标注（让"机器猜的"一眼可见）──────────────────────
// 用户看不懂匹配结果的一大原因是：界面把"规则/语义命中的弱关联"和"确定可用的依据"
// 长得一模一样。实测（项目 18 的 s1.1）：招标文件里的名词定义「2、投标方：指响应
// 招标、参加投标竞争的法人。」被语义命中挂到了 R3（优良 7-10 分）和 K1（编制偏离表）
// —— 只因为共享"投标方"。这条 confidence 只有 0.305，必须标出来。
export const WEAK_EVIDENCE_CONFIDENCE = 0.5;

/** 弱关联：语义命中且置信度偏低 → 界面上提示"建议核对"，不要当真依据用。 */
export function isWeakEvidence(e) {
  if (!e) return false;
  if (e.by !== 'semantic') return false;
  const c = Number(e.confidence);
  return !Number.isFinite(c) || c < WEAK_EVIDENCE_CONFIDENCE;
}

// 文件名里明写"招标/采购/规范/需求书/评分"的文件，多半是**招标方给的文件**，
// 不是我方素材。用户把它传到「素材」窗口时 role='source'，系统就会按"我方素材"处理，
// 于是招标原文里的定义、条款会被当成我方依据（实测踩到）。这里只做**提示**，
// 不擅自改行为（改 role 是用户的事，改判定是产品决策）。
const TENDER_FILE_HINT_RE = /招标|采购|询价|竞争性|谈判|规范书|需求书|评分表|评审要素|评标/;
export function looksLikeTenderFile(filename) {
  return TENDER_FILE_HINT_RE.test(String(filename || ''));
}

/** 依据行的可信度徽标：弱关联 / 疑似招标文件（都是提示，不拦截）。 */
export function evidenceCautionHtml(e) {
  const marks = [];
  if (isWeakEvidence(e)) {
    marks.push(`<span class="be-caution weak" title="语义命中但相似度低（${Math.round((Number(e.confidence) || 0) * 100)}%）—— 建议核对后再引用">弱关联·建议核对</span>`);
  }
  if (looksLikeTenderFile(e?.filename) && e?.source_kind === 'ours') {
    marks.push(`<span class="be-caution tender" title="文件名像是招标方给的文件，却被当作「我方素材」上传 —— 它不适合作为我方技术依据">疑似招标文件</span>`);
  }
  return marks.join('');
}
