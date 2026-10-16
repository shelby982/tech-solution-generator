/**
 * 章节树：**一份**行标记与树渲染，三个模式共用。
 *
 * 为什么要有这个模块（2026-10-13 用户要"左栏归一"）：
 *   合并成一个三模式页面之后，左栏章节树仍是**两套实现** ——
 *   「要求与大纲 / 素材库」用 `mode-outline.js` 内联拼的 `.ot-row`，
 *   「方案编辑」用 `workbench.js` 内联拼的 `.doc-outline-item`。
 *   两边的**数据投影其实早就共用**（`workspace-model.mjs` 的 chapterBadges /
 *   filterChapters / buildOutlineTree），重复的只是行的标记与徽标拼装。
 *   历史教训就在眼前：`workspace-model.mjs` 的注释记着，两边各写一套徽标时
 *   漂移出过"每行显示 #undefined"。
 *
 * ⚠️ 徽标输出与改造前 `mode-outline.js:chapterBadgesHtml` **逐字保持一致**
 * （类名、title、顺序都不动）：下游有若干**就地**更新徽标的代码
 * （`syncOutlineRow` 等按 `.ot-row .block-status-badge` / `.ot-sub` 定位），
 * 换了标记就会静默失效。
 */
import {
  chapterStage,
  CHAPTER_STAGE_LABELS,
  escapeHtml,
  buildOutlineTree,
} from './workspace-model.mjs?v=20261016-v203';

/** 章节徽标：状态 + 依据 N + 覆盖 x/y。三个模式共用这一份拼装。 */
export function chapterBadgesHtml(block, { evidenceCount = 0, coverage = null, status = 'pending', label = '' } = {}) {
  const stage = status === 'extracting' ? 'extracting' : chapterStage(block, { evidenceCount });
  // label 可由调用方指定：**词表按模式不同**（要求与大纲看"提炼到没到"，
  // 方案编辑看"写没写"），但标记拼装只有一份。
  // 实测踩到：编辑器侧不传 label 时行内显示"待提炼"，而它的筛选 chips 是"待撰写"，
  // 同一屏两套词表。
  label = label || (status === 'extracting' ? '提炼中'
              : status === 'error' ? '提炼失败'
              : (CHAPTER_STAGE_LABELS[stage] || status));
  const tip = `依据 ${evidenceCount} 条 · 状态：${label}`;
  const covHtml = coverage && coverage.total
    ? `<span class="ot-cov ${coverage.covered === coverage.total ? 'full' : coverage.covered ? '' : 'zero'}"
             title="本章 ${coverage.total} 条应答要求，已有依据 ${coverage.covered} 条">${coverage.covered}/${coverage.total}</span>`
    : '';
  return `<span class="block-status-badge ${status} ot-badge" title="${escapeHtml(tip)}">${label}</span>`
       + (evidenceCount > 0 ? `<span class="ot-ev" title="${escapeHtml(tip)}">依据 ${evidenceCount}</span>` : '')
       + covHtml;
}

/**
 * 构造一个章节节点：`.ot-node`（带 data-level）+ `.ot-row`（摘要行）。
 *
 * 三个模式共用：要求与大纲 / 素材库（点行选中、点 › 折叠）、方案编辑（点行切换章节）。
 * 差异通过 opts 表达，不再各自拼字符串：
 * - `showEdit`：结构编辑按钮只在「要求与大纲」出现（撰写时不该改目录结构）。
 * - `showSub`：行内摘要（应标要求首行）在撰写模式里省掉，把宽度让给标题。
 * - `badgesHtml`：由调用方用 `chapterBadgesHtml` 生成（各自的数据缓存不同，
 *   但**拼装**只有一份）。
 */
export function chapterNode(block, {
  selected = false,
  sub = '',
  badgesHtml = '',
  showEdit = true,
  showSub = true,
  selectedClass = 'mw-selected',
  // 兼容层：编辑器侧的既有代码仍按 `.doc-outline-item[data-block-id-str|data-block-id]`
  // 定位行（mode-editor.js 有 10 处）。行上同时保留这些类与属性，把改动面压到最小；
  // 后续把那些选择器改成 .ot-row 之后，这几个参数就可以删掉。
  rowClasses = [],
  nodeClasses = [],
  rowSelectedClass = '',
  rowData = {},
  titleClass = '',
  appendHtml = '',
} = {}) {
  const level = String(Math.max(1, Math.min(4, Number(block?.level) || 1)));
  const node = document.createElement('div');
  node.className = ['ot-node', ...nodeClasses].join(' ');
  node.dataset.blockId = String(block?.block_id ?? '');
  node.dataset.level = level;

  const row = document.createElement('header');
  row.className = ['ot-row', ...rowClasses].join(' ');
  row.dataset.blockIdStr = String(block?.block_id ?? '');
  for (const [k, v] of Object.entries(rowData || {})) row.dataset[k] = String(v ?? '');
  row.innerHTML = `
    <span class="ot-chevron">›</span>
    <span class="ot-title${titleClass ? ` ${titleClass}` : ''}" title="${escapeHtml(block?.title || '')}">${escapeHtml(block?.title || '')}</span>
    ${showSub ? `<span class="ot-sub">${escapeHtml(sub || '')}</span>` : ''}
    ${badgesHtml}
    ${appendHtml}
    ${showEdit ? `<span class="ot-actions">
      <button class="ot-act ot-edit-toggle" title="编辑本节点（新增 / 改名 / 移动 / 删除）">编辑</button>
    </span>` : ''}`;

  node.appendChild(row);
  if (selected) {
    node.classList.add(selectedClass);
    if (rowSelectedClass) row.classList.add(rowSelectedClass);
  }
  return { node, row };
}

/**
 * 按 `buildOutlineTree` 的分组把扁平章节渲染成**嵌套**树。
 *
 * 为什么统一到嵌套：改造前编辑器侧是"扁平 + level-N 缩进"，大纲侧是嵌套。
 * 嵌套化之后，缩进、折叠、祖先可见性（`expandVisibleWithAncestors`）只有一套语义。
 *
 * @param host        容器
 * @param makeNode    (block) => HTMLElement  调用方决定每行怎么建（传 chapterNode 的绑定）
 * @param opts.afterNode (block, node) => void  调用方补自己的卡片（大纲模式的 8 字段卡）
 * @returns {Map<string, HTMLElement>} blockId → 节点元素
 */
export function renderChapterTree(host, chapters, {
  makeNode,
  afterNode = null,
  emptyHtml = '<p class="ws-muted" style="padding:12px">暂无符合条件的章节</p>',
} = {}) {
  const index = new Map();
  if (!host) return index;
  host.innerHTML = '';
  host.classList.add('outline-tree');

  const tree = buildOutlineTree(chapters || []);
  if (!tree.length) {
    host.innerHTML = emptyHtml;
    return index;
  }

  const walk = (entries, container) => {
    for (const { node: block, children } of entries) {
      const el = makeNode(block);
      container.appendChild(el);
      index.set(String(block?.block_id ?? ''), el);
      if (afterNode) afterNode(block, el);
      if (children.length) {
        const kids = document.createElement('div');
        kids.className = 'ot-children';
        el.appendChild(kids);
        walk(children, kids);
      }
    }
  };
  walk(tree, host);
  return index;
}

/** 就地切换选中行（不重渲染整棵树）。 */
export function setSelectedRow(index, blockId, selectedClass = 'mw-selected') {
  const target = String(blockId ?? '');
  for (const [id, node] of index) {
    node.classList.toggle(selectedClass, id === target);
  }
}
