import {workspace, subscribe, workspaceUrl, request, refreshWorkspace, confirmChapterAction, showHistory, openDialog, statusLabels, esc} from './workspace.js?v=20260917-loop7';
import {summarize, diffHtml} from './workspace-model.mjs?v=20260917-loop5';
const $ = id => document.getElementById(id);
let activeId = new URLSearchParams(location.search).get('blockId');
let mode = 'current', visibleLimit = 100;
const selected = new Set();
const attention = new Set(['material','revise','stale','error','historical','unreviewed']);
function filtered() {
  const query = $('quality-search').value.trim().toLowerCase();
  const filter = $('quality-filter').value;
  return workspace.chapters.filter(b =>
    (!query || `${b.title} ${b.issues.map(i=>i.point).join(' ')}`.toLowerCase().includes(query)) &&
    (filter === 'all' || (filter === 'attention' ? attention.has(b.status) : b.status === filter)));
}
function renderQueue() {
  const rows = filtered();
  $('quality-list').innerHTML = rows.slice(0, visibleLimit).map(b => `<div class="quality-chapter${b.block_id === activeId ? ' active' : ''}">
    <input type="checkbox" data-select="${esc(b.block_id)}" aria-label="选择 ${esc(b.title)}" ${selected.has(b.block_id)?'checked':''} />
    <button type="button" data-chapter="${esc(b.block_id)}" ${b.block_id === activeId?'aria-current="true"':''}><span class="quality-chapter-title">${esc(b.title)}</span><span class="ws-chapter-status" data-status="${b.status}">${statusLabels[b.status]}${b.issues.length ? ` · ${b.issues.length} 项` : ''}</span></button></div>`).join('') || '<p class="ws-muted">没有符合条件的章节。</p>';
  $('quality-more').hidden = rows.length <= visibleLimit;
  $('quality-more').textContent = `显示更多（已显示 ${Math.min(visibleLimit,rows.length)} / ${rows.length}）`;
}
function renderSummary() {
  const s = summarize(workspace.chapters);
  $('quality-summary').innerHTML = [['已写正文',`${s.written} / ${s.total}`],['已通过',s.counts.passed || 0],['待补料',s.counts.material || 0],['待处理章节',s.needsAction]].map(([label,value])=>`<div class="quality-metric"><span>${label}</span><strong>${value}</strong></div>`).join('');
  const report = workspace.state.review?.report;
  $('quality-report-body').innerHTML = report ? [['主要风险',report.top_risks],['缺失证明',report.missing_evidence],['缺失加分项',report.missing_bonus]].map(([title,items])=>`<section><h3>${title}</h3><ul>${(items || []).map(i=>`<li>${esc(typeof i==='string'?i:JSON.stringify(i))}</li>`).join('') || '<li>本次报告未列出</li>'}</ul></section>`).join('') : '<p>尚无全局评审报告。</p>';
  const c = workspace.state.review?.convergence;
  $('convergence-bar').hidden = !c;
  if (c) {
    $('cv-round').textContent = Number(workspace.state.iteration || 0)+1;
    $('cv-status').textContent = c.status === 'max_iterations' ? '已达自动迭代上限，请人工检查' : c.status === 'converged' ? '后端判定已收敛；请结合正文覆盖率和章节评审核对' : '存在未达标章节';
    $('cv-unconverged').textContent = `${c.unconverged_blocks?.length || 0} 个未达标章节`;
    $('convergence-bar').classList.toggle('unconverged',c.status==='max_iterations');
  }
  $('quality-export').disabled = !s.written || workspace.busy;
  $('quality-approve').disabled = !s.written || workspace.busy || workspace.state.stage !== 'report_review';
}
function refreshActions() {
  const rows = workspace.chapters.filter(b=>selected.has(b.block_id));
  const actionableStage = ['report_review','done','paused','materials_review'].includes(workspace.state.stage);
  const disabled = !rows.length || workspace.busy || !workspace.threadId || !actionableStage;
  $('quality-revise').disabled = disabled;
  $('quality-recheck').disabled = disabled || rows.some(b=>!b.content.trim());
  $('quality-selection').textContent = workspace.busy ? '任务运行中，可继续查看正文与历史；完成后可发起下一次操作。' : `已选 ${rows.length} 个章节${rows.some(b=>!b.content.trim()) ? ' · 空正文需先生成，不能直接复审' : ''}`;
}
function renderDetail() {
  const b = workspace.chapters.find(b=>b.block_id===activeId);
  if (!b) { $('quality-document-body').innerHTML='<p>暂无章节。请先在“要求与大纲”完成提炼。</p>'; return; }
  $('quality-title').textContent = b.title;
  $('quality-breadcrumb').textContent = `${statusLabels[b.status]} · 当前已保存正文`;
  $('quality-edit').hidden = false; $('quality-edit').href = workspaceUrl('editor',b.block_id);
  $('quality-history').disabled = !b.db_id;
  document.querySelectorAll('[data-doc-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.docMode===mode)));
  if (mode === 'diff') {
    $('quality-document-body').innerHTML = b.hasSnapshot
      ? `<p class="ws-muted">${b.previous===b.content?'正文与该评审快照一致。':'正文已发生修改，旧评审意见不能直接代表当前正文质量。'}</p>${diffHtml(b.previous,b.content)}`
      : '<p>该历史评审未记录正文快照，无法可靠对比。执行一次复审后会建立对应关系；也可查看已保存的章节版本。</p>';
  } else $('quality-document-body').innerHTML = b.content.trim() ? `<pre>${esc(b.content)}</pre>` : `<p>本章尚无正文。</p><a href="${esc(workspaceUrl('editor',b.block_id))}">前往撰写本章 →</a>`;
  $('quality-findings').innerHTML = `<h2>评审意见</h2><div class="quality-scores"><span>技术 ${b.tech?.score ?? '—'}</span><span>合规 ${b.comp?.score ?? '—'}</span></div>
    ${b.stale?'<p class="ws-error">正文已修改，以下为上次评审意见。请复审验证修改结果。</p>':!b.bound && (b.tech||b.comp)?'<p class="ws-muted">历史评审未绑定正文版本，需复审核对。</p>':''}
    ${b.tech?.error||b.comp?.error?`<p class="ws-error">${esc(b.tech?.error||b.comp?.error)}</p>`:''}
    <h3>对应应标要求</h3><details><summary>展开要求与评分依据</summary><pre>${esc(b.requirement || '暂无应标要求')}\n${esc(b.score_items || '')}\n${esc(b.evidence_required || '')}</pre></details>
    ${b.issues.map(i=>`<article class="quality-finding"><small>${esc(i.agent)} · ${esc(({critical:'阻断',high:'重要',medium:'一般',low:'建议'})[i.severity] || i.severity)}</small><p><strong>${esc(i.point)}</strong></p><p>${esc(i.suggestion || '请对照原始要求核对。')}</p>${i.needs_material?`<p>需补充：${esc(i.material_query || i.point)}</p><a href="${esc(workspaceUrl('matching',b.block_id))}">补充素材 →</a>`:''}</article>`).join('') || `<p class="ws-muted">${b.tech && b.comp ? '本次评审未列出具体问题，请结合分数与原文核对。' : '尚无完整双视角评审。'}</p>`}
    <button class="ws-button" id="quality-select-current">选择本章处理</button>`;
  $('quality-select-current').onclick = () => { selected.add(b.block_id); renderQueue();refreshActions(); };
}
subscribe(() => {
  if (!activeId || !workspace.chapters.some(b=>b.block_id===activeId)) activeId = workspace.chapters.find(b=>attention.has(b.status))?.block_id || workspace.chapters[0]?.block_id;
  for (const id of selected) if (!workspace.chapters.some(b=>b.block_id===id)) selected.delete(id);
  renderSummary();renderQueue();renderDetail();refreshActions();
});
$('quality-list').addEventListener('click',e=> {
  const button = e.target.closest('[data-chapter]');
  if (!button) return;
  activeId = button.dataset.chapter;
  const url = new URL(location.href);url.searchParams.set('blockId',activeId);history.replaceState(null,'',url);
  renderQueue();renderDetail();
});
$('quality-list').addEventListener('change',e=> {
  const id = e.target.dataset.select; if (!id) return;
  if (e.target.checked) selected.add(id); else selected.delete(id);
  refreshActions();
});
$('quality-search').oninput = $('quality-filter').onchange = () => {visibleLimit=100;renderQueue();};
$('quality-more').onclick = () => {visibleLimit+=100;renderQueue();};
$('quality-select-visible').onclick = () => {filtered().forEach(b=>selected.add(b.block_id));renderQueue();refreshActions();};
$('quality-clear').onclick = () => {selected.clear();renderQueue();refreshActions();};
$('quality-revise').onclick = () => confirmChapterAction([...selected],'revise');
$('quality-recheck').onclick = () => confirmChapterAction([...selected],'review');
$('quality-history').onclick = () => showHistory(workspace.chapters.find(b=>b.block_id===activeId)?.db_id);
for (const button of document.querySelectorAll('[data-doc-mode]')) button.onclick=()=>{mode=button.dataset.docMode;renderDetail();};
$('quality-export').onclick = () => {
  openDialog('导出当前正文', `<p>导出当前已保存正文。导出不代表评审通过；仍有问题的章节可继续修订。</p><a class="ws-button" href="/api/projects/${workspace.projectId}/export">下载 Word</a>`);
};
$('model-selector-btn').onclick = async () => {
  const dialog = openDialog('模型配置','<p>加载中…</p>');
  try {
    const data = await request('/api/configs');
    dialog.querySelector('.ws-dialog-body').innerHTML = `<p>工作流使用服务器配置的模型池。</p>${(data.configs || []).map(c=>`<p>${esc(c.name || c.model)}</p>`).join('') || '<p>尚未配置模型。</p>'}<p><a href="${esc(workspaceUrl('editor'))}">在方案编辑页管理 API 配置</a></p>`;
  } catch(e) {dialog.querySelector('.ws-dialog-body').textContent=e.message;}
};

$('quality-approve').onclick = () => {
  const s=summarize(workspace.chapters);
  const dialog=openDialog('确认本轮完成',`<p>正文 ${s.written}/${s.total}，待处理 ${s.needsAction} 章。请核对后确认；后续仍可继续局部修订。</p><p role="status"></p><footer><button data-confirm>确认结束本轮</button></footer>`);
  dialog.querySelector('[data-confirm]').onclick=async e=>{
    e.target.disabled=true;
    try {
      await request(`/api/workflow/${workspace.threadId}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_choice:'approve',edits:{}})});
      dialog.close();await refreshWorkspace();
    }catch(error){dialog.querySelector('[role="status"]').textContent=error.message;e.target.disabled=false;}
  };
};
