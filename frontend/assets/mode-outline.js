/* 由 project-init.html 的内联 <script type="module"> 机械抽取而来（2026-10-13 三模式合并）。
   抽成模块的目的：两段内联脚本有 90 个同名顶层标识符 + 18 个重复 DOM id，
   同页拼接必然互相覆盖；各自成为一个模块即各自一个作用域。
   本文件在抽取时**未改任何逻辑**。 */
import {mountChapterStudio, exportDocument} from './chapter-studio.mjs?v=20261016-v444';
import { api } from './api.js?v=20261016-v444';
import { specContentHtml } from './spec-content.mjs?v=20261016-v444';
// 顶栏（项目名旁边那行「第 N 轮 · ...」）由共享壳 workspace.js 渲染。这里要把它
// 改成**页面自己的真实状态**：checkpoint 的 stage 在准备中/编写准备完成后都停在
// outline_review，只看它的话顶栏永远显示"待确认目录"（实测就是这个问题）。
import { workspace, renderWorkspaceChrome,
         REQUIREMENT_ROLES, SELECTABLE_REQUIREMENT_ROLES, ROLE_LABEL_SHARED, ROLE_HINT_SHARED,
         roleMismatchWarning, inferRoleFromFilename,
         uiAlert, uiConfirm,
       } from './workspace.js?v=20261016-v444';
import { resolvePackageScope, buildOutlineTree, isOrphanedRun, activeStages, instructionDirty, newOutlineId, outlineSubtreeRange,
         moveOutlineNode, indentOutlineNode, outdentOutlineNode,
         moveManyOutlineNodes, indentManyOutlineNodes, outdentManyOutlineNodes,
         moveOutlineNodeTo, moveOutlineNodesTo,
         moveOutlineNodeToLevel, moveOutlineNodesToLevel, levelRangeAt,
         removeManyOutlineSubtrees,
         pushHistory,
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
       } from './workspace-model.mjs?v=20261016-v444';

const params = new URLSearchParams(location.search);
// projectId **只取数字部分**：链接被误写成 `?projectId=19**(Cmd+Shift+R)`（连带说明文字）
// 时，原实现会把整串当 id，导致每个接口都 422、页面一片空白而无任何解释。
// 归一化 + 控制台告警，既容错又不掩盖真实的参数错误。
const _rawProjectId = params.get('projectId') || '';
const _pidMatch = _rawProjectId.match(/\d+/);
const projectId = _pidMatch ? _pidMatch[0] : '';
const packageId = Number(params.get('packageId') || 0);
const packageCacheKey = (name) => `${name}:${projectId}:package:${packageId || 'legacy'}`;
if (_rawProjectId && String(_rawProjectId) !== String(projectId)) {
  console.warn('[outline] projectId 参数含非数字内容，已取数字部分：',
    JSON.stringify(_rawProjectId), '→', projectId);
}
if (!projectId) { location.href = '/projects'; }
if (projectId && !packageId) {
  location.href = `/project-intake?projectId=${encodeURIComponent(projectId)}`;
}
// URL 显式指定的视图模式（顶部进度条点击会带上）；空 / 非法值视为不覆盖
const _viewParam = params.get('view');
// URL 显式指定的 backend stage（跨视图跳转 click handler 会带）：作为 sessionStorage
// 兜底，确保 renderStageBar 第一次绘制就能用真实 backend stage 点亮 active。
const _backendParam = params.get('backend');
if (_backendParam && projectId) {
  try {
    // 只升不降（单调推进）
    const k = packageCacheKey('furthestStage');
    const prev = sessionStorage.getItem(k);
    const STAGE_ORDER_LITERAL = ['parsing', 'matching', 'generating', 'reviewing'];
    const STAGE_MAP_LITERAL = {
      parsing: 'parsing', outline_review: 'parsing',
      // extracting：闸门 1 放行后的逐节准备，同属「解析大纲」这一步（后端运行期 stage）
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
const btnParsePkg = document.getElementById('btn-parse-pkg');
const matHintEl = document.getElementById('mat-hint');
/** 材料条右侧提示：上传/解析/标包结果都写这里（此前这个节点在 JS 里从没被用过）。 */
function setMatHint(text) {
  // 顶部材料条只保留一个 ⓘ：长说明（"已按当前标包归属展示 · 文件变更请到项目准备页"）
  // 放进 tooltip，不再占一行视觉噪音（用户反馈顶部太杂）。
  if (!matHintEl) return;
  const message = String(text || '').trim();
  matHintEl.textContent = '';
  matHintEl.title = message;
  matHintEl.setAttribute('aria-label', message);
}
const modelBtn      = document.getElementById('model-selector-btn');
const modelLabel    = document.getElementById('model-selector-label');
const modelDropdown = document.getElementById('model-dropdown');
const modelListEl   = document.getElementById('model-list');
const statusBar  = document.getElementById('status-bar');
const packageContext = document.getElementById('package-context');
const switchPackageLink = document.getElementById('switch-package');
if (switchPackageLink) {
  switchPackageLink.href = `/project-intake?projectId=${encodeURIComponent(projectId)}`;
  switchPackageLink.addEventListener('click', async event => {
    event.preventDefault();
    try {
      const intake = await api.projects.intake(projectId);
      const choices = intake.packages.filter(p => p.status === 'confirmed');
      const dialog = document.createElement('dialog');
      dialog.style.cssText = 'max-width:520px;width:90%;border:1px solid #ddd;border-radius:14px;padding:24px';
      dialog.innerHTML = `<h3>切换标包工作空间</h3><p>切换前请保存当前编辑。切换不会重新拆分或覆盖成果。</p>
        <div>${choices.map(p => `<button type="button" data-switch="${p.package_id}" style="display:block;margin:8px 0;padding:10px;width:100%" ${p.package_id === packageId ? 'disabled' : ''}>${escapeHtml(p.display_name || `标的 ${p.target_code || '—'} / 标包 ${p.package_code || '—'}`)}${p.package_id === packageId ? '（当前）' : ''}</button>`).join('') || '尚无已确认标包'}</div>
        <p><a href="/project-intake?projectId=${projectId}">管理投标范围与文件依据</a></p><button data-close type="button">取消</button>`;
      document.body.append(dialog);
      dialog.addEventListener('close', () => dialog.remove());
      dialog.querySelector('[data-close]').onclick = () => dialog.close();
      dialog.querySelectorAll('[data-switch]').forEach(button => button.onclick = async () => {
        try {
          button.disabled = true;
          const next = await api.projects.packageWorkspace(projectId, button.dataset.switch);
          const q = new URLSearchParams({projectId, packageId: button.dataset.switch, view: 'outline'});
          if (next.latest_run?.thread_id) q.set('threadId', next.latest_run.thread_id);
          location.href = `/three-mode?${q}`;
        } catch (err) { button.disabled = false; uiAlert(err.message); }
      });
      dialog.showModal();
    } catch (err) { uiAlert(err.message); }
  });
}
let activePackage = null;
const activePackageReady = packageId ? api.projects.packageWorkspace(projectId, packageId).then(result => {
  activePackage = result.package;
  return result;
}) : Promise.resolve(null);
activePackageReady.catch(() => {});
if (packageContext && packageId) {
  packageContext.href = `/project-intake?projectId=${encodeURIComponent(projectId)}`;
  activePackageReady.then(({ package: pkg }) => {
    packageContext.textContent = pkg.display_name
      || `标的 ${pkg.target_code || '—'} / 标包 ${pkg.package_code || '—'}`;
  }).catch((err) => {
    packageContext.textContent = '标包不可用';
    packageContext.title = err?.message || '';
  });
}
const statusLabel = document.getElementById('status-label');
const progressTrack = document.getElementById('progress-track');
const progressBar   = document.getElementById('progress-bar');
const statusPct     = document.getElementById('status-pct');
const statusActions = document.getElementById('status-actions');
const blocksList  = document.getElementById('blocks-list');
// 覆盖率面板 / 得分风险面板的样式。刻意用**行内流式元素**，不 prepend 到 shell ——
// 之前那条 43px 横幅就是因为插到整页最前面把布局顶下去了。
document.head.insertAdjacentHTML('beforeend', `<style>
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
import { chapterNode, chapterBadgesHtml as sharedBadgesHtml } from './chapter-tree.mjs?v=20261016-v444';
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
    const cached = sessionStorage.getItem(packageCacheKey('furthestStage'));
    if (cached) return cached;
  } catch {}
  return null;
})();
const blocksByOrder = []; // 按 outline_extract 顺序累积，给 confirm 用
const blocksById = new Map();

// ── 视图模式：outline（拆分评分点） / matching（素材匹配） ──
// 优先级：URL ?view=... 显式指定 > 后端 stage > 大纲完成度。
// 这样从顶部进度条点击「解析大纲」/「素材匹配」可以强制切到对应视图，
// 即使大纲已全部编写准备完成也能回到 outline 视图查看 / 编辑。
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
    // 由那边的「素材匹配」按钮触发，不按编写准备完成度自动切过去。
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
      btn.innerHTML = '✦ 拆分评分点';
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

// 同一项目的不同标包不能复用 session thread；否则切换标包会恢复到另一个标包的运行。
const THREAD_KEY = (pid) => `workflow:${pid}:package:${packageId || 'legacy'}`;
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
  // extracting：闸门 1 放行后的逐节准备，仍属「解析大纲」这一步。
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
      const k = packageCacheKey('furthestStage');
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
      const cached = sessionStorage.getItem(packageCacheKey('furthestStage'));
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
    || getThread(projectId)
    || params.get('threadId');
  // 跳转前把 backend stage 写 sessionStorage（跨页面继承）+ URL（同步可靠源）。
  // sessionStorage 写入 + 读取在某些场景下可能与跳转时机错位，URL 是最稳的兜底。
  let backendForUrl = currentStage;
  if (!backendForUrl || !STAGE_MAP[backendForUrl]) {
    backendForUrl = sessionStorage.getItem(packageCacheKey('furthestStage')) || '';
  }
  try {
    if (backendForUrl && STAGE_MAP[backendForUrl]) {
      const k = packageCacheKey('furthestStage');
      const prev = sessionStorage.getItem(k);
      const prevIdx = prev ? STAGE_ORDER.indexOf(STAGE_MAP[prev] || '') : -1;
      const newIdx = STAGE_ORDER.indexOf(STAGE_MAP[backendForUrl]);
      if (newIdx > prevIdx) sessionStorage.setItem(k, backendForUrl);
    }
  } catch {}
  const enc = tid ? encodeURIComponent(tid) : '';
  const tidQuery = tid ? `&threadId=${enc}` : '';
  const backendQuery = backendForUrl ? `&backend=${encodeURIComponent(backendForUrl)}` : '';
  const packageQuery = `&packageId=${encodeURIComponent(packageId)}`;
  const routes = {
    parsing: `/three-mode?projectId=${projectId}${packageQuery}${tidQuery}&view=outline${backendQuery}`,
    matching: `/three-mode?projectId=${projectId}${packageQuery}${tidQuery}&view=matching${backendQuery}`,
    generating: tid ? `/three-mode?projectId=${projectId}${packageQuery}${tidQuery}&view=editor${backendQuery}` : null,
    reviewing: tid ? `/review?projectId=${projectId}${packageQuery}&threadId=${enc}${backendQuery}` : null,
    report_review: tid ? `/review?projectId=${projectId}${packageQuery}&threadId=${enc}${backendQuery}` : null,
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

// 材料条：展开全部 / 管理材料（管理 = 直接展开成多行，逐条可改类型与删除）
document.getElementById('mat-more')?.addEventListener('click', () => {
  const rows = document.getElementById('mat-rows');
  if (!rows) return;
  rows.classList.toggle('expanded');
  refreshMaterialSummary();
});
/** 台面（卡片区）收起/展开 —— 设计稿 A 承诺的能力；状态记在 localStorage，刷新后保持。 */
function setDeskCollapsed(collapsed) {
  const box = document.getElementById('mat-cards');
  const foot = document.getElementById('desk-foot');
  const btn = document.getElementById('mat-desk-toggle');
  if (box) box.hidden = !!collapsed;
  if (foot) foot.hidden = !!collapsed;
  if (btn) {
    btn.textContent = collapsed ? '展开台面 ▾' : '收起台面 ▴';
    btn.setAttribute('aria-expanded', String(!collapsed));
    btn.title = collapsed ? '展开文件台面' : '收起文件台面（状态不会丢）';
  }
  try { localStorage.setItem('ge.matDesk.collapsed', collapsed ? '1' : '0'); } catch {}
}

document.getElementById('mat-desk-toggle')?.addEventListener('click', (ev) => {
  ev.preventDefault();
  const box = document.getElementById('mat-cards');
  setDeskCollapsed(!(box && box.hidden));   // 当前可见 → 收起
});
// 启动即恢复上次的台面折叠状态（刷新后保持）
try {
  if (localStorage.getItem('ge.matDesk.collapsed') !== '0') setDeskCollapsed(true);
} catch {}


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
/** 材料条的摘要（数量 + 分组构成）与"展开全部"控制。
 *  文件多时第一行先给构成，第二行只显示前两行内的 chip，其余折叠。 */
function refreshMaterialSummary() {
  const chips = [...document.querySelectorAll('#req-stack .mat-row, #src-stack .mat-row')];
  const total = packageId ? lastMaterials.length : chips.length;
  const t = document.getElementById('mat-total');
  if (t) t.textContent = String(total);
  const gsum = document.getElementById('mat-gsum');
  if (gsum) {
    // 方案 A 的摘要：份数 + 体积 + **异常计数**（漏项一眼可见，吸收 B 的优点）
    const mats = lastMaterials;
    const bytes = mats.reduce((n, m) => n + (Number(m.file_size) || 0), 0);
    const bad = mats.filter((m) => matIntegrity[String(m.id)]).length;
    gsum.innerHTML = `${mats.length} 份文件 · 共 ${fmtSize(bytes)}`
      + (bad ? ` · <b style="color:#dc2626">异常 ${bad}</b>` : '');
  }
  // 第二行超过可视高度 → 显示「展开全部」
  const rows = document.getElementById('mat-rows');
  const more = document.getElementById('mat-more');
  if (rows && more) {
    const expanded = rows.classList.contains('expanded');
    more.hidden = expanded ? false : rows.scrollHeight <= rows.clientHeight + 4;
    more.textContent = expanded ? '收起 ▴' : '展开全部 ▾';
  }
}

async function loadMaterials() {
  await activePackageReady;
  await loadMaterialIntegrity();
  const list = await api.materials.list(projectId);
  await loadPackageAnalysis().catch(() => {});   // 卡片要显示标包归属
  // 「解析标包」按**全部材料**判断（只看 req-stack 会在"只传了素材"时误禁用）
  if (btnParsePkg) {
    btnParsePkg.disabled = list.length === 0;
    btnParsePkg.title = list.length
      ? '读取已上传文件的正文，检索「标的 / 标包 / 标段」；只按原文事实列出，不猜测'
      : '先上传文件，再解析标包';
  }
  const reqRoles = ['spec', 'main_rfp', 'requirement', 'scoring', 'evaluation'];
  const reqs = list.filter(m => reqRoles.includes(m.role) && (!activePackage ||
    (m.role === 'spec' && activePackage.spec_material_ids !== undefined
      ? activePackage.spec_material_ids.map(String).includes(String(m.id)) :
    (activePackage.source_material_ids || []).map(String).includes(String(m.id)) ||
    materialPackagesOf(m.id).some(p => String(p.target || '') === String(activePackage.target_code || '')
      && String(p.package_no || '') === String(activePackage.package_code || '')))));
  const srcs = list.filter(m => m.role === 'source');
  lastMaterials = reqs;
  lastSrcMaterials = srcs;    // 依据刷新时只重画用量徽标，不重拉列表
  sourceMaterialCount = srcs.length;   // 匹配详情的空状态：一份素材都没有时说清"先上传"
  renderMatchTreeHead();               // 栏头上的「素材 N 份」跟着变
  renderMats(reqStack, reqs, true);
  renderMats(srcStack, srcs, false);
  renderMaterialCards(reqs);
  refreshMaterialSummary();          // 摘要：份数 + 体积 + 异常计数
  setMatHint('已按当前标包归属展示 · 文件变更请到项目准备页');
  // 左栏已按原型移除：计数改由材料条的摘要（#mat-total / #mat-gsum）承担。
  // 这里对旧节点做空值保护，避免整页脚本因一个 null 崩掉（实测踩过）。
  if (reqCount) reqCount.textContent = `${reqs.length} 项`;
  if (srcCount) srcCount.textContent = `${srcs.length} 项`;
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
    const mine = materialPackagesOf(m.id);
    return `<div class="mat-row">
      ${extBadge(m.filename)}
      <span class="mat-name" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</span>
      <button type="button" class="mat-pkg${mine.length ? '' : ' is-none'}" data-pkg-open="${m.id}"
        title="这份文件属于哪些标包（可多选；一份文件可以属于多个标包）">${
        mine.length ? escapeHtml(mine.map(pkgLabelOf).join('、')) : '未指定标包'}</button>
      ${roleCell}
      ${matLossBadge(m.id) || parseStatusBadge(m.parse_status)}
      ${showRole ? '' : materialUsageHtml(m)}
      <span class="mat-size">${fmtSize(m.file_size)}</span>
      <button class="mat-del" data-id="${m.id}" title="删除">×</button>
    </div>`;
  }).join('');
  container.querySelectorAll('[data-pkg-open]').forEach((b) => b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    openMaterialPackagesPicker(b.dataset.pkgOpen, b);
  }));
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
// 顶栏文案：页面（有 blocks 计数 + SSE 帧）比 checkpoint 更清楚"准备到哪了"。
// 返回 '' 表示交回共享壳按 stageLabels 兜底（素材库视图 / 闸门 1 等）。
function headerStageLabel(state) {
  const total = Number(totalBlocks) || 0;
  const done = Number(doneBlocks) || 0;
  const failed = failedBlockCount();
  if (state === 'failed') return '本次运行失败';
  if (state === 'idle') {
    // 主动作从"左栏底部按钮"搬到状态行（左栏已按原型移除）
    setStatusActions('idle', `
      <button class="btn-status-primary" id="btn-start-split">拆分评分点</button>`, () => {
      document.getElementById('btn-start-split')?.addEventListener('click', restartExtraction);
    });
  } else if (state === 'extracting') {
    // 拆分期间（解析 + 梳理目录）不能显示「编写准备中（0/530）」——那时还没开始逐节准备，
    // 而且 totalBlocks 还是上一版的数字（用户反馈：解析时显示"编写准备中"很奇怪）。
    if (splitInProgress) return '拆分中（解析材料）';
    if (outlineDrafting) return '拆分中（梳理目录）';
    return total ? `编写准备中（${done}/${total}）` : '编写准备中';
  }
  if (state === 'paused' && total > 0 && done < total) {
    return failed ? `编写准备到一半（${done}/${total}，${failed} 节失败）` : `编写准备到一半（${done}/${total}）`;
  }
  if (state === 'done' && document.body.dataset.view !== 'matching') {
    return failed ? `编写准备完成（${failed} 节失败，可重试）` : '编写准备完成，待确认内容';
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
  // 同一组按钮**且这次也带按钮**时保留现有节点与监听器
  // （避免用户按下瞬间节点被替换 → "点了没反应"）。
  // 注意必须放过 `html === ''`：那是"这一态不该有按钮"，
  // 若也走早退，上一态留下的按钮会一直挂着（实测：「取消」在梳理目录阶段删不掉）。
  if (key === statusActionsKey && statusActions.innerHTML && html) {
    return;
  }
  statusActionsKey = key;
  statusActions.innerHTML = html;
  if (bind) bind();
}

function setStatusBar(state, label, pct, totalB, doneB) {
  // 统一的"退出运行态"收口：状态不再是 extracting 就把左下的提示文案还原
  // （运行中它显示"正在拆分…点按钮可取消并重新拆分"）。
  if (state !== 'extracting') restoreExtractHint();
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
  // 解析阶段总数还是未知（后端给 -1）→ 不要显示 "0/-1 模块"，读者以为出错了
  statusPct.textContent = Number(totalB) > 0
    ? `${doneB}/${totalB} 模块 · ${pct}%`
    : (outlineDrafting ? '梳理目录' : (splitInProgress ? '正在解析' : '—'));
  // 注意：**不要**在这里无条件清空按钮。清空 = 每次 setStatusBar 都重建节点，
  // 用户按下的瞬间节点可能刚被替换 → click 落在已移除的节点上，"点了没反应"。
  // 各分支用 setStatusActions(签名, html, bind) 渲染，同签名时只留现有节点。
  if (state === 'extracting') {
    // 「取消」只在**逐节准备**这类长任务里给：它停止后续模型调用、保留已准备的章节，
    // 「继续准备」会用 skip_ids 接着跑（每章一次调用，中途停下能省额度）。
    // 「梳理应答目录」是秒级短任务：取消它保不住任何东西，只会让人以为必须点它才能重跑，
    // 所以那个阶段不显示（用户反馈后调整）。
    // 只在"真的准备出过章节"时才给「取消」：解析阶段、梳理目录阶段取消都保不住东西
    // （解析还没产出章节，梳理是秒级短任务），给了反而让用户以为重跑必须先点它。
    const hasProgress = Number(doneBlocks) > 0 || Number(totalBlocks) > 0;
    if (outlineDrafting || !hasProgress) {
      setStatusActions('extracting', '', () => {});
    } else {
      setStatusActions('extracting',
        `<button class="btn-status-secondary" id="btn-cancel"
          title="取消在**当前章节**跑完后生效（正在进行的这一次模型调用不会被打断）">取消</button>`,
        () => document.getElementById('btn-cancel').addEventListener('click', cancelExtraction));
    }
  } else if (state === 'points_pending') {
    // 第①步停留态：唯一该做的就是去生成目录（按钮直接展开「拆分结果」）。
    // 这里**不能**给「确认目录」——那会用上一版目录继续准备（自相矛盾）。
    // 回退类动作（重新拆分评分点 / 重新拆分目录 / 重新拆分评分点）统一收在面板头的「重做 ▾」里，
    // 这里只留**前进**动作，避免同一动作两处出现（用户反馈"逻辑重复"）。
    setStatusActions('points_pending', `
      <button class="btn-status-primary" id="btn-gen-outline">生成所选目录 ▸</button>`, () => {
      document.getElementById('btn-gen-outline')?.addEventListener('click', () => {
        splitPanelAutoOpenedFor = 0;
        openSplitReport();
      });
    });
  } else if (state === 'gate_outline') {
    // 面板里刚点过「确认并生成目录」→ 那是同一次决策，这里自动放行，不再要第二次确认。
    if (autoConfirmOutlineAt && Date.now() - autoConfirmOutlineAt < 15 * 60 * 1000) {
      autoConfirmOutlineAt = 0;
      setStatusActions('gate_outline', `
        <span class="status-pill is-ok">目录已按你的确认生成</span>`, () => {});
      setTimeout(() => confirmOutlineGate(), 60);   // 下一拍再放行，先让状态行落到目标态
      return;
    }
    // 闸门 1 不再给「取消」：运行已停住、不消耗资源，点它只会把 run 标成作废；
    // 要重来用「重新拆分目录」。
    setStatusActions('gate_outline', `
      <button class="btn-status-primary" id="btn-confirm-outline">确认目录</button>`, () => {
      document.getElementById('btn-confirm-outline').addEventListener('click', confirmOutlineGate);
    });
    // 闸门 1 只审**目录结构**（增删改/上下移/缩进走每行的「编辑」按钮）；8 字段此刻
    // 还没准备，没有可编辑内容 —— 这里不再 makeOutlineEditable()，否则只是空转。
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
    // 文案随当前视图变：outline 视图是「要点准备完、等确认内容大纲」，
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
        <button class="btn-status-primary" id="btn-to-matching">确认内容大纲及要求</button>`, () => {
        document.getElementById('btn-to-matching').addEventListener('click', confirmOutlineContent);
      });
      // 确认前允许修改：这份准备结果正是用户要审的内容，只读就没法改
      makeOutlineEditable();
    }
  } else if (state === 'failed') {
    // 硬失败（如目录派生失败）：不是「等一会就好」，给明确的重试入口。
    // 线程保留 —— 要求文件已经解析好，重出目录复用的是同一个 thread 的 checkpoint。
    // 失败态只留一个**上下文重试**（最常见的那条：重出目录）；
    // 其它范围（重新拆分评分点 / 重新拆分评分点）在面板头的「重做 ▾」里。
    // 失败原因不再铺在界面上 → 由面板头的运行状态提示指向「运行详情」。
    setStatusActions('failed', `
      <button class="btn-status-primary" id="btn-retry-outline">重试</button>`, () => {
      document.getElementById('btn-retry-outline')?.addEventListener('click', redraftOutline);
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
      // 「编写准备到一半」的按钮组：此时**不该**再出现「确认目录」——
      // 目录早就确认过了，用户要做的是把剩下的章节接着准备（只补剩余、不重复调用），
      // 或者改目录 / 从头再来（这两条才是要付代价的路径）。运行中才有「取消」，
      // 这个状态下已经没有在跑的东西，所以不再放取消。
      const remain = Math.max((totalBlocks || 0) - (doneBlocks || 0), 0);
      const redraftGate = redraftAllowedByStage();
      setStatusActions('paused_outline', `
        <button class="btn-status-secondary" id="btn-reextract">重新拆分评分点</button>
        <button class="btn-status-secondary" id="btn-redraft-outline"
          ${redraftGate.ok ? '' : 'disabled'}
          title="${redraftGate.ok ? '换一版要求重新派生目录'
                               : '本次运行已过闸门，目录不能再改；请用「重新拆分评分点」'}">重新拆分目录</button>
        <button class="btn-status-primary" id="btn-resume-extract"
          title="只准备还没做的章节；已完成的章节不会重复调用模型">继续准备${
          remain ? `（${remain} 节）` : ''}</button>`, () => {
        document.getElementById('btn-reextract').addEventListener('click', confirmReextract);
        document.getElementById('btn-redraft-outline').addEventListener('click', redraftOutline);
        // 继续准备 = resume 同一条 run（跳过已完成的章节），不是重新起一条
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

// no_source：这节本来就没有可准备的原文（用户新增 / 纯目录节点）——**不是失败**，
// 但要和"已完成"区分开，否则用户看到空字段会以为准备坏了。
const BADGE_TEXT = { pending: '待准备', extracting: '准备中', done: '已完成 ✓',
                     error: '准备失败', no_source: '无需准备' };



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
// 卡片网格、SSE 结果更新、准备结果确认页的编辑提交三处都从这里取字段表，避免各写
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

// 已准备但该字段为空 → 显示「—」（不显示骨架条：骨架会被读成"还在准备"）。
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

// 强制等级：默认 disabled —— 只有 makeOutlineEditable()（准备结果确认页）解禁，
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
// 只是空噪声；准备中挂单行占位（见 mountExtractingPlaceholder）；结果到达时换成它。
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
// 但**准备中是瞬态**，SSE 帧比 blocks 行更新得快，所以那一档以入参 status 为准。
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
      ${chip('unwritten', '未撰写')}${chip('pending', '待准备')}
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
  // 想核对 8 项准备字段就点行首的 ›（展开态记在匹配页自己的记忆里，不影响大纲页）。
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
         暂无可匹配的素材。<br>
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
      暂无素材。上传方案 / 产品资料后点「分析素材归属」，系统会把它们挂到对应章节。</p>`;
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
        <tbody>${rows}</tbody></table>` : '<div class="mw-empty">暂无审查结论。先在「方案编辑」生成正文，再跑审查。</div>'}
    </div>`;
  el.querySelector('[data-act="close-risk"]')?.addEventListener('click', () => {
    el.dataset.risk = ''; renderMatchDetail();
  });
}

// 覆盖矩阵（跨章节一屏看全）：行 = 章节，列 = 准备 / 依据 / 条目覆盖 / 正文。
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

// 「准备中」占位：一行说明 + 转圈，而不是 7 行空字段。
// 逐节准备是串行的，单节十几秒到一分钟；空字段看起来像"这一节没有内容"，
// 用户会以为准备失败或没跑。结果到达后 updateBlockCard 会整块换成 8 字段网格。
function mountExtractingPlaceholder(card) {
  const body = card?.querySelector('.block-card-body');
  if (!body) return;
  body.innerHTML = `
    <div class="block-card-extracting">
      <span class="extract-spinner" aria-hidden="true"></span>
      <span class="extract-text">正在准备本节 8 项要求…</span>
      <span class="extract-hint">应标要求 · 应标重点 · 否决项 · 加分项 · 评分项 · 证明材料 · 强制等级 · 量化指标</span>
    </div>`;
}

// 编写准备完成 / 中止后：把**已有内容**的章节保持展开。
// 用户刚准备出来的 8 字段不该因为一次 resync 或流程收尾就整体折叠起来
// （反馈原话：「编写准备完成之后 8 个字段的模块直接会隐藏」）。
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
  // 结果到达：整块重建 8 字段（闸门 1 的卡片本来没有网格；准备中的骨架也要换成值）。
  // 空字段走「—」而不是骨架 —— 骨架会让用户以为这一节还在准备。
  mountOutlineFields(card, data);
  syncOutlineRow(blockId);
}

// ── 死线程恢复：run 已作废 / 后端不认识这个 thread ──
// 用户看到「该工作流已作废，请创建新的工作流」时并不知道该点哪里，页面会卡在这个
// 死线程上（后续每个动作都打向它）。这里改成：优先切到本项目**最近一次可用运行**
// （它可能正停在某个闸门等着），没有可用的才清空、让用户新建一次。
async function switchToLatestUsableRun() {
  try {
    const runs = await fetch(`/api/projects/${projectId}/workflow-runs?packageId=${encodeURIComponent(packageId)}`)
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
      ? '当前运行已作废（已取消或中断），已清空。点「重新拆分评分点」会新建一次运行。'
      : '找不到这次运行（可能已被清理），已清空。点「重新拆分评分点」会新建一次运行。',
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

// 「已准备」以 status 字段为权威：upsert 时写 'outline_done' 才算完成。
// 不能用 key_points/requirement 是否为空兜底 —— 部分章节（纯目录、分组节点）
// LLM 准备后这两个字段就是空串，会被误判为未完成。
// isDoneBlock / badgeStatusOf 的实现搬到了 assets/workspace-model.mjs：
// 「失败不算完成」是条契约，放到有 node --test 覆盖的纯函数里才守得住。
// 见 import 处的别名。

// 用户在②目录确认里主动点了「调整结构」→ 允许编辑。
// （结构编辑能力本来只在 gate_outline / done 开放，而②可能停在别的阶段；
//   用户主动要求调整时不该被阶段卡住。）
let structureEditRequested = false;

function structureEditable() {
  if (structureEditRequested) return true;
  if (extractionState === 'gate_outline') return true;
  // 编写准备完成后的目录视图也允许调整（此时 8 字段可编辑，改名/调序不该被禁）
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
// 编写准备完成后的目录视图（done 态）不会再调 setStatusBar，不刷就会一直显示旧数字。
function refreshStatusCounters() {
  const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0;
  statusPct.textContent = `${doneBlocks}/${totalBlocks} 模块 · ${pct}%`;
  progressBar.style.width = pct + '%';
  updateSidebarProgress();
}

// ── 保存 ──
// 串行化：连点「上移」时请求依次发出，最后一个请求携带的顺序即最终顺序。
function queueStructureSave(nodes, purge = false, removedIds = []) {
  const tid = outlineTid();
  const payload = structurePayload(nodes);
  const run = structureSaveChain.then(() =>
    api.workflow.updateOutlineStructure(tid, payload, purge, removedIds));
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
 * 「确认目录」—— 一旦确认就会去准备 531 个原始章节。
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
async function resolvePanelThreadId(tid) {
  if (tid) return tid;
  const selected = params.get('threadId') || currentThreadId || workspace?.threadId || getThread(projectId);
  if (selected) return selected;
  try { return await api.workflow.latestRunThreadId(projectId); } catch (e) { return null; }
}

/**
 * 投标范围选择器：**一次设定、贯穿全程**。
 *
 * "我们投哪个标包"是投标人的属性，不该每次从拆分要求里的文字去猜 ——
 * 实测多标包文件里推错范围 = 整份技术应答目录错。选项来自评分地图的标包索引。
 *
 * ⚠️ 这个函数曾被整段删除过（重写 openSplitReport 时连带删掉），而调用点还在，
 * 报错又被下面的 try/catch 吞了 —— 界面只表现为"投标范围一直加载中"。
 * 所以本函数失败时必须**留痕**（console.warn），不能静默。
 */

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
    lastCoverageScope = {
      target: (scope || report?.project_scope || {}).target || '',
      package_no: (scope || report?.project_scope || {}).package_no || '',
      categories: (report || {}).current_categories || [],
    };
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
let splitPanelExpanded = true;   // 面板默认展开（用户要求：不要"收起"这类操作）
// 保存后的即时反馈（面板会重开以刷新状态，用它把"刚才那步成功了"带回来）
let splitPanelFlash = '';
let splitPanelFlashKind = '';      // '' | 'err'
// 目录是否"待生成"（第①步只拆评分点之后的中间态）。由 refreshOutlineCoverage 写入，
// loadExistingBlocks 据此自动展开「拆分结果」—— 启动时 thread 还没解析出来，
// 光靠 restoreSettledThread 那条路会漏掉这个提示（实测面板不出现）。
let outlinePending = false;
// 覆盖率渲染用的上下文（当前目录对应的标包 + 类别），由 refreshOutlineCoverage 写入
let lastCoverageScope = null;
// 面板的「查看范围」：标包下拉切换的是**看哪个标包**（不自动重跑）。
// 用户口径：技术要求也按标包归属，不存在共享；切换后若该标包还没生成目录，
// 相关页签必须显示空态 + 一键生成，绝不能继续显示上一个标包的目录。
let panelViewPkg = null;      // {target, package_no} | null（null = 跟随当前目录）
// ② 目录确认与调整：选中的章节行（选中才显示该行操作）+ 步骤（1 目录 / 2 规范书）
let selectedStructId = null;
let responsePlanChoice = '';
let responsePlanPoll = null;
let structSaveNote = '';   // 结构保存结果（新增/更新/删除）—— 保存后显示，必须可见
let topicSaveNote = '';    // 归属保存结果 —— 保存后\*\*面板会重绘\*\*，所以用模块变量而不是就地写 DOM
// 文件↔标包（多对多）：一个人工可改的归属
/** 方案 A「文档台面」：把每份材料渲染成一张卡 —— 状态与操作**跟着文件走**，
 * 不用点开「明细」就知道每个文件是什么、读到没有、归哪个标包。
 * 「明细」保留为详细编辑（改类型/删除），不再承担"看状态"的职责。
 */
function cardStateOf(m) {
  const reason = matIntegrity[String(m.id)];
  if (reason === 'missing') return { cls: 'is-bad', chip: 'bad', text: '文件已丢失', act: '重新上传', upload: true };
  if (reason === 'empty') return { cls: 'is-bad', chip: 'bad', text: '文件为空', act: '重新上传', upload: true };
  if (reason === 'no_path') return { cls: 'is-warn', chip: 'warn', text: '无文件路径', act: '查看明细' };
  if (reason === 'no_chunks') return { cls: 'is-warn', chip: 'warn', text: '未解析出正文', act: '查看明细' };
  const st = String(m.parse_status || '');
  if (st === 'failed') return { cls: 'is-bad', chip: 'bad', text: '解析失败', act: '查看明细' };
  if (st === 'done') return { cls: '', chip: '', text: '已解析', act: '查看明细' };
  return { cls: '', chip: 'wait', text: '待解析', act: '查看明细' };
}

function renderMaterialCards(mats) {
  const box = document.getElementById('mat-cards');
  if (!box) return;
  if (packageId) {
    box.innerHTML = (mats || []).map(m => `<div class="mcard"><span class="badge">${escapeHtml(ROLE_LABEL_SHARED[m.role] || m.role)}</span>
      <div class="mfname" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</div>
      <span class="mchip">${escapeHtml(cardStateOf(m).text)}</span>
      <a class="mact" href="/api/materials/${m.id}/file" target="_blank" rel="noopener">查看原文 ›</a></div>`).join('')
      || '<p class="hint">当前标包尚无已关联的招标依据，请返回项目准备核对文件归属。</p>';
    return;
  }
  const cards = (mats || []).map((m) => {
    const st = cardStateOf(m);
    const pkgs = materialPackagesOf(m.id);
    const role = String(m.role || '');
    const label = ROLE_LABEL_SHARED[role] || role || '其他';
    const isSrc = !(REQUIREMENT_ROLES || []).includes(role);
    return `<div class="mcard ${st.cls}">
      <button type="button" class="badge${isSrc ? ' src' : ''}" data-card-role="${m.id}"
        title="改类型：展开「明细」后在这一行改">${escapeHtml(label)}</button>
      <div class="mfname" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</div>
      <span class="mchip ${st.chip}"><span class="d"></span>${escapeHtml(st.text)}</span>
      <div class="mpkgs">${pkgs.length
        ? pkgs.map((x) => `<button type="button" class="mpkg" data-pkg-open="${m.id}"
            title="这份文件属于哪些标包（可多选）">${escapeHtml(pkgLabelOf(x))}</button>`).join('')
        : `<button type="button" class="mpkg none" data-pkg-open="${m.id}"
            title="还没指定标包 —— 点开选择（一份文件可以属于多个标包）">未指定标包</button>`}
      </div>
      <button type="button" class="mact${st.upload ? '' : ' q'}"
        data-card-act="${m.id}" data-act-upload="${st.upload ? '1' : ''}">${escapeHtml(st.upload ? st.act : '更多')} ›</button>
    </div>`;
  }).join('');
  box.innerHTML = cards + `<button type="button" class="mcard add" data-card-add="1">
      <span class="plus">＋</span><span class="t">添加文件</span>
      <span class="s">支持多选 · PDF / DOCX / XLSX</span></button>`;
  bindMaterialCards(box);
}

/** 卡片自带的操作菜单：改类型 / 重新上传 / 删除。
 *
 * 用户要求去掉「明细」按钮及其点击逻辑 —— 明细里的改类型与删除必须搬到卡片上，
 * 否则功能就丢了。所以这里复用同一批接口（PATCH /api/materials/{id}、DELETE）。
 */
const CARD_ROLE_OPTIONS = [
  ['main_rfp', '招标文件'], ['spec', '技术规范书'], ['source', '我方素材'], ['requirement', '其他'],
];

function openMaterialCardMenu(materialId, btn) {
  document.querySelectorAll('.mmore-pop').forEach((x) => x.remove());
  const card = btn.closest('.mcard');
  if (!card) return;
  const mat = lastMaterials.find((m) => String(m.id) === String(materialId)) || {};
  const curRole = String(mat.role || '');
  const lost = String(matIntegrity[String(materialId)] || '') === 'missing'
    || String(matIntegrity[String(materialId)] || '') === 'empty';

  const pop = document.createElement('div');
  pop.className = 'mmore-pop';
  pop.innerHTML = `
    <div class="mmore-t">改类型</div>
    ${CARD_ROLE_OPTIONS.map(([v, l]) => `<button type="button" class="mmore-i${v === curRole ? ' on' : ''}"
      data-role="${v}">${escapeHtml(l)}${v === curRole ? ' ✓' : ''}</button>`).join('')}
    <div class="mmore-sep"></div>
    <button type="button" class="mmore-i" data-reup="1">重新上传</button>
    <button type="button" class="mmore-i danger" data-del="1">删除这份文件</button>`;
  card.appendChild(pop);

  pop.querySelectorAll('[data-role]').forEach((b) => b.addEventListener('click', async () => {
    const role = b.dataset.role;
    if (role === curRole) { pop.remove(); return; }
    try {
      await fetch(`/api/materials/${materialId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      });
      setMatHint(`已把这份文件改为「${b.textContent.replace(' ✓', '')}」`);
      pop.remove();
      await loadMaterials();
    } catch (e) { console.error('[materials] 改类型失败', e); }
  }));
  pop.querySelector('[data-reup]')?.addEventListener('click', () => {
    pop.remove();
    document.getElementById('btn-add-req')?.click();
    setMatHint('请重新选择这份文件上传');
  });
  pop.querySelector('[data-del]')?.addEventListener('click', async () => {
    pop.remove();
    const okDel = await uiConfirm('删除这份文件？', '记录与已解析内容都会被删除，不可撤销。', '删除');
    if (!okDel) return;
    await fetch(`/api/materials/${materialId}`, { method: 'DELETE' });
    setMatHint('已删除');
    await loadMaterials();
  });
  if (lost) pop.querySelector('[data-reup]')?.classList.add('on');

  window.setTimeout(() => {
    const off = (e) => {
      if (!pop.contains(e.target) && !card.contains(e.target)) {
        pop.remove();
        document.removeEventListener('click', off, true);
      }
    };
    document.addEventListener('click', off, true);
  }, 0);
}

function bindMaterialCards(box) {
  box.querySelectorAll('[data-pkg-open]').forEach((b) => b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    openMaterialPackagesPicker(b.dataset.pkgOpen, b);
  }));
  box.querySelectorAll('[data-card-act]').forEach((b) => b.addEventListener('click', () => {
    const id = b.dataset.cardAct;
    if (b.dataset.actUpload) {                 // 文件丢了/为空 → 走重新上传
      document.getElementById('btn-add-req')?.click();
      setMatHint('请重新选择这份文件上传；上传后会在原位替换同名记录');
      return;
    }
    // 卡片自带操作菜单（明细入口已按用户要求移除）
    openMaterialCardMenu(id, b);
  }));
  // 改类型：同样走明细（不在这里做第二套编辑入口）
  box.querySelectorAll('[data-card-role]').forEach((b) => b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    openMaterialCardMenu(b.dataset.cardRole, b);
  }));
  box.querySelector('[data-card-add]')?.addEventListener('click', () => {
    document.getElementById('btn-add-req')?.click();
  });
}

/** 文件丢失/未解析出正文的标记（比"已解析"更重要：说了完成但素材是空的）。 */
function matLossBadge(materialId) {
  const reason = matIntegrity[String(materialId)];
  if (!reason) return '';
  const text = { missing: '文件已丢失', empty: '文件为空', no_path: '无文件路径',
    no_chunks: '未解析出正文' }[reason] || reason;
  const tip = { missing: '记录还在，但磁盘上的文件不见了 —— 请重新上传',
    empty: '磁盘上的文件是 0 字节 —— 请重新上传',
    no_path: '这条记录没有文件路径（历史数据）',
    no_chunks: '解析标记为完成，却一个正文片段都没有 —— 素材实际是空的，请重新上传或重新解析' }[reason] || '';
  return `<span class="mat-bad" title="${escapeHtml(tip)}">${escapeHtml(text)}</span>`;
}

/** 拉取素材完整性（丢失 / 未解析出正文）。失败只记 console，不影响材料条。 */
async function loadMaterialIntegrity() {
  try {
    const d = await api.projects.materialsIntegrity(projectId);
    matIntegrity = {};
    for (const x of (d.lost || [])) matIntegrity[String(x.id)] = x.reason;
  } catch (e) {
    console.warn('[materials] 完整性检查失败', e);
  }
}

function materialPackagesOf(materialId) {
  const map = (pkgAnalysis && pkgAnalysis.material_packages) || {};
  return map[String(materialId)] || [];
}
function pkgLabelOf(x) {
  const t = x && x.target ? `标的${x.target} ` : '';
  return `${t}标包${(x && x.package_no) || '—'}`;
}
let lastMaterials = [];      // 最近一次拉到的材料列表（摘要与"文档台面"卡片都由它驱动）
let pkgMergeNote = '';      // 模型整理结果说明（只做合并/命名）
let matIntegrity = {};      // {material_id: reason} —— 文件丢失/未解析出正文（加载时拉取）
let pkgAnalysis = null;    // 标包分析结果（GET package-analysis；null = 还没取）
// 类别短名：**模块级** —— 模块级渲染函数看不到 openSplitReport 内部的 CAT_HINT（实测 ReferenceError）
const CAT_NAME = { commerce: '商务', tech: '技术', price: '报价', other: '其他' };
const batchIds = new Set();   // 批量操作选中的章节（跨渲染保留，方便连着挪几格）
const removedByUser = new Set();   // 用户**明确删掉**的章节 id（区别于"本地列表少了"）
// 撤销栈：只存"操作之前的完整结构"快照（最多 20 步）。纯逻辑用模型层的 pushHistory。
let undoStack = [];
// 「已确认」的确认项默认展示**结果**；点「修改」才展开控件（否则确认完信息就消失了 ——
// 用户反馈："确认进入对应目录后需要能够查到确认的结果情况"）
let confirmEditOpen = {};
// 用户口径「**每个标包是单独的内容**」：先选标包、点「确认标包」，五阶段才针对该标包展开。
// 未确认时面板不出阶段内容（避免把上一个标包/默认范围的内容误当成这个标包的）。
let confirmedPkg = null;      // {target, package_no} | null
const pkgKeyOf = (x) => (x ? `${x.target || ''}|${x.package_no || ''}` : '');
function readConfirmedPkg() {
  let raw = params.get('pkg') || '';
  if (!raw) { try { raw = localStorage.getItem(`ge:pkg:${projectId}`) || ''; } catch { /* 隐私模式 */ } }
  if (!raw) return null;
  const [target, package_no] = String(raw).split('|');
  return (target || package_no) ? { target: target || '', package_no: package_no || '' } : null;
}
function rememberConfirmedPkg(x) {
  confirmedPkg = x;
  const raw = pkgKeyOf(x);
  try { localStorage.setItem(`ge:pkg:${projectId}`, raw); } catch { /* 忽略 */ }
  const url = new URL(location.href);
  if (raw) url.searchParams.set('pkg', raw); else url.searchParams.delete('pkg');
  history.replaceState(null, '', url.toString());
}
let panelViewCats = null;     // 筛选的类别（null = 跟随当前目录）
// 面板当前所在阶段（'1' 评分点 / '2' 待确认 / '3' 产物）与其内的子页签
let panelStage = params.get('planning') === '1' ? '2' : '1';
// ④编写 / ⑤评审 面板的数据（**必须声明在模块顶部**：面板可能在初始化早期就渲染，
// 声明放函数区会踩 TDZ —— 本会话已经因此踩过一次坑）。
let writeStats = { written: 0, total: 0 };
let chapterStudio = null;
let reviewHydratedKey = '';
let reviewSummary = {};
let verdictCache = { key: '', data: null };
// 「确认即放行」：在面板里点过「确认并生成目录」后，闸门 1 不再要求用户第二次确认
// ——范围/类别的确认与目录结构的确认是**同一次决策**（审计 P2-2：
// 两套确认语义曾让界面自相矛盾：产物还没确认、而目录已经派生完并在准备）。
// 带时间戳，避免用户中途改主意后这个标记一直挂在那儿。
let autoConfirmOutlineAt = 0;
// 是否正在「梳理应答目录」这一**短任务**阶段（派生目录：秒级~1 分钟）。
// 该阶段不显示「取消」：取消它既保不住什么，也只会让用户以为必须点它才能重跑
// （用户反馈：右上角的"取消"在这个阶段还有什么意义）。
let outlineDrafting = false;
// 是否正在"拆分评分点"（解析 + 派生，整段过程）。此期间右侧**只显示加载态**：
// 不渲染面板内容（否则会出现空标包下拉、"技术规范书要求 0"、"先点左下按钮"这类
// 在解析中毫无意义甚至自相矛盾的文案），也不显示上一版的目录树。
let splitInProgress = false;
let panelSub = '';

/** 保存失败：就地显示（找不到就地容器时，重开面板让顶部 flash 显示）。 */
function inlineFlashFromSave(err) {
  const msg = '保存失败：' + String(err?.message || err).slice(0, 140);
  const slot = document.querySelector('[data-flash-for="topic_placement"]');
  if (slot) { slot.textContent = msg; slot.className = 'sr-dim sr-inline-err'; }
  else { const el = document.getElementById('split-panel-inline'); if (el) el.remove(); openSplitReport(); }
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

/** 文件的标包归属：**多选**弹层（一份文件可属多个标包，也可不指定）。 */
async function openMaterialPackagesPicker(materialId, btn) {
  document.querySelectorAll('.sr-pkg-pop').forEach((x) => x.remove());
  await loadPackageAnalysis().catch(() => {});
  const all = (pkgAnalysis && pkgAnalysis.packages) || [];
  const mine = materialPackagesOf(materialId);
  const key = (x) => `${x.target || ''}|${x.package_no || ''}`;
  const chosen = new Set(mine.map(key));

  const pop = document.createElement('div');
  pop.className = 'sr-pkg-pop';
  // 分析结果为空时也要能操作：把当前已选的列出来（否则用户没法取消）
  const options = all.length ? all : mine;
  pop.innerHTML = options.length ? options.map((p) => `
      <label class="sr-pkg-opt"><input type="checkbox" data-pkg-opt="${escapeHtml(key(p))}"
        ${chosen.has(key(p)) ? 'checked' : ''} /> ${escapeHtml(pkgLabelOf(p))}
        ${p.package_name ? `<span class="sr-dim">${escapeHtml(p.package_name)}</span>` : ''}</label>`).join('')
    : '<div class="sr-dim" style="padding:4px 6px">还没有识别到标包 —— 先上传并等待解析，或先在第①步做标包分析。</div>';
  btn.parentElement.appendChild(pop);

  const save = async () => {
    const picked = [...pop.querySelectorAll('[data-pkg-opt]:checked')].map((cb) => {
      const [target, package_no] = cb.dataset.pkgOpt.split('|');
      return { target, package_no };
    });
    try {
      await api.projects.setMaterialPackages(projectId, materialId, picked);
      // 更新本地缓存，界面立即反映（不必重新拉分析）
      pkgAnalysis = pkgAnalysis || {};
      pkgAnalysis.material_packages = pkgAnalysis.material_packages || {};
      pkgAnalysis.material_packages[String(materialId)] = picked;
      btn.textContent = picked.length ? picked.map((x) => pkgLabelOf(x)).join('、') : '未指定标包';
      btn.classList.toggle('is-none', !picked.length);
      splitPanelFlash = picked.length
        ? `已把该文件归到 ${picked.map((x) => pkgLabelOf(x)).join('、')}`
        : '该文件已设为"未指定标包"';
    } catch (e) {
      console.error('[materials] 标包归属保存失败', e);
      splitPanelFlash = '标包归属保存失败（详见控制台）';
    }
  };
  pop.querySelectorAll('[data-pkg-opt]').forEach((cb) => cb.addEventListener('change', save));
  window.setTimeout(() => {
    const off = (e) => {
      if (!pop.contains(e.target) && e.target !== btn) {
        pop.remove();
        document.removeEventListener('click', off, true);
      }
    };
    document.addEventListener('click', off, true);
  }, 0);
}

/** 取标包分析结果（来自招标文件正文的事实 + 出处）。失败不阻断面板，只标记。 */
async function loadPackageAnalysis({ refresh = false, useLlm = false } = {}) {
  if (pkgAnalysis && !refresh) return pkgAnalysis;
  try {
    const qs = [];
    if (refresh) qs.push('refresh=1');
    if (useLlm) qs.push('use_llm=1');
    const q = qs.length ? `?${qs.join('&')}` : '';
    pkgAnalysis = await api.projects.packageAnalysis(projectId, q);
  } catch (e) {
    console.warn('[packages] 标包分析获取失败', e);
    pkgAnalysis = { packages: [], note: '标包分析获取失败（不影响其它功能）', chunks: 0 };
  }
  return pkgAnalysis;
}

/** 标包卡片：**每张卡都带原文出处**（用户口径：一切以招标文件为准，不衍生）。 */
function renderPackageCards() {
  const d = pkgAnalysis;
  if (!d) return '<div class="sr-dim">正在读取标包分析…</div>';
  const pkgs = d.packages || [];
  if (!pkgs.length) {
    return `<div class="sr-note">${escapeHtml(d.note || '还没有识别到标包。')}
      <button class="sr-mini" data-act="reanalyze-packages" style="margin-left:8px">重新分析</button></div>`;
  }
  const cards = pkgs.map((p) => {
    const label = p.package_name || `${p.target ? `标的${p.target} · ` : ''}标包${p.package_no || '—'}`;
    const conf = { high: '高', medium: '中', low: '低' }[p.confidence] || p.confidence;
    const ev = (p.evidence || [])[0];
    return `<div class="sr-pkg-card">
      <div class="sr-pkg-head"><b>${escapeHtml(label)}</b>
        <span class="ui-pill ${p.confidence === 'low' ? 'is-warn' : 'is-ok'}">置信度 ${escapeHtml(conf)}</span>
        <button class="sr-mini" data-act="pick-package" data-target="${escapeHtml(p.target)}"
          data-pkg="${escapeHtml(p.package_no)}">用它拆分</button></div>
      <div class="sr-dim">来源文件：${escapeHtml((p.source_material_ids || []).length ? `${(p.source_material_ids || []).length} 个` : '未记录')}
        ｜原文提到 ${p.mentions} 次</div>
      ${ev ? `<div class="sr-pkg-ev">「${escapeHtml(String(ev.quote || ''))}」
        <span class="sr-dim">— ${escapeHtml(ev.filename || '')}${ev.page ? ` 第 ${ev.page} 页` : ''}</span></div>` : ''}
    </div>`;
  }).join('');
  return `<div class="sr-pkg-cards">${cards}</div>
    <div class="sr-dim">标包只按招标文件原文列出；名字需要你确认（系统不编造）。
      <button class="sr-mini" data-act="reanalyze-packages" style="margin-left:6px">重新分析</button>
      <button class="sr-mini" data-act="merge-packages" style="margin-left:4px"
        title="让模型把同一标包的多种写法合并、并从原文里取名；编造的内容会被丢弃">用模型整理</button>
      ${pkgMergeNote ? `<span style="margin-left:8px;color:var(--success)">${escapeHtml(pkgMergeNote)}</span>` : ''}</div>`;
}

/** 评分点对齐行：**解析事实 vs 已落库**（差异可见 + 一键对齐，不花模型）。 */
function renderPointsAlignment(alignment) {
  const a = (alignment || null);
  if (!a || a.expected == null) return '';
  const cats = Object.entries(a.by_category || {})
    .map(([c, v]) => `${CAT_NAME[c] || c} ${v.expected}`).join(' · ');
  if (a.aligned) {
    return `<div class="sr-align ok">✅ 评分点已落库 <b>${a.persisted}</b> 条（${escapeHtml(cats)}），与解析事实一致</div>`;
  }
  const miss = (a.missing || []).length;
  return `<div class="sr-align warn">⚠ 解析到 <b>${a.expected}</b> 条（${escapeHtml(cats)}），
    已落库 <b>${a.persisted}</b> 条${miss ? `，缺 ${miss} 条` : ''}
    <button class="sr-mini" data-act="sync-points" style="margin-left:8px">一键对齐（不花模型）</button></div>`;
}

/** ② 目录确认与调整 的说明行（结构完整 = 每个评分点都有承载章节） */
function structureBlockNote() {
  return '<div class="sr-dim" style="margin-top:6px">结构完整 = 每个评分点都有承载章节；'
    + '缺的会在上方自检里以 ⚠ 提示。</div>';
}

/** 面板编辑用的节点表：从**模型**取，不从目录树 DOM 取。
 *
 * 两个原因（实测踩到）：
 *   ① `structureNodes()` 读 `.ot-node` DOM，而目录树不会把 121 个节点全渲染出来 →
 *      用户能看到的章节在 DOM 里找不到，操作静默失效；
 *   ② `applyStructureNodes()` 要求每个节点都能查到 DOM 元素，缺一个就抛错。
 */
function structureNodesFromModel() {
  return [...blocksById.values()]
    .filter((b) => b && b.level)
    .sort((a, b) => (Number(a.order_idx) || 0) - (Number(b.order_idx) || 0))
    // ⚠️⚠️ id 必须是 **block_id（逻辑 id，如 s1.1）**，不是 b.id（数据库行 id）。
    // 目录结构接口按 block_id 匹配已有行；发错 id 会被当成**新节点**：
    // 实测一次 200 的请求报 `created:121, removed:116` —— 结构被重建、原占位被删。
    // 字符串也是必须的（dataset 一定是字符串，数字与字符串比较恒为 false → 点了没反应）。
    .map((b) => ({ id: String(b.block_id || b.id), level: Number(b.level) || 1, title: b.title || '' }));
}

/** 把新结构写回模型（blocksById）—— 面板据此重绘；目录树下次加载时自然同步 */
function applyStructureToModel(nodes) {
  // **删除也要同步**：只更新存在的项会让被删节点留在 blocksById 里，
  // 重绘时又画出来 —— 界面看起来删了没反应（实测）。
  const keep = new Set(nodes.map((n) => String(n.id)));
  for (const key of [...blocksById.keys()]) {
    if (!keep.has(String(key))) blocksById.delete(key);
  }
  nodes.forEach((n, i) => {
    const key = String(n.id);
    const b = blocksById.get(key) || blocksById.get(Number(key));
    if (b) { b.title = n.title; b.level = n.level; b.order_idx = i; }
  });
}

/** 面板内的结构保存：乐观更新模型 → 串行保存（与目录树同一条队列）→ 采纳服务端归一化 → 重绘面板 */
async function saveStructureFromPanel(next, { afterSave = null, label = '', fromHistory = false } = {}) {
  // 传进来的必须是**节点数组**。这里以前是静默 return —— 结果"点了没反应"查了很久。
  // 宁可控制台报错、界面说实话，也不要静默丢弃用户的操作。
  if (!Array.isArray(next) || !next.length) {
    console.error('[panel-structure] 期望节点数组，实际收到：', next);
    structSaveNote = '保存失败：内部数据结构不对（已跳过，详见控制台）';
    await openSplitReport({ stage: '2' });
    return;
  }

  // ⚠️ 关键安全校验：**本地列表比模型少、而且少的不是用户删的** → 本地模型不可信。
  // 这种保存会把"没列出来的章节"当删除，静默削掉目录（实测发生过两次：少 8 行 / 少 33 行，
  // 服务端日志只有一条 200）。宁可拒绝并重载，也不要拿不可信的列表去覆盖服务器。
  const nextIds = new Set(next.map((n) => String(n.id)));
  const lost = [...blocksById.keys()]
    .map(String)
    .filter((id) => !nextIds.has(id) && !removedByUser.has(id));
  if (lost.length) {
    console.error('[panel-structure] 本地目录比服务器少且非用户删除，已拒绝保存：', lost.slice(0, 10));
    structSaveNote = `本地目录与服务器不一致（少 ${lost.length} 节），已重新加载，请重试`;
    await loadExistingBlocks({ silent: true });
    await openSplitReport({ stage: '2' });
    return;
  }
  // 撤销用的快照：这里记的是"**操作之前**"的完整结构（撤销就回到它）。
  // 撤销/重做自身触发的保存不再入栈，否则栈会被自己的回退操作淹没。
  if (!fromHistory) {
    const before = structureNodesFromModel();
    if (before.length) undoStack = pushHistory(undoStack, { nodes: before, label: label || '编辑' });
  }
  applyStructureToModel(next);
  let res = null;
  try {
    // 把"用户主动删掉的 id"一起报给服务端 —— 服务端据此判断提交是否可信
    // （少了但没声明的章节会被拒绝，见 runner.update_outline_structure）。
    res = await queueStructureSave(next, false, [...removedByUser]);
  } catch (e) {
    console.error('[panel-structure] 保存失败', e);
    await loadExistingBlocks({ silent: true });
    await openSplitReport({ stage: '2' });
    return;
  }
  // 保存结果交给用户看：**增删数量必须可见** —— 曾经"200 + 静默统计"掩盖过一次
  // 目录被整体重建（created:121 / removed:116）。
  if (res && (res.created != null || res.updated != null)) {
    const bits = [];
    if (res.updated) bits.push(`改名/调整 ${res.updated}`);
    if (res.created) bits.push(`新增 ${res.created}`);
    if (res.removed) bits.push(`删除 ${res.removed}`);
    structSaveNote = (label ? `${label} · ` : '')
      + (bits.length ? `已保存：${bits.join(' · ')}` : '已保存');
  }
  if (Array.isArray(res?.nodes) && res.nodes.length) {
    applyStructureToModel(res.nodes.map((n, i) => ({
      id: n.block_id, level: n.level, title: n.title, order_idx: i,
    })));
  }
  if (afterSave) await afterSave();
  await openSplitReport({ stage: '2' });
}

/** 面板内改名：把标题换成输入框（不依赖下方目录树的 DOM） */
function renameStructInPanel(blockId) {
  const row = document.querySelector(`.sr-erow[data-struct="${CSS.escape(blockId)}"]`);
  const titleEl = row?.querySelector('.sr-erow-title');
  if (!row || !titleEl) return;
  const original = titleEl.textContent || '';
  const input = document.createElement('input');
  input.className = 'sr-erow-input';
  input.value = original;
  titleEl.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const value = (input.value || '').trim();
    if (!save || !value || value === original) {
      await openSplitReport({ stage: '2' });
      return;
    }
    const nodes = structureNodesFromModel().map((n) => (n.id === blockId ? { ...n, title: value } : n));
    await saveStructureFromPanel(nodes);
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

/** 拖动排序（上下拖动）：按住行首 ⠿ 拖动，松手即保存。
 *
 * 规则在模型层（moveOutlineNodeTo / moveOutlineNodesTo，有 node --test）：
 * 连带子树、落点层级=插入点后一个节点的层级、不能拖进自己的子树、原位不动不写库。
 * 勾选多行时拖任意一行 = **整组一起拖**（与批量按钮共用同一份选择）。
 */
function startRowDrag(ev) {
  const grip = ev.currentTarget;
  const id = grip.dataset.grip;
  const list = document.querySelector('.sr-struct-edit');
  if (!id || !list) return;
  const rows = [...list.querySelectorAll('.sr-erow')];
  if (rows.length < 2) return;
  const ids = (batchIds.has(id) && batchIds.size) ? [...batchIds].filter(
    (x) => rows.some((r) => r.dataset.struct === x)) : [id];
  const selected = new Set(ids);

  const line = document.createElement('div');
  line.className = 'sr-drop-line';
  list.appendChild(line);
  const tagEl = document.createElement('span');
  tagEl.className = 'sr-drop-tag';
  list.appendChild(tagEl);
  rows.forEach((r) => { if (selected.has(r.dataset.struct)) r.classList.add('is-drag-src'); });
  list.classList.add('is-dragging');

  const INDENT = 16;          // 每级缩进（与结构行的 padding-left 对齐）
  let at = null;
  let level = null;           // 当前投影层级（Notion 式：由水平位置决定）
  const computeAt = (clientY) => {
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i].getBoundingClientRect();
      if (clientY < r.top + r.height / 2) return i;
    }
    return rows.length;
  };
  // 水平位置 → 层级：以后一个节点的层级为基准，每右移一格深一级；
  // 最深只能到"前一个节点的层级 + 1"（否则会跨级），插到最顶只能是 1 级。
  const computeLevel = (clientX, idx) => {
    const nodes = structureNodesFromModel();
    const { max, natural } = levelRangeAt(nodes, Math.min(idx, nodes.length));
    const baseX = list.getBoundingClientRect().left + 10;
    const want = natural + Math.round((clientX - baseX - (natural - 1) * INDENT) / INDENT);
    return { level: Math.max(1, Math.min(max, want)), natural };
  };
  const showLine = (i, lvl, natural, clientX) => {
    const r = rows[i] || rows[rows.length - 1];
    if (!r) return;
    const top = i < rows.length ? r.offsetTop : r.offsetTop + r.offsetHeight;
    line.style.top = `${top}px`;
    line.style.marginLeft = `${(lvl - 1) * INDENT}px`;   // 指示线跟着层级缩进        // 指示线按层级缩进
    const rel = lvl - (natural || lvl);
    tagEl.textContent = rel > 0 ? `成为子级（第 ${lvl} 级）`
      : rel < 0 ? `上级（第 ${lvl} 级）` : `同级（第 ${lvl} 级）`;
    // 提示跟随指针（放在指示线右侧太靠右会被容器裁掉 —— 实测看不到）
    const listLeft = list.getBoundingClientRect().left;
    tagEl.style.top = `${top - 22}px`;
    tagEl.style.left = `${Math.max(4, (clientX || listLeft + 40) - listLeft + 10)}px`;
  };
  const onMove = (e) => {
    at = computeAt(e.clientY);
    const lv = computeLevel(e.clientX, at);
    level = lv.level;
    showLine(at, lv.level, lv.natural, e.clientX);
    e.preventDefault();
  };
  const cleanup = () => {
    line.remove();
    tagEl.remove();
    rows.forEach((r) => r.classList.remove('is-drag-src'));
    list.classList.remove('is-dragging');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape') { at = null; cleanup(); } };
  const onUp = async () => {
    const target = at;
    cleanup();
    if (target == null) return;
    const nodes = structureNodesFromModel();
    const lvl = level == null ? null : level;
    const next = ids.length > 1
      ? (lvl == null ? moveOutlineNodesTo(nodes, ids, target)
                     : moveOutlineNodesToLevel(nodes, ids, target, lvl))
      : (lvl == null ? moveOutlineNodeTo(nodes, ids[0], target)
                     : moveOutlineNodeToLevel(nodes, ids[0], target, lvl));
    if (!next) return;                       // 原位 / 拖进自己的子树 → 不写库
    await saveStructureFromPanel(next, { label: `拖动 ${ids.length} 项（第 ${lvl || '?'} 级）` });
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('keydown', onKey);
  ev.preventDefault();
}

/** 批量操作（目录调整）：整批上移/下移/缩进/减少缩进。
 *
 * 语义在模型层（workspace-model.mjs，有 node --test）：整批按"头"决定一起升降，
 * 动不了的项计入 skipped —— 界面必须如实说出跳过了几项。
 */
async function handleBatchStruct(action) {
  if (action === 'delete') {
    const nodes = structureNodesFromModel();
    const ids = [...batchIds].filter((id) => nodes.some((n) => n.id === id));
    if (!ids.length) { batchIds.clear(); await openSplitReport({ stage: '2' }); return; }
    // 有正文的章节单独说清：删的是**目录节点**，正文仍在库里（与单节删除同一语义）
    const written = ids.filter((id) => String((nodes.find((n) => n.id === id) || {}).content || '').length).length;
    const ok = await uiConfirm(
      `删除选中的 ${ids.length} 章？其子树会一并删除；${written ? `其中 ${written} 章已有正文，正文保留在库里` : '已写正文保留在库里'}。`,
      { title: '批量删除', confirmText: '删除', danger: true },
    );
    if (!ok) return;
    const { nodes: next, removed } = removeManyOutlineSubtrees(nodes, ids);
    if (!removed) return;
    nodes.filter((n) => !next.some((m) => m.id === n.id)).forEach((n) => removedByUser.add(String(n.id)));
    batchIds.clear();
    selectedStructId = null;
    await saveStructureFromPanel(next, { label: `批量删除 ${removed} 节` });
    return;
  }
  if (action === 'clear') {
    batchIds.clear();
    await openSplitReport({ stage: '2' });
    return;
  }
  if (!batchIds.size) return;
  const nodes = structureNodesFromModel();
  const ids = [...batchIds].filter((id) => nodes.some((n) => n.id === id));   // 去掉已不存在的
  if (!ids.length) { batchIds.clear(); await openSplitReport({ stage: '2' }); return; }

  let r = null;
  let label = '';
  if (action === 'up') { r = moveManyOutlineNodes(nodes, ids, -1); label = `批量上移 ${ids.length} 项`; }
  else if (action === 'down') { r = moveManyOutlineNodes(nodes, ids, +1); label = `批量下移 ${ids.length} 项`; }
  else if (action === 'indent') { r = indentManyOutlineNodes(nodes, ids); label = `批量缩进 ${ids.length} 项`; }
  else if (action === 'outdent') { r = outdentManyOutlineNodes(nodes, ids); label = `减少缩进 ${ids.length} 项`; }
  if (!r) return;

  if (!r.moved) {
    // 一项都没动：如实说原因，别让用户以为"点了没反应"
    structSaveNote = `${label}：都动不了（已在边界 / 这批的头没有同级前驱）`;
    await openSplitReport({ stage: '2' });
    return;
  }
  await saveStructureFromPanel(r.nodes, {
    label: r.skipped ? `${label}（${r.skipped} 项动不了）` : label,
  });
}

/** 归属选择**即时保存**：复用既有「保存归属」按钮的 click 路径（收集 →
 * 校验 → 落库 → 提示都在那一处），这里只加 500ms 防抖 —— 连点几个主题时
 * 合并成一次请求，也避免提示刷屏。
 */
let topicSaveTimer = null;
function saveTopicPlacementSoon() {
  window.clearTimeout(topicSaveTimer);
  topicSaveTimer = window.setTimeout(() => {
    const btn = document.querySelector('#split-panel-inline [data-save="topic_placement"]');
    if (btn && !btn.disabled) btn.click();
  }, 500);
}

/** 归属章节选择器：点开 = **完整目录列表**（按层级缩进 + 层级徽标）+ 搜索，点一章即选中。
 *
 * 为什么不用 <select>：用户要求"展示所有的目录列表，然后再去选择对应的哪一级目录"。
 * 下拉里 121 章很难看出层级；铺开的列表能一眼看出谁在哪一级。
 * 值仍写进同行的隐藏 input[data-topic]，既有的「保存归属」逻辑不变。
 */
function openTopicPicker(topic, btn) {
  // 只允许开一个
  document.querySelectorAll('.sr-pick-pop').forEach((p) => p.remove());
  const row = btn.closest('[data-topic-row]');
  if (!row) return;
  const input = row.querySelector('input[data-topic]');
  const nodes = structureNodesFromModel();
  const cur = input?.value || '';

  const pop = document.createElement('div');
  pop.className = 'sr-pick-pop';
  pop.innerHTML = `
    <input class="sr-pick-search" type="search" placeholder="搜索章节…（共 ${nodes.length} 章）" />
    <div class="sr-pick-list"></div>`;
  row.querySelector('.sr-mount-cell')?.appendChild(pop);
  const list = pop.querySelector('.sr-pick-list');
  const search = pop.querySelector('.sr-pick-search');

  const render = (kw = '') => {
    const k = kw.trim().toLowerCase();
    const items = nodes.filter((n) => !k || String(n.title).toLowerCase().includes(k));
    list.innerHTML = (k ? '' : `<button type="button" class="sr-pick-item${cur ? '' : ' on'}"
        data-pick=""><span class="sr-pick-t">默认：技术规范书要求应答</span></button>`)
      + (items.length ? items.map((n) => `
        <button type="button" class="sr-pick-item${String(n.title) === cur ? ' on' : ''}"
          data-pick="${escapeHtml(String(n.title))}">
          <span class="sr-pick-lv">L${n.level}</span>
          <span class="sr-pick-t" style="padding-left:${(n.level - 1) * 14}px">${escapeHtml(String(n.title))}</span>
        </button>`).join('')
        : '<div class="sr-dim" style="padding:6px 10px">没有匹配的章节</div>');
  };
  render();
  pop.querySelectorAll('[data-pick]').forEach((b) => b.addEventListener('click', () => {
    const v = b.dataset.pick || '';
    if (input) input.value = v;
    if (btn) btn.textContent = v || '默认：技术规范书要求应答';
    const pill = row.querySelector('.ui-pill');
    if (pill) { pill.textContent = v ? '已指定' : '默认'; pill.className = `ui-pill ${v ? 'is-ok' : 'is-none'}`; }
    pop.remove();
    // **即时保存**：不再要求用户手动点「保存归属」
    saveTopicPlacementSoon();
  }));
  search?.addEventListener('input', () => render(search.value));
  search?.focus();
  // 点别处关闭
  window.setTimeout(() => {
    const off = (e) => {
      if (!pop.contains(e.target) && e.target !== btn) {
        pop.remove();
        document.removeEventListener('click', off, true);
      }
    };
    document.addEventListener('click', off, true);
  }, 0);
}

/** 撤销上一步目录操作：把"操作之前的完整结构"再存一次。
 *
 * 只保留"操作前快照"，不自己维护重做栈 —— 用户要的是"能退回去"，
 * 而每次保存都会入栈，所以连点撤销就是逐步回退。
 */
async function handleUndoStructure() {
  if (!undoStack.length) {
    structSaveNote = '没有可撤销的操作';
    await openSplitReport({ stage: '2' });
    return;
  }
  const entry = undoStack[undoStack.length - 1];
  undoStack = undoStack.slice(0, -1);
  // 撤销"删除"时，被恢复的章节要从 removedByUser 里移出 ——
  // 否则它又被当成"用户同意它消失"，后续的陈旧保存仍可能把它删掉。
  entry.nodes.forEach((n) => removedByUser.delete(String(n.id)));
  const nowIds = new Set(structureNodesFromModel().map((n) => String(n.id)));
  entry.nodes.forEach((n) => { if (!nowIds.has(String(n.id))) removedByUser.add(String(n.id)); });
  await saveStructureFromPanel(entry.nodes, {
    label: `撤销「${entry.label}」`, fromHistory: true,
  });
}

/** 面板内的结构操作（改名 / 上下移 / 缩进 / 删除 / 新增） */
async function handleStructAct(blockId, op) {
  const nodes = structureNodesFromModel();
  const current = nodes.find((n) => n.id === blockId);
  if (!current) return;
  if (op === 'rename') { renameStructInPanel(blockId); return; }
  if (op === 'delete') {
    const ok = await uiConfirm(`删除章节「${current.title}」？其子树会一并删除（已写正文保留在库里）。`);
    if (!ok) return;
    // ⚠️ removeOutlineSubtree 返回的是 **{nodes, removed}**，不是数组 ——
    // 当成数组用会让 saveStructureFromPanel 的 Array.isArray 判 false 后静默返回，
    // 界面上就是"点了确认但什么都没发生"（实测踩到）。
    const { nodes: next, removed } = removeOutlineSubtree(nodes, blockId);
    if (!removed) return;
    nodes.filter((n) => !next.some((m) => m.id === n.id)).forEach((n) => removedByUser.add(String(n.id)));
    selectedStructId = null;
    await saveStructureFromPanel(next, { label: `删除 ${removed} 节` });
    return;
  }
  let next = null;
  if (op === 'up') next = moveOutlineNode(nodes, blockId, -1);
  else if (op === 'down') next = moveOutlineNode(nodes, blockId, +1);
  else if (op === 'indent') next = indentOutlineNode(nodes, blockId);
  else if (op === 'outdent') next = outdentOutlineNode(nodes, blockId);
  if (!next) return;
  const NAME = { up: '上移', down: '下移', indent: '缩进', outdent: '减少缩进' };
  await saveStructureFromPanel(next, { label: NAME[op] || '' });
}

/** 新增章节（同级 / 子级）：保存后直接进入改名，接着就能写标题 */
async function handleStructNew(op) {
  const nodes = structureNodesFromModel();
  const baseId = selectedStructId || (nodes[nodes.length - 1] || {}).id;
  const base = nodes.find((n) => n.id === baseId);
  if (!base) return;
  const id = newOutlineId(nodes.map((n) => n.id));
  const fresh = { id, level: base.level, title: '新章节' };
  const next = op === 'child' ? insertChildOf(nodes, baseId, fresh) : insertSiblingAfter(nodes, baseId, fresh);
  if (!next) return;
  selectedStructId = id;
  await saveStructureFromPanel(next, { afterSave: async () => { renameStructInPanel(id); } });
}

/** 收起拆分清单：拆下面板、恢复"待确认"横幅、露出目录。 */

// 注意：参数**不能叫 stage** —— 函数体内已有 `const stage = pointsConfirmed ? …`，
// 同名参数会直接 SyntaxError（"Identifier 'stage' has already been declared"），
// 整页脚本不执行（由启动自检面板捕获）。

// ── ④编写 / ⑤评审：与①②③ 同一套「左导航 + 右内容」的布局约定 ──
async function loadVerdict() {
  const key = `${projectId}|${currentThreadId || ''}`;
  if (verdictCache.key === key && verdictCache.data) return verdictCache.data;
  try {
    const q = currentThreadId ? `?threadId=${encodeURIComponent(currentThreadId)}` : '';
    const r = await fetch(`/api/projects/${projectId}/scoring-verdict${q}`);
    verdictCache = { key, data: r.ok ? await r.json() : null };
  } catch (e) {
    console.warn('[panel] 评分点结论取不到', e);
    verdictCache = { key, data: null };
  }
  return verdictCache.data;
}


/** 面板头的运行进度（替代已去掉的状态栏文案）：只在有进度时显示一句话。 */

/** 把状态动作区（#status-actions）挂到面板头里 —— 让"动作"只有一处，
 *  不再单独占一条状态栏。面板不存在时留在原处兜底（例如纯解析期）。 */
function refreshPanelRunState() {
  const el = document.getElementById('sr-run-state');
  if (!el) return;
  const st = workspace?.state?.stage || '';
  const total = Number(totalBlocks) || 0;
  const done = Number(doneBlocks) || 0;
  let text = '';
  if (st === 'failed') text = '上次运行失败 · 原因见「运行详情」';
  else if (st === 'parsing' && workspace?.state?.config?.response_type) text = '正在整合评分依据，生成应答目录草案…';
  else if (splitInProgress || st === 'parsing') text = '正在解析要求文件…';
  else if (outlineDrafting) text = '正在梳理应答目录…';
  else if (st === 'extracting' && total) text = `已准备 ${done}/${total} 章节`;
  else if (st === 'matching') text = '正在匹配素材…';
  else if (st === 'generating' && total) text = `正在生成正文 ${done}/${total} 章节`;
  else if (st === 'reviewing') text = '正在评审…';
  else if (writeStats.total) text = `草稿 ${writeStats.written}/${writeStats.total} 个编写单元`;
  // 停在半途（准备了一部分、没在跑）：状态栏模块已去掉，这里给一句进度，
  // 否则"还剩多少没准备"在界面上完全看不到。
  else if (total && done > 0 && done < total) text = `已准备 ${done}/${total} 章节（未跑完）`;
  el.textContent = text;
}

function hydrateStage(st) {
  if (st === '4') renderWritePane().then(refreshStageHint);
  else if (st === '5') renderReviewPane().then(refreshStageHint);
}

/** 阶段头提示依赖异步数据（写了多少章 / 得分预估），拉到数据后要回填 */
function refreshStageHint() {
  const map = { '4': stageHintText('4'), '5': stageHintText('5') };
  document.querySelectorAll('#split-panel-inline .sr-stage').forEach((st) => {
    const txt = map[st.dataset.stage];
    if (txt == null) return;
    const el = st.querySelector('.sr-stage-head .hint');
    if (el) el.textContent = txt;
  });
}

function stageHintText(st) {
  if (st === '4') {
    // 原型：阶段头回答"进行到哪"，不用口号（2026-10-16）。
    // 注意：本函数是模块级，只能引用模块级状态（writeStats），不能引用渲染作用域的 structBlocks
    // —— 引用后者会抛 ReferenceError，导致整个编写工作台加载失败（真实踩坑）。
    if (!writeStats.total) return '等目录生成后再撰写';
    const done = writeStats.complete || 0;
    return `${writeStats.total} 个编写单元 · 草稿 ${writeStats.written || 0}${done ? ` · 完成 ${done}` : ''}`;
  }
  if (st === '5') {
    return reviewSummary.points_total
      ? `得分预估 ${reviewSummary.score_estimated ?? 0} / ${reviewSummary.score_max ?? 0} · 缺口 ${reviewSummary.gaps ?? 0} 项`
      : '等生成正文后按评分点评审';
  }
  return '';
}

async function renderWritePane() {
  const body = document.getElementById('sr-write-body');
  if (!body) return;
  try {
    const tid = await resolvePanelThreadId();
    chapterStudio = await mountChapterStudio(body, {projectId, threadId:tid, getModel:()=>selectedModel,
      onStats: stats => { writeStats = stats; refreshStageHint(); }});
  } catch(error) {
    body.innerHTML = `<div class="sr-flash err">编制工作台加载失败：${escapeHtml(error.message)}</div>`;
  }
}

async function renderReviewPane() {
  const body = document.getElementById('sr-review-body');
  if (!body) return;
  const key = `${projectId}|${currentThreadId || ''}`;
  if (reviewHydratedKey === key && body.dataset.ready) return;
  body.innerHTML = '<div class="sr-note">正在加载评审结论…</div>';
  const data = await loadVerdict();
  if (!data) {
    body.innerHTML = '<div class="sr-flash err">评审结论暂时取不到（接口未返回）。</div>';
    return;
  }
  const s = data.summary || {};
  reviewSummary = s;
  reviewHydratedKey = key;
  body.dataset.ready = '1';
  const VERDICT = { ok: ['满足', 'ok'], medium: ['部分满足', 'part'],
                    high: ['不满足', 'bad'], unreviewed: ['未评审', 'none'] };
  const pct = s.score_max ? Math.round((s.score_estimated || 0) / s.score_max * 100) : 0;
  body.innerHTML = `
    <div class="sr-kpis">
      <div class="sr-kpi ok"><span>得分预估</span><strong>${s.score_estimated ?? 0} / ${s.score_max ?? 0}</strong>
        <em>${pct}% 满分${s.score_pending ? ` · 另有 ${s.score_pending} 分待评` : ''}</em></div>
      <div class="sr-kpi warn"><span>缺口</span><strong>${s.gaps ?? 0} 项</strong>
        <em>未满足的得分要点</em></div>
      <div class="sr-kpi"><span>不满足 / 部分满足</span><strong>${s.high ?? 0} / ${s.medium ?? 0}</strong>
        <em>满足 ${s.ok ?? 0} · 未评审 ${s.unreviewed ?? 0}</em></div>
      <div class="sr-kpi"><span>需举证章节</span><strong>${s.evidence_chapters ?? 0} 章</strong>
        <em>有举证要求的章节</em></div>
      <div class="sr-kpi"><span>成本</span><strong>${Math.round((s.cost_tokens || 0) / 10000)} 万 token</strong>
        <em>本标包累计</em></div>
    </div>
    <div class="sr-vlist">${(data.points || []).map((p) => {
      const [lab, cls] = VERDICT[p.verdict] || ['—', 'none'];
      return `<details class="sr-vrow"><summary>
        <span class="sr-vpoint">${escapeHtml(p.item || p.factor || p.point_id)}<em>${escapeHtml(p.factor || '')}</em></span>
        <span class="sr-vnum">${p.points} 分</span>
        <span class="sr-vnum">${p.chapters_total} 章</span>
        <span class="sr-vpill ${cls}">${lab}</span>
        <span class="sr-vmiss">${(p.missing_points || []).slice(0, 2).map(escapeHtml).join('；') || '—'}</span>
        <span class="sr-vpill ${p.evidence_chapters ? 'part' : 'none'}">${
          p.evidence_chapters ? `需举证 ${p.evidence_chapters} 章` : '无需举证'}</span>
        <span class="sr-vest">${p.estimated_score == null ? '待评' : `${p.estimated_score} 分`}</span>
      </summary>
      <table class="sr-table"><thead><tr><th>章节</th><th>评审</th><th>缺失的得分要点</th></tr></thead>
        <tbody>${(p.chapters || []).map((c) => {
          const [l2, c2] = VERDICT[c.risk] || ['—', 'none'];
          return `<tr><td>${escapeHtml(c.title)}</td><td><span class="sr-vpill ${c2}">${l2}</span></td>
            <td>${(c.missing_points || []).map(escapeHtml).join('；') || '—'}</td></tr>`; }).join('')
          || '<tr><td colspan="3" class="sr-dim">该评分点暂无承载章节</td></tr>'}</tbody></table>
      </details>`; }).join('') || '<div class="sr-note">暂无评分点。</div>'}</div>
    <div class="sr-note" style="padding:8px 15px">
      这里是按评分点把关；章节级的正文与评审意见仍在
      <a href="/review?projectId=${projectId}&packageId=${encodeURIComponent(packageId)}${currentThreadId ? `&threadId=${encodeURIComponent(currentThreadId)}` : ''}"
         style="color:var(--accent)">质量检查页</a>。</div>`;
}

/** 面板存在时，主区下方的旧目录树/空态一律收起：目录由面板「产物」承载，
 *  两处各画一份是重复（用户反馈"下方为什么还展示目录"）。 */
function hideLaneTreeWhenPanelShows() {
  const list = document.getElementById('blocks-list');
  if (list) { list.hidden = true; list.style.display = 'none'; }
  const empty = document.getElementById('blocks-empty');
  if (empty) { empty.hidden = true; empty.innerHTML = ''; }
}

// ── 篇幅（2026-10-16）：总页数一次填，按分值×类型系数分配到各章 ──
// 数据落 blocks.page_budget + split_answers['page_budget']，接口见 routes/workflow.py。
let responseDraftsData = null;   // 该标包各应答类型的目录草案（后端解析：(标包,类型) → 线程）
let pageBudgetData = null;
let pageBudgetThreadId = '';
// 步骤（2026-10-16）：应答策划分两步 —— ① 确认目录 ② 分配篇幅；**两步操作同一个目录**。
// null 表示按状态自动落位（目录已确认 → 第2步）。
let planStep = null;

function pageBudgetKeyOf(b) { return String(b?.block_id || b?.id || ''); }

function pageBudgetChapterOf(blockId) {
  const list = pageBudgetData?.chapters || [];
  return list.find(c => String(c.block_id) === String(blockId)) || null;
}

/** 目录行右侧的页数单元：可分配的章节给输入框，其余显示占位。 */
function pageBudgetCellHtml(b) {
  if (!pageBudgetData || !pageBudgetData.directory_confirmed) return '';
  const key = pageBudgetKeyOf(b);
  const chapter = pageBudgetChapterOf(key);
  if (!chapter) return '';
  // 只有"分配落点"可编辑；父章节只读显示子树合计（额度不与子章节重复计算）
  if (!chapter.is_target) {
    if (Number(chapter.pages) > 0) {
      return `<span class="sr-erow-page is-sum" title="父章节页数 = 子树合计">子树合计 ${Number(chapter.pages)} 页</span>`;
    }
    return '<span class="sr-erow-page is-muted" title="更深的叶子计入上级章节">—</span>';
  }
  return `<span class="sr-erow-page"><input type="number" class="sr-page-input" min="0" step="1"
    data-page="${escapeHtml(key)}" value="${Number(chapter.pages) || 0}"
    title="本章页数；改过之后固定，不再被自动分配覆盖" aria-label="本章页数" /> 页${
    chapter.pinned ? '<em class="sr-page-pinned">已调整</em>' : ''}</span>`;
}

/** 篇幅控制条：总页数 + 两个动作 + 合计校验 + 确认篇幅。 */
/** 该类型已生成的目录草案（后端解析）。没有则返回 null → 前端知道该"生成"。 */
function draftOfType(type) {
  const list = (responseDraftsData && responseDraftsData.drafts) || [];
  return list.find((d) => d && d.type === type) || null;
}

/** 只在"面板/目录区"显示一行加载态：整页与上方类型卡都不动。
 *
 * 用户反馈（2026-10-17）：点「商务」/「技术」互切时整页重新加载、抖动明显，
 * 希望只让下面目录那一侧显示加载。原实现用 location.assign → 整页导航。
 */
function showPaneBusy(text) {
  const pane = document.querySelector('.sr-stage[data-stage="2"] .sr-stage-body');
  if (!pane) return;
  let tip = pane.querySelector('[data-pane-busy]');
  if (!tip) {
    tip = document.createElement('div');
    tip.setAttribute('data-pane-busy', '1');
    tip.className = 'sr-empty';
    pane.appendChild(tip);          // 追加在类型卡/说明之后 = 目录区
  }
  tip.textContent = text;
}

function clearPaneBusy() {
  document.querySelectorAll('[data-pane-busy]').forEach((n) => n.remove());
}

/** **原地**切换到另一条 run（目录草案）：改线程 + URL + 事件流，然后只重画面板。
 *
 * 与"切换标包"同一套做法（那条路径本来就是原地的），不再整页 location.assign。
 * 注意：本函数在模块作用域，只能引用模块级变量（projectId / params / currentThreadId）。
 */
async function switchPanelThread(newTid, busyText) {
  if (!newTid) return;
  currentThreadId = newTid;
  try { setThread(projectId, newTid); } catch (err) { /* sessionStorage 不可用不影响 */ }
  const url = new URL(location.href);
  url.searchParams.set('threadId', newTid);
  url.searchParams.set('planning', '1');
  history.replaceState(null, '', url.toString());
  // **必须同步模块级 params**：resolvePanelThreadId 优先读 params.get('threadId')，
  // 只改地址栏的话面板仍按旧线程取数（实测：切到商务后目录 0 行、卡片状态不更新）。
  try { params.set('threadId', newTid); params.set('planning', '1'); } catch (err) { /* 只读场景忽略 */ }
  if (typeof attachStream === 'function') { try { attachStream(newTid); } catch (err) { /* 流可选 */ } }
  showPaneBusy(busyText || '正在切换应答目录…');
  try {
    await openSplitReport({ stage: '2' });
  } finally {
    clearPaneBusy();
  }
}

function resolvedStep(directoryConfirmed) {
  if (planStep === '2' && !directoryConfirmed) return '1';   // 第2步以第1步完成为前提
  return planStep || (directoryConfirmed ? '2' : '1');
}

/** 应答策划的**当前步操作条** —— 只是一条，随步骤切换。
 *
 * 为什么合成一条（2026-10-16 用户 PM 审查）：
 *   「① 确认目录」与「② 确认篇幅」是一条**连环状态**，两步操作的是**下面同一个目录结构**。
 *   原来我做成两条并列的条（第 1 步条 + 篇幅条），加上头里的目录状态，看起来是三块东西，
 *   步骤关系要靠用户自己拼。现在：头里放连环步骤链，中间只有**一条**当前步操作条，
 *   下面是被两步共用的目录结构（行内操作随步骤切换：①结构动作 / ②页数输入）。
 */
/** 当前步操作条。**必须把渲染作用域的标志位当参数传进来** ——
 * 该函数定义在模块作用域，直接引用 categoriesConfirmed 会 ReferenceError，
 * 导致整个策划面板渲染失败（实测：工作台整片空白）。这个坑我已犯过三次。
 */
function planStepBarHtml(dirConfirmed) {
  if (resolvedStep(!!dirConfirmed) === '1') {
    return `<div class="response-confirm-bar" data-plan-step-bar data-step-pane="1">
        <div><strong>确认目录</strong>
          <span>检查结构：改名 / 增删章节 / 调整层级 / 核对覆盖；原文点对点章节固定不动。确认后进入第 2 步「确认篇幅」。</span></div>
        <span data-step-slot="1"></span>
      </div>`;
  }
  return pageBudgetBarHtml();     // 第 2 步：篇幅（按已确认 / 待确认分两态）
}


function pageBudgetBarHtml() {
  if (!pageBudgetData) return '';
  const d = pageBudgetData;
  const total = Number(d.total) || 0;
  const allocated = Number(d.allocated) || 0;
  const chapters = Number(d.chapter_count) || 0;
  const hasPinned = Array.isArray(d.chapters) && d.chapters.some((c) => c && c.pinned);
  const unallocated = total > 0 && allocated === 0;      // 实测脏数据：确认了却没有分配
  const ok = !!d.ok && !unallocated;

  // ── 已确认态 ── 与「目录」那一行**同形**（.sr-dir-head + .ui-pill），一行收敛。
  // 用户反馈：确认完篇幅后这里还是大卡片（74px、带说明段），与目录确认后的一行 pill（50px）
  // 不一致，显得又高又重。「进入编写」不在这里给它唯一的归属（收尾栏），避免两个入口打架。
  if (d.confirmed) {
    const mismatched = allocated !== total;
    return `<div class="sr-dir-head" data-plan-step-bar data-page-budget-bar data-step-pane="2">
        <h5 class="sr-sec" style="margin:0">篇幅</h5>
        <span class="ui-pill is-ok">已确认 · ${total} 页 · 分配到 ${chapters} 个章节</span>
        <span class="sr-dim">${mismatched ? `已分配 ${allocated} 页` : '合计与需求一致'}</span>
        <span style="flex:1"></span>
        <button class="mw-btn" data-act="page-edit">修改篇幅</button>
      </div>`;
  }

  // ── 待确认态 ── 编辑区压成一行（padding 用 is-compact 收窄）
  const totalMissing = total <= 0;        // 还没填总页数（0 页不能算"与需求一致"）
  const check = totalMissing ? '请先填总页数，再按篇幅分配'
    : (unallocated ? '⚠ 还没分配，请先点「按篇幅分配」'
      : (ok ? `合计 ${allocated} 页 ✓ 一致`
            : `⚠ 差 ${d.diff > 0 ? '+' : ''}${d.diff} 页`));
  return `<div class="response-confirm-bar is-compact" data-plan-step-bar data-page-budget-bar data-step-pane="2">
      <div><strong>确认篇幅</strong>
        <span>填总页数，按各章分值自动分配；父章节显示子树合计，可逐章调整。</span></div>
      <span class="sr-budget-ctl">
        <label class="sr-budget-total">总页数 <input type="number" id="sr-page-total" min="0" step="1" value="${total}" aria-label="总页数" /> 页</label>
        <button class="mw-btn" data-act="page-alloc">按篇幅分配</button>
        <span id="sr-page-check" class="${ok ? '' : 'sr-page-warn'}">${check}</span>
        ${hasPinned ? '<button class="mw-btn" data-act="page-clear" title="把手工改过的页数恢复成自动分配">清除手工调整</button>' : ''}
        <button class="mw-btn primary" data-act="page-confirm">${totalMissing || !ok ? '按篇幅分配并确认' : '确认篇幅'}</button>
        <span class="sr-dim" data-page-feedback role="status" aria-live="polite"></span>
      </span>
    </div>`;
}


/** 「应答目录未派生」时：让位 + 给出可点的出口。
 *
 * 为什么不能写在面板渲染里：应用自己的警示（#raw-toc-warn）是**异步**插入的，
 * 渲染那一刻它还不存在 —— 实测因此永远不触发（用户看到的就是"空面板 + 指不到的提示"）。
 * 因此这里常驻监听，等警示出现再补，且幂等。
 */
async function ensureRawTocExit() {
  const pane = document.querySelector('.sr-stage.on[data-stage="2"] .sr-stage-body')
    || document.querySelector('.sr-stage[data-stage="2"] .sr-stage-body');
  if (!pane) return;
  // 应用自己插了警示就直接用；没插（实测有的入口不插）就用**同一个后端预检**判断，
  // 而不是靠 DOM 标记 —— 这是上一轮没修好的原因。
  let need = !!document.getElementById('raw-toc-warn');
  if (!need) {
    try {
      const tid = new URLSearchParams(location.search).get('threadId');
      if (!tid) return;
      const chk = await api.workflow.redraftCheck(tid);
      // 只在"确实没派生、且不是正在派生"时才给出口。
      // 实测教训（project 29 商务草案）：草案正在派生（run_in_progress / outline_pending）时，
      // 这个出口也会冒出来，还写着"目录不能再改"并给出破坏性的「重新拆分评分点」——
      // 用户点了它会新建运行，而新运行的 parse **按项目清空 blocks**，把技术草案的目录一起清掉。
      const busy = !!chk && (chk.reasons || []).indexOf('run_in_progress') >= 0;
      if (busy) {
        // 派生进行中：只显示进度，不给任何破坏性动作
        const empty = pane.querySelector('[data-empty-state]');
        const box = empty || pane;
        if (!pane.querySelector('[data-draft-progress]')) {
          const tip = document.createElement('div');
          tip.className = 'response-confirm-bar is-compact';
          tip.setAttribute('data-draft-progress', '1');
          tip.innerHTML = '<div><strong>正在派生应答目录…</strong>'
            + '<span>复用已解析内容（评分点 / 规范要求），通常 10-30 秒；完成后本步会出现「① 确认目录 → ② 分配篇幅」。</span></div>';
          if (empty && empty.parentElement) empty.parentElement.insertBefore(tip, empty);
          else box.appendChild(tip);
        }
        return;
      }
      need = !!chk && chk.has_outline === false && Number(chk.source_sections) > 0;
    } catch (err) { return; }
  }
  const staleTip = pane.querySelector('[data-draft-progress]');
  if (staleTip) staleTip.remove();                 // 已不在派生中 → 撤掉进度条
  if (!need) return;
  // 让位必须**每次**都做（面板会重渲染，步骤条会重新出现）；
  // 之前把去重判断放在最前面，导致"出口块已存在"后就不再让位（实测步骤条又冒出来）。
  pane.querySelectorAll('[data-empty-state], [data-step-pane="1"], .sr-dir-chain').forEach((node) => {
    node.hidden = true;
    node.style.display = 'none';   // 自带 display，hidden 会被自家 CSS 覆盖
  });
  if (pane.querySelector('[data-raw-toc-action]')) return;
  const bar = document.createElement('div');
  bar.className = 'response-confirm-bar';
  bar.setAttribute('data-raw-toc-action', '1');
  bar.innerHTML = `<div><strong>应答目录尚未派生</strong>
    <span>当前显示的是招标文件原始目录（占位），还不是应答目录。需先按已解析内容派生应答目录，之后才会出现「① 确认目录 → ② 分配篇幅」两步。</span></div>`;
  const btn = document.createElement('button');
  btn.className = 'mw-btn primary';
  btn.textContent = '重新拆分目录';
  // 尊重应用自己的门控：它的「重新拆分目录」默认可能被禁用，并说明"本次运行已过闸门，
  // 目录不能再改；请用「重新拆分评分点」"。之前我写成"禁用时直接调 redraftOutline()"，
  // 等于绕过了门控 —— 走进应用明确阻止的路径（这很可能就是点它报错的原因）。
  const gateBtn = document.getElementById('btn-redraft-outline');
  const gateOpen = !gateBtn || !gateBtn.disabled;
  if (!gateOpen) {
    const reason = (gateBtn.getAttribute('title') || '').trim();
    bar.querySelector('span').textContent = reason
      ? `${reason}`
      : '本次运行已过闸门，目录不能再改；请改用「重新拆分评分点」。';
    btn.textContent = '重新拆分评分点';
    btn.addEventListener('click', async () => {
      const re = document.getElementById('btn-reextract');
      if (re && !re.disabled) { re.click(); return; }
      const slot = pane.querySelector('[data-flash-for="generate"]');
      if (slot) slot.textContent = '请到运行详情页点「重新拆分评分点」。';
    });
  } else {
    btn.addEventListener('click', async () => {
      btn.disabled = true; btn.textContent = '正在派生目录…';
      try {
        if (gateBtn) gateBtn.click();
        else await redraftOutline();
      } catch (err) {
        btn.disabled = false; btn.textContent = '重新拆分目录';
        const slot = pane.querySelector('[data-flash-for="generate"]');
        if (slot) slot.textContent = err.message || '派生失败，请重试';
      }
    });
  }
  bar.appendChild(btn);
  const slot = pane.querySelector('[data-flash-for="generate"]');
  if (slot) pane.insertBefore(bar, slot); else pane.appendChild(bar);
  // 可见反馈（2026-10-16 用户反馈"点击生成目录草案没有任何反应"）：
  // 应用把"正在打开独立的应答草案…"写进 [data-flash-for="generate"]，那个槽不显眼，
  // 而草案创建要跑 1-3 分钟（实测日志：评分点 13 条 + 规范要求 411 条落库）——
  // 用户看到的只有"按钮变灰"，于是以为没反应。这里把空态块本身变成进度提示。
  const genBtn = pane.querySelector('[data-act="response-preview"]');
  if (genBtn && !genBtn.dataset.progressWired) {
    genBtn.dataset.progressWired = '1';
    genBtn.addEventListener('click', () => {
      const box = pane.querySelector('[data-empty-state]');
      if (!box) return;
      const t = box.querySelector('strong');
      const sp = box.querySelector('span');
      if (t) t.textContent = '正在生成目录草案…';
      if (sp) sp.textContent = '已开始提取评分点与规范要求，通常需要 1–3 分钟。完成后本页会自动打开新草案，中途请不要重复点击。';
    });
  }
}

/** 启动常驻检查（只装一次）。 */
function watchRawTocWarning() {
  if (window.__rawTocWatcher) return;
  window.__rawTocWatcher = true;
  ensureRawTocExit();
  if (typeof MutationObserver === 'undefined') {
    setInterval(ensureRawTocExit, 1500);
    return;
  }
  let timer = null;
  const schedule = () => {
    if (timer) return;
    timer = setTimeout(() => { timer = null; ensureRawTocExit(); }, 600);   // 防抖：观察者触发很频繁
  };
  new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
}

async function openSplitReport({ auto = false, tab = '', stage: stageIn = '', sub = '' } = {}) {
  hideLaneTreeWhenPanelShows();
  try { await activePackageReady; } catch (err) {
    showSplitPanelRetry(err.message || '当前标包不可用，请返回项目准备核验');
    return;
  }
  if (activePackage) {
    confirmedPkg = {target: activePackage.target_code || '', package_no: activePackage.package_code || ''};
    panelViewPkg = {...confirmedPkg};
  }
  // 面板头那行运行进度：只在真在跑 / 有进度时显示（原来在状态栏，已随该模块去掉）
  setTimeout(refreshPanelRunState, 0);
  // 拆分进行中：报告此时是空的（旧数据已被清），画出来只会得到空标包下拉 +
  // "暂未拆分"这类矛盾文案。保持加载态，等 onDone 再画。
  if (splitInProgress) { showSplitPanelLoading('正在拆分评分点…'); return; }
  let wantStage = stageIn;
  // 兼容旧的 tab 写法（'commerce'/'confirm'/'directory'…）→ 映射到阶段 + 子页签
  if (tab && !wantStage) {
    if (tab === 'confirm') wantStage = '2';
    else if (tab === 'directory' || tab === 'map') { wantStage = '4'; sub = tab; }
    else { wantStage = '1'; sub = tab; }
  }
  if (wantStage === '3') { wantStage = '4'; sub = sub || 'directory'; }
  if (wantStage) panelStage = wantStage;
  if (sub) panelSub = sub;
  // 非"切范围"引起的重开（首次展开/自动展开/收起后再开）→ 查看范围跟随当前目录
  if (!tab && !wantStage) { panelViewPkg = null; panelViewCats = null; }
  const tid = await resolvePanelThreadId(null);
  let data = null;
  try { data = await api.workflow.splitReport(projectId, tid); } catch (e) { data = null; }
  // 该标包各应答类型的目录草案：类型卡据此显示"已生成/未生成"，点击时决定**切换**还是**生成**
  try {
    const pkgId = (activePackage && activePackage.id) || params.get('packageId');
    responseDraftsData = pkgId ? await api.projects.responseDrafts(projectId, pkgId) : null;
  } catch (err) { responseDraftsData = null; }
  // 篇幅与目录同源：面板每次打开都同步一次（目录变了篇幅可能失效，后端以目录确认为前置）
  pageBudgetThreadId = tid || '';
  try {
    const budgetResp = await fetch(`/api/workflow/${encodeURIComponent(tid)}/page-budget`, {signal: AbortSignal.timeout(10000)});
    pageBudgetData = budgetResp.ok ? await budgetResp.json() : null;
  } catch (e) { pageBudgetData = null; }
  if (!data) {
    // 不弹窗：把失败写进面板容器（用户点开的地方），一眼能看到
    const host = document.getElementById('split-panel-inline');
    if (host) {
      host.hidden = false;
      host.innerHTML = `<div class="sr-box"><div class="sr-head"><b>拆分评分点</b>
        <span class="sr-dim" style="margin-left:auto;">拆分结果</span></div>
        <div style="padding:14px 18px" class="sr-flash err">拆分清单暂时取不到（接口未返回）。请稍后重试，或点「重新拆分评分点」。</div></div>`;
      splitPanelExpanded = true;
    }
    return;
  }

  const rp = data.review_points || {}, su = data.scoring_units || {};
  const tm = data.tech_matrix || {}, mp = data.mapping || {};
  const smap = data.scoring_map || {};
  // 「评分点确认」必须按**当前版本**判定：重新拆分产生新版本后，
  // 旧版本的作答不算数 —— 否则该项一直显示"已作答"、确认按钮再也不出现，
  // 用户只剩页脚那个按钮可点（实测反馈"这个我要怎么确认？"）。
  const rpAns = (data.answers || {}).review_points || {};
  const rpCurrent = !!rpAns.confirmed
    && Number(rpAns.revision || 0) === Number(draftRevision || 0);
  const conf = (data.confirmations || []).map(c =>
    (c.key === 'review_points' && !rpCurrent)
      ? { ...c, answered: false, needs_confirm: true }
      : c);
  const allConfs = conf;                       // 全部确认项（含已确认的，面板要展示结果）
  const pending = conf.filter(c => c.needs_confirm);
  const settled = conf.filter(c => !c.needs_confirm);

  // ── 两阶段流程（用户要求）：先确认**评分点**，再确认**目录拆分** ──
  // 点「拆分评分点」→ 解析 → ① 评分点 → ② 待确认 → ③ 产物
  // → 再确认目录结构 → 收起、展示最终应答目录。
  const answers = data.answers || {};
  const pointsConfirmed = rpCurrent;      // 同一判定，避免两处口径不一致
  const stage = pointsConfirmed ? 'structure' : 'points';

  const curScope = activePackage ? {target: activePackage.target_code || '', package_no: activePackage.package_code || ''} : (data.project_scope || {});
  // 每个标包各自的评分点（后端 package_points）：用户反馈"我拆分了 3 个标包，
  // 但后面的评分点又好像对应不上"—— 之前明细页只有当前标包的数据。
  // ── 查看范围：标包下拉决定"下面所有内容看哪个标包" ──
  const viewPkg = (activePackage ? curScope : panelViewPkg) || {
    target: curScope.target || '', package_no: curScope.package_no || '',
  };
  const isRunScope = String(viewPkg.package_no || '') === String(curScope.package_no || '')
    && String(viewPkg.target || '') === String(curScope.target || '');
  const viewLabel = `${viewPkg.target || '—'} / 标包${viewPkg.package_no || '—'}`;
  const runLabel = `${curScope.target || '—'} / 标包${curScope.package_no || '—'}`;
  const catLabelOf = (c) => CAT_HINT[c] || c;

  const pkgs = data.package_points || [];
  // 没记住标包时按"无歧义"顺序兜底，避免从项目列表进来先撞一次「先确认标包」：
  //   ① 老运行（加标包字段之前建的）没有归属，但**项目级范围**（projects.scope_*）知道
  //      这个项目一直在做哪个标包 → 直接采用它；
  //   ② 本文件只有一个标包 → 采用它；
  //   ③ 都不满足（多标包且项目范围为空）→ 仍然要用户确认（这是正确的）。
  if (!confirmedPkg) {
    const ps = curScope || {};
    if (ps.target || ps.package_no) {
      rememberConfirmedPkg({ target: ps.target || '', package_no: ps.package_no || '' });
      panelViewPkg = { target: ps.target || '', package_no: ps.package_no || '' };
    } else if (pkgs.length === 1 && (pkgs[0].package_no || pkgs[0].target)) {
      rememberConfirmedPkg({ target: pkgs[0].target || '', package_no: pkgs[0].package_no || '' });
      panelViewPkg = { target: pkgs[0].target || '', package_no: pkgs[0].package_no || '' };
    }
  }
  const pkgLabel = (p) => `${p.target || '—'} / 标包${p.package_no || '—'}`;
  const curPkgLabel = pkgLabel(curScope);
  // 筛选出来的"查看范围"对应的数据行（指标、依据页、生成动作都用它）
  const viewPkgStat = resolvePackageScope(pkgs, viewPkg);
  const viewMatrixRow = resolvePackageScope(data.package_matrix || [], viewPkg);
  // 类别筛选：默认 = 当前目录真实用的类别（若筛的就是当前范围）；
  // 筛到别的标包 → 默认勾该标包分值最高的那一类，方便直接生成。
  const viewCatsAvail = (viewMatrixRow?.cells || []).map(c => c.category);
  // 注意：不要写成 `(a ? b : []) || (c ? d : e)` —— **空数组是 truthy**，
  // 中间那个 `[]` 会把后面的默认值整段吃掉（实测：viewCats 恒为空 → 指标显示"未选类别/0 个"）。
  let viewCats = (panelViewCats && panelViewCats.length)
    ? panelViewCats.filter(c => viewCatsAvail.includes(c))
    : null;
  if (!viewCats || !viewCats.length) {
    viewCats = (isRunScope && (data.current_categories || []).length)
      ? (data.current_categories || []).filter(c => viewCatsAvail.includes(c))
      : viewCatsAvail.slice(0, 1);
  }
  // 产物（骨架/要求/映射）只有在"筛的就是当前已生成的标包"时才存在
  const hasProducts = isRunScope;
  const matrixRows = data.package_matrix || [];
  // ── 三段式门控（用户口径）──
  //   ① 拆分：商务评分点 / 技术评分点 / 技术规范书要求（三个并列块，都是"依据"）
  //   ② 待确认：三块都拆完才出现（内容是本步真正要用户决定的）
  //   ③ 产物：**确认完成后**才出现 —— 目录 + 技术与评分点映射矩阵
  // 用户原话：「目前"两个产物"和"待确认"的内容在这一步是没有意义的。必须等商务和技术的
  //   评分点、以及技术规范书的拆分完成，并且根据待确认的内容确认完成后，才会有对应的两个产物。」
  const pkgGroups = viewPkgStat?.groups || [];
  const catStat = (c) => pkgGroups.find(g => g.category === c) || null;
  const commerceStat = catStat('commerce');
  const techStat = catStat('tech');
  const priceStat = catStat('price');
  const specCount = Number(tm.count || 0);
  // 该标包**实际有的**类别 → 决定显示哪些拆分页签（没有的不显示空页签）
  const pointTabs = [
    commerceStat ? { id: 'commerce', label: '商务评分点', badge: `${commerceStat.count}` } : null,
    techStat ? { id: 'tech', label: '技术评分点', badge: `${techStat.count}` } : null,
    priceStat ? { id: 'price', label: '报价评分点', badge: `${priceStat.count}` } : null,
  ].filter(Boolean);
  // "三块都拆完" = 该标包的各类评分点都在 + 技术规范书要求有内容
  const analyzedAll = pointTabs.length > 0;
  // "已确认" = 运行级作答里已经有 categories（要生成哪些类别）
  const confirmedCats = ((data.answers || {}).categories || {}).categories || [];
  const responsePlan = (data.answers || {}).response_plan || {};
  const responseType = responsePlanChoice || responsePlan.type || 'tech';
  const responseLabel = responseType === 'commerce' ? '商务应答' : '技术应答';
  const categoriesConfirmed = responsePlan.type ? responsePlan.confirmed === true : confirmedCats.length > 0;
  // "已生成" = 目录已派生（outline_pending 为假且有章节）
  const generated = !outlinePending && (totalBlocks || 0) > 0;

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
        ctrl = `<div class="sr-answer"><span class="sr-dim">目录里暂无可选章节，先拆分出目录再指定归属。</span>
          <button class="mw-btn" data-save="skip" data-key="topic_placement">跳过</button></div>`;
      } else {
        const opts = chapters.map(ch =>
          `<option value="${escapeHtml(ch.title)}">${'　'.repeat(Math.max(0, ch.level - 2))}${escapeHtml(ch.title)}</option>`).join('');
        const picked = (data.answers || {}).topic_placement || {};
        const rows = topics.map(t => `
          <div class="sr-topic-row">
            <span class="sr-topic-name" title="${escapeHtml(t.topic)}">${escapeHtml(t.topic)}
              <span class="sr-dim">（${t.count} 条）</span></span>
            <select data-topic="${escapeHtml(t.topic)}">
              <option value="">默认：进「技术规范书要求应答」</option>
              ${chapters.map(ch => `<option value="${escapeHtml(ch.title)}"${
                picked[t.topic] === ch.title ? ' selected' : ''}>${
                '　'.repeat(Math.max(0, ch.level - 2))}${escapeHtml(ch.title)}</option>`).join('')}
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
    const badge = c.answered ? '已确认' : (c.needs_confirm ? '待确认' : '已识别');
    const cls = c.answered ? 'done' : (c.needs_confirm ? 'warn' : 'ok');
    const editKey = c.answer_key || c.key;
    const editing = !!confirmEditOpen[editKey];
    // 已确认的项：显示**确认结果**，控件折叠在「修改」后面（确认完还查得到 —— 用户要求）
    const answerText = (() => {
      if (!c.answered) return '';
      if (c.answer_key === 'topic_placement') {
        const map = (data.answers || {}).topic_placement || {};
        const rows = Object.entries(map);
        if (!rows.length) return '全部主题保持默认归属（进「技术规范书要求应答」章节）';
        return `<table class="sr-table sr-conf-result"><thead><tr>
            <th>技术规范书主题</th><th>归属章节</th></tr></thead><tbody>${
          rows.map(([t, ch]) => `<tr><td>${escapeHtml(t)}</td>
            <td>${escapeHtml(ch || '默认：技术规范书要求应答')}</td></tr>`).join('')}</tbody></table>`;
      }
      const a = c.answer;
      if (a && typeof a === 'object') {
        return escapeHtml(Object.entries(a).map(([k, v]) => `${k}：${v}`).join('；'));
      }
      return escapeHtml(String(a || c.detected || '已确认'));
    })();
    const body = c.answered && !editing
      ? `<div class="sr-conf-a">${escapeHtml(c.detected)}</div>
         ${answerText ? `<div class="sr-conf-result-wrap">${answerText}</div>` : ''}
         <div class="sr-answer"><button class="mw-btn" data-act="edit-confirm" data-key="${escapeHtml(editKey)}">修改</button></div>`
      : `<div class="sr-conf-a">${escapeHtml(c.detected)}</div>
         <div class="sr-conf-d">${escapeHtml(c.detail || '')}</div>
         ${ctrl}`;
    return `<div class="sr-conf ${cls}">
      <div class="sr-conf-head"><span class="sr-conf-badge">${badge}</span>
        <span class="sr-conf-q">${escapeHtml(c.question)}</span></div>
      ${body}</div>`;
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
        <td class="sr-crit">${escapeHtml(r.criterion || '—')}</td></tr>`).join('');

  // 指标条：一眼看清"这份目录是什么规模、依据确认到什么程度"
  // ── 指标**随筛选变化**（用户口径：筛选在最上层，下面全部由它派生）──
  // 依据类（评分点/评分表）任何范围都能算；产物类（骨架/要求/映射）只有"筛到这个已生成的
  // 范围"才有内容，否则显示中性"—"，而不是把上一个范围的数字摆在这里。
  const viewGroups = (viewPkgStat?.groups || []).filter(g => viewCats.includes(g.category));
  const viewPointCount = viewGroups.reduce((a, g) => a + (g.count || 0), 0);
  const viewPointScore = Math.round(viewGroups.reduce((a, g) => a + (g.score || 0), 0) * 100) / 100;
  const viewCatsLabel = viewCats.length
    ? viewCats.map(c => ({ tech: '技术', commerce: '商务', price: '报价', other: '其他' }[c] || c)).join('、')
    : '未选类别';
  const metrics = [
    { label: '商务评分点', value: commerceStat ? `${commerceStat.count} 个` : '—',
      sub: commerceStat ? `合计 ${commerceStat.score} 分` : '该标包无商务评分点',
      cls: commerceStat ? 'ok' : 'pending' },
    { label: '技术评分点', value: techStat ? `${techStat.count} 个` : '—',
      sub: techStat ? `合计 ${techStat.score} 分` : '该标包无技术评分点',
      cls: techStat ? 'ok' : 'pending' },
    { label: '技术规范书要求', value: specCount ? `${specCount} 条` : '—',
      sub: specCount ? '按原文目录展开' : '还没拆出要求',
      cls: specCount ? 'ok' : 'pending' },
    { label: '应答策划', value: analyzedAll ? `${pending.length} 项` : '未就绪',
      sub: analyzedAll ? '选择类型并检查目录' : '等评分点拆分完成',
      cls: analyzedAll ? (pending.length ? 'warn' : 'ok') : 'pending' },
    { label: '产物 · 目录', value: generated ? `${su.unit_count || 0} 个` : '—',
      sub: generated ? `${su.sub_item_count || 0} 详细项`
                     : (categoriesConfirmed ? '生成中 / 未生成' : '确认后生成'),
      cls: generated ? '' : 'pending' },
    { label: '产物 · 映射矩阵', value: generated ? `${(mp.point_to_requirement || []).length} 个评分点` : '—',
      sub: generated ? `未落点 ${(mp.unmapped_requirements || []).length} 条`
                     : (categoriesConfirmed ? '生成中 / 未生成' : '确认后生成'),
      cls: generated ? '' : 'pending' },
  ];
  const metricHtml = metrics.map(m => `
    <div class="sr-metric ${m.cls}">
      <div class="sr-metric-label">${m.label}</div>
      <div class="sr-metric-value">${escapeHtml(String(m.value))}</div>
      <div class="sr-metric-sub">${escapeHtml(String(m.sub))}</div>
    </div>`).join('');

  // ── 标签页：四张清单 + 拆分依据，各自独立滚动（原来全挤在一页里滚不完）──
  const pkgBlocks = pkgs.filter((p) =>
    String(p.package_no || '') === String(viewPkg.package_no || '')
    && String(p.target || '') === String(viewPkg.target || '')).map((p) => {
    const isCur = isRunScope;
    const totals = (p.groups || []).map(g =>
      `${g.label} ${g.count} 个 · ${g.score} 分`).join('　／　');
    const rows = (p.groups || []).flatMap(g => (g.items || []).map(it => `
      <tr><td>${escapeHtml(it.factor || '')}</td><td>${escapeHtml(it.item || '')}</td>
          <td class="sr-num">${it.points != null ? it.points : '—'}</td>
          <td><span class="sr-chip">${escapeHtml(g.label)}</span></td>
          <td class="sr-crit">${escapeHtml(it.criterion || '—')}</td></tr>`)).join('');
    return `<details class="pp-pkg" ${isCur ? 'open' : ''}>
      <summary><b>${escapeHtml(pkgLabel(p))}</b>
        <span class="pp-name">${escapeHtml(p.package_name || '')}</span>
        <span class="sr-dim">${escapeHtml(totals)}</span>
        ${isCur ? '<span class="sr-chip sel">当前目录用的标包</span>' : ''}</summary>
      <table class="sr-table"><thead><tr><th>评分因素</th><th>详细评审项</th>
        <th class="sr-num">分值</th><th>类别</th><th>评分口径</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="sr-dim">该标包下没有解析到评分点</td></tr>'}</tbody></table>
    </details>`;
  }).join('');

  // 切换到的标包**还没生成目录** → 依据里的评分点能看，产物/要求一律显示空态，
  // 绝不能继续显示上一个标包的内容（这正是"评分点对应不上标包"的来源）。
  const notGenerated = (what) => `
    <div class="sr-empty">
      <b>${escapeHtml(viewLabel)}</b> 暂无目录${what ? `，因此${what}暂时没有内容` : ''}。
      <div class="sr-dim" style="margin-top:5px">
        它的评分点可以在「依据 · 招标文件评分点」看到；
        要按这个标包出目录，点上方的「按此范围生成目录」。
        当前目录仍是：${escapeHtml(runLabel)}${(data.current_categories || []).length
          ? ` · ${(data.current_categories || []).map(catLabelOf).join('、')}` : ''}。
      </div>
    </div>`;

  const unitRows = (su.items || []).map(u => `
    <tr class="sr-unit"><td><b>${escapeHtml(u.title)}</b></td>
        <td class="sr-num">${u.score_weight != null ? u.score_weight : '—'}</td>
        <td class="sr-num">${(u.items || []).length}</td>
        <td class="sr-crit">${(u.items || []).map(i => escapeHtml(i.title)).join('、')}</td></tr>
    ${(u.items || []).map(i => `
      <tr class="sr-sub"><td>　└ ${escapeHtml(i.title)}</td><td class="sr-num">—</td>
          <td class="sr-num">${(i.sub_items || []).length}</td>
          <td class="sr-crit">${(i.sub_items || []).map(x => escapeHtml(x.title)).join('、') || '<span class="sr-dim">—</span>'}</td></tr>`).join('')}`).join('');

  // 技术规范书要求：**逐条列出要求原文**（结构化）。
  // 以前这里只渲染 r.title（章节路径），而且 .slice(0, 3) 每个主题只显示 3 条 ——
  // 153 条要求里绝大多数根本看不到，用户反馈"看不出来具体要求是什么"。
  const REQ_LEVEL = {
    mandatory: ['必须响应', 'is-warn'],
    required: ['应响应', 'is-none'],
    informational: ['参考', 'is-none'],
  };
  const specSectionHtml = (t) => {
    const requiredCount = (t.requirements || []).filter(r => r.mandatory_level === 'mandatory').length;
    const head = `<details class="spec-topic" open><summary class="spec-topic-head">
        <span class="spec-topic-title">${escapeHtml(t.display_title || t.topic)}${(t.requirements || []).some(r=>r.attention?.flags?.includes('veto')) ? ' <span class="spec-risk veto">含否决风险</span>' : ''}</span>
        <span class="spec-topic-count">${t.count ? `${t.count} 条要求` : '原文说明'}${requiredCount ? ` · 必须响应 ${requiredCount}` : ''}</span>
      </summary><div class="spec-topic-body">`;
    const rows = (t.requirements || []).map((r, i) => {
      const [label, cls] = REQ_LEVEL[String(r.mandatory_level || '').toLowerCase()]
        || [String(r.mandatory_level || '—'), 'is-none'];
      const flags = r.attention?.flags || [];
      const labels = {veto:'否决风险',substantive:'实质性要求',marked:'原文标记',check:'待核对',proof:'须提供证明'};
      return `<article class="spec-clause ${flags.includes('veto') ? 'spec-veto' : ''}" data-attention="${escapeHtml(flags.join(' '))}">
        <span class="spec-clause-no">${String(i + 1).padStart(2, '0')}</span>
        <div class="spec-clause-main">${flags.length ? `<div class="spec-attention-tags">${flags.map(f=>`<span class="spec-risk ${f}">${escapeHtml(labels[f] || f)}</span>`).join('')}${escapeHtml((r.attention?.markers || []).join(' '))}${r.attention?.inherited ? ' · 章节标记' : ''}</div>` : ''}${r.attention?.note ? `<p class="spec-risk-note">${escapeHtml(r.attention.note)}</p>` : ''}${r.attention?.evidence?.length ? `<details class="spec-clause-origin"><summary>判定依据</summary>${r.attention.evidence.map(e=>`<p>${escapeHtml(e.basis)}：${escapeHtml(e.text)}</p>`).join('')}</details>` : ''}<div class="spec-clause-text">${specContentHtml(r.text || '')}</div>${
          r.title ? `<details class="spec-clause-origin"><summary>原文位置</summary><p>${escapeHtml(r.title)}</p>${
            (t.source_files || []).map(f => `<p>${escapeHtml(f)}</p>`).join('')}</details>` : ''}</div>
        <span class="ui-pill spec-clause-level ${cls}">${escapeHtml(label)}</span>
      </article>`;
    }).join('');
    const context = !rows && t.original_content ? `<div class="spec-context spec-clause" data-attention=""><div class="spec-clause-text">${specContentHtml(t.original_content)}</div></div>` : '';
    const children = (t.children || []).map(specSectionHtml).join('');
    return head + rows + context + (children ? `<div class="spec-children">${children}</div>` : '') + '</div></details>';
  };
  const specRows = (tm.structure || tm.topics || []).map(specSectionHtml).join('');


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
  // ── 内容区：按三段式组织 ──
  // 拆分层三个块（商务评分点 / 技术评分点 / 技术规范书要求）+ 待确认 + 产物两个
  /**
   * 把评分口径解析成**结构化的得分条件**。
   *
   * 用户反馈：「评分口径的内容能不能更加结构化一些？目前一堆文字展示在这里体验不好」。
   * 招标文件的评分口径其实是"编号条件 + 得分"的规则文本，例如
   *   （1）团队不少于 35 人得 3 分；（2）每提供一个案例得 1 分，最多 5 分。
   * 直接整段铺出来没法读，所以按编号/分号切成条件条目，并把分值抽出来单独成标签。
   * **不丢内容**：每条的原文完整保留，整段原文也放在「原文」折叠里。
   */
  function parseCriterion(text) {
    const raw = String(text || '').trim();
    if (!raw) return { clauses: [], raw: '' };
    // 归一化全角标点，便于统一切分
    let t = raw.replace(/[\uFF08]/g, '(').replace(/[\uFF09]/g, ')')
               .replace(/[\uFF1B]/g, ';').replace(/[\uFF0C]/g, ',');
    // ① 优先按编号切： (1) / 1) / 1. / ①-⑳ / 一、二、
    const numRe = /(?:\(\d{1,2}\)|\d{1,2}[).、]|[\u2460-\u2473]|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]{1,3}[、.])/g;
    let parts = [];
    const marks = [...t.matchAll(numRe)];
    if (marks.length >= 2) {
      marks.forEach((m, i) => {
        const start = m.index;
        const end = i + 1 < marks.length ? marks[i + 1].index : t.length;
        parts.push(t.slice(start, end));
      });
    } else {
      // ② 退化为按分号/换行切（仍比一整段好读）
      parts = t.split(/[;\n]+/).map(x => x.trim()).filter(Boolean);
      if (parts.length < 2) parts = [t];
    }
    const clauses = parts.map((seg) => {
      const seg2 = seg.replace(/^[\s,;:.-]+/, '').trim();
      // 抽分值：优先"得 N 分"，其次"N 分"（含"最多 N 分"）
      const m = seg2.match(/(?:得|计|加)?\s*(\d+(?:\.\d+)?)\s*分/);
      const cap = /最[多高]\s*(\d+(?:\.\d+)?)\s*分/.exec(seg2);
      const label = m ? `${cap && cap[1] !== m[1] ? `最多 ${cap[1]}` : m[1]} 分` : '';
      return { text: seg2, score: label, cap: !!(cap && m && cap[1] !== m[1]) };
    }).filter(c => c.text);
    return { clauses, raw };
  }

  const critCard = (it) => {
    const pc = parseCriterion(it.criterion);
    return `<div class="pc-card">
      <div class="pc-head">
        <span class="pc-factor">${escapeHtml(it.factor || '—')}</span>
        <span class="pc-item">${escapeHtml(it.item || '')}</span>
        <span class="pc-score">${it.points != null ? `${it.points} 分` : '—'}</span>
      </div>
      ${pc.clauses.length > 1 ? `<ul class="pc-list">${pc.clauses.map(c => `
        <li><span class="pc-text">${escapeHtml(c.text)}</span>${
          c.score ? `<span class="pc-band ${c.cap ? 'cap' : ''}">${escapeHtml(c.score)}</span>` : ''}</li>`).join('')}
      </ul>` : `<div class="pc-body">${escapeHtml(pc.raw || '—')}</div>`}
    </div>`;
  };

  const catTable = (stat, catLabel) => {
    if (!stat || !(stat.items || []).length) {
      return `<div class="sr-empty">该标包没有解析到${catLabel}评分点。</div>`;
    }
    return `
      <div class="sr-points-meta"><span>来源：招标文件</span>
        <span class="sr-points-total">合计 <b>${stat.score}</b> 分</span></div>
      ${(stat.items || []).map(critCard).join('')}`;
  };

  const notAnalyzedHint = (what) => `
    <div class="sr-empty">${escapeHtml(viewLabel)} 的${what}暂未拆分。
      <div class="sr-dim" style="margin-top:5px">请点上方「拆分评分点」按该标包重新拆分。</div>
    </div>`;

  // ② 待确认：唯一需要用户决定的界面；确认后才会出现产物
  // 应答策划：选择类型、预览独立草案、确认产物。
  // 面板内的可编辑结构：一行一章（缩进表现层级），选中行显示该行操作
  const pointMeta = new Map(((rp && rp.items) || []).map((x) => [String(x.point_id), x]));
  const reqNameOf = (pid) => {
    const m = pointMeta.get(String(pid));
    return (m && (m.item || m.factor)) || '';
  };
  const parseIds = (raw) => {
    try { const v = JSON.parse(raw || '[]'); return Array.isArray(v) ? v : []; } catch { return []; }
  };
  const structBlocks = [...blocksById.values()]
    .filter((b) => b && b.level)
    .sort((a, b2) => (Number(a.order_idx) || 0) - (Number(b2.order_idx) || 0));
  const chapterSources = new Map((workspace?.state?.spec?.toc || []).map(s => [s.id, s.chain || {}]));
  const structRows = structBlocks.map((b, i) => {
    const source = chapterSources.get(String(b.block_id || b.id)) || {};
    const locked = source.response_part === 'point_to_point';
    const pts = parseIds(b.review_point_ids).map(reqNameOf).filter(Boolean);
    const reqs = parseIds(b.spec_requirement_ids).length;
    const lvl = Math.max(1, Number(b.level) || 1);
    return `<div class="sr-erow${locked ? ' is-source' : ''}${lvl >= 3 ? ' is-deep' : ''}${
        selectedStructId === String(b.block_id || b.id) ? ' on' : ''}"
      style="padding-left:${10 + (lvl - 1) * 16}px"
      data-struct="${escapeHtml(String(b.block_id || b.id))}" data-level="${lvl}" data-i="${i}">
      <span class="sr-grip" data-grip="${escapeHtml(String(b.block_id || b.id))}"
        title="按住上下拖动排序（勾选多行可整组拖）">⠿</span>
      <input type="checkbox" class="sr-pick" data-pick="${escapeHtml(String(b.block_id || b.id))}"
        ${batchIds.has(String(b.block_id || b.id)) ? 'checked' : ''}
        title="选中后可批量操作" ${locked ? 'disabled' : ''} />
      <span class="sr-lvl" title="目录层级">L${lvl}</span><span class="sr-erow-title">${escapeHtml(b.title || '')}</span>${source.response_part ? `<span class="sr-struct-meta">${({scoring:'评分项',priority:'重点要求',point_to_point:'原文目录'})[source.response_part] || ''}</span>` : ''}
      ${pts.length ? `<span class="sr-struct-meta">评分点：${escapeHtml(pts.join('、').slice(0, 20))}</span>` : ''}
      ${b.score_weight ? `<span class="sr-struct-score">${b.score_weight} 分</span>` : ''}
      ${reqs ? `<span class="sr-struct-meta">规范书 ${reqs} 条</span>` : ''}
      ${pageBudgetCellHtml(b)}
      <span class="sr-erow-acts">
        <button class="sr-mini" data-act="struct" data-op="rename" data-block="${escapeHtml(String(b.block_id || b.id))}">改名</button>
        <button class="sr-mini" data-act="struct" data-op="up" data-block="${escapeHtml(String(b.block_id || b.id))}">上移</button>
        <button class="sr-mini" data-act="struct" data-op="down" data-block="${escapeHtml(String(b.block_id || b.id))}">下移</button>
        <button class="sr-mini" data-act="struct" data-op="indent" data-block="${escapeHtml(String(b.block_id || b.id))}">缩进</button>
        <button class="sr-mini" data-act="struct" data-op="outdent" data-block="${escapeHtml(String(b.block_id || b.id))}">减少缩进</button>
        <button class="sr-mini danger" data-act="struct" data-op="delete" data-block="${escapeHtml(String(b.block_id || b.id))}">删除</button>
      </span>

    </div>`;
  }).join('');

  const pane1 = `
      <h5 class="sr-sec">目录草案（${structBlocks.length} 个目录节点）· ${data.outline_checks?.response_outline_version === 2 ? '评分与重点章节可调整，原文目录固定' : '点一行即可改'}</h5>
      <div class="sr-batch${batchIds.size ? '' : ' is-empty'}" id="sr-batch">
        <span class="sr-dim">已选 <b id="sr-batch-n">${batchIds.size}</b> 项</span>
        <button class="sr-mini" data-batch="up">批量上移</button>
        <button class="sr-mini" data-batch="down">批量下移</button>
        <button class="sr-mini" data-batch="indent">批量缩进</button>
        <button class="sr-mini" data-batch="outdent">减少缩进</button>
        <button class="sr-mini danger" data-batch="delete">批量删除</button>
        <button class="sr-mini" data-batch="clear">清空选择</button>
      </div>
      <div class="sr-struct sr-struct-edit">${structRows
        || '<div class="sr-dim">还没有派生目录 —— 确认后会按评分点结构生成。</div>'}</div>
      <div class="response-chapter-evidence" id="response-chapter-evidence">选择章节，查看对应评分点和技术规范书原文依据。</div>
      <div class="sr-answer">
        <button class="mw-btn" data-act="struct-new" data-op="sibling">＋ 新增同级章节</button>
        <button class="mw-btn" data-act="struct-new" data-op="child">＋ 新增子章节</button>
        <button class="mw-btn" data-act="undo-structure"${undoStack.length ? '' : ' disabled'}
          title="${undoStack.length ? `撤销「${escapeHtml(String(undoStack[undoStack.length - 1].label))}」` : '没有可撤销的操作'}">↶ 撤销</button>
        <span class="sr-dim">${structSaveNote || '点一行 → 该行出现操作；改动即时保存'}</span>
      </div>
      ${structureBlockNote()}`;

  const currentPlanMatches = responsePlan.type === responseType;
  const previewReady = currentPlanMatches && generated;
  window.clearTimeout(responsePlanPoll);
  if (responsePlan.type && !generated && workspace?.state?.stage !== 'failed') {
    responsePlanPoll = window.setTimeout(async function pollResponseDraft() {
      if (params.get('threadId') !== tid) return;
      try {
        const state = await api.workflow.state(tid);
        if (['failed', 'aborted', 'outline_review'].includes(state.stage)) { location.reload(); return; }
      } catch (err) { console.warn('读取应答草案进度失败', err); }
      responsePlanPoll = window.setTimeout(pollResponseDraft, 3000);
    }, 3000);
  }
  const expectedPoints = responseType === 'commerce' ? commerceStat?.count || 0 : techStat?.count || 0;
  const landedPoints = new Set(structBlocks.flatMap(b => parseIds(b.review_point_ids)));
  const landedRequirements = new Set(structBlocks.flatMap(b => parseIds(b.spec_requirement_ids)));
  const expectedRequirements = (tm.topics || []).flatMap(t => t.requirements || []);
  const missingRequirements = expectedRequirements.filter(r => !landedRequirements.has(r.requirement_id));
  const responseChecks = data.outline_checks || {};
  const responseWarningsHtml = responseChecks.warnings?.length ? `<details class="response-landing-warning"><summary>建议前部突出 ${responseChecks.warnings.length} 条重点要求 · 不影响确认</summary><p>以下要求已在点对点应答中安排，可结合评分章节补充说明，无需恢复原来的独立章节。</p><ul>${responseChecks.warnings.map(r=>`<li>${escapeHtml(r.title)}${r.critical ? '（明确风险要求）' : ''}</li>`).join('')}</ul></details>` : '';
  const focusSuggest = data.outline_checks?.response_outline_version === 2
    ? Math.max(0, Number(responseChecks.focus_requirements_total || 0) - Number(responseChecks.focus_requirements_in_front || 0))
    : 0;
  const confirmPane = !analyzedAll ? '<div class="sr-empty">请先完成当前标包的评分点拆分。</div>' : `
    <div class="response-type-options" role="group" aria-label="选择应答类型">
      ${[['commerce','商务应答','以商务评分点为主，核对商务格式与证明材料'],['tech','技术应答','整合技术评分点、技术规范书及项目基础内容']].map(([id,label,desc])=>`
        ${(() => {
          const d = draftOfType(id);
          const cur = !!d && String(d.thread_id) === String(tid);
          // 三种状态要分清：已派生（可切换查看）/ 未派生（要重新派生）/ 尚无草案（要生成）。
          // 实测 project 29：技术草案的 blocks 被新解析清空后，卡片还写着"已生成"，
          // 切进去却是空页 —— 用户无从判断。
          const state = !d ? '尚未生成 · 点击生成'
            : (d.has_outline
              ? `${d.confirmed ? '已确认' : '已生成'} · ${Number(d.outline_count) || 0} 个节点${cur ? ' · 正在查看' : ' · 点击切换'}`
              : '目录未派生 · 点击重新派生');
          return `<button type="button" class="response-type ${responseType===id ? 'on' : ''}${cur ? ' is-current' : ''}" data-response-type="${id}" aria-pressed="${responseType===id}"><strong>${label}</strong><span>${desc}</span><em class="response-type-state${d ? (d.confirmed ? ' is-ok' : '') : ' is-none'}">${state}</em></button>`;
        })()}`).join('')}
    </div>
    <div class="response-basis-card">
      <div class="response-basis-top"><div class="response-basis-main"><strong>编制依据</strong>
        <div class="response-basis-metrics"><span>${responseType==='commerce' ? '商务' : '技术'}评分点 <b>${expectedPoints}</b> 项</span>${responseType==='tech' ? `<span>技术规范要求 <b>${specCount}</b> 条</span>` : ''}</div>
        <p>${responseType==='commerce' ? '围绕商务评审项组织应答，核对格式与证明材料。' : '整合项目背景、范围、实施与交付要求，保留重点条款依据。'}</p></div>
      </div>
      <details class="response-basis-details"><summary>来源与编制规则</summary><div class="response-basis-detail-body">${responseType==='tech' ? (tm.source_files || []).map(f=>`<div class="response-source-file"><span>技术规范书</span><p>${escapeHtml(f)}</p></div>`).join('') : '<p>来源：当前标包招标文件中的商务评分表及商务文件格式要求。</p>'}
        <p>优先遵循招标文件规定的组成与格式；格式和证明材料要求需核对原文。</p></div></details>
      ${previewReady ? '<div class="response-basis-footer"><button class="mw-btn response-basis-action" data-act="response-preview">查看目录草案</button></div>' : ''}
    </div>
    ${!previewReady ? (() => {
      const d = draftOfType(responseType);
      if (d && d.thread_id) {
        // 目录已经生成，只是不在当前这条线程上（草案独立保存）→ 直接给"打开"
        return `<div class="response-confirm-bar" data-empty-state>
          <div><strong>本标包的${responseLabel}目录已生成 · ${Number(d.outline_count) || 0} 个目录节点</strong>
            <span>它保存在独立的目录草案里（商务/技术各自一份）。点右侧打开，或在类型卡片之间切换。</span></div>
          <button class="mw-btn primary" data-act="open-draft">打开应答目录</button>
        </div>`;
      }
      const anyDraft = ((responseDraftsData && responseDraftsData.drafts) || []).length > 0;
      return `<div class="response-confirm-bar" data-empty-state>
        <div><strong>还没有生成${responseLabel}目录草案</strong>
          <span>${currentPlanMatches
            ? '正在派生目录，完成后本页会自动出现「① 确认目录 → ② 分配篇幅」。'
            : `点上方「${responseLabel}」卡片即可生成（复用已解析内容，通常 10-30 秒）。${anyDraft ? '本标包另一类型已有目录，可随时切换。' : ''}`}</span></div>
        ${currentPlanMatches ? '<button class="mw-btn" data-act="redraft-outline">重试生成</button>' : ''}
      </div>`;
    })() : ''}
    ${currentPlanMatches && !generated ? '<div class="sr-answer"><span class="sr-dim">草案尚未完成，可在运行详情查看进度。</span><button class="mw-btn" data-act="redraft-outline">重试生成</button></div>' : ''}
    ${previewReady && responseType === 'tech' && data.outline_checks?.response_outline_version !== 2 ? '<div class="sr-answer"><span class="sr-dim">目录编排规则已更新：评分点 → 重点要求 → 原文点对点应答。</span><button class="mw-btn" data-act="redraft-outline">按新规则重新生成</button></div>' : ''}
    ${previewReady ? `<div class="response-coverage"><strong>覆盖检查</strong><span>已关联评分点 ${landedPoints.size} / ${expectedPoints}</span><span class="sr-dim">证明材料与原文格式仍需人工复核</span></div>
      ${previewReady ? `<div class="sr-dir-head">
        <h5 class="sr-sec" style="margin:0">目录</h5>
        <span class="ui-pill ${categoriesConfirmed ? 'is-ok' : 'is-none'}">${categoriesConfirmed ? '已确认' : '待确认'} · ${structBlocks.length} 个目录节点</span>
        ${focusSuggest > 0 ? `<span class="sr-dir-warn">⚠ ${focusSuggest} 条重点要求建议前部突出</span>` : ''}
        <details class="sr-dir-more"><summary>▸ 校验明细</summary>
          <div class="sr-dim">已关联评分点 ${landedPoints.size} / ${expectedPoints}${data.outline_checks?.response_outline_version === 2 ? ` · 原文章节 ${data.outline_checks.original_sections_preserved} / ${data.outline_checks.original_sections_total} · 要求归属 ${data.outline_checks.point_to_point_requirements} / ${data.outline_checks.point_to_point_requirements_total} · 重点要求前部承接 ${responseChecks.focus_requirements_in_front} / ${responseChecks.focus_requirements_total}` : ''}<br>编制依据：${responseType === 'commerce' ? '商务' : '技术'}评分点 ${expectedPoints} 项${responseType === 'tech' ? ` · 技术规范要求 ${specCount} 条` : ''}</div></details>
        <span style="flex:1"></span>
        <span class="sr-dir-chain" role="group" aria-label="应答策划步骤">
          ${(() => {
            const pageDone = !!pageBudgetData?.confirmed;
            const step = resolvedStep(categoriesConfirmed);
            const cls = (n) => (n === 1 && categoriesConfirmed) || (n === 2 && pageDone) ? 'is-done'
              : (step === String(n) ? 'is-now' : 'is-todo');
            return `<button class="sr-step ${cls(1)}" data-step-go="1">① 确认目录${categoriesConfirmed ? ' ✓' : ''}</button>
              <i>→</i>
              <button class="sr-step ${cls(2)}" data-step-go="2" ${categoriesConfirmed ? '' : 'disabled title="先完成第 1 步：确认目录"'}>② 分配篇幅${pageDone ? ' ✓' : ''}</button>`;
          })()}
      </div>` : ''}
      ${previewReady ? planStepBarHtml(categoriesConfirmed) : ''}
      ${pane1}
      <details class="response-mapping"><summary>查看章节与原文依据映射</summary>${structBlocks.filter(b=>parseIds(b.review_point_ids).length||parseIds(b.spec_requirement_ids).length).map(b=>`<div class="response-map-row"><strong>${escapeHtml(b.title)}</strong><span>${parseIds(b.review_point_ids).map(reqNameOf).filter(Boolean).map(escapeHtml).join('、') || '技术规范要求'}</span><span class="sr-dim">${parseIds(b.spec_requirement_ids).length} 条规范要求</span></div>`).join('')}</details>
      <div class="response-confirm-bar"><div><strong>${categoriesConfirmed ? '目录已确认' : '确认应答目录'}</strong><span>${categoriesConfirmed ? '可直接进入编写，随时查看目录与依据' : '保存目录并校验，成功后直接进入编写'}</span></div><button class="mw-btn primary" data-act="${categoriesConfirmed ? 'response-write' : 'response-confirm'}">${categoriesConfirmed ? `进入${responseLabel}编写` : `确认目录，创建${responseLabel}`}</button></div><div class="response-confirm-feedback" data-confirm-feedback role="status" aria-live="polite" hidden></div>` : ''}
    ${previewReady && data.outline_checks?.response_outline_version === 2 ? `<div class="response-coverage"><strong>重点与点对点检查</strong><span>重点要求前部承接 ${responseChecks.focus_requirements_in_front} / ${responseChecks.focus_requirements_total}</span><span>明确风险要求已安排 ${responseChecks.risk_requirements_landed} / ${responseChecks.risk_requirements_total}</span><span>原文章节 ${data.outline_checks.original_sections_preserved} / ${data.outline_checks.original_sections_total}</span><span>${data.outline_checks.original_structure_matches ? '标题、层级与顺序一致' : '原文结构待核对'}</span><span>要求归属 ${data.outline_checks.point_to_point_requirements} / ${data.outline_checks.point_to_point_requirements_total}</span><span class="sr-dim">仅检查承接位置，正文响应质量待编写后核对。</span></div>${responseWarningsHtml}` : ''}
    ${previewReady ? `<div class="response-confirm-bar is-compact sr-dir-foot">
      <b>完成应答策划</b>
      <span class="ui-pill ${categoriesConfirmed && pageBudgetData?.confirmed ? 'is-ok' : 'is-none'}">${(categoriesConfirmed ? 1 : 0) + (pageBudgetData?.confirmed ? 1 : 0)} / 2 已完成</span>
      <span class="sr-dim">${categoriesConfirmed && pageBudgetData?.confirmed ? '目录与篇幅都已确认' : (categoriesConfirmed ? '还差：确认篇幅' : '还差：确认目录')}</span>
      <span style="flex:1"></span>
    </div>` : ''}
    <div class="sr-dim" data-flash-for="generate"></div>`;

  // ③ 产物
  const productsGate = !categoriesConfirmed ? `
      <div class="sr-empty">请先确认应答目录。
        <div class="sr-dim" style="margin-top:5px">
          在「应答策划」中完成目录校验后，即可按章编写。</div>
      </div>` : (!generated ? `
      <div class="sr-empty">正在生成 / 还没生成目录。
        <div class="sr-dim" style="margin-top:5px">
          已确认：${escapeHtml(viewLabel)} · ${(confirmedCats || []).join('、')}。
          请返回「应答策划」生成目录草案。</div>
      </div>` : '');

  // ── 阶段内的子页签内容 ──
  // 评分点 = 商务 / 技术 /（报价）/ 技术规范书要求；产物 = 目录 / 映射矩阵
  const reviewSubs = [
    { id: 'commerce', label: '商务评分点', n: commerceStat?.count ?? '待核对' },
    { id: 'tech', label: '技术评分点', n: techStat?.count ?? '待核对' },
    priceStat ? { id: 'price', label: '报价评分点', n: priceStat.count } : null,
    { id: 'spec', label: '技术规范书要求', n: specCount },
  ].filter(Boolean);
  const specHtml = (specCount || tm.topics?.length) ? `
      <div class="spec-attention-filter"><button type="button" data-attention-filter="all" aria-pressed="true">全部要求 ${specCount}</button>${Object.entries({veto:'否决风险',substantive:'实质性要求',marked:'原文标记',check:'待核对',proof:'须提供证明'}).map(([key,label])=>`<button type="button" data-attention-filter="${key}" aria-pressed="false">${label} ${(tm.topics || []).reduce((n,t)=>n+(t.requirements || []).filter(r=>r.attention?.flags?.includes(key)).length,0)}</button>`).join('')}</div>
      <div class="spec-symbol-guide" id="spec-symbol-guide" hidden></div>
      <div class="spec-overview"><span>原文目录 · ${specCount} 条完整要求</span>
        <details class="spec-sources"><summary>来源文件 · ${(tm.source_files || []).length} 份</summary>${
          (tm.source_files || []).map(f => `<p>${escapeHtml(f)}</p>`).join('')}</details></div>
      <div class="spec-topics">${specRows}</div>` : notAnalyzedHint('技术规范书要求');
  const reviewPanes = {
    commerce: catTable(commerceStat, '商务'),
    tech: catTable(techStat, '技术'),
    price: catTable(priceStat, '报价'),
    spec: specHtml,
  };
  // 产物子页签的角标：**没生成就不显示数字**。截图里"目录 134 / 映射 8"和
  // "产物还没到出现的时候"并列，数字来自上一次运行，会让人以为已经生成好了。
  const prodSubs = [
    { id: 'writing', label: '正文编写', n: '' },
    { id: 'directory', label: '目录总览', n: generated ? structBlocks.length : '—' },
    { id: 'map', label: '按评分点查找',
      n: generated ? (mp.point_to_requirement || []).length : '—' },
  ];
  const prodPanes = {
    writing: productsGate || '<div id="sr-write-body"><div class="sr-note">正在加载章节…</div></div>',
    directory: productsGate || `
      <div class="sr-answer"><strong>${responsePlan.type === 'commerce' ? '商务应答' : '技术应答'}目录 · ${structBlocks.length} 个目录节点</strong><span class="sr-dim">点击章节进入编写</span></div>
      ${responseWarningsHtml}<div class="sr-struct">${structBlocks.map(b=>`<div class="response-map-row" style="padding-left:${10+(Math.max(1,Number(b.level)||1)-1)*16}px"><button class="mw-btn" data-write-chapter="${escapeHtml(String(b.block_id || b.id))}">${escapeHtml(b.title)}</button></div>`).join('')}</div>`,
    map: productsGate || `<table class="sr-table"><thead><tr><th>评分点</th><th>分值</th><th>承接章节 · 点击编写</th></tr></thead><tbody>${(mp.point_to_requirement || []).map(p=>`<tr><td>${escapeHtml(p.item || p.factor)}</td><td>${p.points ?? '—'}</td><td>${structBlocks.filter(b=>parseIds(b.review_point_ids).map(String).includes(String(p.point_id))).map(b=>`<button class="mw-btn" data-write-chapter="${escapeHtml(String(b.block_id || b.id))}">${escapeHtml(b.title)}</button>`).join(' ') || '待补充'}</td></tr>`).join('')}</tbody></table>`,
  };
  // 阶段状态（默认 ① 评分点）：点左侧竖条切换，子页签在阶段内切换
  const STAGES = [
    { id: '1', label: '解析依据', sub: '评分点 · 技术规范' },
    { id: '2', label: '应答策划', sub: '选类型 · 检查目录' },
    // ④⑤ **也在这个面板里**（用户强调的布局约定：左侧五步，点每一步在右侧展示对应内容，
    // 不做整页跳转）。编写面板＝章节列表 + 章头(本章回应) + 正文 + 按需素材；
    // 评审面板＝评分点 verdict（得分预估 / 缺口 / 举证）+ 章节下钻。
    { id: '4', label: '应答编写', sub: '正文 · 目录 · 依据' },
    { id: '5', label: '评审与交付', sub: '检查缺口 · 导出应答' },
  ];
  const stageState = (st) => {
    if (st === '4') return !categoriesConfirmed ? 'gated' : (writeStats.total && writeStats.complete === writeStats.total ? 'done' : '');
    if (st === '5') return reviewSummary.points_total && !reviewSummary.unreviewed ? 'done' : '';
    if (st === '2') return analyzedAll ? (categoriesConfirmed ? 'done' : '') : 'gated';
    return 'done';
  };
  // 阶段内容头 = **该阶段的实时状态**；左竖条 = 阶段定位（名字 + 一句概述）。
  // 两者刻意不重复：以前内容头把竖条那两行原样抄一遍，读起来像同一句话说两次。
  const stageHint = (st) => {
    if (st === '1') {
      // 用与子页签同一份口径（commerceStat/techStat/specCount），不另算一遍
      const parts = [];
      if (commerceStat?.count) parts.push(`商务评分点 ${commerceStat.count}`);
      if (techStat?.count) parts.push(`技术评分点 ${techStat.count}`);
      if (specCount) parts.push(`技术规范书要求 ${specCount}`);
      return parts.length ? parts.join(' · ') : '还没有拆分结果';
    }
    if (st === '2') {
      if (!analyzedAll) return '请先完成评分点拆分';
      // 0 项时别写"待确认 0 项"（绕口）：直接说可以往下走了
      return responsePlan.type ? `${responsePlan.type === 'commerce' ? '商务应答' : '技术应答'} · ${categoriesConfirmed ? '已确认' : '检查目录草案'}` : '选择应答类型，生成目录草案';
    }
    if (st === '4') {
      return totalBlocks ? `${structBlocks.length} 个目录节点 · 按章编写` : '等目录生成后再撰写';
    }
    return reviewSummary.points_total
      ? `得分预估 ${reviewSummary.score_estimated ?? 0} / ${reviewSummary.score_max ?? 0} · 缺口 ${reviewSummary.gaps ?? 0} 项`
      : '等生成正文后按评分点评审';
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
      <!-- 头行已按用户要求删除（阶段说明与左竖条重复）。
           原来的运行状态（"正在准备…"）挪到筛选行右侧，空着时不占位。 -->
      <div class="sr-filter">
        <div class="sr-scope-summary"><span class="sr-scope-label">当前标包</span>
          <span class="sr-scope-name" title="${escapeHtml(activePackage?.display_name || viewLabel)}">${escapeHtml(activePackage?.display_name || viewLabel)}</span></div>
        <a class="sr-scope-manage" href="/project-intake?projectId=${projectId}">范围与依据 <span aria-hidden="true">↗</span></a>
        <span class="sr-run" id="sr-run-state"></span>
      </div>
      ${flashNow ? `<div class="sr-flash ${flashKind === 'err' ? 'err' : ''}">${escapeHtml(flashNow)}</div>` : ''}
      ${confirmedPkg ? `<div class="sr-body">
        <nav class="sr-rail">
          ${STAGES.map((x, index) => `
            <div class="sr-rail-item ${stageState(x.id)}" data-stage="${x.id}"
              ${x.href ? 'data-nav="1" title="切到该视图"' : ''}>
              <span class="sr-dot">${index + 1}</span>
              <span><span class="sr-lab">${x.label}</span><span class="sr-sub">${x.sub}</span></span>
            </div>`).join('')}
        </nav>
        <section class="sr-stack">
          <div class="sr-stage" data-stage="1">
            <div class="sr-stage-head"><span class="t">评分点</span>
              <span class="hint">当前标包的评分标准与技术要求</span></div>
            <div class="sr-stage-body">
              ${!viewPkgStat ? '<div class="sr-note">当前标包与评分表的对应关系尚未明确，请核对投标范围。</div>' : ''}
              <div class="sr-subtabs sr-points-tabs">${reviewSubs.map(x => `
                <span class="sr-subtab" data-sub="${x.id}">${x.label}<span class="n">${x.n}</span></span>`).join('')}</div>
              ${reviewSubs.map(x => `
                <div class="sr-subpane" data-sub="${x.id}">${reviewPanes[x.id] || ''}</div>`).join('')}
            </div>
          </div>
          <div class="sr-stage" data-stage="2">
            <div class="sr-stage-head"><span class="t">应答策划</span>
              <span class="hint">${stageHint('2')}</span></div>
            <div class="sr-stage-body">${confirmPane}</div>
          </div>
          <div class="sr-stage" data-stage="4">
            <div class="sr-stage-head"><span class="t">应答编写</span><span class="hint">${stageHint('4')}</span>
              <div class="sr-subtabs sr-subtabs--head">${prodSubs.map(x => `<span class="sr-subtab" data-sub="${x.id}">${x.label}<span class="n">${x.n}</span></span>`).join('')}</div>
            </div>
            <div class="sr-stage-body">
              ${prodSubs.map(x => `<div class="sr-subpane" data-sub="${x.id}">${prodPanes[x.id] || ''}</div>`).join('')}
            </div>
          </div>
          <!-- ⑤ 评审：评分点 verdict（得分预估 / 缺口 / 举证）+ 章节下钻 -->
          <div class="sr-stage" data-stage="5">
            <div class="sr-stage-head"><span class="t">评审与交付</span>
              <span class="hint">${stageHint('5')}</span>
              <button type="button" class="mw-btn" data-quality-open title="按规范/计划/依据/素材四组列出问题">质检清单</button></div>
            <div class="sr-stage-body"><div class="sr-answer"><strong>导出当前应答</strong><button class="mw-btn" data-response-download>导出 Word 草稿</button><span class="sr-dim">仅导出已保存正文；未编写章节仍为空</span></div><div data-document-export-status role="status"></div></div>
            <div class="sr-stage-body" id="sr-review-body">
              <div class="sr-note">正在加载评审结论…</div>
            </div>
          </div>
        </section>
      </div>` : `<div class="sr-gate">
        <b>先确认标包</b>
        <div class="sr-dim">在上方选好标包并点「确认标包」，四个步骤（解析依据 → 应答策划 → 应答编写 → 评审与交付）
          会针对该标包展开；每个标包的目录、正文与评审各自独立。</div>
      </div>`}
    </div>`;

  if (!document.getElementById('sr-style')) {
    const st = document.createElement('style');
    st.id = 'sr-style';
    st.textContent = `
  /* 内联展开（不再是浮层）：占满目录栏宽度，确认后收起露出目录 */
  /* 上边距交给 .blocks-scroll 的内边距（原来 8+20+材料条 10 = 38px，太松） */
  #split-panel-inline{margin:0 12px 12px}
  .sr-box{background:var(--surface,#fff);border:1px solid var(--line);border-radius:10px;
    display:flex;flex-direction:column;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.06)}
  .sr-head{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line)}
  .sr-head b{font-size:14px}
  .sr-step{font-size:11.5px;color:var(--muted);padding:1px 8px;border-radius:999px;border:1px solid var(--line)}
  .sr-step.on{background:#e0edff;border-color:#bfdbfe;color:#1d4ed8;font-weight:600}
  /* 内联展开时用视口高度的一半多，一屏内看完大部分表格；超出则面板内部滚动 */
  .sr-panes{overflow:hidden;display:flex;max-height:58vh}
  .sr-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
  .sr-table th{position:sticky;top:0;background:var(--surface,#fff);z-index:1;white-space:nowrap;
    color:var(--muted);font-weight:500;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  .sr-table td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  .sr-table tr:hover td{background:var(--surface-2,#faf9f7)}
  .sr-table td.sr-num,.sr-table th.sr-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .sr-unit td{background:var(--surface-2,#faf9f7);font-weight:600}
  .sr-sub td{color:var(--muted)}
  /* 评分口径/要求原文：**完整展示**，允许换行；不再靠 title 提示看全文。
     给足宽度（表格里最长的一列），并允许长串折断，避免撑破面板。 */
  .sr-crit{color:var(--muted);line-height:1.55;white-space:normal;word-break:break-word;
    min-width:240px}
  .sr-table td.sr-crit{max-width:none}
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
  .sr-flash{margin:8px 15px;padding:7px 11px;border-radius:7px;font-size:12.5px;
    background:#ecfdf5;border:1px solid #a7f3d0;color:#047857}
  /* ── ④编写 / ⑤评审：沿用同一套面板容器与 token ── */
  .sr-kpis{display:flex;gap:8px;flex-wrap:wrap;padding:10px 15px 4px}
  .sr-kpi{flex:1 1 140px;border:1px solid var(--line);border-radius:9px;padding:7px 10px;background:var(--panel,#fff);
    display:flex;flex-direction:column;gap:1px}
  .sr-kpi span{font-size:11.5px;color:var(--muted)}
  .sr-kpi strong{font-size:16px}
  .sr-kpi em{font-size:11px;color:var(--muted);font-style:normal}
  .sr-kpi.ok{border-color:var(--success-soft);background:var(--success-soft)}
  .sr-kpi.warn{border-color:var(--warn-soft);background:var(--warn-soft)}
  .sr-vlist{padding:6px 15px 12px}
  .sr-vrow{border:1px solid var(--line);border-radius:9px;background:var(--panel,#fff);margin-bottom:6px}
  .sr-vrow>summary{display:flex;align-items:center;gap:10px;padding:7px 10px;cursor:pointer;font-size:12.5px}
  .sr-vrow>summary:hover{background:var(--panel-soft,#fafbfc)}
  .sr-vpoint{flex:1;font-weight:600;min-width:0}
  .sr-vpoint em{font-style:normal;color:var(--muted);font-weight:400;font-size:11.5px;margin-left:6px}
  .sr-vnum{flex:0 0 52px;color:var(--muted)}
  .sr-vmiss{flex:1.4;color:var(--danger);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sr-vest{flex:0 0 58px;text-align:right;font-weight:600}
  .sr-vpill{flex:0 0 auto;font-size:11.5px;border-radius:999px;padding:0 7px;border:1px solid var(--line);white-space:nowrap}
  .sr-vpill.ok{color:var(--success);background:var(--success-soft);border-color:var(--success-soft)}
  .sr-vpill.part{color:var(--warn);background:var(--warn-soft);border-color:var(--warn-soft)}
  .sr-vpill.bad{color:var(--danger);background:var(--danger-soft);border-color:var(--danger-soft)}
  .sr-vpill.none{color:var(--muted)}
  .sr-head-actions{margin-left:auto;display:flex;gap:6px;align-items:center}
  .sr-redo{position:relative}
  .sr-redo>summary{list-style:none;cursor:pointer}
  .sr-redo>summary::-webkit-details-marker{display:none}
  .sr-redo-menu{position:absolute;right:0;top:calc(100% + 4px);z-index:30;display:flex;
    flex-direction:column;gap:4px;padding:6px;background:var(--panel,#fff);
    border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px rgba(15,23,42,.12);min-width:230px}
  .sr-redo-menu .mw-btn{justify-content:flex-start;text-align:left;white-space:nowrap}
  .sr-head-actions .mw-btn{font-size:12px}
  .sr-run{margin-left:auto;font-size:11.5px;color:var(--muted)}
  .sr-run:empty{display:none}
  .sr-flash.err{background:#fef2f2;border-color:#fca5a5;color:#b91c1c}
  .sr-inline-err{color:#b91c1c}
  /* 目录区顶部工具条：只放入口与投标范围（覆盖率模块已按用户要求删除） */
  .pp-pkg{border:1px solid var(--line);border-radius:9px;margin:8px 0;background:var(--surface-2,#faf9f7)}
  .pp-pkg>summary{cursor:pointer;padding:8px 10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .pp-pkg>summary::marker{color:var(--muted)}
  .pp-pkg[open]>summary{border-bottom:1px solid var(--line)}
  .pp-pkg .pp-name{color:var(--muted);font-size:12px}
  .pp-pkg .sr-table{margin:0}
  /* 筛选条：**最顶层**，下面所有内容（指标→页签→明细）都由它派生 */
  /* ── 主体：左侧竖条三阶段 + 右侧当前阶段内容（点击切换，不展开/收起）── */
  /* 重新拆分 / 首次拆分期间的加载态（替代旧内容，避免"旧数据还挂着"） */
  .sr-loading{display:flex;align-items:center;gap:10px;padding:22px 18px;flex-wrap:wrap}
  .sr-loading>span:nth-child(2){font-size:13px;font-weight:600}
  .sr-spinner{width:14px;height:14px;border-radius:50%;border:2px solid #bfdbfe;
    border-top-color:#2563eb;animation:sr-spin .8s linear infinite;display:inline-block}
  @keyframes sr-spin{to{transform:rotate(360deg)}}
  /* 不再固定 520px 高：产物未生成时内容只有两行，固定高度会留一大片空白（用户反馈） */
  .sr-body{display:flex;align-items:stretch;min-height:0}
  .sr-rail{flex:0 0 172px;border-right:1px solid var(--line);background:#fafbfc;padding:12px 0;
    align-self:stretch}
  /* 顶层五阶段与嵌套两步**共用同一套导航规则**，以后改一处两级都跟着走 */
  .sr-rail-item,.sr-sub2-item{position:relative;display:flex;align-items:center;gap:9px;
    padding:10px 14px;cursor:pointer}
  .sr-rail-item + .sr-rail-item,.sr-sub2-item + .sr-sub2-item{margin-top:2px}
  .sr-dot{flex:0 0 20px;height:20px;border-radius:50%;border:1.5px solid var(--line);background:#fff;
    color:var(--muted);font-size:11.5px;display:flex;align-items:center;justify-content:center;font-weight:600}
  .sr-lab{font-size:13px}
  .sr-sub{display:block;font-size:11px;color:var(--muted);margin-top:1px}
  /* 连接线：从**圆点下沿**开始（圆点在行内垂直居中，行高随副标题行数变化 ——
     写死 top 会在两行标签时穿进圆圈里，实测过）。用 calc(50% + 12px)：圆点 20px 高，
     中线 + 10px 是下沿，再留 2px 间距。 */
  .sr-rail-item:not(:last-of-type)::after,.sr-sub2-item:not(:last-of-type)::after{content:"";
    position:absolute;left:23px;top:calc(50% + 12px);bottom:-14px;width:1.5px;background:var(--line)}
  .sr-rail-item.done .sr-dot,.sr-sub2-item.done .sr-dot{background:#ecfdf5;border-color:#a7f3d0;color:#047857}
  .sr-rail-item.done:not(:last-of-type)::after{background:#a7f3d0}
  .sr-rail-item.on,.sr-sub2-item.on{background:#eff6ff}
  .sr-rail-item.on .sr-dot,.sr-sub2-item.on .sr-dot{background:#2563eb;border-color:#2563eb;color:#fff}
  .sr-rail-item.on .sr-lab,.sr-sub2-item.on .sr-lab{font-weight:600;color:#2563eb}
  .sr-rail-item.gated .sr-dot{border-style:dashed}
  .sr-stack{flex:1;min-width:0;background:#fcfcfd}
  .sr-stage{display:none}
  .sr-stage.on{display:block}
  .sr-stage-head{display:flex;align-items:center;gap:10px;padding:6px 15px;
    border-bottom:1px solid var(--line2,#eef1f5);background:#fff}
  .sr-stage-head .t{font-weight:600;font-size:13.5px}
  .sr-stage-head .hint{color:var(--muted);font-size:11.5px}
  .sr-stage-body{padding:6px 15px 12px}
  .sr-subtabs{display:flex;gap:4px;margin:0 0 8px;flex-wrap:wrap}
  .sr-subtabs--head{margin:0 0 0 auto;flex-wrap:nowrap}
  .sr-subtabs--head .sr-subtab{padding:3px 10px;font-size:12px}
  .sr-subtab{font-size:12.5px;padding:4px 11px;border-radius:999px;border:1px solid var(--line);
    background:#fff;color:var(--muted);cursor:pointer}
  .sr-subtab.on{background:#eff6ff;border-color:#bfdbfe;color:#2563eb;font-weight:600}
  .sr-subtab .n{color:var(--muted);font-weight:400;margin-left:4px}
  .sr-points-tabs{gap:20px;margin:0 0 16px;border-bottom:1px solid var(--line);padding:0 2px}
  .sr-points-tabs .sr-subtab{display:inline-flex;align-items:center;gap:7px;padding:10px 0;border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:var(--muted);white-space:nowrap}
  .sr-points-tabs .sr-subtab.on{background:transparent;border-color:var(--accent,#2563eb);color:var(--accent,#2563eb)}
  .sr-points-tabs .sr-subtab .n{font-size:11px;line-height:18px;min-width:20px;text-align:center;padding:0 5px;margin:0;border-radius:5px;background:#edf0f4;font-variant-numeric:tabular-nums}
  .sr-points-tabs .sr-subtab.on .n{background:#e8f0ff;color:#2563eb}
  .sr-points-meta{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 12px;font-size:12px;color:var(--muted)}
  .sr-points-total{font-size:12px;color:var(--text);font-variant-numeric:tabular-nums}
  .sr-points-total b{font-size:15px;font-weight:600;margin:0 3px}
  .sr-subpane{display:none}
  .sr-subpane.on{display:block}
  .sr-dir-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:4px 0 8px}
  .sr-dir-pill{font-size:11.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);background:#f8fafc;color:var(--muted)}
  .sr-dir-pill.is-ok{background:#ecfdf5;border-color:#bbf7d0;color:#15803d}
  .sr-dir-warn{font-size:12px;color:#b45309}
  .sr-dir-foot{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:14px;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:#fbfdff;font-size:12.5px}
  .sr-dir-foot>b{font-size:13.5px}
  .sr-dir-foot>button{margin-left:0}
  .sr-dir-more{font-size:12px}
  .sr-dir-more>summary{cursor:pointer;color:var(--muted)}
  .sr-dir-more>div{margin-top:4px;line-height:1.7}
  /* 篇幅区复用页面原生 .response-confirm-bar（含实心蓝主按钮）与 .ui-pill，这里只做排布微调 */
  .response-confirm-bar [data-page-budget-bar] strong{display:flex;align-items:center;gap:8px}
  [data-page-budget-bar] .mw-inline{display:inline-flex;gap:8px;margin-top:6px}
  [data-step-slot]{display:inline-flex;gap:8px}
  /* 篇幅条右侧控制区：与「第 1 步」条的主按钮同位（统一形态） */
  .response-confirm-bar .sr-budget-ctl{display:inline-flex!important;align-items:center;gap:8px;flex-wrap:nowrap;justify-content:flex-end;white-space:nowrap;margin-top:0}
  .sr-budget-total{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--muted)}
  .sr-budget-total input{width:64px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;font:inherit;text-align:right}
  #sr-page-check{font-size:12px;color:#15803d}
  #sr-page-check.sr-page-warn{color:#b45309}
  [data-page-budget-bar] input{font:inherit;font-size:12px;width:76px;padding:4px 8px;border:1px solid var(--line);border-radius:6px;text-align:right}
  [data-page-budget-bar] b{font-variant-numeric:tabular-nums}
  [data-page-budget-bar] .sr-page-warn{color:var(--warn)}
  [data-page-budget-bar] .ui-pill{margin-left:0}
  /* 页面原生 :disabled 是 cursor:wait（等待中）；这里语义是"前置未完成"，改回 not-allowed */
  [data-page-budget-bar]>button:disabled{cursor:not-allowed}
  .sr-dir-head .ui-pill{margin:0}
  /* 三步依赖链：① 目录 → ② 篇幅 → ③ 进入编写（结构 → 属性 → 放行） */
  /* 两步导航：可点击切换（当前步高亮、已完成打勾、未解锁禁用） */
  .sr-dir-chain{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--muted)}
  .sr-dir-chain i{font-style:normal;color:#cbd5e1}
  .sr-step{font:inherit;font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:#f8fafc;color:var(--muted);cursor:pointer}
  .sr-step.is-now{border-color:#bfdbfe;background:#eff6ff;color:#1d4ed8;font-weight:600}
  .sr-step.is-done{border-color:#bbf7d0;background:#ecfdf5;color:#15803d}
  .sr-step.is-todo{opacity:.55}
  .sr-step:disabled{cursor:not-allowed}
  .response-confirm-bar.is-compact{padding:10px 14px;margin-top:8px}
  /* 类型卡上的目录状态：已生成/未生成/正在查看（类型卡=切换器） */
  .response-type-state{display:block;margin-top:6px;font-size:11.5px;color:var(--muted);font-style:normal}
  .response-type-state.is-ok{color:#15803d}
  .response-type-state.is-none{color:#b45309}
  .response-type.is-current{border-color:#bbf7d0;background:#f6fffa}
  /* 收尾栏的主按钮沿用应用原生样式（padding:10px 16px!important，高 ~40px），
     与上面两行 pill 的视觉重量不一致，这里对齐成与 .mw-btn 同尺寸 */
  /* 提高特异性：应用的 .response-confirm-bar>button.primary 用 !important 设了 10px 16px，
     同权重下按顺序会赢，这里用 body 前缀提权 */
  body .sr-dir-foot>button.primary{padding:8px 14px!important}
  .sr-lvl{font-size:10.5px;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:0 5px;line-height:16px;flex:none}
  .sr-erow-page{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;color:var(--muted);margin-left:4px;white-space:nowrap}
  .sr-erow-page input{font:inherit;font-size:12px;width:58px;padding:4px 8px;border:1px solid var(--line);border-radius:6px;text-align:right;font-variant-numeric:tabular-nums}
  .sr-erow-page.is-muted{opacity:.5}
  .sr-erow-page.is-sum{color:var(--text);font-variant-numeric:tabular-nums}
  .sr-page-pinned{font-style:normal;font-size:10.5px;padding:1px 5px;border-radius:4px;background:#ecfdf5;color:#15803d}
  .sr-erow.is-source .sr-erow-acts button{display:none}.sr-erow.is-source .sr-grip{visibility:hidden;pointer-events:none}.sr-erow.is-source{background:#fafbfc}
  .response-chapter-evidence{padding:12px;margin-top:10px;border:1px solid var(--line);border-radius:6px;font-size:12px;line-height:1.7}.response-evidence-item{padding:10px 0;border-top:1px solid var(--line)}.response-evidence-item small{display:block;color:var(--muted);margin-bottom:5px}
  .response-landing-warning{font-size:12px;line-height:1.7;padding:10px 12px;margin:8px 0;border:1px solid #fde68a;border-radius:6px;background:#fffbeb;color:#92400e}.response-landing-warning summary{cursor:pointer}.response-landing-warning ul{max-height:180px;overflow:auto;padding-left:20px}.response-landing-warning p{margin:6px 0}
  .response-confirm-bar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px;background:#f8fafc;border:1px solid var(--line);border-radius:8px;margin-top:12px}.response-confirm-bar strong{display:block;font-size:13px}.response-confirm-bar span{display:block;font-size:12px;color:var(--muted);margin-top:5px}.response-confirm-bar>button.primary{background:#2563eb!important;color:#fff!important;border-color:#2563eb!important;padding:10px 16px!important;white-space:nowrap}.response-confirm-bar>button:disabled{opacity:.65;cursor:wait}.response-confirm-feedback{padding:12px 16px;margin-top:8px;border-radius:6px;background:#eff6ff;color:#1e40af;font-size:12px;line-height:1.7}.response-confirm-feedback[hidden]{display:none}.response-confirm-feedback.is-error{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412}.response-confirm-feedback p{margin:5px 0 8px}.response-confirm-feedback ul{max-height:180px;overflow:auto;margin:6px 0 12px;padding-left:20px}.response-confirm-feedback summary{cursor:pointer}@media(max-width:600px){.response-confirm-bar{align-items:stretch;flex-direction:column}.response-confirm-bar>button{width:100%}}
  .response-basis-card{position:relative;border:1px solid var(--line);border-radius:8px;background:#fff;margin:0 0 16px;overflow:hidden}.response-basis-top{display:flex;gap:16px;align-items:center;justify-content:space-between;padding:16px 190px 16px 16px}.response-basis-main{min-width:0}.response-basis-main>strong{display:block;font-size:12px;color:var(--muted);margin-bottom:8px}.response-basis-metrics{display:flex;gap:8px 20px;flex-wrap:wrap;font-size:13px}.response-basis-metrics b{font-size:17px;font-variant-numeric:tabular-nums;margin:0 3px}.response-basis-main>p{font-size:12px;color:var(--muted);line-height:1.6;margin:8px 0 0}.response-basis-action{flex:none;white-space:nowrap!important;padding:9px 14px!important}.response-basis-details{border-top:0;font-size:12px;color:var(--muted)}.response-basis-details>summary{position:absolute;right:16px;top:30px;padding:8px 0;cursor:pointer;background:transparent}.response-basis-footer{padding:10px 16px;border-top:1px solid var(--line);background:#fafbfc}.response-basis-details[open] .response-basis-detail-body{border-top:1px solid var(--line)}.response-basis-detail-body{padding:0 16px 12px;line-height:1.7}.response-basis-detail-body p{margin:10px 0 0}.response-source-file{display:flex;align-items:baseline;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}.response-source-file>span{flex:none;font-size:11px}.response-source-file>p{margin:0;overflow-wrap:anywhere;min-width:0}@media(max-width:600px){.response-basis-top{align-items:flex-start;flex-direction:column;padding:12px}.response-basis-details>summary{position:static;padding:0 12px 10px}.response-basis-action{align-self:flex-start}.response-basis-metrics{gap:6px 12px}}
  .response-type-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:16px}.response-type{font:inherit;text-align:left;padding:14px;border:1px solid var(--line);border-radius:8px;background:#fff;cursor:pointer;color:var(--text)}.response-type.on{border-color:#2563eb;background:#eff6ff;box-shadow:inset 0 0 0 1px #2563eb}.response-type strong{display:block;font-size:14px;margin-bottom:6px}.response-type span{display:block;font-size:12px;color:var(--muted);line-height:1.6}.response-mapping summary,.response-missing summary{cursor:pointer}.response-coverage{display:flex;gap:10px 18px;flex-wrap:wrap;font-size:12px;padding:12px;background:#f8fafc;border:1px solid var(--line);border-radius:6px;margin:12px 0}.response-mapping,.response-missing{font-size:12px;padding:12px 0;line-height:1.7}.response-map-row{display:grid;grid-template-columns:minmax(140px,1fr) 2fr auto;gap:12px;padding:8px;border-bottom:1px solid var(--line)}
  @media(max-width:900px){.sr-body{flex-direction:column}.sr-rail{flex:0 0 auto;display:flex;border-right:0;border-bottom:1px solid var(--line);padding:6px;overflow-x:auto;position:static}.sr-rail-item{flex:1;min-width:max-content;padding:8px}.sr-rail-item+.sr-rail-item{margin-top:0}.sr-rail-item:after{display:none}.sr-rail .sr-desc{display:none}.sr-stage-body{padding:10px}.spec-children{padding-left:8px;margin-left:6px}}
  .sr-gate{padding:28px 20px;display:flex;flex-direction:column;gap:6px;align-items:flex-start}
  .sr-gate b{font-size:13.5px}
  .sr-filter{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
    padding:11px 18px;border-bottom:1px solid var(--line);background:#f8fafc}
  .sr-filter-k{font-weight:600;font-size:12.5px;color:var(--muted)}
  .sr-filter-item{font-size:12.5px;display:flex;align-items:center;gap:6px}
  .sr-scope-summary{display:flex;align-items:center;gap:10px;flex:1 1 240px;min-width:0}
  .sr-scope-label{flex:none;font-size:11px;color:var(--muted);padding:3px 7px;border:1px solid var(--line);border-radius:5px;background:#fff}
  .sr-scope-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--text);min-width:0}
  .sr-scope-manage{display:inline-flex;align-items:center;gap:6px;flex:none;font-size:12px;font-weight:400;color:var(--muted);text-decoration:none;padding:6px 9px;border:1px solid transparent;border-radius:6px;transition:background .15s,color .15s}
  .sr-scope-manage:hover{color:var(--accent,#2563eb);background:#eff6ff;border-color:#dbeafe}
  .sr-scope-manage:focus-visible{outline:2px solid var(--accent,#2563eb);outline-offset:2px}
  .sr-run:empty{display:none}
  .sr-filter select{font:inherit;font-size:12.5px;padding:4px 8px;border:1px solid var(--line);
    border-radius:7px;background:#fff;max-width:330px}
  .sr-cat{display:flex;align-items:center;gap:5px;font-size:12.5px;cursor:pointer;
    border:1px solid var(--line);border-radius:7px;padding:3px 8px;background:#fff}
  .sr-cat input{accent-color:#2563eb}
  .sr-sec{margin:14px 0 4px;font-size:12.5px;font-weight:600;color:var(--text)}
  /* 评分口径的结构化卡片：编号条件成列表，分值单独成标签，原文可展开 */
  .pc-card{border:1px solid var(--line);border-radius:9px;margin:7px 0;background:#fff}
  .pc-head{display:flex;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid var(--line);
    background:var(--surface-2,#faf9f7);border-radius:9px 9px 0 0}
  .pc-factor{font-size:11.5px;color:#1d4ed8;background:#eff6ff;border:1px solid #bfdbfe;
    border-radius:999px;padding:1px 7px;white-space:nowrap}
  .pc-item{font-weight:600;font-size:12.5px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pc-score{font-weight:600;font-size:12.5px;color:#047857;white-space:nowrap}
  .pc-list{margin:6px 0 4px;padding-left:22px}
  .pc-list li{margin:3px 0;font-size:12.5px;color:var(--text);line-height:1.55;
    display:flex;gap:8px;align-items:flex-start}
  .pc-text{flex:1;word-break:break-word}
  .pc-band{flex:0 0 auto;font-size:11.5px;color:#047857;background:#ecfdf5;border:1px solid #a7f3d0;
    border-radius:999px;padding:0 7px;white-space:nowrap}
  .pc-band.cap{color:#92400e;background:#fffbeb;border-color:#fcd34d}
  .pc-body{padding:8px 10px;font-size:12.5px;color:var(--muted);line-height:1.6;word-break:break-word}
  .sr-scope-chip{font-size:11.5px;font-weight:400;color:#1d4ed8;background:#eff6ff;
    border:1px solid #bfdbfe;border-radius:999px;padding:1px 8px;margin-left:4px}
  .sr-topic-list{max-height:38vh;overflow:auto;border:1px solid var(--line);border-radius:8px;
    padding:6px 8px;margin-top:6px;background:var(--surface-2,#faf9f7)}
  .sr-topic-row{display:flex;align-items:center;gap:10px;padding:3px 0}
  .sr-topic-name{flex:0 0 46%;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sr-topic-row select{flex:1;font:inherit;font-size:12.5px;padding:2px 6px;
    border:1px solid var(--line);border-radius:6px;background:#fff}
  #sr-write-body{margin-top:0}
  .cs-document-head{display:none}   /* 交付格式块已隐藏，容器不再占 25px 空白 */
  #sr-write-body .cs-workspace{margin-top:0}
  .sr-note{margin-top:10px;color:var(--muted);font-size:11.5px;line-height:1.6}`;
    document.head.appendChild(st);
  }
  // 面板始终展示（用户要求：不要"展开/收起"这类操作）；滚动位置回到顶部，
  // 让"筛选 → 指标 → 页签 → 明细"从上到下完整可见。
  const scroller = el.closest('#blocks-scroll') || el.closest('.blocks-scroll');
  if (scroller) scroller.scrollTop = 0;

  // ── 左侧竖条切换阶段 + 阶段内子页签（用户要求：点导航直接切换，不做展开/收起）──
  el.querySelectorAll('[data-attention-filter]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.attentionFilter;
    const guide=el.querySelector('#spec-symbol-guide');
    if(guide){
      const symbols=[...new Set((tm.topics || []).flatMap(t=>(t.requirements || []).flatMap(r=>r.attention?.markers || [])))];
      const meanings={'★':'常见为实质性要求','*':'重要条款或脚注','＊':'重要条款或脚注','▲':'常见为重要参数、重点评分项'};
      guide.hidden=key!=='marked' || !symbols.length;
      guide.innerHTML=symbols.map(symbol=>`<span><b>${escapeHtml(symbol)}</b> ${escapeHtml(meanings[symbol] || '含义待核对')}</span>`).join('')
        + '<span class="spec-symbol-caution">具体含义及否决、扣分后果，以本文件说明为准</span>';
    }
    el.querySelectorAll('[data-attention-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    el.querySelectorAll('.spec-topic').forEach(topic=>{
      let visible=0;
      topic.querySelectorAll('.spec-clause').forEach(row=>{row.hidden=key!=='all'&&!row.dataset.attention.split(' ').includes(key);if(!row.hidden)visible++;});
      topic.hidden=key!=='all'&&!visible;if(visible&&key!=='all')topic.open=true;
    });
  });
  const applyStage = () => {
    el.querySelectorAll('.sr-stage').forEach(p =>
      p.classList.toggle('on', p.dataset.stage === String(panelStage)));
    el.querySelectorAll('.sr-rail-item').forEach(r =>
      r.classList.toggle('on', r.dataset.stage === String(panelStage)));
  };
  const applySub = () => {
    const stage = el.querySelector(`.sr-stage[data-stage="${panelStage}"]`);
    if (!stage) return;
    const subs = [...stage.querySelectorAll('.sr-subpane')].map(p => p.dataset.sub);
    if (!subs.includes(panelSub)) panelSub = subs[0] || '';
    stage.querySelectorAll('.sr-subpane').forEach(p =>
      p.classList.toggle('on', p.dataset.sub === panelSub));
    stage.querySelectorAll('.sr-subtab').forEach(t =>
      t.classList.toggle('on', t.dataset.sub === panelSub));
  };
  // 「确认标包」：写范围 + 把当前运行归属到该标包 → 五阶段针对它展开。
  // 该标包还没有运行 → 新建一条（带 target/package_no），后端只清**同标包**的旧数据。
  el.querySelector('#sr-confirm-pkg')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const sel = document.getElementById('sr-view-pkg');
    const [target, package_no] = String(sel?.value || '').split('|');
    if (!target && !package_no) return;
    const stat = document.getElementById('sr-pkg-state');
    btn.disabled = true;
    if (stat) stat.textContent = '正在确认…';
    try {
      const row = pkgs.find((x) => String(x.package_no || '') === String(package_no)
        && String(x.target || '') === String(target)) || {};
      const tidNow = currentThreadId || params.get('threadId') || getThread(projectId);
      // ① 写投标范围（同时把当前运行归属到该标包）
      await api.workflow.setScope(projectId, {
        target, package_no, package_name: row.package_name || '', threadId: tidNow || '',
      });
      // ② 该标包已有运行？有就切过去；没有就为它新建一条（此处带 points_only：先拆评分点）
      const runs = await api.workflow.workflowRuns(projectId);
      // 同一标包会有 1 条解析运行 + 商务/技术各 1 份草案：
      // **优先落在目录草案上**（草案才有目录/篇幅；解析运行只是依据记录），同类型优先。
      const candidates = (runs || []).filter((r) => String(r.target || '') === String(target)
        && String(r.package_no || '') === String(package_no));
      const wantedType = responsePlanChoice || '';
      const same = candidates.find((r) => r.plan_type && r.plan_type === wantedType)
        || candidates.find((r) => r.plan_type)
        || candidates[0];
      let tid = same?.thread_id || '';
      if (!tid) {
        const resp = await api.workflow.start(projectId, {
          target, package_no, points_only: true,
          model: selectedModel, outline_instruction: readOutlineInstruction(),
        });
        tid = resp?.thread_id || '';
        splitPanelFlash = `已为「${row.package_name || `标包${package_no}`}」新建一条作业，正在拆评分点…`;
      } else {
        splitPanelFlash = `已切换到「${row.package_name || `标包${package_no}`}」的作业（该标包的内容相互独立）。`;
      }
      if (tid) {
        currentThreadId = tid;
        setThread(projectId, tid);
        const url = new URL(location.href);
        url.searchParams.set('threadId', tid);
        history.replaceState(null, '', url.toString());
        if (typeof attachStream === 'function') { try { attachStream(tid); } catch { /* 流可选 */ } }
      }
      rememberConfirmedPkg({ target, package_no });
      panelViewPkg = { target, package_no };
      splitPanelAutoOpenedFor = 0;
      // 确认标包后**从第①步开始**（阶段由用户自己往下走，不做自动定位）
      panelStage = '1';
      panelSub = '';
      await openSplitReport({ stage: '1' });
    } catch (err) {
      if (stat) stat.textContent = `确认失败：${String(err?.message || err).slice(0, 80)}`;
      btn.disabled = false;
    }
  });

  el.querySelectorAll('.sr-rail-item').forEach(r => r.addEventListener('click', () => {
    panelStage = r.dataset.stage;
    panelSub = '';                 // 换阶段时子页签回到该阶段的第一个
    applyStage(); applySub();
    hydrateStage(panelStage);      // ④⑤ 按需拉数据（已拉过不重复）
  }));
  el.querySelectorAll('.sr-subtab').forEach(t => t.addEventListener('click', () => {
    panelSub = t.dataset.sub;
    applySub();
  }));
  el.querySelector('[data-act="response-write"]')?.addEventListener('click', () => openSplitReport({stage:'4', sub:'writing'}));
  el.querySelectorAll('[data-write-chapter]').forEach(button => button.addEventListener('click', async () => {
    panelStage = '4'; panelSub = 'writing';
    applyStage(); applySub();
    await renderWritePane();
    await chapterStudio?.select(button.dataset.writeChapter);
  }));
  el.querySelector('[data-act="open-draft"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    const d = draftOfType(responseType);
    if (!d || !d.thread_id) return;
    await switchPanelThread(d.thread_id, '正在打开应答目录…');
  });
  // 类型卡 = **切换器**（用户要求）：点「商务」显示商务应答目录，点「技术」显示/生成技术应答目录。
  // 有 → 切到那条线程；没有 → 以当前线程为来源生成（后端幂等：同标包同类型只保留一份）。
  el.querySelectorAll('[data-response-type]').forEach(button => button.addEventListener('click', async (event) => {
    event.stopPropagation();
    const type = button.dataset.responseType;
    const label = type === 'commerce' ? '商务' : '技术';
    responsePlanChoice = type;
    const draft = draftOfType(type);
    const url = new URL(location.href);
    if (draft && draft.has_outline === false && draft.thread_id) {
      // 草案存在但目录没派生出来（例如被别的解析清空过）→ 直接补派生，再原地切过去。
      const slot0 = el.querySelector('[data-flash-for="generate"]');
      button.disabled = true;
      if (slot0) slot0.textContent = `正在重新派生${label}应答目录…`;
      showPaneBusy(`正在重新派生${label}应答目录…（复用已解析内容）`);
      try {
        const rr = await fetch(`/api/workflow/${encodeURIComponent(draft.thread_id)}/redraft-outline`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const rb = await rr.json();
        if (!rr.ok) throw new Error(rb.detail || '重新派生失败');
      } catch (err) {
        button.disabled = false;
        clearPaneBusy();
        if (slot0) slot0.textContent = err.message || '重新派生失败，请重试';
        return;
      }
      await switchPanelThread(draft.thread_id, `正在打开${label}应答目录…`);
      return;
    }
    if (draft && draft.thread_id && String(draft.thread_id) !== String(tid)) {
      await switchPanelThread(draft.thread_id, `正在切换到${label}应答目录…`);
      return;
    }
    if (draft) { await openSplitReport({stage: '2'}); return; }   // 已在看这一份
    const slot = el.querySelector('[data-flash-for="generate"]');
    button.disabled = true;
    if (slot) slot.textContent = `正在派生${label}应答目录…（复用已解析内容，通常 10-30 秒）`;
    try {
      const resp = await fetch(`/api/workflow/${encodeURIComponent(tid)}/response-plan`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response_type: type }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || '生成目录草案失败');
      await switchPanelThread(body.thread_id, `正在打开${label}应答目录…`);
    } catch (err) {
      button.disabled = false;
      clearPaneBusy();
      if (slot) slot.textContent = err.message || '生成失败，请重试';
    }
  }));
  el.querySelector('[data-act="response-preview"]')?.addEventListener('click', async (event) => {
    if (previewReady) { el.querySelector('.sr-struct-edit')?.scrollIntoView({behavior:'smooth', block:'start'}); return; }
    const button = event.currentTarget;
    const slot = el.querySelector('[data-flash-for="generate"]');
    button.disabled = true;
    slot.textContent = '正在打开独立的应答草案…';
    try {
      const result = await fetch(`/api/workflow/${encodeURIComponent(tid)}/response-plan`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({response_type: responseType}),
      });
      const body = await result.json();
      if (!result.ok) throw new Error(body.detail || '目录草案创建失败');
      const url = new URL(location.href);
      url.searchParams.set('threadId', body.thread_id);
      url.searchParams.set('planning', '1');
      location.assign(url.href);
    } catch (err) { button.disabled = false; slot.textContent = err.message; }
  });
  el.querySelector('[data-quality-open]')?.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('writing-quality:open'));
  });
  el.querySelector('[data-response-download]')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    const slot = el.querySelector('[data-document-export-status]');
    try {
      if (chapterStudio && !await chapterStudio.save()) return;
      const response = await fetch(`/api/projects/${projectId}/studio/document?threadId=${encodeURIComponent(tid)}`);
      const settings = await response.json();
      if (!response.ok) throw new Error(settings.detail || '读取模板失败');
      await exportDocument({projectId, threadId:tid, template:settings.template, slot});
    } catch(error) { slot.textContent = error.message; }
    finally { button.disabled = false; }
  });
  // ── 篇幅：分配 / 清除手工调整 / 确认 / 逐章改页数 ──
  const postPageBudget = async (payload) => {
    const resp = await fetch(`/api/workflow/${encodeURIComponent(tid)}/page-budget`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload), signal: AbortSignal.timeout(15000),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(typeof body.detail === 'string' ? body.detail : '篇幅保存失败，请重试');
    }
    pageBudgetData = body;
    await openSplitReport({stage: '2'});
  };
  const pageFeedback = (message) => {
    const slot = el.querySelector('[data-page-feedback]');
    if (slot) { slot.textContent = message; slot.style.color = '#b45309'; }
  };
  const pageTotal = () => Number(el.querySelector('#sr-page-total')?.value || 0);
  el.querySelectorAll('[data-step-go]').forEach((btn) => btn.addEventListener('click', async (event) => {
    event.stopPropagation();
    planStep = btn.getAttribute('data-step-go');
    await openSplitReport({stage: '2'});
  }));
  el.querySelector('[data-act="page-edit"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    const button = event.currentTarget;
    button.disabled = true;
    try { await postPageBudget({total: pageTotal(), confirm: false}); }
    catch (err) { pageFeedback(err.message); button.disabled = false; }
  });
  el.querySelector('[data-act="page-goto-write"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    await openSplitReport({stage: '4', sub: 'writing'});
    document.querySelector('.sr-stage.on')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
  el.querySelector('[data-act="page-alloc"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    try { await postPageBudget({total: pageTotal(), allocate: true}); }
    catch (err) { pageFeedback(err.message); }
  });
  el.querySelector('[data-act="page-clear"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    try { await postPageBudget({total: pageTotal(), clear_manual: true}); }
    catch (err) { pageFeedback(err.message); }
  });
  el.querySelector('[data-act="page-confirm"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    const button = event.currentTarget;
    const total = pageTotal();
    if (!(Number(total) > 0)) {
      // 用户实测"点确认篇幅没反应"的根因之一：0 页时后端直接 409，而错误无处显示。
      pageFeedback('请先填总页数（例如 120），再点「按篇幅分配」');
      el.querySelector('#sr-page-total')?.focus();
      return;
    }
    button.disabled = true;
    try {
      // 还没分配（合计为 0 / 与需求不一致）→ 先按总篇幅自动分配一次，再确认。
      // 用户意图是"把这个页数定下来"，不该只回一句"还没分配"。
      const needAlloc = !pageBudgetData || !pageBudgetData.ok || Number(pageBudgetData.allocated) === 0;
      if (needAlloc) {
        pageFeedback('正在按总篇幅自动分配…');
        await postPageBudget({total, allocate: true});
      }
      await postPageBudget({total, confirm: true});
      // 两步都确认完成 → 这时才进入编写（与用户要求一致：目录 → 篇幅 → 编辑）
      planStep = null;
      button.textContent = '已确认，正在打开编写…';
      await openSplitReport({stage:'4', sub:'writing'});
      document.querySelector('.sr-stage.on')?.scrollIntoView({behavior:'smooth', block:'start'});
    } catch (err) { pageFeedback(err.message); button.disabled = false; }
  });
  el.querySelectorAll('.sr-page-input').forEach((input) => input.addEventListener('change', async (event) => {
    event.stopPropagation();
    const pages = Math.max(0, Number(input.value) || 0);
    try {
      await postPageBudget({total: pageTotal(), overrides: [{block_id: input.dataset.page, pages, pinned: true}]});
    } catch (err) { pageFeedback(err.message); }
  }));
  // ── 与原型对齐：被合并 / 被替代的块不再重复出现（原型里合并进「目录」段与收尾栏）──
  el.querySelectorAll('.sr-stage.on[data-stage="2"] .sr-stage-body > h5.sr-sec, .sr-stack h5.sr-sec').forEach((h) => {
    if ((h.textContent || '').indexOf('目录草案') === 0) h.hidden = true;   // 已由「目录」段标题替代
  });
  // 隐藏"被合并掉的旧块"。注意：因为要保持 UI 一致，我新增的组件也用了 .response-confirm-bar，
  // 按 class 隐藏会连自己一起藏掉（这个坑踩了三次）。改为**按身份**隐藏：
  // 只有原「确认目录 / 进入编写」那张卡才藏。
  el.querySelectorAll('.response-basis-card, .response-coverage, .response-landing-warning, .response-chapter-evidence, .response-mapping').forEach((node) => { node.hidden = true; });
  el.querySelectorAll('.response-confirm-bar').forEach((node) => {
    if (node.querySelector('[data-act="response-confirm"], [data-act="response-write"]')) node.hidden = true;
  });
  el.querySelectorAll('.sr-dim').forEach((span) => {
    if (span.children.length) return;
    const text = span.textContent || '';
    // 「点一行 → …」与「结构完整 = …」都已并入「? 操作说明与原文依据映射」，不再各占一行
    if (text.indexOf('点一行') >= 0 || text.indexOf('结构完整 =') >= 0) span.hidden = true;
  });
  (function mergeDirHints() {
    const bar = el.querySelector('.sr-answer');
    if (!bar || bar.querySelector('[data-dir-help]')) return;
    const help = document.createElement('details');
    help.setAttribute('data-dir-help', '1');
    help.className = 'sr-dir-help';
    help.style.flexBasis = '100%';
    help.innerHTML = '<summary>? 操作说明与原文依据映射</summary><div class="sr-dim">选择章节，查看对应评分点和技术规范书原文依据；点一行 → 该行出现操作，改动即时保存。<br>结构完整 = 每个评分点都有承载章节；缺的会在上方自检里以 ⚠ 提示。</div>';
    bar.appendChild(help);
  })();
  (function movePreviewButton() {
    // 「查看目录草案 / 生成目录草案」原在编制依据卡里；该卡被合并后必须搬出来，否则功能消失
    const head = el.querySelector('.sr-dir-head');
    const preview = el.querySelector('[data-act="response-preview"]');
    if (!head || !preview) return;
    preview.classList.add('mw-btn');
    head.appendChild(preview);
  })();
  (function moveStepButtons() {
    // 「确认目录」原在被合并的那张卡里 → 搬到第 1 步操作条（目录待确认时用户必须有路可走）
    const confirmBtn = el.querySelector('[data-act="response-confirm"]');
    const slot = el.querySelector('[data-step-slot="1"]');
    if (confirmBtn && slot) { confirmBtn.style.marginLeft = '0'; slot.appendChild(confirmBtn); }
    // 「进入编写」要求两步都完成，放收尾栏（唯一终态主操作）
    const foot = el.querySelector('.sr-dir-foot');
    const writeBtn = el.querySelector('[data-act="response-write"]');
    if (foot && writeBtn) {
      writeBtn.style.marginLeft = '0';
      const ready = !!categoriesConfirmed && !!(pageBudgetData && pageBudgetData.confirmed);
      writeBtn.hidden = !ready;
      if (ready) foot.appendChild(writeBtn);
    }
  })();
  (function prioritizeBudgetStep() {
    // PM 审查（2026-10-16）：②步时篇幅条原本排在第 6 位（要滚过类型卡/编制依据/覆盖检查才看到），
    // 所以②步把篇幅条提到面板最上方，并把只属于①步的卡片收起。
    const step = resolvedStep(!!categoriesConfirmed);
    const bar = el.querySelector('[data-page-budget-bar]');
    if (!bar) return;
    if (step === '2') {
      const body = el.querySelector('.sr-stage-body') || el;
      const head = body.querySelector('.sr-dir-head');
      if (head && head.nextElementSibling !== bar) body.insertBefore(bar, head ? head.nextElementSibling : body.firstChild);
      // 只属于①步（结构编辑）的东西，②步一律收掉：类型卡 / 编制依据 / 覆盖检查 /
      // 批量操作条 / 新增章节·撤销工具（后者按**身份**隐藏，避免误伤同 class 的其它块）
      ['.response-type-options', '.response-basis-card', '.response-coverage', '.sr-batch'].forEach((sel) => {
        el.querySelectorAll(sel).forEach((n) => { n.hidden = true; n.style.display = 'none'; });
      });
      el.querySelectorAll('.sr-answer').forEach((n) => {
        if (n.querySelector('[data-act="struct-new"], [data-act="undo-structure"]')) {
          n.hidden = true; n.style.display = 'none';
        }
      });
      bar.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
  })();
  (function applyStep() {
    // 两步**操作同一个目录**：第 1 步行内是结构操作，第 2 步行内是页数输入
    const step = resolvedStep(!!categoriesConfirmed);
    el.querySelectorAll('[data-step-pane]').forEach((node) => { node.hidden = node.getAttribute('data-step-pane') !== step; });
    el.querySelectorAll('.sr-erow-page').forEach((node) => { node.hidden = step !== '2'; });
    el.querySelectorAll('.sr-erow-acts').forEach((node) => { node.hidden = step !== '1'; });
    el.querySelectorAll('[data-step-go]').forEach((btn) => {
      const target = btn.getAttribute('data-step-go');
      btn.classList.toggle('is-now', target === step);
      btn.setAttribute('aria-current', target === step ? 'step' : 'false');
    });
    // 第 1 步补一句现状说明，避免用户不知道"目录为什么是待确认"
    const head = el.querySelector('.sr-dir-head');
    if (head) head.setAttribute('data-step', step);
  })();
  el.querySelectorAll('[data-act="redraft-outline"]').forEach(button => button.addEventListener('click', async event => {
    event.stopPropagation();
    button.disabled = true;
    try { await redraftOutline(); } finally { button.disabled = false; }
  }));
  el.querySelector('[data-act="response-confirm"]')?.addEventListener('click', async (event) => {
    event.stopPropagation();
    const button = event.currentTarget;
    const slot = el.querySelector('[data-confirm-feedback]');
    const originalLabel = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    button.textContent = '正在保存目录…';
    slot.hidden = false;
    slot.className = 'response-confirm-feedback';
    slot.textContent = '正在等待目录调整保存完成…';
    try {
      let saveTimer;
      try { await Promise.race([structureSaveChain,new Promise((_,reject)=>{saveTimer=setTimeout(()=>reject(new Error('目录仍在保存中，请稍后重试。')),10000);})]); }
      finally { clearTimeout(saveTimer); }
      button.textContent = '正在校验并创建…';
      slot.textContent = '正在检查重点章节、原文章节结构与要求归属…';
      const result = await fetch(`/api/workflow/${encodeURIComponent(tid)}/response-plan/confirm`, {method:'POST',signal:AbortSignal.timeout(20000)});
      const body = await result.json();
      if (!result.ok) {
        const detail = body.detail;
        const error = new Error(typeof detail === 'string' ? detail : detail?.message || '确认失败，请重试');
        error.validation = typeof detail === 'object' ? detail : null;
        throw error;
      }
      // 用户要求（2026-10-16）：确认目录后**不要**直接跳编写，而要进第 2 步「分配篇幅」；
      // 篇幅确认之后才进编写。旧行为把两步压成一步，用户看不到篇幅分配。
      button.textContent = '已确认，进入篇幅分配…';
      splitPanelFlash = `已确认${responseLabel}目录。下一步：填一个总页数，系统按各章分值自动分配。`;
      planStep = '2';
      await openSplitReport({stage:'2'});
      document.querySelector('.sr-stage.on')?.scrollIntoView({behavior:'smooth',block:'start'});
    } catch (err) {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = originalLabel;
      slot.className = 'response-confirm-feedback is-error';
      const missing = err.validation?.missing_sections || [];
      slot.innerHTML = `<strong>暂不能创建${escapeHtml(responseLabel)}</strong><p>${escapeHtml(err.name==='TimeoutError' ? '请求超时，请重试。目录调整已保留。' : err.message)}</p>${missing.length ? `<details open><summary>未承接的明确风险要求 ${missing.length} 条</summary><ul>${missing.map(s=>`<li>${escapeHtml(s.title || (s.source_path || []).join(' > '))}</li>`).join('')}</ul></details>` : ''}<button class="mw-btn" type="button" data-confirm-checks>查看目录检查</button>${err.validation ? ' <button class="mw-btn" type="button" data-confirm-rebuild>重新生成完整目录</button>' : ''}`;
      slot.querySelector('[data-confirm-checks]').onclick = () => el.querySelectorAll('.response-coverage')[1]?.scrollIntoView({behavior:'smooth',block:'center'});
      slot.querySelector('[data-confirm-rebuild]')?.addEventListener('click', async () => {
        if (await uiConfirm('重新生成将替换当前目录的手动调整，并补齐全部重点章节。是否继续？')) await redraftOutline();
      });
      slot.scrollIntoView({behavior:'smooth',block:'nearest'});
    }
  });
  el.querySelectorAll('.sr-erow').forEach((row) => row.addEventListener('click', (ev) => {
    if (ev.target.closest?.('button')) return;          // 点操作按钮不算选行
    selectedStructId = row.dataset.struct;
    el.querySelectorAll('.sr-erow').forEach((x) =>
      x.classList.toggle('on', x.dataset.struct === selectedStructId));
    const block = structBlocks.find(b => String(b.block_id || b.id) === selectedStructId);
    const originalLocked = chapterSources.get(selectedStructId)?.response_part === 'point_to_point';
    el.querySelectorAll('[data-act="struct-new"]').forEach(button => { button.disabled = originalLocked; });
    const detail = el.querySelector('#response-chapter-evidence');
    if (detail && block) {
      const requirements = expectedRequirements.filter(r => parseIds(block.spec_requirement_ids).includes(r.requirement_id));
      detail.innerHTML = `<strong>${escapeHtml(block.title)}</strong><p>评分依据：${parseIds(block.review_point_ids).map(reqNameOf).filter(Boolean).map(escapeHtml).join('、') || '未直接关联评分点'}</p>${requirements.length ? `<details open><summary>技术规范要求 ${requirements.length} 条</summary>${requirements.map(r=>`<div class="response-evidence-item"><small>${escapeHtml(r.title)}</small><div>${specContentHtml(r.text)}</div></div>`).join('')}</details>` : ''}`;
    }
  }));
  // 面板内的结构**写**入口已接回：前端用 block_id（逻辑 id），后端补了两道闸
  // （纯数字 id → 400；一次提交里大量未知 id → 400），误发不会再毁目录。
  el.querySelectorAll('[data-act="struct"]').forEach((b) => b.addEventListener('click', () => {
    handleStructAct(b.dataset.block, b.dataset.op);
  }));
  el.querySelectorAll('[data-act="struct-new"]').forEach((b) => b.addEventListener('click', () => {
    handleStructNew(b.dataset.op);
  }));
  // 批量操作：复选框多选 → 整批上移/下移/缩进/减少缩进（模型层是纯函数，有 node --test）
  el.querySelectorAll('.sr-pick').forEach((cb) => {
    cb.addEventListener('click', (ev) => ev.stopPropagation());   // 勾选不算"选行"
    cb.addEventListener('change', () => {
      if (cb.checked) batchIds.add(cb.dataset.pick); else batchIds.delete(cb.dataset.pick);
      const bar = document.getElementById('sr-batch');
      const n = document.getElementById('sr-batch-n');
      if (n) n.textContent = String(batchIds.size);
      bar?.classList.toggle('is-empty', batchIds.size === 0);
    });
  });
  el.querySelectorAll('[data-batch]').forEach((btn) => btn.addEventListener('click', () => {
    handleBatchStruct(btn.dataset.batch);
  }));
  el.querySelectorAll('.sr-grip').forEach((g) => g.addEventListener('pointerdown', startRowDrag));
  // 标包卡片：重新分析 / 用它拆分
  el.querySelector('[data-act="reanalyze-packages"]')?.addEventListener('click', async () => {
    pkgAnalysis = null;
    pkgMergeNote = '';
    await loadPackageAnalysis({ refresh: true });
    await openSplitReport({ stage: '1' });
  });
  // 用模型整理：只做合并与命名；编造的标包/原文里没有的名字都会被丢弃
  el.querySelector('[data-act="merge-packages"]')?.addEventListener('click', async () => {
    const b = el.querySelector('[data-act="merge-packages"]');
    if (b) { b.disabled = true; b.textContent = '模型整理中…'; }
    pkgAnalysis = null;
    await loadPackageAnalysis({ refresh: true, useLlm: true });
    pkgMergeNote = (pkgAnalysis && pkgAnalysis.note) || '模型没有可整理的内容（已是原文原样）';
    await openSplitReport({ stage: '1' });
  });
  el.querySelectorAll('[data-act="pick-package"]').forEach((b) => b.addEventListener('click', async () => {
    // 选中该标包 → 联动顶部「筛选·标包」下拉，并滚动过去让用户点「确认标包」
    const sel = document.getElementById('sr-view-pkg');
    const want = `${b.dataset.target}|${b.dataset.pkg}`;
    if (sel) {
      const opt = [...sel.options].find((o) => o.value === want);
      if (opt) sel.value = want;
      sel.scrollIntoView({ behavior: 'smooth', block: 'center' });
      splitPanelFlash = `已选中「标的${b.dataset.target || '—'} / 标包${b.dataset.pkg || '—'}」，请点「确认标包」`;
      const host = document.querySelector('.sr-filter');
      if (host) inlineFlash(host, splitPanelFlash, 'ok');
    }
  }));
  // 一键对齐：把解析事实按已确认范围/类别落库（不花模型）
  el.querySelector('[data-act="sync-points"]')?.addEventListener('click', async () => {
    const btn = el.querySelector('[data-act="sync-points"]');
    if (btn) { btn.disabled = true; btn.textContent = '正在对齐…'; }
    try {
      const r = await api.projects.syncPoints(projectId, { threadId: currentThreadId || '' });
      splitPanelFlash = `已对齐：落库 ${r?.written || 0} 条评分点`;
      await openSplitReport({ stage: '1' });
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = '一键对齐（不花模型）'; }
      console.error('[points] 对齐失败', e);
    }
  });

  el.querySelectorAll('[data-topic-open]').forEach((b) => b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    openTopicPicker(b.dataset.topicOpen, b);
  }));
  el.querySelector('[data-act="undo-structure"]')?.addEventListener('click', () => {
    handleUndoStructure();
  });

  // Cmd/Ctrl+Z 也能撤销（输入框里不拦，免得抢掉改标题时的原生撤销）
  if (!window.__undoKeyBound) {
    window.__undoKeyBound = true;
    window.addEventListener('keydown', (e) => {
      const t = e.target;
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (typing) return;
      if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === 'z') {
        if (!document.getElementById('split-panel-inline')?.querySelector('[data-act="undo-structure"]')) return;
        e.preventDefault();
        handleUndoStructure();
      }
    });
  }
  applyStage(); applySub();
  refreshPanelRunState();
  hydrateStage(panelStage);

  el.addEventListener('click', async (e) => {
    // 收起：拆下面板，露出目录（确认与否都能收起，收起不等于确认）

    // 「调整结构」：目录结构编辑能力一直挂在下方目录树上（增删/改名/上下移/缩进），
    // 只是面板打开时被 hideLaneTreeWhenPanelShows() 隐藏了 —— 这里按需露出来并切到可编辑态。
    if (e.target.closest?.('[data-act="edit-structure"]')) {
      structureEditRequested = true;          // 允许编辑（见 structureEditable 的说明）
      const list = document.getElementById('blocks-list');
      if (list) {
        list.hidden = false;
        list.style.display = '';
        refreshStructureEditable();           // 现在会加上 .structure-editable
        list.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      return;
    }

    // 「修改」：把已确认项重新展开成控件（确认结果默认展示，避免"确认完就查不到"）
    const editBtn = e.target.closest?.('[data-act="edit-confirm"]');
    if (editBtn) {
      confirmEditOpen[editBtn.dataset.key] = true;
      await openSplitReport({ stage: '2' });
      return;
    }

    // 「重新拆分评分点」= 从零开始（清空旧结果再跑）
    if (e.target.closest?.('[data-act="redraft-points"]')) {
      el.remove();
      await restartExtraction();
      return;
    }
    // 「重新拆分目录」= 用当前解析结果/拆分要求重新派生目录（带后端预检）
    if (e.target.closest?.('[data-act="redraft-outline"]')) {
      await redraftOutline();
      return;
    }
    // 「重新拆分评分点」= 从头跑准备（会清掉已准备内容，内部有二次确认）
    if (e.target.closest?.('[data-act="reextract"]')) {
      await confirmReextract();
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
      splitPanelExpanded = true;    // 面板始终展示（用户要求：不要"展开/收起"这类操作）
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
      await loadExistingBlocks({ silent: true, keepSelection: true });
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
        splitPanelFlash = '暂无归属选择。想指定就在主题右侧选一章；不想指定就点「全部保持默认」。';
        splitPanelFlashKind = 'err';
        inlineFlash(host, splitPanelFlash, splitPanelFlashKind);
        return;
      }
      value = map;
      splitPanelFlash = `已保存 ${n} 个主题的归属；其余保持默认。下次「拆分评分点」按它挂载。`;
      topicSaveNote = `已保存 ${n} 个主题的归属`;
    } else if (kind === 'structure') {
      value = { confirmed: true };
      splitPanelFlash = '结构已确认。';
    }
    btn.disabled = true;
    try {
      const tid2 = await resolvePanelThreadId(null);
      await api.workflow.answerSplit(projectId, { key: kind, value, threadId: tid2 });
      document.getElementById('split-panel-inline')?.remove();
      splitPanelExpanded = true;    // 面板始终展示（用户要求：不要"展开/收起"这类操作）
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
  if (params.get('planning') === '1') { document.getElementById('raw-toc-warn')?.remove(); return; }
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
    <strong>当前显示的是招标文件原始目录（${chk.source_sections} 节），应答目录未派生。</strong>
    <span>那次运行在解析阶段就中断了。请点上方「重新拆分目录」按已解析的内容派生应答目录；
          确认目录前不要直接开始准备。</span>`;
  blocksList.parentElement?.insertBefore(box, blocksList);
}


async function loadExistingBlocks({ silent = false, keepSelection = false } = {}) {
  // ── 拆分进行中：**什么都不渲染** ──
  // 只保留面板里的加载态（"⟳ 正在拆分评分点…"）。以前这个拦截放在函数末尾，
  // 而"库里没有章节"的分支在它之前就执行了 → 拆分期间底部又会冒出
  // 「上传应标文件后点击「拆分评分点」开始」，与"正在拆分"自相矛盾（用户反馈）。
  if (splitInProgress) {
    if (blocksList) { blocksList.hidden = true; blocksList.style.display = 'none'; }
    const ee0 = document.getElementById('blocks-empty');
    if (ee0) { ee0.hidden = true; ee0.innerHTML = ''; }
    const le0 = document.getElementById('blocks-loading');
    if (le0) le0.hidden = true;
    return;
  }
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
    // 没记住确认过哪个标包时（首访 / 换浏览器）：沿用**最新那条运行所属的标包**。
    // 否则从项目列表进来会先撞上"先确认标包"的门控 —— 用户想看到的是自己上次的进度。
    if (!confirmedPkg) {
      const withScope = (runs || []).find((r) =>
        String(r.target || '') || String(r.package_no || ''));
      if (withScope) {
        rememberConfirmedPkg({ target: withScope.target || '', package_no: withScope.package_no || '' });
        panelViewPkg = { target: withScope.target || '', package_no: withScope.package_no || '' };
      }
    }
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
    if (ee && packageId) {
      ee.hidden = false;
      ee.innerHTML = `<div style="padding:32px;text-align:center"><h3>当前标包尚未拆分</h3>
        <p>根据已确认的招标依据，拆分当前标包的评分点和响应要求。</p>
        <button type="button" class="mw-btn primary" id="start-package-extraction">开始拆分评分点</button>
        <p><a href="/project-intake?projectId=${projectId}">核对标包与文件依据</a></p></div>`;
      ee.querySelector('#start-package-extraction').onclick = () => startExtraction();
    }
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
  // 是否「已准备」以 status 字段为权威（见 isDoneBlock）
  const isDone = isDoneBlock;

  totalBlocks = blocks.length;
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
  //   2) 显示占位指引；3) 展开「拆分评分点」面板（默认展示全部内容，无需再点"展开"）。
  // 必须放在**树渲染之后**：早于树渲染会被后续的构建/切换盖掉（实测面板不出现）。
  // 说明：原来这里还有一个「评分点已拆出，目录还没生成…」的占位块（#blocks-pending），
  // 它与面板里的说明**完全重复**（用户反馈），已删除；需要引导时由面板自身承担。
  if (splitInProgress) {
    // 拆分中：旧的目录树 / 空态都不该出现（截图里旧目录在解析期间又冒出来了）
    blocksList.hidden = true;
    blocksList.style.display = 'none';
    const ee = document.getElementById('blocks-empty');
    if (ee) ee.hidden = true;
    return;
  }
  if (outlinePending) {
    blocksList.hidden = true;
    blocksList.style.display = 'none';
    splitPanelAutoOpenedFor = 0;
    openSplitReport();
  } else if (totalBlocks) {
    // 已生成目录 → 直接展示面板（产物在面板里看；用户要求不要"展开/收起"操作）
    // ⚠️ 同时**必须收起下面的旧目录树**：目录已经由面板的「产物」阶段承载，
    //    两处各画一份会让用户问"界面下方为什么还有目录"（用户反馈）。这里是
    //    旧实现遗留的显示路径 —— 以前它把树亮起来又开面板，于是同一份目录出现两次。
    blocksList.hidden = true;
    blocksList.style.display = 'none';
    // 清掉主区占位：派生阶段写过「正在…梳理应答目录…」，完成后会残留在下方
    const ee = document.getElementById('blocks-empty');
    if (ee) { ee.hidden = true; ee.innerHTML = ''; }
    splitPanelAutoOpenedFor = 0;
    openSplitReport();
  } else {
    blocksList.hidden = false;
    blocksList.style.display = '';
    const empty = document.getElementById('blocks-empty');
    if (empty && packageId) {
      empty.hidden = false;
      empty.innerHTML = `<div style="padding:32px;text-align:center"><h3>当前标包尚未拆分</h3>
        <p>根据已确认的招标文件与技术规范书，拆分当前标包的评分点和响应要求。</p>
        <button type="button" class="mw-btn primary" id="start-package-extraction">开始拆分评分点</button>
        <p><a href="/project-intake?projectId=${projectId}">核对标包与文件依据</a></p></div>`;
      empty.querySelector('#start-package-extraction').onclick = () => startExtraction();
    }
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
      setStatusBar('done', isMatching ? '匹配完成' : '编写准备完成', pct, totalBlocks, doneBlocks);
    } else {
      // 部分准备/匹配（取消后保留的进度）：进入 paused 态，让用户继续 / 重新触发。
      // 大纲视图用统一文案（还剩 N 节 + 继续准备只补剩余），素材库视图保持原样。
      if (!isMatching) {
        setPartialOutlineBar();
      } else {
        setStatusBar('paused', `已保留 ${doneBlocks}/${totalBlocks} 个已匹配章节`,
                     pct, totalBlocks, doneBlocks);
      }
    }
  } else if (totalBlocks > 0 && currentStage !== 'outline_review') {
    // 已解析章节但一节都没准备。**排除闸门 1**：目录已拆好、等用户确认，这是正常
    // 等待态而非中断 —— 写「可继续准备」会把用户从「先确认目录」引偏，paused 的
    // 按钮组也会盖掉闸门的「确认目录」。闸门的文案由 boot /
    // onGateOpen / restoreSettledThread 负责。
    setStatusBar('paused', `已解析 ${totalBlocks} 个章节，可继续准备`, 0, totalBlocks, 0);
  }
}

// 运行终止 / 断流后的唯一对齐手段：以数据库为准重绘，等价于用户手动刷新一次。
// 运行期间页面上的章节来自 SSE 帧，库只在 parse / outline_draft 两处写入；两者不一致时
// 只有重读库能把卡片、进度、状态栏一起拉回真实状态。否则卡片会永远停在最后一帧的样子
// （最常见的是「准备中…」+ 进度条卡在中途）。
async function resyncFromDb() {
  // 闸门期间不重绘：用户可能正在编辑目录，整版替换会丢掉改动（与 scheduleOutlineTree 同一约定）
  if (extractionState === 'gate_outline' || extractionState === 'gate_materials') return;
  try {
    await loadExistingBlocks();
  } catch (e) {
    console.warn('[workflow] 从数据库重同步失败', e);
  }
}

// ── 准备流程（新 workflow driver） ──
function resetWorkflowUi() {
  blocksList.innerHTML = '';
  blocksEmpty.innerHTML = '<p>上传应标文件后点击「拆分评分点」开始</p>';
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
  // 逐节准备每跑完一轮，后端都会主动关掉事件流（emitter.aclose）；这属于正常收尾，
  // 而"恢复额度"只在闸门事件里重置（准备过程没有闸门事件）—— 于是连着跑完两轮之后，
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
    // 准备/匹配还在跑：只是连接掉了，重连继续等帧（不 recover，免得空跑一轮）
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
    // 任务已不存在：**必须解除加载态**，否则面板会永远停在「正在拆分评分点…」
    // （实测：后端重启把运行清掉后，页面就卡成转圈，而顶部已经在提示"请刷新页面重试"）。
    splitInProgress = false;
    outlineDrafting = false;
    setSplitChromeHidden(false);
    await resyncFromDb();
    setStatusBar('failed',
      '事件流已断开，且按检查点也接不回来（该任务可能已被清理）。请刷新页面后重试。',
      totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    showSplitPanelRetry('这次拆分已中断（任务已被清理）');
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
    splitInProgress = false;
    outlineDrafting = false;
    setStatusBar('failed',
      '事件流已断开，按检查点恢复也失败（该任务可能已被清理）。请刷新页面后重试。',
      0, totalBlocks, doneBlocks);
    showSplitPanelRetry('这次拆分已中断（恢复失败）');
  }
}

function attachStream(threadId) {
  closeStream();
  startSettlePoll(threadId);   // 开始等帧 → 同时开始兜底轮询
  currentEventSource = api.workflow.stream(threadId, {
    onStreamError: () => { void recoverStreamAfterDrop(); },
    // 收到**任意**一帧就说明连接是好的 → 重置断流恢复额度。
    // 只靠 stage_change 重置不够：逐节准备期间没有闸门事件，连续跑完两轮之后
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
      // 不再往主区写「正在解析文档结构…(step cur/tot)」：顶部状态栏已经在显示同一进度，
      // 两处重复（用户反馈）。主区在拆分期间由面板的加载态承担。
      const ee = document.getElementById('blocks-empty');
      if (ee && !splitInProgress) ee.hidden = true;
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
        badge.textContent = '准备中…';
      }
      const prog = card.querySelector('.block-card-progress');
      if (prog) prog.classList.add('active');
      // 闸门 1 的卡片不带 8 字段网格（那时一个都没准备）。到"准备中"挂一个**单行占位**，
      // 不挂 7 行空骨架：逐节准备是串行的，每节要跑十几秒到一分钟，7 个空字段看着
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
      // 滚动到当前正在准备的节点，但**只在它不在视口内时**滚：
      // 33 节文档里每节都抢一次滚动，用户想回看已准备的内容就会被反复拽走。
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
        // 第 8 个字段以前没进这个对象 → 卡片永远显示默认值，准备结果里的
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
        `准备编写依据 ${doneBlocks}/${totalBlocks} 章节${failed ? `（${failed} 节失败）` : ''}`,
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
      outlineDrafting = true;
      setStatusBar('extracting', `张衡梳理应答目录${ver}…`, 0, totalBlocks, doneBlocks);
      blocksEmpty.innerHTML = '<p>正在依据要求文件梳理应答目录…</p>';
      blocksEmpty.style.display = 'block';
    },
    onOutlineDraft: (data) => {
      // 目录是整体替换的：一次收全量，旧卡片全部推倒重建
      const toc = Array.isArray(data?.toc) ? data.toc : [];
      resetWorkflowUi();
      draftRevision = Number(data?.revision) || draftRevision;
      outlineError = String(data?.error || '');
      blocksEmpty.innerHTML = '<p>上传应标文件后点击「拆分评分点」开始</p>';
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
      // 逐节准备已移到闸门1 之后，此刻只有目录到手、什么都没在跑，不能写「正在准备章节要求」
      setStatusBar('extracting', `目录已更新${ver ? `（${ver}）` : ''}…`,
        0, totalBlocks, 0);
      // 两阶段 UI 的**主入口**：目录一到手就展示拆分清单。
      // 不能只挂在 gate_open 上 —— 闸门早就开着时重拆不会再发 gate_open 帧，
      // 于是"拆分完直接展示"永远不触发（实测踩到）。
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
        // 快照是后端权威：刷新后靠它回填拆分要求、显示版本号与降级提示
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
            ? `大纲编写准备完成${ver}；${degrades.join('；')}`
            : `大纲编写准备完成${ver}，请检查并编辑后开始准备要点`,
          pct, totalBlocks, doneBlocks);
      } else if (gate === 'review_materials') {
        setStatusBar('gate_materials', '素材匹配完成，请确认后进入撰写', pct, totalBlocks, doneBlocks);
      } else if (gate === 'review_report') {
        // 该闸门不属于本页，但兜底标记完成
        setStatusBar('done', '工作流已进入审核阶段', 100, totalBlocks, doneBlocks);
      }
    },
    onError: async (data) => {
      splitInProgress = false;      // 拆分失败/中断：解除加载态，允许渲染真实状态
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
      // 硬失败要落到 failed 态（带重试按钮），不能停在 extracting —— 那会永远转圈等帧。
      // 但**不把 msg 铺到状态栏**（用户要求去掉那段技术报错）：原因进控制台 + 运行详情。
      console.warn('[workflow] 运行失败：', msg);
      setStatusBar('failed', '', pct, totalBlocks, doneBlocks);
    },
    onCheckpoint: (data) => {
      console.debug('[workflow] checkpoint', data);
    },
    onAborted: (data) => {
      console.warn('[workflow] aborted', data);
      closeStream();
      // **不清 thread**：与 cancelExtraction 同一道理 —— 已准备的章节是这次 run 的成果，
      // 「继续准备」必须能 resume 回同一条 run（否则会重新解析 + 重新派生目录 + 全量重算）。
      currentGate = null;
      setExtractDisabled(false);
      // 中止后卡片仍停在最后一帧（在被中止的那一节上显示「准备中…」）。以库为准重绘，
      // 库里还保留着中止前已落库的进度，正好就是刷新之后会看到的样子。
      resyncFromDb().then(() => openAllExtractedNodes());
    },
    onDone: (data) => {
      console.debug('[workflow] done', data);
      splitInProgress = false;      // 拆分结束：此后允许渲染面板/目录
      setSplitChromeHidden(false);  // 恢复顶部阶段条与页面状态栏
      const eeDone = document.getElementById('blocks-empty');
      if (eeDone) { eeDone.hidden = true; eeDone.innerHTML = ''; }
      // 运行结束但没进闸门（例如被后端中止 / 图跑到尽头）时同样要回读库，
      // 否则页面停在运行期的帧上，只有手动刷新才会对齐。
      // 收尾后把已准备的章节保持展开：用户下一步就是核对这 8 个字段。
      resyncFromDb().then(async () => {
        const st = data?.stage || '';
        // 第①步「只拆评分点」结束时：不生成目录、图直接收尾 —— 这时要主动把
        // 「拆分结果」展开给用户（否则页面停在没有任何提示的状态）。
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
// 「正在准备…」。必须直接按 state 恢复视图。
const SETTLED_STAGES = new Set(['points_review', 'outline_review', 'materials_review', 'report_review', 'done', 'paused', 'aborted', 'failed']);
// 运行期阶段（"准备中"）：新 IA 里这不是用户动作 —— 页面发现上次没跑完就自己接上，
// 见 maybeAutoResume()。用户主动取消（paused）不在其列。
const RUNNING_STAGES = new Set(['parsing', 'extracting', 'matching', 'generating', 'reviewing']);
let autoResumeTried = false;

/** 自动续跑被中断的准备步骤（后端按图的 next 决定接着跑哪一步）。
 *  为什么不做成按钮：新信息架构里只有 评分点 → 待确认 → 产物 → 编写 → 评审，
 *  "准备（逐章抽取要求）"是内部环节，不该出现在用户的操作面上。 */
async function maybeAutoResume(stage) {
  if (autoResumeTried || !RUNNING_STAGES.has(stage)) return;
  autoResumeTried = true;
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  if (!tid) return;
  try {
    const r = await fetch(`/api/workflow/${tid}/resume-auto`, { method: 'POST' });
    if (!r.ok) return;
    const data = await r.json().catch(() => ({}));
    if (data?.resumed && data.resumed !== 'nothing_to_do' && data.resumed !== 'running') {
      const el = document.getElementById('sr-run-state');
      if (el) el.textContent = '已自动继续上次未完成的准备工作…';
      console.info('[workflow] 自动续跑：', data.resumed);
    }
  } catch (e) {
    console.warn('[workflow] 自动续跑失败（不影响查看）', e);
  }
}   // failed：终态，没有帧可等，直接按 state 恢复视图

// 需要「按 state 恢复视图」的停留态（闸门）。failed/done 除外：它们是终态。
// points_review：第①步（只拆评分点）结束的停留态 —— 没有目录可确认，
// 要把「拆分结果」打开让用户选类别，所以和闸门一样按 state 恢复视图。
const GATE_STAGES = new Set(['points_review', 'outline_review', 'materials_review']);

async function restoreSettledThread(threadId, stage, { note = '' } = {}) {
  // 按 state 恢复视图 = 这次运行已经停住，不可能还在"拆分中"
  splitInProgress = false;
  if (stage === 'aborted') {
    clearThread(projectId);
    currentThreadId = null;
    currentGate = null;
    resetWorkflowUi();
    setStatusBar('idle', '该任务已作废，请重新拆分评分点', 0, 0, 0);
    return;
  }
  if (['report_review', 'done'].includes(stage)) {
    // **不跳走**（用户反馈：从项目列表进来直接落到评审页，没走到五步流程）。
    // 新信息架构里评审就是第⑤步，同一套壳（左五步 + 右内容）里展示即可。
    setStage(stage);
    await loadExistingBlocks({ silent: true });
    setStatusBar(stage === 'done' ? 'done' : 'report_review', '', 100, totalBlocks, doneBlocks);
    // 不再"按运行阶段定位"（用户明确要求去掉）：阶段由用户自己点，进来从①开始。
    splitPanelAutoOpenedFor = 0;
    await openSplitReport({ stage: '1' });
    return;
  }
  setStage(stage); // 同时按 stage 切 outline / matching 视图并重绘进度条
  maybeAutoResume(stage);   // 上次没跑完 → 自己接上（不是用户动作）
  await loadExistingBlocks({ silent: true });
  const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
  if (stage === 'failed') {
    currentGate = null;
    // 状态栏**不再写技术报错原文**（用户要求去掉）：一是那段话又长又是给开发者看的，
    // 二是 checkpoint 的 errors 是累加语义、清不掉，重试成功后仍会显示上一次的旧报错
    // ——留着只会误导。失败原因保留在「运行详情」里（showRun 会列 state.errors），
    // 需要定位问题时去那里看。此处只留动作：重新拆分目录 / 从头重新拆分评分点。
    setStatusBar('failed', '', pct, totalBlocks, doneBlocks);
    return;
  }
  if (stage === 'paused') {
    // 暂停有两种，靠"还有没有章节没准备"区分（与闸门态同一套判据）：
    //   还有剩余 → 准备中途取消：留在本页给「继续准备」（resume-extract 只补剩余部分）
    //   全都准备过 → 生成阶段的暂停，不在本页处理，去质量检查
    if (totalBlocks > 0 && doneBlocks < totalBlocks) {
      currentGate = null;
      setPartialOutlineBar({ note });
      openAllExtractedNodes();
      return;
    }
    // 生成阶段暂停：留在面板的④编写（继续撰写），不再跳去质量检查页
    setStage(stage);
    await loadExistingBlocks({ silent: true });
    setStatusBar('paused', '', totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0,
                 totalBlocks, doneBlocks);
    splitPanelAutoOpenedFor = 0;
    await openSplitReport({ stage: '1' });
    return;
  }
  if (stage === 'points_review') {
    // 第①步（只拆评分点）的停留态：评分点已拆出、目录还没生成。
    // 直接展开「拆分结果」，让用户选标包 + 类别后生成目录。
    currentGate = null;
    setStatusBar('gate_outline',
      `评分点已拆出，请在「拆分结果」里选择要生成的目录类别（技术 / 商务 / 报价）${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
    await refreshOutlineCoverage(threadId);
    splitPanelAutoOpenedFor = 0;
    await openSplitReport();
    return;
  }
  if (stage === 'outline_review') {
    // 与 boot 同一套判定：checkpoint 停在 outline_review，但章节已全部准备 = 第二步
    // 其实跑完了（extract 收尾时图直接 END，没有节点再改 stage），别再引导用户
    // 「确认目录」—— 那会再起一次准备（后端有互斥兜底，但体验是错的）。
    if (totalBlocks > 0 && doneBlocks >= totalBlocks) {
      currentGate = null;
      setStatusBar('done', `编写准备完成${note ? `；${note}` : ''}`, 100, totalBlocks, doneBlocks);
      return;
    }
    // 部分章节已准备 = 准备被打断（正在跑的情况由调用方先判掉，见 boot / 兜底轮询）。
    // 此时仍停在闸门 1 的确认按钮上 —— 它是**唯一**能继续准备的入口：extract 会跳过
    // 已完成的章节接着跑（skip_ids）。只是把文案说清楚，别让人以为要从头来。
    if (doneBlocks > 0) {
      // 编写准备到一半（被打断）：给「继续准备」那套按钮，别再引导"确认目录开始准备"
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
      `已恢复上次进度：大纲已就绪，请检查确认后开始准备要点${degrade}${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
  } else {
    currentGate = 'review_materials';
    setStatusBar('gate_materials',
      `已恢复上次进度：素材匹配完成，请确认后进入撰写${note ? `；${note}` : ''}`,
      pct, totalBlocks, doneBlocks);
  }
}

/**
 * 「拆分评分点」入口：**从零开始**。
 *
 * 用户口径（原话）：「重新点击『拆分评分点』，系统应该将之前生成的评分点内容清空，
 * 然后重新生成，不要保存旧数据，不然会造成很多混乱……不要让旧数据对新上传的数据产生影响。」
 *
 * 所以这里先调后端的 reset-analysis：清掉旧评分点/规范要求/章节/评审/全部作答
 * （含范围与类别选择、主题归属），并把旧运行标成作废；再起一个**新 thread**。
 * 保留上传的材料文件（那是输入）与项目级投标范围（用户的显式设定）。
 */
// 防重入：清理 + 起新 run 是一整条链，期间再次点击（或别的路径同时触发）
// 会连起两个运行 —— 实测日志里一次点击出现两次 POST /api/workflow/start。
let restartInFlight = false;
let lastRestartAt = 0;

/** 拆分期间隐藏/恢复两块重复的进度 UI（顶部阶段条 + 页面状态栏）。 */
function setSplitChromeHidden(hidden) {
  const ws = document.getElementById('workspace-status');
  if (ws) ws.hidden = !!hidden;
  const sb = document.getElementById('status-bar');
  if (sb) sb.hidden = !!hidden;
}

/** 拆分中断（任务被清理 / 恢复失败）：面板给出明确说明 + 一键重试。
 *  不能继续显示加载态转圈 —— 那会让人以为还在跑。 */
function showSplitPanelRetry(reason) {
  splitInProgress = false;
  setSplitChromeHidden(false);
  const el = document.getElementById('split-panel-inline');
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `
    <div class="sr-box">
      <div class="sr-head">
        <span class="sr-head-actions">
          <button class="mw-btn" data-act="redraft">重新拆分评分点</button></span></div>
      <div class="sr-flash err" style="margin:12px 15px;">
        ${escapeHtml(reason)}。可以点右上「重新拆分评分点」重跑，或刷新页面后重试。</div>
    </div>`;
  el.querySelector('[data-act="redraft"]')?.addEventListener('click', () => restartExtraction());
}

/** 面板的**加载态**：重新拆分 / 首次拆分期间显示，避免旧内容残留在右侧。
 *  位置与正式面板一致（同一个容器），拆完由 openSplitReport 覆盖重绘。 */
function showSplitPanelLoading(text) {
  splitInProgress = true;
  // 拆分期间把两块**重复信息**收起来：顶部共享壳的阶段条（第 N 轮 · 阶段 + 正文/已通过/待处理，
  // 红框1）与页面状态栏（解析进度，红框2）。此时它们和面板里的加载态说的是同一件事，
  // 用户要求"重新拆分时只展示上面加载的内容"。
  setSplitChromeHidden(true);
  const el = document.getElementById('split-panel-inline');
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `
    <div class="sr-box">

      <div class="sr-loading">
        <span class="sr-spinner"></span>
        <span>${escapeHtml(text)}</span>
        <span class="sr-dim">正在解析材料并按标包拆分商务 / 技术评分点与技术规范书要求，完成后在这里展示。</span>
      </div>
    </div>`;
  // 旧目录一并藏起来：它是上一次的成果，重拆期间显示会让人以为已经被新结果覆盖
  if (blocksList) { blocksList.hidden = true; blocksList.style.display = 'none'; }
  const empty = document.getElementById('blocks-empty');
  if (empty) empty.hidden = true;
}

async function restartExtraction() {
  autoConfirmOutlineAt = 0;   // 主动重来 → 不再自动放行闸门
  if (restartInFlight) {
    console.info('[workflow] 上一次"重新拆分"还在进行中，忽略这次点击');
    return;
  }
  // 抑制"手抖连点"：整条链（取消 → 清库 → 起新 run）在几百毫秒内就结束了，
  // 只靠 in-flight 标志挡不住紧接着的第二次点击 —— 实测连点两次起了两条 run
  // （多余的那条会立刻被下一次 abort，白跑一次解析）。
  const now = Date.now();
  if (now - lastRestartAt < 5000) {
    console.info('[workflow] 5 秒内重复点击"拆分评分点"，忽略');
    return;
  }
  lastRestartAt = now;
  restartInFlight = true;
  const btn = document.getElementById('btn-extract');
  if (btn) btn.disabled = true;
  setStatusBar('extracting', '正在清空上次的拆分结果…', 0, 0, 0);
  // **立刻把旧内容换掉**：重新拆分时右侧还挂着上一次的评分点/规范书要求，
  // 与"正在重新拆分"矛盾、也让人误以为已经拆好了（用户反馈）。这里换成加载态，
  // 新的结果由 loadExistingBlocks → openSplitReport 在同一位置重绘。
  showSplitPanelLoading('正在重新拆分评分点…');
  // **先取消正在跑的任务**再清库：否则旧任务会边跑边往库里写，
  // 清完之后又冒出残行（清了等于没清）。
  if (currentThreadId) {
    try { await api.workflow.abort(currentThreadId); }
    catch (err) {
      console.error('[workflow] 取消旧任务失败，停止重跑', err);
      setStatusBar('error', err?.message || '取消旧任务失败，请重试', 0, 0, 0);
      showSplitPanelRetry(err?.message || '取消旧任务失败，请重试');
      if (btn) btn.disabled = false;
      restartInFlight = false;
      return;
    }
  }
  try {
    const res = await api.projects.resetAnalysis(projectId, packageId);
    const n = res?.deleted?.blocks ?? 0;
    console.info('[workflow] 已清空上次的拆分结果', res?.deleted);
    // 页面级的旧状态也要清：否则刷新或跳转回来又会按旧 thread/旧确认版本恢复视图
    try {
      sessionStorage.removeItem(THREAD_KEY(projectId));
      sessionStorage.removeItem(packageCacheKey('furthestStage'));
      sessionStorage.removeItem(packageCacheKey('outlineInstruction'));
    } catch {}
    currentThreadId = null;
    // 注意：不要再给"确认版本"另设缓存变量 —— 上面已经清掉 sessionStorage 里的键，
    // 任何新增的模块级变量都要先声明（ES 模块是严格模式，给未声明变量赋值会直接抛错）。
    splitPanelAutoOpenedFor = 0;
    if (n) console.info(`[workflow] 清掉旧章节 ${n} 节`);
  } catch (err) {
    console.error('[workflow] 清空旧拆分结果失败，停止重跑', err);
    setStatusBar('error', err?.message || '清空旧拆分结果失败，请重试', 0, 0, 0);
    showSplitPanelRetry(err?.message || '清空旧拆分结果失败，请重试');
    if (btn) btn.disabled = false;
    restartInFlight = false;
    return;
  }
  if (btn) btn.disabled = false;
  try {
    await startExtraction(true);
  } finally {
    restartInFlight = false;
  }
}

async function startExtraction(forceRestart = false) {
  // forceRestart：用户点「重新拆分评分点」，先 abort 旧 thread 再起新
  resetWorkflowUi();
  if (forceRestart) {
    // 重新拆分评分点回到「解析大纲」视图：清掉视图覆盖；不直接重置 currentStage 到 'parsing'，
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
  setStatusBar('extracting', '准备拆分…', 0, 0, 0);
  setExtractDisabled(true);
  // 首次拆分同样先给加载态：避免上一次残留内容与新任务并存
  showSplitPanelLoading('正在拆分评分点…');

  try {
    if (forceRestart && currentThreadId) {
      try { await api.workflow.abort(currentThreadId); } catch {}
      clearThread(projectId);
      currentThreadId = null;
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
      const resp = await api.projects.startPackageRun(projectId, packageId, {
        model: selectedModel,
        outline_instruction: readOutlineInstruction(),
        // 第①步「拆分评分点」：只把评分点拆出来（落库 + 出矩阵），**不生成目录**。
        // 目录留到第②步由用户在「拆分结果」里选类别后再生成（用户明确要求的两步）。
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
    // 以前这里只 console.error + 清状态栏 ——「重新拆分评分点」失败时页面上**零提示**，
    // 用户看到的就是"点了没反应"（实测：abort 之后没有任何后续请求，blocks 也没清，
    // 页面上却什么都不说）。失败必须说清原因与下一步。
    console.error('[workflow] startExtraction 失败', e);
    setExtractDisabled(false);
    const msg = (e && (e.message || e.detail)) || String(e || '未知错误');
    const canFallback = !!(currentThreadId || getThread(projectId));
    statusActions.innerHTML = canFallback
      ? `<button class="btn-status-primary" id="btn-force-restart">重新拆分评分点（强制新建运行）</button>`
      : '';
    const btnForce = document.getElementById('btn-force-restart');
    if (btnForce) btnForce.addEventListener('click', () => forceRestartRun());
    setStatusBar('idle',
      `重新拆分评分点失败：${msg}。` +
      (canFallback ? '可点「重新拆分评分点（强制新建运行）」重试。' : '请刷新页面后重试。'),
      0, 0, 0);
  }
}

/**
 * 强制新建运行：不依赖 sessionStorage 里的旧 thread，直接把当前 thread 作废后走 start。
 *
 * 为什么单独留一条：正常「重新拆分评分点」会先 abort 旧 run 再新建；一旦 abort 之后
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
    await api.projects.resetAnalysis(projectId, packageId);
    const resp = await api.projects.startPackageRun(projectId, packageId, {
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
  // 留下的 'outline_review'**；于是"准备已完成"与"闸门 1 还没放行"在 /state 里长得
  // 一模一样（真实数据：项目 18 thread f0e6f7ae → workflow_runs='done'、33/33 节
  // outline_done、checkpoint stage='outline_review'）。以前这里信了 checkpoint，
  // 点「素材匹配」被判成"还没准备"，`setStage('outline_review')` + `switchView('outline')`
  // 把人拽回要求与大纲并弹「请先在『要求与大纲』确认目录并准备要点」—— 而那边其实
  // 无事可做，用户被卡死（用户报的正是这个）。
  // 真实判据 = **已准备章节数**（doneBlocks，页面自己按 blocks 算，比 checkpoint 新）。
  // - 一节都没准备 → 闸门 1 真的没过：回「要求与大纲」引导（语义正确）。
  // - 已有准备结果 → 就是"开始匹配"：rerun-match 把指针拨回 NODE_EXTRACT 之后跑 match。
  // - 长阶段还在跑（准备/匹配）→ 让它跑完，不发请求也不动视图。
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
    // 准备 / 匹配还在跑：后端 rerun_match 会 409，而且此刻切视图只会让人更困惑
    const running = tid ? await longPhaseRunning(snap) : null;
    if (running) {
      setExtractDisabled(false);
      setStatusBar('extracting',
        running === 'extracting' ? '正在准备要点，等这一轮跑完再开始匹配。' : '正在匹配素材，请稍候…',
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
      return;
    }

    const total = Number(totalBlocks) || 0;
    const extracted = Number(doneBlocks) || 0;
    const stage = snap?.stage || currentStage;
    // 该走哪条路由纯函数判定（matchEntryDecision，有 node --test 覆盖）：判据是
    // **已准备章节数**而不是 checkpoint 的 stage —— 理由见函数头与模型层注释。
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
      // 全新 run：一节都没准备，下面必然走"先确认目录再准备"的引导
      setExtractDisabled(false);
      switchView('outline');
      setStatusBar('extracting', '已新建一次运行：请先在面板里确认目录',
                   0, totalBlocks, doneBlocks);
      return;
    }
    // ⚠️ 这里**不**补 attachStream。线程若已停在闸门，页面本来就没有流；此时新订阅
    // 会命中 emitter 的"有界回放"，把上一轮匹配收尾时的 `gate=review_materials` 帧
    // 重放一遍，于是刚写下的引导文案（"请先在要求与大纲…"）被"素材匹配完成…"盖掉。
    // 真正要干活的分支（下面 rerun-match / 新起 run）各自 attach，那里才是必要的。

    if (decision === MATCH_ENTRY.GATE_OUTLINE) {
      // 闸门 1 还没放行。匹配依赖准备产出的矩阵，不能在这儿代放闸门 —— 那会让
      // 「素材匹配」按钮顺手烧掉 N 次逐节准备调用，用户还看不到准备结果。送回
      // 「要求与大纲」，走「确认目录」。
      setStage(stage);
      setExtractDisabled(false);
      switchView('outline');
      setStatusBar('gate_outline', '请先在面板里确认目录', 0, totalBlocks, doneBlocks);
      return;
    }
    if (decision === MATCH_ENTRY.START) {
      if (stage === 'outline_review') {
        // 准备已完成、checkpoint 仍写着 outline_review：只把进度条挪到匹配步，
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
      // 补充的素材由局部修订入口实时读取 —— 不再整页跳质量检查，落在面板⑤评审里
      // （同一套壳，用户不用在页面之间被搬来搬去）。
      panelStage = '1';
      panelSub = '';
      splitPanelAutoOpenedFor = 0;
      await openSplitReport({ stage: '1' });
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
  // **不清 thread**：已准备的章节是这次 run 的成果（后端逐节 upsert 落库），
  // 取消只是"停一下"。清掉 thread 引用会带来两个真问题（实测）：
  //   ① 「继续准备」找不到原 run → startExtraction 起一条**全新** run → 重新解析 +
  //      重新派生目录（目录可能与用户刚审过的那版不同）→ 用户看到「目录结构变了」；
  //   ② 那等于把已准备的部分全部重算一遍 —— 每章一次模型调用，白烧钱。
  // 保留 thread 后，「继续准备」= resume 同一个 run，extract 会跳过 status='outline_done'
  // 的章节（skip_ids），只补没做的。
  currentGate = null;
  setExtractDisabled(false);
  // 用库里的计数，不用内存里的 doneBlocks/totalBlocks：中止时最后一节可能还没落库，
  // 内存计数会比库里多，状态栏数字和刷新后看到的不一致。
  await resyncFromDb();
  // 已准备的章节保持展开：用户刚看到的内容不该因为"取消"就从屏幕上消失
  openAllExtractedNodes();
}

// 「继续准备」：复用当前 run 从检查点接着跑（不重新派生目录、不重算已完成章节）。
// 用 resume-extract 而不是普通 resume：取消时图已经收尾（next 为空），普通 ainvoke
// 是空跑；该端点借闸门 1 的出边把指针拨回 extract 之前，extract 自己跳过已完成的章节。
async function continueExtraction() {
  const tid = currentThreadId || params.get('threadId') || getThread(projectId);
  if (!tid) {
    // 没有可复用的 run（比如换了浏览器/清过会话）：只能从头，先说清楚代价
    const go = await uiConfirm(
      '找不到这次运行的记录，只能重新跑一遍：\n' +
      '重新解析要求文件 → 重新派生目录 → 重新逐节准备（每章一次模型调用）。\n\n' +
      '确定继续？');
    if (!go) return;
    return startExtraction(true);
  }
  setExtractDisabled(true);
  setStatusBar('extracting', '继续准备剩余章节…', 
               totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
  try {
    await api.workflow.resumeExtract(tid);
    currentThreadId = tid;
    setThread(projectId, tid);
    attachStream(tid);
  } catch (e) {
    console.error('[workflow] continueExtraction 失败', e);
    setExtractDisabled(false);
    setStatusBar('paused', `继续准备失败：${e?.message || e}`, 
                 totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
  }
}

// 「编写准备到一半」的顶栏（文案 + 按钮组）。按钮组由 setStatusBar 的 paused 分支渲染：
// 重新拆分评分点 / 重新拆分目录 / 继续准备（剩余 N 节）。
// 单独抽出来是因为 boot、断流恢复、取消之后三处都必须落到同一个形态 —— 以前它们都
// 走闸门 1 那栏，于是半个准备进度下还在显示「确认目录」，语义是错的。
// 当前项目里准备失败的章节数（blocksById 是页面的权威副本）
function failedBlockCount() {
  let n = 0;
  blocksById.forEach(b => { if ((b?.error_kind || '') === 'failed') n++; });
  return n;
}

// 主按钮有两个入口：左栏底部的「拆分评分点 / 素材匹配」和素材库顶部总览条的
// 「分析素材归属」。禁用态必须一起变 —— 只灰一个的话另一个看起来能点却没反应。
function setExtractDisabled(v) {
  // **不再禁用「拆分评分点」**（用户反馈：运行期按钮变灰，只能先去右上角点「取消」，
  // 再回来点一次按钮 —— 两步操作纯属冗余）。现在它始终可点：
  // 运行中点它 = 自动取消当前任务 + 从零重跑（见 restartExtraction）。
  // 运行期用文案/提示表达状态，而不是把唯一的入口禁掉。
  btnExtract.disabled = false;
  btnExtract.classList.toggle('is-running', !!v);
  btnExtract.title = v
    ? '正在拆分中 —— 点它可以取消当前任务并从零重新拆分'
    : '按招标文件的评分点拆分应答目录';
  const hint = document.querySelector('.materials-footer .extract-hint');
  if (hint && v) {
    hint.dataset.prev = hint.dataset.prev || hint.textContent;
    hint.textContent = '正在拆分…点按钮可取消并重新拆分';
  }
  const alt = document.getElementById('ms-match');
  if (alt) alt.disabled = !!v;      // 素材匹配没有"重跑"语义，保持禁用
}

function restoreExtractHint() {
  const hint = document.querySelector('.materials-footer .extract-hint');
  if (hint && hint.dataset.prev) { hint.textContent = hint.dataset.prev; delete hint.dataset.prev; }
}

function setPartialOutlineBar({ note = '' } = {}) {
  const total = Number(totalBlocks) || 0;
  const done = Number(doneBlocks) || 0;
  const remain = Math.max(total - done, 0);
  const pct = total ? Math.round(done / total * 100) : 0;
  // 文案尽量短：右侧已经有三颗按钮，长句子会把它们挤出去（实测被挤出右边界）。
  // "只补剩余、不重复调用"这句解释挂在按钮 title 上。
  const failed = failedBlockCount();
  const bits = [`已准备 ${done}/${total} 章节${remain ? `，还剩 ${remain} 节` : ''}`];
  if (failed > 0) bits.push(`${failed} 节准备失败，可点「继续准备」重试`);
  if (note) bits.push(note);
  return setStatusBar('paused', bits.join('；'), pct, total, done);
}

// 「重新拆分评分点」= 从头再来：清空已准备内容 + 新起一条 run（重新解析 → 重新派生目录 → 逐节准备）。
// 这是**最贵**的路径，而且新派生的目录可能与用户刚审过的那版不同，所以必须先说清代价，
// 并把"只想接着准备剩下的章节"的用户引导到「继续准备」。
async function confirmReextract() {
  const n = Number(doneBlocks) || 0;
  const total = Number(totalBlocks) || 0;
  const go = await uiConfirm(
    '「重新拆分评分点」会从头跑一遍：\n' +
    `① 清空已准备的 ${n}${total ? `/${total}` : ''} 个章节内容；\n` +
    '② 重新解析要求文件；\n' +
    '③ 重新派生应答目录（新目录可能与当前这版不同）；\n' +
    `④ 重新逐节调用模型${total ? `（约 ${total} 次调用）` : ''}。\n\n` +
    (n > 0 ? '只想继续剩下的章节？点「继续准备」（已完成的章节不会重复调用）。\n\n' : '') +
    '确定重新拆分评分点？');
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
    setStatusBar('extracting', '正在准备编写依据…',
                 totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    // 拆分要求只影响派生：改过却没重新拆分时，目录与输入框不一致。这里问一句，
    // 避免用户在"以为按新要求拆了"的前提下接着准备要点。
    const ta = instructionInput();
    if (ta && instructionDirty(ta.value, appliedInstruction)) {
      const go = await uiConfirm(
        '拆分要求已修改，但当前目录仍是按上一版要求生成的。\n' +
        '继续确认（按当前目录准备要点）？\n\n' +
        '要按新要求重拆，请先取消，再点「重新拆分目录」。');
      if (!go) {
        if (btn) btn.disabled = false;
        // 撤回刚才那句"准备中"，回到闸门（否则按钮禁用着、状态栏还写着准备中）
        currentGate = 'review_outline';
        setStatusBar('gate_outline', '已取消：目录没有变化，核完后可再确认。',
                     totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
        return;
      }
    }
    // 闸门 1 只放行「目录结构」：此刻 8 字段还一个都没准备（卡片里根本没有字段网格，
    // 见 renderBlockCard 的 pending 分支）。历史上这里会把 collectOutlineEdits() 的
    // N 行**全空**矩阵塞给 resume，而 extract 是用新结果整体替换 spec.outline_matrix
    // 的，那 N 行谁都没读过 —— 纯 checkpoint 噪声，去掉。
    // 结构编辑不依赖这里：增删改/排序每次都是即刻 POST /outline-structure 落库。
    freezeOutlineEdit();
    // 结构编辑是**逐次立即保存**的（增删改/排序各有一次 POST）。若用户刚点完上移/
    // 删除就马上点「确认目录…」，resume 可能先到后端 —— extract 读的是 spec.toc，
    // 于是被准备的目录与页面看到的目录不一致（少一节或多一节）。这里等保存队列排空，
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
      // 实际被准备的目录发散。宁可让用户等两秒再点，也不要用旧目录烧一遍模型。
      if (btn) btn.disabled = false;
      currentGate = 'review_outline';
      setStatusBar('gate_outline',
        '目录还在保存中，请稍候两秒再点「确认目录」（避免按旧目录准备）。',
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
    // aclose 过）—— 旧 SSE 连接收不到任何准备帧，卡片会停在骨架屏、等刷新读库
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
        `未能开始准备：${msg}。若是刚刷新过页面，请等这一轮跑完再操作。`,
        totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 0, totalBlocks, doneBlocks);
    }
  }
}

// ── 拆分要求输入（左侧面板底部，闸门 1 可反复改） ──
const OUTLINE_INSTRUCTION_KEY = () => packageCacheKey('outlineInstruction');

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

// 当前目录**是按哪一版拆分要求拆的** —— 以后端闸门快照为准（它是派生的真实输入）。
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

// 「拆分要求改过但没应用到目录」的可见提示（含下一步动作）
function updateInstructionDirtyHint() {
  const hint = document.getElementById('outline-instruction-dirty');
  const ta = instructionInput();
  if (!hint || !ta) return;
  const dirty = instructionDirty(ta.value, appliedInstruction);
  hint.hidden = !dirty;
  if (!dirty) { hint.textContent = ''; return; }
  hint.textContent = appliedInstruction
    ? '拆分要求已修改：当前目录仍是按上一版要求生成的，点「重新拆分目录」后才会生效。'
    : '这段拆分要求还没用于生成目录：点「拆分评分点」（或「重新拆分目录」）后生效。';
}

instructionInput()?.addEventListener('input', () => {
  persistOutlineInstruction();
  updateInstructionDirtyHint();
});

// 闸门 1：换一份拆分要求，让张衡整版重出目录。
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
            '若刚上传了新的要求文件（如技术规范书），请点「重新拆分评分点」—— 会新建一次运行并重新解析全部要求文件。',
  };
}

async function redraftOutline() {
  autoConfirmOutlineAt = 0;   // 主动重来 → 不再自动放行闸门
  // ① 先问后端：能不能重拆。原料变过（删旧文件/传新文件）或已过闸门时，
  //    「重新拆分目录」用的是 checkpoint 里的**旧解析结果**，点下去会返回 200
  //    但页面毫无变化 —— 实测用户连点 8 次。这里直接改走「重新拆分评分点」。
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
        // 「重新拆分评分点」= 新建运行 + 重新解析全部要求文件，并在状态栏说清为什么。
        setStatusBar('extracting',
          '要求文件已变更：目录是按上一次解析的结果生成的，「重新拆分目录」带不上新文件。' +
          '正在改为「重新拆分评分点」（重新解析全部要求文件）…', 0, totalBlocks, doneBlocks);
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

  // 已经准备过一部分时先讲清代价：重出目录 = 换一版 block_id，已准备的内容不再对应
  // （等于要重新拆分评分点）。想改标题/顺序/层级应该用每行的「编辑」，想接着准备用「继续准备」。
  if (Number(doneBlocks) > 0) {
    const go = await uiConfirm(
      `当前已准备 ${doneBlocks}/${totalBlocks} 个章节。\n` +
      '重新拆分目录会用当前的拆分要求重新派生一版目录，已准备的内容不再对应新目录' +
      '（需要重新拆分评分点）。\n\n' +
      '· 只想改章节标题 / 顺序 / 层级？用目录每行的「编辑」。\n' +
      '· 剩下的章节会在流程继续时自动准备。\n\n' +
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
    outlineDrafting = true;
    setStatusBar('extracting', '已提交，正在重新梳理应答目录…', 0, totalBlocks, doneBlocks);
  } catch (e) {
    console.error('[workflow] redraftOutline 失败', e);
    if (await recoverFromDeadThread(e)) return;   // 已作废/线程未知 → 切到可用运行或提示新建
    attachStream(tid);
    const pct = totalBlocks ? Math.round(doneBlocks / totalBlocks * 100) : 100;
    const msg = String(e?.message || e || '');
    // 后端因"已过闸门"拒绝 → 这是**终态**，不再重绘「重新拆分目录」（点了还是失败）。
    // 明确指向唯一有效路径：重新拆分评分点（会重新解析要求文件）。
    if (msg.includes('重新拆分评分点') || !redraftAllowedByStage().ok) {
      setStatusBar('done', `目录不能再改：${msg}`, pct, totalBlocks, doneBlocks);
      statusActions.innerHTML =
        `<button class="btn-status-primary" id="btn-reextract-now">重新拆分评分点（重新解析要求文件）</button>`;
      document.getElementById('btn-reextract-now')
        .addEventListener('click', () => confirmReextract());
      return;
    }
    setStatusBar('gate_outline', `重出目录失败：${msg}`, pct, totalBlocks, doneBlocks);
  }
}

// ── 准备结果确认页：可编辑工具 ──
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

// 「确认内容大纲及要求」：把用户在准备结果上的编辑提交，再进素材库。
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
    location.href = `/three-mode?projectId=${projectId}&threadId=${encodeURIComponent(tid)}&view=editor&backend=generating`;
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
  location.href = `/three-mode${qs}${qs.includes('view=') ? '' : '&view=editor'}`;
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
    // 要求类主文件（招标文件 / 技术规范书）同一角色**只保留最新一份**：
    // 重新选择文件就是替换，不能旧 + 新一起参与解析（用户口径：重新选了就该以新文件为准）。
    const replace = role === 'main_rfp' || role === 'spec';
    await api.materials.upload(projectId, file, role, { replace });
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
  reextract: () => startExtraction(true),      // 重新拆分评分点（重新解析全部要求文件）
  forceRestart: () => forceRestartRun(),       // 强制新建运行（不依赖旧 thread）
  redraft: () => redraftOutline(),             // 重新拆分目录
  state: () => ({ extractionState, currentThreadId, totalBlocks, doneBlocks,
                  thread: getThread(projectId) }),
};

// ── 事件绑定 ──
btnAddReq.addEventListener('click', () => fileInputReq.click());
btnAddSrc.addEventListener('click', () => fileInputSrc.click());
fileInputReq.addEventListener('change', async () => {
  // 以前这里只取 files[0] 且完全没用角色推断 —— 材料条的提示却写着"可一次选多个 /
  // 上传即识别类型"（**提示是假的**，实测核对过）。现在按提示做到：
  //   ① 多选逐个上传；② 类型用 inferRoleFromFilename 按文件名识别，识别不出再用兜底值。
  const files = [...(fileInputReq.files || [])];
  if (!files.length) return;
  const fallback = document.getElementById('req-role-select')?.value || 'main_rfp';
  for (const f of files) {
    // ⚠️ inferRoleFromFilename 返回的是 {role, reason}（不是字符串）—— 直接当 role 传
    // 会把对象写进角色字段（实测核对过返回值形状）。
    let role = fallback;
    try {
      const inferred = inferRoleFromFilename(f.name);
      if (inferred?.role) role = inferred.role;
    } catch { /* 推断失败用兜底 */ }
    await uploadFile(f, role, btnAddReq);
  }
  fileInputReq.value = '';   // 同一个文件可以再传一次
});
fileInputSrc.addEventListener('change', () => {
  const f = fileInputSrc.files[0];
  if (f) uploadFile(f, 'source', btnAddSrc);
});
// 「解析标包」：**上传之后的确认动作** —— 读已上传文件的正文，检索标的/标包/标段。
// 只按原文事实列出（含出处）；找不到就说找不到，不猜。
btnParsePkg?.addEventListener('click', async () => {
  if (btnParsePkg.disabled) return;
  const prev = btnParsePkg.textContent;
  btnParsePkg.disabled = true;
  btnParsePkg.classList.add('is-running');
  btnParsePkg.textContent = '解析中…';
  try {
    pkgAnalysis = null;
    pkgMergeNote = '';
    const d = await loadPackageAnalysis({ refresh: true });
    const n = (d.packages || []).length;
    if (n) {
      setMatHint(`已识别 ${n} 个标包（按招标文件原文）；到「第①步 · 标包」确认要怎么拆`);
      await openSplitReport({ stage: '1' });
    } else if ((d.chunks || 0) === 0) {
      // 最诚实的一种：文件在、但还没有正文可检索
      setMatHint(`文件还没有解析出正文，无法检索标包 —— 先让系统读一遍文件（点「拆分评分点」）`);
    } else {
      setMatHint(d.note || '已检索全部正文，没有找到「标的 / 标包 / 标段」的表述');
    }
  } finally {
    btnParsePkg.disabled = false;
    btnParsePkg.classList.remove('is-running');
    btnParsePkg.textContent = prev || '解析标包';
  }
});

btnExtract.addEventListener('click', async () => {
  // 视图为 matching 时按钮承担「素材匹配」职责
  if (document.body.dataset.view === 'matching') {
    startMatching();
    return;
  }
  // 「拆分评分点」= **从零开始**（用户口径）：
  // 先清空上一次的拆分结果（评分点/规范要求/章节/作答），再新起一个 thread 重跑。
  // 以前"有章节就 redraft"会复用旧 thread 的 config.outline_instruction 与旧作答
  // （范围/类别/主题归属），于是重新上传材料后仍是旧数据在起作用（用户实测反馈）。
  // 也不用 recover 续跑：recover 会越过 interrupt_before 的闸门继续往下。
  restartExtraction();
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

// 自动准备：立即显示准备状态栏，材料加载完后发请求
if (params.get('autoExtract') === '1') {
  statusBar.dataset.state = 'extracting';
  statusLabel.textContent = '正在准备…';
  progressTrack.style.display = 'block';
  progressBar.style.width = '0%';
  statusPct.textContent = '0/0 模块 · 0%';
  statusActions.innerHTML = `<button class="btn-status-secondary" id="btn-cancel">取消</button>`;
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
  // 拆分要求：先按 localStorage 回填，闸门 1 快照到达后再以后端值覆盖空输入框
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
      const k = packageCacheKey('furthestStage');
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
  fetch(`/api/projects/${projectId}/workflow-runs?packageId=${encodeURIComponent(packageId)}`).then(r => r.ok ? r.json() : []).then(runs => {
    const latest = Array.isArray(runs) && runs.length ? runs[0] : null;
    if (!latest) return;
    // thread_id 的恢复**不能**依赖 stage 是否在 STAGE_MAP 里：paused / done / aborted
    // 这些值不在表里，以前会把整段跳过 → currentThreadId 一直是空 → 闸门 1 的
    // 「确认目录」变成静默 return（用户看到"点了没反应"）。
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
    // 而状态栏文案已经按当前视图写好了 —— 顶栏说「已准备」、右栏是匹配面板，自相矛盾。
    // 视图与进度一律以 workflow-runs[0]（项目最新一次 run）为准。
    if (stage === 'failed') {
      // 派生失败等硬失败：**保留 thread**（重试派生要用它），说清原因并给重试入口
      await blocksReady;
      currentGate = null;
      // 同 ①：状态栏不写技术报错（原因看「运行详情」）
      setStatusBar('failed', '',
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
        setStatusBar('paused', '这次运行已作废（已取消或中断），已清空。点「重新拆分评分点」新建一次运行。',
                     0, totalBlocks, doneBlocks);
      }
      return;
    }
    // 后端重启会清空 runner 内存里的 _Run，但 checkpoint 还停在「跑了一半」的 stage 上，
    // 这个 run 已经没有任何东西在推进了（详见 isOrphanedRun）。而 loadExistingBlocks()
    // 只会按库里的章节数写「已解析 N 个章节，可继续准备」—— 中断被包装成了一份正常待办。
    // 这里必须说清楚，并且给出路：继续准备走 recover() 从检查点续跑。
    // 正常停在闸门 1：loadExistingBlocks 只会按数据写「已解析 N 个章节」（那是给中断
    // 场景的文案），这里补回闸门自己的按钮组 —— 先确认目录，再开始准备。
    if (!isOrphanedRun(s)) {
      if (s?.stage === 'outline_review') {
        await blocksReady;
        // checkpoint 停在 outline_review 有三种场景，靠"已准备章节数 + workflow_runs"
        // 区分（extract / match 节点只能在自己结束时写 checkpoint，运行期看不到）：
        //   workflow_runs=extracting 且进程仍认得这个 run → 准备正在跑：重连 SSE 等帧，
        //     不要露出闸门 1 的确认按钮（那会再起一次准备）。
        //   doneBlocks === totalBlocks → 准备已跑完，按完成态停住。
        //   0 < doneBlocks < totalBlocks → 被打断：闸门 1 的确认按钮就是"继续准备"
        //     入口（extract 会跳过已完成的章节）。
        const running = await longPhaseRunning(s);
        if (running === 'extracting') {
          outlineDrafting = false;      // 进入逐节准备：长任务，允许「取消」
      setStatusBar('extracting', '正在准备编写依据…',
                       Math.round(doneBlocks / Math.max(totalBlocks, 1) * 100), totalBlocks, doneBlocks);
          attachStream(tid);
          return;
        }
        if (totalBlocks > 0 && doneBlocks >= totalBlocks) {
          setStatusBar('done', '编写准备完成', 100, totalBlocks, doneBlocks);
          return;
        }
        if (doneBlocks > 0) {
          // 编写准备到一半：给「继续准备 / 重新拆分目录 / 重新拆分评分点」，不是闸门 1 的确认
          currentGate = null;
          setPartialOutlineBar();
          return;
        }
        currentGate = 'review_outline';
        setStatusBar('gate_outline', '目录已拆分，请检查并确认后开始准备要点', 0, totalBlocks, doneBlocks);
      }
      return;
    }
    await blocksReady;  // 等它先写完，否则提示会被它的文案盖掉
    setStatusBar(
      'paused',
      '本次运行已中断（后端重启），章节进度来自上一次运行。点「继续准备」接着跑，或「重新拆分评分点」从头开始。',
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
  // 验证用钩子（无副作用）：模拟"面板刚确认过"与"到达闸门"，用来核对
  // 「确认即放行」这条逻辑，不需要真跑一次模型。
  setAutoConfirm: (on) => { autoConfirmOutlineAt = on ? Date.now() : 0; },
  getAutoConfirm: () => autoConfirmOutlineAt,
  statusBar: (state, label, pct, total, done) => setStatusBar(state, label, pct, total, done),
  confirmOutlineGate,

  refreshCoverage: (tid) => refreshOutlineCoverage(tid || currentThreadId || getThread(projectId)),
  risk: () => toggleScoringRisk(),
  report: (opts) => openSplitReport(opts || {}),
  autoOpen: () => maybeAutoOpenSplitReport(),
  setRevision: (n) => { draftRevision = Number(n) || 0; },
  confirmedRevision: () => getConfirmedRevision(),
};

// 启动「应答目录未派生」的常驻检查（警示由应用异步插入，不能只在渲染时查）
if (typeof document !== 'undefined') {
  watchRawTocWarning();
}
