import {projectChapters, summarize, statusLabels, stageLabels, activeStages, runtimeStages, isOrphanedRun, isExtractDone, diffHtml, escapeHtml as esc} from './workspace-model.mjs?v=20261016-v364';
export {statusLabels, esc};
// 材料角色体系再导出：project-init 的角色下拉与"分类纠错提示"都从这里取
export {
  REQUIREMENT_ROLES, SELECTABLE_REQUIREMENT_ROLES, SOURCE_ROLES, ALL_MATERIAL_ROLES,
  ROLE_LABEL as ROLE_LABEL_SHARED, ROLE_HINT as ROLE_HINT_SHARED,
  isRequirementRole, inferRoleFromFilename, roleMismatchWarning,
} from './material-roles.mjs?v=20261016-v364';
export async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`);
  return data;
}
const params = new URLSearchParams(location.search);
// projectId 只取数字部分（与 mode-outline / mode-editor 一致）：链接里混进说明文字时
// 不再把整串当 id，避免每个接口 422 而页面毫无提示。
const _pid = (params.get('projectId') || '').match(/\d+/)?.[0] || '';
export const workspace = { projectId: _pid, packageId: params.get('packageId'), threadId: params.get('threadId'), state: {}, chapters: [], ready: false, busy: false, orphaned: false,
  // runStage：workflow_runs 里的阶段（运行期会被 runner 更新，见 orchestrator/runner.py
  // 的 _mark_next_phase）。stageLabel：**页面自己**给出的阶段文案（project-init 最清楚
  // "准备到哪了"，它一设这里就盖过 stageLabels 的默认词）。
  runStage: null, stageLabel: '', blocks: [] };
let timer, loading = false, lastProjection = null;
const listeners = new Set();
export function subscribe(callback) { listeners.add(callback); if (workspace.ready) callback(workspace); return () => listeners.delete(callback); }
// 当前选中的章节：由页面通过 workspace.selectedBlockId 写入（导航链接据此带上 blockId）。
// 不带的话「素材库 → 方案编辑」会跳到第一页、用户还得再找一遍刚看的那一章
// （模块化衔接的最后一厘米：章节是三个模块共同的导航单元）。
workspace.selectedBlockId = null;

export function workspaceUrl(view, blockId) {
  const q = new URLSearchParams();
  if (workspace.projectId) q.set('projectId', workspace.projectId);
  if (workspace.packageId) q.set('packageId', workspace.packageId);
  if (workspace.threadId) q.set('threadId', workspace.threadId);
  const bid = blockId ?? workspace.selectedBlockId ?? params.get('blockId');
  if (bid) q.set('blockId', bid);
  // 旧的 /project-init、/workbench 已合并进 /three-mode（P2-3 下线）：
  // 这里统一生成新地址，旧页只保留一个重定向，避免书签/外链失效。
  let path = '/three-mode';
  if (view === 'outline' || view === 'matching') q.set('view', view);
  if (view === 'editor') q.set('view', 'editor');
  if (view === 'review') path = '/review';
  return path + '?' + q;
}
function currentView() {
  if (location.pathname.includes('review')) return 'review';
  if (location.pathname.includes('workbench')) return 'editor';   // 旧地址直接访问时仍识别
  return document.body.dataset.view || params.get('view') || 'outline';
}
export function renderWorkspaceChrome() { renderChrome(); }

// checkpoint 停在闸门态（outline_review / paused）时它区分不了三种子态：一节没准备 /
// 编写准备到一半 / 全准备完了 —— extract 收尾时图直接 END，没有节点再改 stage。
// 这里用 blocks 计数补齐，**不依赖具体页面**：project-init / review / workbench /
// diff-review 都挂这条顶栏，只在某个页面里做覆盖是不够的（实测 review 页就还显示
// 「待确认目录」）。
function outlinePhaseLabel(stage) {
  if (stage !== 'outline_review' && stage !== 'paused') return '';
  const blocks = workspace.blocks || [];
  if (!blocks.length) return '';
  // 'paused' 也可能是"生成阶段暂停"：那种情况不需要这套准备文案
  if (stage === 'paused' && Object.keys(workspace.state?.proposal?.blocks || {}).length) return '';
  const total = blocks.length;
  const done = blocks.filter(b => isExtractDone(b)).length;
  const failed = blocks.filter(b => (b.error_kind || '') === 'failed').length;
  if (done >= total) {
    return failed ? `编写准备完成（${failed} 节失败，可重试）` : '编写准备完成，待确认内容';
  }
  if (done > 0) {
    return failed ? `编写准备到一半（${done}/${total}，${failed} 节失败）` : `编写准备到一半（${done}/${total}）`;
  }
  return '';   // 一节都没准备：闸门 1 的「待确认目录」才是对的
}
function renderChrome() {
  const nav = document.getElementById('workspace-nav');
  if (!nav) return;
  const summary = summarize(workspace.chapters);
  // 合并页（一个页面承载三个模式，body[data-modes] 里有 editor）：导航必须**原地切换**，
  // 不能再跳到 /project-init、/workbench —— 那样就又回到"整页跳转 + 丢选中章节"的老样子。
  // 判定放在这里而不是各页面，因为导航的渲染权本来就属于本模块（它还渲染状态条）。
  const inPlace = (document.body.dataset.modes || '').includes('editor');
  // ── 四模式导航**整块移除**（用户要求）──
  // 「要求与大纲 / 素材库 / 方案编辑 / 质量检查」这块导航的逻辑需要重新梳理，
  // 暂时先不梳理，先把导航整块删掉，只保留当前工作区。
  // 元素保留但清空并隐藏：其它代码会引用 `#workspace-nav`（下面的状态条就挂在
  // 它所属的 header 之后），直接删 DOM 会连带影响那些引用。
  nav.innerHTML = '';
  nav.hidden = true;
  if (!nav.dataset.navRemoved) {
    nav.dataset.navRemoved = '1';
    console.info('[workspace] 四模式导航已按用户要求移除（逻辑待重新设计）');
  }
  // 共享壳状态条已按原型移除（信息并入页面状态行 #status-bar，避免两行重复）。
  // 那三个工具（处理待办 / 版本记录 / 运行详情）现在在状态行里是**静态节点**，
  // 这里按 id 绑定一次即可（原来绑在共享壳自己的副本上，且 id 重复）。
  const bar = null;
  if (!workspace._toolsBound) {
    workspace._toolsBound = true;
    document.getElementById('ws-progress-hub')?.addEventListener('click', () => openProgressHub());
    document.getElementById('ws-specs')?.addEventListener('click', () => openWritingSpecs());
    document.getElementById('ws-materials')?.addEventListener('click', () => openMaterialsHub());
    document.addEventListener('writing-specs:open', (event) => openWritingSpecs(event.detail?.layout));
  }
  const state = workspace.state;
  // 运行期阶段（extracting/matching）以 workflow_runs 为准：这期间 checkpoint 还停在
  // 闸门态，只看 state.stage 会一直显示"待确认目录"（实测就是这个 bug）。
  const runStage = workspace.runStage;
  const running = runtimeStages.has(runStage);
  const stage = running ? runStage : (state.stage || runStage || 'idle');
  workspace.orphaned = isOrphanedRun(state);
  // 中断的 run 已经没有东西在跑了，不能再算 busy —— 否则 refreshWorkspace 会永远每 4 秒轮询。
  // 反过来，运行期阶段必须算 busy，否则 10s 才刷一次、标签看着像死的。
  workspace.busy = (activeStages.has(stage) || running) && !workspace.orphaned;
  const round = Number(state.iteration || 0) + 1;
  // 优先级：页面自己给的（最实时，带 x/y 计数）→ 按 blocks 推出的准备进度 → 默认词
  const label = workspace.stageLabel || outlinePhaseLabel(stage) || stageLabels[stage] || stage;
  const runLabel = null;   // 共享壳已移除；阶段文案由页面状态行显示
  if (runLabel) runLabel.textContent = workspace.threadId
    ? `第 ${round} 轮 · ${label}${workspace.orphaned ? '（已中断）' : ''}`
    : '开始准备方案';
  // 大纲阶段别显示"正文 x/y"（那时还没有正文），换成准备进度更有意义
  const extracting = stage === 'outline_review' || stage === 'paused' || stage === 'extracting';
  const stats = extracting && workspace.blocks?.length
    ? `已准备 ${workspace.blocks.filter(b => isExtractDone(b)).length}/${workspace.blocks.length} 章节`
    : (summary.total
      ? `正文 ${summary.written}/${summary.total} · 已通过 ${summary.counts.passed || 0} · 待处理 ${summary.needsAction}`
      : '');
  const runDetail = null;
  if (runDetail) runDetail.textContent = workspace.orphaned
    ? `后端重启导致该任务中断，进度来自上次运行${stats ? ` · ${stats}` : ''}。可在面板点「继续准备」接着跑`
    : (stats || '上传要求后确认大纲，可随时切换工作区');
  // busy/orphaned 现在挂在 body 上，供样式与状态行使用（原来挂在共享壳上）
  document.body.dataset.busy = String(workspace.busy);
  document.body.dataset.orphaned = String(workspace.orphaned);
}
export async function refreshWorkspace() {
  if (loading) return;
  loading = true;
  try {
    if (workspace.projectId) {
      // 每次刷新都读一次 workflow_runs：运行期阶段（extracting/matching）只有这张表是准的
      const scope = workspace.packageId ? `?packageId=${encodeURIComponent(workspace.packageId)}` : '';
      const runs = await request(`/api/projects/${workspace.projectId}/workflow-runs${scope}`).catch(() => []);
      // **跳过已作废/失败的运行**：runs 是按创建时间倒序的，最新那个可能已作废，
      // 照着它取 blocks 会让顶栏数字与目录树不一致（实测顶栏显示 0/276、
      // 而目录树是有效运行的 212 节 —— 同一页面两个数字打架）。
      const alive = runs.find(r => r.stage !== 'aborted' && r.stage !== 'failed');
      const chosen = runs.find(r => r.thread_id === workspace.threadId) || alive || runs?.[0] || null;
      if (!workspace.threadId) workspace.threadId = chosen?.thread_id || null;
      workspace.runStage = (runs.find(r => r.thread_id === workspace.threadId) || chosen)?.stage || null;
    }
    const state = workspace.threadId ? await request(`/api/workflow/${encodeURIComponent(workspace.threadId)}/state`) : {};
    workspace.state = state;
    workspace.projectId = state.project_id || workspace.projectId;
    const blocksUrl = `/api/projects/${workspace.projectId}/blocks`
      + (workspace.threadId ? `?threadId=${encodeURIComponent(workspace.threadId)}` : '');
    const rows = workspace.projectId ? await request(blocksUrl) : [];
    workspace.blocks = rows;          // 原始行：顶栏要靠 error_kind/status 判断准备进度
    workspace.chapters = projectChapters(state, rows);
    workspace.ready = true;
    renderChrome();
    void updateProgressBadge();
    const projection = JSON.stringify([workspace.state,workspace.chapters]);
    if (projection !== lastProjection) {
      lastProjection = projection;
      for (const callback of listeners) callback(workspace);
    }
    if (workspace.projectId && !workspace.projectLoaded) {
      const p = await request(`/api/projects/${workspace.projectId}`);
      const name = document.getElementById('header-project-name');
      if (name) { name.textContent = p.name; name.title = '返回项目列表'; }
      workspace.projectLoaded = true;
    }
    document.getElementById('ws-load-error')?.remove();
  } catch (error) {
    let el = document.getElementById('ws-load-error');
    if (!el) { el = document.createElement('div'); el.id = 'ws-load-error'; el.className = 'ws-error'; document.getElementById('workspace-nav')?.closest('header')?.after(el); }
    el.textContent = `状态同步失败：${error.message}。将自动重试。`;
  } finally {
    loading = false;
    clearTimeout(timer);
    timer = setTimeout(refreshWorkspace, workspace.busy ? 4000 : 10000);
  }
}
/** 统一提示：替代原生 alert（不打断样式体系，Esc / 点遮罩可关）。 */
export function uiAlert(message, { title = '提示' } = {}) {
  const dialog = openDialog(title,
    `<p>${esc(message)}</p><footer><button type="button" data-ok class="ui-btn ui-btn--primary">知道了</button></footer>`);
  const ok = dialog.querySelector('[data-ok]');
  ok.onclick = () => dialog.close();
  ok.focus();
  return dialog;
}

/** 统一确认：替代原生 confirm。返回 Promise<boolean>（Esc / 点遮罩 = 取消）。 */
export function uiConfirm(message, { title = '请确认', confirmText = '确认',
                                    cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    const dialog = openDialog(title, `<p>${esc(message)}</p><footer>
      <button type="button" data-cancel class="ui-btn">${esc(cancelText)}</button>
      <button type="button" data-ok class="ui-btn ${danger ? 'ui-btn--danger' : 'ui-btn--primary'}">${esc(confirmText)}</button>
    </footer>`);
    let done = false;
    const finish = (v) => { if (done) return; done = true; resolve(v); dialog.close(); };
    dialog.querySelector('[data-ok]').onclick = () => finish(true);
    dialog.querySelector('[data-cancel]').onclick = () => finish(false);
    dialog.addEventListener('close', () => finish(false));   // Esc / 点遮罩
    // 危险操作把默认焦点给**取消**按钮（普通操作给确认）。
    // 否则"弹窗一出现，按 Enter 就执行危险操作" —— 实测被这个坑过：
    // 自动化截图流程误触 Enter，删掉了几行目录。
    (danger ? dialog.querySelector('[data-cancel]') : dialog.querySelector('[data-ok]')).focus();
  });
}

// 非模块脚本（历史内联脚本 / 别处的 window 调用）也能用同一套
window.__ui = { alert: uiAlert, confirm: uiConfirm };

export function openDialog(title, html) {
  const dialog = document.createElement('dialog'); dialog.className = 'ws-dialog';
  dialog.innerHTML = `<header><h2>${esc(title)}</h2><button type="button" aria-label="关闭">×</button></header><div class="ws-dialog-body">${html}</div>`;
  document.body.append(dialog); dialog.querySelector('header button').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
  dialog.showModal(); return dialog;
}
function showRun() {
  const s = workspace.state;
  const c = s.review?.convergence;
  const pending = workspace.chapters.filter(b => ['material','revise','stale','error'].includes(b.status));
  openDialog('运行详情', `<div id="ws-run-records"><p class="ws-dim">正在加载本标包的处理记录…</p></div>
    <p>补充素材 → 撰写 / 修订 → 技术与合规评审 → 人工检查；未通过的章节继续循环。</p>
    <div class="ws-summary"><strong>${esc(stageLabels[s.stage] || '未开始')}</strong><span>第 ${Number(s.iteration || 0)+1} 轮</span></div>
    ${c?.status === 'max_iterations' ? '<p class="ws-error">自动修订已达上限，请检查剩余问题，再决定补料、手改或发起局部修订。</p>' : ''}
    <p>仅复审不会改写正文；按意见修订会读取最新素材，并对选中的章节重新生成和复审。</p>
    <h3>需要处理的章节</h3>${pending.length ? pending.slice(0,100).map(b => `<p><a href="${esc(workspaceUrl('review',b.block_id))}">${esc(b.title)}</a> · ${statusLabels[b.status]}</p>`).join('') : '<p>暂无已识别的待办；无正文或缺少评审不代表通过。</p>'}
    ${(s.errors || []).length ? '<h3>运行错误</h3>'+s.errors.map(e=>`<p class="ws-error">${esc(e.message)}</p>`).join('') : ''}`);
  void fillRunRecords();
}
/** 本标包的处理记录：目录草案（商务/技术各自一份）+ 折叠的解析记录。
 *
 * 用户问过"为什么同一个标包有两条 run"：一条是解析运行（依据来源），
 * 一条是按应答类型派生的目录草案。「运行详情」里把这两类关系摊开，
 * 草案直接可打开，解析记录默认折叠（它不是目录，只是来源）。
 */
async function fillRunRecords() {
  const host = document.getElementById('ws-run-records');
  if (!host) return;
  if (!workspace.projectId || !workspace.packageId) { host.remove(); return; }
  const linkFor = (tid) => {
    const u = new URL(location.href);
    u.searchParams.set('threadId', tid);
    u.searchParams.set('planning', '1');
    return u.toString();
  };
  try {
    const d = await request(`/api/projects/${workspace.projectId}/response-drafts?packageId=${encodeURIComponent(workspace.packageId)}`);
    if (!host.isConnected) return;
    const drafts = d.drafts || [];
    const rows = drafts.map((x) => {
      const state = x.confirmed ? '已确认' : (x.has_outline ? '待确认' : '生成中');
      return `<p>· <b>${x.type === 'commerce' ? '商务' : '技术'}应答目录</b>`
        + ` · ${Number(x.outline_count) || 0} 个节点 · ${state}`
        + ` <a href="${esc(linkFor(x.thread_id))}">打开</a></p>`;
    }).join('');
    host.innerHTML = `<h3>本标包的处理记录</h3>`
      + (rows || '<p class="ws-dim">还没有生成目录草案：在「应答策划」点「商务」/「技术」卡片即可生成。</p>')
      + (d.source_thread_id
        ? `<details><summary>解析记录（依据来源，不是目录）</summary>`
          + `<p class="ws-dim">thread ${esc(String(d.source_thread_id).slice(0, 8))}… <a href="${esc(linkFor(d.source_thread_id))}">打开</a></p>`
          + `</details>`
        : '');
    host.dispatchEvent(new CustomEvent('filled'));   // 供测试/其它逻辑判断已填
  } catch (err) {
    if (host.isConnected) host.innerHTML = `<p class="ws-dim">记录加载失败：${esc(String(err?.message || err))}</p>`;
  }
}

/** 跳到一个工作台阶段（在合并页里原地切，不整页跳转）。 */
function jumpStage(stageId) {
  const rail = document.querySelector(`.sr-rail-item[data-stage="${stageId}"]`);
  if (rail) { rail.click(); return true; }
  return false;
}

function openThread(tid) {
  if (!tid) return;
  const u = new URL(location.href);
  u.searchParams.set('threadId', tid);
  u.searchParams.set('planning', '1');
  location.href = u.toString();
}

/** 状态行摘要：编写 % · 待办 N（节流 15s，避免每次轮询都打两个聚合接口）。 */
let progressBadgeAt = 0;
async function updateProgressBadge(force = false) {
  const button = document.getElementById('ws-progress-hub');
  if (!button || !workspace.projectId) return;
  if (!force && Date.now() - progressBadgeAt < 15000) return;
  progressBadgeAt = Date.now();
  const scope = workspace.packageId ? `?packageId=${encodeURIComponent(workspace.packageId)}` : '';
  try {
    const [progress, todo] = await Promise.all([
      request(`/api/projects/${workspace.projectId}/progress${scope}`),
      request(`/api/projects/${workspace.projectId}/todos${scope}`),
    ]);
    const percent = progress?.units?.percent ?? 0;
    const total = (todo?.todos || []).length;
    const high = todo?.counts?.high || 0;
    button.textContent = `编写 ${percent}% · 待办 ${total}`;
    button.classList.toggle('is-warn', high > 0);
    button.title = `当前阶段：${progress?.current?.label || '—'}；待办 ${total} 条（高优 ${high}）`;
  } catch {
    button.textContent = '编写 — · 待办 —';
  }
}

/** 进度中心：进度 / 待办 / 记录（合并原「处理待办 / 版本记录 / 运行详情」三个入口）。 */
const SPEC_LABELS = {
  global: '全局纪律（L0）', understanding: '项目理解', solution: '技术方案',
  implementation: '实施计划', evidence: '人员／业绩', point_to_point: '点对点应答',
};
const SPEC_SECTIONS = [
  ['skeleton', '骨架', '章节的论证顺序，每行一项（按顺序执行）'],
  ['tables', '必表', '必须出现的表格，每行一张（写清表名与列）'],
  ['figures', '必图', '必须出现的图示，每行一张'],
  ['pitfalls', '失分点', '评审容易扣分的写法，每行一条'],
  ['notes', '备注 / 硬约束', '来源与合规、篇幅与语言等补充要求'],
];

/** 编写规范抽屉（IA 重构版）：
 * 左 = 规范库（L0 全局 + L1 模块配方，带版本与影响面）；右 = 结构化编辑。
 * 为什么结构化：规范本身就是"骨架/必表/必图/失分点"四类内容，一整块裸文本既改不动也校验不了。
 */
export async function openWritingSpecs(layoutKey) {
  const dialog = openDialog('编写规范', '<p class="ws-dim">加载中…</p>');
  dialog.classList.add('ws-specs-dialog');
  const body = dialog.querySelector('.ws-dialog-body');
  let specsData;
  try {
    specsData = await request('/api/writing-specs');
  } catch (err) {
    body.innerHTML = `<p class="ws-error">规范加载失败：${esc(String(err?.message || err))}</p>`;
    return;
  }
  const specs = specsData.specs || {};
  const keys = Object.keys(specs);
  let selected = layoutKey && specs[layoutKey] ? layoutKey : (specs.global ? 'global' : keys[0]);
  if (!selected) { body.innerHTML = '<p class="ws-dim">还没有规范数据。</p>'; return; }
  let draft = null;            // 当前编辑中的结构化内容
  let savedVersion = null;

  const structuredToLines = (value) => (value || []).join('\n');
  const linesToItems = (text) => String(text || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const sameAsSaved = () => JSON.stringify(draft) === JSON.stringify(savedVersion);

  const paint = () => {
    const spec = specs[selected] || {};
    savedVersion = JSON.parse(JSON.stringify(spec.structured || {}));
    draft = JSON.parse(JSON.stringify(spec.structured || {}));
    const isGlobal = selected === 'global';
    const item = (key, label, hint) => `<section class="ws-spec-section">
        <header><b>${label}</b><span class="ws-dim">${hint}</span></header>
        <textarea rows="${key === 'notes' ? 4 : 3}" data-spec-section="${key}" spellcheck="false"
          placeholder="${key === 'notes' ? '补充要求…' : '每行一项…'}">${esc(key === 'notes' ? (draft.notes || '') : structuredToLines(draft[key]))}</textarea>
      </section>`;
    body.innerHTML = `<div class="ws-specs-layout">
      <aside class="ws-specs-nav">
        <h4>规范库</h4>
        <button type="button" class="ws-spec-item${isGlobal ? ' on' : ''}" data-spec-key="global">
          <span>全局纪律<span class="ws-spec-l0">L0 · 全部章节</span></span>
          <em>v${specs.global ? specs.global.version : '—'}</em>
        </button>
        <h4>模块配方 <span class="ws-dim">L1</span></h4>
        ${keys.filter((key) => key !== 'global').map((key) => `
          <button type="button" class="ws-spec-item${key === selected ? ' on' : ''}" data-spec-key="${esc(key)}">
            <span>${esc(SPEC_LABELS[key] || key)}${specs[key]?.impact ? `<span class="ws-spec-impact">${specs[key].impact} 章在用</span>` : ''}</span>
            <em>v${specs[key]?.version ?? '—'}</em>
          </button>`).join('')}
        <p class="ws-specs-note">迭代建议（来自人工修改与审校意见）在 P2 接入。</p>
      </aside>
      <section class="ws-specs-main">
        <header class="ws-specs-head">
          <div><h4>${esc(SPEC_LABELS[selected] || selected)}</h4>
            <p class="ws-dim">当前 <b>v${spec.version ?? '—'}</b>${spec.updated_at ? ` · 修改于 ${esc(String(spec.updated_at).slice(0, 16))}` : ''}${spec.impact ? ` · 影响本项目 ${spec.impact} 个章节` : ''}</p></div>
          <div class="ws-specs-state"><span data-spec-dirty class="ws-spec-dirty" hidden>未保存修改</span></div>
        </header>
        ${SPEC_SECTIONS.map(([key, label, hint]) => item(key, label, hint)).join('')}
        <footer class="ws-specs-actions">
          <button type="button" class="ws-spec-save" data-spec-save>保存为新版本</button>
          <button type="button" data-spec-reset>放弃修改</button>
          <span class="ws-dim" data-spec-msg></span>
        </footer>
        <details class="ws-specs-history"><summary>历史版本 <span data-spec-history-count></span></summary>
          <div data-spec-history></div></details>
      </section>
    </div>`;

    const refreshDirty = () => {
      const dirty = !sameAsSaved();
      const badge = body.querySelector('[data-spec-dirty]');
      if (badge) badge.hidden = !dirty;
    };
    body.querySelectorAll('[data-spec-key]').forEach((button) => button.addEventListener('click', () => {
      selected = button.dataset.specKey;
      paint();
    }));
    body.querySelectorAll('[data-spec-section]').forEach((textarea) => textarea.addEventListener('input', () => {
      const key = textarea.dataset.specSection;
      if (key === 'notes') draft.notes = textarea.value;
      else draft[key] = linesToItems(textarea.value);
      refreshDirty();
    }));
    body.querySelector('[data-spec-reset]')?.addEventListener('click', () => paint());
    body.querySelector('[data-spec-save]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const saved = await request(`/api/writing-specs/${encodeURIComponent(selected)}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ body: draft }),
        });
        specs[selected] = { ...(specs[selected] || {}), version: saved.version, structured: saved.structured, text: saved.text };
        paint();
        body.querySelector('[data-spec-msg]').textContent = `已保存为 v${saved.version}`;
      } catch (err) {
        button.disabled = false;
        body.querySelector('[data-spec-msg]').textContent = `保存失败：${String(err?.message || err)}`;
      }
    });
    void fillHistory();
  };

  const fillHistory = async () => {
    const host = body.querySelector('[data-spec-history]');
    const countHost = body.querySelector('[data-spec-history-count]');
    if (!host) return;
    const hist = await request(`/api/writing-specs/${encodeURIComponent(selected)}/history`).catch(() => ({ versions: [] }));
    const versions = hist.versions || [];
    if (countHost) countHost.textContent = `（${versions.length}）`;
    host.innerHTML = versions.map((version) => `<div class="ws-spec-version">
        <span><b>v${version.version}</b><span class="ws-dim">${esc(String(version.created_at || '').slice(0, 16))}</span></span>
        <button type="button" data-spec-rollback="${version.version}">回滚到此版</button>
      </div>`).join('') || '<p class="ws-dim">暂无历史版本</p>';
    host.querySelectorAll('[data-spec-rollback]').forEach((button) => button.addEventListener('click', async () => {
      const version = Number(button.dataset.specRollback);
      if (!globalThis.confirm(`回滚到 v${version}？会生成一个新版本（历史不丢）。`)) return;
      try {
        await request(`/api/writing-specs/${encodeURIComponent(selected)}/rollback`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version }),
        });
        const fresh = await request('/api/writing-specs');
        Object.assign(specs, fresh.specs || {});
        paint();
      } catch (err) {
        body.querySelector('[data-spec-msg]').textContent = `回滚失败：${String(err?.message || err)}`;
      }
    }));
  };

  paint();
}

/** 素材中心：一个入口、两个分区 —— 我方材料（证据）/ 外部依据（官方来源）。
 *  为什么分区：混排会把"外部依据"当成"我方证明材料"，投标审计是硬错误。 */
export async function openMaterialsHub() {
  const dialog = openDialog('素材', '<p class="ws-dim">加载中…</p>');
  const body = dialog.querySelector('.ws-dialog-body');
  let materials = [];
  let citations = [];
  try {
    const [m, c] = await Promise.all([
      request(`/api/projects/${workspace.projectId}/materials`),
      request(`/api/projects/${workspace.projectId}/citations`),
    ]);
    materials = Array.isArray(m) ? m : (m.materials || []);
    citations = c.citations || [];
  } catch (err) {
    body.innerHTML = `<p class="ws-error">素材加载失败：${esc(String(err?.message || err))}</p>`;
    return;
  }
  const activeCitations = citations.filter((c) => c.status !== 'rejected');
  const accepted = activeCitations.filter((c) => c.status === 'accepted');
  const pending = activeCitations.filter((c) => c.status !== 'accepted');
  const size = (bytes) => (Number(bytes) >= 1024 * 1024
    ? `${(Number(bytes) / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(Number(bytes || 0) / 1024))} KB`);
  const materialRows = materials.map((m) => `<div class="ws-mat-row">
      <span><b>${esc(m.filename || m.name || '')}</b><small>${esc(String(m.type || '').toUpperCase())} · ${size(m.file_size || m.size_bytes)} · ${esc(m.parse_status || '')}</small></span>
      <em>${m.confirmed ? '已确认' : '待确认'}</em></div>`).join('')
    || '<p class="ws-dim">还没有上传我方材料。</p>';
  const citationRows = (list, empty) => (list.map((c) => `<div class="ws-mat-row">
      <span><b>${esc(c.title || c.url)}</b><small>${esc(c.publisher || c.domain)} · ${esc(c.published_at || '日期待补')} · 关联 ${(c.links || []).length} 章</small>
      <small class="ws-mat-url">${esc(c.url)}</small></span>
      <em>${c.status === 'accepted' ? '已接受' : '待处理'}</em></div>`).join('') || `<p class="ws-dim">${empty}</p>`);
  body.innerHTML = `<div class="ws-mat-layout">
      <nav class="ws-mat-nav" aria-label="素材分区">
        <h4>素材</h4>
        <button type="button" data-mat="mine" class="on">我方材料 <em>${materials.length}</em></button>
        <button type="button" data-mat="cite">外部依据 <em>${activeCitations.length}</em></button>
        <p class="ws-mat-note">我方材料＝证据；外部依据＝官方来源。两者分开，生成时也分开注入。</p>
      </nav>
      <section class="ws-mat-main">
        <div data-mat-pane="mine">
          <h4>我方材料 ${materials.length}</h4>
          ${materialRows}
        </div>
        <div data-mat-pane="cite" hidden>
          <h4>外部依据 ${activeCitations.length}</h4>
          <p class="ws-dim">外部依据必须可核验（官方原文）；二手转载、公众号、百科不采。引用不占"我方材料"名额。</p>
          <h4 class="ws-mat-sub">已接受（${accepted.length}）</h4>${citationRows(accepted, '还没有已接受的官方依据：在编写页「素材」页签点「添加官方来源」')}
          ${pending.length ? `<h4 class="ws-mat-sub">待处理（${pending.length}）</h4>${citationRows(pending, '')}` : ''}
        </div>
      </section>
    </div>`;
  body.querySelectorAll('[data-mat]').forEach((button) => button.addEventListener('click', () => {
    body.querySelectorAll('[data-mat]').forEach((x) => x.classList.toggle('on', x === button));
    body.querySelectorAll('[data-mat-pane]').forEach((pane) => {
      pane.hidden = pane.dataset.matPane !== button.dataset.mat;
    });
  }));
}

export async function openProgressHub() {
  const dialog = openDialog('进度中心', '<p class="ws-dim">加载中…</p>');
  const body = dialog.querySelector('.ws-dialog-body');
  const scope = workspace.packageId ? `?packageId=${encodeURIComponent(workspace.packageId)}` : '';
  let progress; let todo;
  try {
    [progress, todo] = await Promise.all([
      request(`/api/projects/${workspace.projectId}/progress${scope}`),
      request(`/api/projects/${workspace.projectId}/todos${scope}`),
    ]);
  } catch (err) {
    body.innerHTML = `<p class="ws-error">进度加载失败：${esc(String(err?.message || err))}</p>`;
    return;
  }
  if (!dialog.isConnected) return;
  const stateText = { done: '已完成', active: '进行中', pending: '未开始' };
  const stageHtml = (progress.stages || []).map(s => `<span class="ws-hub-stage ${esc(s.state)}">${esc(s.label)}`
    + `${s.percent != null ? ` · ${s.percent}%` : ''}<em>${stateText[s.state] || ''}</em></span>`).join('');
  const typeHtml = (progress.types || []).map(t => {
    const dir = t.directory_confirmed ? '目录已确认' : (t.has_outline ? '目录待确认' : '无目录');
    const budget = t.budget?.confirmed ? `篇幅已确认 ${t.budget.allocated}/${t.budget.total} 页`
      : (t.budget?.total ? `篇幅待确认（已分配 ${t.budget.allocated} 页）` : '篇幅未设置');
    return `<div class="ws-hub-type"><h4>${esc(t.label)}应答</h4>`
      + `<p>${dir} · ${budget} · 编写单元 ${t.units.complete}/${t.units.total}（${t.units.percent}%）`
      + ` · 已写 ${t.written_pages} 页</p></div>`;
  }).join('') || '<p class="ws-dim">本标包还没有目录草案：去「应答策划」点商务/技术卡片生成。</p>';
  const todoHtml = (todo.todos || []).map((item, index) => `<div class="ws-hub-todo ${esc(item.severity)}">`
    + `<div><b>${esc(item.title)}</b><span class="ws-dim">${esc(item.detail || '')}</span></div>`
    + `<button type="button" data-todo="${index}">${esc(item.action?.label || '去处理')}</button></div>`).join('')
    || '<p class="ws-dim">没有待办：目录与篇幅都已确认，没有素材缺口。</p>';
  body.innerHTML = `<nav class="ws-hub-tabs" aria-label="进度中心">
      <button type="button" data-hub="progress" class="on">进度</button>
      <button type="button" data-hub="todos">待办${(todo.todos || []).length ? ` ${(todo.todos || []).length}` : ''}</button>
      <button type="button" data-hub="records">记录</button>
    </nav>
    <section data-hub-pane="progress">
      <p class="ws-hub-stages">${stageHtml}</p>
      ${progress.current ? `<p class="ws-dim">当前：${esc(progress.current.label)}（${stateText[progress.current.state] || ''}）</p>` : ''}
      ${typeHtml}
      <p class="ws-micro">编写单元口径与编写页一致（父目录无正文不算单元）；篇幅取「应答策划」的分配结果。</p>
    </section>
    <section data-hub-pane="todos" hidden>${todoHtml}</section>
    <section data-hub-pane="records" hidden>
      <p class="ws-micro">运行记录与章节版本都保留只读；章节版本在每次保存正文时递增。</p>
      <div class="ws-hub-records">
        <button type="button" data-record="run">运行记录</button>
        <button type="button" data-record="history">章节版本</button>
      </div>
    </section>`;
  const tabs = body.querySelectorAll('.ws-hub-tabs button');
  tabs.forEach(button => button.addEventListener('click', () => {
    tabs.forEach(x => x.classList.toggle('on', x === button));
    body.querySelectorAll('[data-hub-pane]').forEach(pane => {
      pane.hidden = pane.dataset.hubPane !== button.dataset.hub;
    });
  }));
  body.querySelectorAll('[data-todo]').forEach(button => button.addEventListener('click', () => {
    const item = (todo.todos || [])[Number(button.dataset.todo)];
    if (!item) return;
    if (item.action?.thread_id) { dialog.close(); openThread(item.action.thread_id); return; }
    if (item.kind === 'run') { body.querySelector('[data-hub="records"]').click(); return; }
    dialog.close();
    if (!jumpStage(item.stage)) openThread(workspace.threadId);
  }));
  body.querySelector('[data-record="run"]')?.addEventListener('click', () => showRun());
  body.querySelector('[data-record="history"]')?.addEventListener('click', () => showHistory());
}

export async function showHistory(dbId) {
  const dialog = openDialog(dbId ? '章节版本记录' : '项目版本记录', '<p>加载中…</p>');
  const body = dialog.querySelector('.ws-dialog-body');
  try {
    if (!workspace.projectId && !dbId) { body.textContent = '请先创建项目。'; return; }
    const data = await request(dbId ? `/api/blocks/${dbId}/revisions` : `/api/projects/${workspace.projectId}/revisions`);
    const rows = dbId ? data.slice().reverse() : data.revisions || [];
    if (!dialog.isConnected) return;
    body.innerHTML = '<p class="ws-muted">以下为各章节保存的版本，不是整份方案的统一版本号。历史记录只读。</p><div class="ws-history-list"></div><div class="ws-history-detail"></div>';
    const list = body.querySelector('.ws-history-list');
    if (!rows.length) { list.textContent = '暂无已记录版本。首次保存或下一轮生成后会记录；历史运行不会补造版本。'; return; }
    for (const [i,r] of rows.entries()) {
      const button = document.createElement('button'); button.type='button';
      button.textContent = `${r.block_title || '当前章节'} · r${r.revision_no} · ${r.summary || '保存'} · ${r.created_at || ''}`;
      button.onclick = () => {
        list.querySelectorAll('button').forEach(b=>b.classList.toggle('active', b===button));
        const previous = r.prev_content ?? (dbId ? rows[i+1]?.content : '') ?? '';
        body.querySelector('.ws-history-detail').innerHTML = `<h3>历史版本 r${r.revision_no} · 只读</h3>${diffHtml(previous,r.content,'上一版本','此版本')}`;
      };
      list.append(button);
    }
    list.firstElementChild.click();
  } catch(e) { body.textContent = `版本加载失败：${e.message}`; }
}
export function confirmChapterAction(ids, action) {
  const chapters = workspace.chapters.filter(b => ids.includes(b.block_id));
  const revise = action === 'revise';
  const dialog = openDialog(revise ? '按评审意见修订' : '复审选中章节',
    `<p>${revise ? '会重写以下章节，携带技术与合规评审意见，并先检索最新素材；完成后自动复审。未选章节保持原文。' : '会评审以下章节当前已保存的正文，不自动改写；结果返回质量检查。'}</p>
    <ul>${chapters.map(b=>`<li>${esc(b.title)} · ${b.issues.length} 条意见${b.issues.some(i=>i.needs_material) ? ' · 需补料' : ''}</li>`).join('')}</ul>
    ${revise && chapters.some(b=>b.issues.some(i=>i.needs_material)) ? `<p class="ws-error">含缺失证明的问题。请先补充真实素材；仅重新生成无法代替证明。<a href="${esc(workspaceUrl('matching'))}">前往素材库</a></p>` : ''}
    <p role="status" class="ws-action-status"></p><footer><button type="button" data-cancel>取消</button><button type="button" class="ws-primary" data-confirm>${revise ? '确认修订并复审' : '开始复审'}</button></footer>`);
  dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
  dialog.querySelector('[data-confirm]').onclick = async e => {
    e.target.disabled = true;
    try {
      await request(`/api/workflow/${encodeURIComponent(workspace.threadId)}/workspace-action`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action,block_ids:ids})});
      dialog.close(); await refreshWorkspace();
    } catch(err) { dialog.querySelector('.ws-action-status').textContent = err.message; e.target.disabled=false; }
  };
}
if (document.getElementById('workspace-nav')) { renderChrome(); refreshWorkspace(); }
window.addEventListener('beforeunload', () => clearTimeout(timer));
window.addEventListener('workspace:refresh', refreshWorkspace);
new MutationObserver(() => { if (workspace.ready) renderChrome(); }).observe(document.body, {attributes:true, attributeFilter:['data-view']});
