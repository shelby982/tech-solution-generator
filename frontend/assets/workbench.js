import { api } from './api.js';

const params    = new URLSearchParams(location.search);
const projectId = params.get('projectId');
const threadId  = params.get('threadId');
if (!projectId) { location.href = '/projects'; }

// 模块级共享状态：避免 loadBlocks 多次调用时累积 window listener
const _blocksMap = new Map();  // block_id (raw) → block object

function _findOutlineItem(bid) {
  if (!bid) return null;
  return document.querySelector(`.doc-outline-item[data-block-id-str="${CSS.escape(String(bid))}"]`);
}

function _onBlockStart(e) {
  const bid = e.detail?.block_id;
  if (!bid) return;
  const b = _blocksMap.get(bid);
  if (!b) return;
  const item = _findOutlineItem(bid);
  if (!item) return;
  const dot = item.querySelector('.outline-item-dot');
  if (dot) {
    dot.classList.remove('done');
    dot.classList.add('writing');
  }
}

function _onBlockDone(e) {
  const bid = e.detail?.block_id;
  if (!bid) return;
  const b = _blocksMap.get(bid);
  if (!b) return;
  const item = _findOutlineItem(bid);
  if (item) {
    const dot = item.querySelector('.outline-item-dot');
    if (dot) {
      dot.classList.remove('writing');
      dot.classList.add('done');
    }
  }
  // 同步缓存内容，便于章节切换时立即看到
  if (e.detail?.content) {
    b.content = e.detail.content;
    if (window.__extractBlockMap) {
      const cached = window.__extractBlockMap.get(bid);
      if (cached) cached.content = e.detail.content;
    }
  }
}

function _onBlockError(e) {
  const bid = e.detail?.block_id;
  if (!bid) return;
  const item = _findOutlineItem(bid);
  if (!item) return;
  const dot = item.querySelector('.outline-item-dot');
  if (dot) dot.classList.remove('writing');
  item.classList.add('block-error');
}

window.addEventListener('block:start', _onBlockStart);
window.addEventListener('block:done', _onBlockDone);
window.addEventListener('block:error', _onBlockError);

// threadId 模式下用 spec.toc 主导章节列表（闸门 1 完成即就绪），
// proposal.blocks 仅注入"已生成内容"（闸门 3 之后才完整）。
// outline_matrix 提供 8 字段（requirement/key_points/...）。
function buildBlockFromTocSection(section, blocks, matrix) {
  if (!section || !section.id) {
    console.warn('[workbench] toc section missing id', section);
    return null;
  }
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
        return toc.map(sec => buildBlockFromTocSection(sec, blocks, matrix)).filter(Boolean);
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

  // 模块级 listener 用 _blocksMap 判断 block 归属，每次 loadBlocks 重置一次
  _blocksMap.clear();
  blockList.forEach(b => _blocksMap.set(b.block_id, b));

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
}

window.__loadBlocks = loadBlocks;
loadBlocks();
window.addEventListener('beforeunload', () => {
  document.querySelectorAll('[data-sse]').forEach(el => el._sse?.close());
});
