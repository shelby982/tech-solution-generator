import { api } from './api.js';

const params    = new URLSearchParams(location.search);
const projectId = params.get('projectId');
if (!projectId) { location.href = '/projects'; }

async function loadBlocks() {
  const blockList = await api.blocks.list(projectId);
  const shell = document.querySelector('.tiptap-shell');
  shell.innerHTML = '';

  blockList.forEach(b => {
    const isHeading = b.kind === 'heading';
    const section = document.createElement('section');
    section.className = `editor-block ${isHeading ? 'title-block' : 'content-block'}`;
    section.id = `block-${b.id}`;
    Object.assign(section.dataset, {
      blockId:     b.id,
      blockKind:   b.kind,
      title:       b.title || '',
      domain:      b.domain || '',
      parentTitle: b.parent_title || '',
      requirement: b.requirement || '',
      score:       b.score || '',
      source:      b.source || '',
    });
    const tag  = b.level === 1 ? 'h2' : b.level === 2 ? 'h3' : 'p';
    section.innerHTML = `
      <div class="block-handle">⋮⋮</div>
      <${tag} contenteditable="true">${b.content || ''}</${tag}>
      <div class="block-side-actions">
        <button type="button" data-block-action="润色">润色</button>
        <button type="button" data-block-action="补充">补充</button>
        <button type="button" data-block-action="风格">风格</button>
      </div>`;
    shell.appendChild(section);
  });

  shell.querySelectorAll('[contenteditable]').forEach(el => {
    el.addEventListener('blur', async () => {
      const blockSection = el.closest('.editor-block');
      if (!blockSection) return;
      const blockId = blockSection.dataset.blockId;
      if (!blockId) return;
      await api.blocks.update(blockId, el.innerHTML);
    });
  });

  shell.querySelectorAll('[data-block-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const section = btn.closest('.editor-block');
      const blockId = section?.dataset.blockId;
      if (!blockId) return;
      const action  = btn.dataset.blockAction;
      const result  = await api.blocks.ai(blockId, action);
      const panelText = document.querySelector('[data-ai-suggestion-text]');
      if (panelText) panelText.textContent = result.suggestion;
      else alert(`AI 建议：\n${result.suggestion}`);
    });
  });

  if (typeof initWorkbench === 'function') initWorkbench();
}

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
    const parts = [{ label: '材料撰写', block: null }];
    const parent = heading.dataset.parentTitle ? headingForTitle(heading.dataset.parentTitle) : null;
    if (parent) parts.push({ label: parent.dataset.title, block: parent });
    parts.push({ label: heading.dataset.title || block.dataset.title, block: heading });
    if (block.dataset.blockKind !== 'heading') parts.push({ label: block.dataset.title, block });
    return parts;
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
