/* 由 project-init.html 的内联 <script type="module"> 机械抽取而来（2026-10-13 三模式合并）。
   抽成模块的目的：两段内联脚本有 90 个同名顶层标识符 + 18 个重复 DOM id，
   同页拼接必然互相覆盖；各自成为一个模块即各自一个作用域。
   本文件在抽取时**未改任何逻辑**。 */
import { api } from './api.js?v=20261013-v9';
// 顶栏（项目名旁边那行「第 N 轮 · ...」）由共享壳 workspace.js 渲染。这里要把它
// 改成**页面自己的真实状态**：checkpoint 的 stage 在提炼中/提炼完成后都停在
// outline_review，只看它的话顶栏永远显示"待确认目录"（实测就是这个问题）。
import { workspace, renderWorkspaceChrome,
         REQUIREMENT_ROLES, SELECTABLE_REQUIREMENT_ROLES, ROLE_LABEL_SHARED, ROLE_HINT_SHARED,
         roleMismatchWarning, inferRoleFromFilename,
       } from './workspace.js?v=20261013-v9';
import { buildOutlineTree, isOrphanedRun, activeStages, instructionDirty, newOutlineId, outlineSubtreeRange,
         moveOutlineNode, indentOutlineNode, outdentOutlineNode,
         removeOutlineSubtree, insertSiblingAfter, insertChildOf,
         CONSTRAINT_LEVELS, constraintLevelLabel,
         isExtractDone as isDoneBlock, extractBadgeState as badgeStatusOf,
         // 素材依据的双向视图（匹配阶段 §4.D）：纯投影与 HTML 都在模型层（有 node --test
         // 覆盖），页面只把本页缓存的应答条目喂进去 —— 素材→章 与 条目→依据 同一份数据。
         evidenceBySource, usageFor, matchEntryDecision, MATCH_ENTRY,
         renderEvidenceSection as renderEvidenceSectionPure,
         groupEvidenceByItem, itemCode, parseEvidence,
         // 章节状态投影：素材库 / 方案编辑 / 覆盖矩阵共用同一份判定（不再各算各的）
         chapterStage, chapterBadges, filterChapters, chapterFilterCounts,
         CHAPTER_STAGE_LABELS, evidenceByChapter, expandVisibleWithAncestors
       } from './workspace-model.mjs?v=20261013-v9';

const params = new URLSearchParams(location.search);
// projectId **只取数字部分**：链接被误写成 `?projectId=19**(Cmd+Shift+R)`（连带说明文字）
// 时，原实现会把整串当 id，导致每个接口都 422、页面一片空白而无任何解释。
// 归一化 + 控制台告警，既容错又不掩盖真实的参数错误。
const _rawProjectId = params.get('projectId') || '';
const _pidMatch = _rawProjectId.match(/\d+/);
const projectId = _pidMatch ? _pidMatch[0] : '';
if (_rawProjectId && String(_rawProjectId) !== String(projectId)) {
  console.warn('[outline] projectId 参数含非数字内容，已取数字部分：',
    JSON.stringify(_rawProjectId), '→', projectId);
}
if (!projectId) { location.href = '/projects'; }
// URL 显式指定的视图模式（顶部进度条点击会带上）；空 / 非法值视为不覆盖
const _viewParam = params.get('view');
// URL 显式指定的 backend stage（跨视图跳转 click handler 会带）：作为 sessionStorage
// 兜底，确保 renderStageBar 第一次绘制就能用真实 backend stage 点亮 active。
const _backendParam = params.get('backend');
if (_backendParam && projectId) {
  try {
    // 只升不降（单调推进）
    const k = `furthestStage:${projectId}`;
    const prev = sessionStorage.getItem(k);
    const STAGE_ORDER_LITERAL = ['parsing', 'matching', 'generating', 'reviewing'];
    const STAGE_MAP_LITERAL = {
      parsing: 'parsing', outline_review: 'parsing',
      // extracting：闸门 1 放行后的逐节提炼，同属「解析大纲」这一步（后端运行期 stage）
      extracting: 'parsing',
      matching: 'matching', materials_review: 'matching',
      generating: 'generating',
      reviewing: 'reviewing',
      report_review: 'reviewing',
    };
    const prevIdx = prev ? STAGE_ORDER_LITERAL.indexOf(STAGE_MAP_LITERAL[prev] || '') : -1;
    const newIdx = STAGE_ORDER_LITERAL.indexOf(STAGE_MAP_LITERAL[_backendParam] || '');
    if (newIdx >= 0 && newIdx >= prevIdx) sessionStorage.setItem(k, _backendParam);
  } catch {}
}

// ── DOM refs ──
const reqStack   = document.getElementById('req-stack');
const srcStack   = document.getElementById('src-stack');
const reqCount   = document.getElementById('req-count');
const srcCount   = document.getElementById('src-count');
const btnAddReq  = document.getElementById('btn-add-req');
const btnAddSrc  = document.getElementById('btn-add-src');
const btnExtract = document.getElementById('btn-extract');
const modelBtn      = document.getElementById('model-selector-btn');
const modelLabel    = document.getElementById('model-selector-label');
const modelDropdown = document.getElementById('model-dropdown');
const modelListEl   = document.getElementById('model-list');
const statusBar  = document.getElementById('status-bar');
const statusLabel = document.getElementById('status-label');
const progressTrack = document.getElementById('progress-track');
const progressBar   = document.getElementById('progress-bar');
const statusPct     = document.getElementById('status-pct');
const statusActions = document.getElementById('status-actions');
const blocksList  = document.getElementById('blocks-list');
// 覆盖率面板 / 得分风险面板的样式。刻意用**行内流式元素**，不 prepend 到 shell ——
// 之前那条 43px 横幅就是因为插到整页最前面把布局顶下去了。
document.head.insertAdjacentHTML('beforeend', `<style>
.ov-panel{margin:8px 12px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;
  background:var(--surface);font-size:12.5px;line-height:1.6}
.ov-panel-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ov-panel-title{font-weight:600;font-size:13px}
.ov-metric{display:inline-flex;align-items:baseline;gap:4px;padding:2px 8px;border-radius:999px;
  background:var(--surface-2,#f6f6f4);border:1px solid var(--line)}
.ov-metric b{font-size:13px}
.ov-metric.ok{background:#ecfdf5;border-color:#a7f3d0;color:#047857}
.ov-metric.warn{background:#fffbeb;border-color:#fcd34d;color:#92400e}
.ov-metric.bad{background:#fef2f2;border-color:#fca5a5;color:#b91c1c}
.ov-gaps{margin-top:6px;color:#92400e}
.ov-actions{display:flex;align-items:center;gap:10px;margin-top:8px;padding-top:8px;
  border-top:1px dashed var(--line)}
.ov-actions .mw-btn{margin-left:auto}
.ov-sub{color:var(--muted);font-size:11.5px}
.ov-scope{display:inline-flex;align-items:center;gap:5px;font-size:12px}
.ov-scope select{font:inherit;font-size:12px;padding:2px 4px;border:1px solid var(--line);
  border-radius:6px;max-width:260px}
.split-banner{display:flex;flex-direction:column;gap:3px;margin:8px 12px;padding:9px 12px;
  border-radius:8px;font-size:12.5px;border:1px solid var(--line)}
.split-banner strong{font-size:13px}
.split-banner span{color:var(--muted)}
.split-banner.todo{background:#fffbeb;border-color:#fcd34d}
.split-banner.done{background:#f0fdf4;border-color:#a7f3d0}
.ov-risk-table{width:100%;border-collapse:collapse;font-size:12.5px}
.ov-risk-table th,.ov-risk-table td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
.ov-risk-table th{color:var(--muted);font-weight:500}
.ov-tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;border:1px solid}
.ov-tag.high{background:#fef2f2;border-color:#fca5a5;color:#b91c1c}
.ov-tag.medium{background:#fffbeb;border-color:#fcd34d;color:#92400e}
.ov-tag.ok{background:#ecfdf5;border-color:#a7f3d0;color:#047857}
.ov-tag.unreviewed{background:var(--surface-2,#f6f6f4);border-color:var(--line);color:var(--muted)}
</style>`);
// 「原始目录 vs 应答目录」的提示条样式（见 warnIfOutlineNotDerived）
document.head.insertAdjacentHTML('beforeend', `<style>
.raw-toc-warn{display:flex;flex-direction:column;gap:4px;margin:8px 12px;padding:10px 12px;
  background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;color:#92400e;font-size:12.5px;line-height:1.5}
.raw-toc-warn strong{font-size:13px}
</style>`);
// 左栏章节树：行标记与「方案编辑」共用一份（2026-10-13 左栏归一）。
// 此前两侧各拼一套（.ot-row / .doc-outline-item），数据投影虽已共用，
// 但标记重复维护会漂移（workspace-model.mjs 注释里记着 "#undefined" 那次）。
import { chapterNode, chapterBadgesHtml as sharedBadgesHtml } from './chapter-tree.mjs?v=20261013-v9';
const blocksEmpty = document.getElementById('blocks-empty');
const sidebarToc  = document.getElementById('sidebar-toc');
const ringFill    = document.getElementById('ring-fill');
const sidebarProgressText = document.getElementById('sidebar-progress-text');
const fileInputReq = document.getElementById('file-input-req');
const fileInputSrc = document.getElementById('file-input-src');

// ── 状态 ──
let extractionState = 'idle'; // idle | extracting | done
let totalBlocks = 0;
let doneBlocks  = 0;
// loadExistingBlocks 的 promise。它自己会写状态栏，而「中断提示」要在它之后写，
// 否则会被它的「已解析 N 个章节」覆盖。
let blocksReady = Promise.resolve();
let matchedCount = 0; // 已匹配章节数（仅匹配阶段使用）
const matchedIds = new Set();
let currentReader = null;
let selectedModel = 'claude-sonnet-4-6';
let selectedModelLabel = 'Sonnet 4.6';

// ── workflow 运行态 ──
let currentThreadId = null;
let currentEventSource = null;
let currentGate = null; // null | 'review_outline' | 'review_materials' | 'review_report'
// 目录派生（应答文件目录）状态：版本号由后端 outline_draft 帧累加，error 非空表示
// 模型没能派生、沿用了规范书原始目录（降级），需要在闸门 1 提示用户
let draftRevision = 0;
let outlineError = '';
// URL backend 参数（来自顶部 stage-progress click）作为 currentStage 初值，
// 让 renderStageBar 第一次绘制就用上真实 backend stage，不依赖异步 state API。
// 没 URL backend 时用 sessionStorage furthestStage 兜底。
let currentStage = (() => {
  if (_backendParam && _backendParam.trim()) return _backendParam;
  try {
    const cached = sessionStorage.getItem(`furthestStage:${projectId}`);
    if (cached) return cached;
  } catch {}
  return null;
})();
const blocksByOrder = []; // 按 outline_extract 顺序累积，给 confirm 用
const blocksById = new Map();

// ── 视图模式：outline（拆解评分点） / matching（素材匹配） ──
// 优先级：URL ?view=... 显式指定 > 后端 stage > 大纲完成度。
// 这样从顶部进度条点击「解析大纲」/「素材匹配」可以强制切到对应视图，
// 即使大纲已全部提炼完成也能回到 outline 视图查看 / 编辑。
let pageViewOverride = (_viewParam === 'outline' || _viewParam === 'matching') ? _viewParam : null;
function applyPageView() {
  // 视图判定：单一规则 = 由 backend stage 决定，与项目实际所处步骤严格对齐。
  //   parsing / outline_review → outline 视图（解析大纲，含闸门 1 等待确认）
  //   matching / materials_review → matching 视图（素材匹配，含闸门 2 等待确认）
  // pageViewOverride（URL ?view=...）优先级最高，让用户能从顶部进度条主动回看。
  let auto;
  if (currentStage === 'matching' || currentStage === 'materials_review') {
    auto = 'matching';
  } else if (currentStage === 'parsing' || currentStage === 'outline_review') {
    auto = 'outline';
  } else if (currentStage) {
    // generating / reviewing / report_review 等：不属于 project-init 视图，但兜底回 outline
    auto = 'outline';
  } else {
    // 没 backend stage（新项目）：停在「要求与大纲」。素材匹配是素材库视图里的动作，
    // 由那边的「素材匹配」按钮触发，不按提炼完成度自动切过去。
    auto = 'outline';
  }
  // 三模式合并（2026-10-13）：body[data-view] 的所有权归 mode-dispatch.js，
  // 本模块只负责 outline / matching 两模式之间的自动推导。
  // 实测问题：请求 ?view=editor 时，这里会按 backend stage 把 view 改回 outline ——
  // 两个模块抢同一个属性，编辑器模式根本进不去。
  if (document.body.dataset.view === 'editor' || _viewParam === 'editor') return;
  const view = pageViewOverride || auto;
  document.body.dataset.view = view;
  if (typeof refreshStructureEditable === 'function') refreshStructureEditable();
  // 同步底部按钮文案
  const btn = document.getElementById('btn-extract');
  const hint = document.querySelector('.materials-footer .extract-hint');
  if (btn && hint) {
    if (view === 'matching') {
      btn.innerHTML = '✦ 素材匹配';
      hint.textContent = '为每个章节匹配最相关的原始素材片段';
    } else {
      btn.innerHTML = '✦ 拆解评分点';
      hint.textContent = '依据应标要求生成章节结构，原始素材用于补充各模块内容';
    }
  }
  // 折叠态按当前视图的记忆恢复：匹配视图默认全收起（它是目录，不是表单）
  if (typeof applyOutlineExpansion === 'function') {
    applyOutlineExpansion(false);
  }
  // view 切换会改变 PAGE_STAGE → 顶部进度条 active 跟着切
  if (typeof renderStageBar === 'function') renderStageBar();
  // 匹配工作区（总览条 / 筛选 / 右侧详情）只在 matching 视图显示，但它们的显示与隐藏
  // 由视图驱动 —— 不在这里同步的话，切回素材库会看到上一次的残留（或空面板）。
  if (typeof renderMatchSummary === 'function') {
    renderMatchSummary();
    applyMatchFilter();
    renderMatchDetail();
  }
}

// 切视图：只改视图，不触发任何后端动作。
function switchView(view, statusLabel) {
  pageViewOverride = view;
  try {
    const url = new URL(location.href);
    url.searchParams.set('view', view);
    history.replaceState({}, '', url);
  } catch {}
  applyPageView();
  if (statusLabel) {
    // 状态栏按钮组按 view 选（见 setStatusBar 的 done 分支），切完视图要重绘一次
    const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
    setStatusBar(extractionState || 'done', statusLabel, pct, totalBlocks, doneBlocks);
  }
}

const THREAD_KEY = (pid) => `workflow:${pid}`;
function getThread(pid) { try { return sessionStorage.getItem(THREAD_KEY(pid)); } catch { return null; } }
function setThread(pid, tid) { try { sessionStorage.setItem(THREAD_KEY(pid), tid); } catch {} }
function clearThread(pid) { try { sessionStorage.removeItem(THREAD_KEY(pid)); } catch {} }

// ── 顶部 stage 进度条 ──
const STAGE_ORDER = [
  'parsing', 'matching', 'generating', 'reviewing',
];
const STAGE_MAP = {
  parsing: 'parsing',
  // 运行失败（如目录派生失败）：仍属「要求与大纲」这一步，进度条不要跳走
  failed: 'parsing',
  // 闸门停留态映射回当前阶段：进度条颜色与"项目实际所处步骤"一致，
  // 避免"进度条点亮方案撰写但页面还是素材匹配"的认知冲突。
  outline_review: 'parsing',
  // extracting：闸门 1 放行后的逐节提炼，仍属「解析大纲」这一步。
  // 后端在续跑前把它写进 workflow_runs（节点只能在结束时写 checkpoint），
  // boot 的兜底 fetch 会拿它点亮进度条。
  extracting: 'parsing',
  matching: 'matching',
  materials_review: 'matching',
  generating: 'generating',
  reviewing: 'reviewing',
  // 报告确认与双视角评审合并为同一 step "评审确认"
  report_review: 'reviewing',
};

// 当前页面承担的阶段：active step 永远是它，与 backendStage 解耦。
// project-init 视图分两种：outline 视图固定 'parsing'，matching 视图固定 'matching'。
function getPageStage() {
  return document.body.dataset.view === 'matching' ? 'matching' : 'parsing';
}

function setStage(stage) {
  if (!stage) return;
  // currentStage 单调推进：避免 SSE / state API 短暂返回更早的 stage（比如 graph
  // 重启瞬间的 'parsing'）把进度条颜色拉回来导致 active 抖动。
  // 例外：'aborted' 强制覆盖（终态）。
  if (currentStage && stage !== 'aborted' && stage !== 'done') {
    const prevMapped = STAGE_MAP[currentStage];
    const newMapped = STAGE_MAP[stage];
    const prevIdx = prevMapped ? STAGE_ORDER.indexOf(prevMapped) : -1;
    const newIdx = newMapped ? STAGE_ORDER.indexOf(newMapped) : -1;
    if (newIdx >= 0 && prevIdx >= 0 && newIdx < prevIdx) {
      // 倒退：忽略，不更新 currentStage 也不重绘
      return;
    }
  }
  currentStage = stage;
  // 维护项目级最远到达的 stage：renderStageBar 在 currentStage 暂时为空 / 比缓存更早时
  // 用它作 backendIdx 兜底，避免用户在 SSE / state 返回前点击进度条 step 被错判为 disabled。
  try {
    const mappedNew = STAGE_MAP[stage];
    if (mappedNew) {
      const newIdx = STAGE_ORDER.indexOf(mappedNew);
      const k = `furthestStage:${projectId}`;
      const prev = sessionStorage.getItem(k);
      const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
      if (newIdx > prevIdx) sessionStorage.setItem(k, stage);
    }
  } catch {}
  applyPageView();
  renderStageBar();
}

// 重绘进度条：active = 后端当前 stage（蓝色 + 呼吸），
//              done = 已经过的阶段（绿色，可点击回看），
//              其余 = 灰色 disabled。
// 当前视图（PAGE_STAGE）只影响视图内容，不再决定 active 颜色。
function renderStageBar() {
  const root = document.getElementById('stage-progress');
  if (!root) return;
  if (root.hasAttribute('hidden')) root.removeAttribute('hidden');

  const pageStage = getPageStage();
  const pageIdx = STAGE_ORDER.indexOf(pageStage);
  // 后端实际所处阶段：
  //   1) currentStage（SSE / state API 拿到）
  //   2) sessionStorage furthest（同 project 在本会话中曾到达的最远 stage）
  //   3) PAGE_STAGE 兜底（用户既然在该页面，graph 至少到这）
  let backend = currentStage;
  if (!backend) {
    try {
      const cached = sessionStorage.getItem(`furthestStage:${projectId}`);
      if (cached) backend = cached;
    } catch {}
  }
  if (!backend && pageStage) backend = pageStage;

  let backendMapped = STAGE_MAP[backend];
  let backendIdx = backendMapped ? STAGE_ORDER.indexOf(backendMapped) : -1;
  if (backend === 'done') backendIdx = STAGE_ORDER.length;
  if (backend === 'aborted') backendIdx = pageIdx;
  // backend 至少不能比当前页面更早（用户已经在这页，说明 graph 至少到这）
  if (backendIdx < pageIdx) backendIdx = pageIdx;

  const steps = root.querySelectorAll('.stage-step');

  // aborted 特殊：把 backend 那一步标红，其他按 backendIdx 标 done
  if (backend === 'aborted') {
    steps.forEach(el => {
      const i = STAGE_ORDER.indexOf(el.dataset.stage);
      el.classList.remove('active', 'done', 'aborted');
      el.removeAttribute('aria-current');
      el.removeAttribute('aria-disabled');
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');
      if (i === backendIdx) {
        el.classList.add('aborted');
        el.setAttribute('aria-disabled', 'true');
        el.setAttribute('tabindex', '-1');
      } else if (i < backendIdx) {
        el.classList.add('done');
      } else {
        el.setAttribute('aria-disabled', 'true');
        el.setAttribute('tabindex', '-1');
      }
    });
    return;
  }

  steps.forEach(el => {
    const i = STAGE_ORDER.indexOf(el.dataset.stage);
    el.classList.remove('active', 'done', 'aborted');
    el.removeAttribute('aria-current');
    el.removeAttribute('aria-disabled');
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');

    if (i === backendIdx) {
      // 后端当前所处 stage：蓝色呼吸（active）
      el.classList.add('active');
      el.setAttribute('aria-current', 'step');
    } else if (i < backendIdx) {
      // 已完成阶段：绿色，可点击回看
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

function resetStageProgress() {
  // 进度条作为整体导航始终显示，仅清空 step 内部高亮（不再隐藏 nav）
  const root = document.getElementById('stage-progress');
  if (!root) return;
  root.querySelectorAll('.stage-step').forEach(el => {
    el.classList.remove('active', 'done', 'aborted');
  });
}

document.getElementById('stage-progress')?.addEventListener('click', (e) => {
  const step = e.target.closest('.stage-step');
  if (!step) return;
  if (step.classList.contains('aborted')) return;
  // 未到达阶段（aria-disabled）不允许点击切换视图
  if (step.getAttribute('aria-disabled') === 'true') return;
  const stage = step.dataset.stage;
  // tid 优先级：当前内存 > sessionStorage > URL（页面初次加载时三者都可能）
  let tid = currentThreadId
    || sessionStorage.getItem(`workflow:${projectId}`)
    || params.get('threadId');
  // 跳转前把 backend stage 写 sessionStorage（跨页面继承）+ URL（同步可靠源）。
  // sessionStorage 写入 + 读取在某些场景下可能与跳转时机错位，URL 是最稳的兜底。
  let backendForUrl = currentStage;
  if (!backendForUrl || !STAGE_MAP[backendForUrl]) {
    backendForUrl = sessionStorage.getItem(`furthestStage:${projectId}`) || '';
  }
  try {
    if (backendForUrl && STAGE_MAP[backendForUrl]) {
      const k = `furthestStage:${projectId}`;
      const prev = sessionStorage.getItem(k);
      const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
      const newIdx = STAGE_ORDER.indexOf(STAGE_MAP[backendForUrl]);
      if (newIdx > prevIdx) sessionStorage.setItem(k, backendForUrl);
    }
  } catch {}
  const enc = tid ? encodeURIComponent(tid) : '';
  const tidQuery = tid ? `&threadId=${enc}` : '';
  const backendQuery = backendForUrl ? `&backend=${encodeURIComponent(backendForUrl)}` : '';
  const routes = {
    parsing: `/project-init?projectId=${projectId}${tidQuery}&view=outline${backendQuery}`,
    matching: `/project-init?projectId=${projectId}${tidQuery}&view=matching${backendQuery}`,
    generating: tid ? `/workbench?projectId=${projectId}${tidQuery}${backendQuery}` : null,
    reviewing: tid ? `/review?threadId=${enc}${backendQuery}` : null,
    report_review: tid ? `/review?threadId=${enc}${backendQuery}` : null,
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

function markBlockError(blockId, message) {
  const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
  if (!card) return;
  const badge = card.querySelector('.block-status-badge');
  if (badge) {
    badge.className = 'block-status-badge error';
    badge.textContent = '失败';
    if (message) badge.title = message;
  }
  syncOutlineRow(blockId);
}

// ── 模型选择器 ──
function escHtml(s) { return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function loadModels() {
  try {
    const resp = await fetch('/api/configs').then(r => r.json());
    const configs = resp?.configs || [];
    if (!configs.length) { renderDefaultModels(); return; }
    if (!configs.find(c => c.model === selectedModel)) {
      selectedModel = configs[0].model;
      selectedModelLabel = configs[0].name || configs[0].model;
      modelLabel.textContent = selectedModelLabel;
    }
    modelListEl.innerHTML = configs.map(c => {
      const lbl = c.name || c.model;
      return `<div class="model-option${c.model === selectedModel ? ' selected' : ''}" data-model="${escHtml(c.model)}" data-label="${escHtml(lbl)}">
        <span>${escHtml(lbl)}</span>
        ${c.model === selectedModel ? '<span class="model-option-check">✓</span>' : ''}
      </div>`;
    }).join('');
  } catch { renderDefaultModels(); }
  bindModelOptions();
}

function renderDefaultModels() {
  const defaults = [
    { model: 'claude-opus-4-8', label: 'Opus 4.8' },
    { model: 'claude-sonnet-4-6', label: 'Sonnet 4.6' },
    { model: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5' },
  ];
  modelListEl.innerHTML = defaults.map(d => `
    <div class="model-option${d.model === selectedModel ? ' selected' : ''}" data-model="${d.model}" data-label="${d.label}">
      <span>${d.label}</span>
      ${d.model === selectedModel ? '<span class="model-option-check">✓</span>' : ''}
    </div>`).join('');
  bindModelOptions();
}

function bindModelOptions() {
  modelListEl.querySelectorAll('.model-option').forEach(el => {
    el.addEventListener('click', () => {
      selectedModel = el.dataset.model;
      selectedModelLabel = el.dataset.label;
      modelLabel.textContent = selectedModelLabel;
      modelDropdown.classList.remove('open');
      modelListEl.querySelectorAll('.model-option').forEach(o => {
        o.classList.remove('selected');
        o.querySelector('.model-option-check')?.remove();
      });
      el.classList.add('selected');
      el.insertAdjacentHTML('beforeend', '<span class="model-option-check">✓</span>');
    });
  });
}

modelBtn.addEventListener('click', e => {
  e.stopPropagation();
  const isOpen = modelDropdown.classList.toggle('open');
  if (isOpen) loadModels();
});
document.addEventListener('click', () => modelDropdown.classList.remove('open'));
modelDropdown.addEventListener('click', e => e.stopPropagation());
document.getElementById('btn-add-config')?.addEventListener('click', () => openApiModal());

// ── API 配置弹窗 ──
const apiBackdrop   = document.getElementById('api-modal-backdrop');
const apiClose      = document.getElementById('api-modal-close');
const apiCancel     = document.getElementById('btn-api-cancel');
const apiSave       = document.getElementById('btn-api-save');
const btnTestConn   = document.getElementById('btn-test-conn');
const testStatus    = document.getElementById('test-status');
const providerChips = document.getElementById('provider-chips');
const apiBaseUrl    = document.getElementById('api-base-url');
const apiKeyInput   = document.getElementById('api-key');
const apiModelInput = document.getElementById('api-model');
const keyToggle     = document.getElementById('key-toggle');

const PROVIDER_DEFAULTS = {
  claude:   { url: 'https://api.anthropic.com',              model: 'claude-sonnet-4-6', placeholder: 'sk-ant-…' },
  openai:   { url: 'https://api.openai.com/v1',              model: 'gpt-4o',            placeholder: 'sk-…' },
  deepseek: { url: 'https://api.deepseek.com',               model: 'deepseek-v4-pro',   placeholder: 'sk-…' },
  kimi:     { url: 'https://api.moonshot.cn/v1',             model: 'moonshot-v1-8k',    placeholder: 'sk-…' },
  minimax:  { url: 'https://api.minimax.chat/v1',            model: 'abab6.5s-chat',     placeholder: '' },
  doubao:   { url: 'https://ark.cn-beijing.volces.com/api/v3', model: '',                placeholder: '' },
};
let currentProvider = 'claude';
let verifiedOk = false;

function openApiModal() {
  apiBackdrop.classList.add('open');
  modelDropdown.classList.remove('open');
  resetApiForm();
}
function closeApiModal() { apiBackdrop.classList.remove('open'); }
function resetApiForm() {
  currentProvider = 'claude'; verifiedOk = false;
  providerChips.querySelectorAll('.provider-chip').forEach(c =>
    c.classList.toggle('active', c.dataset.provider === 'claude'));
  const d = PROVIDER_DEFAULTS['claude'];
  apiBaseUrl.value = ''; apiBaseUrl.placeholder = d.url;
  apiKeyInput.value = ''; apiKeyInput.placeholder = d.placeholder;
  apiModelInput.value = d.model;
  testStatus.textContent = ''; testStatus.className = 'test-status';
  apiSave.disabled = true;
}

providerChips.addEventListener('click', e => {
  const chip = e.target.closest('.provider-chip');
  if (!chip) return;
  currentProvider = chip.dataset.provider;
  providerChips.querySelectorAll('.provider-chip').forEach(c =>
    c.classList.toggle('active', c === chip));
  const d = PROVIDER_DEFAULTS[currentProvider] || {};
  apiBaseUrl.placeholder = d.url || '';
  apiKeyInput.placeholder = d.placeholder || '';
  apiModelInput.value = d.model || '';
  verifiedOk = false; testStatus.textContent = ''; apiSave.disabled = true;
});

keyToggle.addEventListener('click', () => {
  const show = apiKeyInput.type === 'password';
  apiKeyInput.type = show ? 'text' : 'password';
  keyToggle.textContent = show ? '隐藏' : '显示';
});

btnTestConn.addEventListener('click', async () => {
  const key = apiKeyInput.value.trim();
  const model = apiModelInput.value.trim();
  if (!key || !model) {
    testStatus.textContent = '请填写 API Key 和模型标识';
    testStatus.className = 'test-status fail'; return;
  }
  btnTestConn.classList.add('testing'); btnTestConn.textContent = '验证中…';
  testStatus.textContent = ''; verifiedOk = false; apiSave.disabled = true;
  try {
    const body = { provider: currentProvider, api_key: key, base_url: apiBaseUrl.value.trim() || null, model };
    const addResp = await fetch('/api/configs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
    if (!addResp?.config?.id) throw new Error('保存失败');
    const configId = addResp.config.id;
    const checkResp = await fetch(`/api/configs/${configId}/check`).then(r => r.json());
    if (checkResp?.valid) {
      verifiedOk = true;
      testStatus.textContent = '✓ 验证通过'; testStatus.className = 'test-status ok';
      apiSave.disabled = false; apiSave.dataset.configId = configId;
    } else {
      await fetch(`/api/configs/${configId}`, { method: 'DELETE' });
      testStatus.textContent = '✗ ' + (checkResp?.message || '验证失败');
      testStatus.className = 'test-status fail';
    }
  } catch { testStatus.textContent = '✗ 请求失败，请检查网络'; testStatus.className = 'test-status fail'; }
  btnTestConn.classList.remove('testing'); btnTestConn.textContent = '验证连接';
});

apiSave.addEventListener('click', async () => {
  if (!verifiedOk) return;
  closeApiModal();
  const model = apiModelInput.value.trim();
  selectedModel = model; selectedModelLabel = model;
  modelLabel.textContent = model;
  await loadModels();
});

apiClose.addEventListener('click', closeApiModal);
apiCancel.addEventListener('click', async () => {
  const cid = apiSave.dataset.configId;
  if (cid && !verifiedOk) await fetch(`/api/configs/${cid}`, { method: 'DELETE' }).catch(() => {});
  closeApiModal();
});
apiBackdrop.addEventListener('click', e => { if (e.target === apiBackdrop) closeApiModal(); });


// ── 工具 ──
function escapeHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function extBadge(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  if (['docx','doc'].includes(ext)) return `<span class="mat-badge docx">DOCX</span>`;
  if (ext === 'pdf') return `<span class="mat-badge pdf">PDF</span>`;
  if (['ppt','pptx'].includes(ext)) return `<span class="mat-badge pptx">PPTX</span>`;
  return `<span class="mat-badge">${ext.toUpperCase()}</span>`;
}
function fmtSize(bytes) {
  if (!bytes) return '';
  return bytes > 1024*1024 ? (bytes/1024/1024).toFixed(1)+' MB' : (bytes/1024).toFixed(0)+' KB';
}

// ── 材料列表 ──
// 角色标签来自共享模块 assets/material-roles.mjs（与 node --test 同一份口径），
// 不再在此处另写一套 —— 两套文案必然漂移，而角色决定文件进哪条链路。
const ROLE_LABEL = { ...ROLE_LABEL_SHARED };
async function loadMaterials() {
  const list = await api.materials.list(projectId);
  const reqRoles = ['spec', 'main_rfp', 'requirement', 'scoring', 'evaluation'];
  const reqs = list.filter(m => reqRoles.includes(m.role));
  const srcs = list.filter(m => m.role === 'source');
  lastSrcMaterials = srcs;    // 依据刷新时只重画用量徽标，不重拉列表
  sourceMaterialCount = srcs.length;   // 匹配详情的空状态：一份素材都没有时说清"先上传"
  renderMatchTreeHead();               // 栏头上的「素材 N 份」跟着变
  renderMats(reqStack, reqs, true);
  renderMats(srcStack, srcs, false);
  reqCount.textContent = `${reqs.length} 项`;
  srcCount.textContent = `${srcs.length} 项`;
  // 若有材料正在解析，2.5s 后再拉一次直到全部 done/failed
  const hasPending = list.some(m => m.parse_status === 'pending' || m.parse_status === 'parsing');
  if (hasPending) {
    if (!loadMaterials._t) {
      loadMaterials._t = setTimeout(() => { loadMaterials._t = null; loadMaterials(); }, 2500);
    }
  }
}

// ── 素材视角：这份素材供了哪些章、几条依据（双向视图的另一半）──
// 数据同样来自 blocks.source：按 material_id 聚合（文件名相同的两份素材不能并成一行）。
let lastSrcMaterials = [];
let evidenceIndex = { byId: {}, byFile: {} };

// 索引每次按当前 blocks 重建（O(章节数)，可忽略）；material_id 优先，文件名兜底
function materialUsageHtml(m) {
  const rec = usageFor(evidenceIndex, m);
  if (!rec) {
    return `<span class="mat-usage none" title="尚未作为依据挂到任何章节（没上传到「素材」区、或还没匹配）">未挂载</span>`;
  }
  const chapters = rec.chapters.map(c => c.title).filter(Boolean);
  const tip = chapters.length ? `供：${chapters.join(' / ')}` : '已挂载到章节';
  return `<span class="mat-usage" title="${escapeHtml(tip)}">供 ${rec.chapters.length} 章 · ${rec.count} 条</span>`;
}

function refreshMaterialUsage() {
  if (!lastSrcMaterials.length) return;
  evidenceIndex = evidenceBySource(blocksByOrder);
  const rows = srcStack.querySelectorAll('.mat-row');
  rows.forEach((row, i) => {
    const m = lastSrcMaterials[i];
    if (!m) return;
    row.querySelector('.mat-usage')?.remove();
    const size = row.querySelector('.mat-size');
    const html = materialUsageHtml(m);
    if (size) size.insertAdjacentHTML('beforebegin', html);
    else row.insertAdjacentHTML('beforeend', html);
  });
}
function parseStatusBadge(status) {
  if (status === 'parsing' || status === 'pending') {
    return `<span class="mat-parse-badge parsing" title="正在解析（含 OCR），可继续操作">解析中…</span>`;
  }
  if (status === 'failed') {
    return `<span class="mat-parse-badge failed" title="解析失败，可删除后重传">解析失败</span>`;
  }
  return '';
}
function renderMats(container, mats, showRole) {
  if (!mats.length) { container.innerHTML = ''; return; }
  // showRole 时把 role 渲染为可点击下拉，让用户修改已上传文件类型
  // 只给三类可选：招标文件 / 技术规范书 / 其他（用户要求）。
  // 语义全集 REQUIREMENT_ROLES 仍保留 scoring/evaluation，用于老材料的正确归类显示。
  const roleOptions = SELECTABLE_REQUIREMENT_ROLES.map(r => [r, ROLE_LABEL_SHARED[r]]);
  container.innerHTML = mats.map(m => {
    // 老材料可能是 scoring/evaluation 这类"历史分类"（已不在可选列表里）：
    // 补一个只读的当前项，**如实显示**它现在是什么，而不是悄悄显示成列表第一项。
    const opts = roleOptions.slice();
    if (!opts.some(([v]) => v === m.role)) {
      opts.unshift([m.role, ROLE_LABEL_SHARED[m.role] || m.role]);
    }
    const roleCell = showRole
      ? `<select class="mat-role-select role-${m.role}" data-id="${m.id}" title="修改文件类型">
           ${opts.map(([v, l]) => `<option value="${v}" ${m.role === v ? 'selected' : ''}>${l}</option>`).join('')}
         </select>`
      : '';
    return `<div class="mat-row">
      ${extBadge(m.filename)}
      <span class="mat-name" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</span>
      ${roleCell}
      ${parseStatusBadge(m.parse_status)}
      ${showRole ? '' : materialUsageHtml(m)}
      <span class="mat-size">${fmtSize(m.file_size)}</span>
      <button class="mat-del" data-id="${m.id}" title="删除">×</button>
    </div>`;
  }).join('');
  container.querySelectorAll('.mat-del').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      await fetch(`/api/materials/${btn.dataset.id}`, { method: 'DELETE' });
      await loadMaterials();
    });
  });
  container.querySelectorAll('.mat-role-select').forEach(sel => {
    sel.addEventListener('change', async () => {
      const id = sel.dataset.id;
      const role = sel.value;
      // 改 select 自身配色（与原 badge 同步色调）
      sel.className = `mat-role-select role-${role}`;
      try {
        await fetch(`/api/materials/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role }),
        });
        await loadMaterials();
      } catch (e) { console.error('[mat] update role failed', e); }
    });
  });
}

// ── 状态栏 ──
// 顶栏文案：页面（有 blocks 计数 + SSE 帧）比 checkpoint 更清楚"提炼到哪了"。
// 返回 '' 表示交回共享壳按 stageLabels 兜底（素材库视图 / 闸门 1 等）。
function headerStageLabel(state) {
  const total = Number(totalBlocks) || 0;
  const done = Number(doneBlocks) || 0;
  const failed = failedBlockCount();
  if (state === 'failed') return '本次运行失败';
  if (state === 'extracting') {
    return total ? `提炼要点中（${done}/${total}）` : '提炼要点中';
  }
  if (state === 'paused' && total > 0 && done < total) {
    return failed ? `提炼到一半（${done}/${total}，${failed} 节失败）` : `提炼到一半（${done}/${total}）`;
  }
  if (state === 'done' && document.body.dataset.view !== 'matching') {
    return failed ? `提炼完成（${failed} 节失败，可重试）` : '提炼完成，待确认内容';
  }
  return '';   // gate_outline / gate_materials / 素材库视图：沿用共享壳的词
}

function syncHeaderStageLabel(state) {
  try {
    const next = headerStageLabel(state);
    if (workspace.stageLabel !== next) {
      workspace.stageLabel = next;
      renderWorkspaceChrome();
    }
  } catch (e) { /* 顶栏只是展示，失败不影响主流程 */ }
}

// 状态栏按钮的"当前渲染签名"。为什么要它：`setStatusBar` 在 SSE 帧与轮询里被频繁
// 调用，每次都会 `statusActions.innerHTML = ''` 重建按钮 —— 用户按下的那一瞬间节点
// 可能已被替换，click 落在**已移除的节点**上，表现就是"点了完全没反应"。
// 同签名时只更新文案，不动按钮节点。
let statusActionsKey = '';

function setStatusActions(key, html, bind) {
  if (key === statusActionsKey && statusActions.innerHTML) {
    return;      // 同一组按钮：保留现有节点与监听器
  }
  statusActionsKey = key;
  statusActions.innerHTML = html;
  if (bind) bind();
}

function setStatusBar(state, label, pct, totalB, doneB) {
  extractionState = state;
  statusBar.dataset.state = state;
  statusLabel.textContent = label;
  syncHeaderStageLabel(state);
  // 文案可能被 ellipsis 截断：把全文挂 title，hover 能看到
  statusLabel.title = label || '';
  if (state !== 'extracting' && typeof stopSettlePoll === 'function') stopSettlePoll();
  if (typeof refreshStructureEditable === 'function') refreshStructureEditable();
  // 切出 gate_outline（含 idle / extracting / gate_materials / done）统一回到只读，避免在每个分支重复 freeze
  if (state !== 'gate_outline' && typeof freezeOutlineEdit === 'function') {
    freezeOutlineEdit();
  }
  if (state === 'idle') {
    progressTrack.style.display = 'none';
    statusActionsKey = '';
    statusActions.innerHTML = '';
    return;
  }
  progressTrack.style.display = 'block';
  progressBar.style.width = pct + '%';
  statusPct.textContent = `${doneB}/${totalB} 模块 · ${pct}%`;
  // 注意：**不要**在这里无条件清空按钮。清空 = 每次 setStatusBar 都重建节点，
  // 用户按下的瞬间节点可能刚被替换 → click 落在已移除的节点上，"点了没反应"。
  // 各分支用 setStatusActions(签名, html, bind) 渲染，同签名时只留现有节点。
  if (state === 'extracting') {
    setStatusActions('extracting',
      `<button class="btn-status-secondary" id="btn-cancel"
        title="取消在**当前章节**跑完后生效（正在进行的这一次模型调用不会被打断）">取消</button>`,
      () => document.getElementById('btn-cancel').addEventListener('click', cancelExtraction));
  } else if (state === 'points_pending') {
    // 第①步停留态：唯一该做的就是去生成目录（按钮直接展开「拆解结果」）。
    // 这里**不能**给「确认目录，开始提炼要点」——那会用上一版目录继续提炼（自相矛盾）。
    setStatusActions('points_pending', `
      <button class="btn-status-secondary" id="btn-redraft-outline">重新拆解评分点</button>
      <button class="btn-status-primary" id="btn-gen-outline">生成所选目录 ▸</button>`, () => {
      document.getElementById('btn-redraft-outline')?.addEventListener('click', redraftOutline);
      document.getElementById('btn-gen-outline')?.addEventListener('click', () => {
        splitPanelAutoOpenedFor = 0;
        openSplitReport();
      });
    });
  } else if (state === 'gate_outline') {
    setStatusActions('gate_outline', `
      <button class="btn-status-secondary" id="btn-cancel">取消</button>
      <button class="btn-status-secondary" id="btn-redraft-outline">重新拆分目录</button>
      <button class="btn-status-primary" id="btn-confirm-outline">确认目录，开始提炼要点</button>`, () => {
      document.getElementById('btn-cancel').addEventListener('click', cancelExtraction);
      document.getElementById('btn-redraft-outline').addEventListener('click', redraftOutline);
      document.getElementById('btn-confirm-outline').addEventListener('click', confirmOutlineGate);
    });
    // 闸门 1 只审**目录结构**（增删改/上下移/缩进走每行的「编辑」按钮）；8 字段此刻
    // 还没提炼，没有可编辑内容 —— 这里不再 makeOutlineEditable()，否则只是空转。
    freezeOutlineEdit();
  } else if (state === 'gate_materials') {
    setStatusActions('gate_materials', `
      <button class="btn-status-secondary" id="btn-rematch">重新匹配</button>
      <button class="btn-status-primary" id="btn-confirm-materials">确认素材，进入撰写</button>`, () => {
      document.getElementById('btn-rematch').addEventListener('click', () => startMatching());
      document.getElementById('btn-confirm-materials').addEventListener('click', confirmMaterialsGate);
    });
    freezeOutlineEdit();
  } else if (state === 'done') {
    // 文案随当前视图变：outline 视图是「要点提炼完、等确认内容大纲」，
    // matching 视图是「素材匹配完、等进入撰写」。
    const isMatchingView = document.body.dataset.view === 'matching';
    if (isMatchingView) {
      setStatusActions('done_matching', `
        <button class="btn-status-secondary" id="btn-rematch">重新匹配</button>
        <button class="btn-status-primary" id="btn-confirm">进入撰写</button>`, () => {
        // 与 gate_materials 状态的「确认素材，进入撰写」按钮逻辑保持一致：
        // 先 resume('approve') 推 graph 进 generate，再跳 workbench。
        document.getElementById('btn-confirm').addEventListener('click', confirmMaterialsGate);
      });
      freezeOutlineEdit();
    } else {
      setStatusActions('done_outline', `
        <button class="btn-status-secondary" id="btn-reextract">重新提炼</button>
        <button class="btn-status-primary" id="btn-to-matching">确认内容大纲及要求</button>`, () => {
        document.getElementById('btn-reextract').addEventListener('click', () => confirmReextract());
        document.getElementById('btn-to-matching').addEventListener('click', confirmOutlineContent);
      });
      // 确认前允许修改：这份提炼结果正是用户要审的内容，只读就没法改
      makeOutlineEditable();
    }
  } else if (state === 'failed') {
    // 硬失败（如目录派生失败）：不是「等一会就好」，给明确的重试入口。
    // 线程保留 —— 要求文件已经解析好，重出目录复用的是同一个 thread 的 checkpoint。
    setStatusActions('failed', `
      <button class="btn-status-secondary" id="btn-redraft-outline">重新拆分目录</button>
      <button class="btn-status-primary" id="btn-restart-run">从头重新提炼</button>`, () => {
      // 这里以前绑的是 btn-reextract —— 该分支根本没有这个 id，getElementById 返回 null，
      // 于是「从头重新提炼」点了**必然没反应**（用户报的"点了没反应"之一）。
      document.getElementById('btn-redraft-outline').addEventListener('click', redraftOutline);
      document.getElementById('btn-restart-run').addEventListener('click', () => startExtraction(true));
    });
    freezeOutlineEdit();
  } else if (state === 'paused') {
    const isMatchingView = document.body.dataset.view === 'matching';
    if (isMatchingView) {
      setStatusActions('paused_matching', `
        <button class="btn-status-secondary" id="btn-rematch">重新匹配</button>
        <button class="btn-status-primary" id="btn-resume-match">继续匹配</button>`, () => {
        document.getElementById('btn-rematch').addEventListener('click', () => startMatching());
        document.getElementById('btn-resume-match').addEventListener('click', () => startMatching());
      });
    } else {
      // 「提炼到一半」的按钮组：此时**不该**再出现「确认目录，开始提炼要点」——
      // 目录早就确认过了，用户要做的是把剩下的章节接着提炼（只补剩余、不重复调用），
      // 或者改目录 / 从头再来（这两条才是要付代价的路径）。运行中才有「取消」，
      // 这个状态下已经没有在跑的东西，所以不再放取消。
      const remain = Math.max((totalBlocks || 0) - (doneBlocks || 0), 0);
      const redraftGate = redraftAllowedByStage();
      setStatusActions('paused_outline', `
        <button class="btn-status-secondary" id="btn-reextract">重新提炼</button>
        <button class="btn-status-secondary" id="btn-redraft-outline"
          ${redraftGate.ok ? '' : 'disabled'}
          title="${redraftGate.ok ? '换一版提炼要求重新派生目录'
                               : '本次运行已过闸门，目录不能再改；请用「重新提炼」'}">重新拆分目录</button>
        <button class="btn-status-primary" id="btn-resume-extract"
          title="只提炼还没做的章节；已完成的章节不会重复调用模型">继续提炼${
          remain ? `（${remain} 节）` : ''}</button>`, () => {
        document.getElementById('btn-reextract').addEventListener('click', confirmReextract);
        document.getElementById('btn-redraft-outline').addEventListener('click', redraftOutline);
        // 继续提炼 = resume 同一条 run（跳过已完成的章节），不是重新起一条
        document.getElementById('btn-resume-extract').addEventListener('click', continueExtraction);
      });
    }
    freezeOutlineEdit();
  }
  applyPageView();
}

// ── 右侧进度环 ──
function updateSidebarProgress() {
  const pct = totalBlocks ? doneBlocks / totalBlocks : 0;
  const circumference = 88;
  ringFill.style.strokeDashoffset = circumference * (1 - pct);
  sidebarProgressText.textContent = `${doneBlocks} / ${totalBlocks}`;
}

// ── 目录导航（初始全量构建） ──
function inferTocLevel(s) {
  // 优先用后端解析的真实 level（1-4）
  if (typeof s === 'object' && s && Number.isFinite(s.level)) {
    return Math.max(1, Math.min(4, s.level));
  }
  const title = typeof s === 'string' ? s : (s?.title || '');
  if (/^[一二三四五六七八九十百]+[、．\.]/.test(title)) return 1;
  if (/^第[一二三四五六七八九十百\d]+[章节篇]/.test(title)) return 1;
  if (/^\d+\.\d+/.test(title)) return 3;
  return 2;
}

function buildToc(blocks) {
  sidebarToc.innerHTML = '';
  const rawLevels = blocks.map(b => inferTocLevel(b));
  const minLevel = rawLevels.length ? Math.min(...rawLevels) : 1;
  const normalize = (raw) => Math.max(1, Math.min(4, raw - minLevel + 1));
  blocks.forEach((b, i) => {
    const blockId = b.block_id || b.id;
    const level = normalize(rawLevels[i]);
    const btn = document.createElement('button');
    btn.className = 'toc-item' + (level === 1 ? '' : level >= 3 ? ' content level-' + level : ' content');
    btn.id = `toc-${blockId}`;
    btn.dataset.blockId = blockId;
    if (level === 1) {
      btn.innerHTML = `<span class="toc-chevron">›</span><span class="toc-label" title="${escapeHtml(b.title)}">${escapeHtml(b.title)}</span>`;
    } else {
      btn.innerHTML = `<span class="toc-bullet"></span><span class="toc-label" title="${escapeHtml(b.title)}">${escapeHtml(b.title)}</span><span class="toc-dot"></span>`;
    }
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      // 关闭浮层
      document.getElementById('sidebar-toc').style.display = 'none';
      // 滚动到对应卡片：先展开祖先链，否则折叠卡片的 offsetTop 恒为 0
      expandToBlock(blockId);
      scrollToOutlineNode(blockId);
    });
    sidebarToc.appendChild(btn);
  });
}
function markTocDone(blockId) {
  const btn = document.getElementById(`toc-${blockId}`);
  if (!btn) return;
  btn.classList.add('done');
  const dot = btn.querySelector('.toc-dot');
  if (dot) dot.style.background = '#16a34a';
}

// ── 模块卡片 ──
// 一行目录摘要（row 副标题）：要求为空时整行隐藏
function firstLine(text) {
  const s = String(text ?? '').split('\n').map(l => l.trim()).filter(Boolean);
  return s.length ? s[0] : '';
}

// no_source：这节本来就没有可提炼的原文（用户新增 / 纯目录节点）——**不是失败**，
// 但要和"已完成"区分开，否则用户看到空字段会以为提炼坏了。
const BADGE_TEXT = { pending: '待提炼', extracting: '提炼中', done: '已完成 ✓',
                     error: '提炼失败', no_source: '无需提炼' };



function renderBlockCard(block, status = 'pending') {
  // 编辑器模式不渲染：共享宿主此刻归 mode-editor 用，这里画会把写作版目录覆盖掉
  // （反向同理，见 workbench.js:renderOutline）。
  if (document.body.dataset.view === 'editor') return;
  blocksEmpty.style.display = 'none';
  const blockId = block.block_id || block.id;
  const el = document.createElement('div');
  el.className = 'block-card';
  el.dataset.blockId = blockId;
  const badgeText = BADGE_TEXT[status] || status;
  el.innerHTML = `
    <div class="block-card-header">
      <span class="block-card-title">${escapeHtml(block.title)}</span>
      <span class="block-status-badge ${status}" ${block.error ? `title="${escapeHtml(block.error)}"` : ''}>${badgeText}</span>
    </div>
    <div class="block-card-progress ${status==='extracting'?'active':''}">
      <div class="block-card-progress-bar"></div>
    </div>
    <div class="block-card-body">${
      // 闸门 1「解析目录」阶段：只有结构，没有 8 字段（见 mountOutlineFields 注释）
      status === 'pending' ? '' : renderFieldsGrid(block)
    }</div>`;

  // 目录树节点：row 一行摘要（始终可见）+ 卡片（默认折叠）。
  // 卡片仍是 .block-card[data-block-id]，闸门 1 的编辑工具按原契约定位它。
  // 行标记来自共享组件（与「方案编辑」同一份）。level 仍用本模块的推断兜底：
  // 老数据里 block.level 可能缺失，inferTocLevel 按标题编号推。
  const level = Number.isFinite(block.level)
    ? Math.max(1, Math.min(4, block.level))
    : inferTocLevel(block);
  const { node, row } = chapterNode({ ...block, level }, {
    sub: firstLine(block.requirement),
    badgesHtml: chapterBadgesHtml(block, status),
    showEdit: true,
  });
  row.addEventListener('click', (e) => {
    // 结构编辑按钮不能触发折叠/展开，否则点一下按钮顺带把卡片收起
    if (e.target.closest('.ot-act')) return;
    // 素材库视图：行 = 目录项 —— 点它只"选中"（右侧看详情），不再顺手展开 8 字段。
    // 展开/收起是行首 › 的职责，否则"扫目录"和"读表单"两个动作永远缠在一起。
    if (document.body.dataset.view === 'matching' && !e.target.closest('.ot-chevron')) return;
    toggleOutlineNode(blockId);
  });
  row.querySelector('.ot-actions').addEventListener('click', (e) => {
    e.stopPropagation();
    if (e.target.closest('.ot-edit-toggle')) toggleOutlineEditMode(blockId);
  });
  node.appendChild(el);

  const host = blocksList.querySelector('.outline-tree') || blocksList;
  host.appendChild(node);
}
// ── 响应矩阵 8 字段：渲染与提交的**唯一定义** ──
// 卡片网格、SSE 结果更新、提炼结果确认页的编辑提交三处都从这里取字段表，避免各写
// 一份顺序、或与后端 OutlineMatrixRow / blocks 表的列名对不上（历史上 UI 只认 7 个，
// constraint_level 从未被渲染，而它偏偏决定正文字数档）。
//
// ⚠️ 顺序不能乱：syncOutlineRow 取网格里**第一个** .block-field 当目录行摘要
//（= 应标要求），所以新增字段只能追加在末尾。
const OUTLINE_FIELDS = [
  { key: 'requirement',       label: '应标要求', cls: '' },
  { key: 'key_points',        label: '应标重点', cls: '' },
  { key: 'veto_items',        label: '● 否决项', cls: 'veto' },
  { key: 'bonus_items',       label: '★ 加分项', cls: 'bonus' },
  { key: 'score_items',       label: '☆ 评分项', cls: 'score' },
  { key: 'evidence_required', label: '证明材料', cls: '' },
  { key: 'indicators',        label: '量化指标', cls: '' },
  // 第 8 个：三选一枚举，后端存英文（models.py:65），界面显示中文
  { key: 'constraint_level',  label: '强制等级', cls: '', constraint: true },
];

// 8 字段里的 constraint_level：三档枚举定义在 workspace-model.mjs（与 workbench 共用）

// 已提炼但该字段为空 → 显示「—」（不显示骨架条：骨架会被读成"还在提炼"）。
function renderField(label, value, cls, key = '') {
  const isEmpty = !value;
  let content;
  if (isEmpty) {
    content = '<div class="block-field-empty">—</div>';
  } else {
    const lines = value.split('\n').map(l => l.trim()).filter(Boolean);
    if (lines.length <= 1) {
      content = `<div class="block-field-value">${escapeHtml(value)}</div>`;
    } else {
      content = `<ul class="block-field-list">${lines.map(l => `<li>${escapeHtml(l)}</li>`).join('')}</ul>`;
    }
  }
  return `<div class="block-field" data-field-key="${key}">
    <div class="block-field-label ${cls}">${label}</div>
    ${content}
  </div>`;
}

// 强制等级：默认 disabled —— 只有 makeOutlineEditable()（提炼结果确认页）解禁，
// 与文本字段的 contenteditable 共用同一套开关（见 makeOutlineEditable/freezeOutlineEdit）。
function renderConstraintField(value) {
  const cur = CONSTRAINT_LEVELS.some(o => o.value === value) ? value : 'recommended';
  const opts = CONSTRAINT_LEVELS.map(o =>
    `<option value="${o.value}"${o.value === cur ? ' selected' : ''}>${o.label}</option>`).join('');
  return `<div class="block-field" data-field-key="constraint_level">
    <div class="block-field-label">强制等级</div>
    <select class="block-field-select" data-constraint-level disabled>${opts}</select>
  </div>`;
}

function renderFieldsGrid(row) {
  const r = row || {};
  const cells = OUTLINE_FIELDS.map(f => f.constraint
    ? renderConstraintField(r[f.key])
    : renderField(f.label, r[f.key], f.cls, f.key)).join('');
  return `<div class="block-card-grid">${cells}</div>`;
}

// 挂载/替换卡片的 8 字段网格。
// 「解析目录」阶段（pending）的卡片**不带**网格：那时 8 字段一个都还没有，渲染出来
// 只是空噪声；提炼中挂单行占位（见 mountExtractingPlaceholder）；结果到达时换成它。
function mountOutlineFields(card, row) {
  const body = card?.querySelector('.block-card-body');
  if (!body) return null;
  body.innerHTML = renderFieldsGrid(row);
  return body.querySelector('.block-card-grid');
}

// ── 素材匹配工作区（素材库视图）────────────────────────────────
// 设计见 docs/superpowers/plans/2026-09-21-chapter-centric-workspace-design.md：
// 左＝素材池（用量在素材列表行上）｜中＝章节树（筛选 + 徽标）｜右＝当前章节的匹配详情。
// 数据源与撰写、评审**同一份**：blocks.source（匹配节点写的依据）+ 应答条目（后端展开）。
//
// 为什么要有这一屏：匹配跑完以前只有章节行上一个「依据 N」徽标 —— 用户看不到
// "哪章缺依据、缺哪几条、哪份素材没用上"，也不知道匹配到底做了没有（实测反馈）。
let responseItemsByBlock = {};   // { blockId: [条目…] }，来自 GET /projects/{id}/response-items
let chapterEvidence = {};        // { blockId: {total, items, files, evidence} }（模型层投影）
let matchOverview = null;        // GET /projects/{id}/match-overview（总览 + 覆盖矩阵）
let matchOverviewByChapter = {}; // { blockId: {items_covered, items_total, …} }（总览的按章索引）
let matchFilter = 'all';         // 章节树筛选：all | without_evidence | unwritten | pending
let matchSearch = '';            // 目录搜索（与「方案编辑」同款搜索框同位置）
let selectedMatchBlockId = null; // 右侧详情正在看哪一章
let sourceMaterialCount = 0;     // 素材池里有几份原始素材（空状态判定用）

// 行内徽标：章节状态 +「依据 N」。
// 状态文案来自 workspace-model 的 chapterBadges（与筛选、覆盖矩阵、撰写页同一份判定）；
// 但**提炼中是瞬态**，SSE 帧比 blocks 行更新得快，所以那一档以入参 status 为准。
// 类名沿用 `block-status-badge {status} ot-badge`：setBlockBadge/updateBlockCard 按它找节点。
// 本模块只负责**取数**（依据数 / 条目覆盖来自各自的缓存），拼装交给共享实现，
// 于是「要求与大纲 / 素材库 / 方案编辑」三处的徽标标记只有一份。
function chapterBadgesHtml(block, status = 'pending') {
  const id = String(block?.block_id ?? block?.id ?? '');
  // 索引在 loadExistingBlocks 后半段才建好，首屏先按 block 自身解析一份：
  // 否则「依据 N」要等下一次重绘才出现（实测刷完页面第一眼看不到依据数）。
  const n = chapterEvidence[id] ? chapterEvidence[id].total : parseEvidence(block).length;
  const coverage = chapterCoverage(id);
  return sharedBadgesHtml(block, { evidenceCount: n, coverage, status });
}

// 条目覆盖 {blockId: {covered, total}}：优先用后端总览（它按"条目 id 是否存在"去重），
// 总览还没回来时就地算一份，避免行上数字闪一下再变。
function chapterCoverage(blockId) {
  const id = String(blockId);
  const ov = matchOverviewByChapter[id];
  if (ov && ov.actionable_items_total != null) {
    // 分母只算"要回应的事项"（排除评分口径）—— 与右侧详情、顶部总览同一个口径，
    // 否则同一页会出现 3/28 与 2/10 两个数（实测用户就是被这种不一致绕晕的）
    return { covered: ov.actionable_items_covered, total: ov.actionable_items_total };
  }
  const items = (responseItemsByBlock[id] || []).filter(it => !it.informative);
  const evs = parseEvidence(blocksById.get(id));
  const covered = items.filter(it => evs.some(e => (e.item_ids || []).includes(it.item_id))).length;
  return { covered, total: items.length };
}

function evidenceCountOf(blockId) {
  return (chapterEvidence[String(blockId)]?.total) || 0;
}

// ── 顶部总览：33 章 / 165 条要求 / 有依据 128 / 无依据 37（信息性，不阻断撰写）──
function renderMatchSummary() {
  const el = document.getElementById('match-summary');
  if (!el) return;
  if (!totalBlocks) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  const ov = matchOverview;
  const stat = ov
    ? `<span>共 <span class="ms-num">${ov.chapters.total}</span> 章</span>
       <span class="ms-dim" title="展开出的全部条目；其中「要回应的事项」不包含评分口径">条目 <span class="ms-num">${ov.requirements.total}</span> 条</span>
       <span>有依据章节 <span class="ms-num">${ov.chapters.with_evidence}</span></span>
       <span class="${ov.chapters.without_evidence ? '' : 'ms-dim'}">无依据章节 <span class="ms-num">${ov.chapters.without_evidence}</span></span>
       <span class="ms-dim" title="要回应的事项（不含评分口径）里已有素材依据的条数">要回应事项覆盖 <span class="ms-num">${ov.requirements.actionable_covered}/${ov.requirements.actionable_total}</span></span>
       <span class="ms-dim">${ov.materials.length} 份素材 · 依据 ${ov.evidence.total} 条</span>`
    : `<span class="ms-dim">正在统计匹配总览…</span>`;
  el.innerHTML = `${stat}
    <span class="ms-chips">
      <button class="chap-btn" id="ms-matrix">覆盖矩阵</button>
      <button class="chap-btn" id="ms-risk" title="按评分点汇总审查结论：哪些评分点的应答还不达标、缺什么">得分风险</button>
      <button class="ms-primary" id="ms-match" title="按当前章节结构把素材归属到条目（零模型调用，只处理新上传的素材）">分析素材归属</button>
    </span>`;
}

// 目录栏头：它才是"过滤这个目录"的地方（筛选 chips 从顶部总览条挪到这里，
// 顶部只留全局数字）。顺带给"展开全部/收起全部"一个显式入口 —— 收起的目录
// 需要一条通路去核对 8 字段。
function renderMatchTreeHead() {
  const el = document.getElementById('match-tree-head');
  if (!el) return;
  if (!totalBlocks) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  const counts = chapterFilterCounts(blocksByOrder, chapterEvidenceCounts());
  const chip = (mode, label) =>
    `<button class="chap-chip" role="tab" data-filter="${mode}" aria-selected="${matchFilter === mode}">` +
    `${label}<span class="chap-chip-count">${counts[mode]}</span></button>`;
  const shown = counts[matchFilter] ?? counts.all;
  const anyOpen = !!blocksList.querySelector('.ot-node.open');
  el.innerHTML = `
    <div class="chap-head">
      <span class="chap-head-title">章节目录 <b>${shown}</b>${matchFilter === 'all' ? '' : ` / ${counts.all}`}</span>
      <span class="chap-head-actions">
        <button class="chap-btn" data-act="toggle-all">${anyOpen ? '收起全部' : '展开全部'}</button>
        <button class="chap-btn icon" data-act="collapse-lane" title="收起目录（匹配详情占满整个宽度）">«</button>
      </span>
    </div>
    <div class="chap-head" style="padding-top:0;">
      <span class="chap-head-title">素材 <b>${sourceMaterialCount}</b> 份</span>
      <span class="chap-head-actions">
        <button class="chap-btn" data-act="upload-src">＋ 上传</button>
        <button class="chap-btn" data-act="mat-list">清单</button>
      </span>
    </div>
    <div class="chap-chips">
      ${chip('all', '全部')}${chip('without_evidence', '无依据')}
      ${chip('unwritten', '未撰写')}${chip('pending', '待提炼')}
    </div>
    <div class="chap-search">
      <input type="search" id="match-chapter-search" placeholder="搜索章节名称" value="${escapeHtml(matchSearch)}">
    </div>`;
}

function chapterEvidenceCounts() {
  const out = {};
  for (const [bid, rec] of Object.entries(chapterEvidence)) out[bid] = rec.total;
  return out;
}

// 筛选只做显示/隐藏：节点（含 id、编辑面板、展开态）全部留在树上，
// 重建 DOM 会丢掉用户正在编辑的字段与折叠记忆。
function applyMatchFilter() {
  // 切回大纲视图要把筛选放开：否则筛选态残留会让大纲视图少显示几章
  if (document.body.dataset.view !== 'matching') {
    blocksList.querySelectorAll('.ot-node.mw-hidden').forEach(n => n.classList.remove('mw-hidden'));
    return;
  }
  let hits = filterChapters(blocksByOrder, { mode: matchFilter, evidenceByBlock: chapterEvidenceCounts() });
  const q = matchSearch.trim().toLowerCase();
  if (q) hits = hits.filter(b => String(b.title || '').toLowerCase().includes(q));
  hits = hits.map(b => String(b.block_id ?? b.id));
  // 命中节点的祖先链要一起留着：树是嵌套的，父节点一隐藏，命中的子节点也跟着消失
  // （实测点「无依据」整栏空白）。层级用 blocksByOrder 的树序 + level 推导。
  const visible = expandVisibleWithAncestors(
    blocksByOrder.map(b => ({ id: String(b.block_id ?? b.id), level: b.level })), hits);
  blocksList.querySelectorAll('.ot-node').forEach(node => {
    node.classList.toggle('mw-hidden', !visible.has(String(node.dataset.blockId)));
  });
  // 筛选后命中的可能是深层节点：父节点都展开，否则"看得见"却"展不开"
  if (matchFilter !== 'all') {
    blocksList.querySelectorAll('.ot-node').forEach(n => n.classList.add('open'));
  }
}

// ── 章节树：选中 → 右侧详情 ──
function bindMatchSelection() {
  blocksList.addEventListener('click', (e) => {
    if (document.body.dataset.view !== 'matching') return;
    const row = e.target.closest?.('.ot-row');
    if (!row || e.target.closest('.ot-act')) return;
    const node = row.closest('.ot-node');
    if (node) selectMatchChapter(String(node.dataset.blockId));
  });
}

function selectMatchChapter(blockId, { keepOpen = false } = {}) {
  selectedMatchBlockId = String(blockId);
  // 告诉共享壳"当前在哪一章"：顶部四个模块的跳转链接会带上它，
  // 切到方案编辑 / 质量检查时直接落到同一章（不用再找一遍）。
  workspace.selectedBlockId = selectedMatchBlockId;
  try {
    const url = new URL(location.href);
    url.searchParams.set('blockId', selectedMatchBlockId);
    history.replaceState({}, '', url);
  } catch {}
  // 顶部导航的链接也要跟着更新（它们带着 blockId 跳到方案编辑 / 质量检查）
  if (typeof renderWorkspaceChrome === 'function') renderWorkspaceChrome();
  blocksList.querySelectorAll('.ot-node').forEach(n =>
    n.classList.toggle('mw-selected', n.dataset.blockId === selectedMatchBlockId));
  // **不**自动展开卡片：匹配页的目录默认收起，点行 = 右侧看详情。
  // 想核对 8 项提炼字段就点行首的 ›（展开态记在匹配页自己的记忆里，不影响大纲页）。
  scrollToOutlineNode(selectedMatchBlockId);
  renderMatchDetail();
}

// ── 右侧详情：应答条目 × 依据（可人工挂接 / 解绑）──
function renderMatchDetail() {
  const el = document.getElementById('match-detail');
  if (!el) return;
  if (document.body.dataset.view !== 'matching') { el.hidden = true; return; }
  el.hidden = false;

  const bid = selectedMatchBlockId;
  const block = bid ? blocksById.get(bid) : null;
  if (!bid || !block) {
    el.innerHTML = `<div class="mw-empty">
      从左侧选一个章节，这里显示它的<b>应答要求条目</b>与<b>素材依据</b>。<br>
      <span style="font-size:12px;">条目上「找素材」可以人工挂接；挂错了点「解绑」。</span>
    </div>`;
    return;
  }

  const items = responseItemsByBlock[bid] || [];
  const evCount = evidenceCountOf(bid);
  const stage = chapterStage(block, { evidenceCount: evCount });
  // 覆盖度只看"要回应的事项"：评分口径条目（优良 7-10 分 / 满分 10 分）永远没有
  // 素材依据，算进去只会把分母撑大、把用户吓一跳（实测反馈"这部分没看懂"）。
  const actionable = items.filter(it => !it.informative);
  const informative = items.length - actionable.length;
  const covered = actionable.filter(it => {
    return parseEvidence(block).some(e => (e.item_ids || []).includes(it.item_id));
  }).length;

  const emptyPool = sourceMaterialCount === 0
    ? `<div class="mw-empty" style="padding:20px 8px;text-align:left;">
         还没有可匹配的原始素材。<br>
         <span style="font-size:12px;">点左上角「＋ 上传」加方案 / 产品资料，再点右上角「分析素材归属」。</span>
       </div>`
    : '';

  // 这一屏到底在说什么：一句话讲清（用户看不懂的根源是界面只给数据、不给解释）
  const legend = `<div class="mw-legend">
    <b>这一屏是本章"要回应的事项"与它们的素材依据。</b>
    没有依据的条目<b>不阻断撰写</b>，撰写时会直接按招标要求作答；
    ${informative ? `另有 ${informative} 条评分口径（评委打分用），已单独归到下面。` : ''}
  </div>`;
  const noEvidence = evCount === 0
    ? `<div class="mw-empty" style="padding:10px 8px;text-align:left;">
         本章 <b>没有匹配到素材</b> —— 不阻断撰写，撰写阶段会直接按应答要求来写。<br>
         <span style="font-size:12px;">也可以点下面任意条目的「找素材」手动挂一段。</span>
       </div>`
    : '';

  const body = renderEvidenceSectionPure(block, items, {
    open: true,          // 匹配工作区里依据默认展开：只看一行标题等于没看到依据
    foldUncovered: true, // 未覆盖的条目默认收成一行（25/28 条无依据时逐条铺开就是噪声）
    itemActions: (it) =>
      `<button class="mw-btn" data-act="find" data-item="${escapeHtml(it.item_id)}">找素材</button>`,
    rowActions: (e) =>
      `<button class="mw-btn" data-act="unmount" data-chunk="${e.chunk_id}">解绑</button>`,
  });

  el.innerHTML = `
    <div class="mw-head">
      <div class="mw-title">${escapeHtml(block.title || bid)}</div>
      <div class="mw-meta">
        <span class="ot-ev stage">${CHAPTER_STAGE_LABELS[stage] || stage}</span>
        <span title="本章挂到的素材片段条数">依据 <b>${evCount}</b> 条</span>
        ${actionable.length
          ? `<span title="要回应的事项里，已找到素材依据的条数">要回应 <b>${covered}/${actionable.length}</b> 条有依据</span>`
          : '<span class="ms-dim">本章未展开应答条目</span>'}
        ${informative ? `<span class="ms-dim" title="评分口径/量化档位，说明性，不需要找素材">评分口径 ${informative} 条</span>` : ''}
        <span style="margin-left:auto;">
          <button class="mw-btn" data-act="find" data-item="">+ 挂整章素材</button>
        </span>
      </div>
    </div>
    <div class="mw-body">
      ${emptyPool}${noEvidence}${legend}
      ${body || ''}
      <div class="mw-picker-slot" data-slot=""></div>
    </div>`;
}

// ── 「找素材」：从素材池里挑一段挂到这条要求上 ──
// 交互按用户确认的口径：**条目行 → 找素材 → 从素材池选**（可发现性优先，不用拖拽）。
function previewOneLine(text, max = 90) {
  const one = String(text || '').split(String.fromCharCode(10)).map(l => l.trim())
    .filter(Boolean).join(' · ');
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

async function openChunkPicker(itemId, itemText) {
  const slot = document.querySelector('#match-detail .mw-picker-slot');
  if (!slot) return;
  if (slot.dataset.open === itemId) { slot.innerHTML = ''; slot.dataset.open = ''; return; }
  slot.dataset.open = itemId;
  slot.innerHTML = `<div class="mw-picker"><div class="ms-dim" style="font-size:12px;">正在读取素材…</div></div>`;
  let chunks = [];
  try {
    const res = await api.materials.materialChunks(projectId, { limit: 300 });
    chunks = res?.chunks || [];
  } catch (err) {
    console.error('[match] 读取素材失败', err);
  }
  const mounted = new Set(parseEvidence(blocksById.get(selectedMatchBlockId)).map(e => e.chunk_id));
  slot.innerHTML = `<div class="mw-picker">
    <div class="mw-picker-head">
      <input type="search" placeholder="在素材里搜关键词…" data-role="q">
      <button class="mw-btn" data-act="close-picker">收起</button>
    </div>
    <div data-role="list">${chunks.length ? chunks.map(c => `
      <div class="mw-chunk${mounted.has(c.chunk_id) ? ' mounted' : ''}" data-chunk="${c.chunk_id}"
           title="${escapeHtml(c.preview || '')}">
        <span class="mw-chunk-file">${escapeHtml(c.filename)}${c.page ? ` P${c.page}` : ''}</span>
        <span class="mw-chunk-text">${escapeHtml(previewOneLine(c.preview))}</span>
      </div>`).join('') : '<div class="ms-dim" style="padding:8px;font-size:12px;">素材池是空的：先在左侧上传原始素材。</div>'}</div>
  </div>`;
  const input = slot.querySelector('[data-role="q"]');
  input?.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    slot.querySelectorAll('.mw-chunk').forEach(node => {
      node.style.display = node.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
}

// 挂接 / 解绑：写库后**以库为准**重绘（同一次操作会同时改 blocks.source 与 checkpoint，
// 只改本地副本迟早发散）。
async function editEvidence({ action, chunkId, itemIds }) {
  const block = blocksById.get(selectedMatchBlockId);
  if (!block) return;
  const rowId = Number(block.id);
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  try {
    await api.blocks.updateEvidence(rowId, { action, chunkId, itemIds, threadId: tid });
  } catch (err) {
    console.error('[match] 素材挂接失败', err);
    setStatusBar('failed', `素材挂接失败：${err.message}`, 0, totalBlocks, doneBlocks);
    return;
  }
  await loadExistingBlocks({ silent: true, keepSelection: true });
}

function bindMatchDetailActions() {
  const el = document.getElementById('match-detail');
  if (!el) return;
  el.addEventListener('click', async (e) => {
    // 未覆盖条目的展开/收起（模型层产出的 .be-uncovered）
    const unc = e.target.closest?.('.be-uncovered-head');
    if (unc && el.contains(unc)) {
      const box = unc.closest('.be-uncovered');
      const body = box.querySelector('.be-uncovered-body');
      body.hidden = !body.hidden;
      box.classList.toggle('open', !body.hidden);
      return;
    }
    // 依据区的展开/收起（.be-head 是模型层产出的，事件委托挂在这里）
    const head = e.target.closest?.('.be-head');
    if (head && el.contains(head)) {
      head.closest('.block-evidence')?.classList.toggle('open');
      return;
    }
    const btn = e.target.closest?.('[data-act]');
    if (btn) {
      const act = btn.dataset.act;
      if (act === 'close-picker') {
        const slot = el.querySelector('.mw-picker-slot');
        slot.innerHTML = ''; slot.dataset.open = '';
        return;
      }
      if (act === 'find') {
        btn.disabled = true;
        await openChunkPicker(btn.dataset.item || '', btn.closest('.be-group-head')?.querySelector('.be-group-text')?.textContent || '');
        btn.disabled = false;
        return;
      }
      if (act === 'unmount') {
        btn.disabled = true;
        await editEvidence({ action: 'unmount', chunkId: Number(btn.dataset.chunk) });
        return;
      }
    }
    const chunk = e.target.closest?.('.mw-chunk');
    if (chunk) {
      const slot = el.querySelector('.mw-picker-slot');
      const itemId = slot?.dataset.open || '';
      chunk.classList.add('mounted');
      await editEvidence({
        action: 'mount',
        chunkId: Number(chunk.dataset.chunk),
        itemIds: itemId ? [itemId] : [],
      });
    }
  });
}

// 顶部总览条：覆盖矩阵 + 主按钮「分析素材归属」
// （素材库视图里左栏整条收起，所以匹配入口必须在这一屏的顶部，否则无处可点）
function bindMatchSummary() {
  const el = document.getElementById('match-summary');
  if (!el) return;
  el.addEventListener('click', (e) => {
    if (e.target.closest?.('#ms-matrix')) { toggleCoverageMatrix(); return; }
    if (e.target.closest?.('#ms-risk')) { toggleScoringRisk(); return; }
    if (e.target.closest?.('#ms-match')) startMatching();
  });
}

// 素材清单抽屉：复用素材面板那一份渲染（renderMats），所以「供 N 章 · M 条」、
// 解析状态、删除按钮都在；改动后两边一起刷（see loadMaterials）。
function renderMatDrawer() {
  const el = document.getElementById('mat-drawer');
  if (!el || el.hidden) return;
  el.innerHTML = `<div class="mat-drawer-head">
      <span>原始素材 <b>${sourceMaterialCount}</b> 份</span>
      <button class="mw-btn" data-act="upload-src">＋ 上传素材</button>
      <button class="mw-btn" data-act="close-drawer">收起</button>
    </div>
    <div class="mat-drawer-list" id="mat-drawer-list"></div>`;
  const list = el.querySelector('#mat-drawer-list');
  if (!lastSrcMaterials.length) {
    list.innerHTML = `<p class="ms-dim" style="font-size:12px;padding:8px;">
      还没有原始素材。上传方案 / 产品资料后点「分析素材归属」，系统会把它们挂到对应章节。</p>`;
    return;
  }
  renderMats(list, lastSrcMaterials, false);
}

function toggleMatDrawer(force) {
  const el = document.getElementById('mat-drawer');
  if (!el) return;
  const next = force === undefined ? el.hidden : !!force;
  el.hidden = !next;
  renderMatDrawer();
}

// 目录栏头：筛选 + 展开/收起全部
function bindMatchTreeHead() {
  const el = document.getElementById('match-tree-head');
  if (!el) return;
  // 搜索：只重算可见性，不整块重绘（重绘会让输入框丢焦点）
  el.addEventListener('input', (e) => {
    if (e.target?.id === 'match-chapter-search') { matchSearch = e.target.value; applyMatchFilter(); }
  });
  el.addEventListener('click', (e) => {
    if (e.target.closest?.('[data-act="collapse-lane"]')) { setLaneCollapsed(true); return; }
    if (e.target.closest?.('[data-act="upload-src"]')) { fileInputSrc.click(); return; }
    if (e.target.closest?.('[data-act="mat-list"]')) { toggleMatDrawer(); return; }
    if (e.target.closest?.('[data-act="close-drawer"]')) { toggleMatDrawer(false); return; }
    if (e.target.closest?.('[data-act="toggle-all"]')) {
      const open = !blocksList.querySelector('.ot-node.open');
      blocksList.querySelectorAll('.ot-node').forEach(n => n.classList.toggle('open', open));
      writeOpenSet(open ? new Set(blocksByOrder.map(b => String(b.block_id ?? b.id))) : new Set());
      renderMatchTreeHead();
      return;
    }
    const chipEl = e.target.closest?.('.chap-chip');
    if (!chipEl) return;
    matchFilter = chipEl.dataset.filter || 'all';
    renderMatchTreeHead();
    applyMatchFilter();
    // 选中的章节被筛掉了：清掉详情，免得"目录里看不到、右侧还在显示"
    if (selectedMatchBlockId &&
        document.querySelector(`.ot-node.mw-hidden[data-block-id="${selectedMatchBlockId}"]`)) {
      selectedMatchBlockId = null;
      renderMatchDetail();
    }
  });
}

// 目录栏收起/展开：与「方案编辑」的左栏同一套交互（44px 竖条 + » 恢复，状态记 localStorage）
function setLaneCollapsed(collapsed) {
  document.body.classList.toggle('match-lane-collapsed', !!collapsed);
  try { localStorage.setItem('matching:laneCollapsed', collapsed ? '1' : '0'); } catch {}
}

function bindMatDrawer() {
  const el = document.getElementById('mat-drawer');
  if (!el) return;
  el.addEventListener('click', (e) => e.stopPropagation());
  el.addEventListener('click', (e) => {
    if (e.target.closest?.('[data-act="upload-src"]')) { fileInputSrc.click(); return; }
    if (e.target.closest?.('[data-act="close-drawer"]')) toggleMatDrawer(false);
  });
  // 点别处收起：浮层开着会盖住目录，用户点目录其实就是想关掉它
  document.addEventListener('click', (e) => {
    if (el.hidden) return;
    if (e.target.closest?.('#mat-drawer') || e.target.closest?.('[data-act="mat-list"]')) return;
    toggleMatDrawer(false);
  });
  // Esc 收起
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el.hidden) toggleMatDrawer(false);
  });
}

// 单节点展开/收起后同步栏头文案（展开全部 ⇄ 收起全部）
function bindOutlineToggleSync() {
  blocksList.addEventListener('click', (e) => {
    if (e.target.closest?.('.ot-chevron')) setTimeout(renderMatchTreeHead, 0);
  });
}

// 匹配总览 + 章节依据索引：**一次请求拿到全部数字**（后端零调用聚合）。
// 树上的徽标、筛选计数、顶部总览、覆盖矩阵都从这一份数据来 —— 以前这些数字
// 各自从 blocks 里现算，同一章在总览和树上可能显示不同的数。
async function loadMatchOverview() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  try {
    matchOverview = await api.materials.matchOverview(projectId, tid);
  } catch (err) {
    console.warn('[match] 匹配总览加载失败（树与筛选会退化为本地计数）', err);
    matchOverview = null;
  }
  matchOverviewByChapter = {};
  for (const c of matchOverview?.chapters_detail || []) {
    matchOverviewByChapter[String(c.block_id)] = c;
  }
  return matchOverview;
}

// 本地也维护一份 {blockId: {total}}：筛选与徽标要能**同步**算（接口回来前不闪空白）
function refreshChapterEvidenceIndex() {
  chapterEvidence = evidenceByChapter(blocksByOrder);
}

/**
 * **得分风险面板**：把逐章审查结论按评分点聚合后的决策视图。
 *
 * 回答的问题不是"素材够不够"，而是你链条里最后那一环：
 * 「这几个评分点，哪些的应答还不达标？分别缺什么？」
 * 与覆盖矩阵并列放在素材库视图的右栏（同一个 `#match-detail` 容器，互斥切换）。
 */
async function toggleScoringRisk() {
  const el = document.getElementById('match-detail');
  if (!el) return;
  if (el.dataset.risk === '1') { el.dataset.risk = ''; renderMatchDetail(); return; }
  el.dataset.risk = '1';
  el.dataset.matrix = '';
  el.innerHTML = `<div class="mw-empty">正在汇总得分风险…</div>`;
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  let data = null;
  try { data = await api.workflow.scoringRisk(projectId, tid); } catch (e) { data = null; }
  const rows = (data?.points || []).map(p => `
    <tr data-point="${escapeHtml(p.point_id)}">
      <td><span class="ov-tag ${p.risk}">${escapeHtml(p.risk_label || p.risk)}</span></td>
      <td>${escapeHtml(p.factor || '')}<div style="color:var(--muted);font-size:11.5px;">${escapeHtml(p.item || '')}</div></td>
      <td>${p.points != null ? p.points : '—'}</td>
      <td>${p.chapters_reviewed}/${p.chapters_total}</td>
      <td>${(p.missing_points || []).slice(0, 4).map(escapeHtml).join('；') || '<span style="color:var(--muted)">—</span>'}</td>
    </tr>`).join('');
  const s = data?.summary || {};
  el.innerHTML = `
    <div class="mw-head">
      <div class="mw-title">得分风险（按评分点）</div>
      <div class="mw-meta">
        <span>不满足 ${s.high || 0}</span>
        <span>有缺口 ${s.medium || 0}</span>
        <span>未审查 ${s.unreviewed || 0}</span>
        <span>正常 ${s.ok || 0}</span>
        <span style="margin-left:auto;"><button class="mw-btn" data-act="close-risk">返回详情</button></span>
      </div>
    </div>
    <div class="mw-body">
      ${rows ? `<table class="ov-risk-table">
        <thead><tr><th>风险</th><th>评分点</th><th>分值</th><th>已审章节</th><th>缺口（未响应的得分要点）</th></tr></thead>
        <tbody>${rows}</tbody></table>` : '<div class="mw-empty">还没有审查结论。先在「方案编辑」生成正文，再跑审查。</div>'}
    </div>`;
  el.querySelector('[data-act="close-risk"]')?.addEventListener('click', () => {
    el.dataset.risk = ''; renderMatchDetail();
  });
}

// 覆盖矩阵（跨章节一屏看全）：行 = 章节，列 = 提炼 / 依据 / 条目覆盖 / 正文。
// 挂在素材库视图上：它回答的正是"哪些章节还没找到素材"。
function toggleCoverageMatrix() {
  const el = document.getElementById('match-detail');
  if (!el) return;
  if (el.dataset.matrix === '1') { el.dataset.matrix = ''; renderMatchDetail(); return; }
  el.dataset.matrix = '1';
  el.dataset.risk = '';        // 与得分风险面板互斥
  const ov = matchOverview;
  if (!ov) { el.innerHTML = `<div class="mw-empty">正在统计…</div>`; return; }
  const rows = ov.chapters_detail.map(c => {
    const block = blocksById.get(String(c.block_id)) || {};
    const stage = chapterStage(block, { evidenceCount: c.evidence });
    const written = String(block.content || '').trim() ? '✓' : '';
    const cls = c.evidence ? '' : 'ms-dim';
    return `<tr class="${cls}" data-block="${escapeHtml(c.block_id)}">
      <td style="text-align:left;padding-left:${8 + (c.level - 1) * 12}px;">${escapeHtml(c.title || c.block_id)}</td>
      <td>${CHAPTER_STAGE_LABELS[stage] || stage}</td>
      <td>${c.evidence}</td>
      <td title="要回应 ${c.actionable_items_total ?? c.items_total} 条 / 评分口径 ${c.informative_items_total ?? 0} 条">${
        c.actionable_items_total ? `${c.actionable_items_covered}/${c.actionable_items_total}` : '—'}</td>
      <td>${written}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `
    <div class="mw-head">
      <div class="mw-title">章节覆盖矩阵</div>
      <div class="mw-meta">
        <span>共 ${ov.chapters.total} 章</span>
        <span>有依据 ${ov.chapters.with_evidence}</span>
        <span>无依据 ${ov.chapters.without_evidence}</span>
        <span>要回应事项覆盖 ${ov.requirements.actionable_covered}/${ov.requirements.actionable_total}</span>
        <span style="margin-left:auto;"><button class="mw-btn" data-act="close-matrix">返回详情</button></span>
      </div>
    </div>
    <div class="mw-body">
      <table class="mw-matrix">
        <thead><tr><th>章节</th><th>状态</th><th>依据</th><th>条目覆盖</th><th>正文</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  el.querySelector('[data-act="close-matrix"]')?.addEventListener('click', () => {
    el.dataset.matrix = ''; renderMatchDetail();
  });
  el.querySelectorAll('tr[data-block]').forEach(tr => tr.addEventListener('click', () => {
    el.dataset.matrix = '';     // 点矩阵里的章节 = 跳到它的详情
    selectMatchChapter(tr.dataset.block);
  }));
}

// 应答条目（条目级挂载的分组键）——一次拉取，供所有卡片渲染与重渲染使用。
// 失败不致命：没有条目就退化成章节级平铺（renderEvidenceSection 已兜底）。
async function loadResponseItems() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  try {
    const map = await api.materials.responseItems(projectId, tid);
    if (map && typeof map === 'object') responseItemsByBlock = map;
  } catch (err) {
    console.warn('[evidence] 应答条目加载失败，依据退化为章节级展示', err);
  }
}

// 依据变化（匹配跑完 / 重新匹配）由 loadExistingBlocks 整版重绘覆盖（onDone → resyncFromDb），
// 不另开一条"就地刷新"路径 —— 少一条路径就少一处状态不同步的可能。

// 「提炼中」占位：一行说明 + 转圈，而不是 7 行空字段。
// 逐节提炼是串行的，单节十几秒到一分钟；空字段看起来像"这一节没有内容"，
// 用户会以为提炼失败或没跑。结果到达后 updateBlockCard 会整块换成 8 字段网格。
function mountExtractingPlaceholder(card) {
  const body = card?.querySelector('.block-card-body');
  if (!body) return;
  body.innerHTML = `
    <div class="block-card-extracting">
      <span class="extract-spinner" aria-hidden="true"></span>
      <span class="extract-text">正在提炼本节 8 项要求…</span>
      <span class="extract-hint">应标要求 · 应标重点 · 否决项 · 加分项 · 评分项 · 证明材料 · 强制等级 · 量化指标</span>
    </div>`;
}

// 提炼完成 / 中止后：把**已有内容**的章节保持展开。
// 用户刚提炼出来的 8 字段不该因为一次 resync 或流程收尾就整体折叠起来
// （反馈原话：「提炼完成之后 8 个字段的模块直接会隐藏」）。
function openAllExtractedNodes() {
  const set = readOpenSet();
  let changed = false;
  blocksList.querySelectorAll('.ot-node').forEach(node => {
    const id = node.dataset.blockId;
    const b = blocksById.get(id);
    if (b && isDoneBlock(b) && !set.has(id)) { set.add(id); changed = true; }
  });
  if (!changed) return;
  writeOpenSet(set);
  applyOutlineExpansion(false);
}
// ── 目录树：层级挂载 / 折叠 / 定位 ──
// 卡片默认折叠，row 常显。树形结构的纯逻辑在 assets/workspace-model.mjs 的
// buildOutlineTree（与后端 normalize_nodes 同一套规则），这里只负责 DOM 搬运。
// 折叠记忆按**视图**分开存：
//   大纲视图 = 表单（用户要逐个改 8 字段，展开态是他的工作现场，必须记住）
//   素材库视图 = 目录（价值在"扫"，默认全部收起；以前两个视图共用一份记忆，
//   匹配页又被强制全展开，结果 360px 的中栏塞满 8 字段长文本，一屏看不到 3 章）
const OUTLINE_OPEN_KEY = (pid) => `outlineTree:${pid}`;
const MATCH_OPEN_KEY = (pid) => `matchTree:${pid}`;
function openStorageKey(pid) {
  return document.body.dataset.view === 'matching' ? MATCH_OPEN_KEY(pid) : OUTLINE_OPEN_KEY(pid);
}

function outlineNodeEl(blockId) {
  return document.querySelector(`.ot-node[data-block-id="${blockId}"]`);
}

function readOpenSet() {
  try { return new Set(JSON.parse(sessionStorage.getItem(openStorageKey(projectId)) || '[]')); }
  catch { return new Set(); }
}

function writeOpenSet(set) {
  try { sessionStorage.setItem(openStorageKey(projectId), JSON.stringify([...set])); } catch {}
}

function toggleOutlineNode(blockId) {
  const node = outlineNodeEl(blockId);
  if (!node) return;
  const open = !node.classList.contains('open');
  node.classList.toggle('open', open);
  const set = readOpenSet();
  if (open) set.add(blockId); else set.delete(blockId);
  writeOpenSet(set);
}

// 展开祖先链：隐藏卡片的 offsetTop 是 0，不展开就滚到顶部
function expandToBlock(blockId) {
  let node = outlineNodeEl(blockId);
  const set = readOpenSet();
  let changed = false;
  while (node) {
    if (!node.classList.contains('open')) {
      node.classList.add('open');
      changed = true;
    }
    set.add(node.dataset.blockId);
    node = node.parentElement?.closest('.ot-node') || null;
  }
  if (changed) writeOpenSet(set);
}

function scrollToOutlineNode(blockId) {
  const node = outlineNodeEl(blockId);
  if (!node) return;
  const scroller = document.getElementById('blocks-scroll');
  try {
    scroller
      ? scroller.scrollTo({ top: node.offsetTop - scroller.offsetTop - 16, behavior: 'smooth' })
      : node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch {}
}

// 展开态：matching 视图全展开（用户要逐章核对素材），outline 视图按用户折叠记忆
function applyOutlineExpansion(forceAll = false) {
  const stored = readOpenSet();
  blocksList.querySelectorAll('.ot-node').forEach(node => {
    node.classList.toggle('open', forceAll || stored.has(node.dataset.blockId));
  });
}

// 按 level 把已渲染的 .ot-node 重新挂成树
function renderOutlineTree() {
  if (document.body.dataset.view === 'editor') return;
  const entries = Array.from(blocksList.querySelectorAll('.ot-node'))
    .map(node => ({
      node,
      id: node.dataset.blockId,
      level: Number(node.dataset.level) || 1,
    }));
  if (!entries.length) return;
  const byId = new Map(entries.map(e => [e.id, e.node]));
  const tree = buildOutlineTree(entries.map(e => ({ id: e.id, level: e.level })));

  const root = document.createElement('div');
  root.className = 'outline-tree';
  const place = (entry) => {
    const el = byId.get(entry.node.id);
    if (!el) return null;
    el.querySelector(':scope > .ot-children')?.remove();
    if (entry.children.length) {
      const kids = document.createElement('div');
      kids.className = 'ot-children';
      entry.children.forEach(c => { const child = place(c); if (child) kids.appendChild(child); });
      el.appendChild(kids);
    }
    return el;
  };
  tree.forEach(t => { const el = place(t); if (el) root.appendChild(el); });
  blocksList.innerHTML = '';
  blocksList.appendChild(root);
}

// 闸门打开时不重排 DOM：用户可能正在编辑，重排会夺走焦点
function scheduleOutlineTree() {
  if (extractionState === 'gate_outline' || extractionState === 'gate_materials') return;
  renderOutlineTree();
}

// row 是卡片的摘要镜像：徽章 / 要求首行都从卡片里同步，避免两份状态各自漂移
function syncOutlineRow(blockId) {
  const node = outlineNodeEl(blockId);
  const card = node?.querySelector(':scope > .block-card');
  if (!node || !card) return;
  const src = card.querySelector('.block-status-badge');
  const badge = node.querySelector(':scope > .ot-row .block-status-badge');
  if (src && badge) {
    badge.className = src.className + ' ot-badge';
    badge.textContent = src.textContent;
    if (src.title) badge.title = src.title;
  }
  const sub = node.querySelector(':scope > .ot-row .ot-sub');
  if (sub) {
    // 第一个字段 = 应标要求；多行走 <ul>，单行走 .block-field-value。
    // 读 textContent 不读 innerText：卡片折叠时没有布局，innerText 会退化成 textContent，
    // 展开后结果就该一致，不能因为折叠与否给出两种副标题。
    const field = card.querySelector('.block-card-grid .block-field');
    const items = field ? Array.from(field.querySelectorAll('.block-field-list li')) : [];
    const raw = items.length
      ? items.map(li => li.textContent).filter(Boolean).join('\n')
      : (field?.querySelector('.block-field-value')?.textContent || '');
    sub.textContent = firstLine(raw);
  }
}

function updateBlockCard(blockId, data) {
  // 闸门 1（gate_outline）/ 闸门 2（gate_materials）期间避免冲掉用户编辑：后端若重发 outline_extract / match_progress 直接忽略
  if (extractionState === 'gate_outline' || extractionState === 'gate_materials') return;
  const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
  if (!card) return;
  const badgeState = badgeStatusOf({ ...data, status: 'outline_done' });
  const badge = card.querySelector('.block-status-badge');
  badge.className = `block-status-badge ${badgeState}`;
  badge.textContent = BADGE_TEXT[badgeState] || badgeState;
  badge.title = data.error || '';
  card.querySelector('.block-card-progress')?.classList.remove('active');
  // 结果到达：整块重建 8 字段（闸门 1 的卡片本来没有网格；提炼中的骨架也要换成值）。
  // 空字段走「—」而不是骨架 —— 骨架会让用户以为这一节还在提炼。
  mountOutlineFields(card, data);
  syncOutlineRow(blockId);
}

// ── 死线程恢复：run 已作废 / 后端不认识这个 thread ──
// 用户看到「该工作流已作废，请创建新的工作流」时并不知道该点哪里，页面会卡在这个
// 死线程上（后续每个动作都打向它）。这里改成：优先切到本项目**最近一次可用运行**
// （它可能正停在某个闸门等着），没有可用的才清空、让用户新建一次。
async function switchToLatestUsableRun() {
  try {
    const runs = await fetch(`/api/projects/${projectId}/workflow-runs`)
      .then(r => (r.ok ? r.json() : []));
    const usable = (runs || []).find(
      r => r.thread_id && r.stage && !['aborted', 'done'].includes(r.stage));
    if (!usable) return false;
    const current = params.get('threadId') || getThread(projectId) || currentThreadId;
    if (usable.thread_id === current) return false;   // 已经是它了，别自己重定向自己
    setThread(projectId, usable.thread_id);
    const url = new URL(location.href);
    url.searchParams.set('threadId', usable.thread_id);
    // 重新加载：按那次运行的阶段重建整页（卡片、闸门按钮、进度），比在旧页面上打补丁可靠
    location.replace(url.toString());
    return true;
  } catch {
    return false;
  }
}

// 收到「已作废 / 线程未知」时调用。返回 true 表示已经接管（调用方不要再报错）
async function recoverFromDeadThread(err) {
  const code = err?.code;
  if (code !== 'workflow_aborted' && code !== 'unknown_thread') return false;
  clearThread(projectId);
  currentThreadId = null;
  if (await switchToLatestUsableRun()) return true;   // 切走后页面会重载
  setStatusBar('paused',
    code === 'workflow_aborted'
      ? '当前运行已作废（已取消或中断），已清空。点「重新提炼」会新建一次运行。'
      : '找不到这次运行（可能已被清理），已清空。点「重新提炼」会新建一次运行。',
    0, totalBlocks, doneBlocks);
  return true;
}

// ── 目录结构编辑（增删章节 / 改标题 / 上下移动 / 缩进）──
// 纯逻辑在 workspace-model.mjs（有 node --test 覆盖），这里负责 DOM 与执行时序：
//   · 面板：整行只露「编辑」，点开弹出操作面板（绝对定位，不挤压布局）
//   · 乐观更新：先按新顺序重排本地，再保存；失败以库为准回滚
//   · 保存队列：串行化请求，快速连点不会互相覆盖
//   · 采纳服务端返回的归一化结构（跨级加深 / 超范围会被服务端压平）
const STRUCTURE_SAVED_MS = 1500;

let editingBlockId = null;
let structureSaveChain = Promise.resolve();
let pendingRenameId = null;

// 「已提炼」以 status 字段为权威：upsert 时写 'outline_done' 才算完成。
// 不能用 key_points/requirement 是否为空兜底 —— 部分章节（纯目录、分组节点）
// LLM 提炼后这两个字段就是空串，会被误判为未完成。
// isDoneBlock / badgeStatusOf 的实现搬到了 assets/workspace-model.mjs：
// 「失败不算完成」是条契约，放到有 node --test 覆盖的纯函数里才守得住。
// 见 import 处的别名。

function structureEditable() {
  if (extractionState === 'gate_outline') return true;
  // 提炼完成后的目录视图也允许调整（此时 8 字段可编辑，改名/调序不该被禁）
  return extractionState === 'done' && document.body.dataset.view !== 'matching';
}

function refreshStructureEditable() {
  const editable = structureEditable();
  blocksList.classList.toggle('structure-editable', editable);
  if (!editable) closeEditPanel();   // 离开可编辑态收起面板，避免残留
}

function outlineTid() {
  return currentThreadId || params.get('threadId') || getThread(projectId);
}

// ── 读 / 写本地结构 ──
// 顺序即结构：DOM 的文档序（pre-order）就是扁平列表序，由 renderOutlineTree 保证。
function structureNodes() {
  return Array.from(blocksList.querySelectorAll('.ot-node')).map(n => {
    const id = n.dataset.blockId;
    return {
      id,
      level: Number(n.dataset.level) || 1,
      title: (blocksById.get(id)?.title)
          || n.querySelector(':scope > .ot-row > .ot-title')?.textContent || '',
    };
  });
}

function structurePayload(nodes) {
  return nodes.map(n => ({ block_id: n.id, level: n.level, title: n.title }));
}

// 按给定顺序重排/刷新本地 DOM。有节点本地不存在（新增）→ 返回 false，调用方重读库。
//
// ⚠️ 必须**先把所有节点查完再动 DOM**：树是嵌套的，把父节点移出文档会连带移走它的
// 子节点，于是后续子节点的 querySelector 查不到 —— 早退时已经搬进临时容器的节点
// 就被一起丢掉了（实测：删一个二级章节，页面上消失了 14 个节点，库里却是对的）。
function applyStructureNodes(nodes) {
  const wanted = new Set(nodes.map(n => n.id));
  // 先把「不在新结构里」的节点从 DOM 摘掉。树是嵌套的：被删节点的元素往往还挂在
  // 它父节点的 .ot-children 里，replaceChildren 只重组 blocksList 的直接子节点，
  // 藏着的那个会跟着父节点活下来（实测：删掉 s1.4，它却变成了 s1 的第一个子级）。
  const strays = Array.from(blocksList.querySelectorAll('.ot-node'))
    .filter(el => !wanted.has(el.dataset.blockId));

  const elements = nodes.map(n => outlineNodeEl(n.id));
  if (elements.some(el => !el)) return false;   // 查全之前不碰 DOM
  // 兜底：若某个待保留节点正好嵌在待摘除节点内部（结构不自洽），交给调用方重读库
  if (elements.some(el => strays.some(s => s.contains(el)))) return false;

  strays.forEach(el => el.remove());

  nodes.forEach((n, i) => {
    const el = elements[i];
    el.dataset.level = String(n.level);
    const titleEl = el.querySelector(':scope > .ot-row > .ot-title');
    if (titleEl) { titleEl.textContent = n.title; titleEl.title = n.title; }
    const stored = blocksById.get(n.id);
    if (stored) { stored.title = n.title; stored.level = n.level; }
  });

  // 一次性替换：replaceChildren 会把这些元素从原父节点（含彼此嵌套）摘下、按参数顺序
  // 放成同一层，顺序正确后由 renderOutlineTree 重新挂树。
  blocksList.replaceChildren(...elements);
  renderOutlineTree();
  const ordered = nodes.map(n => blocksById.get(n.id)).filter(Boolean);
  totalBlocks = ordered.length;
  doneBlocks = ordered.filter(isDoneBlock).length;
  buildToc(ordered);
  refreshStatusCounters();
  return true;
}

// 右上角「N/M 模块 · x%」与进度条只在 setStatusBar 里写过 —— 结构增删后章节数变了，
// 提炼完成后的目录视图（done 态）不会再调 setStatusBar，不刷就会一直显示旧数字。
function refreshStatusCounters() {
  const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0;
  statusPct.textContent = `${doneBlocks}/${totalBlocks} 模块 · ${pct}%`;
  progressBar.style.width = pct + '%';
  updateSidebarProgress();
}

// ── 保存 ──
// 串行化：连点「上移」时请求依次发出，最后一个请求携带的顺序即最终顺序。
function queueStructureSave(nodes, purge = false) {
  const tid = outlineTid();
  const payload = structurePayload(nodes);
  const run = structureSaveChain.then(() =>
    api.workflow.updateOutlineStructure(tid, payload, purge));
  structureSaveChain = run.catch(() => {});   // 单次失败不能断链
  return run;
}

function editPanelEl(sel) {
  if (!editingBlockId) return null;
  return outlineNodeEl(editingBlockId)?.querySelector(sel) || null;
}

function setEditPanelState(text, kind = '') {
  const el = editPanelEl('.ot-edit-state');
  if (!el) return;
  el.textContent = text || '';
  el.dataset.kind = kind;
}

function setEditPanelError(text) {
  const msg = editPanelEl('.ot-edit-msg');
  if (msg) msg.textContent = text || '';
  setEditPanelState(text ? '未保存' : '', text ? 'error' : '');
}

function setEditPanelBusy(busy) {
  const panel = editPanelEl('.ot-edit-panel');
  if (!panel) return;
  if (busy) panel.querySelectorAll('button').forEach(b => { b.disabled = true; });
  else renderEditPanel();          // 恢复时按可用性重算
}

/**
 * 应用一次结构变更：乐观重排 → 串行保存 → 采纳服务端归一化结果。
 * missing=true：本地缺该节点（新增），保存成功后重读库把它变成真实卡片。
 */
async function persistStructure(nodes, { missing = false, afterSave = null, purge = false } = {}) {
  if (!missing) applyStructureNodes(nodes);
  setEditPanelState('保存中…', 'saving');
  setEditPanelBusy(true);
  let saved = false;
  let failure = '';
  try {
    const res = await queueStructureSave(nodes, purge);
    if (missing) {
      await loadExistingBlocks({ silent: true });
    } else if (Array.isArray(res?.nodes) && res.nodes.length) {
      // 服务端可能压平跨级层级 —— 以它为准再同步一次，避免页面与库发散
      applyStructureNodes(res.nodes.map(n => ({
        id: n.block_id, level: n.level, title: n.title,
      })));
    }
    saved = true;
    if (afterSave) await afterSave();
  } catch (e) {
    console.error('[outline] 结构保存失败', e);
    if (await recoverFromDeadThread(e)) {
      // 线程已作废：页面正在切走或已提示新建，别再把用户按在这个死线程上报错
      return false;
    }
    failure = `保存失败：${e?.message || e}`;
    await loadExistingBlocks({ silent: true });   // 以库为准回滚
  } finally {
    // 解除禁用并重算按钮可用性（会重绘面板）
    setEditPanelBusy(false);
  }
  // 状态文案必须写在重绘**之后**，否则会被 renderEditPanel 清掉
  if (saved) {
    setEditPanelState('已保存', 'saved');
    window.setTimeout(() => {
      if (editingBlockId) setEditPanelState('', '');
    }, STRUCTURE_SAVED_MS);
  } else {
    setEditPanelError(failure);
  }
  return saved;
}

// ── 面板 ──
function toggleOutlineEditMode(blockId) {
  if (editingBlockId === blockId) { closeEditPanel(); return; }
  openEditPanel(blockId);
}

function closeEditPanel() {
  editingBlockId = null;
  blocksList.querySelectorAll('.ot-node.editing').forEach(n => n.classList.remove('editing'));
  blocksList.querySelectorAll('.ot-edit-panel').forEach(p => p.remove());
}

function openEditPanel(blockId) {
  closeEditPanel();
  const node = outlineNodeEl(blockId);
  const row = node?.querySelector(':scope > .ot-row');
  if (!row) return;
  editingBlockId = blockId;
  node.classList.add('editing');
  const panel = document.createElement('div');
  panel.className = 'ot-edit-panel';
  panel.addEventListener('click', (e) => e.stopPropagation());
  row.appendChild(panel);
  renderEditPanel();
}

// 面板按当前结构渲染：**不可用的操作直接置灰并说明原因**，而不是点了没反应
function renderEditPanel() {
  if (!editingBlockId) return;
  const panel = editPanelEl('.ot-edit-panel');
  if (!panel) return;

  const nodes = structureNodes();
  const current = nodes.find(n => n.id === editingBlockId);
  if (!current) { closeEditPanel(); return; }

  const [start, end] = outlineSubtreeRange(nodes, editingBlockId);
  const childCount = Math.max(0, end - start - 1);
  const canChild = insertChildOf(nodes, editingBlockId, { id: '__probe__', level: 1, title: '' }) !== null;
  const canIndent = indentOutlineNode(nodes, editingBlockId) !== null;
  const canOutdent = outdentOutlineNode(nodes, editingBlockId) !== null;
  const canUp = moveOutlineNode(nodes, editingBlockId, -1) !== null;
  const canDown = moveOutlineNode(nodes, editingBlockId, +1) !== null;
  const canDelete = nodes.length > (end - start);   // 目录至少要留一个章节

  const btn = (act, label, enabled, title) =>
    `<button class="ot-act" data-act="${act}"${enabled ? '' : ' disabled'} title="${title}">${label}</button>`;

  panel.innerHTML = `
    <div class="ot-edit-head">
      <span>编辑：<b>${escapeHtml(current.title)}</b></span>
      <span class="ot-edit-state" data-kind=""></span>
      <button class="ot-act" data-role="close" title="收起（Esc）">收起</button>
    </div>
    <div class="ot-edit-row">
      <span class="ot-edit-label">新增</span>
      ${btn('child', '新增子章节', canChild, canChild ? '在本节下新增子章节' : '已是最深层级（4 级），不能再加子级')}
      ${btn('sibling', '新增同级', true, '在本节之后新增同级章节')}
    </div>
    <div class="ot-edit-row">
      <span class="ot-edit-label">调整</span>
      ${btn('outdent', '升一级', canOutdent, canOutdent ? '升为上一级' : '已是一级章节')}
      ${btn('indent', '降一级', canIndent, canIndent ? '降为上一个同级章节的子级' : '前面没有同级章节可作父级')}
      ${btn('up', '上移', canUp, canUp ? '与上一个同级章节交换位置' : '已是第一个同级章节')}
      ${btn('down', '下移', canDown, canDown ? '与下一个同级章节交换位置' : '已是最后一个同级章节')}
    </div>
    <div class="ot-edit-row">
      <span class="ot-edit-label">其他</span>
      ${btn('rename', '重命名', true, '就地修改标题')}
      ${btn('delete', '删除', canDelete, canDelete ? '删除本节及其子章节' : '目录至少要保留一个章节')}
      ${childCount ? `<span class="ot-edit-hint">含 ${childCount} 个子章节</span>` : ''}
    </div>
    <div class="ot-edit-msg" data-role="msg"></div>`;

  panel.querySelector('[data-role="close"]').addEventListener('click', closeEditPanel);
  panel.querySelectorAll('button[data-act]').forEach(b => {
    b.addEventListener('click', () => handleOutlineAction(editingBlockId, b.dataset.act));
  });
}

// ── 操作 ──
async function handleOutlineAction(blockId, act) {
  if (!structureEditable() || !blockId) return;
  const nodes = structureNodes();
  const current = nodes.find(n => n.id === blockId);
  if (!current) return;

  if (act === 'rename') { startRenameOutlineNode(blockId); return; }
  if (act === 'delete') { confirmDeleteOutlineNode(blockId, current, nodes); return; }

  let next = null;
  let afterSave = null;
  let missing = false;

  if (act === 'up') next = moveOutlineNode(nodes, blockId, -1);
  else if (act === 'down') next = moveOutlineNode(nodes, blockId, +1);
  else if (act === 'indent') next = indentOutlineNode(nodes, blockId);
  else if (act === 'outdent') next = outdentOutlineNode(nodes, blockId);
  else if (act === 'child' || act === 'sibling') {
    const id = newOutlineId(nodes.map(n => n.id));
    const fresh = { id, level: current.level, title: '新章节' };
    next = act === 'child'
      ? insertChildOf(nodes, blockId, fresh)
      : insertSiblingAfter(nodes, blockId, fresh);
    if (next) {
      missing = true;                     // 本地没有这个 DOM 节点，保存后重读库
      afterSave = async () => {
        expandToBlock(id);
        openEditPanel(id);                // 新节点直接进编辑态，接着改名
        startRenameOutlineNode(id);
      };
    }
  }

  if (!next) return;   // 空操作（已在顶/底、深度到顶）——这些按钮本应已置灰
  await persistStructure(next, { missing, afterSave });
}

// 删除走两步确认：不用 window.confirm（风格不一致，也放不下「有正文时内容保留」的提示）
function confirmDeleteOutlineNode(blockId, current, nodes) {
  const panel = editPanelEl('.ot-edit-panel');
  const msg = editPanelEl('.ot-edit-msg');
  if (!panel || !msg) return;
  const [start, end] = outlineSubtreeRange(nodes, blockId);
  const childCount = Math.max(0, end - start - 1);
  const hasContent = nodes.slice(start, end)
    .some(n => !!((blocksById.get(n.id)?.content || '').trim()));

  // 含正文时给两个选项：保留正文的行**仍会出现在导出里**（export.py 取全量 blocks），
  // 这个取舍必须由用户明确选择，不能被"删除目录节点"顺手替他决定。
  msg.innerHTML = `
    <div class="ot-edit-row ot-edit-confirm">
      <span>删除「${escapeHtml(current.title)}」${childCount ? `及其下 ${childCount} 个子章节` : ''}？${
        hasContent ? '<em>其中含已有正文。</em>' : ''}</span>
      ${hasContent ? '<button class="ot-act" data-role="keep-content">仅移除（保留正文）</button>' : ''}
      <button class="ot-act ot-act-danger" data-role="ok">${
        hasContent ? '移除并删除正文' : '确认删除'}</button>
      <button class="ot-act" data-role="cancel">取消</button>
    </div>
    ${hasContent ? '<div class="ot-edit-hint">保留正文：内容留在库里（可在后续手改时找回），但导出方案时仍会包含这一章。</div>' : ''}`;
  msg.querySelector('[data-role="cancel"]').addEventListener('click', () => setEditPanelError(''));
  const removeNode = async (purge) => {
    const next = removeOutlineSubtree(nodes, blockId).nodes;
    // 先别收面板：失败时错误要显示在面板里（面板已收的话用户看不到任何原因）
    const ok = await persistStructure(next, { purge });
    if (ok) closeEditPanel();             // 成功后被删节点已消失，面板没有归属了
  };
  msg.querySelector('[data-role="ok"]').addEventListener('click', () => removeNode(hasContent));
  msg.querySelector('[data-role="keep-content"]')?.addEventListener('click', () => removeNode(false));
}

// 行内改名：✎ 或新增后直接进入；Enter / 失焦保存，Esc 取消
function startRenameOutlineNode(blockId) {
  const node = outlineNodeEl(blockId);
  const titleEl = node?.querySelector(':scope > .ot-row > .ot-title');
  if (!titleEl) return;
  const original = titleEl.textContent;
  titleEl.setAttribute('contenteditable', 'true');
  titleEl.focus();
  const range = document.createRange();
  range.selectNodeContents(titleEl);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    titleEl.removeAttribute('contenteditable');
    const value = (titleEl.textContent || '').trim();
    if (!save || value === original) { titleEl.textContent = original; return; }
    if (!value) {
      // 空标题后端会 400 —— 本地先拦下并说明原因
      titleEl.textContent = original;
      setEditPanelError('标题不能为空，已还原');
      return;
    }
    titleEl.textContent = value;
    // structureNodes 优先读 blocksById —— 不同步的话提交的还是旧标题
    const stored = blocksById.get(blockId);
    if (stored) stored.title = value;
    await persistStructure(structureNodes());
  };
  titleEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  titleEl.addEventListener('blur', () => finish(true), { once: true });
}

// 点面板外 / Esc 收起面板；改名进行中的 Esc 由标题自己处理，不连带收面板
document.addEventListener('mousedown', (e) => {
  if (!editingBlockId) return;
  if (e.target.closest('.ot-edit-panel')) return;
  if (e.target.closest('.ot-edit-toggle')) return;    // 交给 toggle 自己收
  if (e.target.closest('[contenteditable="true"]')) return;
  closeEditPanel();
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !editingBlockId) return;
  if (document.activeElement?.closest?.('[contenteditable="true"]')) return;
  closeEditPanel();
});


// ── 加载已有 blocks ──
/**
 * 目录**尚未派生**时把话说清楚。
 *
 * 实测（项目 19 run efe2ac4c）：那次运行在 **parsing 阶段被 abort**，parse 已经把
 * 531 个**招标文件原始目录**写成占位 blocks（"1.招标条件 / 2.项目概况…"），
 * 而派生目录还没跑。页面却把这批占位当成"应答目录"显示，还邀请用户
 * 「确认目录，开始提炼要点」—— 一旦确认就会去提炼 531 个原始章节。
 *
 * 判据用后端预检的 `has_outline`（= outline_revision 有没有值），比在前端
 * 比对 source_toc 与 toc 更可靠。
 */
/**
 * 闸门上的**覆盖率面板**：评分点覆盖 / 分值合计 / 规范要求落点 + 缺口清单。
 *
 * 为什么放闸门：这是用户决定"目录结构能不能定下来"的地方。分值合计最实用 ——
 * 漏一个评分点，100 立刻变 90，一眼可见，不用自己逐条数。
 * 渲染在树栏内部（流式元素），不动全局布局。
 */
function renderOutlineCoverage(coverage) {
  // 就地填充**静态容器**（three-mode.html 里预置）：插入式 append 会把目录往下推，
  // 表现为进页面时抖动（用户反馈）。容器不存在（旧页面）时退回创建。
  let box = document.getElementById('outline-coverage-panel');
  if (!box) {
    const host = document.getElementById('blocks-scroll') || blocksList?.parentElement;
    if (!host) return;
    box = document.createElement('div');
    box.id = 'outline-coverage-panel';
    host.insertBefore(box, host.firstChild);
  }
  if (!coverage || !coverage.review_points_total) {
    box.innerHTML = '';
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.className = 'ov-panel';
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
  const cls = (a, b) => (a >= b ? 'ok' : a ? 'warn' : 'bad');
  const gaps = []
    .concat((coverage.review_points_missing || []).map(x => `评分点未成章：${x}`))
    .concat((coverage.requirements_missing || []).slice(0, 4).map(x => `规范要求未落点：${x}`));
  box.innerHTML = `
    <div class="ov-panel-head">
      <span class="ov-panel-title">目录覆盖率</span>
      <span class="ov-metric ${cls(coverage.review_points_covered, coverage.review_points_total)}"
        title="每个评分点都要有章节承载；已拆细 = 该评分点下还分了子检查项">
        评分点 <b>${coverage.review_points_covered}/${coverage.review_points_total}</b>
        <span class="ov-sub">已拆细 ${coverage.review_points_decomposed}</span></span>
      <span class="ov-metric ${cls(coverage.score_covered, coverage.score_total)}"
        title="分值合计是最实用的完整性校验：漏一个评分点，100 立刻变 90">
        分值 <b>${coverage.score_covered}/${coverage.score_total}</b></span>
      <span class="ov-metric ${cls(coverage.requirements_landed, coverage.requirements_total)}"
        title="技术规范书要求是否都在目录里有落点">
        规范要求 <b>${coverage.requirements_landed}/${coverage.requirements_total}</b>
        <span class="ov-sub">${pct(coverage.requirements_landed, coverage.requirements_total)}%</span></span>
    </div>
    <div class="ov-actions">
      <span class="ov-scope" id="ov-scope-box" title="投标范围（投标人视角）：一次设定，拆分与报告都按它执行">
        <span class="ov-sub">投标范围</span>
        <select id="ov-scope-select" style="min-width:220px;"><option value=""></option></select>
        <span class="ov-sub" id="ov-scope-hint" style="color:#047857"></span>
      </span>
      <button class="mw-btn" id="ov-split-report"
        title="展开/收起拆分清单：拆分依据（需确认项）+ 评分点 / 评分单元与子检查项 / 技术要求矩阵 / 评分↔技术要求映射矩阵">
        ${splitPanelExpanded ? '收起清单' : '展开拆分清单'}</button>
    </div>
    ${gaps.length ? `<div class="ov-gaps">缺口：${gaps.slice(0, 6).map(escapeHtml).join('；')}</div>` : ''}`;
  box.querySelector('#ov-split-report')?.addEventListener('click', () => toggleSplitPanel());
}

async function resolvePanelThreadId(tid) {
  if (tid) return tid;
  try { return await api.workflow.latestRunThreadId(projectId); } catch (e) { return null; }
}

/**
 * 投标范围选择器：**一次设定、贯穿全程**。
 *
 * "我们投哪个标包"是投标人的属性，不该每次从提炼要求里的文字去猜 ——
 * 实测多标包文件里推错范围 = 整份技术应答目录错。选项来自评分地图的标包索引。
 *
 * ⚠️ 这个函数曾被整段删除过（重写 openSplitReport 时连带删掉），而调用点还在，
 * 报错又被下面的 try/catch 吞了 —— 界面只表现为"投标范围一直加载中"。
 * 所以本函数失败时必须**留痕**（console.warn），不能静默。
 */
/** 用已取到的数据**同步**填充投标范围选项（不再自己发请求、不再显示"加载中…"）。 */
function fillScopeSelector(report, scope) {
  const sel = document.getElementById('ov-scope-select');
  if (!sel) return;
  const idx = (report && report.scoring_map && report.scoring_map.index) || [];
  const opts = ['<option value="">未设定（按提炼要求推断）</option>'];
  idx.forEach(t => (t.packages || []).forEach(p => {
    opts.push(`<option value="${escapeHtml(`${t.target}|${p.no}`)}">${
      escapeHtml(`${t.target} / 标包${p.no}｜${p.name}`)}</option>`);
  }));
  sel.innerHTML = opts.join('');
  if (scope && (scope.target || scope.package_no)) {
    sel.value = `${scope.target}|${scope.package_no}`;
  }
  if (!sel.dataset.bound) {
    sel.dataset.bound = '1';
    sel.addEventListener('change', async () => {
      const [target, no] = (sel.value || '').split('|');
      const pack = (idx.find(t => t.target === target) || {}).packages?.find(p => p.no === no);
      await api.workflow.setScope(projectId, {
        target: target || '', package_no: no || '',
        package_name: (pack && pack.name) || '',
      });
      await refreshOutlineCoverage(null);
      // 内联提示代替弹窗：保存是高频操作，弹窗每次都打断
      const hint = document.getElementById('ov-scope-hint');
      if (hint) {
        hint.textContent = '已保存；下次「拆解评分点」按它执行';
        window.setTimeout(() => { if (hint) hint.textContent = ''; }, 4000);
      }
    });
  }
}

async function refreshOutlineCoverage(tid) {
  tid = await resolvePanelThreadId(tid);
  if (!tid) return;
  try {
    // 三份数据**并行取**，取完再一次性渲染覆盖率面板 + 投标范围选项 ——
    // 否则面板先画（显示"投标范围 加载中…"）再被选项替换，进页面时会看到一次闪动。
    const [st, report, scope] = await Promise.all([
      api.workflow.state(tid),
      api.workflow.splitReport(projectId, tid).catch(() => null),
      api.workflow.getScope(projectId).catch(() => null),
    ]);
    outlinePending = !!(st && st.spec && st.spec.outline_pending);
    renderOutlineCoverage((st && st.spec && st.spec.outline_coverage) || null);
    fillScopeSelector(report, scope);
  } catch (e) {
    // 面板是增强信息，但不许静默 —— 之前正是静默吞掉了 "bindScopeSelector is not defined"，
    // 让"投标范围一直加载中"这种问题查了很久。
    console.warn('[outline] 覆盖率面板刷新失败：', e);
  }
}

/**
 * **拆分清单**面板：拆目录的依据（待确认项）+ 拆完的四张表。
 *
 * 用户要求（固定流程）：拆分时把需要确认的内容与用户沟通；拆分完成后列出
 * 完整的评分点及数量、评分单元与子检查项、技术要求矩阵、评分↔技术要求映射矩阵。
 * 用整页浮层呈现（四张大表在侧栏里看不完），不占布局、关掉即走。
 */
/** 拆分结果的确认状态：按 项目+run 记住"第几版已确认"。
 *
 * UI 逻辑（用户要求）：拆分完成 → **直接展示拆分清单**（评分点 / 评分单元 /
 * 技术要求矩阵 / 映射矩阵）→ 确认之后才把目录当作最终应答目录输出。
 * 所以要记住"这一版确认过没有"，否则每次目录更新都弹一次。
 */
// 拆分清单是否展开。容器现在是**静态占位**（一直在 DOM 里，只是 hidden），
// 所以不能再用"元素是否存在"判断状态 —— 否则按钮永远显示"收起清单"。
let splitPanelExpanded = false;
// 保存后的即时反馈（面板会重开以刷新状态，用它把"刚才那步成功了"带回来）
let splitPanelFlash = '';
let splitPanelFlashKind = '';      // '' | 'err'
// 目录是否"待生成"（第①步只拆评分点之后的中间态）。由 refreshOutlineCoverage 写入，
// loadExistingBlocks 据此自动展开「拆解结果」—— 启动时 thread 还没解析出来，
// 光靠 restoreSettledThread 那条路会漏掉这个提示（实测面板不出现）。
let outlinePending = false;

/** 保存失败：就地显示（找不到就地容器时，重开面板让顶部 flash 显示）。 */
function inlineFlashFromSave(err) {
  const msg = '保存失败：' + String(err?.message || err).slice(0, 140);
  const slot = document.querySelector('[data-flash-for="topic_placement"]');
  if (slot) { slot.textContent = msg; slot.className = 'sr-dim sr-inline-err'; }
  else { const el = document.getElementById('split-panel-inline'); if (el) el.remove(); splitPanelExpanded = false; openSplitReport(); }
}

/** 把提示写到对应确认项下方（有就地容器就就地显示，否则落回顶部反馈条）。 */
function inlineFlash(host, text, kind = '') {
  const slot = host?.parentElement?.querySelector('[data-flash-for="topic_placement"]');
  if (!slot) return;
  slot.textContent = text;
  slot.className = kind === 'err' ? 'sr-dim sr-inline-err' : 'sr-dim';
}

function confirmedRevisionKey() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId) || 'latest';
  return `aibidding:split-confirmed:${projectId}:${tid}`;
}
function getConfirmedRevision() {
  try { return Number(sessionStorage.getItem(confirmedRevisionKey()) || 0); } catch (e) { return 0; }
}
function markRevisionConfirmed(rev) {
  try { sessionStorage.setItem(confirmedRevisionKey(), String(rev || 0)); } catch (e) { /* 忽略 */ }
}

/** 拆分完成时自动展示拆分清单 —— 确认之前不把目录当最终结果输出。 */
let splitPanelAutoOpenedFor = 0;
async function maybeAutoOpenSplitReport() {
  const rev = Number(draftRevision || 0);
  if (!rev) return;
  if (rev <= getConfirmedRevision()) return;        // 这一版已确认过
  if (splitPanelAutoOpenedFor === rev) return;      // 同一版只自动展示一次
  splitPanelAutoOpenedFor = rev;
  await openSplitReport({ auto: true });
}

/** 收起拆分清单：拆下面板、恢复"待确认"横幅、露出目录。 */
function collapseSplitPanel() {
  splitPanelExpanded = false;
  const el = document.getElementById('split-panel-inline');
  if (el) { el.hidden = true; el.innerHTML = ''; }
  showConfirmedBanner();      // 按当前确认状态刷新横幅文案（并取消隐藏）
}

/** 展开 / 收起切换（覆盖率面板上的「拆分清单」按钮）。 */
async function toggleSplitPanel() {
  if (splitPanelExpanded) { collapseSplitPanel(); return; }
  await openSplitReport();
}

async function openSplitReport({ auto = false } = {}) {
  const tid = await resolvePanelThreadId(null);
  let data = null;
  try { data = await api.workflow.splitReport(projectId, tid); } catch (e) { data = null; }
  if (!data) {
    // 不弹窗：把失败写进面板容器（用户点开的地方），一眼能看到
    const host = document.getElementById('split-panel-inline');
    if (host) {
      host.hidden = false;
      host.innerHTML = `<div class="sr-box"><div class="sr-head"><b>拆解评分点</b>
        <button class="mw-btn" data-act="collapse" style="margin-left:auto;">收起，看目录</button></div>
        <div style="padding:14px 18px" class="sr-flash err">拆分清单暂时取不到（接口未返回）。请稍后重试，或点「重新拆解评分点」。</div></div>`;
      host.querySelector('[data-act="collapse"]')?.addEventListener('click', () => collapseSplitPanel());
      splitPanelExpanded = true;
    }
    return;
  }

  const rp = data.review_points || {}, su = data.scoring_units || {};
  const tm = data.tech_matrix || {}, mp = data.mapping || {};
  const smap = data.scoring_map || {};
  // 「评分点确认」必须按**当前版本**判定：重新拆解产生新版本后，
  // 旧版本的作答不算数 —— 否则该项一直显示"已作答"、确认按钮再也不出现，
  // 用户只剩页脚那个按钮可点（实测反馈"这个我要怎么确认？"）。
  const rpAns = (data.answers || {}).review_points || {};
  const rpCurrent = !!rpAns.confirmed
    && Number(rpAns.revision || 0) === Number(draftRevision || 0);
  const conf = (data.confirmations || []).map(c =>
    (c.key === 'review_points' && !rpCurrent)
      ? { ...c, answered: false, needs_confirm: true }
      : c);
  const pending = conf.filter(c => c.needs_confirm);
  const settled = conf.filter(c => !c.needs_confirm);

  // ── 两阶段流程（用户要求）：先确认**评分点**，再确认**目录拆分** ──
  // 点「拆解评分点」→ 解析 → 评分点面板（本面板的「评分点」页）→ 确认评分点
  // → 再确认目录结构 → 收起、展示最终应答目录。
  const answers = data.answers || {};
  const pointsConfirmed = rpCurrent;      // 同一判定，避免两处口径不一致
  const stage = pointsConfirmed ? 'structure' : 'points';

  // 指标条：一眼看清"这份目录是什么规模、依据确认到什么程度"
  const metrics = [
    { label: '评分点', value: `${rp.count || 0} 个`, sub: `合计 ${rp.score_total != null ? rp.score_total : '—'} 分`,
      cls: Math.abs((rp.score_total || 0) - 100) < 0.01 ? 'ok' : 'warn' },
    { label: '评分单元', value: `${su.unit_count || 0} 个`,
      sub: `${su.sub_item_count || 0} 详细项 / ${su.check_item_count || 0} 子项`, cls: '' },
    { label: '技术要求', value: `${tm.count || 0} 条`, sub: `${tm.topic_count || 0} 个主题`, cls: '' },
    { label: '映射', value: `${(mp.point_to_requirement || []).length} 个评分点`,
      sub: `未落点 ${(mp.unmapped_requirements || []).length} 条`, cls: '' },
    { label: '评分表', value: `${smap.stats?.tables || 0} 张`,
      sub: smap.selected_table_no ? `选中表${smap.selected_table_no}` : '未选中', cls: '' },
    { label: '待确认', value: `${pending.length} 项`, sub: pending.length ? '需要你确认' : '依据已确认',
      cls: pending.length ? 'warn' : 'ok' },
  ];
  const metricHtml = metrics.map(m => `
    <div class="sr-metric ${m.cls}">
      <div class="sr-metric-label">${m.label}</div>
      <div class="sr-metric-value">${escapeHtml(String(m.value))}</div>
      <div class="sr-metric-sub">${escapeHtml(String(m.sub))}</div>
    </div>`).join('');

  // ── 标签页：四张清单 + 拆分依据，各自独立滚动（原来全挤在一页里滚不完）──
  const matrixRows = data.package_matrix || [];
  const tabs = [
    { id: 'matrix', label: '① 拆解结果', badge: `${matrixRows.length} 个标包` },
    { id: 'points', label: '② 评分点明细', badge: `${rp.count || 0}` },
    { id: 'units', label: '③ 评分单元与子项', badge: `${su.unit_count || 0}` },
    { id: 'tech', label: '技术要求矩阵', badge: `${tm.topic_count || 0}` },
    { id: 'map', label: '映射矩阵', badge: `${(mp.point_to_requirement || []).length}` },
    { id: 'basis', label: '拆分依据', badge: pending.length ? `${pending.length} 待确认` : '已确认' },
  ];
  // 默认停在「拆解结果」：这是入口 —— 先看清各标包的商务/技术/报价评分点，
  // 再由用户选择生成哪一份目录（用户明确要求的结构）。
  const activeTab = 'matrix';
  const tabHtml = tabs.map(t => `
    <button class="sr-tab ${t.id === activeTab ? 'active' : ''}" data-tab="${t.id}">
      ${t.label}<span class="sr-tab-badge">${escapeHtml(String(t.badge))}</span></button>`).join('');

  const confItem = (c) => {
    const opts = (c.options || []).map(o => `
      <option value="${o.table_no}">表${o.table_no} ${escapeHtml(o.label)}（技术 ${o.tech_count} 条 / ${o.tech_score} 分）</option>`).join('');
    let ctrl = '';
    if (c.answer_key === 'review_points') {
      // 第①步的确认按钮就在这里 —— 之前这一项**没有任何控件**，
      // 用户看到"待确认"却无从下手（实测反馈："这个我要怎么确认？"）。
      ctrl = `<div class="sr-answer">
        <button class="mw-btn primary" data-save="review_points">确认评分点清单无误</button>
        <span class="sr-dim">确认后进入第②步：确认目录拆分</span></div>`;
    } else if (c.answer_key === 'scope' && opts) {
      ctrl = `<div class="sr-answer"><select data-answer="scope">${opts}</select>
        <button class="mw-btn" data-save="scope">按此表拆分</button></div>`;
    } else if (c.answer_key === 'scope') {
      ctrl = `<div class="sr-answer"><span class="sr-dim">已在项目「投标范围」里设定，可到覆盖率面板修改</span></div>`;
    } else if (c.answer_key === 'topic_placement') {
      // **下拉选择，不让用户手打章节名** —— 实测手打必错（用户原话：
      // "用户去输入一个名称，大概率都会输错"）。候选章节与未归属主题都由后端给出。
      const topics = data.topic_options || [];
      const chapters = data.chapter_options || [];
      if (!topics.length) {
        ctrl = `<div class="sr-answer"><span class="sr-dim">没有未归属的主题，无需处理。</span></div>`;
      } else if (!chapters.length) {
        ctrl = `<div class="sr-answer"><span class="sr-dim">目录里还没有可选章节，先拆出目录再指定归属。</span>
          <button class="mw-btn" data-save="skip" data-key="topic_placement">跳过</button></div>`;
      } else {
        const opts = chapters.map(ch =>
          `<option value="${escapeHtml(ch.title)}">${'　'.repeat(Math.max(0, ch.level - 2))}${escapeHtml(ch.title)}</option>`).join('');
        const rows = topics.map(t => `
          <div class="sr-topic-row">
            <span class="sr-topic-name" title="${escapeHtml(t.topic)}">${escapeHtml(t.topic)}
              <span class="sr-dim">（${t.count} 条）</span></span>
            <select data-topic="${escapeHtml(t.topic)}">
              <option value="">默认：进「技术规范书要求应答」</option>
              ${opts}
            </select>
          </div>`).join('');
        ctrl = `<div class="sr-topic-list">${rows}</div>
          <div class="sr-answer">
            <button class="mw-btn primary" data-save="topic_placement">保存归属</button>
            <button class="mw-btn" data-save="skip" data-key="topic_placement">全部保持默认</button>
            <span class="sr-dim">只对选中的主题生效，其余按默认处理</span>
          </div>
          <div class="sr-dim" data-flash-for="topic_placement"></div>`;
      }
    } else if (c.answer_key === 'structure') {
      ctrl = `<div class="sr-answer"><button class="mw-btn" data-save="structure">结构没问题，确认</button></div>`;
    }
    const badge = c.answered ? '已作答' : (c.needs_confirm ? '待确认' : '已识别');
    const cls = c.answered ? 'done' : (c.needs_confirm ? 'warn' : 'ok');
    return `<div class="sr-conf ${cls}">
      <div class="sr-conf-head"><span class="sr-conf-badge">${badge}</span>
        <span class="sr-conf-q">${escapeHtml(c.question)}</span></div>
      <div class="sr-conf-a">${escapeHtml(c.detected)}</div>
      <div class="sr-conf-d">${escapeHtml(c.detail || '')}</div>
      ${ctrl}</div>`;
  };

  const statusCell = (r) => {
    const st = r.satisfied === 'unsatisfied' ? '不满足' : (r.satisfied === 'partial' ? '部分' : '');
    return st ? `<span class="ov-tag ${r.satisfied === 'unsatisfied' ? 'high' : 'medium'}">${st}</span>` : '';
  };

  const rpRows = (rp.items || []).map(r => `
    <tr><td>${escapeHtml(r.factor)}</td><td>${escapeHtml(r.item)}</td>
        <td class="sr-num">${r.points != null ? r.points : '—'}</td>
        <td>${r.category ? `<span class="sr-chip">${escapeHtml(r.category)}</span>` : '—'}</td>
        <td>${escapeHtml(r.package_no || '—')}</td>
        <td class="sr-crit" title="${escapeHtml(r.criterion || '')}">${escapeHtml((r.criterion || '').slice(0, 70))}${(r.criterion || '').length > 70 ? '…' : ''}</td></tr>`).join('');

  const unitRows = (su.items || []).map(u => `
    <tr class="sr-unit"><td><b>${escapeHtml(u.title)}</b></td>
        <td class="sr-num">${u.score_weight != null ? u.score_weight : '—'}</td>
        <td class="sr-num">${(u.items || []).length}</td>
        <td class="sr-crit">${(u.items || []).map(i => escapeHtml(i.title)).join('、')}</td></tr>
    ${(u.items || []).map(i => `
      <tr class="sr-sub"><td>　└ ${escapeHtml(i.title)}</td><td class="sr-num">—</td>
          <td class="sr-num">${(i.sub_items || []).length}</td>
          <td class="sr-crit">${(i.sub_items || []).map(x => escapeHtml(x.title)).join('、') || '<span class="sr-dim">—</span>'}</td></tr>`).join('')}`).join('');

  const techRows = (tm.topics || []).map(t => `
    <tr><td>${escapeHtml(t.topic)}</td><td class="sr-num">${t.count}</td>
        <td class="sr-crit">${(t.requirements || []).slice(0, 3).map(r => escapeHtml(r.title || (r.text || '').slice(0, 40))).join('；')}</td></tr>`).join('');

  const mapRows = (mp.point_to_requirement || []).map(m => `
    <tr><td>${escapeHtml(m.factor)}<div class="sr-dim">${escapeHtml(m.item)}</div></td>
        <td class="sr-num">${m.points != null ? m.points : '—'}</td>
        <td class="sr-num">${m.chapter_count}</td>
        <td class="sr-num">${m.requirement_count}</td>
        <td class="sr-crit">${(m.requirement_topics || []).map(escapeHtml).join('、') || '<span class="sr-dim">未挂到要求</span>'}</td></tr>`).join('');

  const scopeRows = (smap.entries || []).map(e => {
    const sel = e.table_no === smap.selected_table_no;
    return `<tr class="${sel ? 'sr-selected' : ''}">
      <td>${sel ? '<span class="sr-chip sel">选中</span>' : ''}表${e.table_no}</td>
      <td>${e.resolved ? `${escapeHtml(e.target)} / 标包${escapeHtml(e.package_no)}` : '<span class="sr-dim">未归属</span>'}</td>
      <td>${escapeHtml((e.package_name || e.scope_label || '—')).slice(0, 30)}</td>
      <td class="sr-crit">${(e.categories || []).map(c => `${c.label} ${c.count} 条 / ${c.score} 分`).join('　')}</td>
      <td class="sr-crit">${(e.source_files || [e.source_file]).filter(Boolean).map(f => escapeHtml(String(f).slice(0, 18))).join('、') || '—'}</td></tr>`;
  }).join('');

  const flashNow = splitPanelFlash;
  const flashKind = splitPanelFlashKind;
  splitPanelFlash = '';          // 消费一次即清（下次重开不再重复显示旧消息）
  splitPanelFlashKind = '';
  const CAT_HINT = { tech: '技术应答目录', commerce: '商务应答目录', price: '报价应答目录', other: '其他应答目录' };
  const curScope = data.project_scope || {};
  const rowHtml = (row) => {
    const isCurScope = String(curScope.target || '') === row.target
      && String(curScope.package_no || '') === String(row.package_no);
    const cells = (row.cells || []).map(c => {
      const checked = isCurScope && (data.current_categories || []).includes(c.category);
      return `<label class="pm-cell ${checked ? 'on' : ''}">
        <input type="checkbox" data-cat="${c.category}" ${checked ? 'checked' : ''}>
        <span class="pm-cell-label">${escapeHtml(c.label)}</span>
        <span class="pm-cell-num">${c.count} 个评分点 · ${c.score} 分</span>
        <span class="pm-cell-target">${escapeHtml(CAT_HINT[c.category] || '')}</span>
      </label>`;
    }).join('');
    return `<div class="pm-row ${isCurScope ? 'cur' : ''}" data-row="${escapeHtml(row.target + '|' + row.package_no)}">
      <label class="pm-pick">
        <input type="radio" name="pm-scope" value="${escapeHtml(row.target + '|' + row.package_no)}" ${isCurScope ? 'checked' : ''}>
        <span class="pm-scope">${escapeHtml(row.target)} / 标包${escapeHtml(row.package_no)}</span>
        <span class="pm-name" title="${escapeHtml(row.package_name)}">${escapeHtml(row.package_name)}</span>
        ${isCurScope ? '<span class="sr-chip sel">当前</span>' : ''}
      </label>
      <div class="pm-cells">${cells}</div>
    </div>`;
  };
  const matrixHtml = matrixRows.length
    ? matrixRows.map(rowHtml).join('')
    : '<div class="sr-empty">没有解析到评分表。请先上传招标文件与技术规范书，再点左下「拆解评分点」。</div>';

  const panes = {
    matrix: `
      <h4>拆解结果：每个标包的商务 / 技术 / 报价评分点</h4>
      ${matrixHtml}
      <div class="sr-answer" style="margin-top:10px">
        <button class="mw-btn primary" data-act="generate">按所选生成应答目录</button>
        <span class="sr-dim">选一个标包 + 勾选要生成的类别（可多选）。生成后可回到本页切换成另一份目录。</span>
      </div>
      <div class="sr-dim" data-flash-for="generate"></div>
      <div class="sr-note">当前目录包含：${
        (data.current_categories || []).map(c => CAT_HINT[c] || c).join('、') || '—'}
        ｜投标范围：${escapeHtml((curScope.target || '未设定') + ' / 标包' + (curScope.package_no || '—'))}</div>`,
    basis: `
      ${flashNow ? `<div class="sr-flash ${flashKind === 'err' ? 'err' : ''}">${escapeHtml(flashNow)}</div>` : ''}
      <h4>待确认（${pending.length}）</h4>
      ${pending.length ? pending.map(confItem).join('') : '<div class="sr-empty">没有待确认项，拆分依据已闭合。</div>'}
      <h4>已识别（${settled.length}）</h4>
      ${settled.map(confItem).join('')}
      <h4>评分地图（全文评分表与选中结果）</h4>
      <table class="sr-table"><thead><tr><th>表</th><th>标的/标包</th><th>名称</th><th>类别构成</th><th>来源文件</th></tr></thead>
        <tbody>${scopeRows}</tbody></table>
      <div class="sr-note">${escapeHtml(smap.selection?.reason || '')}${
        smap.selection?.by ? `（依据：${escapeHtml(smap.selection.by)}）` : ''}
        未选中的表不属于本次投标范围，因此不参与本次拆分。</div>`,
    points: `
      <h4>① 评分点清单（${rp.count || 0} 个，合计 ${rp.score_total != null ? rp.score_total : '—'} 分）</h4>
      <table class="sr-table"><thead><tr><th>评分因素</th><th>详细评审项</th><th class="sr-num">分值</th><th>类别</th><th>标包</th><th>评分口径</th></tr></thead>
        <tbody>${rpRows}</tbody></table>`,
    units: `
      <h4>② 评分单元与子检查项（${su.unit_count || 0} 单元 / ${su.sub_item_count || 0} 详细项 / ${su.check_item_count || 0} 子项）</h4>
      <table class="sr-table"><thead><tr><th>评分单元 / 详细评审项</th><th class="sr-num">分值</th><th class="sr-num">子项数</th><th>子检查项</th></tr></thead>
        <tbody>${unitRows}</tbody></table>`,
    tech: `
      <h4>③ 技术要求矩阵（${tm.count || 0} 条要求 / ${tm.topic_count || 0} 个主题）</h4>
      <table class="sr-table"><thead><tr><th>主题</th><th class="sr-num">条数</th><th>要求示例</th></tr></thead>
        <tbody>${techRows}</tbody></table>`,
    map: `
      <h4>④ 评分 ↔ 技术要求映射矩阵</h4>
      <table class="sr-table"><thead><tr><th>评分点</th><th class="sr-num">分值</th><th class="sr-num">章节数</th><th class="sr-num">关联要求数</th><th>要求主题</th></tr></thead>
        <tbody>${mapRows}</tbody></table>
      <div class="sr-note">未落点要求 ${(mp.unmapped_requirements || []).length} 条；
        未关联评分点的要求 ${(mp.requirements_without_point || []).length} 条。
        需要调整归属或范围时，在「拆分依据」里作答后重新拆分即可。</div>`,
  };

  const el = document.getElementById('split-panel-inline') || (() => {
    const d = document.createElement('div');
    d.id = 'split-panel-inline';
    (blocksList?.parentElement || document.body).insertBefore(d, blocksList);
    return d;
  })();
  splitPanelExpanded = true;
  el.hidden = false;
  el.innerHTML = `
    <div class="sr-box">
      <div class="sr-head">
        <b>拆解评分点</b>
        <span class="sr-step ${outlinePending ? 'on' : ''}">① 拆解评分点</span>
        <span class="sr-step-arrow">→</span>
        <span class="sr-step ${outlinePending ? '' : 'on'}">② 生成应答目录</span>
        <span class="sr-dim" id="sr-sub">${escapeHtml(smap.selection?.reason || '')}</span>
        <button class="mw-btn" data-act="collapse" style="margin-left:auto;">收起，看目录</button>
      </div>
      <div class="sr-metrics">${metricHtml}</div>
      <div class="sr-tabs">${tabHtml}</div>
      <div class="sr-panes">${Object.entries(panes).map(([id, html]) =>
        `<div class="sr-pane ${id === activeTab ? 'active' : ''}" data-pane="${id}">${html}</div>`).join('')}</div>
      <div class="sr-foot">
        <span class="sr-dim">${outlinePending
          ? `目录还没生成：${rp.count || 0} 个评分点、合计 ${rp.score_total != null ? rp.score_total : '—'} 分。
             请在上方选一个标包、勾选类别，再点「按所选生成应答目录」。`
          : `目录已生成${draftRevision ? `（第 ${draftRevision} 版）` : ''}：${su.unit_count || 0} 个评分单元 / ${su.sub_item_count || 0} 个详细评审项。
             需要换一份（技术 ⇄ 商务）就回「① 拆解结果」改选后重新生成。`}</span>
        <button class="mw-btn" data-act="redraft">重新拆解评分点</button>
        ${outlinePending ? '' : '<button class="mw-btn primary" data-act="collapse">收起，看目录</button>'}
      </div>
    </div>`;

  if (!document.getElementById('sr-style')) {
    const st = document.createElement('style');
    st.id = 'sr-style';
    st.textContent = `
  /* 内联展开（不再是浮层）：占满目录栏宽度，确认后收起露出目录 */
  #split-panel-inline{margin:8px 12px 12px}
  .sr-box{background:var(--surface,#fff);border:1px solid var(--line);border-radius:10px;
    display:flex;flex-direction:column;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.06)}
  .sr-head{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line)}
  .sr-head b{font-size:14px}
  .sr-step{font-size:11.5px;color:var(--muted);padding:1px 8px;border-radius:999px;border:1px solid var(--line)}
  .sr-step.on{background:#e0edff;border-color:#bfdbfe;color:#1d4ed8;font-weight:600}
  .sr-step-arrow{color:var(--muted);font-size:11.5px}
  .sr-metrics{display:flex;gap:10px;padding:12px 18px 0;flex-wrap:wrap}
  .sr-metric{flex:1 1 130px;border:1px solid var(--line);border-radius:8px;padding:7px 10px;background:var(--surface-2,#faf9f7)}
  .sr-metric.ok{border-color:#a7f3d0;background:#f0fdf4}
  .sr-metric.warn{border-color:#fcd34d;background:#fffbeb}
  .sr-metric-label{font-size:11.5px;color:var(--muted)}
  .sr-metric-value{font-size:15px;font-weight:600;line-height:1.35}
  .sr-metric-sub{font-size:11px;color:var(--muted)}
  .sr-tabs{display:flex;gap:2px;padding:10px 18px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .sr-tab{border:none;background:none;font:inherit;font-size:12.5px;padding:7px 12px;cursor:pointer;
    color:var(--muted);border-bottom:2px solid transparent;display:inline-flex;align-items:center;gap:6px}
  .sr-tab:hover{color:inherit}
  .sr-tab.active{color:inherit;font-weight:600;border-bottom-color:#2563eb}
  .sr-tab-badge{background:var(--surface-2,#f1f0ee);border-radius:999px;padding:0 6px;font-size:11px;
    color:var(--muted);margin-left:2px}
  .sr-tab.active .sr-tab-badge{background:#e0edff;color:#1d4ed8}
  /* 内联展开时用视口高度的一半多，一屏内看完大部分表格；超出则面板内部滚动 */
  .sr-panes{overflow:hidden;display:flex;max-height:58vh}
  .sr-pane{display:none;overflow:auto;padding:6px 18px 18px;width:100%;max-height:58vh}
  .sr-pane.active{display:block}
  .sr-pane h4{margin:14px 0 6px;font-size:12.5px;color:var(--muted);font-weight:600}
  .sr-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
  .sr-table th{position:sticky;top:0;background:var(--surface,#fff);z-index:1;white-space:nowrap;
    color:var(--muted);font-weight:500;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  .sr-table td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  .sr-table tr:hover td{background:var(--surface-2,#faf9f7)}
  .sr-table td.sr-num,.sr-table th.sr-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .sr-unit td{background:var(--surface-2,#faf9f7);font-weight:600}
  .sr-sub td{color:var(--muted)}
  .sr-crit{color:var(--muted);line-height:1.5;max-width:520px}
  .sr-dim{color:var(--muted);font-size:11.5px}
  .sr-chip{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;
    background:var(--surface-2,#f1f0ee);color:var(--muted)}
  .sr-chip.sel{background:#e0edff;color:#1d4ed8;margin-right:4px}
  .sr-selected td{background:#f0f7ff}
  .sr-conf{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:12.5px}
  .sr-conf.warn{background:#fffbeb;border-color:#fcd34d}
  .sr-conf.done{background:#f0fdf4;border-color:#a7f3d0}
  .sr-conf-head{display:flex;align-items:center;gap:8px}
  .sr-conf-q{font-weight:600}
  .sr-conf-badge{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
  .sr-conf.warn .sr-conf-badge{border-color:#fcd34d;color:#92400e}
  .sr-conf.done .sr-conf-badge{border-color:#a7f3d0;color:#047857}
  .sr-conf-a{margin-top:3px}
  .sr-conf-d{color:var(--muted);font-size:11.5px;line-height:1.5}
  .sr-answer{margin-top:7px;display:flex;gap:8px;align-items:flex-start}
  .sr-answer select,.sr-answer textarea{font:inherit;font-size:12.5px;padding:4px 7px;
    border:1px solid var(--line);border-radius:6px;max-width:100%}
  .sr-answer textarea{flex:1;min-width:300px}
  .sr-empty{color:var(--muted);font-size:12.5px;padding:6px 0}
  .sr-flash{margin:8px 0;padding:7px 11px;border-radius:7px;font-size:12.5px;
    background:#ecfdf5;border:1px solid #a7f3d0;color:#047857}
  .sr-flash.err{background:#fef2f2;border-color:#fca5a5;color:#b91c1c}
  .sr-inline-err{color:#b91c1c}
  .sr-topic-list{max-height:38vh;overflow:auto;border:1px solid var(--line);border-radius:8px;
    padding:6px 8px;margin-top:6px;background:var(--surface-2,#faf9f7)}
  .sr-topic-row{display:flex;align-items:center;gap:10px;padding:3px 0}
  .sr-topic-name{flex:0 0 46%;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sr-topic-row select{flex:1;font:inherit;font-size:12.5px;padding:2px 6px;
    border:1px solid var(--line);border-radius:6px;background:#fff}
  .pm-row{border:1px solid var(--line);border-radius:9px;padding:8px 10px;margin:7px 0;
    background:var(--surface-2,#faf9f7)}
  .pm-row.cur{border-color:#bfdbfe;background:#f0f7ff}
  .pm-pick{display:flex;align-items:center;gap:8px;cursor:pointer}
  .pm-scope{font-weight:600;font-size:12.5px;white-space:nowrap}
  .pm-name{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pm-cells{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}
  .pm-cell{display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:8px;
    padding:5px 9px;background:#fff;cursor:pointer;font-size:12.5px}
  .pm-cell.on{border-color:#2563eb;background:#eff6ff}
  .pm-cell-label{font-weight:600}
  .pm-cell-num{color:var(--muted)}
  .pm-cell-target{color:#1d4ed8;font-size:11.5px}
  .pm-cell input{accent-color:#2563eb}
  .sr-foot{display:flex;align-items:center;gap:10px;padding:10px 18px;border-top:1px solid var(--line);
    background:var(--surface-2,#faf9f7)}
  .sr-foot .mw-btn.primary{margin-left:auto;background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
  .sr-foot .mw-btn.primary:hover{background:#1d4ed8}
  .sr-note{margin-top:10px;color:var(--muted);font-size:11.5px;line-height:1.6}`;
    document.head.appendChild(st);
  }
  // **内联展开**（不再是浮层）：填在目录上方的静态容器里，确认后收起、露出目录。
  // 用户要求：「直接将解析面板展开确认后再收起，展示目录」。
  // 展开期间隐藏"待确认"横幅（它只是引导用户来看这个面板，展开后重复）
  const banner = document.getElementById('split-confirm-banner');
  if (banner) banner.hidden = true;
  el.scrollIntoView({ block: 'start' });

  el.querySelectorAll('.sr-tab').forEach(tab => tab.addEventListener('click', () => {
    el.querySelectorAll('.sr-tab').forEach(t => t.classList.toggle('active', t === tab));
    el.querySelectorAll('.sr-pane').forEach(p =>
      p.classList.toggle('active', p.dataset.pane === tab.dataset.tab));
  }));

  el.addEventListener('click', async (e) => {
    // 收起：拆下面板，露出目录（确认与否都能收起，收起不等于确认）
    if (e.target.closest?.('[data-act="collapse"]')) { collapseSplitPanel(); return; }

    // 重新拆分：复用状态栏那个入口（带后端预检，原料变过时引导到「重新提炼」）
    if (e.target.closest?.('[data-act="redraft"]')) {
      el.remove();
      await redraftOutline();
      return;
    }

    // 「按所选生成应答目录」：设投标范围 → 存类别选择 → 重新派生
    if (e.target.closest?.('[data-act="generate"]')) {
      const btnG = e.target.closest('[data-act="generate"]');
      const slot = document.querySelector('[data-flash-for="generate"]');
      const pickedRow = document.querySelector('#split-panel-inline input[name="pm-scope"]:checked');
      if (!pickedRow) {
        if (slot) { slot.textContent = '请先选择一个标包。'; slot.className = 'sr-dim sr-inline-err'; }
        return;
      }
      const [target, no] = pickedRow.value.split('|');
      const row = (data.package_matrix || []).find(r =>
        r.target === target && String(r.package_no) === String(no)) || {};
      const cats = [...document.querySelectorAll('#split-panel-inline [data-cat]:checked')]
        .map(x => x.dataset.cat);
      if (!cats.length) {
        if (slot) { slot.textContent = '请至少勾选一个类别（技术 / 商务 / 报价）。'; slot.className = 'sr-dim sr-inline-err'; }
        return;
      }
      btnG.disabled = true;
      if (slot) { slot.textContent = '正在保存选择…'; slot.className = 'sr-dim'; }
      try {
        await api.workflow.setScope(projectId, {
          target: target || '', package_no: no || '',
          package_name: row.package_name || '',
        });
        const tidG = await resolvePanelThreadId(null);
        await api.workflow.answerSplit(projectId, {
          key: 'categories', value: { categories: cats }, threadId: tidG,
        });
        const label = row.cells.filter(c => cats.includes(c.category)).map(c => c.label).join('+');
        splitPanelFlash = `已选择 ${target}/标包${no} · ${label}，正在按此生成应答目录…`;
        if (slot) { slot.textContent = splitPanelFlash; slot.className = 'sr-dim'; }
        // 复用状态栏入口（带后端预检；原料变过时会引导到「重新提炼」）
        await redraftOutline();
      } catch (err) {
        btnG.disabled = false;
        if (slot) { slot.textContent = '生成失败：' + String(err?.message || err).slice(0, 120); slot.className = 'sr-dim sr-inline-err'; }
      }
      return;
    }

    // 「跳过（暂不指定）」：可选项标记为已处理，不影响后续确认
    if (e.target.closest?.('[data-save="skip"]')) {
      const key = e.target.closest('[data-save="skip"]').dataset.key;
      try {
        const tidS = await resolvePanelThreadId(null);
        await api.workflow.answerSplit(projectId, {
          key, value: { skipped: true }, threadId: tidS,
        });
        splitPanelFlash = '已跳过该项（不指定归属，拆分照常进行）';
      } catch (err) {
        splitPanelFlash = '保存失败：' + String(err).slice(0, 120);
        splitPanelFlashKind = 'err';
        inlineFlashFromSave(err);
        return;
      }
      document.getElementById('split-panel-inline')?.remove();
      splitPanelExpanded = false;
      await openSplitReport();
      return;
    }

    // 第 ① 步：确认评分点（面板里的按钮与页脚主按钮走同一条路径）
    // → 落库 review_points，重开面板进入第 ② 步
    if (e.target.closest?.('[data-act="confirm-points"]')
        || e.target.closest?.('[data-save="review_points"]')) {
      const btnP = e.target.closest('[data-act="confirm-points"]')
        || e.target.closest('[data-save="review_points"]');
      btnP.disabled = true;
      try {
        const tidP = await resolvePanelThreadId(null);
        await api.workflow.answerSplit(projectId, {
          key: 'review_points',
          value: { confirmed: true, revision: Number(draftRevision || 0),
                   count: Number(rp.count || 0), score: rp.score_total },
          threadId: tidP,
        });
        splitPanelFlash = `已确认评分点清单（${rp.count || 0} 个，合计 ${
          rp.score_total != null ? rp.score_total : '—'} 分）。请继续确认目录拆分。`;
      } catch (err) {
        btnP.disabled = false;
        splitPanelFlash = '保存失败：' + String(err).slice(0, 120);
        splitPanelFlashKind = 'err';
        inlineFlashFromSave(err);
        return;
      }
      await openSplitReport();          // 重开：此时阶段已是"确认目录拆分"
      return;
    }

    // 第 ② 步：确认目录拆分 → 记住这一版已确认 → 收起面板，输出最终应答目录
    if (e.target.closest?.('[data-act="confirm"]')) {
      const confirmBtn = e.target.closest('[data-act="confirm"]');
      confirmBtn.disabled = true;
      try {
        const tidC = await resolvePanelThreadId(null);
        await api.workflow.answerSplit(projectId, {
          key: 'structure',
          value: { confirmed: true, revision: Number(draftRevision || 0) },
          threadId: tidC,
        });
      } catch (err) { /* 落库失败也放行：确认首先是本地状态，不能把用户卡住 */ }
      markRevisionConfirmed(draftRevision);
      // 确认 → **收起面板，展示目录**（用户要的就是这个动作序列）
      collapseSplitPanel();
      await loadExistingBlocks({ silent: true, keepSelection: true });
      showConfirmedBanner();
      document.querySelector('.chapter-tree-head')?.scrollIntoView({ block: 'start' })
        || blocksList?.scrollIntoView({ block: 'start' });
      return;
    }

    const btn = e.target.closest?.('[data-save]');
    if (!btn) return;
    const kind = btn.dataset.save;
    const host = btn.closest('.sr-answer');
    let value = {};
    if (kind === 'scope') {
      value = { table_no: Number(host.querySelector('[data-answer="scope"]').value) };
    } else if (kind === 'topic_placement') {
      // 下拉选择：只收集"选了具体章节"的那些主题
      const map = {};
      document.querySelectorAll('#split-panel-inline [data-topic]').forEach((sel) => {
        if (sel.value) map[sel.dataset.topic] = sel.value;
      });
      const n = Object.keys(map).length;
      if (!n) {
        splitPanelFlash = '还没有选择任何归属。想指定就在主题右侧选一章；不想指定就点「全部保持默认」。';
        splitPanelFlashKind = 'err';
        inlineFlash(host, splitPanelFlash, splitPanelFlashKind);
        return;
      }
      value = map;
      splitPanelFlash = `已保存 ${n} 个主题的归属；其余保持默认。下次「拆解评分点」按它挂载。`;
    } else if (kind === 'structure') {
      value = { confirmed: true };
      splitPanelFlash = '结构已确认。';
    }
    btn.disabled = true;
    try {
      const tid2 = await resolvePanelThreadId(null);
      await api.workflow.answerSplit(projectId, { key: kind, value, threadId: tid2 });
      document.getElementById('split-panel-inline')?.remove();
      splitPanelExpanded = false;
      await openSplitReport();          // 重新拉取：作答已反映到确认项（flash 会带回来）
    } catch (err) {
      btn.disabled = false;
      splitPanelFlash = '保存失败：' + String(err).slice(0, 120);
      splitPanelFlashKind = 'err';
      inlineFlashFromSave(err);
    }
  });
}

async function warnIfOutlineNotDerived(tid) {
  if (!blocksList) return;
  tid = await resolvePanelThreadId(tid);
  if (!tid) return;
  let chk = null;
  try { chk = await api.workflow.redraftCheck(tid); } catch { return; }
  const stale = document.getElementById('raw-toc-warn');
  if (!chk || chk.has_outline || !chk.source_sections) { stale?.remove(); return; }
  if (stale) return;                       // 已经提示过，不重复插
  const box = document.createElement('div');
  box.id = 'raw-toc-warn';
  box.className = 'raw-toc-warn';
  box.innerHTML = `
    <strong>当前显示的是招标文件原始目录（${chk.source_sections} 节），应答目录还没有派生。</strong>
    <span>那次运行在解析阶段就中断了。请点上方「重新拆分目录」按已解析的内容派生应答目录；
          确认目录前不要直接开始提炼。</span>`;
  blocksList.parentElement?.insertBefore(box, blocksList);
}

/** 两阶段提示：确认前"以下目录待确认"，确认后"这是最终应答目录"。 */
function showConfirmedBanner() {
  const box = document.getElementById('split-confirm-banner');
  if (!box) return;
  if (!blocksList || !totalBlocks) { box.hidden = true; box.innerHTML = ''; return; }
  const rev = Number(draftRevision || 0);
  const confirmed = rev > 0 && rev <= getConfirmedRevision();
  box.hidden = false;
  box.className = confirmed ? 'split-banner done' : 'split-banner todo';
  box.innerHTML = confirmed
    ? `<strong>拆分结果已确认${rev ? `（第 ${rev} 版）` : ''}，以下是最终应答目录。</strong>
       <span>需要调整范围或主题归属时，点右上「拆分清单」重新确认。</span>`
    : `<strong>以下目录待确认 —— 确认后才是最终应答目录。</strong>
       <span>点右上「拆分清单」查看评分点 / 评分单元与子检查项 / 技术要求矩阵 / 映射矩阵，并确认。</span>`;
  if (!box.dataset.bound) {
    box.dataset.bound = '1';
    box.addEventListener('click', () => openSplitReport());
    box.style.cursor = 'pointer';
  }
}

async function loadExistingBlocks({ silent = false, keepSelection = false } = {}) {
  // 这里自己解析 threadId（优先级同 boot 段：内存 → URL → sessionStorage），
  // 因为 boot 里首次调用发生在 currentThreadId 赋值之前 —— 只读它会把 URL
  // 指定的 run 丢掉，拿到服务端的「最新 run」。
  let tid = currentThreadId || params.get('threadId') || getThread(projectId);
  // 会话里记着的线程可能是**已作废/失败**的那次运行：它的 checkpoint 常停在中间态，
  // 页面照着它渲染就会出现"目录混着商务报价评分点""确认过的目录却不是最终版"这类
  // 自相矛盾（实测：页面显示 276 节 / 25 个评分点，而最新有效运行是 212 节 / 13 个）。
  // 所以解析出的线程若已作废/失败，改用它之前最近的有效运行。
  try {
    const runs = await api.workflow.workflowRuns(projectId);
    const cur = runs.find(r => r.thread_id === tid);
    // 两种情况都要切：① 记着的线程已作废/失败；② **没有线程**（首访 / sessionStorage 空）——
    // 后者服务端会回退到"最新 run"，而最新那个可能正是已作废的
    //（实测：目录树渲染了 276 节的作废运行，而有效运行是 212 节，
    //  覆盖率面板与目录树因此自相矛盾）。
    const needSwitch = !tid || !cur
      || cur.stage === 'aborted' || cur.stage === 'failed';
    if (needSwitch) {
      const alive = runs.find(r => r.stage !== 'aborted' && r.stage !== 'failed');
      if (alive) {
        tid = alive.thread_id;
        currentThreadId = tid;
        setThread(projectId, tid);
      }
    }
  } catch (e) { /* 拿不到 run 列表就按原线程渲染 */ }
  // 目录 + 面板数据**并行取**，然后一次渲染：以前是"目录先画 → 再异步插面板"，
  // 每插一块都把目录往下推，进入页面时会抖动/闪（用户反馈）。
  const [blocks] = await Promise.all([
    api.blocks.list(projectId, tid),
    // 目录可能还没派生（运行在解析阶段中断）——先把这件事告诉用户，再谈其它
    warnIfOutlineNotDerived(tid),
    // 覆盖率面板：闸门上的"我该拿的分/该响应的要求都安排上了吗"
    refreshOutlineCoverage(tid),
  ]);
  // 两阶段 UI：确认前提示"确认后才是最终目录"，确认后说明"这是最终目录"
  // 首帧骨架退场（有目录时由树顶上，没有时露出空态文案）
  const loadingEl = document.getElementById('blocks-loading');
  if (loadingEl) loadingEl.hidden = true;
  if (!blocks?.length) {
    // 库里没有任何章节：清掉列表与计数。早退会把上一次运行的残留视图（卡片、进度环、
    // 状态栏）留在页面上，与「刷新后看到的样子」不一致。
    const le = document.getElementById('blocks-loading');
    if (le) le.hidden = true;
    const ee = document.getElementById('blocks-empty');
    if (ee) ee.hidden = false;
    resetWorkflowUi();
    if (!silent) setStatusBar('idle', '', 0, 0, 0);
    return;
  }
  const emptyEl = document.getElementById('blocks-empty');
  if (emptyEl) emptyEl.hidden = true;
  blocksList.innerHTML = '';
  blocksById.clear();
  blocksByOrder.length = 0;
  if (!keepSelection) selectedMatchBlockId = null;
  // 应答条目 + 匹配总览先拿到：详情要按**条目**分组、树/筛选要按依据计数，
  // 后拿到的话会先渲染成章节级再闪一下重排。
  await Promise.all([loadResponseItems(), loadMatchOverview()]);
  // 是否「已提炼」以 status 字段为权威（见 isDoneBlock）
  const isDone = isDoneBlock;

  totalBlocks = blocks.length;
  // 横幅文案依赖 totalBlocks，必须在赋值**之后**渲染（放前面会导致横幅永不出现）
  showConfirmedBanner();
  doneBlocks = blocks.filter(isDone).length;
  blocks.forEach(b => {
    const blockId = b.block_id || b.id;
    blocksByOrder.push(b);
    blocksById.set(blockId, b);
    renderBlockCard(b, badgeStatusOf(b));
  });
  renderOutlineTree();
  applyOutlineExpansion(false);
  buildToc(blocks);
  blocks.filter(isDone).forEach(b => markTocDone(b.block_id || b.id));
  updateSidebarProgress();
  refreshMaterialUsage();      // 素材列表的「供 N 章 · M 条」按刚加载的依据重算
  renderMatDrawer();           // 抽屉开着的话跟着更新（同一份 lastSrcMaterials）
  refreshChapterEvidenceIndex();
  // 第①步（只拆评分点）之后：目录尚未生成 →
  //   1) **隐藏上一版目录**（否则和"只拆评分点"自相矛盾：下面还挂着旧目录）；
  //   2) 显示占位指引；3) 展开「拆解结果」让用户选标包+类别。
  // 必须放在**树渲染之后**：早于树渲染会被后续的构建/切换盖掉（实测面板不出现）。
  const pendingEl = document.getElementById('blocks-pending');
  if (outlinePending) {
    blocksList.hidden = true;
    blocksList.style.display = 'none';
    if (pendingEl) pendingEl.hidden = false;
    splitPanelAutoOpenedFor = 0;
    openSplitReport();
  } else {
    blocksList.hidden = false;
    blocksList.style.display = '';
    if (pendingEl) pendingEl.hidden = true;
  }
  applyPageView();             // 先定视图：下面的总览/筛选/详情都按当前视图渲染
  renderMatchSummary();
  renderMatchTreeHead();
  applyMatchFilter();
  // 跨模块跳转带过来的 blockId：直接选中那一章（素材库 → 方案编辑 → 切回来，上下文不丢）
  const wantBid = params.get('blockId');
  if (wantBid && blocksById.has(wantBid)) {
    workspace.selectedBlockId = wantBid;
    selectMatchChapter(wantBid);
  } else {
    renderMatchDetail();
  }
  if (silent) return;
  if (doneBlocks > 0) {
    const pct = Math.round(doneBlocks / totalBlocks * 100);
    const isMatching = document.body.dataset.view === 'matching';
    if (doneBlocks >= totalBlocks) {
      setStatusBar('done', isMatching ? '匹配完成' : '提炼完成', pct, totalBlocks, doneBlocks);
    } else {
      // 部分提炼/匹配（取消后保留的进度）：进入 paused 态，让用户继续 / 重新触发。
      // 大纲视图用统一文案（还剩 N 节 + 继续提炼只补剩余），素材库视图保持原样。
      if (!isMatching) {
        setPartialOutlineBar();
      } else {
        setStatusBar('paused', `已保留 ${doneBlocks}/${totalBlocks} 个已匹配章节`,
                     pct, totalBlocks, doneBlocks);
      }
    }
  } else if (totalBlocks > 0 && currentStage !== 'outline_review') {
    // 已解析章节但一节都没提炼。**排除闸门 1**：目录已拆好、等用户确认，这是正常
    // 等待态而非中断 —— 写「可继续提炼」会把用户从「先确认目录」引偏，paused 的
    // 按钮组也会盖掉闸门的「确认目录，开始提炼要点」。闸门的文案由 boot /
    // onGateOpen / restoreSettledThread 负责。
    setStatusBar('paused', `已解析 ${totalBlocks} 个章节，可继续提炼`, 0, totalBlocks, 0);
  }
}

// 运行终止 / 断流后的唯一对齐手段：以数据库为准重绘，等价于用户手动刷新一次。
// 运行期间页面上的章节来自 SSE 帧，库只在 parse / outline_draft 两处写入；两者不一致时
// 只有重读库能把卡片、进度、状态栏一起拉回真实状态。否则卡片会永远停在最后一帧的样子
// （最常见的是「提炼中…」+ 进度条卡在中途）。
async function resyncFromDb() {
  // 闸门期间不重绘：用户可能正在编辑目录，整版替换会丢掉改动（与 scheduleOutlineTree 同一约定）
  if (extractionState === 'gate_outline' || extractionState === 'gate_materials') return;
  try {
    await loadExistingBlocks();
  } catch (e) {
    console.warn('[workflow] 从数据库重同步失败', e);
  }
}

// ── 提炼流程（新 workflow driver） ──
function resetWorkflowUi() {
  blocksList.innerHTML = '';
  blocksEmpty.innerHTML = '<p>上传应标文件后点击「拆解评分点」开始</p>';
  blocksEmpty.style.display = 'block';
  sidebarToc.innerHTML = '';
  totalBlocks = 0; doneBlocks = 0;
  blocksByOrder.length = 0;
  blocksById.clear();
  currentGate = null;
  draftRevision = 0;
  outlineError = '';
  updateSidebarProgress();
}

// 运行失败原因：runner 把异常写进 checkpoint 的 errors（operator.add 追加，取最后一条）
function lastRunErrorMessage(state) {
  const errs = Array.isArray(state?.errors) ? state.errors : [];
  const last = errs[errs.length - 1];
  return (last && (last.message || last.error)) || '';
}

// ── 等待帧期间的兜底轮询（SSE 无回放的自救）──
// SSE 是实时队列：浏览器连晚了、后端重启过、或任务在本页订阅之前就跑完了，都会导致
// **一帧都收不到**，页面永远停在「正在梳理应答目录…」。实测踩过：运行早已停在闸门 1，
// 页面还在等一个不会再来的帧，用户只能看着转圈。
// 这里每 3 秒读一次 state：一旦这个 run 已经不在「跑」的阶段（或已是孤儿），就按 state
// 恢复视图。停在 extracting 之外的任何状态都会自动停表（见 setStatusBar）。
const SETTLE_POLL_MS = 3000;
let settlePollTimer = null;

function stopSettlePoll() {
  if (settlePollTimer) {
    clearInterval(settlePollTimer);
    settlePollTimer = null;
  }
}

function startSettlePoll(threadId) {
  stopSettlePoll();
  if (!threadId) return;
  settlePollTimer = window.setInterval(async () => {
    if (extractionState !== 'extracting') { stopSettlePoll(); return; }
    try {
      const s = await api.workflow.state(threadId);
      const stage = s?.stage;
      if (!stage) return;                       // 读不到就下一轮再试
      if (activeStages.has(stage) && !isOrphanedRun(s)) return;   // 确实还在跑，继续等
      // checkpoint 停在闸门态也可能是"长阶段正在跑"：extract / match 节点只能在自己
      // 结束时写 checkpoint，这十几分钟里 /state 一直读回闸门 1 的 outline_review。
      // 唯一能区分的是 workflow_runs —— runner 在续跑前把它标成 extracting / matching。
      // 进程还认得这个 run（alive !== false）时才信它；后端重启过则视为已中断。
      if (s?.alive !== false) {
        const running = await api.workflow.latestRunStage(projectId).catch(() => null);
        if (running === 'extracting' || running === 'matching') return;   // 后台确实在跑
      }
      stopSettlePoll();
      console.info('[workflow] 兜底轮询：运行已停在', stage, '，按 state 恢复视图');
      await restoreSettledThread(threadId, stage);
    } catch { /* 忽略：下一轮再试 */ }
  }, SETTLE_POLL_MS);
}

function closeStream() {
  stopSettlePoll();
  if (currentEventSource) {
    try { currentEventSource.close(); } catch {}
    currentEventSource = null;
  }
}

window.addEventListener('pagehide', closeStream);

// 原生 SSE 连接失败（404 / 断线）≠ 工作流报错。最常见的原因是后端重启过：thread 不在
// runner 的内存 _runs 里，stream 直接 404，而 checkpoint 里状态完好 —— 这时 POST /recover
// 就能把 _Run 重建回来（停在闸门时 ainvoke(None) 是空跑，不会触发新的模型调用，也就不会
// 产生新的费用）。正在跑的 stage 一律不擅自续跑，只把原因显示出来，避免把「重出目录失败：
// …」这类更具体的状态栏文案盖掉。
const RECOVERABLE_STAGES = new Set(['outline_review', 'materials_review', 'report_review', 'paused',
                                    // extracting / matching：闸门 1 放行后的长阶段。checkpoint
                                    // 停在闸门态但 workflow_runs 说这两个值 —— 断流要重连，
                                    // 不是"任务不在本次服务运行中"。
                                    'extracting', 'matching']);
const MAX_STREAM_RECOVER = 2;
let streamRecoverCount = 0;

// 后台是否真的在跑长阶段？checkpoint 只在节点结束时变，所以 extract / match 期间
// /state 读回的是闸门态；能回答这个问题的只有 workflow_runs（runner 续跑前写的）。
// 进程不认得这个 run（alive === false，如后端重启）时不采信，避免陈旧的 extracting
// 让页面永远等下去。
async function longPhaseRunning(snap) {
  if (snap?.alive === false) return null;
  const running = await api.workflow.latestRunStage(projectId).catch(() => null);
  return (running === 'extracting' || running === 'matching') ? running : null;
}

async function recoverStreamAfterDrop() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  if (!tid) return;
  // **先读状态再决定**，别一断流就喊"后端可能已重启"。
  // 逐节提炼每跑完一轮，后端都会主动关掉事件流（emitter.aclose）；这属于正常收尾，
  // 而"恢复额度"只在闸门事件里重置（提炼过程没有闸门事件）—— 于是连着跑完两轮之后，
  // 第三次关流必然撞上额度上限，弹出那条**假警报**。先看 state 就能区分：
  const snap = await api.workflow.state(tid).catch(() => null);
  if (!snap || !snap.project_id) {
    // 连 checkpoint 都读不到：这个 thread 真的不在本次服务里（后端重启 / 已被清理）
    await resyncFromDb();
    setStatusBar('extracting',
      '事件流已断开：该任务不在本次服务运行中（后端可能已重启）。请刷新页面后重试。',
      totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    return;
  }
  if (await longPhaseRunning(snap)) {
    // 提炼/匹配还在跑：只是连接掉了，重连继续等帧（不 recover，免得空跑一轮）
    attachStream(tid);
    return;
  }
  if (SETTLED_STAGES.has(snap.stage)) {
    // 停在闸门 / 暂停 / 终态 = 这一轮已经收尾，SSE 关掉是正常的：按 state 恢复视图即可。
    // 以前这里会先 recover（在后端再起一个空跑 task → 又发一次 done → 又关流 → 又触发
    // 断流），来回抖动还攒额度。
    await restoreSettledThread(tid, snap.stage);
    return;
  }
  // 图确实停在中途、进程也不认得它（alive=false）：才值得试一次 recover 把 _Run 建回来
  if (!RECOVERABLE_STAGES.has(currentStage) || streamRecoverCount >= MAX_STREAM_RECOVER) {
    await resyncFromDb();
    setStatusBar('extracting',
      '事件流已断开，且按检查点也接不回来（该任务可能已被清理）。请刷新页面后重试。',
      totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    return;
  }
  streamRecoverCount++;
  try {
    // 这里只重建 _Run + 重连，不动视图：调用方（startMatching）可能正在推进图，
    // 按 state 去"恢复闸门视图"会和它抢状态。停住等帧的坑在 startExtraction 里判。
    await api.workflow.recover(tid);
    attachStream(tid); // emitter 是新的，必须重连才能收到后续帧
  } catch (e) {
    console.warn('[workflow] 断线恢复失败', e);
    setStatusBar('extracting',
      '事件流已断开，按检查点恢复也失败（该任务可能已被清理）。请刷新页面后重试。',
      0, totalBlocks, doneBlocks);
  }
}

function attachStream(threadId) {
  closeStream();
  startSettlePoll(threadId);   // 开始等帧 → 同时开始兜底轮询
  currentEventSource = api.workflow.stream(threadId, {
    onStreamError: () => { void recoverStreamAfterDrop(); },
    // 收到**任意**一帧就说明连接是好的 → 重置断流恢复额度。
    // 只靠 stage_change 重置不够：逐节提炼期间没有闸门事件，连续跑完两轮之后
    // 第三次"正常关流"就会撞上额度上限、弹出假的"事件流已断开"。
    onAnyEvent: () => { streamRecoverCount = 0; },
    onStageChange: (data) => {
      streamRecoverCount = 0; // 收到真实帧说明连接是好的，恢复额度重置
      if (typeof data?.stage === 'string') {
        setStage(data.stage);
      }
    },
    onParseProgress: (data) => {
      const step = data?.step || '解析要求文件';
      const cur = data?.current ?? 0;
      const tot = data?.total ?? 0;
      // 仅在 parse_node 末尾显式发出的「解析完成」帧锁定总章节数。
      // parser 内部 sub-step（"切页 2/2" 等）也满足 cur===tot，但 tot 是 sub-step 的而非
      // 章节总数，会让 onOutlineExtract 的进度百分比超 100%。
      if (tot > 0 && step === '解析完成') {
        totalBlocks = tot;
        loadExistingBlocks({ silent: true }).catch(e => console.warn('loadExisting on parse_done failed', e));
      }
      const pct = tot ? Math.round(cur / tot * 100) : 0;
      setStatusBar('extracting', `张衡解析：${step} ${cur}/${tot}`, pct, totalBlocks || tot, doneBlocks);
      // 主区占位反馈：parse 阶段尚无 block 卡片，给用户进度感
      if (!blocksById.size) {
        blocksEmpty.innerHTML = `<p>正在解析文档结构…(${escapeHtml(step)} ${cur}/${tot})</p>`;
        blocksEmpty.style.display = 'block';
      }
    },
    onOutlineExtractStart: (data) => {
      // 章节真正开始处理：把对应卡片切到 'extracting' 态（骨架屏 + 闪烁进度条）。
      // 由于 parse 阶段已 silent 加载占位卡，这里直接找卡片切状态即可。
      const blockId = data?.block_id;
      if (!blockId) return;
      blocksEmpty.style.display = 'none';
      const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
      if (!card) return;
      const badge = card.querySelector('.block-status-badge');
      if (badge) {
        badge.className = 'block-status-badge extracting';
        badge.textContent = '提炼中…';
      }
      const prog = card.querySelector('.block-card-progress');
      if (prog) prog.classList.add('active');
      // 闸门 1 的卡片不带 8 字段网格（那时一个都没提炼）。到"提炼中"挂一个**单行占位**，
      // 不挂 7 行空骨架：逐节提炼是串行的，每节要跑十几秒到一分钟，7 个空字段看着
      // 像"这一节没内容"（用户明确反馈过这个体验）。结果到达时再整块换成 8 字段网格。
      mountExtractingPlaceholder(card);
      // 自动展开当前节并把展开状态**存下来**：断流/刷新触发的一次 resync 不该把用户
      // 正在看的内容收起来（此前只改 DOM，不落 sessionStorage，resync 后即丢失）
      const node = outlineNodeEl(blockId);
      if (node) {
        node.classList.add('open');
        const openSet = readOpenSet();
        openSet.add(blockId);
        writeOpenSet(openSet);
      }
      syncOutlineRow(blockId);
      // 滚动到当前正在提炼的节点，但**只在它不在视口内时**滚：
      // 33 节文档里每节都抢一次滚动，用户想回看已提炼的内容就会被反复拽走。
      if (!document.querySelector('.block-card.scroll-locked')) {
        const rect = card.getBoundingClientRect();
        const offscreen = rect.top < 0 || rect.bottom > window.innerHeight;
        if (offscreen) {
          card.classList.add('scroll-locked');
          scrollToOutlineNode(blockId);
          setTimeout(() => card.classList.remove('scroll-locked'), 800);
        }
      }
    },
    onOutlineExtract: (data) => {
      const blockId = data?.block_id;
      if (!blockId) return;
      const matrix = data.matrix || {};
      const block = {
        block_id: blockId,
        title: data.title || matrix.title || blockId,
        requirement: matrix.requirement || '',
        key_points: matrix.key_points || '',
        veto_items: matrix.veto_items || '',
        bonus_items: matrix.bonus_items || '',
        score_items: matrix.score_items || '',
        evidence_required: matrix.evidence_required || '',
        // 第 8 个字段以前没进这个对象 → 卡片永远显示默认值，提炼结果里的
        // mandatory 会在页面上消失（而它决定正文字数档）
        constraint_level: matrix.constraint_level || 'recommended',
        indicators: matrix.indicators || '',
        // 结果状态：失败原因必须带进 DOM（否则界面上只剩一张空卡片，用户不知道发生了什么）
        error: matrix.error || '',
        error_kind: matrix.error_kind || '',
      };
      const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
      if (card) {
        // 已有占位 / extracting 卡片：原地更新到 done。
        // wasDone 判定与 loadExistingBlocks 一致：以 status='outline_done' 为权威，
        // 兜底允许 key_points/requirement 非空，避免重复 ++doneBlocks。
        const prev = blocksById.get(blockId) || {};
        const wasDone = isDoneBlock(prev);
        const next = { ...prev, ...block, status: 'outline_done' };
        blocksById.set(blockId, next);
        updateBlockCard(blockId, block);
        if (isDoneBlock(next)) {
          markTocDone(blockId);
          if (!wasDone) {
            doneBlocks++;
            if (!totalBlocks) totalBlocks = doneBlocks;
          }
        }
      } else if (!blocksById.has(blockId)) {
        // 没有占位卡片（极端情况：解析跳过 / parse_progress 没收到完成帧）：临时补建
        blocksByOrder.push(block);
        blocksById.set(blockId, block);
        blocksEmpty.style.display = 'none';
        renderBlockCard(block, 'done');
        const level = inferTocLevel({ title: block.title });
        const tocBtn = document.createElement('button');
        tocBtn.className = 'toc-item' + (level === 1 ? '' : level >= 3 ? ' content level-' + level : ' content');
        tocBtn.id = `toc-${blockId}`;
        tocBtn.dataset.blockId = blockId;
        if (level === 1) {
          tocBtn.innerHTML = `<span class="toc-chevron">›</span><span class="toc-label" title="${escapeHtml(block.title)}">${escapeHtml(block.title)}</span>`;
        } else {
          tocBtn.innerHTML = `<span class="toc-bullet"></span><span class="toc-label" title="${escapeHtml(block.title)}">${escapeHtml(block.title)}</span><span class="toc-dot"></span>`;
        }
        tocBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          document.getElementById('sidebar-toc').style.display = 'none';
          expandToBlock(blockId);
          scrollToOutlineNode(blockId);
        });
        sidebarToc.appendChild(tocBtn);
        markTocDone(blockId);
        scheduleOutlineTree();
        doneBlocks++;
        if (!totalBlocks) totalBlocks = doneBlocks;
      } else {
        // 同一 block 二次更新但没有 DOM（不应发生）→ 兜底
        Object.assign(blocksById.get(blockId), block);
      }
      updateSidebarProgress();
      const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0;
      const failed = failedBlockCount();
      setStatusBar('extracting',
        `张衡提炼 ${doneBlocks}/${totalBlocks} 章节${failed ? `（${failed} 节失败）` : ''}`,
        pct, totalBlocks, doneBlocks);
    },
    onOutlineDraftStart: (data) => {
      // 模型正在派生应答目录：目录还没有内容，先把张衡的进度文案切上去。
      // 同时清掉旧卡片：本次 run 的行还没落库，读路径此刻仍会退回显示上一次 run
      // 的内容（见 services/block_store.list_blocks 的 fallback_all），不清就会
      // 出现「旧卡片 + 新进度文案」的错配。
      blocksList.innerHTML = '';
      blocksById.clear();
      blocksByOrder.length = 0;
      sidebarToc.innerHTML = '';
      draftRevision = Number(data?.revision) || draftRevision;
      const ver = draftRevision ? `（第 ${draftRevision} 版）` : '';
      setStatusBar('extracting', `张衡梳理应答目录${ver}…`, 0, totalBlocks, doneBlocks);
      blocksEmpty.innerHTML = '<p>正在依据要求文件与提炼要求梳理应答目录…</p>';
      blocksEmpty.style.display = 'block';
    },
    onOutlineDraft: (data) => {
      // 目录是整体替换的：一次收全量，旧卡片全部推倒重建
      const toc = Array.isArray(data?.toc) ? data.toc : [];
      resetWorkflowUi();
      draftRevision = Number(data?.revision) || draftRevision;
      outlineError = String(data?.error || '');
      blocksEmpty.innerHTML = '<p>上传应标文件后点击「拆解评分点」开始</p>';
      blocksEmpty.style.display = toc.length ? 'none' : 'block';
      toc.forEach(sec => {
        const block = {
          block_id: sec.id,
          title: sec.title || sec.id,
          level: sec.level,
          status: 'pending',
        };
        blocksByOrder.push(block);
        blocksById.set(sec.id, block);
        renderBlockCard(block, 'pending');
      });
      totalBlocks = toc.length;
      doneBlocks = 0;
      renderOutlineTree();
      // 新一版目录先全展开：用户要核对张衡是不是按他的要求改了结构
      applyOutlineExpansion(true);
      buildToc(toc);
      updateSidebarProgress();
      const ver = draftRevision ? `第 ${draftRevision} 版` : '';
      // 逐节提炼已移到闸门1 之后，此刻只有目录到手、什么都没在跑，不能写「正在提炼章节要求」
      setStatusBar('extracting', `目录已更新${ver ? `（${ver}）` : ''}…`,
        0, totalBlocks, 0);
      // 两阶段 UI 的**主入口**：目录一到手就展示拆分清单。
      // 不能只挂在 gate_open 上 —— 闸门早就开着时重拆不会再发 gate_open 帧，
      // 于是"拆分完直接展示"永远不触发（实测踩到）。
      showConfirmedBanner();
      maybeAutoOpenSplitReport();
    },
    onMatchStart: (data) => {
      // 单 block 开始匹配：进度条切动画 + 自动滚动聚焦
      const blockId = data?.block_id;
      if (!blockId) return;
      const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
      if (!card) return;
      const prog = card.querySelector('.block-card-progress');
      if (prog) prog.classList.add('active');
      if (!document.querySelector('.block-card.scroll-locked')) {
        card.classList.add('scroll-locked');
        scrollToOutlineNode(blockId);
        setTimeout(() => card.classList.remove('scroll-locked'), 800);
      }
    },
    onMatchProgress: (data) => {
      const blockId = data?.block_id;
      if (!blockId) return;
      // 关掉进度条动画
      const card = document.querySelector(`.block-card[data-block-id="${blockId}"]`);
      const prog = card?.querySelector('.block-card-progress');
      if (prog) prog.classList.remove('active');
      if (!matchedIds.has(blockId)) {
        matchedIds.add(blockId);
        matchedCount = matchedIds.size;
      }
      const tot = totalBlocks || matchedCount;
      const pct = tot ? Math.round(matchedCount / tot * 100) : 0;
      setStatusBar('extracting', `沈括匹配素材 ${matchedCount}/${tot} 章节`, pct, tot, matchedCount);
    },
    onGateOpen: (data) => {
      streamRecoverCount = 0; // 收到真实帧说明连接是好的，恢复额度重置
      const gate = data?.gate;
      currentGate = gate;
      const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
      if (gate === 'review_outline') {
        // 快照是后端权威：刷新后靠它回填提炼要求、显示版本号与降级提示
        const snap = data?.snapshot || {};
        if (Number(snap.outline_revision)) draftRevision = Number(snap.outline_revision);
        if (typeof snap.outline_error === 'string' && snap.outline_error) {
          outlineError = snap.outline_error;
        }
        restoreOutlineInstruction({ text: typeof snap.outline_instruction === 'string' ? snap.outline_instruction : '' });
        const ver = draftRevision ? `（第 ${draftRevision} 版）` : '';
        // 两处降级各自独立：parse 的「项目概述」与 outline_draft 的「目录派生」
        // 可能同时失败，所以并列提示，不二选一。
        const degrades = [];
        // 派生失败现在会直接把运行标 failed（见 outline_draft_node），走到这里说明
        // 派生是成功的，outline_error 里只有 normalize 的告警 —— 措辞别说成"降级"。
        if (outlineError) degrades.push(`目录派生提示：${outlineError}`);
        // 有要求文件没参与派生（解析失败 / 解析出 0 章节）：必须说清，否则用户
        // 以为上传的每份文件都被用上了（单份坏文件现在只跳过，不再整体失败）。
        const missedFiles = Array.isArray(snap.parse_failed_files)
          ? snap.parse_failed_files.filter(Boolean) : [];
        if (missedFiles.length) degrades.push(`未参与派生的要求文件：${missedFiles.join('、')}`);
        if (typeof snap.doc_summary_error === 'string' && snap.doc_summary_error) {
          degrades.push(`项目概述缺失：${snap.doc_summary_error}`);
        }
        setStatusBar('gate_outline',
          degrades.length
            ? `大纲提炼完成${ver}；${degrades.join('；')}`
            : `大纲提炼完成${ver}，请检查并编辑后开始提炼要点`,
          pct, totalBlocks, doneBlocks);
      } else if (gate === 'review_materials') {
        setStatusBar('gate_materials', '素材匹配完成，请确认后进入撰写', pct, totalBlocks, doneBlocks);
      } else if (gate === 'review_report') {
        // 该闸门不属于本页，但兜底标记完成
        setStatusBar('done', '工作流已进入审核阶段', 100, totalBlocks, doneBlocks);
      }
    },
    onError: async (data) => {
      const msg = data?.message || '工作流出错';
      const blockId = data?.block_id;
      if (blockId) {
        markBlockError(blockId, msg);
      }
      console.error('[workflow] error', data);

      // 图执行中途抛异常时，runner 会把运行标成 stage=failed（终态）→ 走 failed 态。
      // 但「重新拆分目录」这类**局部失败**只发错误帧、DB 里的 stage 仍停在原来的闸门
      //（上一版目录还有效）—— 这种情况按闸门恢复并附上失败原因，别把用户按在一个
      // 其实还成立的「失败」态上（那会让刷新前后的页面表现不一致）。
      const tid = currentThreadId || getThread(projectId);
      let stage = '';
      try { stage = (await api.workflow.state(tid))?.stage || ''; } catch { /* 读不到按终态处理 */ }
      if (stage && stage !== 'failed' && GATE_STAGES.has(stage)) {
        await restoreSettledThread(tid, stage, { note: `上次操作失败：${msg}` });
        return;
      }
      const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0;
      // 硬失败要落到 failed 态（带重试按钮），不能停在 extracting —— 那会永远转圈等帧
      setStatusBar('failed', msg, pct, totalBlocks, doneBlocks);
    },
    onCheckpoint: (data) => {
      console.debug('[workflow] checkpoint', data);
    },
    onAborted: (data) => {
      console.warn('[workflow] aborted', data);
      closeStream();
      // **不清 thread**：与 cancelExtraction 同一道理 —— 已提炼的章节是这次 run 的成果，
      // 「继续提炼」必须能 resume 回同一条 run（否则会重新解析 + 重新派生目录 + 全量重算）。
      currentGate = null;
      setExtractDisabled(false);
      // 中止后卡片仍停在最后一帧（在被中止的那一节上显示「提炼中…」）。以库为准重绘，
      // 库里还保留着中止前已落库的进度，正好就是刷新之后会看到的样子。
      resyncFromDb().then(() => openAllExtractedNodes());
    },
    onDone: (data) => {
      console.debug('[workflow] done', data);
      // 运行结束但没进闸门（例如被后端中止 / 图跑到尽头）时同样要回读库，
      // 否则页面停在运行期的帧上，只有手动刷新才会对齐。
      // 收尾后把已提炼的章节保持展开：用户下一步就是核对这 8 个字段。
      resyncFromDb().then(async () => {
        const st = data?.stage || '';
        // 第①步「只拆评分点」结束时：不生成目录、图直接收尾 —— 这时要主动把
        // 「拆解结果」展开给用户（否则页面停在没有任何提示的状态）。
        let pending = false;
        try {
          const tid = currentThreadId || getThread(projectId);
          const snap = tid ? await api.workflow.state(tid).catch(() => null) : null;
          pending = !!(snap?.spec || {}).outline_pending || st === 'points_review';
          if (pending) draftRevision = Number((snap?.spec || {}).outline_revision || draftRevision) || draftRevision;
        } catch (e) { /* 读不到就按原逻辑 */ }
        if (pending) {
          splitPanelAutoOpenedFor = 0;
          refreshOutlineCoverage(null);
          await openSplitReport();
        } else {
          openAllExtractedNodes();
        }
      });
    },
  });
}

// 停在闸门 / 终态的 thread 不会自己再往下走：recover 只是把 _Run 重建回来（停在闸门时
// ainvoke(None) 是空跑），后端不会再发任何帧。这时照常 attachStream 等帧，页面就永远停在
// 「准备提炼…」。必须直接按 state 恢复视图。
const SETTLED_STAGES = new Set(['points_review', 'outline_review', 'materials_review', 'report_review', 'done', 'paused', 'aborted', 'failed']);   // failed：终态，没有帧可等，直接按 state 恢复视图

// 需要「按 state 恢复视图」的停留态（闸门）。failed/done 除外：它们是终态。
// points_review：第①步（只拆评分点）结束的停留态 —— 没有目录可确认，
// 要把「拆解结果」打开让用户选类别，所以和闸门一样按 state 恢复视图。
const GATE_STAGES = new Set(['points_review', 'outline_review', 'materials_review']);

async function restoreSettledThread(threadId, stage, { note = '' } = {}) {
  if (stage === 'aborted') {
    clearThread(projectId);
    currentThreadId = null;
    currentGate = null;
    resetWorkflowUi();
    setStatusBar('idle', '该任务已作废，请重新拆解评分点', 0, 0, 0);
    return;
  }
  if (['report_review', 'done'].includes(stage)) {
    // 与 startMatching 的既有约定一致：这几个阶段不在本页完成
    location.href = `/review?projectId=${projectId}&threadId=${encodeURIComponent(threadId)}`;
    return;
  }
  setStage(stage); // 同时按 stage 切 outline / matching 视图并重绘进度条
  await loadExistingBlocks({ silent: true });
  const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
  if (stage === 'failed') {
    currentGate = null;
    let reason = '';
    try { reason = lastRunErrorMessage(await api.workflow.state(threadId)); } catch { /* 读不到就不显示原因 */ }
    setStatusBar('failed',
      `本次运行失败${reason ? `：${reason}` : ''}。可点「重新拆分目录」重试（要求文件已解析好，无需重传）。`,
      pct, totalBlocks, doneBlocks);
    return;
  }
  if (stage === 'paused') {
    // 暂停有两种，靠"还有没有章节没提炼"区分（与闸门态同一套判据）：
    //   还有剩余 → 提炼中途取消：留在本页给「继续提炼」（resume-extract 只补剩余部分）
    //   全都提炼过 → 生成阶段的暂停，不在本页处理，去质量检查
    if (totalBlocks > 0 && doneBlocks < totalBlocks) {
      currentGate = null;
      setPartialOutlineBar({ note });
      openAllExtractedNodes();
      return;
    }
    location.href = `/review?projectId=${projectId}&threadId=${encodeURIComponent(threadId)}`;
    return;
  }
  if (stage === 'points_review') {
    // 第①步（只拆评分点）的停留态：评分点已拆出、目录还没生成。
    // 直接展开「拆解结果」，让用户选标包 + 类别后生成目录。
    currentGate = null;
    setStatusBar('gate_outline',
      `评分点已拆出，请在「拆解结果」里选择要生成的目录类别（技术 / 商务 / 报价）${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
    await refreshOutlineCoverage(threadId);
    splitPanelAutoOpenedFor = 0;
    await openSplitReport();
    return;
  }
  if (stage === 'outline_review') {
    // 与 boot 同一套判定：checkpoint 停在 outline_review，但章节已全部提炼 = 第二步
    // 其实跑完了（extract 收尾时图直接 END，没有节点再改 stage），别再引导用户
    // 「确认目录，开始提炼要点」—— 那会再起一次提炼（后端有互斥兜底，但体验是错的）。
    if (totalBlocks > 0 && doneBlocks >= totalBlocks) {
      currentGate = null;
      setStatusBar('done', `提炼完成${note ? `；${note}` : ''}`, 100, totalBlocks, doneBlocks);
      return;
    }
    // 部分章节已提炼 = 提炼被打断（正在跑的情况由调用方先判掉，见 boot / 兜底轮询）。
    // 此时仍停在闸门 1 的确认按钮上 —— 它是**唯一**能继续提炼的入口：extract 会跳过
    // 已完成的章节接着跑（skip_ids）。只是把文案说清楚，别让人以为要从头来。
    if (doneBlocks > 0) {
      // 提炼到一半（被打断）：给「继续提炼」那套按钮，别再引导"确认目录开始提炼"
      currentGate = null;
      setPartialOutlineBar({ note });
      openAllExtractedNodes();
      return;
    }
    currentGate = 'review_outline';
    // 恢复视图时也要说清「这份目录是不是派生的」：派生失败会**回落到规范书原始目录**，
    // 不提示的话用户会以为模型真的按他的要求拆了章节（实测：outline_error=Connection error）。
    let degrade = '';
    try {
      const snap = await api.workflow.state(threadId);
      const missed = (snap?.spec || {}).parse_failed_files;
      if (Array.isArray(missed) && missed.filter(Boolean).length) {
        degrade = `；未参与派生的要求文件：${missed.filter(Boolean).join('、')}`;
      }
      const err = (snap?.spec || {}).outline_error;
      // 注意：派生**失败**现在已经直接把运行标成 failed（不再回落原始目录），
      // 所以这里的 outline_error 只承载 normalize 的告警，措辞保持中性。
      if (err) degrade += `；目录派生提示：${err}`;
    } catch { /* 读不到就不提示，不阻断恢复 */ }
    setStatusBar('gate_outline',
      `已恢复上次进度：大纲已就绪，请检查确认后开始提炼要点${degrade}${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
  } else {
    currentGate = 'review_materials';
    setStatusBar('gate_materials',
      `已恢复上次进度：素材匹配完成，请确认后进入撰写${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
  }
}

async function startExtraction(forceRestart = false) {
  // forceRestart：用户点「重新提炼」，先 abort 旧 thread 再起新
  resetWorkflowUi();
  if (forceRestart) {
    // 重新提炼回到「解析大纲」视图：清掉视图覆盖；不直接重置 currentStage 到 'parsing'，
    // 否则进度条 active 会回退到「解析大纲」蓝色（但 graph 实际仍处于更后阶段）。
    // 真实 stage 由 graph 跑起来时 SSE / state 返回。
    pageViewOverride = 'outline';
    // 同步 URL 上 view 参数，避免页面跳转 / 刷新后又被旧的 view=matching 拉回去
    try {
      const url = new URL(location.href);
      url.searchParams.set('view', 'outline');
      history.replaceState({}, '', url);
    } catch {}
    applyPageView();
  }
  setStatusBar('extracting', '准备提炼…', 0, 0, 0);
  setExtractDisabled(true);

  try {
    if (forceRestart && currentThreadId) {
      try { await api.workflow.abort(currentThreadId); } catch {}
      clearThread(projectId);
      currentThreadId = null;
    }
    if (forceRestart) {
      // 重新提炼：清空已落库的 blocks，避免 extract 节点把旧 outline_done 当已完成跳过
      try { await api.blocks.clear(projectId); } catch (e) { console.warn('clear blocks failed', e); }
    }

    let threadId = forceRestart ? null : getThread(projectId);
    if (threadId) {
      try {
        await api.workflow.recover(threadId);
      } catch (e) {
        console.warn('[workflow] recover 失败，重启', e);
        threadId = null;
        clearThread(projectId);
      }
    }
    if (!threadId) {
      const resp = await api.workflow.start(projectId, {
        model: selectedModel,
        outline_instruction: readOutlineInstruction(),
        // 第①步「拆解评分点」：只把评分点拆出来（落库 + 出矩阵），**不生成目录**。
        // 目录留到第②步由用户在「拆解结果」里选类别后再生成（用户明确要求的两步）。
        points_only: true,
      });
      threadId = resp?.thread_id;
      if (!threadId) {
        setStatusBar('idle', '', 0, 0, 0);
        setExtractDisabled(false);
        console.error('[workflow] start 未返回 thread_id', resp);
        return;
      }
      setThread(projectId, threadId);
    }
    currentThreadId = threadId;
    // 新运行开始：立刻按新 thread 重算这两个面板。否则覆盖率/未派生提示会**残留
    // 上一次运行的结论** —— 页面看起来自相矛盾（实测：树是 367 节原始目录，
    // 面板却显示上一轮的「评分点 8/8」）。
    refreshOutlineCoverage(null);
    warnIfOutlineNotDerived(null);
    // 已有 thread 可能停在上次运行的闸门 / 终态（recover 不改状态）—— 这时没有帧可等
    const settled = (await api.workflow.state(threadId).catch(() => null))?.stage;
    if (SETTLED_STAGES.has(settled)) {
      await restoreSettledThread(threadId, settled);
      return;
    }
    attachStream(threadId);
  } catch (e) {
    // 以前这里只 console.error + 清状态栏 ——「重新提炼」失败时页面上**零提示**，
    // 用户看到的就是"点了没反应"（实测：abort 之后没有任何后续请求，blocks 也没清，
    // 页面上却什么都不说）。失败必须说清原因与下一步。
    console.error('[workflow] startExtraction 失败', e);
    setExtractDisabled(false);
    const msg = (e && (e.message || e.detail)) || String(e || '未知错误');
    const canFallback = !!(currentThreadId || getThread(projectId));
    statusActions.innerHTML = canFallback
      ? `<button class="btn-status-primary" id="btn-force-restart">重新提炼（强制新建运行）</button>`
      : '';
    const btnForce = document.getElementById('btn-force-restart');
    if (btnForce) btnForce.addEventListener('click', () => forceRestartRun());
    setStatusBar('idle',
      `重新提炼失败：${msg}。` +
      (canFallback ? '可点「重新提炼（强制新建运行）」重试。' : '请刷新页面后重试。'),
      0, 0, 0);
  }
}

/**
 * 强制新建运行：不依赖 sessionStorage 里的旧 thread，直接把当前 thread 作废后走 start。
 *
 * 为什么单独留一条：正常「重新提炼」会先 abort 旧 run 再新建；一旦 abort 之后
 * 任何一步失败（网络、5xx、页面里的异常），用户就被卡在"旧目录 + 无提示"，
 * 实测连续点 25 次「重新拆分目录」也没用（它不重跑解析）。这条路径把每一步的
 * 失败都报出来，并且**先建新 run 再谈其它**。
 */
async function forceRestartRun() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  setExtractDisabled(true);
  setStatusBar('extracting', '正在新建运行（重新解析全部要求文件）…', 0, 0, 0);
  try {
    if (tid) { try { await api.workflow.abort(tid); } catch {} }
    clearThread(projectId);
    currentThreadId = null;
    try { await api.blocks.clear(projectId); } catch (e) { console.warn('clear blocks failed', e); }
    const resp = await api.workflow.start(projectId, {
      model: selectedModel,
      outline_instruction: readOutlineInstruction(),
    });
    const newTid = resp && resp.thread_id;
    if (!newTid) throw new Error(`start 未返回 thread_id：${JSON.stringify(resp).slice(0, 200)}`);
    setThread(projectId, newTid);
    currentThreadId = newTid;
    attachStream(newTid);
    setStatusBar('extracting', '已新建运行，正在解析要求文件…', 0, 0, 0);
  } catch (e) {
    console.error('[workflow] forceRestartRun 失败', e);
    setExtractDisabled(false);
    setStatusBar('idle', `新建运行失败：${(e && (e.message || e.detail)) || e}`, 0, 0, 0);
  }
}

async function startMatching() {
  // 「素材匹配」按钮（素材库视图）：根据**真实进度**决定推进路径，绝不重新跑 parse/extract。
  //
  // ⚠️ 不能拿 checkpoint 的 stage 当"闸门 1 过没过"的判据 —— 这是本函数踩过的坑：
  // extract 是这条分支的最后一个节点，跑完图直接 END，**checkpoint 只会停在闸门 1
  // 留下的 'outline_review'**；于是"提炼已完成"与"闸门 1 还没放行"在 /state 里长得
  // 一模一样（真实数据：项目 18 thread f0e6f7ae → workflow_runs='done'、33/33 节
  // outline_done、checkpoint stage='outline_review'）。以前这里信了 checkpoint，
  // 点「素材匹配」被判成"还没提炼"，`setStage('outline_review')` + `switchView('outline')`
  // 把人拽回要求与大纲并弹「请先在『要求与大纲』确认目录并提炼要点」—— 而那边其实
  // 无事可做，用户被卡死（用户报的正是这个）。
  // 真实判据 = **已提炼章节数**（doneBlocks，页面自己按 blocks 算，比 checkpoint 新）。
  // - 一节都没提炼 → 闸门 1 真的没过：回「要求与大纲」引导（语义正确）。
  // - 已有提炼结果 → 就是"开始匹配"：rerun-match 把指针拨回 NODE_EXTRACT 之后跑 match。
  // - 长阶段还在跑（提炼/匹配）→ 让它跑完，不发请求也不动视图。
  const tid0 = currentThreadId || params.get('threadId') || getThread(projectId);
  setExtractDisabled(true);
  matchedIds.clear();
  matchedCount = 0;
  setStatusBar('extracting', '准备进入素材匹配…', 0, totalBlocks, doneBlocks);
  try {
    let tid = tid0;
    let snap = null;
    if (tid) {
      try { snap = await api.workflow.state(tid); } catch {}
    }
    // 提炼 / 匹配还在跑：后端 rerun_match 会 409，而且此刻切视图只会让人更困惑
    const running = tid ? await longPhaseRunning(snap) : null;
    if (running) {
      setExtractDisabled(false);
      setStatusBar('extracting',
        running === 'extracting' ? '正在提炼要点，等这一轮跑完再开始匹配。' : '正在匹配素材，请稍候…',
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
      return;
    }

    const total = Number(totalBlocks) || 0;
    const extracted = Number(doneBlocks) || 0;
    const stage = snap?.stage || currentStage;
    // 该走哪条路由纯函数判定（matchEntryDecision，有 node --test 覆盖）：判据是
    // **已提炼章节数**而不是 checkpoint 的 stage —— 理由见函数头与模型层注释。
    const decision = matchEntryDecision({ stage, totalBlocks: total, doneBlocks: extracted, running });

    if (!tid) {
      const resp = await api.workflow.start(projectId, {
        model: selectedModel,
        outline_instruction: readOutlineInstruction(),
      });
      tid = resp?.thread_id;
      if (!tid) {
        setStatusBar('idle', '', 0, 0, 0);
        setExtractDisabled(false);
        return;
      }
      setThread(projectId, tid);
      currentThreadId = tid;
      attachStream(tid);
      // 全新 run：一节都没提炼，下面必然走"先确认目录再提炼"的引导
      setExtractDisabled(false);
      switchView('outline');
      setStatusBar('extracting', '已新建一次运行：请先在「要求与大纲」确认目录并提炼要点',
                   0, totalBlocks, doneBlocks);
      return;
    }
    // ⚠️ 这里**不**补 attachStream。线程若已停在闸门，页面本来就没有流；此时新订阅
    // 会命中 emitter 的"有界回放"，把上一轮匹配收尾时的 `gate=review_materials` 帧
    // 重放一遍，于是刚写下的引导文案（"请先在要求与大纲…"）被"素材匹配完成…"盖掉。
    // 真正要干活的分支（下面 rerun-match / 新起 run）各自 attach，那里才是必要的。

    if (decision === MATCH_ENTRY.GATE_OUTLINE) {
      // 闸门 1 还没放行。匹配依赖提炼产出的矩阵，不能在这儿代放闸门 —— 那会让
      // 「素材匹配」按钮顺手烧掉 N 次逐节提炼调用，用户还看不到提炼结果。送回
      // 「要求与大纲」，走「确认目录，开始提炼要点」。
      setStage(stage);
      setExtractDisabled(false);
      switchView('outline');
      setStatusBar('gate_outline', '请先在「要求与大纲」确认目录并提炼要点', 0, totalBlocks, doneBlocks);
      return;
    }
    if (decision === MATCH_ENTRY.START) {
      if (stage === 'outline_review') {
        // 提炼已完成、checkpoint 仍写着 outline_review：只把进度条挪到匹配步，
        // **不调 setStage/switchView**（它们会按 stale 闸门态把视图切回要求与大纲）。
        currentStage = 'matching';
        renderStageBar();
      } else {
        // 用 setStage 而非直接赋值：单调保护避免 backend stage 比当前内存里更早时拉回
        setStage(stage);
      }
      // 把指针拨回 NODE_MATCH 之前重新匹配。emitter 会重建，旧 SSE 连接收不到新事件
      // → 重连一次（增量匹配：只归属新上传的素材）
      closeStream();
      await api.workflow.rerunMatch(tid);
      attachStream(tid);
      switchView('matching');
      setStatusBar('extracting', '沈括匹配素材中…', 0, totalBlocks, doneBlocks);
    } else if (decision === MATCH_ENTRY.REVIEW) {
      // 补充的素材由局部修订入口实时读取，返回质量检查选择受影响章节。
      location.href = `/review?projectId=${projectId}&threadId=${encodeURIComponent(tid)}`;
    } else {
      // 例如正在 parse / 派生目录（checkpoint 的 stage 还没落到闸门）：说清原因，
      // 别把状态栏清空 —— 那看起来就是"点了没反应"。
      console.warn('[matching] 当前 stage 不可触发匹配：', stage);
      setStatusBar('idle', '现在还不能开始匹配：请等这一轮跑完（当前阶段：' + (stage || '未知') + '）',
                   0, totalBlocks, doneBlocks);
      setExtractDisabled(false);
    }
  } catch (e) {
    console.error('[matching] startMatching 失败', e);
    setExtractDisabled(false);
    // 后端 409（同一 thread 已有任务在跑）等失败要说出来，否则按钮像点了没反应
    const msg = e?.message || '';
    if (msg) setStatusBar('idle', `无法开始匹配：${msg}`, 0, totalBlocks, doneBlocks);
  }
}

// （删掉了原来的 waitForStage 轮询：它只在「素材匹配时新起 run」那条分支里用来等
//   闸门 1，而那条分支现在直接切回「要求与大纲」，由 SSE 帧推进即可。）

async function cancelExtraction() {
  if (currentThreadId) {
    try { await api.workflow.abort(currentThreadId); } catch (e) { console.warn('abort failed', e); }
  }
  closeStream();
  // **不清 thread**：已提炼的章节是这次 run 的成果（后端逐节 upsert 落库），
  // 取消只是"停一下"。清掉 thread 引用会带来两个真问题（实测）：
  //   ① 「继续提炼」找不到原 run → startExtraction 起一条**全新** run → 重新解析 +
  //      重新派生目录（目录可能与用户刚审过的那版不同）→ 用户看到「目录结构变了」；
  //   ② 那等于把已提炼的部分全部重算一遍 —— 每章一次模型调用，白烧钱。
  // 保留 thread 后，「继续提炼」= resume 同一个 run，extract 会跳过 status='outline_done'
  // 的章节（skip_ids），只补没做的。
  currentGate = null;
  setExtractDisabled(false);
  // 用库里的计数，不用内存里的 doneBlocks/totalBlocks：中止时最后一节可能还没落库，
  // 内存计数会比库里多，状态栏数字和刷新后看到的不一致。
  await resyncFromDb();
  // 已提炼的章节保持展开：用户刚看到的内容不该因为"取消"就从屏幕上消失
  openAllExtractedNodes();
}

// 「继续提炼」：复用当前 run 从检查点接着跑（不重新派生目录、不重算已完成章节）。
// 用 resume-extract 而不是普通 resume：取消时图已经收尾（next 为空），普通 ainvoke
// 是空跑；该端点借闸门 1 的出边把指针拨回 extract 之前，extract 自己跳过已完成的章节。
async function continueExtraction() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  if (!tid) {
    // 没有可复用的 run（比如换了浏览器/清过会话）：只能从头，先说清楚代价
    const go = window.confirm(
      '找不到这次运行的记录，只能重新跑一遍：\n' +
      '重新解析要求文件 → 重新派生目录 → 重新逐节提炼（每章一次模型调用）。\n\n' +
      '确定继续？');
    if (!go) return;
    return startExtraction(true);
  }
  setExtractDisabled(true);
  setStatusBar('extracting', '继续提炼剩余章节…', 
               totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
  try {
    await api.workflow.resumeExtract(tid);
    currentThreadId = tid;
    setThread(projectId, tid);
    attachStream(tid);
  } catch (e) {
    console.error('[workflow] continueExtraction 失败', e);
    setExtractDisabled(false);
    setStatusBar('paused', `继续提炼失败：${e?.message || e}`, 
                 totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
  }
}

// 「提炼到一半」的顶栏（文案 + 按钮组）。按钮组由 setStatusBar 的 paused 分支渲染：
// 重新提炼 / 重新拆分目录 / 继续提炼（剩余 N 节）。
// 单独抽出来是因为 boot、断流恢复、取消之后三处都必须落到同一个形态 —— 以前它们都
// 走闸门 1 那栏，于是半个提炼进度下还在显示「确认目录，开始提炼要点」，语义是错的。
// 当前项目里提炼失败的章节数（blocksById 是页面的权威副本）
function failedBlockCount() {
  let n = 0;
  blocksById.forEach(b => { if ((b?.error_kind || '') === 'failed') n++; });
  return n;
}

// 主按钮有两个入口：左栏底部的「拆解评分点 / 素材匹配」和素材库顶部总览条的
// 「分析素材归属」。禁用态必须一起变 —— 只灰一个的话另一个看起来能点却没反应。
function setExtractDisabled(v) {
  btnExtract.disabled = !!v;
  const alt = document.getElementById('ms-match');
  if (alt) alt.disabled = !!v;
}

function setPartialOutlineBar({ note = '' } = {}) {
  const total = Number(totalBlocks) || 0;
  const done = Number(doneBlocks) || 0;
  const remain = Math.max(total - done, 0);
  const pct = total ? Math.round(done / total * 100) : 0;
  // 文案尽量短：右侧已经有三颗按钮，长句子会把它们挤出去（实测被挤出右边界）。
  // "只补剩余、不重复调用"这句解释挂在按钮 title 上。
  const failed = failedBlockCount();
  const bits = [`已提炼 ${done}/${total} 章节${remain ? `，还剩 ${remain} 节` : ''}`];
  if (failed > 0) bits.push(`${failed} 节提炼失败，可点「继续提炼」重试`);
  if (note) bits.push(note);
  return setStatusBar('paused', bits.join('；'), pct, total, done);
}

// 「重新提炼」= 从头再来：清空已提炼内容 + 新起一条 run（重新解析 → 重新派生目录 → 逐节提炼）。
// 这是**最贵**的路径，而且新派生的目录可能与用户刚审过的那版不同，所以必须先说清代价，
// 并把"只想接着提炼剩下的章节"的用户引导到「继续提炼」。
async function confirmReextract() {
  const n = Number(doneBlocks) || 0;
  const total = Number(totalBlocks) || 0;
  const go = window.confirm(
    '「重新提炼」会从头跑一遍：\n' +
    `① 清空已提炼的 ${n}${total ? `/${total}` : ''} 个章节内容；\n` +
    '② 重新解析要求文件；\n' +
    '③ 重新派生应答目录（新目录可能与当前这版不同）；\n' +
    `④ 重新逐节调用模型${total ? `（约 ${total} 次调用）` : ''}。\n\n` +
    (n > 0 ? '只想接着提炼剩下的章节？请点「继续提炼」（不重复调用已完成的章节）。\n\n' : '') +
    '确定重新提炼？');
  if (go) await startExtraction(true);
}

async function confirmOutlineGate() {
  // thread 从三处解析：内存 → URL → sessionStorage。以前只认 currentThreadId，
  // 为空时**静默 return** —— 用户看到的就是"点了没反应"，且没有任何线索。
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  const btn = document.getElementById('btn-confirm-outline');
  if (!tid) {
    console.warn('[workflow] confirmOutlineGate：找不到 thread，无法放行闸门 1');
    setStatusBar('gate_outline',
      '找不到这次运行的记录（可能换了浏览器或清过会话）。请点「重新拆分目录」新建一次运行。',
      0, totalBlocks, doneBlocks);
    return;
  }
  if (btn) btn.disabled = true;
  try {
    // 立即给反馈：下面的结构保存队列可能因为一次卡住的请求迟迟不回，而以前状态栏是
    // 在 await 之后才切的 —— 于是"点了没反应"。
    setStatusBar('extracting', '张衡提炼章节要求中…',
                 totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    // 提炼要求只影响派生：改过却没重新拆分时，目录与输入框不一致。这里问一句，
    // 避免用户在"以为按新要求拆了"的前提下接着提炼要点。
    const ta = instructionInput();
    if (ta && instructionDirty(ta.value, appliedInstruction)) {
      const go = window.confirm(
        '提炼要求已修改，但当前目录仍是按上一版要求生成的。\n' +
        '继续确认（按当前目录提炼要点）？\n\n' +
        '要按新要求重拆，请先取消，再点「重新拆分目录」。');
      if (!go) {
        if (btn) btn.disabled = false;
        // 撤回刚才那句"提炼中"，回到闸门（否则按钮禁用着、状态栏还写着提炼中）
        currentGate = 'review_outline';
        setStatusBar('gate_outline', '已取消：目录没有变化，核完后可再确认。',
                     totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
        return;
      }
    }
    // 闸门 1 只放行「目录结构」：此刻 8 字段还一个都没提炼（卡片里根本没有字段网格，
    // 见 renderBlockCard 的 pending 分支）。历史上这里会把 collectOutlineEdits() 的
    // N 行**全空**矩阵塞给 resume，而 extract 是用新结果整体替换 spec.outline_matrix
    // 的，那 N 行谁都没读过 —— 纯 checkpoint 噪声，去掉。
    // 结构编辑不依赖这里：增删改/排序每次都是即刻 POST /outline-structure 落库。
    freezeOutlineEdit();
    // 结构编辑是**逐次立即保存**的（增删改/排序各有一次 POST）。若用户刚点完上移/
    // 删除就马上点「确认目录…」，resume 可能先到后端 —— extract 读的是 spec.toc，
    // 于是被提炼的目录与页面看到的目录不一致（少一节或多一节）。这里等保存队列排空，
    // 但**限时 4s**：一次卡住的保存请求不该让确认变成"点了没反应"。
    let structureSaved = true;
    try {
      structureSaved = await Promise.race([
        structureSaveChain.then(() => true),
        new Promise(r => window.setTimeout(() => r(false), 4000)),
      ]);
    } catch { /* 保存失败：面板已报错，且 persistStructure 会把 DOM 回滚到库里的版本 */ }
    if (!structureSaved) {
      // 保存还没落地就放行，extract 读到的 spec.toc 会是旧版 —— 页面显示的目录与
      // 实际被提炼的目录发散。宁可让用户等两秒再点，也不要用旧目录烧一遍模型。
      if (btn) btn.disabled = false;
      currentGate = 'review_outline';
      setStatusBar('gate_outline',
        '目录还在保存中，请稍候两秒再点「确认目录」（避免按旧目录提炼）。',
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
      return;
    }
    // 用 resume-extract 而不是普通 resume：取消/暂停之后图已经收尾（next 为空），普通
    // ainvoke(None) 是空跑 —— 实测点「确认目录」后一次模型调用都没发、进度不动，用户
    // 看到的就是"无反应"。该端点借闸门 1 的出边把指针拨回 extract 之前；对"正常停在
    // 闸门 1 等放行"也等价（同一个 next），并顺带清 cancel_requested、修回 aborted 标记。
    await api.workflow.resumeExtract(tid);
    currentThreadId = tid;
    setThread(projectId, tid);
    // 后端可能已经换了一份 emitter（服务重启后 _runs 被清空，或上一段流收尾时
    // aclose 过）—— 旧 SSE 连接收不到任何提炼帧，卡片会停在骨架屏、等刷新读库
    // 才更新。attachStream 内部先 close 再建，直接续上当前 emitter。
    attachStream(tid);
  } catch (e) {
    console.error('[workflow] resume(review_outline) 失败', e);
    if (btn) btn.disabled = false;
    // 最常见的失败是后端 409「当前任务仍在运行，请等待完成后再操作」——比如另一个
    // 标签页/刚刚的刷新已经放行过闸门。必须把原因写出来，否则按钮像是点了没反应。
    const msg = e?.message || '';
    if (msg) {
      currentGate = 'review_outline';
      setStatusBar('gate_outline',
        `未能开始提炼：${msg}。若是刚刷新过页面，请等这一轮跑完再操作。`,
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    }
  }
}

// ── 提炼要求输入（左侧面板底部，闸门 1 可反复改） ──
const OUTLINE_INSTRUCTION_KEY = (pid) => `outlineInstruction:${pid}`;

function instructionInput() {
  return document.getElementById('outline-instruction');
}

function persistOutlineInstruction() {
  const ta = instructionInput();
  if (!ta) return;
  try { localStorage.setItem(OUTLINE_INSTRUCTION_KEY(projectId), ta.value.trim()); } catch {}
}

function readOutlineInstruction() {
  const ta = instructionInput();
  if (ta) return ta.value.trim();
  try { return (localStorage.getItem(OUTLINE_INSTRUCTION_KEY(projectId)) || '').trim(); } catch { return ''; }
}

// 当前目录**是按哪一版提炼要求拆的** —— 以后端闸门快照为准（它是派生的真实输入）。
let appliedInstruction = '';

// 回填：闸门快照（后端权威）负责「哪一版生效」，输入框负责「用户正在打什么」。
// 两者不一致时**不覆盖用户的字**，而是在输入框下方标出「已修改、需重新拆分」
// —— 之前只覆盖「空框」，且注释写的是"后端权威优先"，实现与注释相反：
// 用户改完要求直接点主按钮，新要求不生效，刷新后框里还显示那份没生效的文本。
function restoreOutlineInstruction({ text = '', force = false } = {}) {
  const ta = instructionInput();
  if (!ta) return;
  appliedInstruction = (text || '').trim();
  if (force || !ta.value.trim()) {
    let cached = '';
    try { cached = localStorage.getItem(OUTLINE_INSTRUCTION_KEY(projectId)) || ''; } catch {}
    ta.value = text || cached;
  }
  persistOutlineInstruction();
  updateInstructionDirtyHint();
}

// 「提炼要求改过但没应用到目录」的可见提示（含下一步动作）
function updateInstructionDirtyHint() {
  const hint = document.getElementById('outline-instruction-dirty');
  const ta = instructionInput();
  if (!hint || !ta) return;
  const dirty = instructionDirty(ta.value, appliedInstruction);
  hint.hidden = !dirty;
  if (!dirty) { hint.textContent = ''; return; }
  hint.textContent = appliedInstruction
    ? '提炼要求已修改：当前目录仍是按上一版要求生成的，点「重新拆分目录」后才会生效。'
    : '这段提炼要求还没用于生成目录：点「拆解评分点」（或「重新拆分目录」）后生效。';
}

instructionInput()?.addEventListener('input', () => {
  persistOutlineInstruction();
  updateInstructionDirtyHint();
});

// 闸门 1：换一份提炼要求，让张衡整版重出目录。
// 刻意不走 startExtraction(true) —— 那条路会 abort + 重跑 parse（几百页 PDF 再 OCR 一遍）。
// 目录重出只在**闸门处**成立。run 推进到更后阶段后目录不能再改（改了正文就对不上），
// 后端会 409 拒绝 —— 但只把这句话显示出来，用户点几次都一样，无从下手（实测点了 3 次）。
// 所以这里先按 stage 判定，界面上直接禁掉并说明原因与替代路径。
const REDRAFT_ALLOWED_STAGES = new Set(['outline_review', 'materials_review', 'paused', 'failed']);
function redraftAllowedByStage() {
  const st = currentStage || '';
  if (!st) return { ok: true };
  if (REDRAFT_ALLOWED_STAGES.has(st)) return { ok: true };
  return {
    ok: false,
    reason: '本次运行已推进到「撰写/评审」之后，目录不能再改（改了正文就对不上）。\n' +
            '若刚上传了新的要求文件（如技术规范书），请点「重新提炼」—— 会新建一次运行并重新解析全部要求文件。',
  };
}

async function redraftOutline() {
  // ① 先问后端：能不能重拆。原料变过（删旧文件/传新文件）或已过闸门时，
  //    「重新拆分目录」用的是 checkpoint 里的**旧解析结果**，点下去会返回 200
  //    但页面毫无变化 —— 实测用户连点 8 次。这里直接改走「重新提炼」。
  const tidForCheck = currentThreadId || params.get('threadId') || getThread(projectId);
  console.log('[redraft] 入口', { tidForCheck, totalBlocks, doneBlocks, extractionState });
  if (tidForCheck) {
    let check = null;
    try {
      check = await api.workflow.redraftCheck(tidForCheck);
      console.log('[redraft] 预检', check);
    } catch (e) {
      // 预检失败**不能静默** —— 以前这里 catch 成 null 就直落"重拆"，用户看不到任何原因
      console.warn('[redraft] 预检失败，按可重拆继续', e);
    }
    if (check && check.allowed === false) {
      const reason = (check.reasons || [])[0] || '';
      if (reason === 'requirement_files_changed') {
        // 不再用 window.confirm 做闸门：对话框没被接受就完全没反应（实测用户点了 23 次
        // 预检、一次重拆都没发出去）。直接执行用户本来要做的事 ——
        // 「重新提炼」= 新建运行 + 重新解析全部要求文件，并在状态栏说清为什么。
        setStatusBar('extracting',
          '要求文件已变更：目录是按上一次解析的结果生成的，「重新拆分目录」带不上新文件。' +
          '正在改为「重新提炼」（重新解析全部要求文件）…', 0, totalBlocks, doneBlocks);
        await startExtraction(true);
        return;
      }
      setStatusBar('gate_outline',
        `${check.message || '当前不能重新拆分目录'}（原因：${reason || '未知'}）`,
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0,
        totalBlocks, doneBlocks);
      return;
    }
  }

  // 已经提炼过一部分时先讲清代价：重出目录 = 换一版 block_id，已提炼的内容不再对应
  // （等于要重新提炼）。想改标题/顺序/层级应该用每行的「编辑」，想接着提炼用「继续提炼」。
  if (Number(doneBlocks) > 0) {
    const go = window.confirm(
      `当前已提炼 ${doneBlocks}/${totalBlocks} 个章节。\n` +
      '重新拆分目录会用当前的提炼要求重新派生一版目录，已提炼的内容不再对应新目录' +
      '（需要重新提炼）。\n\n' +
      '· 只想改章节标题 / 顺序 / 层级？用目录每行的「编辑」。\n' +
      '· 想接着提炼剩下的章节？点「继续提炼」。\n\n' +
      '确定重新拆分目录？');
    if (!go) return;
  }
  const text = readOutlineInstruction();
  persistOutlineInstruction();
  freezeOutlineEdit();
  // thread 同样从三处解析，别因为内存里没有就走"新起一条 run"（那会重新解析 + 重新派生）
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  if (!tid) {
    // 还没有 thread（首次就点重出）：退回正常启动路径
    await startExtraction(false);
    return;
  }
  try {
    closeStream();
    await api.workflow.redraftOutline(tid, text);
    currentThreadId = tid;
    // runner 会重建 emitter，旧 SSE 连接收不到新一版的帧
    attachStream(tid);
    setStatusBar('extracting', '已提交提炼要求，张衡重新梳理应答目录…', 0, totalBlocks, doneBlocks);
  } catch (e) {
    console.error('[workflow] redraftOutline 失败', e);
    if (await recoverFromDeadThread(e)) return;   // 已作废/线程未知 → 切到可用运行或提示新建
    attachStream(tid);
    const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
    const msg = String(e?.message || e || '');
    // 后端因"已过闸门"拒绝 → 这是**终态**，不再重绘「重新拆分目录」（点了还是失败）。
    // 明确指向唯一有效路径：重新提炼（会重新解析要求文件）。
    if (msg.includes('重新提炼') || !redraftAllowedByStage().ok) {
      setStatusBar('done', `目录不能再改：${msg}`, pct, totalBlocks, doneBlocks);
      statusActions.innerHTML =
        `<button class="btn-status-primary" id="btn-reextract-now">重新提炼（重新解析要求文件）</button>`;
      document.getElementById('btn-reextract-now')
        .addEventListener('click', () => confirmReextract());
      return;
    }
    setStatusBar('gate_outline', `重出目录失败：${msg}`, pct, totalBlocks, doneBlocks);
  }
}

// ── 提炼结果确认页：可编辑工具 ──
// 字段键从 OUTLINE_FIELDS 派生（见该常量注释），不再手抄一份顺序 —— 漏一个字段
// 就会让那一列在提交时被静默丢回默认值（constraint_level 以前就是这样）。
const OUTLINE_FIELD_KEYS = OUTLINE_FIELDS.map(f => f.key);

function makeOutlineEditable() {
  document.querySelectorAll('.block-card').forEach(card => {
    card.querySelectorAll('.block-field-value, .block-field-list').forEach(el => {
      el.setAttribute('contenteditable', 'true');
      el.dataset.editable = '1';
    });
    // 下拉框不能用 contenteditable，单独解禁（与文本字段同一套开关）
    card.querySelectorAll('.block-field-select').forEach(el => {
      el.disabled = false;
      el.dataset.editable = '1';
    });
  });
}

function freezeOutlineEdit() {
  document.querySelectorAll('.block-card [data-editable="1"]').forEach(el => {
    el.removeAttribute('contenteditable');
    if (el.tagName === 'SELECT') el.disabled = true;
    delete el.dataset.editable;
  });
}

function collectOutlineEdits() {
  const matrix = {};
  document.querySelectorAll('.block-card').forEach(card => {
    const blockId = card.dataset.blockId;
    if (!blockId) return;
    const grid = card.querySelector('.block-card-grid');
    if (!grid) return;
    const fields = grid.querySelectorAll('.block-field');
    const existing = blocksById.get(blockId) || {};
    const row = {
      block_id: blockId,
      title: existing.title || '',
    };
    fields.forEach((f) => {
      // 按 data-field-key 取值，不再按位置对 OUTLINE_FIELD_KEYS —— 位置对齐在
      // 字段增删 / 网格顺序调整时会静默错位（写错列比重建更糟）。
      const key = f.dataset.fieldKey;
      if (!key || !OUTLINE_FIELD_KEYS.includes(key)) return;
      const sel = f.querySelector('.block-field-select');
      const list = f.querySelector('.block-field-list');
      const value = f.querySelector('.block-field-value');
      let text = '';
      if (sel) {
        text = sel.value;
      } else if (list) {
        text = Array.from(list.querySelectorAll('li'))
          .map(li => li.innerText.trim())
          .filter(Boolean)
          .join('\n');
      } else if (value) {
        text = value.innerText.trim();
      }
      row[key] = text;
    });
    matrix[blockId] = row;
    // 同步回本地副本，避免后续 updateBlockCard / 其它分支用旧数据重渲染
    const stored = blocksById.get(blockId);
    if (stored) Object.assign(stored, row);
  });
  return matrix;
}

// 「确认内容大纲及要求」：把用户在提炼结果上的编辑提交，再进素材库。
// 图此时已收尾，所以走 confirm-outline 端点写回（blocks 表 + 图里的矩阵）——
// 不能复用闸门 resume，那条路会 ainvoke 继续往下跑。
async function confirmOutlineContent() {
  const tid = currentThreadId || getThread(projectId);
  const btn = document.getElementById('btn-to-matching');
  if (btn) btn.disabled = true;
  try {
    const matrix = collectOutlineEdits();
    if (tid && Object.keys(matrix).length) {
      await api.workflow.confirmOutline(tid, matrix);
    }
    freezeOutlineEdit();
    switchView('matching', '点下方「素材匹配」开始匹配');
  } catch (e) {
    console.error('[workflow] confirmOutline 失败', e);
    // setStatusBar 会重建按钮组，失败后用户能直接重试
    setStatusBar('done', `确认失败：${e?.message || e}`, 100, totalBlocks, doneBlocks);
  }
}

async function confirmMaterialsGate() {
  if (!currentThreadId) return;
  const btn = document.getElementById('btn-confirm-materials');
  if (btn) btn.disabled = true;
  try {
    const tid = currentThreadId;
    // 本页已移除匹配素材栏，不再做素材剔除：不带 materials 提交，保留沈括匹配到的全部素材。
    await api.workflow.resume(currentThreadId, 'approve');
    // runner.resume 是 fire-and-forget：HTTP 200 时后端 task 仍未把 user_choice
    // 落到 checkpoint。这里等 user_choice='approve' 写盘后再跳，否则 workbench
    // 拿到 stage='materials_review' + user_choice='' 会跳回 project-init（ping-pong）。
    await waitForGateCommitted(tid, 'approve', 3000);
    // 显式带 backend=generating：用户点确认后已经"进入撰写"阶段，即使 backend state api
    // 仍返回 materials_review（graph 还没跑到 generate 节点），workbench 进度条也会立即
    // 把"方案撰写"标蓝。从 workbench 跳回素材匹配时也会保留正确的 furthestStage。
    location.href = `/workbench?projectId=${projectId}&threadId=${encodeURIComponent(tid)}&backend=generating`;
  } catch (e) {
    console.error('[workflow] resume(review_materials) 失败', e);
    if (btn) btn.disabled = false;
  }
}

// 轮询 workflow state，直到 user_choice 更新为 expected（说明 runner._resume_until_pause
// 已 aupdate_state 落盘）；超时即 fallthrough，workbench 端有兜底分支。
async function waitForGateCommitted(tid, expected, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const s = await api.workflow.state(tid);
      if (s?.user_choice === expected) return;
    } catch {}
    await new Promise(r => setTimeout(r, 150));
  }
}

async function confirmOutline() {
  // 老入口兜底：若没有走新流程而点了「进入撰写」，直接跳转
  const btn = document.getElementById('btn-confirm');
  if (btn) btn.disabled = true;
  const tid = currentThreadId || getThread(projectId);
  const qs = tid
    ? `?projectId=${projectId}&threadId=${encodeURIComponent(tid)}`
    : `?projectId=${projectId}`;
  location.href = `/workbench${qs}`;
}

// ── 上传 ──
// 分类纠错：文件名明显是招标人给的技术规范书，却被归到我方素材时给提示。
// **不自动改写角色** —— 用户可能是故意把一份旧规范书当素材引用，拦下来即可。
function renderRoleWarning(filename, role) {
  const box = document.getElementById('req-role-warn');
  if (!box) return;
  const warn = roleMismatchWarning(role, filename);
  box.innerHTML = warn
    ? `<div class="mat-role-warn">「${escapeHtmlForRole(filename)}」${escapeHtmlForRole(warn)}</div>`
    : '';
}
function escapeHtmlForRole(t) {
  return String(t == null ? '' : t).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

async function uploadFile(file, role, btn) {
  btn.disabled = true;
  renderRoleWarning(file.name, role);
  try {
    await api.materials.upload(projectId, file, role);
    await loadMaterials();
  } finally {
    btn.disabled = false;
    // 让 input 可再次选同一个文件
    fileInputReq.value = ''; fileInputSrc.value = '';
  }
}

// 角色说明随下拉联动
(function bindRoleHint() {
  const sel = document.getElementById('req-role-select');
  const hint = document.getElementById('req-role-hint');
  if (!sel || !hint) return;
  const sync = () => { hint.textContent = ROLE_HINT_SHARED[sel.value] || ''; };
  sel.addEventListener('change', sync);
  sync();
})();

// 调试/自救入口：页面上的按钮若因状态判断被隐藏或事件未绑定，可在控制台直接调用。
// （实测出现过"按钮在、点了没反应"，与控制台自查相比，直接给入口更快定位。）
window.dsv = {
  reextract: () => startExtraction(true),      // 重新提炼（重新解析全部要求文件）
  forceRestart: () => forceRestartRun(),       // 强制新建运行（不依赖旧 thread）
  redraft: () => redraftOutline(),             // 重新拆分目录
  state: () => ({ extractionState, currentThreadId, totalBlocks, doneBlocks,
                  thread: getThread(projectId) }),
};

// ── 事件绑定 ──
btnAddReq.addEventListener('click', () => fileInputReq.click());
btnAddSrc.addEventListener('click', () => fileInputSrc.click());
fileInputReq.addEventListener('change', () => {
  const f = fileInputReq.files[0];
  if (f) {
    const role = document.getElementById('req-role-select').value || 'main_rfp';
    uploadFile(f, role, btnAddReq);
  }
});
fileInputSrc.addEventListener('change', () => {
  const f = fileInputSrc.files[0];
  if (f) uploadFile(f, 'source', btnAddSrc);
});
btnExtract.addEventListener('click', async () => {
  // 视图为 matching 时按钮承担「素材匹配」职责
  if (document.body.dataset.view === 'matching') {
    startMatching();
    return;
  }
  // 「拆解评分点」按是否已有章节分派，绝不用 recover 续跑：recover 会越过
  // interrupt_before 的闸门继续往下 —— 停在闸门 2 的线程会被直接带进生成。
  // 已有章节 = 之前拆分过，本次点击的语义是「重新拆分」（redraft 跳过 parse，
  // 只重跑张衡的目录派生）；没有章节才是首次拆分。
  if (totalBlocks > 0) {
    redraftOutline();
  } else {
    startExtraction();
  }
});

// ── 初始化 ──
api.projects.get(projectId).then(p => {
  if (!p?.name) return;
  document.getElementById('header-project-name').textContent = p.name;
  document.title = `AiBidding · ${p.name}`;
});
// 目录展开/收起（点击进度环）
document.getElementById('sidebar-ring-wrap').addEventListener('click', (e) => {
  e.stopPropagation();
  const toc = document.getElementById('sidebar-toc');
  toc.style.display = toc.style.display === 'none' ? 'block' : 'none';
});
document.addEventListener('click', () => {
  const toc = document.getElementById('sidebar-toc');
  if (toc) toc.style.display = 'none';
});

// 自动提炼：立即显示提炼状态栏，材料加载完后发请求
if (params.get('autoExtract') === '1') {
  statusBar.dataset.state = 'extracting';
  statusLabel.textContent = '准备提炼…';
  progressTrack.style.display = 'block';
  progressBar.style.width = '0%';
  statusPct.textContent = '0/0 模块 · 0%';
  statusActions.innerHTML = `<button class="btn-status-secondary" id="btn-cancel">取消提炼</button>`;
  statusActions.querySelector('#btn-cancel')?.addEventListener('click', cancelExtraction);
  setExtractDisabled(true);
  blocksReady = loadExistingBlocks();
  Promise.all([loadMaterials(), blocksReady, loadModels()]).then(() => {
    // 稍等确保材料写入完成
    setTimeout(() => startExtraction(), 500);
  });
} else {
  loadMaterials();
  blocksReady = loadExistingBlocks().then(() => { window.__dshAppReady = true; })
    .catch((e) => {
      // 启动失败必须留痕并显示，不能只留骨架：这条曾经把"模块没加载"藏成"一直在加载"
      console.error('[outline] 首屏加载失败', e);
      (window.__bootErrors = window.__bootErrors || []).push('首屏加载失败：' + (e?.message || e));
      const skel = document.getElementById('blocks-loading');
      if (skel) skel.hidden = true;
    });
  loadModels();
  // 提炼要求：先按 localStorage 回填，闸门 1 快照到达后再以后端值覆盖空输入框
  restoreOutlineInstruction();
}

// 素材匹配工作区的交互（事件委托，只挂一次）：章节树选中 / 总览筛选 / 详情里的挂接解绑
bindMatchSelection();
bindMatchSummary();
bindMatchTreeHead();
bindMatDrawer();
document.getElementById('btn-expand-lane')?.addEventListener('click', () => setLaneCollapsed(false));
try {
  if (localStorage.getItem('matching:laneCollapsed') === '1') setLaneCollapsed(true);
} catch {}
bindOutlineToggleSync();
bindMatchDetailActions();

// 还原 stage 进度（断线续连场景）
(() => {
  // 用 URL 的 view 参数推算 backendStage 下界：用户能从顶部进度条点过来，
  // 必然是 graph 已到达过的阶段。这样在 IIFE 异步 fetch 还没返回前，进度条
  // 也能立即按"matching 已可达"渲染，避免 step 被错误 disabled。
  try {
    if (_viewParam === 'matching') {
      const k = `furthestStage:${projectId}`;
      const prev = sessionStorage.getItem(k);
      const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
      if (prevIdx < 1) sessionStorage.setItem(k, 'materials_review');
    }
  } catch {}

  // 进入页面立刻按 PAGE_STAGE 点亮当前 step（active 不依赖后端 state，
  // 否则在请求返回前进度条会全灰，看似"loading 没完成"）
  renderStageBar();

  // URL 含 threadId（从 /projects 路由进来或顶部进度条历史回看）→ 优先写入 sessionStorage
  const urlTid = params.get('threadId');
  if (urlTid) {
    setThread(projectId, urlTid);
    currentThreadId = urlTid;
  }
  const tid = urlTid || getThread(projectId);

  // 先用 workflow-runs 表里的 stage 兜底点亮进度条（这是 routes 层 update_stage 持久化的版本，
  // 即使 runner 内存清空 / checkpoint 读失败也能拿到）。然后再 best-effort 用 state API 校准。
  fetch(`/api/projects/${projectId}/workflow-runs`).then(r => r.ok ? r.json() : []).then(runs => {
    const latest = Array.isArray(runs) && runs.length ? runs[0] : null;
    if (!latest) return;
    // thread_id 的恢复**不能**依赖 stage 是否在 STAGE_MAP 里：paused / done / aborted
    // 这些值不在表里，以前会把整段跳过 → currentThreadId 一直是空 → 闸门 1 的
    // 「确认目录，开始提炼要点」变成静默 return（用户看到"点了没反应"）。
    if (!currentThreadId && latest.thread_id) {
      setThread(projectId, latest.thread_id);
      currentThreadId = latest.thread_id;
    }
    // 进度条点亮仍按已知阶段来（未知阶段不强行 setStage，避免把视图带偏）
    if (latest.stage && STAGE_MAP[latest.stage]) setStage(latest.stage);
  }).catch(() => {});

  if (!tid) {
    // 无 thread：进度条已默认可见且所有 step 灰显，无需额外处理
    return;
  }
  api.workflow.state(tid).then(async s => {
    const stage = s?.stage;
    // 这里只清过期 threadId，**不再用它的 stage 覆盖视图**。
    // sessionStorage 里这个 thread 可能是上一次停留的（比如上次已经匹配过素材），
    // 它的 stage 比项目最新一次 run 更靠后，照它去 setStage 会把页面切成素材匹配视图，
    // 而状态栏文案已经按当前视图写好了 —— 顶栏说「已提炼」、右栏是匹配面板，自相矛盾。
    // 视图与进度一律以 workflow-runs[0]（项目最新一次 run）为准。
    if (stage === 'failed') {
      // 派生失败等硬失败：**保留 thread**（重试派生要用它），说清原因并给重试入口
      await blocksReady;
      currentGate = null;
      const reason = lastRunErrorMessage(s);
      setStatusBar('failed',
        `本次运行失败${reason ? `：${reason}` : ''}。可点「重新拆分目录」重试。`,
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
      return;
    }
    if (stage === 'done' || stage === 'aborted') {
      resetStageProgress();
      clearThread(projectId);
      // 关键：sessionStorage 清了，但内存里的 currentThreadId 还指着这个死线程 ——
      // 后续「重出目录」「结构调整」会继续打向它，用户只会反复看到同一句报错。
      currentThreadId = null;
      if (stage === 'aborted') {
        if (await switchToLatestUsableRun()) return;   // 切走即重载
        // 没有可用的历史运行：明确给出下一步，别让用户停在空状态里
        await blocksReady;
        setStatusBar('paused', '这次运行已作废（已取消或中断），已清空。点「重新提炼」新建一次运行。',
                     0, totalBlocks, doneBlocks);
      }
      return;
    }
    // 后端重启会清空 runner 内存里的 _Run，但 checkpoint 还停在「跑了一半」的 stage 上，
    // 这个 run 已经没有任何东西在推进了（详见 isOrphanedRun）。而 loadExistingBlocks()
    // 只会按库里的章节数写「已解析 N 个章节，可继续提炼」—— 中断被包装成了一份正常待办。
    // 这里必须说清楚，并且给出路：继续提炼走 recover() 从检查点续跑。
    // 正常停在闸门 1：loadExistingBlocks 只会按数据写「已解析 N 个章节」（那是给中断
    // 场景的文案），这里补回闸门自己的按钮组 —— 先确认目录，再开始提炼。
    if (!isOrphanedRun(s)) {
      if (s?.stage === 'outline_review') {
        await blocksReady;
        // checkpoint 停在 outline_review 有三种场景，靠"已提炼章节数 + workflow_runs"
        // 区分（extract / match 节点只能在自己结束时写 checkpoint，运行期看不到）：
        //   workflow_runs=extracting 且进程仍认得这个 run → 提炼正在跑：重连 SSE 等帧，
        //     不要露出闸门 1 的确认按钮（那会再起一次提炼）。
        //   doneBlocks === totalBlocks → 提炼已跑完，按完成态停住。
        //   0 < doneBlocks < totalBlocks → 被打断：闸门 1 的确认按钮就是"继续提炼"
        //     入口（extract 会跳过已完成的章节）。
        const running = await longPhaseRunning(s);
        if (running === 'extracting') {
          setStatusBar('extracting', '张衡提炼章节要求中…',
                       Math.round(doneBlocks / Math.max(totalBlocks, 1) * 100), totalBlocks, doneBlocks);
          attachStream(tid);
          return;
        }
        if (totalBlocks > 0 && doneBlocks >= totalBlocks) {
          setStatusBar('done', '提炼完成', 100, totalBlocks, doneBlocks);
          return;
        }
        if (doneBlocks > 0) {
          // 提炼到一半：给「继续提炼 / 重新拆分目录 / 重新提炼」，不是闸门 1 的确认
          currentGate = null;
          setPartialOutlineBar();
          return;
        }
        currentGate = 'review_outline';
        setStatusBar('gate_outline', '目录已拆分，请检查并确认后开始提炼要点', 0, totalBlocks, doneBlocks);
      }
      return;
    }
    await blocksReady;  // 等它先写完，否则提示会被它的文案盖掉
    setStatusBar(
      'paused',
      '本次运行已中断（后端重启），章节进度来自上一次运行。点「继续提炼」接着跑，或「重新提炼」从头开始。',
      totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0,
      totalBlocks,
      doneBlocks,
    );
    // 其它情况（idle / 空 stage）保持 workflow-runs 兜底的进度，不主动清空
  }).catch(() => {
    // 后端无该 thread / 请求失败 → 不主动清空，保留 workflow-runs 的兜底
  });
})();
    

// 模式切换：从编辑器切回「要求与大纲 / 素材库」时，重新按 stage 推导一次视图
// （编辑器模式下上面是提前返回的，这里补上）。
document.addEventListener('workspace:mode', (e) => {
  const m = e.detail && e.detail.mode;
  if (!m || m === 'editor') return;
  // 调度层是视图选择的**唯一发起者**：这里必须同步 pageViewOverride，
  // 否则 applyPageView() 会按 backend stage 把刚切的模式改回去
  // （实测：点「素材库」后 view 仍是 outline）。
  pageViewOverride = m;
  applyPageView();
});


// 三模式合并：模式切回「要求与大纲 / 素材库」时由 mode-dispatch 调用。
// 共享宿主 #blocks-list 被搬回来之后，需要按当前模式重画一遍（行/卡片/徽标）。
window.__reloadOutlineTree = () => {
  if (document.body.dataset.view === 'editor') return;   // 编辑器模式下由对方渲染
  try { loadExistingBlocks({ silent: true, keepSelection: true }); } catch (err) {
    console.warn('[outline] 模式切换重绘失败', err);
  }
};


// 调试入口：面板类函数做成窗口钩子，便于在控制台直接验证/排查
// （与 project-init 的 window.dsv 同一做法）。
window.__ovPanels = {
  refreshCoverage: (tid) => refreshOutlineCoverage(tid || currentThreadId || getThread(projectId)),
  renderCoverage: (cov) => renderOutlineCoverage(cov),
  risk: () => toggleScoringRisk(),
  report: (opts) => openSplitReport(opts || {}),
  autoOpen: () => maybeAutoOpenSplitReport(),
  banner: () => showConfirmedBanner(),
  setRevision: (n) => { draftRevision = Number(n) || 0; },
  confirmedRevision: () => getConfirmedRevision(),
};
