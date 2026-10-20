import {mountLiveWriting} from './chapter-live.mjs?v=20261016-v323';
import {mountChapterTeam,canDisplayResult} from './chapter-team.mjs?v=20261016-v323';
import {specContentHtml} from './spec-content.mjs?v=20261016-v323';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = async (url, options = {}) => {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试');
  return data;
};
const send = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

export function previewContent(value) {
  const lines = String(value || '').split('\n');
  const html = [];
  let text = [];
  const flush = () => { if (text.length) { html.push(specContentHtml(text.join('\n'))); text = []; } };
  for (let index = 0; index < lines.length; index++) {
    const image = lines[index].trim().match(/^!\[([^\]]*)\]\((\/api\/studio\/materials\/\d+\/image)\)$/);
    const heading = lines[index].match(/^(#{1,6})\s+(.+)$/);
    if (lines[index].startsWith(':::diagram ')) {
      flush();
      const title = lines[index].slice(11);
      const body = [];
      while (++index < lines.length && lines[index].trim() !== ':::') body.push(lines[index]);
      const nodes = body.join('\n').replaceAll('→','->').split('->').map(n=>n.trim()).filter(Boolean);
      html.push(`<figure class="cs-diagram">${nodes.map((n,i)=>`${i ? '<span aria-hidden="true">↓</span>' : ''}<div>${esc(n)}</div>`).join('')}<figcaption>${esc(title)}</figcaption></figure>`);
    } else if (image) {
      flush(); html.push(`<figure><img src="${esc(image[2])}" alt="${esc(image[1])}" loading="lazy"><figcaption>${esc(image[1])}</figcaption></figure>`);
    } else if (heading) {
      flush(); html.push(`<h${Math.min(heading[1].length + 1,6)}>${esc(heading[2])}</h${Math.min(heading[1].length + 1,6)}>`);
    } else text.push(lines[index]);
  }
  flush();
  return html.join('') || '<p class="cs-muted">暂无内容</p>';
}

export function chapterDisplayState(preview,live){
  return {editorHidden:preview||live,liveHidden:preview||!live,previewHidden:!preview};
}

export function localDraftAction(local,serverContent){
  if(!local||local.content===serverContent)return 'none';
  return local.base===serverContent?'restore':'conflict';
}

export function exportScope(blocks, activeId, scope) {
  if (scope === 'document') return blocks;
  const start = blocks.findIndex(b => String(b.id) === String(activeId));
  if (start < 0) return [];
  let end = start + 1;
  if (scope === 'group') while (end < blocks.length && Number(blocks[end].level) > Number(blocks[start].level)) end++;
  return blocks.slice(start,end);
}

export async function exportDocument({projectId, threadId, template='standard', scope='document', blockId, slot}) {
  const job = await json(`/api/projects/${projectId}/studio/exports`, send('POST', {
    thread_id:threadId, template, scope, block_id:blockId || null,
  }));
  if (slot) {
    slot.innerHTML = `<span>已排队${job.empty_chapters ? ` · ${job.empty_chapters} 个空节点将保留为空` : ''}。使用已保存版本，离开页面后继续。</span>`;
    await monitorExport(projectId, job.id, slot);
  }
  return job;
}

async function monitorExport(projectId, id, slot) {
  // 单次等待短，页面移除后停止轮询，任务仍在服务器执行。
  while (slot.isConnected) {
    let job;
    try { job = await json(`/api/projects/${projectId}/studio/exports/${id}`); }
    catch (error) { slot.textContent = `任务 ${id.slice(0,8)}：${error.message}，可刷新查看进度`; return; }
    if (job.status === 'done') {
      slot.innerHTML = `<a href="/api/projects/${projectId}/studio/exports/${id}/file">下载 Word 草稿</a><span class="cs-muted"> · 固定版本 ${id.slice(0,8)}</span>`;
      if(job.preview_status==='done') {
        slot.innerHTML+=` · <a href="/api/projects/${projectId}/studio/exports/${id}/preview" target="_blank" rel="noopener">查看导出排版 PDF</a>`;
        return;
      }
      if(job.preview_status==='failed') {
        slot.innerHTML+=`<p>Word 已完成，排版预览失败：${esc(job.preview_error)}</p><button type="button">重试排版预览</button>`;
        slot.querySelector('button').onclick=async()=>{
          try {await json(`/api/projects/${projectId}/studio/exports/${id}/preview`,{method:'POST'});await monitorExport(projectId,id,slot);}
          catch(error){slot.textContent=error.message;}
        };
        return;
      }
      slot.innerHTML+='<p class="cs-muted">Word 已生成，正在进行实际排版预览，可先下载。</p>';
    } else {
    if (job.status === 'failed') {
      slot.innerHTML = `<span>导出失败：${esc(job.error)}</span> <button type="button">重试同一版本</button>`;
      slot.querySelector('button').onclick = async () => {
        try { await json(`/api/projects/${projectId}/studio/exports/${id}/retry`, {method:'POST'}); await monitorExport(projectId,id,slot); }
        catch (error) { slot.textContent = error.message; }
      };
      return;
    }
    slot.textContent = `${job.status === 'queued' ? '排队中' : '正在渲染并合并图文表'} · ${job.progress}% · 任务 ${id.slice(0,8)}，离开后继续`;
    }
    await new Promise(resolve=>setTimeout(resolve,1800));
  }
}

let currentStudio;
export async function mountChapterStudio(host, {projectId, threadId, onStats, getModel}) {
  if (currentStudio?.host === host && host.querySelector('.cs-editor')) return currentStudio;
  currentStudio?.dispose();
  host.innerHTML = '<p class="cs-muted">正在加载章节编制工作台…</p>';
  const [blocks, document] = await Promise.all([
    json(`/api/projects/${projectId}/blocks?threadId=${encodeURIComponent(threadId)}`),
    json(`/api/projects/${projectId}/studio/document?threadId=${encodeURIComponent(threadId)}`),
  ]);
  if (!host.isConnected) return null;
  let active, context, dirty = false, selecting = 0, disposed = false;
  const units = new Set(document.unit_ids);
  const profile = document.profiles || {};
  const draftKey = b => `chapter-draft:${threadId}:${b.id}`;
  host.innerHTML = `
    <div class="cs-document-head"><div hidden><strong>章节编写</strong><span data-cs="progress"></span></div>
      <div class="cs-document" hidden><select data-cs="template">${Object.entries(document.templates).map(([key,tt])=>`<option value="${key}" ${key===document.template?'selected':''}>${esc(tt.label)}</option>`).join('')}</select><span class="cs-muted" data-cs="format-summary"></span><a data-cs="format-sample" target="_blank" rel="noopener"></a></div>
    </div>
</details></div>
    <div class="cs-workspace">
      <aside class="cs-tree"><div class="cs-tree-label">应答目录</div><input type="search" data-cs="search" placeholder="搜索章节" aria-label="搜索章节"><div data-cs="tree"></div></aside>
      <main class="cs-main">
        <div class="cs-chapter-heading" data-cs="chapter-head"></div>
        <div class="cs-toolbar">
          <button data-action="ai" class="cs-primary" title="启动本章编制；在右侧查看进度">AI 编制本章</button>
          <button data-action="save" disabled>保存</button>
          <div class="cs-view-switch" role="group" aria-label="正文显示方式"><button data-action="edit" aria-pressed="true">编辑</button><button data-action="preview" aria-pressed="false">预览</button></div>
          <div class="cs-toolbar-right">
            <button type="button" class="cs-insert-btn" data-open-insert title="从官方来源 / 素材 / 结构模板插入内容">插入…</button>
            <details class="cs-more"><summary>更多 ▾</summary><div class="cs-more-menu">
              <button data-action="complete">确认本章完成</button>
              <div class="cs-more-sep"></div>
              <button data-action="export-chapter">导出本章 Word</button>
              <button data-action="export-group">导出本组 Word</button>
            </div></details>
          </div>
        </div>
        <div data-cs="status" role="status" aria-live="polite"></div>
        <div data-cs="recovery" hidden></div>
        <textarea class="cs-editor" data-cs="editor" aria-label="本章正文" placeholder="点击上方「AI 编制」生成本章内容，也可直接在此编写。生成结果将显示在这里；核对后点击「保存正文」。"></textarea>
        <div class="cs-live-writing" data-cs="live" hidden></div>
        <div class="cs-preview" data-cs="preview" hidden></div>
        <dialog class="cs-completion" data-cs="completion"><div data-cs="completion-summary"></div><div class="cs-completion-actions"><button data-action="completion-cancel">继续编辑</button><button data-action="completion-confirm">已核对，确认本章完成</button></div></dialog><div data-cs="export-status" role="status"></div>
        <details class="cs-history"><summary>最近导出任务</summary><div data-cs="jobs"></div></details>
      </main>
      <aside class="cs-aside cs-inspector" data-cs="inspector" data-panel="todo"><nav class="cs-inspector-tabs" aria-label="本章助手"><button data-panel="todo" aria-pressed="true">待办<span class="cs-tab-badge" data-todo-badge hidden></span></button><button data-panel="materials" aria-pressed="false">素材</button><button data-panel="ai" aria-pressed="false">进度</button></nav><div data-cs="aside"></div><div class="cs-team" data-cs="team"></div></aside>
    </div>`;
  const get = key => host.querySelector(`[data-cs="${key}"]`);
  const editor = get('editor');
  const showPanel = panel => {
    get('inspector').dataset.panel=panel;
    for(const button of host.querySelectorAll('.cs-inspector-tabs button'))button.setAttribute('aria-pressed',String(button.dataset.panel===panel));
    if(window.innerWidth<=980)get('inspector').scrollIntoView({behavior:'smooth',block:'start'});
  };
  let statusTimer = null;
  const status = message => {
    const el = get('status');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('is-error', /失败|错误|Cannot|null|异常/.test(String(message)));
    if (!message) { el.hidden = true; return; }
    el.hidden = false;
    el.classList.add('is-on');
    globalThis.clearTimeout(statusTimer);
    statusTimer = globalThis.setTimeout(() => { el.classList.remove('is-on'); el.hidden = true; }, el.classList.contains('is-error') ? 8000 : 4000);
  };
  const stats = () => {
    const written = blocks.filter(b=>units.has(b.id) && b.content?.trim()).length;
    const complete = blocks.filter(b=>units.has(b.id) && profile[b.id]?.writing_status==='complete').length;
    get('progress').textContent = `${units.size} 个编写单元 · 草稿 ${written} · 完成 ${complete}`;
    onStats?.({written, total:units.size, complete});
  };
  // ── 篇幅（2026-10-16）：预算页数来自 /page-budget，已写字数来自正文长度 ──
  // 原型要求：目录行只给颜色（状态）与行底进度（预算），名称必须完整。
  // ── 编写计划（P0）：单位 = 技术评分点的一级目录（评分根的直接子目录）──
  // 只在这一层出「编写计划」入口（一页 7 次确认，而不是每节一次）。
  const specInfo = { specs: {} };    // 编写规范（L0/L1）版本，右栏显示 + 就近打开
  const citationInfo = { rows: [] }; // 已接受的官方来源（本章 / 项目级）
  let basisInfo = null;
  let planBrief = null;      // 本章计划摘要（待办数），用于章头按钮角标

  /** 依据 ↔ 正文对应 + 本章计划摘要（待办数）。 */
  async function loadBasis(blockKey) {
    if (!blockKey) { basisInfo = null; planBrief = null; return; }
    try {
      const [basisResp, planResp] = await Promise.all([
        fetch(`/api/projects/${projectId}/basis?threadId=${encodeURIComponent(threadId)}&blockId=${encodeURIComponent(blockKey)}`, {signal: AbortSignal.timeout(20000)}),
        fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockKey)}/plan?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(30000)}),
      ]);
      basisInfo = basisResp.ok ? await basisResp.json() : null;
      if (planResp.ok) {
        const plan = await planResp.json();
        planBrief = {
          todo: plan.todo || [],
          gaps: plan.material_gaps || [],
          pages: plan.pages || 0,
          wordPlan: plan.word_plan || null,
          deliverables: plan.deliverables || [],
        };
      } else { planBrief = null; }
    } catch (e) { basisInfo = null; planBrief = null; }
  }

  async function loadCitations() {
    try {
      const resp = await fetch(`/api/projects/${projectId}/citations?status=accepted`, {signal: AbortSignal.timeout(12000)});
      if (!resp.ok) return;
      const data = await resp.json();
      citationInfo.rows = data.citations || [];
      if (active) paintAside();
    } catch (e) { /* 引用拿不到不影响编写 */ }
  }
  async function loadSpecs() {
    try {
      const resp = await fetch('/api/writing-specs', {signal: AbortSignal.timeout(12000)});
      if (!resp.ok) return;
      const data = await resp.json();
      specInfo.specs = data.specs || {};
      if (active) paintAside();
    } catch (e) { /* 规范拿不到不影响编写 */ }
  }
  const planUnits = new Map();      // block_id -> {title, leaves}
  const unitOf = new Map();         // 叶/任意子孙 block_id -> 所属一级目录 block_id
  async function loadPlanUnits() {
    if (!threadId) return;
    try {
      const resp = await fetch(`/api/projects/${projectId}/writing-plan-units?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(15000)});
      if (!resp.ok) return;
      const data = await resp.json();
      planUnits.clear(); unitOf.clear();
      (data.units || []).forEach((unit) => {
        planUnits.set(String(unit.block_id), { title: unit.title, leaves: unit.leaves, plan: unit.plan || null });
        (blocks || []).forEach((b) => {
          const key = String(b.block_id || b.id);
          if (key === String(unit.block_id) || key.startsWith(`${unit.block_id}.`)) unitOf.set(key, String(unit.block_id));
        });
      });
      paintTree();
      try { if (active) paintPlanAction(active); } catch (e) { /* active 尚未初始化 */ }
    } catch (e) { /* 计划单元拿不到不影响编写 */ }
  }
  const WPP = 250;                    // 1 页 ≈ 250 字（与策划页分配口径一致）
  const pageBudget = { byBlock: {}, loaded: false };
  const budgetOf = b => pageBudget.byBlock[String(b.block_id || b.id)] || null;
  const wordsOf = b => String(b.content || '').replace(/\s+/g, '').length;
  const pagesOf = b => wordsOf(b) / WPP;
  // 编写规范（L0/L1）的显示名：模块作用域 —— 渲染模板与抽屉都会用，别放进函数里
  const RECIPE_LABELS = {understanding: '项目理解', solution: '技术方案', implementation: '实施计划',
                         evidence: '人员／业绩', point_to_point: '点对点应答', global: '全局纪律'};
  const recipeLabel = (key) => RECIPE_LABELS[key] || key;

  /** 编写计划抽屉：先看计划（每节的表/图/素材缺口），确认后一次起 job。 */
  /** 质检清单：结构完整性 / 图表数量 / 占位与空词 / 篇幅偏差（确定性检查，不阻断保存）。 */
  /** 质检清单（分组版）：规范 / 计划 / 依据 / 素材 四组，每条带一键动作。 */
  /** 绑定/编辑后刷新计划抽屉里的要求块（只重绘该章，避免整表跳动）。 */
  async function refreshPlanRequirements(host, block) {
    const blockId = host.dataset.planChapter;
    try {
      const [coverageResp, needsResp] = await Promise.all([
        fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(planIdOf(host))}/coverage?threadId=${encodeURIComponent(threadId)}`).catch(() => null),
        fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(planIdOf(host))}/needs?threadId=${encodeURIComponent(threadId)}`).catch(() => null),
      ]);
      const coverage = coverageResp?.ok ? await coverageResp.json() : null;
      const needsData = needsResp?.ok ? await needsResp.json() : null;
      const cov = (coverage?.chapters || []).find((c) => String(c.block_id) === String(blockId));
      const needs = (needsData?.chapters || []).find((c) => String(c.block_id) === String(blockId))?.needs || [];
      block.querySelectorAll('[data-req-row]').forEach((row, index) => {
        const status = row.querySelector('em');
        if (status && cov) status.textContent = cov.requirements?.[index]?.responded ? '已回应' : '未回应';
        if (status) status.classList.toggle('ok', !!cov?.requirements?.[index]?.responded);
        const needButton = row.querySelector('[data-req-need]');
        if (needButton) {
          const count = needs[index]?.snippets || 0;
          needButton.textContent = count ? `素材 ${count}` : '未补素材';
          needButton.classList.toggle('ok', !!count);
        }
      });
    } catch (e) { /* 刷新失败保持原样 */ }
  }

  function planIdOf(host) {
    return globalThis.document.querySelector('[data-req-block]')?.dataset.planId || host?.dataset?.planId || '';
  }

  async function openQualityDialog() {
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog';
    dialog.innerHTML = `<header><h3>质检汇总（按章处理请用「本章编写计划」）</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">正在体检…</p></div>`;
    globalThis.document.body.append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    const body = dialog.querySelector('.cs-plan-body');
    const ACTION_LABEL = {find_material: '去补素材', regenerate: '重新生成', open_spec: '打开规范', open_budget: '去看篇幅'};
    const render = (data) => {
      const started = (data.chapters || []).filter((row) => row.started);
      const groups = ['规范', '计划', '依据', '素材'];
      const groupCounts = data.summary?.groups || {};
      const issuesOf = (group) => started.flatMap((row) => (row.issues || [])
        .filter((issue) => (issue.group || '规范') === group)
        .map((issue) => ({...issue, block_id: row.block_id, title: row.title})));
      const groupHtml = groups.map((group) => {
        const items = issuesOf(group);
        if (!items.length) return '';
        return `<section class="cs-qc-group"><h4>${group} <em>${groupCounts[group] ?? items.length}</em></h4>
          ${items.map((issue) => `<div class="cs-qc-issue ${esc(issue.level)}" data-finding="${esc(issue.finding_id || '')}" data-block="${esc(String(issue.block_id))}">
              <div class="cs-qc-issue-main"><b>${esc(issue.check || '')}</b>
                <span>${esc(issue.message)}</span>
                <small>${esc(String(issue.title || '').slice(0, 16))}</small></div>
              <div class="cs-qc-issue-acts">
                ${(issue.actions || []).map((action) => `<button type="button" class="mw-btn" data-qc-action="${esc(action)}" data-block="${esc(String(issue.block_id))}" data-finding="${esc(issue.finding_id || '')}">${ACTION_LABEL[action] || action}</button>`).join('')}
                ${issue.finding_id ? `<button type="button" class="mw-btn" data-qc-ignore="${esc(issue.finding_id)}">标为已处理</button>` : ''}
              </div>
            </div>`).join('')}</section>`;
      }).join('');
      body.innerHTML = `<p class="cs-muted">待修 ${data.summary.high + data.summary.medium} 条（高优 ${data.summary.high} · 中优 ${data.summary.medium}）
        · 已开始 ${data.summary.started} 章 · 通过 ${data.summary.clean} 章${data.summary.ignored ? ` · 已忽略 ${data.summary.ignored}` : ''}
        <br><span class="cs-dim">按"规范 / 计划 / 依据 / 素材"四组；每条可一键处理，不阻断保存。</span></p>
        ${groupHtml || '<p class="cs-dim">没有待修问题。</p>'}`;
      body.querySelectorAll('[data-qc-ignore]').forEach((button) => button.addEventListener('click', async () => {
        try {
          const resp = await fetch(`/api/projects/${projectId}/quality/findings/${encodeURIComponent(button.dataset.qcIgnore)}/ignore`, {method: 'POST', signal: AbortSignal.timeout(20000)});
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          button.closest('.cs-qc-issue')?.remove();
          status('已标记为已处理');
        } catch (err) {
          status(`标记失败：${String(err.message || err)}`);
        }
      }));
      body.querySelectorAll('[data-qc-action]').forEach((button) => button.addEventListener('click', async () => {
        const action = button.dataset.qcAction;
        const blockKey = button.dataset.block;
        if (action === 'open_spec') {
          globalThis.document.dispatchEvent(new CustomEvent('writing-specs:open', {detail: {layout: active?.layout || 'global'}}));
          return;
        }
        if (action === 'open_budget') {
          dialog.close();
          globalThis.document.querySelector('.sr-rail-item[data-stage="2"]')?.click();
          return;
        }
        if (action === 'regenerate') {
          dialog.close();
          await select(blockKey);
          status('已定位到本章：点「AI 编制本章」重新生成');
          return;
        }
        // find_material：打开官方来源抽屉（带当前章的上下文）
        dialog.close();
        await select(blockKey);
        openCitationDialog(blockKey);
      }));
    };
    try {
      const resp = await fetch(`/api/projects/${projectId}/quality?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(60000)});
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      render(data);
    } catch (err) {
      body.innerHTML = `<p class="cs-warning">质检失败：${esc(String(err.message || err))}</p>`;
    }
  }

  /** 本章编写计划（可执行版）：交付物 / 对内要求 / 字数分配 / 素材清单 / 怎么写 / 自检。
   *  父章额外给"子节统筹"。 */
  async function openChapterPlan(blockId) {
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog cs-cp-dialog';
    dialog.innerHTML = `<header><h3>本章计划</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">正在加载本章计划…</p></div>`;
    globalThis.document.body.append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    const body = dialog.querySelector('.cs-plan-body');
    let data = null;
    const kindLabel = {text: '写作', table: '产出·表', figure: '产出·图', material: '素材'};
    const materialTone = (status) => status === '已满足' ? 'ok' : status === '已补不足' ? 'warn' : 'bad';
    const load = async () => {
      const resp = await fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockId)}/plan?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(30000)});
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(payload.detail || `HTTP ${resp.status}`);
      return payload;
    };
    const openBindPicker = (requirement) => {
      if (!data.plan_id) { status('本章还没有计划，无法绑定素材'); return; }
      const linked = citationInfo.rows.filter((c) => (c.links || []).includes(String(blockId)) || !c.block_id);
      const segRows = linked.map((citation) => (citation.segments || []).map((segment, index) => ({citation, segment, index}))).flat().slice(0, 60);
      const picker = globalThis.document.createElement('dialog');
      picker.className = 'cs-plan-dialog';
      picker.innerHTML = `<header><h3>给「${esc(String(requirement.text).slice(0, 16))}」补充素材</h3><button type="button" aria-label="关闭">×</button></header>
        <div class="cs-plan-body"><p class="cs-muted">从本章已接受的来源里勾选可用片段，绑定到这条要求。</p>
          ${segRows.length ? segRows.map(({citation, segment, index}) => `<label class="cs-seg-row">
              <input type="checkbox" data-cp-bind-citation="${esc(citation.id)}" data-cp-bind-index="${index}" />
              <div><span class="cs-seg-kind ${esc(segment.kind)}">${{text:'段落',table:'表格',image:'图片'}[segment.kind] || '内容'}</span>
                <b>${esc(String(citation.title || '').slice(0, 22))}</b>
                <p>${esc(String(segment.content || '').slice(0, 160))}</p></div></label>`).join('')
            : '<p class="cs-warning">本章还没有可用的来源片段：先在「素材池」添加官方来源并提取片段。</p>'}
          <div class="cs-plan-actions"><button type="button" class="mw-btn" data-cp-bind-cancel>取消</button>
            ${segRows.length ? '<button type="button" class="mw-btn primary" data-cp-bind-save>绑定</button>' : ''}</div>
          <p class="cs-dim" data-cp-bind-msg></p></div>`;
      globalThis.document.body.append(picker);
      picker.querySelector('header button').onclick = () => picker.close();
      picker.querySelector('[data-cp-bind-cancel]').onclick = () => picker.close();
      picker.addEventListener('close', () => picker.remove());
      picker.querySelector('[data-cp-bind-save]')?.addEventListener('click', async () => {
        const checked = [...picker.querySelectorAll('[data-cp-bind-citation]:checked')];
        if (!checked.length) { picker.querySelector('[data-cp-bind-msg]').textContent = '先勾选至少一个片段'; return; }
        try {
          for (const input of checked) {
            await fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(data.plan_id)}/needs/bind`, {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({block_id: blockId, requirement_id: String(requirement.id),
                                    citation_id: input.dataset.cpBindCitation,
                                    indices: [Number(input.dataset.cpBindIndex)]}),
              signal: AbortSignal.timeout(30000),
            });
          }
          picker.close();
          status(`已绑定 ${checked.length} 个片段`);
          data = await load();
          await render();
        } catch (err) { picker.querySelector('[data-cp-bind-msg]').textContent = `绑定失败：${String(err.message || err)}`; }
      });
      picker.showModal();
    };
    const render = async () => {
      const wordPlan = data.word_plan || {total: 0, written: 0, percent: 0};
      const deliverables = data.deliverables || [];
      const todo = data.todo || [];
      const clauses = data.clauses || [];
      const points = data.basis?.points || [];
      const children = data.children || [];
      const guidance = data.guidance || {};
      const pages = data.pages || 0;
      const keyPointsBy = data.key_points_by_deliverable || {};
      const isCatalog = !!data.catalog;
      const isParent = children.length > 0 && !isCatalog;
      const todoCount = todo.length;
      const doneCount = deliverables.filter((item) => item.status === '达标').length;
      const unmetCount = deliverables.filter((item) => item.status === '不足' || item.status === '超额').length;
      const unwrittenCount = deliverables.filter((item) => item.status === '未写').length;
      // 只展开"第一项未达标"的交付物
      const firstUnmet = deliverables.findIndex((item) => item.status !== '达标');
      const mainAction = isCatalog ? '看子节进度' : todoCount ? `去补 ${todoCount} 项` : '去生成正文';
      const headline = isCatalog
        ? `目录节点 · 下辖 ${children.length} 节（计划 ${children.reduce((sum, c) => sum + (c.pages || 0), 0)} 页 · 已写 ${children.reduce((sum, c) => sum + (c.written_pages || 0), 0)} 页）`
        : isParent
          ? `总述 ${wordPlan.written || 0} / ${wordPlan.total || '—'} 字 ｜ 子节 ${children.length} 个（计划 ${children.reduce((sum, c) => sum + (c.pages || 0), 0)} 页 · 已写 ${children.reduce((sum, c) => sum + (c.written_pages || 0), 0)} 页）`
          : `计划 ${pages || '—'} 页 · ${wordPlan.total || '—'} 字 ｜ 已写 ${wordPlan.written || 0} 字 ｜ 待办见右栏`;
      const detected = deliverables.reduce((sum, item) => sum + (item.written_words || 0), 0);
      const wordTooltip = `全章已写 ${wordPlan.written || 0} 字；能按小节标题对应到交付物的是 ${detected} 字，逐项"已写"为估算值，以正文为准`;
      body.innerHTML = `
        <div class="cs-cp-head">
          <h4>${esc(data.title || '')}${isCatalog ? '<span class="cs-cp-tag">目录节点</span>' : isParent ? '<span class="cs-cp-tag">父章 · 只写总述</span>' : ''}</h4>
          <div class="cs-cp-toprow">
            <p class="cs-cp-facts">${esc(headline)}${isCatalog ? '' : `<span class="cs-cp-info" title="${esc(wordTooltip)}">i</span>`}</p>
            <button type="button" class="cs-cp-primary" data-cp-main>AI 编制本章</button>
          </div>
          ${wordPlan.advice ? `<p class="cs-cp-warn-line">${esc(wordPlan.advice)}</p>` : ''}
        </div>



        ${isCatalog ? '' : `<details class="cs-cp-block" data-cp-sec="deliverables" open>
          <summary><b><span class="cs-cp-idx">1</span>交付物</b><em>${[...(unmetCount ? [`${unmetCount} 项不足`] : []), ...(unwrittenCount ? [`${unwrittenCount} 项未写`] : []), ...(doneCount ? [`${doneCount} 项达标`] : [])].join(' · ') || '—'}</em>
            <button type="button" class="cs-cp-link" data-cp-edit-req>改要求</button></summary>
          ${deliverables.length ? deliverables.map((item, index) => {
            const open = index === firstUnmet;
            return `<div class="cs-cp-del tone-${item.status === '达标' ? 'ok' : item.status === '未写' ? 'idle' : 'warn'}" data-index="${index}" ${open ? '' : 'data-collapsed'}>
                <div class="cs-cp-del-head">
                  <span class="cs-cp-tri">${open ? '▾' : '▸'}</span>
                  <span class="cs-cp-kind">${esc(item.kind_label)}</span>
                  <b>${esc(item.title)}</b>
                  <span class="cs-cp-del-num">${item.target_words ? `${item.target_words} 字 · ` : ''}${item.written_detected === false ? '未检测到段落' : `已写 ${item.written_words || 0}`}</span>
                  <span class="cs-cp-del-status">${esc(item.status)}</span>
                </div>
                <div class="cs-cp-del-body" ${open ? '' : 'hidden'}>
                  ${(keyPointsBy[index] || []).length ? `<p class="cs-cp-brief">必答：${esc((keyPointsBy[index] || []).join(' / '))}</p>` : ''}
                  <p class="cs-cp-del-acts"><button type="button" class="cs-cp-link" data-cp-locate="${index}">定位到正文</button></p>
                </div>
              </div>`;
          }).join('') : '<p class="cs-cp-empty">本章还没有交付物</p>'}
        </details>`}

        <details class="cs-cp-block" data-cp-sec="standard">
          <summary><b><span class="cs-cp-idx">2</span>依据与标准</b><em>${points.length ? `评分点 ${points.length}` : ''}${clauses.length ? ` · 招标条款 ${clauses.length}` : ''}${(guidance.pitfalls || []).length ? ` · 失分点 ${(guidance.pitfalls || []).length}` : ''}</em></summary>
          <div class="cs-cp-fold-body">
            ${points.map((point) => `<p class="cs-cp-point"><b>${esc(point.label)}（${point.points ?? '—'} 分）</b></p>`).join('')}
            ${data.grade_band ? `<details class="cs-cp-sub"><summary>满分档要求（得 8–10 分的要求）</summary>
              <p class="cs-cp-band">${esc(data.grade_band)}</p></details>` : ''}
            ${clauses.length ? `<details class="cs-cp-sub"><summary>本章承接的招标条款 ${clauses.length} 条</summary>
                <div class="cs-cp-band" style="padding:8px 12px">
                ${clauses.map((clause) => `<p class="cs-cp-clause">${clause.mandatory ? '<span class="cs-cp-must">强制</span>' : ''}${esc(clause.title)}</p>`).join('')}
                <button type="button" class="cs-cp-link" data-cp-match-clauses>重新用 AI 匹配</button></div></details>`
              : `<p class="cs-cp-empty">本章只对评分点负责（无对应招标条款）
                  <button type="button" class="cs-cp-link" data-cp-match-clauses>用 AI 匹配条款</button></p>`}
            ${(guidance.pitfalls || []).length ? `<p class="cs-cp-pitfall">失分点：${esc(guidance.pitfalls.join('；'))}</p>` : ''}
          </div>
        </details>

        <details class="cs-cp-block" data-cp-sec="how">
          <summary><b><span class="cs-cp-idx">3</span>写法要点</b><em>3 条（结构 / 段落 / 落点）</em>
            <button type="button" class="cs-cp-link" data-cp-open-spec>看写作规范（全局）</button></summary>
          <div class="cs-cp-fold-body cs-cp-write">
            ${(guidance.skeleton || []).length ? `<p><b>结构</b>：${esc(guidance.skeleton.join(' → '))}</p>` : ''}
            <p><b>段落</b>：每段第一句给判断，后面给依据与数据；表后必跟一段"从表可见…"的落点句。</p>
            <p><b>落点</b>：每个必答点至少一段；引用政策/标准/数据处必须带【来源】。</p>
            <label class="cs-cp-kind-row">本章类型
              <select data-cp-layout>${Object.entries(context?.layouts || {}).map(([id, l]) => `<option value="${id}" ${id === ((context?.profile || {}).layout || '') ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
              <span class="cs-dim">决定本章按哪类章节编制（影响提示词与结构）</span>
            </label>
          </div>
        </details>

        ${(isParent || isCatalog) ? `<details class="cs-cp-block" data-cp-sec="children" open>
          <summary><b><span class="cs-cp-idx">4</span>子节统筹</b><em>${children.length} 节 · 共 ${children.reduce((sum, c) => sum + (c.pages || 0), 0)} 页</em></summary>
          ${isParent ? '<p class="cs-cp-howto">本章只写总述（300–500 字）：先全貌 → 3 个关键结论 → 子节索引，细节见下列各节。</p>' : ''}
          ${children.map((child) => `<div class="cs-cp-kid" data-cp-kid="${esc(child.block_id)}" role="button" tabindex="0">
              <span class="cs-cp-kid-title">${esc(child.title)}</span>
              <span class="cs-cp-kid-bar"><i style="width:${child.pages ? Math.min(100, Math.round((child.written_pages || 0) / child.pages * 100)) : 0}%"></i></span>
              <small>${child.written_pages || 0} / ${child.pages || '—'} 页</small>
              <em class="${child.has_content ? 'ok' : ''}">${child.has_content ? '有正文' : '未开始'}</em>
            </div>`).join('')}
        </details>` : ''}
        <div class="cs-cp-footer">
          <button type="button" class="cs-cp-ghost" data-cp-close>关闭</button>
          <button type="button" class="cs-cp-primary" data-cp-generate>AI 编制本章</button>
        </div>
        <div data-cp-req-edit hidden></div>`;
      bindEvents();
    };
    const runAction = (action) => {
      if (action === 'find') { dialog.close(); openCitationDialog(blockId); return; }
      dialog.close();
      void select(blockId);
      globalThis.setTimeout(() => host.querySelector('[data-action="ai"]')?.click(), 900);
    };
    const locateInEditor = (titleHint) => {
      const text = String(editor.value || '');
      const keys = String(titleHint || '').replace(/[「」]/g, '').split(/[\s、，,；;（）()]+/).filter((part) => part.length >= 2);
      let index = -1;
      for (const key of keys) {
        index = text.indexOf(key);
        if (index >= 0) break;
      }
      if (index < 0) { status('已定位到本章（未找到对应小节标题，请按标题手动查找）'); return; }
      const lineStart = text.lastIndexOf('\n', index) + 1;
      const lineEnd = text.indexOf('\n', index) === -1 ? text.length : text.indexOf('\n', index);
      editor.focus();
      editor.setSelectionRange(lineStart, lineEnd);
      const ratio = lineStart / Math.max(1, text.length);
      editor.scrollTop = Math.max(0, Math.round(editor.scrollHeight * ratio) - 60);
      status('已定位到对应小节');
    };
    const bindEvents = () => {
      body.querySelector('[data-cp-main]')?.addEventListener('click', () => {
        // 计划面板不再承载「去补待办」：待办在右栏「待办」页签
        if (data.catalog || (data.children || []).length) {
          body.querySelector('[data-cp-sec="children"]')?.scrollIntoView({behavior: 'smooth', block: 'start'});
          return;
        }
        runAction('regen');
      });
      body.querySelectorAll('[data-cp-todo]').forEach((button) => button.addEventListener('click', () => {
        runAction(button.dataset.cpTodo);
      }));
      // 交付物：同时只展开一项
      body.querySelectorAll('.cs-cp-del').forEach((row) => row.addEventListener('click', (event) => {
        if (event.target.closest('[data-cp-locate]') || event.target.closest('[data-cp-link]')) return;
        if (!row.hasAttribute('data-collapsed')) return;
        body.querySelectorAll('.cs-cp-del').forEach((other) => {
          other.setAttribute('data-collapsed', '');
          const bodyEl = other.querySelector('.cs-cp-del-body'); if (bodyEl) bodyEl.hidden = true;
          const tri = other.querySelector('.cs-cp-tri'); if (tri) tri.textContent = '▸';
        });
        row.removeAttribute('data-collapsed');
        const rowBody = row.querySelector('.cs-cp-del-body'); if (rowBody) rowBody.hidden = false;
        const rowTri = row.querySelector('.cs-cp-tri'); if (rowTri) rowTri.textContent = '▾';
      }));
      body.querySelectorAll('[data-cp-locate]').forEach((button) => button.addEventListener('click', async (event) => {
        event.stopPropagation();
        const item = (data.deliverables || [])[Number(button.dataset.cpLocate || 0)];
        dialog.close();
        await select(blockId);
        if (item) locateInEditor(item.title);
      }));
      body.querySelector('[data-cp-close]')?.addEventListener('click', () => dialog.close());
      body.querySelector('[data-cp-generate]')?.addEventListener('click', () => runAction('regen'));
      body.querySelectorAll('[data-cp-kid]').forEach((row) => {
        row.addEventListener('click', () => {
          dialog.close();
          void openChapterPlan(String(row.dataset.cpKid));
        });
        row.addEventListener('keydown', (event) => {
          if (event.key === 'Enter') { dialog.close(); void openChapterPlan(String(row.dataset.cpKid)); }
        });
      });
      body.querySelector('[data-cp-condense]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (!globalThis.confirm('把本章现有正文压缩为 300–500 字的总述？\n原稿会存入版本记录，可回滚。')) return;
        button.disabled = true;
        button.textContent = '正在精简…';
        try {
          const resp = await fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockId)}/condense`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({threadId}), signal: AbortSignal.timeout(180000),
          });
          const payload = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(payload.detail || `HTTP ${resp.status}`);
          status(`已精简为 ${payload.words} 字总述（原 ${payload.previous_words} 字已存档）`);
          data = await load();
          await render();
          void select(blockId);
        } catch (err) {
          status(`精简失败：${String(err.message || err)}`);
          button.disabled = false;
          button.textContent = '精简为总述';
        }
      });
      body.querySelector('[data-cp-match-clauses]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        const unit = unitOf.get(String(blockId));
        button.disabled = true;
        button.textContent = '匹配中…';
        try {
          const resp = await fetch(`/api/projects/${projectId}/match-clauses`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({threadId, root_block_id: String(unit || blockId)}),
            signal: AbortSignal.timeout(180000),
          });
          const payload = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(payload.detail || `HTTP ${resp.status}`);
          status(payload.source === 'llm'
            ? `AI 已把条款匹配到 ${payload.chapters} 个章节`
            : `已用关键词匹配 ${payload.chapters} 个章节（AI 未返回有效结果）`);
          data = await load();
          await render();
        } catch (err) {
          status(`匹配失败：${String(err.message || err)}`);
          button.disabled = false;
          button.textContent = '用 AI 匹配条款';
        }
      });
      body.querySelector('[data-cp-layout]')?.addEventListener('change', async (event) => {
        const previous = (context?.profile || {}).layout;
        try {
          const updated = await json(`/api/blocks/${active.id}/studio`,
            send('PUT', {layout: event.target.value, writing_status: (context?.profile || {}).writing_status}));
          context.profile = {...context.profile, ...updated};
          status(`本章类型已改为：${(context.layouts[event.target.value] || {}).label || event.target.value}`);
        } catch (error) { event.target.value = previous; status(error.message); }
      });
      body.querySelector('[data-cp-open-spec]')?.addEventListener('click', (event) => {
        event.stopPropagation();
        globalThis.document.dispatchEvent(new CustomEvent('writing-specs:open',
          {detail: {layout: (data.guidance && data.guidance.layout) || 'global'}}));
      });
      body.querySelector('[data-cp-edit-req]')?.addEventListener('click', (event) => {
        event.stopPropagation();          // 在 summary 里，别把折叠面板一起关掉
        event.preventDefault();
        const edit = body.querySelector('[data-cp-req-edit]');
        const rows = (data.requirements || []).map((requirement) => `<div class="cs-req-row" data-cp-req-row>
            <select data-cp-req-type>${['text','table','figure','material'].map((kind) => `<option value="${kind}" ${requirement.type === kind ? 'selected' : ''}>${kindLabel[kind]}</option>`).join('')}</select>
            <input type="text" data-cp-req-text value="${esc(requirement.text)}" />
            <button type="button" class="cs-req-del" data-cp-req-del>×</button>
          </div>`).join('');
        edit.hidden = false;
        edit.innerHTML = `<h5 class="cs-cp-sub">编辑本章要求</h5>${rows || '<p class="cs-dim">还没有要求</p>'}
          <div class="cs-plan-actions"><button type="button" class="mw-btn" data-cp-req-add>＋ 添加要求</button>
            <button type="button" class="mw-btn primary" data-cp-req-save>保存要求</button></div>
          <p class="cs-dim" data-cp-req-msg></p>`;
        edit.scrollIntoView({block: 'nearest'});
        edit.querySelector('[data-cp-req-add]').onclick = () => {
          edit.insertAdjacentHTML('beforeend', `<div class="cs-req-row" data-cp-req-row>
              <select data-cp-req-type><option value="text">写作</option><option value="table">产出·表</option><option value="figure">产出·图</option><option value="material">素材</option></select>
              <input type="text" data-cp-req-text placeholder="例：说明政策依据（至少两个部委文件）" />
              <button type="button" class="cs-req-del" data-cp-req-del>×</button></div>`);
        };
        edit.addEventListener('click', (event) => {
          if (event.target.closest('[data-cp-req-del]')) event.target.closest('[data-cp-req-row]').remove();
        });
        edit.querySelector('[data-cp-req-save]').onclick = async () => {
          const requirementsNext = [...edit.querySelectorAll('[data-cp-req-row]')].map((row) => ({
            text: (row.querySelector('[data-cp-req-text]') || {}).value?.trim() || '',
            type: (row.querySelector('[data-cp-req-type]') || {}).value || 'text',
          })).filter((item) => item.text);
          if (!data.plan_id) { edit.querySelector('[data-cp-req-msg]').textContent = '本章还没有计划，无法保存要求'; return; }
          try {
            const resp = await fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(data.plan_id)}/requirements`, {
              method: 'PUT', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({block_id: blockId, requirements: requirementsNext}),
              signal: AbortSignal.timeout(30000),
            });
            const payload = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(payload.detail || `HTTP ${resp.status}`);
            status(`已保存 ${payload.requirements.length} 条编写要求`);
            data = await load();
            await render();
          } catch (err) { edit.querySelector('[data-cp-req-msg]').textContent = `保存失败：${String(err.message || err)}`; }
        };
      });
    };
    try {
      data = await load();
      await render();
    } catch (err) {
      body.innerHTML = `<p class="cs-warning">加载失败：${esc(String(err.message || err))}</p>`;
      // 出错时把细节打到控制台，便于定位（不弹给用户）
      globalThis.console?.error?.('[本章计划] 渲染失败', err);
    }
  }

  /** 来源的可用片段  /** 来源的可用片段  /** 来源的可用片段  /** 来源的可用片段：段落/表格/图片，勾选后进入本章编写上下文（AI 基于它改写）。 */
  function openCitationSegments(citationId) {
    const citation = citationInfo.rows.find((row) => String(row.id) === String(citationId));
    if (!citation) return;
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog';
    let filter = 'all';
    let keyword = '';
    const render = (segments, selected) => {
      const kindLabel = {text: '段落', table: '表格', image: '图片'};
      const chosen = new Set(selected || []);
      const visible = (segments || []).map((segment, index) => ({segment, index}))
        .filter(({segment}) => filter === 'all' || segment.kind === filter)
        .filter(({segment}) => !keyword || String(segment.content || '').includes(keyword)
          || String(segment.heading || '').includes(keyword));
      const rows = visible.map(({segment, index}) => `<label class="cs-seg-row${chosen.has(index) ? ' on' : ''}">
          <input type="checkbox" data-seg-index="${index}" ${chosen.has(index) ? 'checked' : ''} />
          <div><span class="cs-seg-kind ${esc(segment.kind)}">${kindLabel[segment.kind] || '内容'}</span>
            ${segment.heading ? `<b>${esc(segment.heading)}</b>` : ''}
            <p>${esc(String(segment.content || '').slice(0, 260))}</p>
            ${segment.kind === 'image' ? `<button type="button" class="cs-seg-download" data-seg-image="${esc(segment.content)}">下载为我方素材</button>` : ''}
          </div>
        </label>`).join('');
      dialog.querySelector('.cs-plan-body').innerHTML = `<p class="cs-muted">《${esc(citation.title || citation.url)}》
          里可以直接沿用的内容。勾选后进入本章编写上下文：AI 会基于所选片段改写，并保留来源标注。</p>
        ${segments && segments.length ? `<div class="cs-seg-tools">
          ${['all', 'text', 'table', 'image'].map((kind) => `<button type="button" class="cs-seg-filter${filter === kind ? ' on' : ''}" data-seg-filter="${kind}">${kind === 'all' ? '全部' : kindLabel[kind]}</button>`).join('')}
          <input type="search" data-seg-search placeholder="搜索片段…" value="${esc(keyword)}" />
          <button type="button" class="mw-btn" data-seg-all>全选可见</button>
        </div>${rows}` : '<p class="cs-warning">这条来源还没有提取到片段。</p>'}
        <div class="cs-plan-actions">
          ${segments && segments.length ? '' : '<button type="button" class="mw-btn" data-seg-refresh>重新抓取并提取片段</button>'}
          <button type="button" class="mw-btn" data-seg-cancel>取消</button>
          ${segments && segments.length ? '<button type="button" class="mw-btn primary" data-seg-save>保存选用</button>' : ''}
        </div>`;
      dialog.querySelector('[data-seg-cancel]').onclick = () => dialog.close();
      dialog.querySelectorAll('[data-seg-filter]').forEach((button) => button.addEventListener('click', () => {
        filter = button.dataset.segFilter;
        render(citation.segments || [], [...dialog.querySelectorAll('[data-seg-index]:checked')].map((input) => Number(input.dataset.segIndex)));
      }));
      dialog.querySelector('[data-seg-search]')?.addEventListener('input', (event) => {
        keyword = event.target.value.trim();
        render(citation.segments || [], [...dialog.querySelectorAll('[data-seg-index]:checked')].map((input) => Number(input.dataset.segIndex)));
        dialog.querySelector('[data-seg-search]')?.focus();
      });
      dialog.querySelector('[data-seg-all]')?.addEventListener('click', () => {
        dialog.querySelectorAll('[data-seg-index]').forEach((input) => { input.checked = true; input.closest('.cs-seg-row').classList.add('on'); });
      });
      dialog.querySelectorAll('[data-seg-image]').forEach((button) => button.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        button.disabled = true;
        button.textContent = '下载中…';
        try {
          const resp = await fetch(`/api/projects/${projectId}/citations/${encodeURIComponent(citation.id)}/image`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: button.dataset.segImage}), signal: AbortSignal.timeout(120000),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          status(`已下载为素材《${data.filename}》，可在「本章素材」里引用`);
          button.textContent = '已入库 ✓';
        } catch (err) {
          button.disabled = false;
          button.textContent = '下载为我方素材';
          status(`下载失败：${String(err.message || err)}`);
        }
      }));
      dialog.querySelector('[data-seg-refresh]')?.addEventListener('click', async () => {
        try {
          const resp = await fetch(`/api/projects/${projectId}/citations/${encodeURIComponent(citation.id)}/refresh`, {
            method: 'POST', signal: AbortSignal.timeout(120000),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          citation.segments = data.segments || [];
          citation.selected = data.selected || [];
          render(citation.segments, citation.selected);
        } catch (err) {
          status(`提取失败：${String(err.message || err)}`);
        }
      });
      dialog.querySelector('[data-seg-save]')?.addEventListener('click', async () => {
        const indices = [...dialog.querySelectorAll('[data-seg-index]:checked')]
          .map((input) => Number(input.dataset.segIndex));
        try {
          const resp = await fetch(`/api/projects/${projectId}/citations/${encodeURIComponent(citation.id)}/select`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({indices}), signal: AbortSignal.timeout(30000),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          citation.selected = data.selected || [];
          status(`已选用 ${citation.selected.length} 个片段：编写本章时会基于它们改写`);
          dialog.close();
          if (active) paintAside();
        } catch (err) {
          status(`保存失败：${String(err.message || err)}`);
        }
      });
    };
    dialog.innerHTML = `<header><h3>可用片段</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-dim">加载中…</p></div>`;
    globalThis.document.body.append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    render(citation.segments || [], citation.selected || []);
  }

  function openCitationDialog(blockKey, initialQuery = '') {
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog cs-cite-dialog';
    dialog.innerHTML = `<header><h3>官方来源<small data-cite-count></small></h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body">
        <div class="cs-cite-search">
          <input type="text" data-cite-input value="${esc(initialQuery)}"
            placeholder="输入检索词自动找官方原文；或直接粘贴 https:// 链接核验" />
          <button type="button" class="mw-btn primary" data-cite-go>自动找来源</button>
        </div>
        <div class="cs-cite-quick">
          <span class="cs-dim">官方入口</span>
          <span data-cite-portals class="cs-cite-chips"></span>
        </div>
        <div data-cite-needs></div>
        <details class="cs-cite-rule"><summary>来源规则</summary>
          官方原文最优；非官方渠道内容可能真实，但必须标注来源并由你确认后才能引用。
          涉政/标准/指标等硬信息建议用官方原文；找不到来源时正文写【待补充】。
        </details>
        <details class="cs-cite-history" data-cite-history hidden><summary data-cite-history-label>上次查找</summary>
          <div data-cite-history-list></div></details>
        <div data-cite-panel></div>
      </div>`;
    globalThis.document.body.append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    const panel = dialog.querySelector('[data-cite-panel]');
    const input = dialog.querySelector('[data-cite-input]');
    const goButton = dialog.querySelector('[data-cite-go]');
    const countHost = dialog.querySelector('[data-cite-count]');
    let accepted = 0;
    let activeGap = '';                       // 当前在补哪个素材缺口（接受引用时记下）
    const markAccepted = () => { countHost.textContent = accepted ? `已接受 ${accepted} 条` : ''; };
    const isUrl = (value) => /^https?:\/\//i.test(String(value || '').trim());
    const post = async (url, body) => {
      const resp = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: AbortSignal.timeout(120000)});
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      return data;
    };
    const renderPortals = (list) => {
      const host = dialog.querySelector('[data-cite-portals]');
      host.innerHTML = (list || []).map((portal) => `<a href="${esc(portal.url)}" target="_blank" rel="noreferrer" title="${esc(portal.hint)}">${esc(portal.name.replace(' · ', ' '))}</a>`).join('');
    };
    const accept = async (candidate, row, buttonEl) => {
      const button = buttonEl || row.querySelector('[data-cite-accept]');
      if (button) button.disabled = true;
      const confirmed = candidate.trust === 'official' ? true
        : !!(row && row.querySelector('[data-cite-confirm]')?.checked)
          || !!(panel.querySelector('[data-cite-confirm]')?.checked);
      try {
        await post(`/api/projects/${projectId}/citations`, {...candidate, block_id: blockKey, thread_id: threadId, confirmed, gap: activeGap});
        accepted += 1;
        markAccepted();
        row.classList.add('accepted');
        if (button) { button.textContent = '已接受 ✓'; button.disabled = true; }
        status('已接受 1 条官方来源');
      } catch (err) {
        row.querySelector('[data-cite-msg]').textContent = `接受失败：${String(err.message || err)}`;
        button.disabled = false;
      }
    };
    const verifyInto = async (url, row, button) => {
      if (!url || url.length < 8) { panel.innerHTML = '<p class="cs-warning">请先输入检索词或粘贴官方原文链接。</p>'; return; }
      const preview = row ? row.querySelector('[data-cite-preview]') : null;
      if (button) { button.disabled = true; button.textContent = '抓取中…'; }
      if (preview) preview.innerHTML = '<p class="cs-dim">正在抓取并抽取机构/日期/摘录…</p>';
      try {
        const candidate = await post(`/api/projects/${projectId}/citations/fetch`, {url});
        const unverified = candidate.trust !== 'official';
        const html = `<div class="cs-cite-card">
            <h4>${esc(candidate.title)} <span class="cs-cite-trust ${unverified ? 'unverified' : 'official'}">${unverified ? '非官方来源 · 需确认' : '官方原文'}</span></h4>
            <p class="cs-muted">${esc(candidate.publisher || candidate.domain)} · ${esc(candidate.published_at || '日期待补')}</p>
            <p>${esc(candidate.excerpt || '')}</p>
            <p class="cs-dim">${esc(candidate.url)}</p>
            ${unverified ? `<p class="cs-warning">${esc(candidate.warning || '非官方渠道内容可能真实，但请先核对')}</p>
              <label class="cs-cite-confirm"><input type="checkbox" data-cite-confirm /> 我已核对：内容真实可靠，愿意以该来源引用</label>` : ''}
            <div class="cs-plan-actions"><button type="button" class="mw-btn primary" data-cite-accept${unverified ? ' disabled' : ''}>接受为引用</button></div>
            <p class="cs-dim" data-cite-msg></p></div>`;
        const host = preview || panel;
        host.innerHTML = html;
        const confirmBox = host.querySelector('[data-cite-confirm]');
        const acceptButton = host.querySelector('[data-cite-accept]');
        if (confirmBox && acceptButton) {
          confirmBox.addEventListener('change', () => { acceptButton.disabled = !confirmBox.checked; });
        }
        acceptButton.onclick = (event) => void accept(candidate, row || panel, event.currentTarget);
      } catch (err) {
        const message = `<p class="cs-warning">抓取失败：${esc(String(err.message || err))}</p>`;
        if (preview) preview.innerHTML = message; else panel.innerHTML = message;
      } finally {
        if (button) { button.disabled = false; button.textContent = '抓取核验'; }
      }
    };
    const discover = async () => {
      const query = input.value.trim();
      if (query.length < 2) { panel.innerHTML = '<p class="cs-warning">先输入要查什么（≥2 字）。</p>'; return; }
      goButton.disabled = true;
      goButton.textContent = '查找中…';
      panel.innerHTML = '<p class="cs-dim">正在官方入口里查找…</p>';
      try {
        const data = await post(`/api/projects/${projectId}/citations/discover`,
          {query, thread_id: threadId, block_id: blockKey});
        renderPortals(data.portals);
        const results = data.results || [];
        const rows = results.map((item, index) => `<div class="cs-cite-cand${item.matched ? ' hit' : ''}" data-cite-row="${index}">
            <div class="cs-cite-cand-main">
              <b>${esc(item.title || item.url)}</b>
              <small>${esc(item.domain)}${item.matched ? ' · 命中检索词' : ' · 未命中检索词，建议打开原文自查'}</small>
            </div>
            <div class="cs-cite-cand-acts">
              <a href="${esc(item.url)}" target="_blank" rel="noreferrer">打开</a>
              <button type="button" class="mw-btn" data-cite-verify="${index}">抓取核验</button>
            </div>
            <div data-cite-preview></div>
          </div>`).join('');
        panel.innerHTML = `<p class="cs-dim">${esc(data.message || '')}</p>${rows
          || '<p class="cs-dim">没有候选。<button type="button" class="mw-btn" data-cite-open-portals>打开官方入口自己找</button></p>'}`;
        const rowsHost = panel;
        rowsHost.querySelectorAll('[data-cite-verify]').forEach((button) => button.addEventListener('click', () => {
          const row = button.closest('[data-cite-row]');
          const item = results[Number(button.dataset.citeVerify)];
          if (item) { input.value = item.url; goButton.textContent = '核验链接'; void verifyInto(item.url, row, button); }
        }));
        rowsHost.querySelector('[data-cite-open-portals]')?.addEventListener('click', () => {
          const first = (data.portals || [])[0];
          if (first) globalThis.open(first.url, '_blank', 'noreferrer');
        });
      } catch (err) {
        panel.innerHTML = `<p class="cs-warning">查找失败：${esc(String(err.message || err))}</p>`;
      } finally {
        goButton.disabled = false;
        if (!isUrl(input.value)) goButton.textContent = '自动找来源';
      }
    };
    input.addEventListener('input', () => { goButton.textContent = isUrl(input.value) ? '核验链接' : '自动找来源'; });
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); void discover(); } });
    goButton.onclick = () => (isUrl(input.value) ? verifyInto(input.value.trim(), null, goButton) : discover());
    renderPortals([]);
    fetch(`/api/projects/${projectId}/citations/portals`, {signal: AbortSignal.timeout(15000)})
      .then((resp) => resp.json())
      .then((data) => { if (dialog.isConnected) renderPortals(data.portals); })
      .catch(() => {});
    const renderNeeds = (needs) => {
      const host = dialog.querySelector('[data-cite-needs]');
      if (!host) return;
      if (!needs.length) { host.innerHTML = ''; return; }
      host.innerHTML = `<div class="cs-cite-needs-head">素材缺口（逐条补，补上会标记）</div>`
        + needs.map((need) => `<div class="cs-cite-need${need.covered ? ' done' : ''}">
            <span>${esc(need.text)}</span>
            <em>${need.covered ? `已补 ${need.covered} 条` : '未补'}</em>
            <button type="button" class="mw-btn" data-need-query="${esc(need.query)}" data-need-gap="${esc(need.text)}">检索</button>
          </div>`).join('');
      host.querySelectorAll('[data-need-query]').forEach((button) => button.addEventListener('click', () => {
        activeGap = button.dataset.needGap || '';
        input.value = button.dataset.needQuery || '';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        void discover();
      }));
    };
    const renderHistory = (history) => {
      const box = dialog.querySelector('[data-cite-history]');
      const list = dialog.querySelector('[data-cite-history-list]');
      const label = dialog.querySelector('[data-cite-history-label]');
      if (!box || !list || !history.length) return;
      box.hidden = false;
      if (label) label.textContent = `上次查找（${history.length}）`;
      list.innerHTML = history.map((item, index) => `<div class="cs-cite-history-row">
          <span><b>${esc(item.query)}</b><small>${esc(String(item.created_at || '').slice(0, 16))} · ${item.results.length} 条候选</small></span>
          <button type="button" class="mw-btn" data-history-restore="${index}">看上次候选</button>
        </div>`).join('');
      list.querySelectorAll('[data-history-restore]').forEach((button) => button.addEventListener('click', () => {
        const item = history[Number(button.dataset.historyRestore)];
        if (!item) return;
        input.value = item.query;
        const rows = (item.results || []).map((candidate, index) => `<div class="cs-cite-cand${candidate.matched ? ' hit' : ''}" data-cite-row="${index}">
            <div class="cs-cite-cand-main"><b>${esc(candidate.title || candidate.url)}</b>
              <small>${esc(candidate.domain || '')} · 历史结果</small></div>
            <div class="cs-cite-cand-acts"><a href="${esc(candidate.url)}" target="_blank" rel="noreferrer">打开</a>
              <button type="button" class="mw-btn" data-cite-verify="${index}">抓取核验</button></div>
            <div data-cite-preview></div></div>`).join('');
        panel.innerHTML = `<p class="cs-dim">历史检索「${esc(item.query)}」的候选（${(item.results || []).length} 条）</p>${rows}`;
        panel.querySelectorAll('[data-cite-verify]').forEach((verifyButton) => verifyButton.addEventListener('click', () => {
          const row = verifyButton.closest('[data-cite-row]');
          const candidate = (item.results || [])[Number(verifyButton.dataset.citeVerify)];
          if (candidate) { input.value = candidate.url; void verifyInto(candidate.url, row, verifyButton); }
        }));
      }));
    };
    markAccepted();
    fetch(`/api/projects/${projectId}/citations/history?threadId=${encodeURIComponent(threadId)}&blockId=${encodeURIComponent(blockKey)}`, {signal: AbortSignal.timeout(15000)})
      .then((resp) => (resp.ok ? resp.json() : null))
      .then((data) => { if (dialog.isConnected && data) renderHistory(data.history || []); })
      .catch(() => {});
    if (initialQuery && !isUrl(initialQuery)) void discover();
    // 按当前章节推导检索词（标题 + 素材缺口 + 配方骨架），并自动检索一次 —— 用户不需要盲搜
    if (blockKey) {
      fetch(`/api/projects/${projectId}/citations/suggest?threadId=${encodeURIComponent(threadId)}&blockId=${encodeURIComponent(blockKey)}`, {signal: AbortSignal.timeout(15000)})
        .then((resp) => (resp.ok ? resp.json() : null))
        .then((data) => {
          if (!dialog.isConnected || !data || !data.query) return;
          if (!input.value.trim()) input.value = data.query;
          const context = globalThis.document.createElement('p');
          context.className = 'cs-dim cs-cite-ctx';
          context.textContent = `本章《${data.chapter.title}》 · 已有 ${data.have.citations} 条来源 / ${data.have.materials} 份材料`
            + (data.gaps.length ? ` · 素材缺口：${data.gaps.join('、')}` : '');
          dialog.querySelector('.cs-cite-search').insertAdjacentElement('afterend', context);
          renderNeeds(data.needs || []);
          if (!initialQuery) void discover();
        })
        .catch(() => {});
    }
  }

  /** 文字生成图：用户粘贴一段描述 → 抽成关系链图（:::diagram）→ 插入正文。 */
  function openDiagramDialog() {
    if (!active) return;
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog';
    dialog.innerHTML = `<header><h3>文字生成图</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">粘贴一段描述层级、流程或因果关系的文字（≥10 字），
        抽成关系链图后插入本章正文，可再手动修改。复杂架构图建议上传素材图。</p>
        <textarea data-diagram-text rows="6" placeholder="例：监控平台由采集层、存储层、分析层、展示层组成，采集层上报到存储层…"></textarea>
        <div class="cs-plan-actions"><button type="button" class="mw-btn" data-diagram-cancel>取消</button>
          <button type="button" class="mw-btn primary" data-diagram-run>生成图示</button></div>
        <div data-diagram-result></div></div>`;
    (globalThis.document.body).append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    dialog.querySelector('[data-diagram-cancel]').onclick = () => dialog.close();
    dialog.querySelector('[data-diagram-run]').onclick = async (event) => {
      const button = event.currentTarget;
      const text = dialog.querySelector('[data-diagram-text]').value.trim();
      const result = dialog.querySelector('[data-diagram-result]');
      if (text.length < 10) { result.innerHTML = '<p class="cs-warning">请至少粘贴 10 个字。</p>'; return; }
      button.disabled = true;
      button.textContent = '生成中…';
      try {
        const resp = await fetch(`/api/blocks/${active.id}/studio/diagram`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text, title: active.title}), signal: AbortSignal.timeout(120000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        result.innerHTML = `<p class="cs-muted">节点 ${data.nodes.length} 个 · 模型 ${esc(data.model || '')}</p>
          <pre class="cs-diagram-preview">${esc(data.markdown)}</pre>
          <div class="cs-plan-actions"><button type="button" class="mw-btn primary" data-diagram-insert>插入正文</button></div>`;
        result.querySelector('[data-diagram-insert]').onclick = () => {
          insert(data.markdown, '');
          dialog.close();
          status('已插入图示，请核对关系是否准确');
        };
      } catch (err) {
        result.innerHTML = `<p class="cs-warning">生成失败：${esc(String(err.message || err))}</p>`;
      } finally {
        button.disabled = false;
        button.textContent = '生成图示';
      }
    };
  }

  /** 目录级编写计划（C1：降级为"汇总 + 批量"）——只看工作量与状态；编辑与确认都在章计划里。 */
  async function openWritingPlan(unitBlockId) {
    if (!unitBlockId) return;
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog cs-branch-dialog';
    dialog.innerHTML = `<header><h3>编写计划</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">正在生成计划…</p></div>`;
    (host.ownerDocument?.body || globalThis.document.body).append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    const body = dialog.querySelector('.cs-plan-body');
    let plan;
    try {
      const resp = await fetch(`/api/projects/${projectId}/writing-plans`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({thread_id: threadId, root_block_id: String(unitBlockId)}),
        signal: AbortSignal.timeout(60000),
      });
      plan = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(plan.detail || `HTTP ${resp.status}`);
    } catch (err) {
      body.innerHTML = `<p class="cs-warning">计划生成失败：${esc(String(err.message || err))}</p>`;
      return;
    }
    const render = () => {
      const chapters = plan.chapters || [];
      const totalPages = chapters.reduce((sum, chapter) => sum + (Number(chapter.pages) || 0), 0);
      const totalRequirements = chapters.reduce((sum, chapter) => sum + ((chapter.requirements || []).length), 0);
      const totalTables = chapters.reduce((sum, chapter) => sum + (chapter.tasks || []).filter((task) => task.kind === 'table').length, 0);
      const totalFigures = chapters.reduce((sum, chapter) => sum + (chapter.tasks || []).filter((task) => task.kind === 'diagram').length, 0);
      const blocksByKey = {};
      for (const block of blocks) blocksByKey[String(block.block_id)] = block;
      body.innerHTML = `<h4>《${esc(plan.root_title || '')}》共 ${chapters.length} 章</h4>
        <p class="cs-dim">目录级只做汇总与批量；要求编辑与"我能写"的确认都在每一章自己的计划里。</p>
        <p class="cs-branch-stats">工作量：计划 ${totalPages || '—'} 页 · 要求 ${totalRequirements} 条 · 表 ${totalTables} 张 · 图 ${totalFigures} 张</p>
        <div class="cs-branch-table">
          <div class="cs-branch-row cs-branch-head"><span>章</span><span>页数</span><span>要求</span><span>素材</span><span>正文</span><span>计划</span><span></span></div>
          ${chapters.map((chapter) => {
            const block = blocksByKey[String(chapter.block_id)] || {};
            const requirements = chapter.requirements || [];
            const gaps = chapter.gaps || [];
            const words = String(block.content || '').replace(/\s+/g, '').length;
            return `<div class="cs-branch-row">
                <span class="cs-branch-title">${esc(chapter.title)}</span>
                <span>${chapter.pages || '—'}</span>
                <span>${requirements.length} 条</span>
                <span class="${gaps.length ? 'cs-cp-bad' : ''}">${gaps.length ? `缺口 ${gaps.length}` : '—'}</span>
                <span>${words ? `${Math.round(words / 250 * 10) / 10} 页` : '未开始'}</span>
                <span class="${chapter.confirmed || plan.status === 'confirmed' ? 'cs-cite-used' : 'cs-cite-unused'}">${chapter.confirmed || plan.status === 'confirmed' ? '已确认' : '未确认'}</span>
                <span><button type="button" class="cs-cp-mini" data-branch-open="${esc(String(chapter.block_id))}">打开本章计划</button></span>
              </div>`;
          }).join('')}
        </div>
        <div class="cs-plan-actions">
          <button type="button" class="mw-btn" data-branch-close>关闭</button>
        </div>
        <p class="cs-dim">计划由系统按评分点与总篇幅统筹生成、自动生效；要调整就进各章的「本章编写计划」。</p>`;
      body.querySelector('[data-branch-close]').onclick = () => dialog.close();
      body.querySelectorAll('[data-branch-open]').forEach((button) => button.addEventListener('click', () => {
        dialog.close();
        void openChapterPlan(button.dataset.branchOpen);
      }));
    };
    render();
  }

  async function loadPageBudget() {
    const tid = threadId;                 // mountChapterStudio 的参数，不猜 URL
    if (!tid) return;
    try {
      const resp = await fetch(`/api/workflow/${encodeURIComponent(tid)}/page-budget`, {signal: AbortSignal.timeout(10000)});
      if (!resp.ok) return;
      const data = await resp.json();
      pageBudget.byBlock = {};
      (data.chapters || []).forEach((c) => { pageBudget.byBlock[String(c.block_id)] = c; });
      pageBudget.loaded = true;
    } catch (e) { /* 篇幅拿不到时不影响编写 */ }
  }
  /** 子树已写字数：父章节自己不写正文，正文在子章节里 —— 直接数自己会得到 0，误导用户。 */
  function subtreeWords(b) {
    const key = String(b.block_id || b.id);
    const self = wordsOf(b);
    const children = (blocks || []).filter((x) => {
      const k = String(x.block_id || x.id);
      return k !== key && k.startsWith(`${key}.`);
    });
    return self + children.reduce((sum, x) => sum + wordsOf(x), 0);
  }
  /** 单个章节的篇幅数字（父章节自动取子树合计）。
   *  章头、章节类型行、目录树悬浮提示共用**同一个口径**，避免三处各算一套。 */
  function budgetStats(b) {
    const c = b ? budgetOf(b) : null;
    const isParent = !!(c && c.is_target === false);
    const words = b ? (isParent ? subtreeWords(b) : wordsOf(b)) : 0;
    const pages = words / WPP;
    const target = c && Number(c.pages) ? Number(c.pages) : 0;
    const gap = target ? target - pages : null;
    return {
      c, isParent, words: Math.round(words), pages, target, gap,
      gapText: target ? (gap <= 0.1 ? '已达标' : `差 ${gap.toFixed(1)} 页`) : '',
      over: !!(target && gap < 0),
    };
  }

  function budgetLineHtml(b) {
    const st = budgetStats(b);
    if (!st.target) return '';
    const state = st.gap <= 0.1
      ? '<span class="is-ok">已达标</span>'
      : `<span class="is-gap">差 ${st.gap.toFixed(1)} 页</span>`;
    return `<div class="cs-chapter-budget">
      <span>预算 <b>${st.target}</b> 页 · 已写 <b>${st.pages.toFixed(1)}</b> 页（${st.words} 字）</span>
      ${state}
      ${st.isParent ? '<span class="cs-muted">父章节：预算与已写均为子树合计</span>' : ''}
    </div>`;
  }
  const stateOf = b => !units.has(b.id) ? '目录' : profile[b.id]?.writing_status==='complete' ? '完成' : b.content?.trim() ? (profile[b.id]?.writing_status==='review'?'待核对':'草稿') : '未开始';
  // 拿到篇幅后重画左树与章头（不阻塞首屏）
  loadPageBudget().then(() => { if (active) paintTree(); });
  void loadPlanUnits();          // 一级目录上挂「编写计划」入口
  void loadSpecs();              // 编写规范版本（右栏就近入口）
  void loadCitations();          // 已接受的官方来源
  // 质检清单入口在「评审与交付」阶段（编写页不再提供），通过事件唤起
  const onQualityOpen = () => { if (active) void openQualityDialog(); };
  globalThis.document.addEventListener('writing-quality:open', onQualityOpen);
  // P5（2026-10-16）：章节类型是"会影响生成的输入"，从右栏「编排建议」搬到正文上方。
  // 右栏每次切换章节都会重渲染，因此监听其变化，搬完即止。
  const asideHost = host.querySelector('[data-cs="aside"]');
  const mainHost = host.querySelector('.cs-main');
  if (asideHost && mainHost && typeof MutationObserver !== 'undefined') {
    const moveKindRow = () => {
      const select = asideHost.querySelector('[data-cs="layout"]');
      if (!select) return;
      let row = mainHost.querySelector('.cs-kind-row');
      // 注意：本模块内 document 是接口返回的数据对象（不是 DOM），必须用 globalThis.document
      if (!row) { row = globalThis.document.createElement('div'); row.className = 'cs-kind-row'; }
      // 右栏每次重渲染都会**新建**一个 select，必须把新的换进来、旧的丢掉，
      // 否则主栏与右栏会各留一个（实测出现过两个"章节类型"、值还不一样）
      const stale = row.querySelector('[data-cs="layout"]');
      if (stale && stale !== select) stale.remove();
      if (select.parentElement !== row) {
        row.innerHTML = '<span class="cs-muted">章节类型</span>';
        row.appendChild(select);
        row.insertAdjacentHTML('beforeend', '<span class="cs-muted">决定本章按哪类章节编制（影响提示与结构）</span>');
      }
      const label = select.closest('label');
      if (label) label.hidden = true;          // 右栏原位置不再重复出现
      const editor = mainHost.querySelector('[data-cs="editor"]');
      if (row.parentElement !== mainHost) {
        if (editor && editor.parentElement === mainHost) mainHost.insertBefore(row, editor);
        else mainHost.appendChild(row);
      }
    };
    new MutationObserver(moveKindRow).observe(asideHost, {childList: true, subtree: true});
    moveKindRow();
  }
  const paintTree = () => {
    const keyword = get('search').value.trim().toLowerCase();
    get('tree').innerHTML = blocks.filter(b=>!keyword || b.title.toLowerCase().includes(keyword)).map(b=>{
      const st = stateOf(b), c = budgetOf(b);
      const target = c && Number(c.pages) ? Number(c.pages) : 0;
      const pg = target ? Math.min(100, Math.round(pagesOf(b) / target * 100)) : null;
      // title 带状态与预算，悬浮可读；行内不再显示文字，保证名称完整
      // 口径与章头/章节类型行一致：父章节按**子树合计**，并给出"差多少"
      const bs = budgetStats(b);
      const tips = [b.title, st,
        bs.target ? `预算 ${bs.target} 页 · 已写 ${bs.pages.toFixed(1)} 页 · ${bs.gapText}${bs.isParent ? '（子树合计）' : ''}` : '',
      ].filter(Boolean).join(' · ');
      return `<button class="cs-node ${active?.id===b.id?'on':''}" data-chapter="${b.id}" data-st="${esc(st)}"
        ${pg === null ? '' : `data-pg="1" style="--pg:${pg}%"`}
        style="padding-left:${8+Math.min(5,Math.max(0,(Number(b.level)||1)-1))*12}px" title="${esc(tips)}"><span>${esc(b.title)}</span><small>${esc(st)}</small></button>`;
    }).join('');
  };
  async function save() {
    if(live.active&&live.pending){status('图文表尚未完成，编制进度已保留；完成或移除未完成项后可保存正文。');return false;}
    if (!active || !dirty) return true;
    const block = active, value = editor.value;
    status('正在保存…');
    try {
      if(live.active){
        const latest=await json(`/api/projects/${projectId}/blocks?threadId=${encodeURIComponent(threadId)}`);
        const server=latest.find(b=>b.id===block.id);
        if(!server||(server.content||'')!==(block.content||'')){status('服务器正文已发生变化，本地编制内容已保留，请核对版本后再保存。');return false;}
      }
      await json(`/api/blocks/${block.id}`, send('PUT',{content:value}));
      block.content = value;
      if (value.trim()) units.add(block.id);
      if (profile[block.id]?.writing_status==='complete') profile[block.id].writing_status='review';
      if (active.id===block.id && editor.value===value) { dirty=false; sessionStorage.removeItem(draftKey(block)); }
      paintTree(); stats();
      if(active.id===block.id){const savedState=stateOf(block);get('chapter-head').querySelector('span').textContent=savedState==='目录'?'已保存':`已保存 · ${savedState}`;}
      host.querySelector('[data-action="save"]').disabled=!dirty;
      status(''); return true;
    } catch (error) { status(`保存失败：${error.message}。本地草稿已保留。`); return false; }
  }
  const changed = () => {
    dirty=true;
    if(profile[active.id]?.writing_status==='complete'){
      profile[active.id].writing_status='review';context.profile.writing_status='review';paintTree();stats();
    }
    get('chapter-head').querySelector('span').textContent='未保存';
    host.querySelector('[data-action="save"]').disabled=false;
    sessionStorage.setItem(draftKey(active),JSON.stringify({content:editor.value,base:active.content || ''}));
    status('');
  };
  const live=mountLiveWriting(get('live'),{editor,render:previewContent,onChange:changed,scope:threadId});
  let previewVisible=false,viewRequest=0,writingBusy=false,pendingCompletion=null;
  const paintView = preview => {
    previewVisible=preview;++viewRequest;
    const display=chapterDisplayState(preview,live.active);
    editor.hidden=display.editorHidden;get('preview').hidden=display.previewHidden;
    if(preview)live.hide();else if(live.active)live.show();
    host.querySelector('[data-action="edit"]').setAttribute('aria-pressed',String(!preview));
    host.querySelector('[data-action="preview"]').setAttribute('aria-pressed',String(preview));
    const addMenu=host.querySelector('[data-add-menu]');if(addMenu)addMenu.hidden=preview||writingBusy;  // 已并入「插入 ▾」
  };
  const insert = (text,kind='text') => {
    if(writingBusy){status('AI 正在编制，结束后可手动添加内容。');return;}
    paintView(false);
    if(live.active){live.add(kind,text);return;}
    editor.setRangeText(`\n\n${text}\n\n`,editor.selectionStart,editor.selectionEnd,'end');
    changed();editor.focus();
  };
  // 素材类问题归「素材」页签（由其缺口三分法承载），待办只保留可"就地处理"的模块
  const TODO_GROUPS = [
    ['content', '内容'], ['source', '来源'], ['budget', '篇幅'], ['figure', '图表'],
  ];

  /** 右栏「待办」页签标题上的计数徽标（口径与待办页签一致，排除素材类） */
  function paintTodoBadge() {
    const el = host.querySelector('[data-todo-badge]');
    if (!el) return;
    const count = (planBrief?.todo || []).filter((x) => (x.module || 'content') !== 'material').length;
    el.textContent = String(count);
    el.hidden = !count;
  }

  /** 右栏「待办」页签：按处理模块分组，每条 = 模块 + 带数字的问题 + 一个动作 */
  function todoPaneHtml() {
    const todos = (planBrief?.todo || []).filter((x) => (x.module || 'content') !== 'material');
    if (!todos.length) {
      return `<section data-pane="todo"><h4>待办</h4>
        <p class="cs-todo-ok">✓ 本章没有待办，可以生成或交付</p></section>`;
    }
    const groups = TODO_GROUPS.map(([key, label]) => {
      const items = todos.filter((x) => (x.module || 'content') === key);
      if (!items.length) return '';
      return `<div class="cs-todo-group"><p class="cs-todo-glabel"><i class="m-${key}"></i>${label}模块 ${items.length}</p>
        ${items.map((x, i) => `<div class="cs-todo-row">
            <span class="cs-todo-idx">${i + 1}</span>
            <span class="cs-todo-text">${esc(x.text)}</span>
            <button type="button" class="cs-todo-act" data-aside-todo="${esc(x.action)}" data-gap-kind="${esc(x.target || '')}">${esc(x.action_label)}</button>
          </div>`).join('')}</div>`;
    }).join('');
    return `<section data-pane="todo"><h4>待办 <small>${todos.length} 项 · 按优先级</small></h4>${groups}</section>`;
  }

  /** 右栏「素材」页签：三分法 —— 已有可用 / 公开资料可补 / 必须你上传 */
  function gapGroupsHtml() {
    const gaps = planBrief?.gaps || [];
    if (!gaps.length) return '';
    const pub = gaps.filter((g) => g.availability === 'public');
    const up = gaps.filter((g) => g.availability === 'upload');
    const block = (title, hint, items) => !items.length ? '' : `<div class="cs-gap-group">
        <p class="cs-gap-glabel">${title} <small>${hint}</small></p>
        ${items.map((g) => `<div class="cs-gap-row">
            <span class="cs-gap-kind">${esc(g.kind)}</span>
            <span class="cs-gap-meta">缺 ${g.lack} 份${g.purpose ? ` · ${esc(g.purpose)}` : ''}</span>
            ${g.availability === 'public'
              ? `<button type="button" class="cs-gap-act" data-gap-search="${esc(g.suggestions[0] || g.kind)}">去检索</button>`
              : `<button type="button" class="cs-gap-act" data-gap-upload="1">上传</button>`}
            <span class="cs-gap-why">${esc(g.reason || '')}</span>
          </div>`).join('')}</div>`;
    return `${block('公开资料可补', '系统可检索官方原文', pub)}${block('必须你上传', '公开渠道没有这类材料', up)}`;
  }

  /** 插入面板：图片 / 表格 两类，各自支持「AI 生成」与「从素材插入」 */
  function openInsertDialog() {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog cs-insert-dialog';
    dlg.innerHTML = `<header><h3>插入内容</h3><button type="button" aria-label="关闭" data-ins-close>×</button></header>
      <div class="cs-plan-body">
        <nav class="cs-ins-tabs">
          <button type="button" data-ins-tab="image" aria-pressed="true">图片</button>
          <button type="button" data-ins-tab="table" aria-pressed="false">表格</button>
        </nav>
        <div class="cs-ins-body" data-ins-body></div>
      </div>
      <footer class="cs-ins-foot"><span class="cs-ins-picked" data-ins-picked></span>
        <button type="button" class="cs-cp-ghost" data-ins-cancel>取消</button></footer>`;
    const bodyEl = dlg.querySelector('[data-ins-body]');
    const pickedEl = dlg.querySelector('[data-ins-picked]');
    const state = { tab: 'image', view: 'menu' };

    const materials = () => context?.materials || [];
    const chapterGist = () => {
      const sel = String(editor.value || '').slice(editor.selectionStart || 0, editor.selectionEnd || 0).trim();
      if (sel.length >= 10) return sel.slice(0, 2000);
      const reqs = (planBrief?.deliverables || []).map((d) => d.title).filter(Boolean);
      return `${active?.title || ''}${reqs.length ? `：${reqs.join('、')}` : ''}`.slice(0, 2000);
    };

    const renderMenu = () => {
      const isImage = state.tab === 'image';
      pickedEl.textContent = '';
      bodyEl.innerHTML = isImage ? `
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>AI 生成信息图</b>
            <small>把选中的文字 / 本章要点抽成结构关系图</small></div>
          <button type="button" class="cs-ins-chip" data-ins-generate="info">生成</button></div>
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>AI 生成架构图</b>
            <small>按"接入 → 服务 → 数据"分层生成图示</small></div>
          <button type="button" class="cs-ins-chip" data-ins-generate="arch">生成</button></div>
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>从素材插入图片</b>
            <small>证书 / 截图 / 现场照（真实图片）</small></div>
          <button type="button" class="cs-ins-chip" data-ins-from-material="image">选择素材</button></div>`
      : `
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>AI 生成表格</b>
            <small>按本章交付物要求生成表格草稿（缺数据写【待补充】）</small></div>
          <button type="button" class="cs-ins-chip" data-ins-generate="table">生成</button></div>
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>从素材插入表格</b>
            <small>docx / xlsx 抽取真实表格</small></div>
          <button type="button" class="cs-ins-chip" data-ins-from-material="table">选择素材</button></div>`;
    };

    const renderGenerate = (kind) => {
      const isTable = kind === 'table';
      const prefill = isTable
        ? ((planBrief?.deliverables || []).filter((d) => d.kind === 'table').map((d) => d.title).join('；') || `按《${active?.title || '本章'}》的编写要求出表`)
        : chapterGist();
      bodyEl.innerHTML = `<p class="cs-ins-hint">${isTable
          ? '说明这张表要表达什么（列名、行对象）。缺具体数据会写【待补充】，不会编造。'
          : '描述层级 / 流程 / 因果关系，系统抽成结构图；生成后可继续手改。'}</p>
        <textarea class="cs-ins-text" data-ins-text rows="5">${esc(prefill)}</textarea>
        ${isTable ? '<div class="cs-ins-cols">列数 <select data-ins-cols><option>3</option><option>4</option><option>5</option></select></div>' : ''}
        <div class="cs-ins-actions"><button type="button" class="cs-cp-ghost" data-ins-back>返回</button>
          <button type="button" class="cs-cp-primary" data-ins-run="${kind}">生成</button></div>
        <div class="cs-ins-result" data-ins-result></div>`;
      pickedEl.textContent = '';
    };

    const renderMaterials = (kind) => {
      const list = kind === 'image' ? materials().filter((m) => m.image_url) : materials().filter((m) => /\.(docx|xlsx|xlsm|pdf)$/i.test(m.filename || ''));
      bodyEl.innerHTML = `<div class="cs-ins-actions"><button type="button" class="cs-cp-ghost" data-ins-back>返回</button></div>
        ${list.length
          ? list.map((m) => `<div class="cs-ins-mat">
              <span class="cs-ins-mat-name" title="${esc(m.filename)}">${esc(m.filename)}</span>
              <button type="button" class="cs-ins-chip" ${kind === 'image' ? `data-ins-use-image="${m.id}"` : `data-ins-use-table="${m.id}"`}>插入</button></div>`).join('')
          : `<p class="cs-ins-empty">${kind === 'image' ? '本章还没有带图片的素材' : '本章还没有可抽表的素材'} · <button type="button" class="cs-mat-link" data-ins-goto>去素材页签上传</button></p>`}`;
      pickedEl.textContent = '';
    };

    const showMenu = () => { state.view = 'menu'; renderMenu(); };

    dlg.addEventListener('click', async (event) => {
      const el = event.target;
      if (el.closest('[data-ins-close]') || el.closest('[data-ins-cancel]')) { dlg.close(); return; }
      const tab = el.closest('[data-ins-tab]');
      if (tab) { state.tab = tab.dataset.insTab; for (const b of dlg.querySelectorAll('[data-ins-tab]')) b.setAttribute('aria-pressed', String(b.dataset.insTab === state.tab)); showMenu(); return; }
      if (el.closest('[data-ins-goto]')) { dlg.close(); showPanel('materials'); return; }
      const gen = el.closest('[data-ins-generate]');
      if (gen) { state.view = 'generate'; renderGenerate(gen.dataset.insGenerate); return; }
      const fromMat = el.closest('[data-ins-from-material]');
      if (fromMat) { state.view = 'materials'; renderMaterials(fromMat.dataset.insFromMaterial); return; }
      if (el.closest('[data-ins-back]')) { showMenu(); return; }

      const useImg = el.closest('[data-ins-use-image]');
      if (useImg) {
        const m = materials().find((x) => String(x.id) === useImg.dataset.insUseImage);
        if (m?.image_url) { insertAtCursor(`:::figure ${m.filename}\n![${String(m.filename).replaceAll(']', '')}](${m.image_url})\n:::`); dlg.close(); }
        return;
      }
      const useTbl = el.closest('[data-ins-use-table]');
      if (useTbl) {
        const m = materials().find((x) => String(x.id) === useTbl.dataset.insUseTable);
        dlg.close();
        await insertMaterialTable(useTbl.dataset.insUseTable, m?.filename || '素材');
        return;
      }
      const run = el.closest('[data-ins-run]');
      if (run) {
        const kind = run.dataset.insRun;
        const text = dlg.querySelector('[data-ins-text]')?.value.trim() || '';
        const result = dlg.querySelector('[data-ins-result]');
        if (!text) { result.innerHTML = '<p class="cs-warning">先写一句要生成什么。</p>'; return; }
        run.disabled = true; const label = run.textContent; run.textContent = '生成中…';
        try {
          const url = kind === 'table' ? `/api/blocks/${active.id}/studio/table` : `/api/blocks/${active.id}/studio/diagram`;
          const payload = kind === 'table'
            ? {title: active.title, requirement: text, text: chapterGist(), columns: Number(dlg.querySelector('[data-ins-cols]')?.value || 3)}
            : {text, title: active.title};
          const resp = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: AbortSignal.timeout(120000)});
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          result.innerHTML = `<p class="cs-muted">${kind === 'table' ? '表格草稿' : `节点 ${(data.nodes || []).length} 个`} · 模型 ${esc(data.model || '')}</p>
            <pre class="cs-ins-preview">${esc(data.markdown)}</pre>
            <div class="cs-ins-actions"><button type="button" class="cs-cp-ghost" data-ins-rerun="${kind}">重新生成</button>
              <button type="button" class="cs-cp-primary" data-ins-apply>插入正文</button></div>`;
          result.querySelector('[data-ins-apply]').onclick = () => { insertAtCursor(data.markdown); dlg.close(); };
          result.querySelector('[data-ins-rerun]').onclick = () => run.click();
        } catch (error) {
          result.innerHTML = `<p class="cs-warning">${esc(error.message)}</p>`;
          run.disabled = false; run.textContent = label;
        }
        return;
      }
    });
    renderMenu();
    globalThis.document.body.appendChild(dlg);
    dlg.addEventListener('close', () => dlg.remove());
    dlg.showModal();
  }

  /** 把后端抽取的表格转成 Markdown  /** 把后端抽取的表格转成 Markdown  /** 把后端抽取的表格转成 Markdown（单元格里的 | 转义） */
  function markdownTable(table) {
    const rows = (table.rows || []).filter((r) => (r || []).some((c) => String(c || '').trim()));
    if (!rows.length) return '';
    const cols = Math.max(...rows.map((r) => r.length));
    const fmt = (row) => `| ${Array.from({length: cols}, (_, i) => String(row[i] ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ').trim()).join(' | ')} |`;
    const head = fmt(rows[0]);
    const sep = `| ${Array.from({length: cols}, () => '---').join(' | ')} |`;
    return [head, sep, ...rows.slice(1).map(fmt)].join('\n');
  }

  async function insertMaterialTable(materialId, filename) {
    status('正在抽取表格…');
    let data;
    try {
      const resp = await fetch(`/api/materials/${materialId}/tables`, {signal: AbortSignal.timeout(30000)});
      data = await resp.json();
    } catch (error) {
      status(`表格抽取失败：${error.message}`);
      return;
    }
    const tables = data.tables || [];
    if (!tables.length) { status(data.reason || '该文件里没有可插入的表格'); return; }
    const pick = tables.length === 1 ? tables[0] : tables[0];   // 多表时先取第一张，可在预览里换
    const md = markdownTable(pick);
    if (!md) { status('这张表是空的'); return; }
    insertAtCursor(`**表：来自《${filename}》${tables.length > 1 ? ` · ${pick.name}` : ''}**\n\n${md}`);
    if (tables.length > 1) status(`已插入「${pick.name}」；该文件共 ${tables.length} 张表`);
  }

  /** 在光标处插入，并选中新内容（便于继续编辑 / 撤销） */
  function insertAtCursor(text, { kind = 'text', select = true } = {}) {
    if (writingBusy) { status('AI 正在编制，结束后可手动插入内容。'); return; }
    paintView(false);
    if (live.active) { live.add(kind, text); status('已插入到实时编制区'); return; }
    const start = editor.selectionStart;
    const payload = `\n\n${text}\n\n`;
    editor.setRangeText(payload, start, editor.selectionEnd, 'end');
    // 光标落在插入内容末尾（不选中）：否则下一次"插入"会把刚插入的内容替换掉
    const to = start + 2 + text.length;
    if (select) editor.setSelectionRange(to, to);
    editor.focus();
    changed();
    status('已插入；确认后可继续编辑（⌘Z / Ctrl+Z 撤销）');
  }

  async function confirmComplete(check){
    if(check.blockId!==active.id){status('章节已切换，请重新核对。');return;}
    const updated=await json(`/api/blocks/${active.id}/studio`,send('PUT',{layout:context.profile.layout,writing_status:'complete',
      acknowledge_open_items:true,expected_content_hash:check.content_hash}));
    context.profile={...context.profile,...updated};profile[active.id]=context.profile;
    get('chapter-head').querySelector('span').textContent='已核对完成';
    paintTree();stats();get('completion').close();pendingCompletion=null;
    status('本章已确认完成；修改正文后将退回待核对。');
  }
  // TODO(挂起·编写模块): 技术规范书**关键要求（★标星 / mandatory）**需在「本章编制依据」里
  // 置顶并标星、给出计数。业务口径：挂载是"编写时的参考依据"，不参与目录生成。
  // 详见 docs/design/原型到代码-落点对照.md 「四、【挂起·待办】编写模块：技术规范书关键要求」。
  const paintAside = () => {
    profile[active.id]=context.profile;paintTree();stats();paintTodoBadge();
    const layout = context.layouts[context.profile.layout];
    const basisKey = String(active.block_id);
    const basisCovered = basisInfo?.block_id === basisKey ? basisInfo : null;
    get('aside').innerHTML = `${todoPaneHtml()}
      <section data-pane="materials">${(() => { const gaps = planBrief?.gaps || []; const lackTotal = gaps.reduce((sum, g) => sum + (g.lack || 0), 0); const title = gaps.length ? `素材缺口 <small>缺 ${gaps.length} 类 · 共 ${lackTotal} 项</small>` : `素材缺口 <small>没有缺口</small>`; return `<h4>${title}</h4>${gaps.length ? gapGroupsHtml() : `<p class="cs-mat-empty">计划要求的素材已齐，可直接生成。</p>`}`; })()}
      <details class="cs-mat-manage">
        <summary>已关联素材与外部依据</summary>
        <div class="cs-mat-manage-body">
        ${(() => {
        const key = String(active.block_id);
        const body = String(active.content || '');
        // 来源"有没有被正文用到"：完整标题（去站点后缀、≥12 字）或 URL 出现在正文里（前 10 字会误判）
        const norm = (text) => String(text || '').split('_')[0].replace(/\s+/g, '');
        const usedOf = (c) => {
          const k = norm(c.title);
          if (k.length >= 12 && norm(body).includes(k)) return true;
          return !!(c.url && body.includes(String(c.url)));
        };
        const linked = citationInfo.rows.filter((c) => (c.links || []).includes(key) || !c.block_id);
        const unused = linked.filter((c) => !usedOf(c));
        return `<div class="cs-material-head"><h4>外部依据 <b>${linked.length}</b>${linked.length ? `<small>已引用 ${linked.length - unused.length} · 未引用 ${unused.length}</small>` : ''}</h4>
            <button type="button" class="cs-mat-add" data-add-citation="${esc(key)}">添加官方来源</button></div>
          ${linked.map((c) => {
            const segmentCount = (c.segments || []).length;
            const selectedCount = (c.selected || []).length;
            const used = usedOf(c);
            return `<div class="cs-mat-card">
                <div class="cs-mat-card-top">
                  <p class="cs-mat-title">${esc(c.title || c.url)}</p>
                  <span class="cs-mat-tag ${used ? 'ok' : ''}">${used ? '已引用' : '未引用'}</span>
                </div>
                <p class="cs-mat-meta">${esc(c.publisher || c.domain || '')}${c.published_at ? ` · ${esc(c.published_at)}` : ''} · 关联 ${(c.links || []).length} 章</p>
                <p class="cs-mat-acts">
                  <button type="button" class="cs-mat-link" data-cite-segments="${esc(c.id)}">${segmentCount ? `片段 ${segmentCount}${selectedCount ? `（已选 ${selectedCount}）` : ''}` : '提取片段'}</button>
                  ${c.url ? `<a class="cs-mat-link" href="${esc(c.url)}" target="_blank" rel="noreferrer">打开原文</a>` : ''}
                </p></div>`;
          }).join('')
            || '<p class="cs-mat-empty">本章还没有关联外部依据。外部信息必须可核验，二手内容不采。</p>'}
          ${unused.length ? '<p class="cs-mat-warn">这些来源还没被正文引用：重新生成本章即可用上。</p>' : ''}
          <div class="cs-material-head cs-material-head--sub"><h4>本章素材 <b>${context.materials.length}</b></h4>
            <label class="cs-mat-add">上传素材<input data-cs="upload" type="file" hidden /></label></div>
          ${context.materials.length
            ? context.materials.map((m) => `<div class="cs-mat-card">
                <p class="cs-mat-title">${esc(m.filename)}</p>
                <p class="cs-mat-meta">${esc(m.parse_status || '待解析')} · ${Math.max(1, Math.round((m.file_size || 0) / 1024))} KB${m.confirmed ? ' · 已核对' : ''}</p>
                <p class="cs-mat-acts">
                  ${m.parse_status === 'done' ? `<button type="button" class="cs-mat-link" data-material="insert" data-id="${m.id}">${m.image_url ? '插入图片' : '引用原文摘录'}</button>` : ''}
                  <button type="button" class="cs-mat-link" data-material="confirm" data-id="${m.id}">${m.confirmed ? '取消核对' : '标记已核对'}</button>
                  <button type="button" class="cs-mat-link" data-material="unlink" data-id="${m.id}">移除关联</button>
                </p></div>`).join('')
            : '<p class="cs-mat-empty">还没有关联素材。可上传 PDF / Word（自动提取原文与页码），或从项目素材库关联。</p>'}
          <p class="cs-mat-acts">
            ${context.materials.length ? '<button type="button" class="cs-mat-link" data-refresh-materials>刷新解析状态</button>' : ''}
          </p>`;})()}        </div>
      </details>
      </section>
      `;
    const asideLayout = get('layout');      // 右栏「编排建议」已移除（章节类型移入计划面板），这里可能为 null
    if (asideLayout) asideLayout.onchange = async event => {
      const previous = context.profile.layout;
      try {
        const updated=await json(`/api/blocks/${active.id}/studio`,send('PUT',{layout:event.target.value,writing_status:context.profile.writing_status}));
        context.profile={...context.profile,...updated}; paintAside();
      } catch(error) { event.target.value=previous;status(error.message); }
    };
    get('upload').onchange = async event => {
      const block=active;
      for (const file of event.target.files) {
        status(`正在上传 ${file.name}…`);
        try { const form=new FormData();form.append('file',file);await json(`/api/blocks/${block.id}/studio/materials`,{method:'POST',body:form}); }
        catch(error) { status(`${file.name}：${error.message}`); return; }
      }
      if(active.id===block.id) { context=await json(`/api/blocks/${block.id}/studio`);paintAside(); }
      status('素材已关联本章；文档后台解析后，核对摘录即可用于编写。');
    };
  };
  /** 工具栏「编写计划」：只在当前章属于某个一级目录时出现（动作与树无关，树只看结构）。 */
  function paintPlanAction(block) {
    const slot = host.querySelector('[data-plan-slot]');
    if (!slot) return;
    const unit = block ? unitOf.get(String(block.block_id)) : null;
    const info = unit ? planUnits.get(unit) : null;
    if (!info) { slot.innerHTML = ''; return; }
    slot.innerHTML = `<button type="button" class="cs-plan-btn" data-plan="${esc(unit)}" `
      + `title="本章计划：要交什么 / 依据与写法 —— 点开查看">本章计划</button>`;
  }

  async function select(id) {
    if(live.active&&live.pending){live.reset();dirty=false;}
    if (!await save()) return;
    const block=blocks.find(b=>String(b.block_id)===String(id) || String(b.id)===String(id));
    if (!block) return;
    const token=++selecting;
    status('正在加载本章依据与素材…');
    try {
      const data=await json(`/api/blocks/${block.id}/studio`);
      if(disposed || token!==selecting) return;
      get('completion').close();pendingCompletion=null;
      live.reset();previewVisible=false;
      active=block;context=data;dirty=false;
      profile[block.id]=data.profile;
      await loadBasis(block.block_id);   // 依据 ↔ 正文对应（评分点/条款是否已回应）
      editor.value=block.content||''; editor.hidden=false;get('preview').hidden=true;paintView(false);
      const headState = stateOf(block);
      // 「目录」是结构节点标记，不是编写状态 —— 不在章头显示（用户反馈）
      get('chapter-head').innerHTML=`<h3>${esc(block.title)}</h3><span class="cs-chapter-state">${headState === '目录' ? '' : esc(headState)}</span>${budgetLineHtml(block)}<span class="cs-plan-slot" data-plan-slot></span>`;
      paintPlanAction(block);
      host.querySelector('[data-action="save"]').disabled=true;
      let local;try{local=JSON.parse(sessionStorage.getItem(draftKey(block))||'null');}catch{local=null;}
      const recovery=get('recovery');recovery.hidden=true;
      if(localDraftAction(local,block.content||'')!=='none'){
        if(localDraftAction(local,block.content||'')==='restore'){
          editor.value=local.content;changed();get('chapter-head').querySelector('span').textContent='未保存 · 已恢复本地修改';
        }else{
          recovery.hidden=false;
          recovery.innerHTML='<span>服务器正文已更新，另有本地草稿。</span><details><summary>处理版本冲突</summary><button data-action="restore">载入本地草稿</button><button data-action="discard">保留服务器正文</button></details>';
        }
      }
      paintTree();paintAside();stats();status('');team?.refresh();
    } catch(error) { status(error.message); }
  }
  get('search').oninput=paintTree;
  editor.oninput=changed;
  const paintFormat = () => {
    const preset=document.templates[document.template];
    if(!preset)return;
    // 「Word 导出格式」折叠块已移除：这些节点可能不存在，全部可选链，避免整台编制器崩掉
    const summary=get('format-summary');if(summary)summary.textContent=`A4 · ${preset.font} ${preset.size} 磅 · 1.5 倍行距 · 四边 ${preset.margin} cm`;
    const version=get('format-version');if(version)version.textContent=`模板版本 ${preset.version.slice(0,12)} · 导出任务固定内容、图片与模板版本`;
    const sample=get('format-sample');if(sample)sample.href=`/api/studio/templates/${document.template}/sample?format=pdf`;
    const download=get('format-download');if(download)download.href=`/api/studio/templates/${document.template}/sample?format=docx`;
  };
  paintFormat();
  const templateSelect=get('template');if(templateSelect)templateSelect.onchange=async event=>{
    try { await json(`/api/projects/${projectId}/studio/document`,send('PUT',{thread_id:threadId,template:event.target.value}));document.template=event.target.value;paintFormat();status('交付格式已保存，导出时应用；已生成文件保留原版本。'); }
    catch(error) {event.target.value=document.template;status(error.message);}
  };
  host.addEventListener('toggle', (event) => {
    if (event.target.classList?.contains('cs-insert') && event.target.open) renderInsertMenu();
  }, true);
  host.onclick = async event => {
    const panel=event.target.closest('.cs-inspector-tabs [data-panel]');
    if(panel){showPanel(panel.dataset.panel);return;}
    const asidePlan=event.target.closest('[data-aside-open-plan]');
    if(asidePlan){await openChapterPlan(asidePlan.dataset.asideOpenPlan);return;}
    const segButton=event.target.closest('[data-cite-segments]');
    if(segButton){openCitationSegments(segButton.dataset.citeSegments);return;}
    const citeButton=event.target.closest('[data-add-citation]');
    if(citeButton){openCitationDialog(citeButton.dataset.addCitation);return;}
    const specLink=event.target.closest('[data-spec]');
    if(specLink){globalThis.document.dispatchEvent(new CustomEvent('writing-specs:open',{detail:{layout:specLink.dataset.spec}}));return;}
    const planButton=event.target.closest('[data-plan]');
    if(planButton){await openChapterPlan(String(active?.block_id || ''));return;}
    const chapter=event.target.closest('[data-chapter]');
    if(chapter) {await select(chapter.dataset.chapter);return;}
    const asideTodo=event.target.closest('[data-aside-todo]');
    if(asideTodo && active){
      const action=asideTodo.dataset.asideTodo;
      if(action==='find'){openCitationDialog(String(active.block_id), asideTodo.dataset.gapKind || '');return;}
      if(action==='condense'){status('篇幅超额：可删减重复论述，或让 AI 重新精简本章');return;}
      // regen：直接触发本章重编
      host.querySelector('[data-action="ai"]')?.click();
      return;
    }
    const gapSearch=event.target.closest('[data-gap-search]');
    if(gapSearch && active){openCitationDialog(String(active.block_id), gapSearch.dataset.gapSearch || '');return;}
    const gapUpload=event.target.closest('[data-gap-upload]');
    if(gapUpload && active){
      showPanel('materials');
      globalThis.setTimeout(()=>host.querySelector('[data-cs="upload"]')?.click(), 200);
      return;
    }
    const refreshMat=event.target.closest('[data-refresh-materials]');
    if(refreshMat && active){
      try{context=await json(`/api/blocks/${active.id}/studio`);paintAside();status('已刷新素材解析状态');}
      catch(error){status(error.message);}
      return;
    }
    const material=event.target.closest('[data-material]');
    if(material && active) {
      const m=context.materials.find(x=>String(x.id)===material.dataset.id), block=active;
      try {
        if(material.dataset.material==='insert') {
          insert(m.image_url?`![${m.filename.replaceAll(']','')}](${m.image_url})`:`> 摘自《${m.filename}》${m.excerpts[0]?.page?'第 '+m.excerpts[0].page+' 页':''}\n\n${m.excerpts[0]?.text||''}`);
          return;
        }
        await json(`/api/blocks/${block.id}/studio/materials${material.dataset.material==='unlink'?'/'+m.id:''}`,
          material.dataset.material==='unlink'?{method:'DELETE'}:send('PUT',{material_id:m.id,confirmed:!m.confirmed}));
        if(active.id===block.id){context=await json(`/api/blocks/${block.id}/studio`);paintAside();}
      } catch(error){status(error.message);}
      return;
    }
    const inMenu=event.target.closest('.cs-insert-menu, .cs-more-menu');
    if(inMenu) event.target.closest('details')?.removeAttribute('open');
    // 素材 / 官方来源 插入
    const matBtn=event.target.closest('[data-ins-material]');
    if(matBtn){
      const m=(context?.materials||[]).find(x=>String(x.id)===matBtn.dataset.insMaterial);
      const ex=(m?.excerpts||[])[0];
      if(ex){ const page=ex.page?`（第 ${ex.page} 页）`:''; insertAtCursor(`> 摘自《${m.filename}》${page}\n${ex.text||ex.content||''}`); }
      else status('该素材尚未解析出可引用原文');
      return;
    }
    if(event.target.closest('[data-open-insert]')){openInsertDialog();return;}
    const tblBtn=event.target.closest('[data-ins-table]');
    if(tblBtn){
      const m=(context?.materials||[]).find(x=>String(x.id)===tblBtn.dataset.insTable);
      await insertMaterialTable(tblBtn.dataset.insTable, m?.filename || '素材');
      return;
    }
    const imgBtn=event.target.closest('[data-ins-image]');
    if(imgBtn){
      const m=(context?.materials||[]).find(x=>String(x.id)===imgBtn.dataset.insImage);
      if(m?.image_url){ insertAtCursor(`:::figure ${m.filename}\n![${String(m.filename).replaceAll(']','')}](${m.image_url})\n:::`); }
      else status('该素材没有可插入的图片');
      return;
    }
    const segBtn=event.target.closest('[data-ins-segment]');
    if(segBtn){
      const [cid, idxRaw]=String(segBtn.dataset.insSegment).split(':');
      const c=(citationInfo?.rows||[]).find(x=>String(x.id)===cid);
      const seg=(c?.segments||[])[Number(idxRaw)];
      if(seg){
        const body=String(seg.content||seg.heading||'').trim();
        const src=`${c.publisher||c.domain||''}${c.published_at?` · ${c.published_at}`:''}`;
        insertAtCursor(`> ${body}\n\n【来源：${c.title||c.url}${src?`（${src}）`:''}】`);
      } else status('这段片段为空，换个片段试试');
      return;
    }
    const button=event.target.closest('[data-action]');
    if(!button || !active) return;
    const action=button.dataset.action;
    host.querySelector('.cs-more').removeAttribute('open');
    try {
      if(action==='save') await save();
      if(action==='quality'){openQualityDialog();return;}
      if(action==='diagram'){openDiagramDialog();return;}
      if(action==='ai'){
        // 生成闸：本章所属一级目录还没有"已确认"的编写计划 → 先看计划、确认后再生成。
        // 否则「AI 编制本章」会先跑起来，计划闸形同虚设（产品评审结论）。
        const unit = active ? unitOf.get(String(active.block_id)) : null;
        const info = unit ? planUnits.get(unit) : null;
        paintView(false);status('');showPanel('ai');await team.start();
      }
      if(action==='edit'){
        paintView(false);get('preview').hidden=true;
        if(live.active)live.show();else editor.hidden=false;
        status('');team.refresh();
      }
      if(action==='preview'){
        if(previewVisible)return;
        const block=active,content=editor.value,request=++viewRequest;
        button.disabled=true;status('正在排版预览…');
        try{
          const result=await json(`/api/blocks/${block.id}/studio/content-preview`,send('POST',{content}));
          if(request!==viewRequest)return;
          if(active.id!==block.id||editor.value!==content){status('内容已更新，请再次点击预览。');return;}
          get('preview').innerHTML='<p class="cs-preview-note">正文预览 · Word 的实际分页请查看导出排版预览</p>'+result.html;
          get('preview').hidden=false;editor.hidden=true;live.hide();paintView(true);
          status('');
        }finally{button.disabled=false;}
      }
      if(action==='text') insert('');
      if(action==='table') insert('| 要求／工作项 | 响应方案 | 验证／证明 |\n| --- | --- | --- |\n| 【待补充：要求】 | 【待补充：响应】 | 【待补充：依据】 |','table');
      if(action==='diagram') insert(':::diagram 技术关系图（请按实际方案修改）\n用户接入层 -> 应用服务层 -> 数据服务层\n:::','diagram');
      if(action==='restore'){live.reset();paintView(false);editor.value=JSON.parse(sessionStorage.getItem(draftKey(active))).content;get('recovery').hidden=true;changed();}
      if(action==='discard'){sessionStorage.removeItem(draftKey(active));get('recovery').hidden=true;}
      if(action==='refresh-materials'){context=await json(`/api/blocks/${active.id}/studio`);paintAside();status('素材状态已更新');}
      if(action==='library') {
        const materials=await json(`/api/projects/${projectId}/materials`);
        get('library').innerHTML=materials.filter(m=>m.role==='source'&&!context.materials.some(x=>x.id===m.id)).map(m=>`<button data-action="link" data-id="${m.id}">${esc(m.filename)}</button>`).join('')||'<p class="cs-muted">没有可关联素材，可直接上传。</p>';
      }
      if(action==='link'){await json(`/api/blocks/${active.id}/studio/materials`,send('PUT',{material_id:Number(button.dataset.id)}));context=await json(`/api/blocks/${active.id}/studio`);paintAside();}
      if(action==='complete') {
        const blockId=active.id;
        button.disabled=true;
        try{
          if(writingBusy||(live.active&&live.pending)){status('AI 编制或图表尚未完成，请结束后再确认。');return;}
          if(!await save())return;
          if(active.id!==blockId)return;
          const check=await json(`/api/blocks/${blockId}/studio/completion-check`);
          if(active.id!==blockId)return;
          if(check.running){status('AI 编制尚未结束，请完成或停止后再核对。');return;}
          if(check.empty){status('正文为空，请先编写后确认完成。');return;}
          pendingCompletion={...check,blockId:active.id};
          if(!check.gaps.length&&!check.issues.length){await confirmComplete(pendingCompletion);return;}
          get('completion-summary').innerHTML=`<h3>确认本章完成</h3>${(() => {
        // 真实检查：预算页数来自 /page-budget，已写字数来自正文（不编造）
        const c = budgetOf(active), words = wordsOf(active), pages = pagesOf(active);
        if (!c || !Number(c.pages)) return '';
        const gap = Number(c.pages) - pages;
        return `<p class="${gap <= 0.1 ? 'cs-muted' : 'cs-warning'}">字数达标：预算 ${Number(c.pages)} 页 · 已写 ${pages.toFixed(1)} 页（${words} 字）${gap <= 0.1 ? ' ✅ 已达标' : ` ⚠ 差 ${gap.toFixed(1)} 页`}</p>`;
      })()}<p>本章已保存。请核对以下内容，再决定是否标记完成。</p>${check.gaps.length?`<details><summary>待补充 ${check.gaps.length} 项 · 查看明细</summary><ul>${check.gaps.map(g=>`<li>${esc(g)}</li>`).join('')}</ul></details>`:''}${check.issues.length?`<details><summary>AI 检查意见 ${check.issues.length} 项 · 查看明细</summary><ul>${check.issues.map(i=>`<li>${esc(i.point)}${i.suggestion?`：${esc(i.suggestion)}`:''}</li>`).join('')}</ul></details>`:''}<p class="cs-muted">${esc(check.notice)} 确认仅表示人工核对状态，不会自动修复问题。</p>`;
          get('completion').showModal();
        }finally{button.disabled=false;}
      }
      if(action==='completion-cancel'){get('completion').close();pendingCompletion=null;}
      if(action==='completion-confirm'&&pendingCompletion){
        button.disabled=true;try{await confirmComplete(pendingCompletion);}finally{button.disabled=false;}
      }
      if(action==='export-chapter'||action==='export-group') {
        if(!await save())return;
        const scope=action==='export-chapter'?'chapter':'group';
        const selected=exportScope(blocks,active.id,scope);
        status(`正在提交 ${selected.length} 个目录节点的已保存版本…`);
        await exportDocument({projectId,threadId,template:document.template,scope,blockId:active.id,slot:get('export-status')});
      }
    } catch(error){status(error.name==='TimeoutError'?'请求超时，原正文保留，请重试。':error.message);}
  };
  const beforeUnload = event => {if(dirty){event.preventDefault();event.returnValue='';}};
  window.addEventListener('beforeunload',beforeUnload);
  const controller={host,select,dispose(){disposed=true;team.dispose();window.removeEventListener('beforeunload',beforeUnload);},save};
  const team=mountChapterTeam(get('team'),{projectId,threadId,getActive:()=>active,getModel,beforeStart:save,
    onState:job=>{const button=host.querySelector('[data-action="ai"]');const running=!!job&&['queued','running'].includes(job.status);writingBusy=running;host.querySelector('[data-add-menu]').hidden=previewVisible||running;button.disabled=running;button.textContent=running?'编制中…':job?.status==='done'?'重新编制本章':'AI 编制本章';},
    getPreparation:()=>context,
    onProgress:job=>{
      if(disposed)return;
      if(active?.id!==job.block_id||previewVisible)return;
      if(!live.isActiveFor(job.id)&&(!get('recovery').hidden||dirty||(active.content||'')!==job.base_content))return;
      if(live.update(job))status(job.status==='done'?'协作编制已完成，人工修改已保留；请核对后保存。':'Agent 协作编制中 · 可编辑已完成内容，图表完成后原位替换。');
    },
    onResult:async(job,{automatic=false,base}={})=>{
      if(disposed||previewVisible)return false;
      if(automatic && active?.id!==job.block_id)return false;
      if(automatic&&!get('recovery').hidden)return false;
      if(live.isActiveFor(job.id)){live.update(job);status('协作编制已完成，请核对图文表与检查意见后保存正文。');return true;}
      if(!canDisplayResult({dirty,automatic,activeId:active?.id,blockId:job.block_id,currentContent:active?.content||'',base})){
        status('AI 编制已完成，你的编辑已保留。请先保存当前修改，再从 AI 编制中打开结果。');return false;
      }
      if(active?.id!==job.block_id)await select(job.block_id);
      if(active?.id!==job.block_id)return false;
      if(job.blocks?.length){live.update(job);status('协作内容已在正文中打开，请核对后保存。');return true;}
      editor.value=job.content;editor.hidden=false;get('preview').hidden=true;
      paintView(false);
      changed();status('AI 内容已显示在正文编辑区 · 尚未保存，请核对后保存正文。');
      return true;
    }
  });
  currentStudio=controller;
  paintTree();stats();
  get('jobs').innerHTML=document.jobs.map(j=>`<p data-job="${esc(j.id)}"></p>`).join('')||'<p class="cs-muted">暂无导出任务</p>';
  for(const job of document.jobs) monitorExport(projectId,job.id,get('jobs').querySelector(`[data-job="${job.id}"]`));
  await select((blocks.find(b=>units.has(b.id)&&!b.content?.trim())||blocks[0])?.id);
  return controller;
}
