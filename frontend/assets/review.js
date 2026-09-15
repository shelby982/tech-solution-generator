// review.js — 评审看板逻辑
//
// URL: /review?threadId=xxx
//
// 流程：
//   1. state(threadId)  — 拿 stage / project_id，回填 tab href、阶段提示、恢复按钮
//   2. review.get(threadId) — 拉历史评审快照渲染卡片
//   3. workflow.stream(threadId) — 监听后续事件
//   4. 三动作按钮 + 卡片复选框

import { api } from './api.js';

// ── URL 参数 ───────────────────────────────────────
const params = new URLSearchParams(location.search);
const threadId = params.get('threadId');
if (!threadId) {
  location.href = '/projects';
  throw new Error('threadId 缺失，已跳转到 /projects');
}
// URL ?backend=... 兜底：跨页面跳转时显式带，让进度条第一次绘制就用真实 backend stage
const _backendParam = params.get('backend');

// ── 常量 ──────────────────────────────────────────
const AGENT_LABEL = { wang_anshi: '王安石', bao_zheng: '包拯' };
const AGENT_KIND  = { wang_anshi: '技术',   bao_zheng: '合规' };
const PENDING_STAGES = new Set([
  'parsing', 'outline_review', 'matching', 'materials_review',
  'generating', 'reviewing', 'report_review',
]);
const STAGE_LABEL = {
  parsing: '解析中',
  outline_review: '大纲评审',
  matching: '素材匹配',
  materials_review: '素材评审',
  generating: '生成中',
  reviewing: '评审中',
  report_review: '报告评审',
  done: '已完成',
  aborted: '已作废',
};

// ── DOM 引用 ─────────────────────────────────────
const $ = (id) => document.getElementById(id);
const els = {
  threadId: $('thread-id-display'),
  banner: $('review-banner'),
  bannerScore: $('banner-score'),
  bannerRisks: $('banner-risks'),
  bannerME: $('banner-missing-evidence'),
  bannerMB: $('banner-missing-bonus'),
  recover: $('review-recover'),
  recoverMsg: $('recover-msg'),
  btnRecover: $('btn-recover'),
  actionsStatus: $('actions-status'),
  btnDownload: $('btn-download'),
  btnRegen: $('btn-regen'),
  btnAbort: $('btn-abort'),
  stageValue: $('stage-value'),
  grid: $('review-grid'),
  headerName: $('header-project-name'),
};

if (els.threadId) els.threadId.textContent = threadId.slice(0, 8) + '…';

// ── 状态：blockId -> {wang_anshi: finding, bao_zheng: finding} ──
const findings = new Map();
const selected = new Set();
// 评审进度：block_id → {wang_anshi: 'idle'|'reviewing'|'done', bao_zheng: ...}
const reviewStatus = new Map();
// block 元信息（title / content / matrix）：进入页面时一次性 fetch /api/projects/{pid}/blocks
let blocksMeta = []; // 数组（保持 order_idx 顺序，决定卡片排序）
let blocksMetaById = new Map();
let totalBlocks = 0;
// 章节多选：默认全选所有 block，用户在左侧 outline 勾选 / 取消后过滤 review-grid
const selectedOutline = new Set();
let downloadUrl = null;
let stream = null;
// stage 进度条相关（实际渲染逻辑在文件末尾），提前声明避免 renderStage 内 TDZ
let _stageProjectId = null;
let _stageBackend = null;

// 页面卸载时关闭 SSE，避免后端连接泄漏
window.addEventListener('beforeunload', () => {
  try { stream?.close(); } catch {}
});

// ── 工具 ──────────────────────────────────────────
function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function scoreClass(score) {
  if (score == null) return '';
  if (score >= 85) return 'score-high';
  if (score >= 70) return 'score-mid';
  return 'score-low';
}

function ensureFindingBucket(blockId) {
  if (!findings.has(blockId)) {
    findings.set(blockId, {});
  }
  return findings.get(blockId);
}

// ── 渲染：单个卡片 ────────────────────────────────
function cardHtml(blockId) {
  const bucket = findings.get(blockId) || {};
  const wang = bucket.wang_anshi || {};
  const bao  = bucket.bao_zheng  || {};
  const isSelected = selected.has(blockId);
  const status = reviewStatus.get(blockId) || {};
  const blockMeta = blocksMetaById.get(blockId) || {};
  const title = blockMeta.title || `Block ${blockId}`;

  const renderScorePill = (agent) => {
    const f = bucket[agent] || {};
    const s = status[agent] || 'idle';
    const score = f.score;
    const cls = scoreClass(score);
    let badge = '';
    if (s === 'reviewing') {
      badge = `<span class="rev-state reviewing">评审中…</span>`;
    } else if (s === 'done' || score != null) {
      badge = `<span class="rev-state done">✓</span>`;
    } else {
      badge = `<span class="rev-state idle">待评审</span>`;
    }
    return `
      <span class="score-pill ${cls}">
        <span class="agent">${AGENT_KIND[agent]}（${AGENT_LABEL[agent]}）</span>
        <span class="num">${score == null ? '—' : score}</span>
        ${badge}
      </span>`;
  };

  const renderIssues = (f, kindLabel) => {
    const issues = Array.isArray(f.issues) ? f.issues : [];
    if (!issues.length) return '';
    return `
      <div class="card-section collapsible">
        <div class="card-section-title">${kindLabel}问题</div>
        <ul class="issue-list">
          ${issues.map(it => `
            <li>
              <span class="severity-tag ${escHtml(it.severity || 'medium')}">${escHtml(it.severity || 'medium')}</span>
              ${escHtml(it.point || '')}
              ${it.suggestion ? `<span class="muted"> → ${escHtml(it.suggestion)}</span>` : ''}
            </li>`).join('')}
        </ul>
      </div>`;
  };

  const renderStrengths = (f, kindLabel) => {
    const strengths = Array.isArray(f.strengths) ? f.strengths : [];
    if (!strengths.length) return '';
    return `
      <div class="card-section collapsible">
        <div class="card-section-title">${kindLabel}优势</div>
        <ul class="strength-list">
          ${strengths.map(s => `<li>${escHtml(s)}</li>`).join('')}
        </ul>
      </div>`;
  };

  return `
    <article class="block-card ${isSelected ? 'selected' : ''}" data-block-id="${escHtml(blockId)}">
      <header class="card-head">
        <input type="checkbox" class="card-checkbox" ${isSelected ? 'checked' : ''} aria-label="选择 block ${escHtml(blockId)}" />
        <div class="card-title" title="${escHtml(title)}">${escHtml(title)}</div>
        <button type="button" class="card-view-content" data-block-id="${escHtml(blockId)}" title="查看正文">查看正文</button>
      </header>

      <div class="card-scores">
        ${renderScorePill('wang_anshi')}
        ${renderScorePill('bao_zheng')}
      </div>

      ${renderIssues(wang, '技术')}
      ${renderIssues(bao,  '合规')}
      ${renderStrengths(wang, '技术')}
      ${renderStrengths(bao,  '合规')}

      <button type="button" class="card-toggle">收起 / 展开</button>
    </article>`;
}

function renderGrid() {
  // 排序：按 blocksMeta（toc）顺序，没有 meta 的 block_id 追加到末尾
  const orderedIds = blocksMeta
    .map(b => b.block_id)
    .filter(bid => findings.has(bid) || reviewStatus.has(bid));
  const remaining = [...findings.keys()].filter(bid => !orderedIds.includes(bid));
  let ids = orderedIds.concat(remaining);
  // 大纲多选过滤：仅显示用户勾选的章节卡片（selectedOutline 为空时不过滤）
  if (selectedOutline.size > 0) {
    ids = ids.filter(bid => selectedOutline.has(String(bid)));
  }
  if (!ids.length) {
    els.grid.innerHTML = '<p class="muted">未选中任何章节，请在左侧大纲勾选要查看的章节。</p>';
    renderReviewProgress();
    return;
  }
  els.grid.innerHTML = ids.map(cardHtml).join('');
  // 同步进度头部
  renderReviewProgress();
  els.grid.querySelectorAll('.block-card').forEach((card) => {
    const blockId = card.dataset.blockId;
    const cb = card.querySelector('.card-checkbox');
    cb?.addEventListener('change', () => {
      if (cb.checked) selected.add(blockId);
      else selected.delete(blockId);
      card.classList.toggle('selected', cb.checked);
      refreshActionsStatus();
    });
    card.querySelector('.card-toggle')?.addEventListener('click', () => {
      card.classList.toggle('collapsed');
    });
    card.querySelector('.card-view-content')?.addEventListener('click', () => {
      const meta = blocksMetaById.get(card.dataset.blockId) || {};
      openContentModal(meta.title || card.dataset.blockId, meta.content || '（暂无正文）');
    });
  });
}

function renderReviewProgress() {
  const total = totalBlocks || blocksMeta.length || 0;
  const root = document.getElementById('review-progress');
  if (!root || !total) return;
  let wangDone = 0, baoDone = 0, wangIp = 0, baoIp = 0;
  reviewStatus.forEach(s => {
    if (s.wang_anshi === 'done') wangDone++;
    else if (s.wang_anshi === 'reviewing') wangIp++;
    if (s.bao_zheng === 'done') baoDone++;
    else if (s.bao_zheng === 'reviewing') baoIp++;
  });
  const fmt = (done, ip) => ip > 0 ? `${done}/${total}（评审中…）` : `${done}/${total}`;
  const wangLine = root.querySelector('[data-agent="wang_anshi"] .rev-progress-meta');
  const baoLine  = root.querySelector('[data-agent="bao_zheng"] .rev-progress-meta');
  const wangBar  = root.querySelector('[data-agent="wang_anshi"] .rev-progress-bar');
  const baoBar   = root.querySelector('[data-agent="bao_zheng"] .rev-progress-bar');
  if (wangLine) wangLine.textContent = fmt(wangDone, wangIp);
  if (baoLine)  baoLine.textContent  = fmt(baoDone, baoIp);
  if (wangBar)  wangBar.style.width = `${Math.round((wangDone / total) * 100)}%`;
  if (baoBar)   baoBar.style.width  = `${Math.round((baoDone / total) * 100)}%`;
  root.hidden = false;
}

function renderOutlinePanel() {
  const root = document.getElementById('review-outline-list');
  if (!root || !blocksMeta.length) return;
  const minLevel = Math.min(...blocksMeta.map(b => Number(b.level) || 1));
  const normalize = (raw) => Math.max(1, Math.min(4, (Number(raw) || 1) - minLevel + 1));
  root.innerHTML = blocksMeta.map(b => {
    const bid = String(b.block_id || '');
    const checked = selectedOutline.has(bid);
    const lv = normalize(b.level);
    const title = b.title || bid;
    return `<label class="outline-item level-${lv} ${checked ? 'selected' : ''}" data-block-id="${escHtml(bid)}">
      <input type="checkbox" data-block-id="${escHtml(bid)}" ${checked ? 'checked' : ''} />
      <span class="outline-item-label" title="${escHtml(title)}">${escHtml(title)}</span>
    </label>`;
  }).join('');
  // 委托 change：切换 selectedOutline 并仅刷新当前 item + review-grid（不重绘整列表，避免抖动）
  root.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const bid = cb.dataset.blockId;
      if (cb.checked) selectedOutline.add(bid); else selectedOutline.delete(bid);
      const li = cb.closest('.outline-item');
      if (li) li.classList.toggle('selected', cb.checked);
      renderGrid();
    });
  });
  document.getElementById('btn-outline-all')?.addEventListener('click', () => {
    blocksMeta.forEach(b => { if (b?.block_id) selectedOutline.add(String(b.block_id)); });
    renderOutlinePanel();
    renderGrid();
  });
  document.getElementById('btn-outline-none')?.addEventListener('click', () => {
    selectedOutline.clear();
    renderOutlinePanel();
    renderGrid();
  });
}

function openContentModal(title, content) {
  let modal = document.getElementById('content-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'content-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:9999;';
    modal.innerHTML = `
      <div class="content-modal-box" style="background:#fff;width:min(720px,92vw);max-height:80vh;border-radius:10px;display:flex;flex-direction:column;overflow:hidden;">
        <div class="content-modal-head" style="padding:14px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;">
          <h3 style="flex:1;margin:0;font-size:15px;" class="cm-title"></h3>
          <button type="button" class="cm-close" style="border:none;background:none;cursor:pointer;font-size:18px;">✕</button>
        </div>
        <div class="cm-body" style="padding:14px 18px;overflow-y:auto;white-space:pre-wrap;font-size:13.5px;line-height:1.7;"></div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    modal.querySelector('.cm-close').addEventListener('click', () => modal.remove());
  }
  modal.querySelector('.cm-title').textContent = title;
  modal.querySelector('.cm-body').textContent = content;
}

function refreshActionsStatus() {
  const n = selected.size;
  els.actionsStatus.textContent = `已选 ${n} 个 block`;
  els.btnRegen.textContent = `重新生成 ${n} 个`;
  els.btnRegen.disabled = n === 0;
}

// ── 渲染：全局报告条 ──────────────────────────────
function renderBanner(report) {
  if (!report) return;
  els.banner.hidden = false;
  els.bannerScore.textContent = report.total_score == null ? '—' : Number(report.total_score).toFixed(1);

  const fillList = (el, items) => {
    if (!Array.isArray(items) || !items.length) {
      el.innerHTML = '<li class="muted">无</li>';
      return;
    }
    el.innerHTML = items.map(s => `<li>${escHtml(typeof s === 'string' ? s : JSON.stringify(s))}</li>`).join('');
  };
  fillList(els.bannerRisks, report.top_risks);
  fillList(els.bannerME,    report.missing_evidence);
  fillList(els.bannerMB,    report.missing_bonus);
}

// ── 渲染：阶段 + 恢复条 ──────────────────────────
function renderStage(stage) {
  // 顶部进度条同步：active 永远是 reviewing（PAGE_STAGE），backend stage 仅决定 done/disabled
  _stageBackend = stage;
  if (typeof renderStageBar === 'function') renderStageBar();
  const label = STAGE_LABEL[stage] || stage || '未知';
  if (els.stageValue) els.stageValue.textContent = label;

  if (PENDING_STAGES.has(stage)) {
    if (els.btnDownload) els.btnDownload.disabled = true;
  } else if (stage === 'aborted') {
    if (els.btnDownload) els.btnDownload.disabled = true;
    if (els.actionsStatus) els.actionsStatus.textContent = '该工作流已作废';
  } else {
    if (els.btnDownload) els.btnDownload.disabled = false;
  }
}

// ── 事件流 ───────────────────────────────────────
function openStream() {
  if (stream) { try { stream.close(); } catch {} }
  stream = api.workflow.stream(threadId, {
    onReviewBlockStart: (data) => {
      if (!data?.block_id || !data?.agent) return;
      const status = reviewStatus.get(data.block_id) || {};
      status[data.agent] = 'reviewing';
      reviewStatus.set(data.block_id, status);
      // 没 finding 也要让卡片出现在网格里
      if (!findings.has(data.block_id)) findings.set(data.block_id, {});
      renderGrid();
    },
    onReviewFinding: (data) => {
      if (!data?.block_id || !data?.agent) return;
      const bucket = ensureFindingBucket(data.block_id);
      const prev = bucket[data.agent] || {};
      bucket[data.agent] = {
        ...prev,
        score: data.score,
        issues: Array.isArray(data.issues) ? data.issues : (prev.issues || []),
      };
      const status = reviewStatus.get(data.block_id) || {};
      status[data.agent] = 'done';
      reviewStatus.set(data.block_id, status);
      renderGrid();
    },
    onReportReady: (data) => {
      renderBanner(data?.report);
    },
    onCheckpoint: (data) => {
      if (data?.stage) renderStage(data.stage);
    },
    onDone: (data) => {
      downloadUrl = data?.download_url || `/api/workflow/${threadId}/download`;
      renderStage('done');
    },
    onAborted: () => {
      renderStage('aborted');
    },
    onError: (data) => {
      console.error('workflow error:', data);
    },
  });
}

// ── 按钮事件 ─────────────────────────────────────
els.btnDownload.addEventListener('click', () => {
  const url = downloadUrl || `/api/workflow/${threadId}/download`;
  window.open(url, '_blank');
});

els.btnRegen.addEventListener('click', async () => {
  if (!selected.size) return;
  const ids = [...selected];
  els.btnRegen.disabled = true;
  try {
    await api.workflow.regen(threadId, ids);
    els.actionsStatus.textContent = `已发起重生：${ids.join(', ')}`;
    selected.clear();
    renderGrid();
    refreshActionsStatus();
  } catch (err) {
    console.error('regen failed:', err);
    alert('重新生成请求失败，请查看控制台。');
    els.btnRegen.disabled = false;
  }
});

els.btnAbort.addEventListener('click', async () => {
  if (!confirm('确认整体作废本次工作流？此操作不可撤销。')) return;
  try {
    await api.workflow.abort(threadId);
    renderStage('aborted');
  } catch (err) {
    console.error('abort failed:', err);
    alert('作废请求失败，请查看控制台。');
  }
});

els.btnRecover?.addEventListener('click', async () => {
  els.btnRecover.disabled = true;
  try {
    await api.workflow.recover(threadId);
    openStream();
    if (els.recover) els.recover.hidden = true;
  } catch (err) {
    console.error('recover failed:', err);
    alert('恢复请求失败，请查看控制台。');
    els.btnRecover.disabled = false;
  }
});

// ── 初始化 ───────────────────────────────────────
async function init() {
  // 1. state -> stage / project_id / report
  let state = {};
  try {
    state = await api.workflow.state(threadId) || {};
  } catch (err) {
    console.error('state failed:', err);
  }

  const stage = state.stage || 'unknown';
  renderStage(stage);

  const projectId = state.project_id;
  if (projectId) {
    fetch(`/api/projects/${projectId}`).then(r => r.json()).then(p => {
      if (p?.name) els.headerName.textContent = p.name;
      else els.headerName.textContent = `项目 #${projectId}`;
    }).catch(() => { els.headerName.textContent = `项目 #${projectId}`; });
    setupStageBar(stage, projectId);
    // 拉 blocks meta：title / content 用于卡片显示与"查看正文"弹窗
    try {
      const list = await fetch(`/api/projects/${projectId}/blocks`).then(r => r.ok ? r.json() : []);
      if (Array.isArray(list)) {
        blocksMeta = list.slice().sort((a, b) => (a.order_idx ?? 0) - (b.order_idx ?? 0));
        blocksMetaById = new Map();
        blocksMeta.forEach(b => {
          if (b?.block_id) blocksMetaById.set(String(b.block_id), b);
        });
        // 评审进度的"总数"=已生成内容 block 数（heading 类无内容不参与评审）
        totalBlocks = blocksMeta.filter(b => b?.content && String(b.content).trim()).length || blocksMeta.length;
        // 默认全选所有章节
        selectedOutline.clear();
        blocksMeta.forEach(b => { if (b?.block_id) selectedOutline.add(String(b.block_id)); });
        renderOutlinePanel();
      }
    } catch (e) { console.warn('[review] load blocks meta failed', e); }
  } else {
    els.headerName.textContent = '未知项目';
    setupStageBar(stage, null);
  }
  loadModels();

  // state 中的 review.report 也尝试渲染
  if (state.review?.report) {
    renderBanner(state.review.report);
  }

  // state 中的 tech_findings / compliance_findings 作为 findings 来源兜底：
  // 早期 reviews 表未落库的工作流，仍能通过 LangGraph checkpoint 状态恢复完整评审视图。
  const stateTech = state.review?.tech_findings || {};
  const stateComp = state.review?.compliance_findings || {};
  for (const [bid, f] of Object.entries(stateTech)) {
    const bucket = ensureFindingBucket(bid);
    bucket.wang_anshi = {
      score: f.score,
      issues: Array.isArray(f.issues) ? f.issues : [],
      strengths: Array.isArray(f.strengths) ? f.strengths : [],
      error: f.error || '',
    };
    const status = reviewStatus.get(bid) || {};
    status.wang_anshi = 'done';
    reviewStatus.set(bid, status);
  }
  for (const [bid, f] of Object.entries(stateComp)) {
    const bucket = ensureFindingBucket(bid);
    bucket.bao_zheng = {
      score: f.score,
      issues: Array.isArray(f.issues) ? f.issues : [],
      strengths: Array.isArray(f.strengths) ? f.strengths : [],
      error: f.error || '',
    };
    const status = reviewStatus.get(bid) || {};
    status.bao_zheng = 'done';
    reviewStatus.set(bid, status);
  }

  // 2. review.get -> 历史 findings
  try {
    const snap = await api.review.get(threadId);
    for (const f of (snap?.findings || [])) {
      if (!f?.block_id || !f?.agent) continue;
      const bucket = ensureFindingBucket(f.block_id);
      bucket[f.agent] = {
        score: f.score,
        issues: Array.isArray(f.issues) ? f.issues : [],
        strengths: Array.isArray(f.strengths) ? f.strengths : [],
        error: f.error || '',
      };
      // 历史 finding 视为已完成评审，更新状态点
      const status = reviewStatus.get(f.block_id) || {};
      status[f.agent] = 'done';
      reviewStatus.set(f.block_id, status);
    }
  } catch (err) {
    console.error('review.get failed:', err);
  }
  renderGrid();
  refreshActionsStatus();

  // 3. 打开 SSE
  openStream();
}

// ── 顶部 stage 进度条 ───────────────────────────────
const STAGE_ORDER = ['parsing', 'matching', 'generating', 'reviewing'];
const STAGE_MAP = {
  parsing: 'parsing',
  // 闸门停留态映射回当前阶段：进度条与项目实际所处步骤一致
  outline_review: 'parsing',
  matching: 'matching',
  materials_review: 'matching',
  generating: 'generating',
  reviewing: 'reviewing',
  // 合并为同一 step
  report_review: 'reviewing',
};
const PAGE_STAGE = 'reviewing';

function setupStageBar(stage, projectId) {
  _stageProjectId = projectId;
  // URL backend 参数优先级低于实际 state，但当 state.stage 未定时作兜底
  const effective = stage || _backendParam || null;
  _stageBackend = effective;
  // 维护项目级最远到达 stage（跨页面复用）
  if (projectId && effective) {
    try {
      const mappedNew = STAGE_MAP[effective];
      if (mappedNew) {
        const newIdx = STAGE_ORDER.indexOf(mappedNew);
        const k = `furthestStage:${projectId}`;
        const prev = sessionStorage.getItem(k);
        const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
        if (newIdx > prevIdx) sessionStorage.setItem(k, effective);
      }
    } catch {}
  }
  renderStageBar();
}

function renderStageBar() {
  const root = document.getElementById('stage-progress');
  if (!root) return;
  const pageIdx = STAGE_ORDER.indexOf(PAGE_STAGE);
  let backend = _stageBackend;
  if (!backend && _stageProjectId) {
    try {
      const cached = sessionStorage.getItem(`furthestStage:${_stageProjectId}`);
      if (cached) backend = cached;
    } catch {}
  }
  if (!backend) backend = PAGE_STAGE;
  let backendIdx = STAGE_MAP[backend] ? STAGE_ORDER.indexOf(STAGE_MAP[backend]) : -1;
  if (backend === 'done') backendIdx = STAGE_ORDER.length;
  if (backend === 'aborted') backendIdx = pageIdx;
  if (backendIdx < pageIdx) backendIdx = pageIdx;

  const steps = root.querySelectorAll('.stage-step');
  steps.forEach(el => {
    const i = STAGE_ORDER.indexOf(el.dataset.stage);
    el.classList.remove('active', 'done', 'aborted');
    el.removeAttribute('aria-current');
    el.removeAttribute('aria-disabled');
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    if (backend === 'aborted' && i === backendIdx) {
      el.classList.add('aborted');
      el.setAttribute('aria-disabled', 'true');
      el.setAttribute('tabindex', '-1');
    } else if (i === backendIdx) {
      el.classList.add('active');
      el.setAttribute('aria-current', 'step');
    } else if (i < backendIdx) {
      el.classList.add('done');
    } else {
      // 未到达：灰色 disabled，不允许点击切换视图
      el.setAttribute('aria-disabled', 'true');
      el.setAttribute('tabindex', '-1');
    }
    const name = el.querySelector('.stage-name')?.textContent || '';
    el.setAttribute('aria-label', `跳转到 ${name}`);
  });
}

document.getElementById('stage-progress')?.addEventListener('click', (e) => {
  const step = e.target.closest('.stage-step');
  if (!step) return;
  if (step.classList.contains('aborted')) return;
  // 未到达阶段不允许点击切换视图
  if (step.getAttribute('aria-disabled') === 'true') return;
  const stage = step.dataset.stage;
  const tid = threadId;
  const enc = encodeURIComponent(tid);
  const pid = _stageProjectId;
  // 跳转前持久化 backend stage（sessionStorage）+ 显式带 URL 参数（最稳）
  let backendForUrl = _stageBackend;
  if (!backendForUrl || !STAGE_MAP[backendForUrl]) {
    backendForUrl = (pid && sessionStorage.getItem(`furthestStage:${pid}`)) || '';
  }
  if (pid && backendForUrl && STAGE_MAP[backendForUrl]) {
    try {
      const k = `furthestStage:${pid}`;
      const prev = sessionStorage.getItem(k);
      const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
      const newIdx = STAGE_ORDER.indexOf(STAGE_MAP[backendForUrl]);
      if (newIdx > prevIdx) sessionStorage.setItem(k, backendForUrl);
    } catch {}
  }
  const backendQuery = backendForUrl ? `&backend=${encodeURIComponent(backendForUrl)}` : '';
  const routes = {
    parsing: pid ? `/project-init?projectId=${pid}&threadId=${enc}&view=outline${backendQuery}` : null,
    matching: pid ? `/project-init?projectId=${pid}&threadId=${enc}&view=matching${backendQuery}` : null,
    generating: pid ? `/workbench?projectId=${pid}&threadId=${enc}${backendQuery}` : null,
    reviewing: `/review?threadId=${enc}${backendQuery}`,
    report_review: `/review?threadId=${enc}${backendQuery}`,
  };
  if (routes[stage]) location.href = routes[stage];
});

document.getElementById('stage-progress')?.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const step = e.target.closest('.stage-step');
  if (!step) return;
  e.preventDefault();
  step.click();
});

// ── 模型选择器 ───────────────────────────────────────
let _selectedModel = 'claude-sonnet-4-6';
let _selectedModelLabel = 'Sonnet 4.6';

async function loadModels() {
  const labelEl = document.getElementById('model-selector-label');
  const listEl = document.getElementById('model-list');
  if (!labelEl || !listEl) return;
  let configs = [];
  try {
    const resp = await fetch('/api/configs').then(r => r.json());
    configs = resp?.configs || [];
  } catch {}
  if (!configs.length) {
    configs = [
      { model: 'claude-opus-4-8', name: 'Opus 4.8' },
      { model: 'claude-sonnet-4-6', name: 'Sonnet 4.6' },
      { model: 'claude-haiku-4-5-20251001', name: 'Haiku 4.5' },
    ];
  }
  if (!configs.find(c => c.model === _selectedModel)) {
    _selectedModel = configs[0].model;
    _selectedModelLabel = configs[0].name || configs[0].model;
  }
  labelEl.textContent = _selectedModelLabel;
  listEl.innerHTML = configs.map(c => {
    const lbl = c.name || c.model;
    return `<div class="model-option${c.model === _selectedModel ? ' selected' : ''}" data-model="${c.model}" data-label="${lbl}">
      <span>${lbl}</span>
      ${c.model === _selectedModel ? '<span>✓</span>' : ''}
    </div>`;
  }).join('');
  listEl.querySelectorAll('.model-option').forEach(opt => {
    opt.addEventListener('click', () => {
      _selectedModel = opt.dataset.model;
      _selectedModelLabel = opt.dataset.label || _selectedModel;
      labelEl.textContent = _selectedModelLabel;
      document.getElementById('model-dropdown')?.classList.remove('open');
      loadModels();
    });
  });
}

document.getElementById('model-selector-btn')?.addEventListener('click', (e) => {
  e.stopPropagation();
  document.getElementById('model-dropdown')?.classList.toggle('open');
});
document.addEventListener('click', () => {
  document.getElementById('model-dropdown')?.classList.remove('open');
});

init();
