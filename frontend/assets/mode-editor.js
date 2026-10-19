/* 由 workbench.html 的内联 <script type="module"> 机械抽取而来（2026-10-13 三模式合并）。
   抽成模块的目的：两段内联脚本有 90 个同名顶层标识符 + 18 个重复 DOM id，
   同页拼接必然互相覆盖；各自成为一个模块即各自一个作用域。
   本文件在抽取时**未改任何逻辑**。 */
import { GenerateSession } from './generate-session.js?v=20261016-v279';
// 左栏章节树：与「要求与大纲 / 素材库」共用一份行标记（2026-10-13 左栏归一）
import { chapterNode, renderChapterTree } from './chapter-tree.mjs?v=20261016-v279';
import { initContextPanel } from './context-panel.js?v=20261016-v279';
import { api } from './api.js?v=20261016-v279';
import { request as workspaceRequest, refreshWorkspace, workspace, confirmChapterAction ,
         uiAlert, uiConfirm} from './workspace.js?v=20261016-v279';

// ── 本章要求 bar 的应答条目缓存 ──────────────────────────────
// 必须**声明在模块最前面**：它原本声明在文件中部，而模块之间存在循环依赖
// （workbench.js ↔ mode-editor.js），另一个模块在顶层就会调用 renderReqBar，
// 那时本声明还没执行 → `Cannot access 'responseItemsByBlock' before initialization`
// （TDZ，整页脚本报错；由启动自检面板捕获后才发现）。
let responseItemsByBlock = {};
// 准备字段枚举 + 素材依据的**渲染片段**与 project-init 共用（见 workspace-model.mjs 注释）：
// 性质徽标与出处行的实现在那边只有一份，两个页面不再各写一套（曾经因此漂移出
// "每行显示 #undefined"）。
import { CONSTRAINT_LEVELS, constraintLevelLabel,
         evidenceKindBadgeHtml, evidenceSourceLineHtml, evidenceSnippet,
         chapterStage, CHAPTER_STAGE_LABELS } from './workspace-model.mjs?v=20261016-v279';
initContextPanel();

// 素材抽屉：默认收起（写作时右栏就是正文本身），点工具栏「素材 n ▸」打开；
// 引用动作在抽屉里完成，编辑区底部保留「已引用 n 条」。
(function initMaterialDrawer() {
  const drawer = document.getElementById('mat-drawer-wb');
  const btn = document.getElementById('btn-toggle-materials');
  const close = document.getElementById('btn-close-mat-drawer');
  const toggle = (open) => {
    if (!drawer) return;
    drawer.hidden = open === undefined ? !drawer.hidden : !open;
    btn?.classList.toggle('active', !drawer.hidden);
  };
  btn?.addEventListener('click', () => toggle());
  close?.addEventListener('click', () => toggle(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && drawer && !drawer.hidden) toggle(false);
  });
  window.__toggleMaterialDrawer = toggle;
})();

// 评分点元信息缓存（point_id → {item, factor, points}）。
// ⚠️ 必须在模块顶部声明：renderReqBar/renderChapterPoints 会在**模块初始化早期**
// 就被调用（第 917 行的首屏渲染），若声明写在函数区（更靠后的行）会踩 TDZ，
// 表现为"未处理的 Promise 失败：Cannot access 'pointMetaCache' before initialization"。
let pointMetaCache = null;

const params = new URLSearchParams(location.search);
// projectId 只取数字部分：链接里混进说明文字（如 `?projectId=19**(Cmd+Shift+R)`）时
// 原实现把整串当 id，导致每个接口 422、页面空白且没有任何解释。
const projectId = (params.get('projectId') || '').match(/\d+/)?.[0] || '';
const packageId = (params.get('packageId') || '').match(/\d+/)?.[0] || '';
const packageQuery = packageId ? `&packageId=${encodeURIComponent(packageId)}` : '';
let threadId = params.get('threadId');
// URL ?backend=... 兜底：跨页面跳转时显式带，让进度条第一次绘制就用真实 backend stage
const _backendParam = params.get('backend');
if (_backendParam && projectId) {
  try {
    // 单调推进，避免 URL 旧值覆盖更新的 sessionStorage
    const k = `furthestStage:${projectId}`;
    const prev = sessionStorage.getItem(k);
    const STAGE_ORDER_LITERAL = ['parsing', 'matching', 'generating', 'reviewing'];
    const STAGE_MAP_LITERAL = {
      parsing: 'parsing', outline_review: 'parsing',
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

// ── 顶部 stage 进度条 ──
const STAGE_ORDER = [
  'parsing', 'matching', 'generating', 'reviewing',
];
const STAGE_MAP = {
  parsing: 'parsing',
  // 闸门停留态映射回当前阶段：进度条与项目实际所处步骤一致
  outline_review: 'parsing',
  // extracting：闸门 1 放行后的逐节准备，仍属「解析大纲」这一步
  extracting: 'parsing',
  matching: 'matching',
  materials_review: 'matching',
  generating: 'generating',
  reviewing: 'reviewing',
  // 报告确认与双视角评审合并为同一 step
  report_review: 'reviewing',
};

// 当前页面承担的阶段：仅作 backend 兜底（用户已在该页 → graph 至少到这）
const PAGE_STAGE = 'generating';
let currentBackendStage = (() => {
  if (_backendParam && _backendParam.trim()) return _backendParam;
  try {
    const cached = sessionStorage.getItem(`furthestStage:${projectId}`);
    if (cached) return cached;
  } catch {}
  return null;
})();

function setStage(stage) {
  if (!stage) return;
  // 单调推进：避免 SSE / state API 短暂返回更早的 stage 把进度条 active 拉回来
  if (currentBackendStage && stage !== 'aborted' && stage !== 'done') {
    const prevMapped = STAGE_MAP[currentBackendStage];
    const newMapped = STAGE_MAP[stage];
    const prevIdx = prevMapped ? STAGE_ORDER.indexOf(prevMapped) : -1;
    const newIdx = newMapped ? STAGE_ORDER.indexOf(newMapped) : -1;
    if (newIdx >= 0 && prevIdx >= 0 && newIdx < prevIdx) return;
  }
  currentBackendStage = stage;
  // 维护项目级最远到达 stage（跨页面复用）
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
  renderStageBar();
  if (typeof _refreshGoReviewBtn === 'function') _refreshGoReviewBtn();
}

function renderStageBar() {
  const root = document.getElementById('stage-progress');
  if (!root) return;
  if (root.hasAttribute('hidden')) root.removeAttribute('hidden');

  const pageIdx = STAGE_ORDER.indexOf(PAGE_STAGE);
  let backend = currentBackendStage;
  if (!backend) {
    try {
      const cached = sessionStorage.getItem(`furthestStage:${projectId}`);
      if (cached) backend = cached;
    } catch {}
  }
  if (!backend) backend = PAGE_STAGE;
  let backendMapped = STAGE_MAP[backend];
  let backendIdx = backendMapped ? STAGE_ORDER.indexOf(backendMapped) : -1;
  if (backend === 'done') backendIdx = STAGE_ORDER.length;
  if (backend === 'aborted') backendIdx = pageIdx;
  if (backendIdx < pageIdx) backendIdx = pageIdx;

  const steps = root.querySelectorAll('.stage-step');

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
  const tid = threadId || sessionStorage.getItem(`workflow:${projectId}`);
  if (!tid) return;
  // 跳转前把 backend stage 写 sessionStorage（跨页面继承）+ URL（同步可靠源）
  let backendForUrl = currentBackendStage;
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
  const enc = encodeURIComponent(tid);
  const backendQuery = backendForUrl ? `&backend=${encodeURIComponent(backendForUrl)}` : '';
  const routes = {
    parsing: `/three-mode?projectId=${projectId}${packageQuery}&threadId=${enc}&view=outline${backendQuery}`,
    matching: `/three-mode?projectId=${projectId}${packageQuery}&threadId=${enc}&view=matching${backendQuery}`,
    generating: `/three-mode?projectId=${projectId}${packageQuery}&threadId=${enc}&view=editor${backendQuery}`,
    reviewing: `/review?projectId=${projectId}${packageQuery}&threadId=${enc}${backendQuery}`,
    report_review: `/review?projectId=${projectId}${packageQuery}&threadId=${enc}${backendQuery}`,
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

// 进入页面立刻按 PAGE_STAGE 点亮当前 step
renderStageBar();

// 撰写完成 banner — 进入评审按钮
document.querySelector('#generation-done-banner .btn-go-review')?.addEventListener('click', () => {
  if (!threadId) return;
  // 主动推进顶部进度条到"双视角评审"，给用户视觉过渡 200ms 看到变化再跳转
  setStage('reviewing');
  setTimeout(() => {
    location.href = `/review?projectId=${projectId}${packageQuery}&threadId=${encodeURIComponent(threadId)}`;
  }, 200);
});

// 顶部常驻「进入评审」按钮：用户随时可点（包括暂停态），调 skipToReview 让 graph
// 用已生成的 block 跑评审，然后跳 /review 页。
const btnGoReviewTop = document.getElementById('btn-go-review-top');
if (btnGoReviewTop) {
  btnGoReviewTop.addEventListener('click', async () => {
    if (!threadId) return;
    btnGoReviewTop.disabled = true;
    btnGoReviewTop.textContent = '准备评审…';
    try {
      // 已经在 review 阶段直接跳；否则先 skipToReview 让 graph 跑评审
      const s = await api.workflow.state(threadId).catch(() => null);
      const stage = s?.stage;
      if (stage !== 'reviewing' && stage !== 'report_review' && stage !== 'done') {
        await api.workflow.skipToReview(threadId).catch(() => {});
      }
      location.href = `/review?projectId=${projectId}${packageQuery}&threadId=${encodeURIComponent(threadId)}`;
    } catch (e) {
      console.error('[workbench] go-review-top failed', e);
      btnGoReviewTop.disabled = false;
      btnGoReviewTop.textContent = '进入评审 →';
    }
  });
}
// 当 backend stage 进入 generating 之后，按钮变可点（之前不可，避免还没生成就跳评审）。
// 用一个 helper 在 setStage / 初次渲染时调。
// 已生成 block 数：进入评审按钮启用判定使用，SSE block_done 时累加
let _generatedCount = 0;
function _refreshGoReviewBtn() {
  if (!btnGoReviewTop) return;
  // 启用条件：至少 1 个 block 已生成（用户需求："撰写到一半也可进入评审"）。
  // 兜底：currentBackendStage 已到 generating 也启用（防止 SSE 还没推 block_done）。
  const cur = currentBackendStage || sessionStorage.getItem(`furthestStage:${projectId}`);
  const mapped = cur ? STAGE_MAP[cur] : null;
  const idx = mapped ? STAGE_ORDER.indexOf(mapped) : -1;
  const stageReady = idx >= STAGE_ORDER.indexOf('generating');
  btnGoReviewTop.disabled = !(stageReady || _generatedCount > 0);
}
// 进入页面时拉一次 blocks 看是否已有内容
(async () => {
  try {
    const list = await fetch(`/api/projects/${projectId}/blocks${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(r => r.json());
    if (Array.isArray(list)) {
      _generatedCount = list.filter(b => b?.content && String(b.content).trim()).length;
      _refreshGoReviewBtn();
    }
  } catch {}
})();
_refreshGoReviewBtn();

// 初始化时还原 stage + stage routing（强制 threadId 模式）
(async () => {
  // 解析 threadId：URL 优先，fallback sessionStorage
  let tid = threadId;
  if (!tid && projectId) {
    try {
      tid = sessionStorage.getItem(`workflow:${projectId}`);
    } catch {}
  }

  if (!tid) {
    // 强制：没 thread 不能进 workbench，跳回 project-init 让用户启动工作流
    // 固定工作区保持可访问；空态由共享工作区展示。
    return;
  }

  // 把解析到的 tid 同步到模块顶层 threadId，下游 startWorkflowGeneration / abort / regen 都依赖它
  threadId = tid;

  // URL 不带 threadId 但 sessionStorage 命中时，同步 URL 让刷新仍生效
  if (!params.get('threadId')) {
    try {
      const newUrl = new URL(location.href);
      newUrl.searchParams.set('threadId', tid);
      history.replaceState(null, '', newUrl);
    } catch {}
  }

  let s;
  try {
    s = await api.workflow.state(tid);
  } catch {
    // state fetch 失败：清掉过期 sessionStorage，跳回 project-init
    try { sessionStorage.removeItem(`workflow:${projectId}`); } catch {}
    // 固定工作区保持可访问；空态由共享工作区展示。
    return;
  }

  const stage = s?.stage;
  if (!stage || stage === 'idle') {
    // 没有真实推进，跳回 project-init
    try { sessionStorage.removeItem(`workflow:${projectId}`); } catch {}
    // 固定工作区保持可访问；空态由共享工作区展示。
    return;
  }

  // 撰写完成后顶部进度条停在"方案撰写" active；reviewing/report_review/done 在下方 case
  // 内部自行 setStage('generating')，此处先跳过 setStage 避免一次闪烁推进。
  if (stage !== 'reviewing' && stage !== 'report_review' && stage !== 'done') {
    setStage(stage);
  }

  // stage routing — 根据当前阶段决定页面行为
  // 注意：闸门 2 用户点确认后到 generate_node 写出 stage='generating' 之间有窗口
  // （runner 在后台 task 里跑），workbench 进来时 stage 仍可能停在 'materials_review'，
  // 但 user_choice='approve' 已写盘 — 这种"在途"状态不能 bounce 回 project-init。
  switch (stage) {
    case 'parsing':
    case 'outline_review':
    // extracting：逐节准备还在跑，同样属于「前置步骤未完成」
    case 'extracting':
    case 'matching':
      // 前置阶段：用户大概率从 stage-progress 点击「方案撰写」误进；不再硬 redirect
      // 否则会出现"闪一下方案撰写后跳回素材匹配"的体验。改为提示+引导。
      try {
        const banner = document.createElement('div');
        banner.style.cssText = 'padding:12px 20px;background:#fff7ed;color:#9a3412;border-bottom:1px solid #fdba74;font-size:13px;text-align:center;';
        banner.innerHTML = `项目尚未到达「方案撰写」阶段，请先完成 <a href="/three-mode?projectId=${projectId}&threadId=${encodeURIComponent(tid)}&view=outline" style="color:#1d4ed8;text-decoration:underline;">前置步骤</a>。`;
        // 合并页里三个模式都在同一页，"回前置步骤"的引导没有意义，且这条横幅
        // 会把头部整体下推（实测刷新后布局变样的元凶）。改为不插入。
        if (!isMergedPage()) insertNotice(banner);
      } catch {}
      return;
    case 'materials_review':
      if (s?.user_choice === 'approve') {
        // 闸门 2 已确认，generate_node 还没写出 'generating'。视作 generating 处理。
        setStage('generating');  // 在 SSE stage_change 来之前提前推进 UI
        autoStartWorkflowStream();
        return;
      }
      // user 还没点"确认素材"——不再 redirect 回 project-init（避免"闪一下方案撰写又跳回"）；
      // 留在 workbench 显示提示，引导用户回素材匹配页面确认。
      try {
        setStage('generating');
        const banner = document.createElement('div');
        banner.style.cssText = 'padding:12px 20px;background:#fff7ed;color:#9a3412;border-bottom:1px solid #fdba74;font-size:13px;text-align:center;';
        banner.innerHTML = `素材匹配尚未确认，请先回 <a href="/three-mode?projectId=${projectId}&threadId=${encodeURIComponent(tid)}&view=matching" style="color:#1d4ed8;text-decoration:underline;">素材匹配页面</a> 点击「确认素材，进入撰写」。`;
        if (!isMergedPage()) insertNotice(banner);
      } catch {}
      return;
    case 'generating': {
      // 仅当 graph 后台 task 真的还在跑时才自动开流；否则只展示已生成的内容，
      // 避免"已生成完毕的项目重新进入工作台时，UI 进入批量进度条状态"的误导。
      try {
        const dbBlocks = await fetch(`/api/projects/${projectId}/blocks${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(r => r.json());
        const list = Array.isArray(dbBlocks) ? dbBlocks : [];
        const contentBlocks = list.filter(b => Number(b.level) >= 2 || b.kind !== 'heading');
        const empty = contentBlocks.filter(b => !(b.content && String(b.content).trim()));
        if (empty.length === 0 && contentBlocks.length > 0) {
          // 全部章节均已落库 → 直接展示，不开 SSE 流，不显示批量进度条
          if (typeof window.__loadBlocks === 'function') {
            try { window.__loadBlocks(); } catch {}
          }
          return;
        }
      } catch {}
      // 否则正常自动开流（graph 在途，等 SSE 推 block_done）
      autoStartWorkflowStream();
      return;
    }
    case 'reviewing':
    case 'report_review':
    case 'done': {
      // 留在 workbench：顶部进度条停在"方案撰写" active（不自动推进到 reviewing/done），
      // 用户点击"进入评审"按钮才主动 setStage('reviewing') + 跳 review。
      setStage('generating');
      // 保险：让左侧大纲 + 中间编辑区有数据可用（workbench.js 顶层已 loadBlocks，
      // 此处兜底覆盖竞争条件 / 重复刷新场景）。
      if (typeof window.__loadBlocks === 'function') {
        try { window.__loadBlocks(); } catch {}
      }
      const banner = document.getElementById('generation-done-banner');
      if (banner) banner.removeAttribute('hidden');
      return;
    }
    case 'aborted':
      showAbortedBanner();
      return;
    default:
      // 未知 stage — 保持现有手动模式
      return;
  }
})();

// stage routing 辅助函数 — 自动开流（复用现有 startWorkflowGeneration）
async function autoStartWorkflowStream() {
  if (workflowStream) return;
  await startWorkflowGeneration();
}

// 合并页（一个页面承载三个模式）判定。这些"你走错页面了，请回前置步骤"的横幅
// 在合并页里前提就不成立 —— 方案编辑就是同一个页面里的一个模式。
function isMergedPage() {
  return (document.body.dataset.modes || '').includes('editor');
}

/**
 * 把横幅插到**不改变全局布局**的位置。
 *
 * 原实现是"插到 #stage-progress 之后，否则 prepend 到 body/shell"。但合并页里
 * 从来没有 #stage-progress（它本来就是空引用），于是走了兜底 prepend ——
 * 一条 43px 的橙色横幅被塞到整页最前面，把 header 和所有内容往下推。
 * 实测：刷新后编辑器区从 619px 变成 576px，看起来就是"刷新之后样式变了"。
 */
function insertNotice(node) {
  const stageBar = document.getElementById('stage-progress');
  if (stageBar && stageBar.parentNode) {
    stageBar.parentNode.insertBefore(node, stageBar.nextSibling);
    return;
  }
  // 没有阶段条：**追加**到编辑器区内（不 prepend、不动全局布局）
  const host = document.querySelector('.editor-pane')
    || document.querySelector('.workspace-main-shell')
    || document.body;
  host.appendChild(node);
}

// stage routing 辅助函数 — aborted 状态横幅
function showAbortedBanner() {
  const banner = document.createElement('div');
  banner.style.cssText = 'padding:12px 20px;background:#fee2e2;color:#991b1b;border-bottom:1px solid #fca5a5;font-size:13px;text-align:center;';
  banner.innerHTML = '工作流已作废。<a href="/projects" style="color:#1d4ed8;text-decoration:underline;margin-left:8px;">返回项目列表</a>';
  // 合并页里不弹：作废状态已由工作区状态条说明，再顶一条横幅只会挤布局
  if (isMergedPage()) return;
  insertNotice(banner);
}


let session = null;
let total = 0;
let doneCount = 0;
let workflowStream = null;
// 批量生成进行中标记 — 仅当批量入口（startWorkflowGeneration / 自动批量）开启时为 true，
// 单 block regen 通过同一个 ensureWorkflowStream 路径打开 SSE，但不应触发批量进度条 UI。
let batchInFlight = false;

const btnGen = document.getElementById('btn-generate-all');
const progress = document.getElementById('generate-progress');
const genCard = document.getElementById('gen-status-card');
const genTitle = document.getElementById('gen-status-title');
const genBar = document.getElementById('gen-status-bar');
const genPct = document.getElementById('gen-status-pct');
const genCancel = document.getElementById('gen-status-cancel');

function updateGenCard(title, index, total) {
  const pct = total > 0 ? Math.round((index / total) * 100) : 0;
  if (genTitle) genTitle.textContent = title || '—';
  if (genBar) genBar.style.width = pct + '%';
  if (genPct) genPct.textContent = pct + '%';
}

genCancel?.addEventListener('click', () => { if (session?.running) session.stop(); });

window.addEventListener('session:start', e => {
  total = e.detail.total || 0;
  if (btnGen) btnGen.textContent = '■ 停止';
  if (progress) { progress.hidden = false; progress.textContent = `0 / ${total}`; }
  // 空闲不显示进度卡：一张「— 0% 取消」的空卡挂在顶上很难看，还占编辑区高度。
  // 真正的第一帧进度（onBlockStart）才把它亮出来。
  if (genCard) genCard.hidden = true;
  updateGenCard('', 0, total);
});

window.addEventListener('block:start', e => {
  const { title = '', index = 0 } = e.detail;
  if (genCard) genCard.hidden = false;   // 有真实进度了才显示
  updateGenCard(title, index, total);
  if (progress) progress.textContent = `${index} / ${total}`;
});

window.addEventListener('block:done', () => {
  doneCount++;
  if (progress) progress.textContent = `${doneCount} / ${total}`;
  if (genTitle) updateGenCard(genTitle.textContent, doneCount, total);
});

window.addEventListener('session:done', () => {
  if (btnGen) btnGen.innerHTML = '✦ 批量编写';
  if (progress) progress.hidden = true;
  doneCount = 0;
  session = null;
  if (genCard) genCard.hidden = true;
});

btnGen?.addEventListener('click', async () => {
  if (workflowStream) {
    // 正在跑 workflow → 取消
    if (threadId) await api.workflow.abort(threadId).catch(() => {});
    onWorkflowAborted();
    return;
  }
  if (threadId) {
    const ok = await startWorkflowGeneration();
    if (ok) return;
  }
  if (session?.running) { session.stop(); return; }
  doneCount = 0;
  session = new GenerateSession(projectId);
  session.start();
});

// ── 新工作流流式接入（threadId 模式）───────────────────────
function findEditableForBlock(blockIdStr) {
  // 优先用 outline 项的 data-block-id-str（extract 流程下的 DOM 结构）
  const item = document.querySelector(`.doc-outline-item[data-block-id-str="${blockIdStr}"]`);
  if (!item) return null;
  // 选中该 outline 项以让右侧编辑器跟随当前 block
  if (!item.classList.contains('active')) item.click();
  return document.getElementById('ced-editable');
}

// 幂等打开 SSE 流并注册 handler — 不切 btnGen UI，单 block regen 也能调用
function ensureWorkflowStream() {
  if (!threadId) return false;
  if (workflowStream) return true;
  const seen = new Set();
  const batchStatusEl = document.getElementById('outline-batch-status');
  const batchCurrentEl = document.getElementById('outline-batch-current');
  const batchBarEl = document.getElementById('outline-batch-bar');
  const batchMetaEl = document.getElementById('outline-batch-meta');
  const updateBatchProgress = () => {
    if (batchBarEl && total > 0) {
      batchBarEl.style.width = `${Math.round(doneCount / total * 100)}%`;
    }
    if (batchMetaEl) batchMetaEl.textContent = `${doneCount} / ${total || '?'}`;
  };
  workflowStream = api.workflow.stream(threadId, {
    onBlockStart: (data) => {
      const bid = data.block_id;
      if (bid && !seen.has(bid)) { seen.add(bid); total++; }
      // 真的有章节开始跑了：这时才显示进度卡（并同步标题/百分比）
      if (genCard) { genCard.hidden = false; genCard.style.display = ''; }
      updateGenCard(data.title || bid || '', doneCount, total);
      const editable = findEditableForBlock(bid);
      if (editable) {
        editable.textContent = '';
        editable.style.display = '';
      }
      if (genTitle) genTitle.textContent = data.title || bid || '';
      // 批量生成才显示批量进度条；单 block regen 不动 batch UI
      if (!batchInFlight) return;
      if (batchStatusEl) batchStatusEl.classList.remove('hidden');
      if (batchCurrentEl) batchCurrentEl.textContent = data.title || bid || '撰写中…';
      updateBatchProgress();
    },
    onBlockToken: (data) => {
      const editable = findEditableForBlock(data.block_id);
      if (editable) editable.appendChild(document.createTextNode(data.token || ''));
    },
    onBlockDone: (data) => {
      const editable = findEditableForBlock(data.block_id);
      if (editable) {
        if (typeof renderSourceMarked === 'function') {
          renderSourceMarked(data.content || '', editable);
        } else {
          editable.textContent = data.content || '';
        }
      }
      if (typeof markOutlineDone === 'function' && data.block_id) {
        try { markOutlineDone(data.block_id, data); } catch {}
      }
      // 单 block 生成完毕：进入评审按钮启用条件之一
      if (typeof _generatedCount !== 'undefined') {
        _generatedCount++;
        if (typeof _refreshGoReviewBtn === 'function') _refreshGoReviewBtn();
      }
      // 单 block regen 完成 → 复位编辑器顶部 banner / 进度
      if (window.__currentSingleRegenBlockId && window.__currentSingleRegenBlockId === data.block_id) {
        const banner = document.getElementById('ced-gen-banner');
        const pbar = document.getElementById('ced-gen-pbar');
        const pct = document.getElementById('ced-gen-pct');
        const stage = document.getElementById('ced-gen-stage');
        if (typeof finishGenerate === 'function' && banner) {
          try { finishGenerate(banner, pbar, pct, stage); } catch {}
        }
        window.__currentSingleRegenBlockId = null;
      }
      // 批量进度只在批量生成进行中累加并刷新批量 UI
      if (!batchInFlight) return;
      doneCount++;
      if (genBar && total > 0) {
        const pct = Math.round(doneCount / total * 100);
        genBar.style.width = pct + '%';
        if (genPct) genPct.textContent = pct + '%';
      }
      updateBatchProgress();
    },
    onError: (data) => {
      console.error('workflow error', data);
      if (data && data.block_id) {
        const item = document.querySelector(`.doc-outline-item[data-block-id-str="${data.block_id}"]`);
        if (item) item.classList.add('block-error');
      }
    },
    onAborted: () => onWorkflowAborted(),
    onDone: () => onWorkflowDone(),
    onPaused: (data) => onWorkflowPaused(data),
    onStageChange: (data) => {
      const stage = data?.stage;
      if (typeof stage !== 'string') return;
      // 撰写完成后顶部进度条停在"方案撰写" active；reviewing/report_review/done 不再 setStage，
      // 仅显示完成 banner，等用户主动点"进入评审"按钮才推进进度条 + 跳 review。
      if (stage === 'reviewing' || stage === 'report_review' || stage === 'done') {
        const banner = document.getElementById('generation-done-banner');
        if (banner) banner.removeAttribute('hidden');
        return;
      }
      setStage(stage);
    },
  });
  return true;
}

async function startWorkflowGeneration() {
  if (!threadId) return false;
  closeWorkflowStream();
  if (btnGen) btnGen.textContent = '■ 停止';
  // 先隐藏，等 onBlockStart 的第一帧再亮（否则会出现一张「— 0% 取消」的空卡）
  if (genCard) { genCard.hidden = true; genCard.style.display = ''; }
  doneCount = 0;
  // total 在 block_start 累计；进度条按已知 total 推进
  total = 0;
  // 批量入口：开启批量进度条 UI；ensureWorkflowStream 内的 onBlockStart/onBlockDone 据此判断
  batchInFlight = true;
  return ensureWorkflowStream();
}

function closeWorkflowStream() {
  if (workflowStream) {
    try { workflowStream.close(); } catch {}
    workflowStream = null;
  }
}

function onWorkflowAborted() {
  if (btnGen) btnGen.innerHTML = '✦ 批量编写';
  if (genCard) { genCard.hidden = true; genCard.style.display = 'none'; }
  const batchStatusEl = document.getElementById('outline-batch-status');
  if (batchStatusEl) batchStatusEl.classList.add('hidden');
  batchInFlight = false;
  closeWorkflowStream();
}

function onWorkflowDone() {
  closeWorkflowStream();
  if (btnGen) btnGen.innerHTML = '✦ 批量编写';
  const batchStatusEl = document.getElementById('outline-batch-status');
  const batchCurrentEl = document.getElementById('outline-batch-current');
  const batchBarEl = document.getElementById('outline-batch-bar');
  // 仅当批量生成进行中才动批量进度条文案 / 宽度（避免单 block regen 触发后的 done 事件污染）
  if (batchInFlight) {
    if (batchCurrentEl) batchCurrentEl.textContent = '已完成';
    if (batchBarEl) batchBarEl.style.width = '100%';
    if (batchStatusEl) {
      setTimeout(() => batchStatusEl.classList.add('hidden'), 1500);
    }
  }
  batchInFlight = false;
  // 不自动跳转 — 不 setStage，仅显示完成 banner，让用户主动点击"进入评审"
  const banner = document.getElementById('generation-done-banner');
  if (banner) banner.removeAttribute('hidden');
}

function onWorkflowPaused(data) {
  // 暂停事件：保留已生成的 block 内容，graph 已停在 GATE_PAUSE 闸门；
  // 显示提示卡让用户三选一：继续生成 / 仅评审已生成 / 放弃工作流。
  closeWorkflowStream();
  batchInFlight = false;
  batchGenerating = false;
  if (btnGen) btnGen.innerHTML = '✦ 批量编写';
  const batchStatusEl = document.getElementById('outline-batch-status');
  if (batchStatusEl) batchStatusEl.classList.add('hidden');
  const btnBatchCancelEl = document.getElementById('btn-batch-cancel');
  if (btnBatchCancelEl) {
    btnBatchCancelEl.disabled = false;
    btnBatchCancelEl.textContent = '暂停';
  }
  if (btnBatchGen) btnBatchGen.style.display = '';
  // 暂停后顶部「进入评审」按钮立即可点（用已生成的 block 跑评审）
  if (typeof _refreshGoReviewBtn === 'function') {
    _generatedCount = Math.max(_generatedCount, data?.generated ?? 0);
    _refreshGoReviewBtn();
  }
  // 渲染 / 复用提示卡
  let card = document.getElementById('paused-banner');
  if (!card) {
    card = document.createElement('div');
    card.id = 'paused-banner';
    card.style.cssText = 'padding:14px 20px;background:#fff7ed;border:1px solid #fdba74;color:#9a3412;border-radius:8px;margin:12px 20px;font-size:13px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;';
    const banner = document.getElementById('generation-done-banner');
    // 有 generation-done-banner 就插它前面（原地），否则**追加**到编辑器区，
    // 不用 prepend：prepend 会把卡片塞到 shell 最前面、挤动整页布局。
    if (banner?.parentNode) banner.parentNode.insertBefore(card, banner);
    else (document.querySelector('.editor-pane')
          || document.querySelector('.workspace-main-shell')
          || document.body).appendChild(card);
  }
  const generated = data?.generated ?? 0;
  const total = data?.total ?? 0;
  card.innerHTML = `
    <span style="flex:1;">已暂停撰写。已生成 <b>${generated}/${total}</b> 个章节。</span>
    <button type="button" class="btn-secondary" id="paused-resume">继续生成</button>
    <button type="button" class="btn-primary" id="paused-skip">仅评审已生成</button>
    <button type="button" class="btn-danger" id="paused-abort">放弃工作流</button>`;
  const resumeBtn = card.querySelector('#paused-resume');
  const skipBtn = card.querySelector('#paused-skip');
  const abortBtn = card.querySelector('#paused-abort');
  resumeBtn?.addEventListener('click', async () => {
    resumeBtn.disabled = true;
    skipBtn.disabled = true;
    abortBtn.disabled = true;
    try {
      await api.workflow.resumeGeneration(threadId);
      card.remove();
      startWorkflowGeneration();
    } catch (e) { console.error('resumeGeneration failed', e); resumeBtn.disabled = false; skipBtn.disabled = false; abortBtn.disabled = false; }
  });
  skipBtn?.addEventListener('click', async () => {
    resumeBtn.disabled = true;
    skipBtn.disabled = true;
    abortBtn.disabled = true;
    try {
      await api.workflow.skipToReview(threadId);
      card.remove();
      // 等 graph 跑评审 → reviewing/report_review，stage_change 收到时显示完成 banner
      ensureWorkflowStream();
    } catch (e) { console.error('skipToReview failed', e); resumeBtn.disabled = false; skipBtn.disabled = false; abortBtn.disabled = false; }
  });
  abortBtn?.addEventListener('click', async () => {
    resumeBtn.disabled = true;
    skipBtn.disabled = true;
    abortBtn.disabled = true;
    try {
      await api.workflow.abort(threadId);
      card.remove();
    } catch (e) { console.error('abort failed', e); }
  });
}

window.addEventListener('pagehide', closeWorkflowStream);

// ── 编辑器数据绑定 ──────────────────────────────────────────────
let currentBlock = null;
let editorDirty = false;
window.__editorDirty = () => editorDirty;
window.__discardEditorChanges = () => { editorDirty = false; };
window.addEventListener('beforeunload', e => {
  if (editorDirty) { e.preventDefault(); e.returnValue = ''; }
});
let editorGenerating = false;
// blockId → 最新内容（批量生成后同步）
const blockContentCache = new Map();

// 暴露 selectBlock 到全局供 workbench.js 调用
window.__onBlockSelected = function(b) {
  // 原来是同步的原生 confirm；改成统一的 uiConfirm（异步）。
  // 返回 false 让调用方知道"这次切换没发生"，语义保持不变。
  if (editorDirty) {
    uiConfirm('当前章节未保存，是否放弃修改并切换章节？',
              { title: '未保存的修改', confirmText: '放弃修改并切换', danger: true })
      .then((ok) => { if (ok) switchBlockDiscarding(b); });
    return false;
  }
  switchBlockDiscarding(b);
};

function switchBlockDiscarding(b) {
  editorDirty = false;
  currentBlock = b;
  window.__currentEditorBlock = b;
  showEditorWorkspace(b);
  renderPageHint(b);
  // 切回编辑模式（避免新 block 还显示上一份预览）
  setEditorMode('edit');
  // 标题由顶部条展示（#et-title，见 assets/workbench.js 的 renderEditorTopbar）
  return true;
};
window.__refreshEditorChapter = function(b) {
  if (!editorDirty && currentBlock?.id === b.id && currentBlock.content !== b.content) {
    currentBlock = b; window.__currentEditorBlock = b;
    showEditorWorkspace(b);
  }
};
window.dispatchEvent(new Event('workspace:editor-ready'));

// 编辑/预览模式切换：编辑态用 contenteditable，预览态用 marked 渲染 markdown
function setEditorMode(mode) {
  const editable = document.getElementById('ced-editable');
  const preview  = document.getElementById('ced-preview');
  const ph       = document.getElementById('ced-placeholder');
  if (!editable || !preview) return;
  if (mode === 'preview') {
    const md = (editable.innerText || editable.textContent || '').trim();
    if (!md) {
      preview.innerHTML = '<div style="color:var(--ink-3);font-size:13px;">（暂无正文，请先在编辑模式下撰写或调用 AI 生成）</div>';
    } else if (typeof window.marked !== 'undefined') {
      const rawHtml = window.marked.parse(md, { breaks: true, gfm: true });
      preview.innerHTML = decoratePlaceholders(rawHtml);
    } else {
      preview.innerText = md;
    }
    editable.style.display = 'none';
    if (ph) ph.style.display = 'none';
    preview.style.display = '';
  } else {
    preview.style.display = 'none';
    editable.style.display = (editable.innerText || '').trim() ? '' : 'none';
    if (ph) ph.style.display = (editable.innerText || '').trim() ? 'none' : '';
  }
  document.querySelectorAll('#ced-mode-switch .ced-mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
}

// 占位符美化：架构图/流程图/拓扑图/部署图/时序图 → 灰底虚线占位图；待补充 → 黄高亮；公司全称等变量 → 蓝高亮
function decoratePlaceholders(html) {
  // 图形占位：【架构图：xx】【流程图：xx】【拓扑图：xx】【部署图：xx】【时序图：xx】
  html = html.replace(/【(架构图|流程图|拓扑图|部署图|时序图|网络图|示意图)[：:]\s*([^】]+)】/g,
    (_, type, desc) => `<div class="ced-ph-img">${escHtml(type)}<br/>${escHtml(desc)}</div><div class="ced-ph-img-caption">图：${escHtml(desc)}</div>`);
  // 待补充：【待补充：xx】
  html = html.replace(/【待补充[：:]\s*([^】]+)】/g,
    (_, desc) => `<span class="ced-ph-todo">待补充：${escHtml(desc)}</span>`);
  // 变量占位：【公司全称】【签署日期】【签字人】【法定代表人】等
  html = html.replace(/【(公司全称|公司名称|投标人名称|签署日期|签字日期|签字人|法定代表人|授权代表|联系电话|联系地址|项目名称|项目编号)】/g,
    (_, name) => `<span class="ced-ph-var">${escHtml(name)}</span>`);
  return html;
}

document.querySelectorAll('#ced-mode-switch .ced-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => setEditorMode(btn.dataset.mode));
});

// 预估页数：requirement+key_points 字数占比 × target_pages
function renderPageHint(b) {
  const hint = document.getElementById('ced-page-hint');
  if (!hint) return;
  const target = Number(window.__projectTargetPages || 0);
  const all = Array.isArray(window.__allBlocks) ? window.__allBlocks : [];
  if (!target) {
    hint.hidden = true; hint.textContent = ''; return;
  }
  if (!b || b.kind !== 'content') {
    hint.hidden = true; hint.textContent = ''; return;
  }
  const leaves = all.filter(x => x && x.kind === 'content');
  const charsOf = (x) => ((x.requirement || '') + (x.key_points || '')).length;
  const total = leaves.reduce((s, x) => s + charsOf(x), 0);
  if (total <= 0) {
    const each = Math.max(1, Math.round(target / Math.max(leaves.length, 1)));
    hint.textContent = `约 ${each} 页 / 共 ${target} 页`;
  } else {
    const ratio = charsOf(b) / total;
    const pages = Math.max(1, Math.round(target * ratio));
    hint.textContent = `约 ${pages} 页 / 共 ${target} 页`;
  }
  hint.hidden = false;
}

function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function showEditorWorkspace(b) {
  // 显示工作区
  const sh = document.getElementById('section-header');
  if (sh) sh.style.display = 'flex';
  document.getElementById('editor-empty-hint').style.display = 'none';
  const ws = document.getElementById('editor-workspace');
  ws.style.display = 'flex';

  // 面包屑
  const bc = document.querySelector('[data-breadcrumb]');
  if (bc) bc.textContent = b.parent_title ? `${b.parent_title} › ${b.title}` : (b.title || '');

  // 标题
  const sTitle = document.getElementById('section-title');
  if (sTitle) sTitle.textContent = b.title || '—';

  // 渲染折叠卡 + 本章要求 bar（条目编号与「素材库」同一份 response_items）
  renderRefCards(b);
  renderReqBar(b);
  renderChapterPoints(b);

  // 编辑器内容：优先使用缓存（批量生成后已更新），其次用 block.content
  const editable = document.getElementById('ced-editable');
  const placeholder = document.getElementById('ced-placeholder');
  const hint = document.getElementById('ced-ai-hint');
  const content = b.content || '';
  blockContentCache.set(b.id, content);
  // 先全部重置，再按内容状态设置
  editable.style.display = 'none';
  editable.textContent = '';
  placeholder.style.display = '';
  if (hint) hint.style.display = 'flex';
  if (content) {
    if (!renderAnnotatedContent(content, editable)) {
      editable.textContent = content;
    }
    editable.style.display = '';
    placeholder.style.display = 'none';
    if (hint) hint.style.display = 'none';
  }
  updateWordCount();

  // 保存时间（顶部条 + 卡片页脚共用一份）
  setSavedTime(b.updated_at
    ? `已保存 · ${new Date(b.updated_at).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'})}`
    : '—');
}

// 保存状态在两处显示：右栏顶部条（显眼）+ 编辑卡页脚（贴近正文）。
// 以前只写页脚，用户切章节时看不到"这一章存了没有"。
function setSavedTime(text) {
  const el = document.getElementById('ced-save-time');   // 现在在右栏顶部条上
  if (el) el.textContent = text;
}

// ── 本章要求 bar ────────────────────────────────────────
// 写作时唯一必须常驻的参照：本章要回应什么 + 每条有没有素材依据。
// 数据与「素材库」同源：GET /response-items（后端展开，R/K/V/B/S/E/I 编号一致）。
async function loadResponseItems() {
  if (Object.keys(responseItemsByBlock || {}).length) return responseItemsByBlock;
  try {
    const r = await fetch(`/api/projects/${projectId}/response-items${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`);
    const map = await r.json();
    if (map && typeof map === 'object') {
      responseItemsByBlock = map;
      // 目录行要显示"覆盖 x/y"，与素材库同源同一份数据（workbench.js 读这个）
      window.__responseItems = responseItemsByBlock;
    }
  } catch (err) { console.warn('[req-bar] 应答条目加载失败', err); }
  return responseItemsByBlock;
}

const ITEM_CODE = (id) => String(id || '').split('#').pop();
// ── 本章回应：回应哪个评分点 / 多少分 / 得分口径 / 需什么举证 ──
// 写作时最该常驻的信息。评分点的名称与分值来自评分点结论接口（按 point_id 索引，缓存一次）；
// 得分口径与举证要求直接读块上的 criterion_text / evidence_required（拆分与准备时已写好）。
async function loadPointMeta() {
  if (pointMetaCache) return pointMetaCache;
  pointMetaCache = {};
  try {
    const q = threadId ? `?threadId=${encodeURIComponent(threadId)}` : '';
    const data = await fetch(`/api/projects/${projectId}/scoring-verdict${q}`).then(r => r.json());
    for (const p of data.points || []) {
      pointMetaCache[String(p.point_id)] = {
        item: p.item || p.factor || p.point_id,
        factor: p.factor || '',
        points: p.points,
      };
    }
  } catch (e) {
    console.warn('[editor] 评分点元信息取不到，本章回应只显示口径与举证', e);
  }
  return pointMetaCache;
}

async function renderChapterPoints(b) {
  const box = document.getElementById('chap-points');
  if (!box || !b) return;
  let ids = [];
  try {
    const raw = b.review_point_ids;
    ids = Array.isArray(raw) ? raw.map(String) : (JSON.parse(raw || '[]') || []).map(String);
  } catch { ids = []; }
  const criterion = String(b.criterion_text || '').trim();
  const evidence = String(b.evidence_required || '').trim();
  if (!ids.length && !criterion && !evidence) { box.hidden = true; return; }
  box.hidden = false;

  const meta = await loadPointMeta();
  const pointsEl = document.getElementById('cp-points');
  pointsEl.innerHTML = ids.length
    ? ids.map((id) => {
        const m = meta[id] || {};
        return `<span class="cp-point">${escHtml(m.item || id)}${
          m.points != null ? `<em>${m.points} 分</em>` : ''}${
          m.factor && m.factor !== m.item ? `<span class="cp-cat">${escHtml(m.factor)}</span>` : ''}</span>`;
      }).join('<span class="cp-cat">·</span>')
    : '<span class="cp-cat">本章未挂到具体评分点（骨架章节）</span>';

  const ev = document.getElementById('cp-ev');
  ev.textContent = evidence ? `需举证：${evidence}` : '本章无需强制举证';
  ev.className = evidence ? 'cp-ev' : 'cp-ev none';

  const crit = document.getElementById('cp-criterion');
  crit.hidden = !criterion;
  if (criterion) crit.innerHTML = `<b>得分口径</b>${escHtml(criterion)}`;

  // 有举证要求 → 给"引用素材"入口（点开按需素材抽屉，而不是预先全量挂载）
  const cite = document.getElementById('cp-cite');
  cite.hidden = !evidence;
  if (evidence && !cite.dataset.bound) {
    cite.dataset.bound = '1';
    cite.addEventListener('click', () => window.__toggleMaterialDrawer?.(true));
  }
}

// 调试/验证入口：可用合成块直接驱动"本章回应"渲染（不依赖库里数据）
window.__ovEditor = { renderChapterPoints, renderReqBar };

function renderReqBar(b) {
  const bar = document.getElementById('req-bar');
  if (!bar) return;
  if (!b) { bar.hidden = true; return; }
  bar.hidden = false;

  const items = ((responseItemsByBlock || {})[String(b.block_id)] || [])
    .filter(it => !it.informative);
  const evs = [];
  try { evs.push(...(JSON.parse(b.source || '[]') || [])); } catch {}
  const covered = new Set(evs.flatMap(e => e.item_ids || []));

  const main = document.getElementById('req-bar-main');
  if (main) {
    main.textContent = (b.requirement || '').trim() || '本章暂无准备出的应标要求（可直接撰写）。';
  }

  const chips = document.getElementById('req-bar-items');
  if (chips) {
    chips.innerHTML = items.length
      ? items.map(it => `<span class="req-bar-item${covered.has(it.item_id) ? ' has' : ''}"
            title="${escHtml(it.text)}${covered.has(it.item_id) ? '（有素材依据）' : ''}">${ITEM_CODE(it.item_id)}</span>`).join('')
      : '';
  }

  // 评审区（#req-bar-review）由 assets/workbench.js 负责：它手上有 statusLabels、
  // 复审动作与版本弹窗。这里不写，避免两个写者互相覆盖。
  // 有依据的条目标记（与素材抽屉里的引用动作配合）
  const drawerCount = document.getElementById('mat-drawer-count');
  if (drawerCount) drawerCount.textContent = String(evs.length);
}

function workspaceReviewUrl(blockId) {
  const q = new URLSearchParams({ projectId: String(projectId) });
  if (packageId) q.set('packageId', packageId);
  if (threadId) q.set('threadId', threadId);
  if (blockId) q.set('blockId', String(blockId));
  return `/review?${q}`;
}

(function initReqBar() {
  // 条目表要等声明之后才能用（let 的 TDZ：放在模块顶部调用会抛 ReferenceError）
  loadResponseItems().then(() => {
    if (currentBlock) renderReqBar(currentBlock);
  });
  const bar = document.getElementById('req-bar');
  const toggle = document.getElementById('req-bar-toggle');
  const more = document.getElementById('req-bar-more');
  if (!bar) return;
  let open = true;
  try { open = localStorage.getItem('workbench:reqBar') !== '0'; } catch {}
  const apply = () => {
    bar.classList.toggle('open', open);
    toggle?.setAttribute('aria-expanded', String(open));
    const body = document.getElementById('req-bar-body');
    if (body) body.hidden = !open;
  };
  apply();
  toggle?.addEventListener('click', () => {
    open = !open; apply();
    try { localStorage.setItem('workbench:reqBar', open ? '1' : '0'); } catch {}
  });
  more?.addEventListener('click', () => {
    const fields = document.getElementById('req-bar-fields');
    if (!fields) return;
    fields.hidden = !fields.hidden;
    // 展开字段时藏掉上面那段要求原文：8 张卡里的第一张就是「应标要求」，
    // 同一段文本上下各一遍（用户截图反馈"上下存在重复"）。
    bar.classList.toggle('fields-open', !fields.hidden);
    more.textContent = fields.hidden ? '展开全部字段' : '收起全部字段';
  });
})();

function renderRefCards(b) {
  // 应标要求
  const reqBody = document.getElementById('ref-req-body');
  if (reqBody) {
    reqBody.textContent = b.requirement || '暂无应标要求';
    reqBody.dataset.refType = 'requirement';
  }

  // 应标重点
  const focusBody = document.getElementById('ref-focus-body');
  const focusCount = document.getElementById('ref-focus-count');
  const kp = (b.key_points || '').trim();
  const kpItems = kp ? kp.split('\n').filter(Boolean) : [];
  if (focusCount) focusCount.textContent = kpItems.length || '';
  if (focusBody) {
    focusBody.textContent = kp || '暂无应标重点';
    focusBody.dataset.refType = 'key_points';
  }

  // 否决项
  const vetoBody = document.getElementById('ref-veto-body');
  const vetoCount = document.getElementById('ref-veto-count');
  const vi = (b.veto_items || '').trim();
  const viItems = vi ? vi.split('\n').filter(Boolean) : [];
  if (vetoCount) vetoCount.textContent = viItems.length || '';
  if (vetoBody) {
    vetoBody.textContent = vi || '暂无否决项';
    vetoBody.dataset.refType = 'veto_items';
  }

  // 加分项
  const bonusBody = document.getElementById('ref-bonus-body');
  const bonusCount = document.getElementById('ref-bonus-count');
  const bi = (b.bonus_items || '').trim();
  const biItems = bi ? bi.split('\n').filter(Boolean) : [];
  if (bonusCount) bonusCount.textContent = biItems.length || '';
  if (bonusBody) {
    bonusBody.textContent = bi || '暂无加分项';
    bonusBody.dataset.refType = 'bonus_items';
  }

  // 评分项
  const scoreBody = document.getElementById('ref-score-body');
  const scoreCount = document.getElementById('ref-score-count');
  const si = (b.score_items || '').trim();
  const siItems = si ? si.split('\n').filter(l => l.trim() && l.trim() !== '---') : [];
  if (scoreCount) scoreCount.textContent = siItems.length || '';
  if (scoreBody) {
    scoreBody.textContent = si || '暂无评分项';
    scoreBody.dataset.refType = 'score_items';
  }

  // 证明材料
  const evidenceBody = document.getElementById('ref-evidence-body');
  const evidenceCount = document.getElementById('ref-evidence-count');
  const er = (b.evidence_required || '').trim();
  const erItems = er ? er.split('\n').filter(Boolean) : [];
  if (evidenceCount) evidenceCount.textContent = erItems.length || '';
  if (evidenceBody) {
    evidenceBody.textContent = er || '暂无证明材料';
    evidenceBody.dataset.refType = 'evidence_required';
  }

  // 量化指标
  const indBody = document.getElementById('ref-indicators-body');
  const indCount = document.getElementById('ref-indicators-count');
  const ind = (b.indicators || '').trim();
  const indItems = ind ? ind.split('\n').filter(Boolean) : [];
  if (indCount) indCount.textContent = indItems.length || '';
  if (indBody) {
    indBody.textContent = ind || '暂无量化指标';
    indBody.dataset.refType = 'indicators';
  }

  // 强制等级（第 8 个字段）：三选一下拉。后端存英文枚举，界面显示中文；
  // 改一次就 PUT 一个字段（后端局部更新，不会清空别的字段）。
  const cBody = document.getElementById('ref-constraint-body');
  const cCount = document.getElementById('ref-constraint-count');
  const level = CONSTRAINT_LEVELS.some(o => o.value === b.constraint_level)
    ? b.constraint_level : 'recommended';
  if (cCount) cCount.textContent = constraintLevelLabel(level);
  if (cBody) {
    cBody.innerHTML = '';
    const sel = document.createElement('select');
    sel.className = 'ref-card-select';
    sel.id = 'ref-constraint-select';
    CONSTRAINT_LEVELS.forEach(o => {
      const opt = document.createElement('option');
      opt.value = o.value; opt.textContent = o.label;
      if (o.value === level) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener('change', async () => {
      const value = sel.value;
      sel.disabled = true;
      try {
        await api.blocks.updateFields(currentBlock.id, { constraint_level: value });
        currentBlock.constraint_level = value;
        if (cCount) cCount.textContent = constraintLevelLabel(value);
      } catch (err) {
        console.error('强制等级保存失败', err);
        sel.value = currentBlock.constraint_level || 'recommended';
        uiAlert('强制等级保存失败');
      } finally {
        sel.disabled = false;
      }
    });
    cBody.appendChild(sel);
  }
}

// 准备参考卡片点击 → 弹窗编辑
document.querySelectorAll('.ref-card-body').forEach(body => {
  body.addEventListener('click', () => {
    if (!currentBlock) return;
    const refType = body.dataset.refType;
    if (!refType) return;
    const titleMap = { requirement:'应标要求', key_points:'应标重点', veto_items:'否决项', bonus_items:'加分项', score_items:'评分项', evidence_required:'证明材料', indicators:'量化指标' };
    openRefEditModal(refType, titleMap[refType] || '', body.textContent || '');
  });
});

function openRefEditModal(refType, title, content) {
  const overlay = document.createElement('div');
  overlay.className = 'edit-modal-overlay';
  overlay.innerHTML = `<div class="edit-modal">
    <div class="edit-modal-header">
      <span class="edit-modal-title">${escHtml(title)}</span>
      <button type="button" class="edit-modal-close" title="关闭">×</button>
    </div>
    <div class="edit-modal-body" contenteditable="true">${escHtml(content)}</div>
    <div class="edit-modal-footer">
      <button type="button" class="edit-modal-btn cancel">取消</button>
      <button type="button" class="edit-modal-btn save">保存</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);
  const bodyEl = overlay.querySelector('.edit-modal-body');
  bodyEl.focus();

  overlay.querySelector('.edit-modal-close').addEventListener('click', () => overlay.remove());
  overlay.querySelector('.edit-modal-btn.cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  overlay.querySelector('.edit-modal-btn.save').addEventListener('click', async () => {
    const newContent = bodyEl.innerText.trim();
    const saveBtn = overlay.querySelector('.edit-modal-btn.save');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      const payload = {};
      if (refType === 'requirement') {
        payload.requirement = newContent;
      } else {
        payload.content = newContent;
      }
      // 保存到后端 block
      if (refType === 'requirement') {
        await fetch(`/api/blocks/${currentBlock.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ requirement: newContent }),
        });
        currentBlock.requirement = newContent;
      } else {
        // key_points/veto_items/bonus_items 存在 block 字段里
        await fetch(`/api/blocks/${currentBlock.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [refType]: newContent }),
        });
        currentBlock[refType] = newContent;
      }
      overlay.remove();
      renderRefCards(currentBlock);
    } catch (err) {
      console.error('章节要求保存失败', err);
      uiAlert('保存失败');
      saveBtn.disabled = false;
      saveBtn.textContent = '保存';
    }
  });
}

// ── 字数统计 ──
function updateWordCount() {
  const editable = document.getElementById('ced-editable');
  const limitInput = document.getElementById('ced-wordlimit');
  const curEl = document.getElementById('ced-wordlimit-cur');
  const footerWc = document.getElementById('ced-footer-wc');
  const wc = (editable?.innerText || '').replace(/\s/g, '').length;
  const limit = parseInt(limitInput?.value) || 800;
  if (curEl) {
    curEl.textContent = `当前 ${wc}`;
    curEl.classList.toggle('over', wc > limit);
  }
  if (footerWc) footerWc.textContent = `${wc} / ${limit} 字`;
}

document.getElementById('ced-editable')?.addEventListener('input', () => {
  updateWordCount(); editorDirty = true;
  setSavedTime('未保存');
});
document.getElementById('ced-placeholder')?.addEventListener('click', () => {
  if (!currentBlock) return;
  const editable = document.getElementById('ced-editable');
  editable.style.display = ''; document.getElementById('ced-placeholder').style.display = 'none'; editable.focus();
});
document.getElementById('ced-wordlimit')?.addEventListener('input', updateWordCount);

// ── 溯源标注：双击切换为纯文本编辑 ──
(function() {
  const editable = document.getElementById('ced-editable');
  if (!editable) return;

  editable.addEventListener('dblclick', () => {
    // 双击进入编辑模式：去掉标记，显示纯文本
    if (!currentBlock) return;
    const cached = blockContentCache.get(currentBlock.id) || currentBlock.content || '';
    if (/\[G\]|\[M\]|\[E\]/.test(cached)) {
      editable.textContent = getAnnotatedPlainText(cached);
    }
  });

  editable.addEventListener('blur', () => {
    // 失焦后如果有原始 JSON 且用户未修改，恢复标注视图
    // 如果用户修改了内容，保持纯文本（他们的编辑优先）
  });
})();

const markdownTokens = {'加粗':['**','**'], '斜体':['*','*'], '下划线':['<u>','</u>'], '一级标题':['# ',''], '二级标题':['## ',''], '列表':['- ','']};
document.querySelectorAll('.ced-fmt-btn,.ced-fmt-label-btn').forEach(button => {
  button.addEventListener('mousedown', e => e.preventDefault());
  button.addEventListener('click', () => {
    const editable = document.getElementById('ced-editable');
    const selection = window.getSelection();
    const tokens = markdownTokens[button.title];
    if (!tokens || !currentBlock) return;
    editable.style.display = ''; document.getElementById('ced-placeholder').style.display = 'none';
    if (!selection.rangeCount || !editable.contains(selection.anchorNode)) editable.focus();
    if (!selection.rangeCount || !editable.contains(selection.anchorNode)) return;
    const range = selection.getRangeAt(0);
    const node = document.createTextNode(tokens[0] + (selection.toString() || '文字') + tokens[1]);
    range.deleteContents(); range.insertNode(node); range.selectNodeContents(node);
    selection.removeAllRanges(); selection.addRange(range);
    editable.dispatchEvent(new Event('input', {bubbles:true}));
  });
});
// ── 文风切换 ──
document.querySelectorAll('.ced-tone-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ced-tone-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const toneNames = { official:'公文文风', tech:'技术文风', concise:'精简文风' };
    const footerModel = document.getElementById('ced-footer-model');
    if (footerModel) footerModel.textContent = `Sonnet 4.6 · ${toneNames[btn.dataset.tone] || '公文文风'}`;
  });
});

// ── AI 生成（单块）：POST SSE fetch ──
async function startSingleGenerate() {
  if (!currentBlock || editorGenerating) return;
  editorGenerating = true;

  const tone = 'official';
  const targetWords = parseInt(document.getElementById('ced-wordlimit')?.value) || 800;

  const editable = document.getElementById('ced-editable');
  const placeholder = document.getElementById('ced-placeholder');
  const hint = document.getElementById('ced-ai-hint');
  const banner = document.getElementById('ced-gen-banner');
  const pbar = document.getElementById('ced-gen-pbar');
  const pct = document.getElementById('ced-gen-pct');
  const stage = document.getElementById('ced-gen-stage');

  placeholder.style.display = 'none';
  if (hint) hint.style.display = 'none';
  editable.style.display = '';
  editable.textContent = '';
  banner.classList.add('visible');
  if (stage) stage.textContent = '· 整理应标要求与素材中';
  if (pbar) pbar.style.width = '0%';
  if (pct) pct.textContent = '0%';
  let receivedFirstToken = false;

  const abortCtrl = new AbortController();
  document.getElementById('ced-gen-stop')?.addEventListener('click', () => {
    abortCtrl.abort();
  }, { once: true });

  try {
    // workflow 模式：单 block 重新生成走 api.workflow.regen，UI 通过 SSE block_token / block_done 事件刷新
    if (threadId && currentBlock?.block_id) {
      try {
        // 确保 SSE 流已打开（review 页或 hard reload 进入时 workflowStream 可能为 null）
        ensureWorkflowStream();
        // 标记当前单 block regen，供 onBlockDone 复位 banner
        window.__currentSingleRegenBlockId = currentBlock.block_id;
        await api.workflow.regen(threadId, [currentBlock.block_id]);
        // fire-and-forget — SSE handler 已处理 token/done 事件，banner 由 onBlockDone 命中匹配后复位
        if (stage) stage.textContent = '· 已提交重新生成';
        editorGenerating = false;
        return;
      } catch (e) {
        console.error('workflow regen failed', e);
        window.__currentSingleRegenBlockId = null;
        finishGenerate(banner, pbar, pct, stage);
        editorGenerating = false;
        return;
      }
    }

    const cited = blockCiteMap.get(currentBlock.id);
    let extra_context = cited && cited.size
      ? [...cited.values()]
          .map(s => `[${s.filename || '素材'} #${s.chunk_index}]\n${s.content || ''}`)
          .join('\n\n')
      : '';

    // 追加用户自定义要求
    const customReq = window.__promptCustomRequirement || '';
    if (customReq) {
      extra_context = (extra_context ? extra_context + '\n\n' : '') + `【用户额外要求】\n${customReq}`;
    }
    window.__promptCustomRequirement = '';

    const resp = await fetch(`/api/blocks/${currentBlock.id}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tone, target_words: targetWords, extra_context, model: window.__generateModel || null }),
      signal: abortCtrl.signal,
    });

    if (!resp.ok || !resp.body) {
      finishGenerate(banner, pbar, pct, stage);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let streamBuf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // 按双换行分割完整 SSE 事件
      const events = buf.split('\n\n');
      buf = events.pop(); // 保留不完整部分
      for (const evt of events) {
        if (!evt.trim()) continue;
        // 从事件中提取 data: 行
        const dataLine = evt.split('\n').find(l => l.startsWith('data:'));
        if (!dataLine) continue;
        try {
          const data = JSON.parse(dataLine.slice(5).trim());
          if (data.text) {
            if (!receivedFirstToken) {
              receivedFirstToken = true;
              if (stage) stage.textContent = '· 正在撰写';
              editable.style.display = '';
              placeholder.style.display = 'none';
              initStreamRenderer(editable);
            }
            streamBuf += data.text;
            appendStreamToken(data.text);
          }
          if (data.block_id) {
            flushStreamRenderer();
            const fullContent = data.content || streamBuf || '';
            renderSourceMarked(fullContent, editable);
            finishGenerate(banner, pbar, pct, stage);
            return;
          }
        } catch {}
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') console.error('生成失败', err);
  }

  finishGenerate(banner, pbar, pct, stage);
}

// ── 溯源标注渲染（流式 Markdown + 内联标记） ──
// 全量渲染（用于加载已有内容和 block_done）
function renderSourceMarked(content, editable) {
  if (!content || !editable) return;
  let html = escHtml(content);
  html = html.replace(/\[G\]([\s\S]*?)\[\/G\]/g, '<span class="src-guide">$1</span>');
  html = html.replace(/\[M\]([\s\S]*?)\[\/M\]/g, '<span class="src-material">$1</span>');
  html = html.replace(/\[E\]([\s\S]*?)\[\/E\]/g, '<span class="src-extended">$1</span>');
  html = html.replace(/\[G\]([\s\S]*)$/, '<span class="src-guide">$1</span>');
  html = html.replace(/\[M\]([\s\S]*)$/, '<span class="src-material">$1</span>');
  html = html.replace(/\[E\]([\s\S]*)$/, '<span class="src-extended">$1</span>');
  html = html.replace(/\[G\]|\[\/G\]|\[M\]|\[\/M\]|\[E\]|\[\/E\]/g, '');
  editable.innerHTML = html;
}

// ── 增量流式追加器 ──
const TAG_CLASS = { G: 'src-guide', M: 'src-material', E: 'src-extended' };
let _streamState = null;

function initStreamRenderer(editable) {
  editable.innerHTML = '';
  _streamState = { editable, currentSpan: null, currentClass: '', pending: '' };
}

function appendStreamToken(token) {
  if (!_streamState) return;
  const { editable } = _streamState;
  _streamState.pending += token;

  // 处理 pending 中的标记
  let text = _streamState.pending;
  _streamState.pending = '';

  while (text.length > 0) {
    // 检查是否以标记开头
    const openMatch = text.match(/^\[(G|M|E)\]/);
    if (openMatch) {
      const cls = TAG_CLASS[openMatch[1]];
      const span = document.createElement('span');
      span.className = cls;
      editable.appendChild(span);
      _streamState.currentSpan = span;
      _streamState.currentClass = cls;
      text = text.slice(openMatch[0].length);
      continue;
    }

    const closeMatch = text.match(/^\[\/(G|M|E)\]/);
    if (closeMatch) {
      _streamState.currentSpan = null;
      _streamState.currentClass = '';
      text = text.slice(closeMatch[0].length);
      continue;
    }

    // 检查是否有不完整的标记在末尾（可能下一个 token 补齐）
    const partialMatch = text.match(/\[\/?(G|M|E)?]?$/);
    if (partialMatch && partialMatch.index > 0 && text.slice(partialMatch.index).length < 4) {
      // 先输出标记之前的文本
      const before = text.slice(0, partialMatch.index);
      if (before) appendTextNode(before);
      _streamState.pending = text.slice(partialMatch.index);
      break;
    }
    // 也许开头就是半个标记
    if (text.startsWith('[') && text.length < 4) {
      _streamState.pending = text;
      break;
    }

    // 找到下一个标记位置
    const nextTag = text.indexOf('[', 1);
    if (nextTag === -1) {
      // 检查末尾是否可能是半个标记
      if (text.endsWith('[')) {
        appendTextNode(text.slice(0, -1));
        _streamState.pending = '[';
      } else {
        appendTextNode(text);
      }
      break;
    } else {
      appendTextNode(text.slice(0, nextTag));
      text = text.slice(nextTag);
    }
  }

  // 自动滚动到底
  editable.scrollTop = editable.scrollHeight;
}

function appendTextNode(str) {
  if (!str || !_streamState) return;
  const target = _streamState.currentSpan || _streamState.editable;
  // 直接追加文本节点
  target.appendChild(document.createTextNode(str));
}

function flushStreamRenderer() {
  if (!_streamState) return;
  if (_streamState.pending) {
    appendTextNode(_streamState.pending);
    _streamState.pending = '';
  }
  _streamState = null;
}

// 加载已有 block 内容时的渲染
function renderAnnotatedContent(content, editable) {
  if (!content || !editable) return false;
  // 检测是否包含来源标记
  if (!/\[G\]|\[M\]|\[E\]/.test(content)) return false;
  renderSourceMarked(content, editable);
  return true;
}

// 获取去掉标记的纯文本
function getAnnotatedPlainText(content) {
  return (content || '').replace(/\[G\]|\[\/G\]|\[M\]|\[\/M\]|\[E\]|\[\/E\]/g, '');
}

function finishGenerate(banner, pbar, pct, stageEl) {
  editorGenerating = false;
  banner.classList.remove('visible');
  if (pbar) pbar.style.width = '100%';
  if (pct) pct.textContent = '100%';
  if (stageEl) stageEl.textContent = '· 撰写完成';
  updateWordCount();
  setSavedTime(`已保存 · ${new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'})}`);
}

document.getElementById('btn-ai-gen')?.addEventListener('click', openPromptPreview);
document.getElementById('btn-ai-hint-gen')?.addEventListener('click', openPromptPreview);

// ── Prompt 预览弹窗逻辑 ──
const TONE_LABELS = { official: '公文', tech: '技术', concise: '精简' };
const TONE_DESCRIPTIONS = {
  official: '你是一个技术应标高级专家，专门针对技术规范书去输出对应的技术应答内容',
  tech: '你是一个技术应标高级专家，专门针对技术规范书去输出对应的技术应答内容',
  concise: '你是一个技术应标高级专家，专门针对技术规范书去输出对应的技术应答内容',
};

function openPromptPreview() {
  if (threadId && currentBlock?.block_id) {
    if (editorDirty) { uiAlert('请先保存正文，再发起修订。'); return; }
    confirmChapterAction([currentBlock.block_id], 'revise');
    return;
  }
  if (!currentBlock) return;
  window.__isBatchPrompt = false;

  const tone = 'official';
  const targetWords = parseInt(document.getElementById('ced-wordlimit')?.value) || 800;

  // 组装各部分数据
  const block = currentBlock;
  const cited = blockCiteMap.get(block.id);
  const extraContext = cited && cited.size
    ? [...cited.values()].map(s => `[${s.filename || '素材'} #${s.chunk_index}]\n${s.content || ''}`).join('\n\n')
    : '';

  const sections = [
    {
      key: 'role',
      title: '角色设定',
      tag: '文风: ' + TONE_LABELS[tone],
      content: TONE_DESCRIPTIONS[tone] || '',
      collapsed: true,
    },
    {
      key: 'guidance',
      title: '拆分要求（应标指导）',
      tag: '来自准备',
      content: [
        block.requirement ? `核心要求：${block.requirement}` : '',
        block.key_points ? `策略要点：${block.key_points}` : '',
        block.veto_items ? `否决项：${block.veto_items}` : '',
        block.bonus_items ? `加分项：${block.bonus_items}` : '',
        block.score_items ? `评分项：${block.score_items}` : '',
        block.evidence_required ? `证明材料：${block.evidence_required}` : '',
        block.constraint_level
          ? `强制性：${constraintLevelLabel(block.constraint_level)}` : '',
        block.indicators ? `量化指标：${block.indicators}` : '',
      ].filter(Boolean).join('\n') || '（暂无准备内容）',
      collapsed: false,
    },
    {
      key: 'section',
      title: '章节内容',
      tag: '来自大纲',
      content: `「${block.title || ''}」\n${block.content || '（空）'}`,
      collapsed: true,
    },
    {
      key: 'sources',
      title: '参考素材',
      tag: `已引用 ${cited?.size || 0} 条`,
      content: extraContext || '（未引用素材）',
      collapsed: true,
    },
    {
      key: 'custom',
      title: '自定义要求',
      tag: '用户编辑',
      content: '',
      placeholder: '可填写额外要求，如：重点突出安全性、不要写背景介绍...',
      collapsed: false,
    },
    {
      key: 'output',
      title: '输出规范',
      tag: `目标 ${targetWords} 字`,
      content: `目标字数：${targetWords} 字\n核心规则：忠于原意、保持段落结构、补充背景释义与应用场景`,
      collapsed: true,
    },
  ];

  // 渲染弹窗 — 左侧导航 + 右侧编辑区
  const nav = document.getElementById('prompt-modal-nav');
  const editor = document.getElementById('prompt-modal-editor');

  nav.innerHTML = sections.map((sec, i) => `
    <div class="prompt-nav-item${i === 1 ? ' active' : ''}" data-idx="${i}">
      <span class="prompt-nav-dot"></span>
      <span>${sec.title}</span>
    </div>
  `).join('');

  // 存储数据用于切换
  window.__promptSections = sections;
  window.__promptTextareas = {};

  function showSection(idx) {
    const sec = sections[idx];
    editor.innerHTML = `
      <div class="prompt-editor-title">
        <span>${sec.title}</span>
        <span class="prompt-editor-tag">${sec.tag}</span>
      </div>
      <textarea class="prompt-editor-textarea" data-section-key="${sec.key}" placeholder="${sec.placeholder || ''}">${escHtml(window.__promptTextareas[sec.key] ?? sec.content)}</textarea>
    `;
    // 自动聚焦
    const ta = editor.querySelector('textarea');
    if (sec.key === 'custom') ta?.focus();
    // 保存编辑内容
    ta?.addEventListener('input', () => {
      window.__promptTextareas[sec.key] = ta.value;
    });
    // 更新导航 active
    nav.querySelectorAll('.prompt-nav-item').forEach((item, i) => {
      item.classList.toggle('active', i === idx);
    });
  }

  // 默认显示"拆分要求"
  showSection(1);

  // 导航点击切换
  nav.querySelectorAll('.prompt-nav-item').forEach(item => {
    item.addEventListener('click', () => {
      showSection(parseInt(item.dataset.idx));
    });
  });

  document.getElementById('prompt-modal-backdrop').classList.add('open');
}

// 关闭 prompt 弹窗
document.getElementById('prompt-modal-close')?.addEventListener('click', () => {
  document.getElementById('prompt-modal-backdrop').classList.remove('open');
  window.__isBatchPrompt = false;
});
document.getElementById('btn-prompt-cancel')?.addEventListener('click', () => {
  document.getElementById('prompt-modal-backdrop').classList.remove('open');
  window.__isBatchPrompt = false;
});
document.getElementById('prompt-modal-backdrop')?.addEventListener('click', e => {
  if (e.target === e.currentTarget) { e.currentTarget.classList.remove('open'); window.__isBatchPrompt = false; }
});

// 确认生成
document.getElementById('btn-prompt-confirm')?.addEventListener('click', () => {
  window.__promptCustomRequirement = (window.__promptTextareas?.custom || '').trim();
  document.getElementById('prompt-modal-backdrop').classList.remove('open');
  if (window.__isBatchPrompt) {
    window.__isBatchPrompt = false;
    executeBatchGenerate();
  } else {
    startSingleGenerate();
  }
});

// ── block:token 事件（批量生成流式写入编辑器） ──
window.addEventListener('block:token', e => {
  const blockId = e.detail.block_id;
  // 累积到缓存
  blockContentCache.set(blockId, (blockContentCache.get(blockId) || '') + e.detail.token);

  if (!currentBlock || blockId !== currentBlock.id) return;
  const editable = document.getElementById('ced-editable');
  if (editable) {
    editable.style.display = '';
    document.getElementById('ced-placeholder').style.display = 'none';
    const hint = document.getElementById('ced-ai-hint');
    if (hint) hint.style.display = 'none';
    renderSourceMarked(blockContentCache.get(blockId), editable);
    updateWordCount();
  }
});

// block:done 时确认缓存已有完整内容（token 已累积完毕）
window.addEventListener('block:done', e => {
  // blockContentCache 已通过 block:token 累积，无需额外处理
  // 若当前显示节点正好是该 block，刷新字数
  if (currentBlock && e.detail.block_id === currentBlock.id) updateWordCount();
});

// ── 保存草稿：真实成功后确认，网络失败保留用户输入 ──
document.getElementById('btn-save-draft')?.addEventListener('click', async () => {
  if (!currentBlock) return;
  const editable = document.getElementById('ced-editable');
  const button = document.getElementById('btn-save-draft');
  const status = document.getElementById('ced-save-time');
  const block = currentBlock;
  if (!Number.isInteger(Number(block.id))) { status.textContent = '本章未落库，暂不能保存'; return; }
  const content = editable.innerText;
  button.disabled = true; status.textContent = '保存中…';
  try {
    const saved = await workspaceRequest(`/api/blocks/${block.id}`, {
      method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({content}),
    });
    block.content = saved.content; block.updated_at = saved.updated_at;
    blockContentCache.set(block.id, saved.content);
    if (currentBlock === block) {
      editorDirty = editable.innerText !== content;
      status.textContent = editorDirty ? '仍有未保存修改' : '已保存 · 修改后请复审';
    }
    await refreshWorkspace();
  } catch (e) { if (currentBlock === block) status.textContent = `保存失败：${e.message}，内容已保留，请重试`; }
  finally { button.disabled = false; }
});

// ── 右侧面板（无 Tab，两个上下模块） ──

// ── 匹配素材渲染 ──
const FILE_BADGE_PALETTE = {
  PDF:  { bg:'#fdecec', fg:'#b3261e' },
  DOCX: { bg:'#e8efff', fg:'#1f4ed8' },
  DOC:  { bg:'#e8efff', fg:'#1f4ed8' },
  PPTX: { bg:'#fff1e0', fg:'#a35b00' },
  PPT:  { bg:'#fff1e0', fg:'#a35b00' },
};

const matchedList = document.getElementById('matched-list');
const matchedCountBadge = document.getElementById('matched-count-badge');

// blockId → Map<citeKey, citeItem>
const blockCiteMap = new Map();
const blockMatchedCache = new Map();
function citeKey(s) { return `${s.material_id}-${s.chunk_index}`; }
function getCiteSet() {
  if (!currentBlock) return new Map();
  let m = blockCiteMap.get(currentBlock.id);
  if (!m) { m = new Map(); blockCiteMap.set(currentBlock.id, m); }
  return m;
}

function parseMatchedFromBlock(block) {
  if (!block) return [];
  try {
    const arr = JSON.parse(block.source || '[]');
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

function renderMatchedSources(sources) {
  if (matchedCountBadge) matchedCountBadge.textContent = sources.length || 0;
  renderCiteBar();

  if (!sources.length) {
    matchedList.innerHTML = `<div class="matched-empty">暂无匹配素材<br><span style="font-size:11px;">请在「全部素材」中上传原始素材后刷新</span></div>`;
    return;
  }

  const allZero = sources.every(s => !s.score);
  const weakHint = allZero
    ? `<div class="matched-empty" style="padding:10px 12px;text-align:left;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;margin-bottom:4px;">未找到强相关素材，以下为弱关联结果，仅供参考。</div>`
    : '';

  const cited = getCiteSet();

  matchedList.innerHTML = weakHint + sources.map((s, i) => {
    const filename = s.filename || `素材 #${s.material_id}`;
    const ext = (filename.split('.').pop() || '').toUpperCase();
    const pal = FILE_BADGE_PALETTE[ext] || { bg:'#eee', fg:'#444' };
    const content = (s.content || s.snippet || '').trim();
    const collapsed = content.length > 90;
    const score = typeof s.score === 'number' ? s.score : 0;
    const isCited = cited.has(citeKey(s));
    // 出处 / 性质都用共享片段（模型层一份实现）：老数据没有 locator，退回旧字段，
    // 不能像以前那样固定渲染 `#${chunk_index}`（契约升级后该字段已不存在）。
    const locator = s.locator || (s.chunk_index != null ? `#${s.chunk_index}` : '');
    const legacy = { ...s, locator };
    // 相关度：新契约给 0~1 的 confidence（规则命中=1、语义命中=归一化分），
    // 老数据的 BM25 原始分是十几位小数，渲染出来是一串噪声，退回两位小数。
    const relevance = typeof s.confidence === 'number'
      ? `${Math.round(s.confidence * 100)}%`
      : (score > 0 ? `×${Number(score).toFixed(2)}` : '');

    return `<div class="matched-card" data-idx="${i}">
      <div class="matched-card-head">
        <span class="matched-card-ext" style="background:${pal.bg};color:${pal.fg};">${escHtml(ext)}</span>
        <span class="matched-card-name" title="${escHtml(filename)}">${escHtml(filename)}</span>
        ${evidenceKindBadgeHtml(legacy)}
        ${locator ? `<span class="matched-card-chunk" title="出处">${escHtml(locator)}</span>` : ''}
        ${relevance ? `<span class="matched-card-score" title="相关度${s.by ? `（${s.by === 'rule' ? '规则命中' : s.by === 'semantic' ? '语义命中' : s.by}）` : ''}">${relevance}</span>` : ''}
      </div>
      <div class="matched-card-body${collapsed ? ' collapsed' : ''}" data-content>${escHtml(content)}</div>
      ${collapsed ? `<button type="button" class="matched-card-toggle">展开全文</button>` : ''}
      <div class="matched-card-actions">
        <button type="button" class="matched-card-btn" data-act="edit">编辑</button>
        <button type="button" class="matched-card-btn" data-act="view">查看</button>
        <button type="button" class="matched-card-btn cite${isCited ? ' active' : ''}" data-act="cite">${isCited ? '✓ 已引用' : '+ 引用'}</button>
      </div>
    </div>`;
  }).join('');

  matchedList.querySelectorAll('.matched-card-toggle').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const body = btn.parentElement.querySelector('[data-content]');
      const isCollapsed = body.classList.toggle('collapsed');
      btn.textContent = isCollapsed ? '展开全文' : '收起';
    });
  });

  matchedList.querySelectorAll('.matched-card-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      onCardAction(btn);
    });
  });

  matchedList.querySelectorAll('.matched-card').forEach(card => {
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
      const idx = parseInt(card.dataset.idx);
      const sources = blockMatchedCache.get(currentBlock?.id) || [];
      const s = sources[idx];
      if (s) openEditModal(s, card);
    });
  });
}

async function onCardAction(btn) {
  const card = btn.closest('.matched-card');
  const idx = parseInt(card.dataset.idx);
  const sources = blockMatchedCache.get(currentBlock?.id) || [];
  const s = sources[idx];
  if (!s) return;
  const act = btn.dataset.act;

  if (act === 'view') {
    if (!s.material_id) return;
    window.open(`/api/materials/${s.material_id}/file`, '_blank');
    return;
  }

  if (act === 'cite') {
    const cited = getCiteSet();
    const key = citeKey(s);
    if (cited.has(key)) {
      cited.delete(key);
      btn.classList.remove('active');
      btn.textContent = '+ 引用';
    } else {
      cited.set(key, { ...s });
      btn.classList.add('active');
      btn.textContent = '✓ 已引用';
    }
    renderCiteBar();
    return;
  }

  if (act === 'edit') {
    openEditModal(s, card);
  }

  if (act === 'cancel' || act === 'save') { return; }
}

// ── 编辑弹窗 ──
function openEditModal(source, card) {
  const content = (source.content || source.snippet || '').trim();
  const filename = source.filename || `素材 #${source.material_id}`;

  const overlay = document.createElement('div');
  overlay.className = 'edit-modal-overlay';
  overlay.innerHTML = `<div class="edit-modal">
    <div class="edit-modal-header">
      <span class="edit-modal-title">${escHtml(filename)} · #${source.chunk_index}</span>
      <button type="button" class="edit-modal-close" title="关闭">×</button>
    </div>
    <div class="edit-modal-body" contenteditable="true">${escHtml(content)}</div>
    <div class="edit-modal-footer">
      <button type="button" class="edit-modal-btn cancel">取消</button>
      <button type="button" class="edit-modal-btn save">保存</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);
  const body = overlay.querySelector('.edit-modal-body');
  body.focus();

  overlay.querySelector('.edit-modal-close').addEventListener('click', () => overlay.remove());
  overlay.querySelector('.edit-modal-btn.cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  overlay.querySelector('.edit-modal-btn.save').addEventListener('click', async () => {
    const newContent = body.innerText.trim();
    if (!newContent) { uiAlert('内容不能为空'); return; }
    const saveBtn = overlay.querySelector('.edit-modal-btn.save');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      await fetch(`/api/material-chunks/${source.material_id}/${source.chunk_index}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: newContent }),
      });
      source.content = newContent;
      const cited = getCiteSet();
      const k = citeKey(source);
      if (cited.has(k)) cited.set(k, { ...source });
      overlay.remove();
      const sources = blockMatchedCache.get(currentBlock?.id) || [];
      renderMatchedSources(sources);
    } catch (err) {
      console.error('chunk 保存失败', err);
      uiAlert('保存失败');
      saveBtn.disabled = false;
      saveBtn.textContent = '保存';
    }
  });
}

function renderCiteBar() {
  const bar = document.getElementById('cite-bar');
  const cnt = document.getElementById('cite-bar-count');
  const list = document.getElementById('cite-bar-list');
  const cited = getCiteSet();
  const items = [...cited.values()];
  if (cnt) cnt.textContent = items.length;
  if (!items.length) {
    bar.classList.remove('visible', 'expanded');
    return;
  }
  bar.classList.add('visible');
  if (list) {
    list.innerHTML = items.map(s => {
      const k = citeKey(s);
      const filename = s.filename || `素材 #${s.material_id}`;
      const preview = (s.content || '').slice(0, 60);
      return `<div class="cite-bar-item">
        <span class="cite-bar-item-text"><b>${escHtml(filename)}#${s.chunk_index}</b> · ${escHtml(preview)}</span>
        <button type="button" class="cite-bar-item-remove" data-cite-key="${escHtml(k)}" title="移除">×</button>
      </div>`;
    }).join('');
    list.querySelectorAll('.cite-bar-item-remove').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const k = btn.dataset.citeKey;
        cited.delete(k);
        renderCiteBar();
        const sources = blockMatchedCache.get(currentBlock?.id) || [];
        renderMatchedSources(sources);
      });
    });
  }
}

document.getElementById('cite-bar-head')?.addEventListener('click', () => {
  document.getElementById('cite-bar').classList.toggle('expanded');
});

function refreshMatchedForCurrent() {
  if (!currentBlock) {
    renderMatchedSources([]);
    return;
  }
  const cached = blockMatchedCache.get(currentBlock.id);
  if (cached) { autoCiteMatched(cached); renderMatchedSources(cached); return; }
  const fromBlock = parseMatchedFromBlock(currentBlock);
  blockMatchedCache.set(currentBlock.id, fromBlock);
  autoCiteMatched(fromBlock);
  renderMatchedSources(fromBlock);
}

// 将匹配的素材自动添加到引用集合
function autoCiteMatched(sources) {
  if (!currentBlock || !sources || !sources.length) return;
  let m = blockCiteMap.get(currentBlock.id);
  if (!m) { m = new Map(); blockCiteMap.set(currentBlock.id, m); }
  for (const s of sources) {
    const key = citeKey(s);
    if (!m.has(key)) m.set(key, { ...s });
  }
}

// 章节切换时刷新匹配面板
const _origOnSelected = window.__onBlockSelected;
window.__onBlockSelected = function(b) {
  if (typeof _origOnSelected === 'function' && _origOnSelected(b) === false) return false;
  currentBlock = b;
  refreshMatchedForCurrent();
  return true;
};

// 刷新按钮：请求最新匹配
document.getElementById('btn-refresh-matched')?.addEventListener('click', async () => {
  if (!currentBlock) return;
  const btn = document.getElementById('btn-refresh-matched');
  btn.classList.add('spinning');
  try {
    const res = await fetch(`/api/blocks/${currentBlock.id}/matched-sources`).then(r => r.json());
    const sources = res.matched_sources || [];
    blockMatchedCache.set(currentBlock.id, sources);
    autoCiteMatched(sources);
    renderMatchedSources(sources);
  } catch (err) {
    console.error('[matched] refresh failed', err);
  } finally {
    btn.classList.remove('spinning');
  }
});

// 初始渲染（无章节时）
renderMatchedSources([]);

// ── 全部素材面板 ──
const matList  = document.getElementById('mat-list');
const matEmpty = document.getElementById('mat-empty');
const matCountBadge = document.getElementById('mat-count-badge');

async function loadSourceMaterials() {
  if (!matList || !matEmpty) return;
  try {
    const list = await fetch(`/api/projects/${projectId}/materials`).then(r => r.json());
    const sources = list.filter(m => m.role === 'source');
    if (!sources.length) {
      matList.style.display = 'none';
      matEmpty.style.display = 'block';
      if (matCountBadge) matCountBadge.textContent = '0';
      return;
    }
    matList.style.display = 'flex';
    matEmpty.style.display = 'none';
    if (matCountBadge) matCountBadge.textContent = sources.length;
    matList.innerHTML = sources.map(m => {
      const ext = (m.filename || '').split('.').pop().toUpperCase();
      const pal = FILE_BADGE_PALETTE[ext] || { bg:'#eee', fg:'#444' };
      const name = m.filename || '';
      const size = m.file_size ? (m.file_size > 1024*1024 ? (m.file_size/1024/1024).toFixed(1)+' MB' : Math.round(m.file_size/1024)+' KB') : '';
      return `<div style="padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--panel);display:flex;flex-direction:column;gap:6px;">
        <div style="display:flex;align-items:center;gap:7px;">
          <span style="font-size:9px;font-weight:700;letter-spacing:.5px;padding:2px 6px;border-radius:4px;background:${pal.bg};color:${pal.fg};flex:none;">${ext}</span>
          <span style="flex:1;font-size:13px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(name)}">${escHtml(name)}</span>
          ${size ? `<span style="font-size:11px;color:var(--muted);flex:none;">${size}</span>` : ''}
        </div>
        <div style="display:flex;gap:6px;">
          <button style="all:unset;cursor:pointer;padding:3px 10px;border-radius:6px;font-size:11.5px;color:var(--muted);background:var(--bg-2);border:1px solid var(--line);">查看</button>
          <button style="all:unset;cursor:pointer;padding:3px 10px;border-radius:6px;font-size:11.5px;color:#2563eb;background:#eff6ff;font-weight:600;">+ 引用</button>
        </div>
      </div>`;
    }).join('') + `<button id="btn-upload-source" type="button" style="width:100%;padding:10px;border:1.5px dashed var(--line);border-radius:10px;background:none;font-size:13px;color:var(--muted);cursor:pointer;margin-top:2px;">+ 添加原始素材</button>`;
  } catch {
    matList.style.display = 'none';
    matEmpty.style.display = 'block';
  }
}

if (projectId) loadSourceMaterials();

// 添加原始素材：直接选文件上传（事件委托，支持动态渲染的按钮）
const srcFileInput = document.createElement('input');
srcFileInput.type = 'file';
srcFileInput.accept = '.pdf,.docx,.doc,.ppt,.pptx';
srcFileInput.multiple = true;
srcFileInput.style.display = 'none';
document.body.appendChild(srcFileInput);

document.addEventListener('click', e => {
  if (e.target.closest('#btn-upload-source')) srcFileInput.click();
});

srcFileInput.addEventListener('change', async () => {
  const files = Array.from(srcFileInput.files || []);
  if (!files.length) return;
  const btn = document.getElementById('btn-upload-source');
  if (btn) { btn.disabled = true; btn.textContent = '上传中…'; }
  for (const file of files) {
    await fetch(`/api/projects/${projectId}/materials`, {
      method: 'POST',
      body: (() => { const fd = new FormData(); fd.append('file', file); fd.append('material_role', 'source'); return fd; })(),
    }).catch(console.error);
  }
  srcFileInput.value = '';
  if (btn) { btn.disabled = false; btn.textContent = '+ 添加原始素材'; }
  loadSourceMaterials();
  loadMatModal();
});

// ── 素材列表弹窗 ──
const matModalBackdrop = document.getElementById('mat-modal-backdrop');
const matModalBody = document.getElementById('mat-modal-body');
const matModalClose = document.getElementById('mat-modal-close');
const btnMatList = document.getElementById('btn-mat-list');
const btnMatModalUpload = document.getElementById('btn-mat-modal-upload');

async function loadMatModal() {
  matModalBody.innerHTML = '<p class="muted" style="text-align:center;padding:20px;">加载中...</p>';
  try {
    const list = await fetch(`/api/projects/${projectId}/materials`).then(r => r.json());
    const sources = list.filter(m => m.role === 'source');
    if (!sources.length) {
      matModalBody.innerHTML = '<p class="muted" style="text-align:center;padding:20px;">暂无素材</p>';
      return;
    }
    matModalBody.innerHTML = sources.map(m => {
      const ext = (m.filename || '').split('.').pop().toUpperCase();
      const pal = FILE_BADGE_PALETTE[ext] || { bg:'#eee', fg:'#444' };
      const name = m.filename || '';
      const size = m.file_size ? (m.file_size > 1024*1024 ? (m.file_size/1024/1024).toFixed(1)+' MB' : Math.round(m.file_size/1024)+' KB') : '';
      return `<div class="mat-modal-item">
        <span class="mat-modal-item-ext" style="background:${pal.bg};color:${pal.fg};">${ext}</span>
        <span class="mat-modal-item-name" title="${escHtml(name)}">${escHtml(name)}</span>
        ${size ? `<span class="mat-modal-item-size">${size}</span>` : ''}
      </div>`;
    }).join('');
  } catch {
    matModalBody.innerHTML = '<p class="muted" style="text-align:center;padding:20px;">加载失败</p>';
  }
}

btnMatList?.addEventListener('click', () => {
  matModalBackdrop.classList.add('open');
  loadMatModal();
});
matModalClose?.addEventListener('click', () => matModalBackdrop.classList.remove('open'));
matModalBackdrop?.addEventListener('click', e => { if (e.target === matModalBackdrop) matModalBackdrop.classList.remove('open'); });
btnMatModalUpload?.addEventListener('click', () => srcFileInput.click());

// ── autoExtract: 边准备边在 workbench 渲染 ──
const autoExtract = params.get('autoExtract') === '1';
if (autoExtract) {
  startWorkbenchExtraction();
}

// ── 准备按钮逻辑 ──
const extractActionBar = document.getElementById('extract-action-bar');
const btnStartExtract = document.getElementById('btn-start-extract');
if (!autoExtract) {
  fetch(`/api/projects/${projectId}/materials`).then(r => r.json()).then(mats => {
    const hasReq = Array.isArray(mats) && mats.some(m => m.role === 'requirement');
    if (hasReq && extractActionBar) {
      extractActionBar.classList.remove('hidden');
      const outline = document.querySelector('.doc-outline');
      const hasBlocks = outline && outline.children.length > 0;
      btnStartExtract.textContent = hasBlocks ? '重新拆分评分点' : '准备';
    }
  });
}
if (btnStartExtract) {
  btnStartExtract.addEventListener('click', () => {
    extractActionBar.classList.add('hidden');
    startWorkbenchExtraction();
  });
}

// ── 批量生成正文按钮 ──
const btnBatchGen = document.getElementById('btn-batch-generate');
const batchStatus = document.getElementById('outline-batch-status');
const batchCurrent = document.getElementById('outline-batch-current');
const btnBatchCancel = document.getElementById('btn-batch-cancel');
let batchGenerating = false;
let batchAbort = null;

if (btnBatchCancel) {
  btnBatchCancel.addEventListener('click', async () => {
    // 1) 旧路径 fetch：通过 AbortController 中断
    if (batchAbort) {
      try { batchAbort.abort(); } catch {}
    }
    // 2) 新 LangGraph workflow 路径：无条件调 pause（不再依赖 workflowStream），
    //    因为 graph 在后端 runner task 中独立运行，前端 SSE 只是订阅；用户进入页面
    //    若刚好命中"已展示已生成内容、不开 SSE 流"分支，workflowStream 是 null，
    //    但 graph 仍在跑——此时仍要让后端停下，否则用户看到"暂停后还自动进评审"。
    if (typeof threadId !== 'undefined' && threadId) {
      try { await api.workflow.pause(threadId).catch(() => {}); } catch {}
    }
    // 旧路径不需要等 SSE 事件，立刻清 UI
    batchGenerating = false;
    batchAbort = null;
    // batchInFlight / workflowStream 不在此清空，保留给 onPaused 收到事件后再清。
    if (btnBatchCancel) {
      btnBatchCancel.disabled = true;
      btnBatchCancel.textContent = '暂停中…';
    }
  });
}

if (btnBatchGen) {
  btnBatchGen.addEventListener('click', () => {
    if (workspace.threadId) {
      if (editorDirty) { uiAlert('请先保存正文。'); return; }
      const ids = workspace.chapters.filter(b => !b.content.trim()).map(b => b.block_id);
      if (!ids.length) { location.href = `/review?projectId=${projectId}${packageQuery}&threadId=${encodeURIComponent(workspace.threadId)}`; return; }
      confirmChapterAction(ids, 'revise'); return;
    }
    openBatchPromptPreview();
  });
}

// ── 批量生成 Prompt 预览弹窗 ──
function openBatchPromptPreview() {
  const tone = 'official';
  const targetWords = parseInt(document.getElementById('ced-wordlimit')?.value) || 800;

  const sections = [
    {
      key: 'role',
      title: '角色设定',
      tag: '统一设置',
      content: TONE_DESCRIPTIONS[tone] || '',
      placeholder: '',
    },
    {
      key: 'guidance',
      title: '拆分要求（应标指导）',
      tag: '各模块独立',
      content: '各模块自动使用对应的章节要求（核心要求、策略要点、否决项、加分项）',
      placeholder: '',
    },
    {
      key: 'sources',
      title: '参考素材',
      tag: '自动引用',
      content: '各模块自动引用对应的匹配素材',
      placeholder: '',
    },
    {
      key: 'custom',
      title: '自定义要求',
      tag: '统一设置',
      content: '',
      placeholder: '可填写统一的额外要求，所有模块共用。如：重点突出技术创新、篇幅控制在500字...',
    },
    {
      key: 'output',
      title: '输出规范',
      tag: `统一 ${targetWords} 字`,
      content: `目标字数：${targetWords} 字\n核心规则：忠于原意、保持段落结构、补充背景释义与应用场景`,
      placeholder: '',
    },
  ];

  const nav = document.getElementById('prompt-modal-nav');
  const editor = document.getElementById('prompt-modal-editor');

  nav.innerHTML = sections.map((sec, i) => `
    <div class="prompt-nav-item${i === 0 ? ' active' : ''}" data-idx="${i}">
      <span class="prompt-nav-dot"></span>
      <span>${sec.title}</span>
    </div>
  `).join('');

  window.__promptSections = sections;
  window.__promptTextareas = {};
  window.__isBatchPrompt = true;

  function showSection(idx) {
    const sec = sections[idx];
    editor.innerHTML = `
      <div class="prompt-editor-title">
        <span>${sec.title}</span>
        <span class="prompt-editor-tag">${sec.tag}</span>
      </div>
      <textarea class="prompt-editor-textarea" data-section-key="${sec.key}" placeholder="${sec.placeholder || ''}">${escHtml(window.__promptTextareas[sec.key] ?? sec.content)}</textarea>
    `;
    const ta = editor.querySelector('textarea');
    if (sec.key === 'custom') ta?.focus();
    ta?.addEventListener('input', () => {
      window.__promptTextareas[sec.key] = ta.value;
    });
    nav.querySelectorAll('.prompt-nav-item').forEach((item, i) => {
      item.classList.toggle('active', i === idx);
    });
  }

  showSection(0);

  nav.querySelectorAll('.prompt-nav-item').forEach(item => {
    item.addEventListener('click', () => {
      showSection(parseInt(item.dataset.idx));
    });
  });

  document.getElementById('prompt-modal-backdrop').classList.add('open');
}

async function executeBatchGenerate() {
  if (batchGenerating) return;
  batchGenerating = true;
  batchAbort = new AbortController();
  btnBatchGen.style.display = 'none';
  batchStatus.classList.remove('hidden');
  batchCurrent.textContent = '准备中…';
  const batchBar = document.getElementById('outline-batch-bar');
  const batchMeta = document.getElementById('outline-batch-meta');
  if (batchBar) batchBar.style.width = '0%';
  if (batchMeta) batchMeta.textContent = '0 / 0';

  const batchCustomReq = (window.__promptTextareas?.custom || '').trim();
  const targetWords = parseInt(document.getElementById('ced-wordlimit')?.value) || 800;

  const blocks = await fetch(`/api/projects/${projectId}/blocks${threadId ? `?threadId=${encodeURIComponent(threadId)}` : ''}`).then(r => r.json());
  const contentBlocks = blocks.filter(b => !(b.content && b.content.trim()));
  let done = 0;
  if (batchMeta) batchMeta.textContent = `0 / ${contentBlocks.length}`;

  const progressText = document.getElementById('outline-progress-text');
  const progressBar = document.getElementById('outline-progress-bar');
  const banner = document.getElementById('ced-gen-banner');
  const genStage = document.getElementById('ced-gen-stage');
  const genPbar = document.getElementById('ced-gen-pbar');
  const genPct = document.getElementById('ced-gen-pct');
  const editable = document.getElementById('ced-editable');
  const placeholder = document.getElementById('ced-placeholder');

  for (const block of contentBlocks) {
    if (batchAbort.signal.aborted) break;
    batchCurrent.textContent = block.title || '（无标题）';

    const outlineItem = document.querySelector(`.doc-outline-item[data-block-id="${block.id}"]`);
    if (outlineItem) outlineItem.click();

    // 确保该 block 的匹配素材已加载并自动引用
    await new Promise(r => setTimeout(r, 50));
    const cited = blockCiteMap.get(block.id);
    let extra_context = cited && cited.size
      ? [...cited.values()].map(s => `[${s.filename || '素材'} #${s.chunk_index}]\n${s.content || ''}`).join('\n\n')
      : '';
    if (batchCustomReq) {
      extra_context = (extra_context ? extra_context + '\n\n' : '') + `【用户额外要求】\n${batchCustomReq}`;
    }

    if (banner) banner.classList.add('visible');
    if (genStage) genStage.textContent = '· 整理应标要求与素材中';
    if (genPbar) genPbar.style.width = '0%';
    if (genPct) genPct.textContent = '0%';
    if (placeholder) placeholder.style.display = 'none';
    if (editable) { editable.style.display = ''; editable.textContent = ''; initStreamRenderer(editable); }

    let receivedFirst = false;
    let streamBuf = '';
    try {
      const resp = await fetch(`/api/blocks/${block.id}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tone: 'official', target_words: targetWords, extra_context, model: window.__generateModel || null }),
        signal: batchAbort.signal,
      });
      if (resp.ok && resp.body) {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done: rdDone, value } = await reader.read();
          if (rdDone) break;
          buf += decoder.decode(value, { stream: true });
          const events = buf.split('\n\n');
          buf = events.pop();
          for (const evt of events) {
            if (!evt.trim()) continue;
            const dataLine = evt.split('\n').find(l => l.startsWith('data:'));
            if (!dataLine) continue;
            try {
              const data = JSON.parse(dataLine.slice(5).trim());
              if (data.text) {
                if (!receivedFirst) {
                  receivedFirst = true;
                  if (genStage) genStage.textContent = '· 正在撰写';
                }
                streamBuf += data.text;
                appendStreamToken(data.text);
              }
              if (data.block_id) {
                flushStreamRenderer();
                const fullContent = data.content || streamBuf || '';
                if (editable) renderSourceMarked(fullContent, editable);
                blockContentCache.set(block.id, fullContent);
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') break;
    }

    flushStreamRenderer();
    if (banner) banner.classList.remove('visible');
    if (genPbar) genPbar.style.width = '100%';
    if (genPct) genPct.textContent = '100%';
    done++;
    if (batchBar && contentBlocks.length) {
      batchBar.style.width = `${Math.round(done / contentBlocks.length * 100)}%`;
    }
    if (batchMeta) batchMeta.textContent = `${done} / ${contentBlocks.length}`;
    const writtenTotal = blocks.filter(b => b.content && b.content.trim()).length + done;
    if (progressText) progressText.textContent = `${writtenTotal} / ${blocks.length}`;
    if (progressBar) progressBar.style.width = blocks.length ? `${Math.round(writtenTotal / blocks.length * 100)}%` : '0%';
    const dot = document.querySelector(`.doc-outline-item[data-block-id="${block.id}"] .outline-item-dot`);
    if (dot) dot.classList.add('done');
  }

  batchGenerating = false;
  batchAbort = null;
  batchStatus.classList.add('hidden');
  btnBatchGen.style.display = '';
  if (banner) banner.classList.remove('visible');
  if (typeof window.__loadBlocks === 'function') window.__loadBlocks();
}

function inferOutlineLevel(s) {
  // 优先用后端解析的真实 level
  if (typeof s === 'object' && s && Number.isFinite(s.level)) {
    return Math.max(1, Math.min(4, s.level));
  }
  const title = typeof s === 'string' ? s : (s?.title || '');
  if (/^[一二三四五六七八九十百]+[、．\.]/.test(title)) return 1;
  if (/^第[一二三四五六七八九十百\d]+[章节篇]/.test(title)) return 1;
  if (/^\d+\.\d+/.test(title)) return 3;
  return 2;
}

function escapeHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// 用 SSE 数据增量构建左侧大纲
function renderExtractOutline(sections) {
  // 共享宿主：归一后左栏只有一棵树（#blocks-list 由 mode-dispatch 搬进编辑器左栏）
  const outlineNav = document.getElementById('blocks-list') || document.querySelector('.doc-outline');
  const progressBadge = document.getElementById('outline-progress-badge');
  const progressText = document.getElementById('outline-progress-text');
  const progressBar = document.getElementById('outline-progress-bar');
  if (!outlineNav) return;
  outlineNav.innerHTML = '';

  // 隐藏空状态、显示工作区
  document.getElementById('editor-empty-hint').style.display = 'none';
  document.getElementById('editor-workspace').style.display = 'flex';

  // 规整化 level：找到全局最小值，平移成 1（处理"文档没有标题1，从标题2/3 起步"的情况）
  const rawLevels = sections.map(s => inferOutlineLevel(s));
  const minLevel = rawLevels.length ? Math.min(...rawLevels) : 1;
  const normalize = (raw) => Math.max(1, Math.min(4, raw - minLevel + 1));

  // 归一后的建树：交给共享组件（嵌套 .ot-node），不再本模块手拼
  // `.outline-group` / `.outline-heading` / `.doc-outline-item` 三套标记。
  const nodes = sections.map((sec, idx) => {
    const blockIdStr = `outline-${idx}`;
    return {
      block_id: blockIdStr,
      level: normalize(inferOutlineLevel(sec)),
      title: sec.title || '（无标题）',
      _idx: idx,
    };
  });
  window.__extractBlockMap = new Map(nodes.map(n => [n.block_id, {
    id: 0, block_id: n.block_id, title: n.title, kind: 'content', level: 1,
    requirement: '', key_points: '', veto_items: '', bonus_items: '',
    source: '[]', content: '', _pending: true,
  }]));

  renderChapterTree(outlineNav, nodes, {
    makeNode: (n) => chapterNode(n, {
      showEdit: false, showSub: false,
      selectedClass: 'active', rowSelectedClass: 'active',
      rowClasses: ['doc-outline-item', `level-${n.level}`],
      rowData: { idx: n._idx },
      titleClass: 'outline-item-label',
      appendHtml: '<span class="outline-item-bullet"></span><span class="outline-item-dot"></span>',
    }).node,
  });

  // 点击行 = 选中章节（回调与原实现一致）
  outlineNav.querySelectorAll('.doc-outline-item').forEach((item) => {
    item.addEventListener('click', () => {
      const block = window.__extractBlockMap.get(item.dataset.blockIdStr);
      if (!block) return;
      outlineNav.querySelectorAll('.doc-outline-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      if (typeof window.__onBlockSelected === 'function') window.__onBlockSelected(block);
    });
  });

}

function markOutlineDone(blockIdStr, blockData) {
  const item = document.querySelector(`.doc-outline-item[data-block-id-str="${blockIdStr}"]`);
  if (item) {
    const dot = item.querySelector('.outline-item-dot');
    if (dot) dot.classList.add('done');
  }
  // 缓存完整 block 数据
  if (window.__extractBlockMap) {
    const cached = window.__extractBlockMap.get(blockIdStr) || {};
    // matched_sources 数组转为 source 字符串以兼容 parseMatchedFromBlock
    const matched = blockData.matched_sources || [];
    window.__extractBlockMap.set(blockIdStr, {
      ...cached,
      ...blockData,
      block_id: blockIdStr,
      source: JSON.stringify(matched),
      _pending: false,
    });
  }
  // 更新撰写进度（只统计有实际 content 的 block）
  const allItems = document.querySelectorAll('.doc-outline-item');
  const totalSections = window.__extractTotalSections || allItems.length;
  let writtenCount = 0;
  if (window.__extractBlockMap) {
    for (const [, b] of window.__extractBlockMap) {
      if (b.content && b.content.trim()) writtenCount++;
    }
  }
  const progressBadge = document.getElementById('outline-progress-badge');
  const progressText = document.getElementById('outline-progress-text');
  const progressBar = document.getElementById('outline-progress-bar');
  if (progressBadge) progressBadge.textContent = totalSections;
  if (progressText) progressText.textContent = `${writtenCount} / ${totalSections}`;
  if (progressBar) progressBar.style.width = totalSections ? `${Math.round(writtenCount/totalSections*100)}%` : '0%';

  // 如果当前选中的就是这个 block，刷新右侧
  if (currentBlock && currentBlock.block_id === blockIdStr) {
    const block = window.__extractBlockMap.get(blockIdStr);
    if (block && typeof window.__onBlockSelected === 'function') window.__onBlockSelected(block);
  }
}

async function startWorkbenchExtraction() {
  const bar = document.getElementById('extract-progress-bar');
  const stage = document.getElementById('extract-progress-stage');
  const fill = document.getElementById('extract-progress-fill');
  const pct = document.getElementById('extract-progress-pct');
  const cancelBtn = document.getElementById('extract-cancel-btn');
  const spinner = document.getElementById('extract-spinner');
  bar.classList.remove('hidden', 'error', 'cancelled');
  cancelBtn.classList.remove('hidden');
  if (spinner) spinner.style.display = '';

  let totalSec = 0;
  let doneSec = 0;
  let extractReader = null;
  let cancelled = false;
  const genAbort = new AbortController();
  let genDoneCount = 0;

  const updateProgress = (done, total) => {
    const p = total ? Math.round(done / total * 100) : 0;
    if (stage) stage.textContent = `${done}/${total}`;
    if (fill) fill.style.width = p + '%';
    if (pct) pct.textContent = p + '%';
  };

  // 并行生成单个 block（fire-and-forget）
  function generateBlockInBackground(dbId, blockIdStr) {
    fetch(`/api/blocks/${dbId}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tone: 'official', target_words: 800, model: window.__generateModel || null }),
      signal: genAbort.signal,
    }).then(async resp => {
      if (!resp.ok || !resp.body) return;
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let content = '';
      while (true) {
        const { done: rdDone, value } = await reader.read();
        if (rdDone) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let ev;
          try { ev = JSON.parse(raw); } catch { continue; }
          if (ev.text) {
            content += ev.text;
            // 更新 block 缓存
            if (window.__extractBlockMap) {
              const cached = window.__extractBlockMap.get(blockIdStr);
              if (cached) {
                cached.content = content;
                window.__extractBlockMap.set(blockIdStr, cached);
              }
            }
            // 如果当前选中的就是这个 block，实时更新编辑区
            if (currentBlock && currentBlock.block_id === blockIdStr) {
              const editable = document.getElementById('ced-editable');
              if (editable) editable.textContent = content;
            }
          }
          if (ev.block_id && ev.content) {
            content = ev.content;
          }
        }
      }
      // 生成完成，更新撰写进度
      genDoneCount++;
      const progressBadge = document.getElementById('outline-progress-badge');
      const progressText = document.getElementById('outline-progress-text');
      const progressBar = document.getElementById('outline-progress-bar');
      const totalSections = window.__extractTotalSections || 1;
      if (progressText) progressText.textContent = `${genDoneCount} / ${totalSections}`;
      if (progressBar) progressBar.style.width = totalSections ? `${Math.round(genDoneCount/totalSections*100)}%` : '0%';
    }).catch(() => {});
  }

  // 取消按钮
  cancelBtn.onclick = () => {
    cancelled = true;
    genAbort.abort();
    if (extractReader) extractReader.cancel();
    bar.classList.add('cancelled');
    cancelBtn.classList.add('hidden');
    if (spinner) spinner.style.display = 'none';
    if (stage) stage.textContent = `已取消（已完成 ${doneSec}/${totalSec}）`;
    if (typeof window.__loadBlocks === 'function') window.__loadBlocks();
  };

  try {
    const extractModelParam = window.__selectedModel || '';
    const outlineUrl = `/api/projects/${projectId}/outline` + (extractModelParam ? `?model=${encodeURIComponent(extractModelParam)}` : '');
    const resp = await fetch(outlineUrl, { method: 'POST' });
    if (!resp.ok || !resp.body) throw new Error('准备请求失败');
    extractReader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let firstSelected = false;

    while (true) {
      const { done, value } = await extractReader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        let ev;
        try { ev = JSON.parse(raw); } catch { continue; }

        if (ev.sections) {
          totalSec = ev.total || ev.sections.length;
          renderExtractOutline(ev.sections);
          updateProgress(0, totalSec);
        }

        if (ev.block_id && ev.title) {
          markOutlineDone(ev.block_id, ev);
          doneSec++;
          updateProgress(doneSec, totalSec);

          // 暂停"边准备边生成"：编写准备完成后由用户手动点"批量生成"
          // if (ev.id) {
          //   generateBlockInBackground(ev.id, ev.block_id);
          // }

          if (!firstSelected) {
            const item = document.querySelector(`.doc-outline-item[data-block-id-str="${ev.block_id}"]`);
            if (item) { item.click(); firstSelected = true; }
          }
        }

        if ('block_count' in ev) {
          updateProgress(doneSec, totalSec);
          if (fill) fill.style.width = '100%';
          if (pct) pct.textContent = '100%';
          cancelBtn.classList.add('hidden');
          setTimeout(() => bar.classList.add('hidden'), 800);
          if (typeof window.__loadBlocks === 'function') {
            await window.__loadBlocks();
          }
          // 准备成功完成 → 清除 URL 上 autoExtract，防止刷新重跑
          const url = new URL(location.href);
          url.searchParams.delete('autoExtract');
          history.replaceState({}, '', url);
        }

        if (ev.message && !ev.title) {
          bar.classList.add('error');
          cancelBtn.classList.add('hidden');
          if (spinner) spinner.style.display = 'none';
          if (stage) stage.textContent = '准备失败：' + ev.message;
        }
      }
    }
  } catch (err) {
    if (cancelled) return;
    console.error('[autoExtract]', err);
    bar.classList.add('error');
    cancelBtn.classList.add('hidden');
    retryBtn.classList.remove('hidden');
    if (spinner) spinner.style.display = 'none';
    if (stage) stage.textContent = '准备失败：' + (err.message || '网络错误');
  }
}
    

// 模式切换：进入编辑器模式时刷新一次工作区，避免用的是切走之前的旧数据。
document.addEventListener('workspace:mode', (e) => {
  if (e.detail && e.detail.mode === 'editor') {
    try { refreshWorkspace(); } catch (err) { console.warn('[editor] 进入编辑器模式刷新失败', err); }
  }
});
