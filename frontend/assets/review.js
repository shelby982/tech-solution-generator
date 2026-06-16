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
if (!threadId) { location.href = '/projects'; }

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
  tabInit: $('tab-init'),
  tabWorkbench: $('tab-workbench'),
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

els.threadId.textContent = threadId.slice(0, 8) + '…';

// ── 状态：blockId -> {wang_anshi: finding, bao_zheng: finding} ──
const findings = new Map();
const selected = new Set();
let downloadUrl = null;
let stream = null;

// ── 工具 ──────────────────────────────────────────
function escHtml(s) {
  const el = document.createElement('div');
  el.textContent = s == null ? '' : String(s);
  return el.innerHTML;
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

  const renderScorePill = (agent) => {
    const f = bucket[agent] || {};
    const score = f.score;
    const cls = scoreClass(score);
    return `
      <span class="score-pill ${cls}">
        <span class="agent">${AGENT_KIND[agent]}（${AGENT_LABEL[agent]}）</span>
        <span class="num">${score == null ? '—' : score}</span>
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
        <div class="card-title">Block ${escHtml(blockId)}</div>
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
  const ids = [...findings.keys()].sort();
  if (!ids.length) {
    els.grid.innerHTML = '<p class="muted">暂无评审结果。</p>';
    return;
  }
  els.grid.innerHTML = ids.map(cardHtml).join('');

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
  });
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
  const label = STAGE_LABEL[stage] || stage || '未知';
  els.stageValue.textContent = label;

  if (PENDING_STAGES.has(stage)) {
    els.recover.hidden = false;
    els.recoverMsg.textContent = `当前工作流暂停在 “${label}”。`;
    els.btnDownload.disabled = true;
  } else if (stage === 'aborted') {
    els.recover.hidden = true;
    els.btnDownload.disabled = true;
    els.actionsStatus.textContent = '该工作流已作废';
  } else {
    els.recover.hidden = true;
    els.btnDownload.disabled = false;
  }
}

// ── 事件流 ───────────────────────────────────────
function openStream() {
  if (stream) { try { stream.close(); } catch {} }
  stream = api.workflow.stream(threadId, {
    onReviewFinding: (data) => {
      if (!data?.block_id || !data?.agent) return;
      const bucket = ensureFindingBucket(data.block_id);
      const prev = bucket[data.agent] || {};
      bucket[data.agent] = {
        ...prev,
        score: data.score,
        issues: Array.isArray(data.issues) ? data.issues : (prev.issues || []),
      };
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

els.btnRecover.addEventListener('click', async () => {
  els.btnRecover.disabled = true;
  try {
    await api.workflow.recover(threadId);
    openStream();
    els.recover.hidden = true;
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
    els.tabInit.href      = `/project-init?projectId=${projectId}`;
    els.tabWorkbench.href = `/workbench?projectId=${projectId}`;
    fetch(`/api/projects/${projectId}`).then(r => r.json()).then(p => {
      if (p?.name) els.headerName.textContent = p.name;
      else els.headerName.textContent = `项目 #${projectId}`;
    }).catch(() => { els.headerName.textContent = `项目 #${projectId}`; });
  } else {
    els.headerName.textContent = '未知项目';
  }

  // state 中的 review.report 也尝试渲染
  if (state.review?.report) {
    renderBanner(state.review.report);
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
    }
  } catch (err) {
    console.error('review.get failed:', err);
  }
  renderGrid();
  refreshActionsStatus();

  // 3. 打开 SSE
  openStream();
}

init();
