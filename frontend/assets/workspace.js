import {projectChapters, summarize, statusLabels, stageLabels, activeStages, runtimeStages, isOrphanedRun, isExtractDone, diffHtml, escapeHtml as esc} from './workspace-model.mjs?v=20260921-roles';
export {statusLabels, esc};
// 材料角色体系再导出：project-init 的角色下拉与"分类纠错提示"都从这里取
export {
  REQUIREMENT_ROLES, SOURCE_ROLES, ALL_MATERIAL_ROLES,
  ROLE_LABEL as ROLE_LABEL_SHARED, ROLE_HINT as ROLE_HINT_SHARED,
  isRequirementRole, inferRoleFromFilename, roleMismatchWarning,
} from './material-roles.mjs';
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
export const workspace = { projectId: _pid, threadId: params.get('threadId'), state: {}, chapters: [], ready: false, busy: false, orphaned: false,
  // runStage：workflow_runs 里的阶段（运行期会被 runner 更新，见 orchestrator/runner.py
  // 的 _mark_next_phase）。stageLabel：**页面自己**给出的阶段文案（project-init 最清楚
  // "提炼到哪了"，它一设这里就盖过 stageLabels 的默认词）。
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
  if (workspace.threadId) q.set('threadId', workspace.threadId);
  const bid = blockId ?? workspace.selectedBlockId ?? params.get('blockId');
  if (bid) q.set('blockId', bid);
  let path = '/project-init';
  if (view === 'outline' || view === 'matching') q.set('view', view);
  if (view === 'editor') path = '/workbench';
  if (view === 'review') path = '/review';
  return path + '?' + q;
}
function currentView() {
  if (location.pathname.includes('review')) return 'review';
  if (location.pathname.includes('workbench')) return 'editor';
  return document.body.dataset.view || params.get('view') || 'outline';
}
export function renderWorkspaceChrome() { renderChrome(); }

// checkpoint 停在闸门态（outline_review / paused）时它区分不了三种子态：一节没提炼 /
// 提炼到一半 / 全提炼完了 —— extract 收尾时图直接 END，没有节点再改 stage。
// 这里用 blocks 计数补齐，**不依赖具体页面**：project-init / review / workbench /
// diff-review 都挂这条顶栏，只在某个页面里做覆盖是不够的（实测 review 页就还显示
// 「待确认目录」）。
function outlinePhaseLabel(stage) {
  if (stage !== 'outline_review' && stage !== 'paused') return '';
  const blocks = workspace.blocks || [];
  if (!blocks.length) return '';
  // 'paused' 也可能是"生成阶段暂停"：那种情况不需要这套提炼文案
  if (stage === 'paused' && Object.keys(workspace.state?.proposal?.blocks || {}).length) return '';
  const total = blocks.length;
  const done = blocks.filter(b => isExtractDone(b)).length;
  const failed = blocks.filter(b => (b.error_kind || '') === 'failed').length;
  if (done >= total) {
    return failed ? `提炼完成（${failed} 节失败，可重试）` : '提炼完成，待确认内容';
  }
  if (done > 0) {
    return failed ? `提炼到一半（${done}/${total}，${failed} 节失败）` : `提炼到一半（${done}/${total}）`;
  }
  return '';   // 一节都没提炼：闸门 1 的「待确认目录」才是对的
}
function renderChrome() {
  const nav = document.getElementById('workspace-nav');
  if (!nav) return;
  const summary = summarize(workspace.chapters);
  // 合并页（一个页面承载三个模式，body[data-modes] 里有 editor）：导航必须**原地切换**，
  // 不能再跳到 /project-init、/workbench —— 那样就又回到"整页跳转 + 丢选中章节"的老样子。
  // 判定放在这里而不是各页面，因为导航的渲染权本来就属于本模块（它还渲染状态条）。
  const inPlace = (document.body.dataset.modes || '').includes('editor');
  nav.innerHTML = [['outline','要求与大纲'],['matching','素材库'],['editor','方案编辑'],['review','质量检查']].map(([key,label]) => {
    const jump = !inPlace || key === 'review';
    const href = jump ? esc(workspaceUrl(key)) : '#';
    const modeAttr = jump ? '' : ` data-mode="${key}"`;
    const active = currentView() === key ? 'aria-current="page"' : '';
    return `<a href="${href}"${modeAttr} ${active}>${label}${key === 'review' && summary.needsAction ? `<span class="ws-count">${summary.needsAction}</span>` : ''}</a>`;
  }).join('');
  let bar = document.getElementById('workspace-status');
  if (!bar) {
    bar = document.createElement('section'); bar.id = 'workspace-status'; bar.className = 'ws-status';
    nav.closest('header').after(bar);
    bar.innerHTML = '<div class="ws-state-copy" role="status"><strong id="ws-run-label"></strong><span id="ws-run-detail"></span></div><div class="ws-status-actions"><a id="ws-todo-link">处理待办</a><button type="button" id="ws-history">版本记录</button><button type="button" id="ws-run">运行详情</button></div>';
    bar.querySelector('#ws-history').onclick = () => showHistory();
    bar.querySelector('#ws-run').onclick = showRun;
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
  // 优先级：页面自己给的（最实时，带 x/y 计数）→ 按 blocks 推出的提炼进度 → 默认词
  const label = workspace.stageLabel || outlinePhaseLabel(stage) || stageLabels[stage] || stage;
  bar.querySelector('#ws-run-label').textContent = workspace.threadId
    ? `第 ${round} 轮 · ${label}${workspace.orphaned ? '（已中断）' : ''}`
    : '开始准备方案';
  // 大纲阶段别显示"正文 x/y"（那时还没有正文），换成提炼进度更有意义
  const extracting = stage === 'outline_review' || stage === 'paused' || stage === 'extracting';
  const stats = extracting && workspace.blocks?.length
    ? `已提炼 ${workspace.blocks.filter(b => isExtractDone(b)).length}/${workspace.blocks.length} 章节`
    : (summary.total
      ? `正文 ${summary.written}/${summary.total} · 已通过 ${summary.counts.passed || 0} · 待处理 ${summary.needsAction}`
      : '');
  bar.querySelector('#ws-run-detail').textContent = workspace.orphaned
    ? `后端重启导致该任务中断，进度来自上次运行${stats ? ` · ${stats}` : ''}。可在「要求与大纲」页点「继续提炼」接着跑`
    : (stats || '上传要求后确认大纲，可随时切换工作区');
  bar.querySelector('#ws-todo-link').href = workspaceUrl('review');
  bar.dataset.busy = String(workspace.busy);
  bar.dataset.orphaned = String(workspace.orphaned);
}
export async function refreshWorkspace() {
  if (loading) return;
  loading = true;
  try {
    if (workspace.projectId) {
      // 每次刷新都读一次 workflow_runs：运行期阶段（extracting/matching）只有这张表是准的
      const runs = await request(`/api/projects/${workspace.projectId}/workflow-runs`).catch(() => []);
      workspace.runStage = runs?.[0]?.stage || null;
      if (!workspace.threadId) workspace.threadId = runs?.[0]?.thread_id || null;
    }
    const state = workspace.threadId ? await request(`/api/workflow/${encodeURIComponent(workspace.threadId)}/state`) : {};
    workspace.state = state;
    workspace.projectId = state.project_id || workspace.projectId;
    const blocksUrl = `/api/projects/${workspace.projectId}/blocks`
      + (workspace.threadId ? `?threadId=${encodeURIComponent(workspace.threadId)}` : '');
    const rows = workspace.projectId ? await request(blocksUrl) : [];
    workspace.blocks = rows;          // 原始行：顶栏要靠 error_kind/status 判断提炼进度
    workspace.chapters = projectChapters(state, rows);
    workspace.ready = true;
    renderChrome();
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
  openDialog('运行详情', `<p>补充素材 → 撰写 / 修订 → 技术与合规评审 → 人工检查；未通过的章节继续循环。</p>
    <div class="ws-summary"><strong>${esc(stageLabels[s.stage] || '尚未开始')}</strong><span>第 ${Number(s.iteration || 0)+1} 轮</span></div>
    ${c?.status === 'max_iterations' ? '<p class="ws-error">自动修订已达上限，请检查剩余问题，再决定补料、手改或发起局部修订。</p>' : ''}
    <p>仅复审不会改写正文；按意见修订会读取最新素材，并对选中的章节重新生成和复审。</p>
    <h3>需要处理的章节</h3>${pending.length ? pending.slice(0,100).map(b => `<p><a href="${esc(workspaceUrl('review',b.block_id))}">${esc(b.title)}</a> · ${statusLabels[b.status]}</p>`).join('') : '<p>暂无已识别的待办；无正文或缺少评审不代表通过。</p>'}
    ${(s.errors || []).length ? '<h3>运行错误</h3>'+s.errors.map(e=>`<p class="ws-error">${esc(e.message)}</p>`).join('') : ''}`);
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
