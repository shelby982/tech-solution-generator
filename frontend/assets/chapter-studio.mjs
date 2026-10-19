import {mountLiveWriting} from './chapter-live.mjs?v=20261016-v252';
import {mountChapterTeam,canDisplayResult} from './chapter-team.mjs?v=20261016-v252';
import {specContentHtml} from './spec-content.mjs?v=20261016-v252';

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
    <div class="cs-document-head"><div hidden><strong>章节编写</strong><span data-cs="progress"></span></div><details class="cs-delivery"><summary>Word 导出格式</summary>    <div class="cs-document"><label>交付格式 <select data-cs="template">${Object.entries(document.templates).map(([key,t])=>`<option value="${key}" ${key===document.template?'selected':''}>${esc(t.label)}</option>`).join('')}</select></label><span class="cs-muted" data-cs="format-summary"></span><a data-cs="format-sample" target="_blank" rel="noopener">预览样张</a><a data-cs="format-download">Word 样张</a><details class="cs-format-rules"><summary>规则与应用时机</summary><p>编写前确认招标规定的目录与固定表单；本设置在导出时应用，不改变正文。内置格式不包含企业封面或招标指定表单。</p><p>统一正文与标题样式、图题、页码和跨页表头。实际分页以导出排版预览为准；Word 与 LibreOffice 的字体和分页可能存在差异。</p><p data-cs="format-version"></p></details></div>
</details></div>
    <div class="cs-workspace">
      <aside class="cs-tree"><div class="cs-tree-label">应答目录</div><input type="search" data-cs="search" placeholder="搜索章节" aria-label="搜索章节"><div data-cs="tree"></div></aside>
      <main class="cs-main">
        <div class="cs-chapter-heading" data-cs="chapter-head"></div>
        <div class="cs-toolbar"><button data-action="ai" class="cs-primary" title="启动本章编制；在右侧查看进度">AI 编制本章</button><button data-action="save" disabled>保存</button><div class="cs-view-switch" role="group" aria-label="正文显示方式"><button data-action="edit" aria-pressed="true">编辑</button><button data-action="preview" aria-pressed="false">预览</button></div><button type="button" data-action="diagram" title="把一段文字抽成结构图，插入正文">文字生成图</button><details class="cs-more"><summary>更多</summary><div class="cs-more-menu"><details data-add-menu><summary>添加内容</summary><button data-action="text">文字说明</button><button data-action="table">手动表格</button><button data-action="diagram">手动关系图</button></details><button data-action="complete">确认本章完成</button><button data-action="export-chapter">导出本章 Word</button><button data-action="quality">质检清单</button><button data-action="export-group">导出本组 Word</button></div></details></div>
        <div data-cs="status" role="status" aria-live="polite"></div>
        <div data-cs="recovery" hidden></div>
        <textarea class="cs-editor" data-cs="editor" aria-label="本章正文" placeholder="点击上方「AI 编制」生成本章内容，也可直接在此编写。生成结果将显示在这里；核对后点击「保存正文」。"></textarea>
        <div class="cs-live-writing" data-cs="live" hidden></div>
        <div class="cs-preview" data-cs="preview" hidden></div>
        <dialog class="cs-completion" data-cs="completion"><div data-cs="completion-summary"></div><div class="cs-completion-actions"><button data-action="completion-cancel">继续编辑</button><button data-action="completion-confirm">已核对，确认本章完成</button></div></dialog><div data-cs="export-status" role="status"></div>
        <details class="cs-history"><summary>最近导出任务</summary><div data-cs="jobs"></div></details>
      </main>
      <aside class="cs-aside cs-inspector" data-cs="inspector" data-panel="basis"><nav class="cs-inspector-tabs" aria-label="本章助手"><button data-panel="basis" aria-pressed="true">依据</button><button data-panel="materials" aria-pressed="false">素材</button><button data-panel="ai" aria-pressed="false">编制进度</button></nav><div data-cs="aside"></div><div class="cs-team" data-cs="team"></div></aside>
    </div>`;
  const get = key => host.querySelector(`[data-cs="${key}"]`);
  const editor = get('editor');
  const showPanel = panel => {
    get('inspector').dataset.panel=panel;
    for(const button of host.querySelectorAll('.cs-inspector-tabs button'))button.setAttribute('aria-pressed',String(button.dataset.panel===panel));
    if(window.innerWidth<=980)get('inspector').scrollIntoView({behavior:'smooth',block:'start'});
  };
  const status = message => { get('status').textContent = message; };
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
  async function openQualityDialog() {
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog';
    dialog.innerHTML = `<header><h3>质检清单</h3><button type="button" aria-label="关闭">×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">正在检查…</p></div>`;
    globalThis.document.body.append(dialog);
    dialog.querySelector('header button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
    const body = dialog.querySelector('.cs-plan-body');
    try {
      const resp = await fetch(`/api/projects/${projectId}/quality?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(60000)});
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      const rows = (data.chapters || []).map((row) => `<section class="cs-qc-row ${row.ok ? 'ok' : 'issue'}" data-qc-block="${esc(String(row.block_id))}">
          <header><b>${esc(row.title)}</b><span class="cs-dim">${esc(row.layout || '')} · ${row.stats.tables} 表 · ${row.stats.figures} 图 · ${row.stats.pages} 页</span></header>
          ${row.issues.length ? `<ul>${row.issues.map((issue) => `<li class="${esc(issue.level)}">${esc(issue.message)}</li>`).join('')}</ul>` : '<p class="cs-dim">通过</p>'}
        </section>`).join('');
      const unitRows = (data.units || []).map((unit) => `<section class="cs-qc-row ${unit.ok ? 'ok' : 'issue'}" data-qc-block="${esc(String(unit.block_id))}">
          <header><b>【章】${esc(unit.title)}</b><span class="cs-dim">${esc(unit.layout || '')} · ${unit.chapters} 节 · ${unit.stats.tables} 表 · ${unit.stats.figures} 图 · ${unit.stats.pages} 页</span></header>
          ${unit.issues.length ? `<ul>${unit.issues.map((issue) => `<li class="${esc(issue.level)}">${esc(issue.message)}</li>`).join('')}</ul>` : '<p class="cs-dim">结构通过</p>'}
        </section>`).join('');
      body.innerHTML = `<p class="cs-muted">共 ${data.summary.chapters} 个编写单元（已开始 ${data.summary.started || 0}）· 通过 ${data.summary.clean} · 高优 ${data.summary.high} · 中优 ${data.summary.medium}
        ${data.summary.units ? `· 章级 ${data.summary.units} 个（问题 ${data.summary.unit_issues || 0}）` : ''}
        （只出清单，不阻断保存；点击一行跳到该章节处理）</p>${unitRows}${rows || '<p class="cs-dim">还没有可检查的章节</p>'}`;
      body.querySelectorAll('[data-qc-block]').forEach((section) => section.addEventListener('click', async () => {
        dialog.close();
        await select(section.dataset.qcBlock);
      }));
    } catch (err) {
      body.innerHTML = `<p class="cs-warning">质检失败：${esc(String(err.message || err))}</p>`;
    }
  }

  /** 官方来源（V2）：一个输入框搞定 —— 输入检索词→自动找；粘贴链接→直接核验。
   *  操作上的取舍：合并两个入口、候选行内预览+接受、说明折叠、入口常驻、接受后有回执。 */
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
        <details class="cs-cite-rule"><summary>来源规则</summary>
          只采政府、南方电网/广东电网、标准发布机构、招标方与公共资源交易平台的<strong>原文</strong>；
          二手转载、公众号、百科不采；找不到官方源时正文写【待补充：需官方来源】。
        </details>
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
    const accept = async (candidate, row) => {
      const button = row.querySelector('[data-cite-accept]');
      button.disabled = true;
      try {
        await post(`/api/projects/${projectId}/citations`, {...candidate, block_id: blockKey, thread_id: threadId});
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
        const html = `<div class="cs-cite-card">
            <h4>${esc(candidate.title)}</h4>
            <p class="cs-muted">${esc(candidate.publisher || candidate.domain)} · ${esc(candidate.published_at || '日期待补')}</p>
            <p>${esc(candidate.excerpt || '')}</p>
            <p class="cs-dim">${esc(candidate.url)}</p>
            <div class="cs-plan-actions"><button type="button" class="mw-btn primary" data-cite-accept>接受为引用</button></div>
            <p class="cs-dim" data-cite-msg></p></div>`;
        if (preview) { preview.innerHTML = html; preview.querySelector('[data-cite-accept]').onclick = () => void accept(candidate, row); }
        else { panel.innerHTML = html; panel.querySelector('[data-cite-accept]').onclick = (event) => void accept(candidate, panel); }
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
        const data = await post(`/api/projects/${projectId}/citations/discover`, {query});
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
    markAccepted();
    if (initialQuery && !isUrl(initialQuery)) void discover();
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

  async function openWritingPlan(unitBlockId) {
    if (!unitBlockId) return;
    const dialog = globalThis.document.createElement('dialog');
    dialog.className = 'cs-plan-dialog';
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

  const kindLabel = {text: '正文', table: '表格', diagram: '图示'};
    body.innerHTML = `<p class="cs-muted">《${esc(plan.root_title)}》共 ${plan.chapters.length} 个章节。确认后按章节并发生成；
       主编会按此计划执行（不再重新规划）。</p>`
      + plan.chapters.map((c) => `<section class="cs-plan-chapter">
          <h4>${esc(c.title)} <em>${esc(c.layout_label)}${c.pages ? ` · ${c.pages} 页` : ''}</em></h4>
          <p>${c.tasks.map((t) => `<span class="cs-plan-task ${esc(t.kind)}">${kindLabel[t.kind] || t.kind} · ${esc(t.title)}</span>`).join('')}</p>
          ${c.gaps.length ? `<div class="cs-plan-gap"><p class="cs-warning">素材缺口：${esc(c.gaps.join('；'))}</p>
            <div class="cs-plan-gap-actions"><label class="mw-btn cs-plan-upload">上传素材
              <input type="file" hidden data-plan-upload="${esc(String(c.id || ''))}" /></label>
              <button type="button" class="mw-btn" data-plan-goto="${esc(String(c.block_id))}">去本章编写</button>
              <button type="button" class="mw-btn" data-plan-cite="${esc(String(c.block_id))}"
                data-plan-cite-query="${esc((c.gaps || []).join('；'))}">检索官方来源</button></div>
            <p class="cs-dim" data-plan-upload-msg></p></div>` : ''}
        </section>`).join('')
      + `${plan.gaps.length ? `<p class="cs-muted">全章素材缺口 ${plan.gaps.length} 项</p>` : ''}`
      + `<div class="cs-plan-actions"><button type="button" class="mw-btn" data-plan-cancel>取消</button>
           <button type="button" class="mw-btn primary" data-plan-confirm>确认并开始编制</button></div>`;
    body.querySelector('[data-plan-cancel]').onclick = () => dialog.close();
    body.querySelectorAll('[data-plan-upload]').forEach((input) => input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const card = input.closest('.cs-plan-gap');
      const msg = card ? card.querySelector('[data-plan-upload-msg]') : null;
      const form = new FormData();
      form.append('file', file);
      try {
        const resp = await fetch(`/api/blocks/${input.dataset.planUpload}/studio/materials`, {
          method: 'POST', body: form, signal: AbortSignal.timeout(180000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        if (msg) { msg.textContent = `已上传《${file.name}》并关联到本章`; msg.classList.remove('cs-warning'); }
      } catch (err) {
        if (msg) { msg.textContent = `上传失败：${String(err.message || err)}`; msg.classList.add('cs-warning'); }
      }
    }));
    body.querySelectorAll('[data-plan-cite]').forEach((button) => button.addEventListener('click', () => {
      openCitationDialog(button.dataset.planCite, button.dataset.planCiteQuery || '');
    }));
    body.querySelectorAll('[data-plan-goto]').forEach((button) => button.addEventListener('click', async () => {
      dialog.close();
      await select(button.dataset.planGoto);
    }));
    body.querySelector('[data-plan-confirm]').onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = '启动中…';
      try {
        const resp = await fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(plan.id)}/confirm`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({leaf_block_ids: []}),
          signal: AbortSignal.timeout(60000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        dialog.close();
        status(`已启动 ${data.chapters} 个章节的编制，可在右栏查看进度`);
        team?.refresh();
      } catch (err) {
        button.disabled = false;
        button.textContent = '确认并开始编制';
        body.insertAdjacentHTML('afterbegin', `<p class="cs-warning">启动失败：${esc(String(err.message || err))}</p>`);
      }
    };
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
    host.querySelector('[data-add-menu]').hidden=preview||writingBusy;
  };
  const insert = (text,kind='text') => {
    if(writingBusy){status('AI 正在编制，结束后可手动添加内容。');return;}
    paintView(false);
    if(live.active){live.add(kind,text);return;}
    editor.setRangeText(`\n\n${text}\n\n`,editor.selectionStart,editor.selectionEnd,'end');
    changed();editor.focus();
  };
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
    profile[active.id]=context.profile;paintTree();stats();
    const layout = context.layouts[context.profile.layout];
    get('aside').innerHTML = `<section data-pane="basis"><h4>本章编制依据</h4>${context.points.map(p=>`<details><summary>${esc(p.item||p.factor)} · ${p.points ?? '—'} 分</summary><p>${esc(p.criterion)}</p></details>`).join('') || '<p class="cs-muted">本章未直接关联评分点。</p>'}${context.requirements.length?`<details><summary>规范要求 ${context.requirements.length} 条 · 查看原文</summary>${context.requirements.map(r=>`<p>${esc(r.text)}<small>${esc(r.source_file||'')}</small></p>`).join('')}</details>`:''}${context.source.raw_content?`<details><summary>本章原文内容</summary>${specContentHtml(context.source.raw_content)}</details>`:''}</section>
      ${(() => {
        const mine = citationInfo.rows.filter((c) => !c.block_id || c.block_id === String(active.block_id));
        const rows = mine.slice(0, 5).map((c) => `<p class="cs-cite-line">${esc(c.title || c.url)}<small>${esc(c.publisher || c.domain)} · ${esc(c.published_at || '日期待补')}</small></p>`).join('');
        return `<section data-pane="basis"><h4>官方来源 ${mine.length ? `<small>${mine.length} 条</small>` : ''}</h4>
          ${rows || '<p class="cs-muted">本章还没有可引用的官方来源。外部信息必须可核验，二手内容不采。</p>'}
          <button type="button" class="mw-btn" data-add-citation="${esc(String(active.block_id))}">添加官方来源</button>
          </section>`;
      })()}
      ${(() => { const layout = context.profile.layout; const recipe = specInfo.specs[layout]; const global = specInfo.specs.global;
        return recipe ? `<section data-pane="basis"><h4>编写规范</h4><p class="cs-muted">本章按《${esc(recipeLabel(layout))} v${recipe.version}》编写，全局纪律 v${global ? global.version : '—'}。</p><button type="button" class="mw-btn" data-spec="${esc(layout)}">查看 / 编辑规范</button></section>` : ''; })()}
      ${(() => { const unit = active && unitOf.get(String(active.block_id)); const info = unit && planUnits.get(unit);
        return info ? `<section data-pane="basis"><h4>编写计划</h4><p class="cs-muted">本章属于《${esc(info.title)}》的编写计划（${info.leaves} 节）：先确认计划，再按计划编制。</p><button type="button" class="mw-btn" data-plan="${esc(unit)}">查看 / 确认计划</button></section>` : ''; })()}
      <section data-pane="basis"><h4>编排建议</h4><label>章节类型 <select data-cs="layout">${Object.entries(context.layouts).map(([id,l])=>`<option value="${id}" ${id===context.profile.layout?'selected':''}>${esc(l.label)}</option>`).join('')}</select></label><p>${layout.blocks.map(esc).join(' → ')}</p><p class="cs-muted">${context.materials.some(m=>m.confirmed)?'生成仅引用已核对素材，并结合本章评分及规范要求。':'无已核对素材：可先写项目分析与拟实施方案；人员、业绩、证书、实测参数等保留待补充。'}</p>${context.missing.map(m=>`<p class="cs-warning">${esc(m)}</p>`).join('')}</section>
      <section data-pane="materials">${(() => {
        const key = String(active.block_id);
        const linked = citationInfo.rows.filter((c) => (c.links || []).includes(key) || !c.block_id);
        return `<div class="cs-material-head"><h4>外部依据 ${linked.length}</h4>
          <button type="button" class="mw-btn" data-add-citation="${esc(key)}">添加官方来源</button></div>
          ${linked.map((c) => `<p class="cs-cite-line">${esc(c.title || c.url)}
            <small>${esc(c.publisher || c.domain)} · ${esc(c.published_at || '日期待补')} · 关联 ${(c.links || []).length} 章</small></p>`).join('')
            || '<p class="cs-muted">本章还没有关联的官方依据。外部信息必须可核验，二手内容不采。</p>'}`;
      })()}</section>
      <section data-pane="materials"><div class="cs-material-head"><h4>本章素材 ${context.materials.length}</h4><label class="cs-upload">上传素材<input data-cs="upload" type="file" multiple accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp"></label></div><p class="cs-muted">PDF／Word 提取原文与页码；图片保留原图，需人工核对含义。</p><button data-action="library">从项目素材库关联</button><button data-action="refresh-materials">刷新解析状态</button><div data-cs="library"></div>${context.materials.map(m=>`<article class="cs-material"><strong>${esc(m.filename)}</strong><small>${esc(m.parse_status==='done'?'已解析':m.parse_status==='failed'?'解析失败，可移除后重传':'解析中，请稍后刷新')} · ${m.confirmed?'已核对':'待核对'}${m.adopted?' · 正文已引用':''}</small>${m.image_url?`<img src="${esc(m.image_url)}" alt="${esc(m.filename)}">`:''}<details><summary>提炼摘录与映射依据（${m.excerpts.length} 段）</summary><p class="cs-muted">${esc(m.mapping_reason)}</p>${(m.key_points||[]).length?`<h5>规则提炼的原文要点</h5>${m.key_points.map(p=>`<p><b>${esc(p.category)}</b>：${esc(p.quote)}<small>${p.page?`第 ${p.page} 页`:`片段 ${p.chunk_id}`}</small></p>`).join('')}<h5>完整原文片段</h5>`:''}${m.excerpts.map(x=>`<p>${esc(x.text)}<small>${x.page?`第 ${x.page} 页`:`片段 ${x.chunk_id}`}</small></p>`).join('') || '<p>图片不自动推断事实；文档解析中或暂无可提取原文。</p>'}</details><button data-material="confirm" data-id="${m.id}" ${m.parse_status!=='done'?'disabled':''}>${m.confirmed?'撤销核对':'核对适用于本章'}</button>${m.confirmed?`<button data-material="insert" data-id="${m.id}">${m.image_url?'插入图片':'引用原文摘录'}</button>`:''}<button data-material="unlink" data-id="${m.id}">移除关联</button></article>`).join('')}</section>`;
    get('layout').onchange = async event => {
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
      + `title="《${esc(info.title)}》共 ${info.leaves} 节：先确认计划，再按章并发生成">`
      + `《${esc(info.title)}》编写计划 · ${info.leaves} 节</button>`;
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
    get('format-summary').textContent=`A4 · ${preset.font} ${preset.size} 磅 · 1.5 倍行距 · 四边 ${preset.margin} cm`;
    get('format-version').textContent=`模板版本 ${preset.version.slice(0,12)} · 导出任务固定内容、图片与模板版本`;
    get('format-sample').href=`/api/studio/templates/${document.template}/sample?format=pdf`;
    get('format-download').href=`/api/studio/templates/${document.template}/sample?format=docx`;
  };
  paintFormat();
  get('template').onchange=async event=>{
    try { await json(`/api/projects/${projectId}/studio/document`,send('PUT',{thread_id:threadId,template:event.target.value}));document.template=event.target.value;paintFormat();status('交付格式已保存，导出时应用；已生成文件保留原版本。'); }
    catch(error) {event.target.value=document.template;status(error.message);}
  };
  host.onclick = async event => {
    const panel=event.target.closest('.cs-inspector-tabs [data-panel]');
    if(panel){showPanel(panel.dataset.panel);return;}
    const citeButton=event.target.closest('[data-add-citation]');
    if(citeButton){openCitationDialog(citeButton.dataset.addCitation);return;}
    const specLink=event.target.closest('[data-spec]');
    if(specLink){globalThis.document.dispatchEvent(new CustomEvent('writing-specs:open',{detail:{layout:specLink.dataset.spec}}));return;}
    const planButton=event.target.closest('[data-plan]');
    if(planButton){await openWritingPlan(planButton.dataset.plan);return;}
    const chapter=event.target.closest('[data-chapter]');
    if(chapter) {await select(chapter.dataset.chapter);return;}
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
        if (unit && info && !(info.plan && info.plan.status === 'confirmed')) {
          status('本章属于《' + info.title + '》：请先确认编写计划，再开始编制。');
          showPanel('ai');
          await openWritingPlan(unit);
          return;
        }
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
