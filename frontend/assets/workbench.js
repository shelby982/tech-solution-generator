import { api } from './api.js';

const params    = new URLSearchParams(location.search);
const projectId = params.get('projectId');
const threadId  = params.get('threadId');
if (!projectId) { location.href = '/projects'; }

// threadId 模式下用 spec.toc 主导章节列表（闸门 1 完成即就绪），
// proposal.blocks 仅注入"已生成内容"（闸门 3 之后才完整）。
// outline_matrix 提供 8 字段（requirement/key_points/...）。
function buildBlockFromTocSection(section, blocks, matrix) {
  const blockId  = section.id;
  const output   = blocks[blockId] || {};
  const row      = matrix[blockId] || {};
  const rawLevel = Number(section.level) || 1;
  const level    = Math.max(1, Math.min(4, rawLevel));
  const isHeading = level === 1;
  const sources  = Array.isArray(output.sources) ? output.sources : [];
  return {
    id: String(blockId).replace(/\./g, '_'),  // DOM-safe id
    block_id: blockId,                          // 业务 id（含点的 s1.1 等）
    title: section.title || row.title || blockId,
    kind: isHeading ? 'heading' : 'content',
    level,
    content: output.content || '',
    outline: output.outline || '',
    source: JSON.stringify(sources),
    requirement:       row.requirement       || '',
    key_points:        row.key_points        || '',
    veto_items:        row.veto_items        || '',
    bonus_items:       row.bonus_items       || '',
    score_items:       row.score_items       || '',
    evidence_required: row.evidence_required || '',
    constraint_level:  row.constraint_level  || 'recommended',
    indicators:        row.indicators        || '',
    domain: '',
    parent_title: '',
    score: '',
  };
}

async function loadBlocksForOutline(pid, tid) {
  if (tid) {
    try {
      const state  = await api.workflow.state(tid);
      const blocks = (state && state.proposal && state.proposal.blocks) || {};
      const matrix = (state && state.spec && state.spec.outline_matrix) || {};
      const toc    = Array.isArray(state && state.spec && state.spec.toc) ? state.spec.toc : [];
      if (toc.length > 0) {
        return toc.map(sec => buildBlockFromTocSection(sec, blocks, matrix));
      }
      // toc 空 — workflow 未推进过闸门 1，回退老路径
    } catch (e) {
      console.warn('[workbench] workflow state failed, falling back', e);
    }
  }
  return await api.blocks.list(pid);
}

// 顶部 tab 链接注入 projectId
document.querySelectorAll('.process-tab').forEach(a => {
  const url = new URL(a.href, location.origin);
  url.searchParams.set('projectId', projectId);
  a.href = url.pathname + '?' + url.searchParams.toString();
});
// 加载项目名称
let projectName = '项目画廊';
api.projects.get(projectId).then(p => {
  if (p?.name) {
    projectName = p.name;
    document.title = `AiBidding · ${p.name}`;
    const el = document.getElementById('header-project-name');
    if (el) el.textContent = p.name;
  }
});

async function loadBlocks() {
  const blockList = await loadBlocksForOutline(projectId, threadId);
  const outlineNav = document.querySelector('.doc-outline');
  if (!outlineNav) return;
  outlineNav.innerHTML = '';

  // 与 workbench.html 内联模块约定：__extractBlockMap 缓存 block 数据，
  // outline-item 点击调用 __onBlockSelected 让中间编辑器显示该 block。
  if (!window.__extractBlockMap) window.__extractBlockMap = new Map();
  const blockMap = window.__extractBlockMap;
  // 暴露完整列表给 renderPageHint 等使用
  window.__allBlocks = blockList;

  // 规整化 level：找到全局最小值，平移成 1（兼容文档没有 level=1 标题的情况）
  const rawLevels = blockList.map(b => Number(b.level) || 1);
  const minLevel = rawLevels.length ? Math.min(...rawLevels) : 1;
  const normalize = (raw) => Math.max(1, Math.min(4, raw - minLevel + 1));

  let currentChildren = null;
  let firstContentItem = null;

  blockList.forEach((b, idx) => {
    const blockIdStr = b.block_id || b.id;
    const title = b.title || '（无标题）';
    const level = normalize(rawLevels[idx]);
    blockMap.set(blockIdStr, b);

    if (level === 1) {
      const group = document.createElement('div');
      group.className = 'outline-group';
      const headingBtn = document.createElement('button');
      headingBtn.type = 'button';
      headingBtn.className = 'doc-outline-heading';
      headingBtn.innerHTML = `<span class="outline-chevron">›</span><span class="outline-item-label"></span>`;
      headingBtn.querySelector('.outline-item-label').textContent = title;
      headingBtn.addEventListener('click', () => group.classList.toggle('collapsed'));
      group.appendChild(headingBtn);
      currentChildren = document.createElement('div');
      currentChildren.className = 'outline-children';
      group.appendChild(currentChildren);
      outlineNav.appendChild(group);
    } else {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `doc-outline-item level-${level}`;
      item.dataset.blockIdStr = blockIdStr;
      item.dataset.blockId = b.id;
      item.dataset.level = String(level);
      item.innerHTML = `<span class="outline-item-bullet"></span><span class="outline-item-label"></span><span class="outline-item-dot${b.content ? ' done' : ''}"></span>`;
      item.querySelector('.outline-item-label').textContent = title;
      item.addEventListener('click', () => {
        outlineNav.querySelectorAll('.doc-outline-item').forEach(el => el.classList.remove('active'));
        item.classList.add('active');
        const block = blockMap.get(blockIdStr) || b;
        if (typeof window.__onBlockSelected === 'function') window.__onBlockSelected(block);
      });
      (currentChildren || outlineNav).appendChild(item);
      if (!firstContentItem) firstContentItem = item;
    }
  });

  // 初始化撰写进度展示
  const contentBlocks = blockList.filter((b, i) => normalize(rawLevels[i]) >= 2);
  const writtenCount = contentBlocks.filter(b => b.content && b.content.trim()).length;
  const progressBadge = document.getElementById('outline-progress-badge');
  const progressText  = document.getElementById('outline-progress-text');
  const progressBar   = document.getElementById('outline-progress-bar');
  if (progressBadge) progressBadge.textContent = String(contentBlocks.length);
  if (progressText)  progressText.textContent  = `${writtenCount} / ${contentBlocks.length}`;
  if (progressBar)   progressBar.style.width   = contentBlocks.length ? `${Math.round(writtenCount / contentBlocks.length * 100)}%` : '0%';

  // 自动选中首个内容章节
  if (firstContentItem) firstContentItem.click();

  // 批量生成事件监听 — block_id 含点的子节（如 1.1）DOM 用 data-block-id-str 索引
  const findOutlineItem = (bid) => {
    if (!bid) return null;
    return outlineNav.querySelector(`.doc-outline-item[data-block-id-str="${CSS.escape(String(bid))}"]`);
  };
  window.addEventListener('block:start', e => {
    const item = findOutlineItem(e.detail.block_id);
    if (!item) return;
    const dot = item.querySelector('.outline-item-dot');
    if (dot) { dot.classList.remove('done'); dot.classList.add('writing'); }
  });

  window.addEventListener('block:done', e => {
    const item = findOutlineItem(e.detail.block_id);
    if (!item) return;
    const dot = item.querySelector('.outline-item-dot');
    if (dot) { dot.classList.remove('writing'); dot.classList.add('done'); }
    // 同步缓存内容，便于章节切换时立即看到
    const cached = blockMap.get(e.detail.block_id);
    if (cached && e.detail.content) {
      cached.content = e.detail.content;
      blockMap.set(e.detail.block_id, cached);
    }
  });

  window.addEventListener('block:error', e => {
    const item = findOutlineItem(e.detail.block_id);
    if (!item) return;
    item.classList.add('block-error');
    const dot = item.querySelector('.outline-item-dot');
    if (dot) dot.classList.remove('writing');
  });
}

window.__loadBlocks = loadBlocks;
loadBlocks();
window.addEventListener('beforeunload', () => {
  document.querySelectorAll('[data-sse]').forEach(el => el._sse?.close());
});

// ── 原 workbench.js 核心逻辑（去掉 IIFE 外层包装） ──

function initWorkbench() {
  const root = document.querySelector('[data-workbench]');
  if (!root) return;

  const outlineItems = [...root.querySelectorAll('.doc-outline-item')];
  const blocks = [...root.querySelectorAll('.editor-block')];
  const currentBlockId = root.querySelector('[data-current-block-id]');
  const editorScroller = root.querySelector('.tiptap-shell');
  const breadcrumb = root.querySelector('[data-breadcrumb]');
  const exitDrill = root.querySelector('[data-exit-drill]');
  const contextDomain = root.querySelector('[data-context-domain]');
  const contextScope = root.querySelector('[data-context-scope]');
  const contextTitle = root.querySelector('[data-context-title]');
  const contextExpression = root.querySelector('[data-context-expression]');
  const contextRequirement = root.querySelector('[data-context-requirement]');
  const contextScore = root.querySelector('[data-context-score]');
  const contextSource = root.querySelector('[data-context-source]');
  const contextPotential = root.querySelector('[data-context-potential]');
  const historyList = root.querySelector('[data-block-history]');
  const historyPreview = root.querySelector('[data-history-preview]');
  const historyPreviewText = root.querySelector('[data-history-preview-text]');
  const suggestion = root.querySelector('[data-ai-suggestion]');
  const suggestionText = root.querySelector('[data-ai-suggestion-text]');
  let drilledHeading = null;

  function historyEntries(block) {
    const fallbackTimes = ['今天 12:41', '今天 10:18', '昨天 18:32', '05-12 21:09'];
    return (block.dataset.history || '')
      .split('|')
      .filter(Boolean)
      .map((entry, index) => {
        const [version, ...rest] = entry.trim().split(' ');
        const label = rest.join(' ');
        const time = /(?:\d{1,2}:\d{2}|\d{2}-\d{2})/.test(label) ? label : fallbackTimes[index] || '05-12 09:00';
        return { version, time };
      });
  }

  function closestHeading(block) {
    if (block.dataset.blockKind === 'heading') return block;
    const parentTitle = block.dataset.parentTitle;
    return blocks.find((item) => item.dataset.blockKind === 'heading' && item.dataset.title === parentTitle) || block;
  }

  function scopeLabels(heading) {
    const ids = (heading.dataset.scope || heading.id).split(/\s+/).filter(Boolean);
    return ids.map((id) => root.querySelector(`#${id}`)?.dataset.title).filter(Boolean).join('、');
  }

  function headingForTitle(title) {
    return blocks.find((item) => item.dataset.blockKind === 'heading' && item.dataset.title === title);
  }

  function breadcrumbParts(block) {
    const heading = closestHeading(block);
    return [{ label: heading.dataset.title || block.dataset.title, block: heading }];
  }

  function updateBreadcrumb(block) {
    breadcrumb.innerHTML = breadcrumbParts(block).map((part, index, parts) => {
      const separator = index === parts.length - 1 ? '' : '<span class="breadcrumb-separator">/</span>';
      if (!part.block) return `<button type="button" data-breadcrumb-action="root">${part.label}</button>${separator}`;
      return `<button type="button" data-breadcrumb-target="${part.block.id}">${part.label}</button>${separator}`;
    }).join('');
  }

  function renderHistory(block) {
    historyList.innerHTML = historyEntries(block).map((entry, index) => `
      <button class="history-item${index === 0 ? ' active' : ''}" type="button" data-version="${entry.version}" data-time="${entry.time}">
        <span class="history-version">${entry.version}</span>
        <span class="history-time">${entry.time}</span>
      </button>
    `).join('');
  }

  function scrollBlockIntoView(block) {
    if (!editorScroller) return;
    const scrollerRect = editorScroller.getBoundingClientRect();
    const blockRect = block.getBoundingClientRect();
    const top = editorScroller.scrollTop + blockRect.top - scrollerRect.top - 16;
    editorScroller.scrollTo({ top, behavior: 'smooth' });
  }

  function setActiveBlock(block, shouldScroll = false) {
    blocks.forEach((item) => item.classList.toggle('active', item === block));

    outlineItems.forEach((item) => {
      const target = root.querySelector(`#${item.dataset.target}`);
      const isActive = item.dataset.target === block.id || target === closestHeading(block);
      item.classList.toggle('active', isActive);
    });

    const heading = closestHeading(block);
    if (currentBlockId) currentBlockId.textContent = block.dataset.blockId || '';
    if (contextDomain) contextDomain.textContent = heading.dataset.title || block.dataset.domain || '';
    if (contextScope) contextScope.textContent = `${heading.dataset.title || block.dataset.domain} 统领：${scopeLabels(heading) || block.dataset.title}`;
    if (contextTitle) contextTitle.textContent = block.dataset.title || '';
    if (contextExpression) contextExpression.textContent = block.dataset.expression || '';
    if (contextRequirement) contextRequirement.textContent = block.dataset.requirement || '';
    if (contextScore) contextScore.textContent = block.dataset.score || '';
    if (contextSource) contextSource.textContent = block.dataset.source || '';
    if (contextPotential) contextPotential.textContent = block.dataset.potential || '';

    updateBreadcrumb(block);
    renderHistory(block);
    if (historyPreview) historyPreview.hidden = true;
    if (suggestion) suggestion.hidden = true;

    if (shouldScroll) scrollBlockIntoView(block);
  }

  function drillInto(heading) {
    drilledHeading = heading;
    const allowed = new Set((heading.dataset.scope || heading.id).split(/\s+/).filter(Boolean));
    blocks.forEach((block) => block.hidden = !allowed.has(block.id));
    outlineItems.forEach((item) => {
      const target = item.dataset.target;
      const targetBlock = root.querySelector(`#${target}`);
      item.hidden = targetBlock ? !allowed.has(target) : true;
    });
    if (exitDrill) exitDrill.hidden = false;
    setActiveBlock(heading, true);
  }

  function exitDrilldown() {
    drilledHeading = null;
    blocks.forEach((block) => block.hidden = false);
    outlineItems.forEach((item) => item.hidden = false);
    if (exitDrill) exitDrill.hidden = true;
    const active = root.querySelector('.editor-block.active') || blocks[0];
    setActiveBlock(active, true);
  }

  breadcrumb.addEventListener('click', (event) => {
    const rootButton = event.target.closest('[data-breadcrumb-action="root"]');
    if (rootButton) {
      exitDrilldown();
      return;
    }
    const targetButton = event.target.closest('[data-breadcrumb-target]');
    if (!targetButton) return;
    const block = root.querySelector(`#${targetButton.dataset.breadcrumbTarget}`);
    if (!block) return;
    if (block.dataset.blockKind === 'heading') drillInto(block);
    else setActiveBlock(block, true);
  });

  outlineItems.forEach((item) => {
    item.addEventListener('click', () => {
      const block = root.querySelector(`#${item.dataset.target}`);
      if (block) setActiveBlock(block, true);
    });
    item.addEventListener('dblclick', () => {
      const block = root.querySelector(`#${item.dataset.target}`);
      if (block?.dataset.blockKind === 'heading') drillInto(block);
    });
  });

  blocks.forEach((block) => {
    block.addEventListener('click', () => setActiveBlock(block));
    block.addEventListener('focusin', () => setActiveBlock(block));
    block.addEventListener('dblclick', (event) => {
      if (block.dataset.blockKind === 'heading') {
        event.preventDefault();
        drillInto(block);
      }
    });
  });

  if (exitDrill) exitDrill.addEventListener('click', exitDrilldown);

  root.querySelectorAll('[data-ai-action], [data-ai-global], [data-block-action], [data-inline-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.aiAction || button.dataset.aiGlobal || button.dataset.blockAction || button.dataset.inlineAction;
      const active = root.querySelector('.editor-block.active') || blocks[0];
      setActiveBlock(active);
      if (suggestionText) suggestionText.textContent = `${action}：已基于"${active.dataset.title}"和"${active.dataset.domain}"生成建议。实际实现时会用 AI 的 rg 工具检索材料和要求，只在确认后写入新版本。`;
      if (suggestion) suggestion.hidden = false;
    });
  });

  if (historyList) {
    historyList.addEventListener('click', (event) => {
      const item = event.target.closest('.history-item');
      if (!item) return;
      historyList.querySelectorAll('.history-item').forEach((node) => node.classList.toggle('active', node === item));
      const active = root.querySelector('.editor-block.active') || blocks[0];
      if (historyPreviewText) historyPreviewText.textContent = `${active.dataset.title} · ${item.dataset.version} · ${item.dataset.time}：这里展示单块 diff，可只回溯当前块，不影响同标题域下其他块。`;
      if (historyPreview) historyPreview.hidden = false;
    });
  }

  root.querySelectorAll('[data-suggestion-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.suggestionAction;
      if (action === 'dismiss') {
        if (suggestion) suggestion.hidden = true;
        return;
      }
      if (suggestionText) suggestionText.textContent = action === 'accept'
        ? '建议已模拟接受：当前块生成新的版本节点，并保留可回溯记录。'
        : '差异预览：将弱表达替换为可评分表述，并补充要求来源、潜在要求和更明确的承诺。';
    });
  });

  document.addEventListener('selectionchange', () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return;
    const anchor = selection.anchorNode && selection.anchorNode.parentElement;
    const block = anchor?.closest?.('.editor-block');
    if (block) setActiveBlock(block);
  });

  if (blocks.length > 0) setActiveBlock(blocks[0]);
}
