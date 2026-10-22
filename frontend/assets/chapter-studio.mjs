import {mountLiveWriting} from './chapter-live.mjs?v=20261016-v416';
import {specContentHtml} from './spec-content.mjs?v=20261016-v416';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = async (url, options = {}) => {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试');
  return data;
};
const send = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

/** 把 `:::diagram type=… 标题` + 结构行渲染成对应图型（flow / layered / timeline） */
function renderDiagramFigure(head, bodyLines) {
  const m = /^:::diagram(?:\s+type=(\w+))?(?:\s+(.*))?$/.exec(String(head || '').trim());
  const type = ['flow', 'layered', 'timeline'].includes(m && m[1]) ? m[1] : 'flow';
  const title = ((m && m[2]) || '结构关系图').trim();
  const lines = bodyLines.map((line) => String(line).trim()).filter(Boolean);
  if (type === 'layered') {
    const nodes = lines.map((line) => {
      const hit = /^(-{1,6})\s*(.+)$/.exec(line);
      return hit ? {level: hit[1].length - 1, label: hit[2].trim()} : {level: 0, label: line};
    });
    const levels = new Map();
    nodes.forEach((n) => { if (!levels.has(n.level)) levels.set(n.level, []); levels.get(n.level).push(n.label); });
    const rows = [...levels.keys()].sort((a, b) => a - b).map((level) =>
      `<div class="cs-dg-row lv${Math.min(level, 2)}">${levels.get(level).map((label) => `<div class="cs-dg-box">${esc(label)}</div>`).join('')}</div>`).join('');
    return `<figure class="cs-diagram cs-dg cs-dg-layered"><div class="cs-dg-rows">${rows}</div><figcaption>${esc(title)}</figcaption></figure>`;
  }
  if (type === 'timeline') {
    const items = lines.map((line) => {
      const hit = /^(\d{4})\s*[|:：、]\s*(.+)$/.exec(line);
      return hit ? {when: hit[1], label: hit[2].trim()} : {when: '', label: line};
    });
    return `<figure class="cs-diagram cs-dg cs-dg-timeline"><div class="cs-dg-axis">${items.map((item) =>
      `<div class="cs-dg-tl"><span class="cs-dg-when">${esc(item.when || '—')}</span><span class="cs-dg-dot"></span><div class="cs-dg-box">${esc(item.label)}</div></div>`).join('')}</div><figcaption>${esc(title)}</figcaption></figure>`;
  }
  // 有箭头按箭头切；没有箭头（旧写法）则每行一个节点
  const joined = lines.join(' ');
  const nodes = (joined.includes('->') || joined.includes('→'))
    ? joined.replaceAll('→', '->').split('->').map((n) => n.trim()).filter(Boolean)
    : lines.map((line) => line.replace(/^[-*]\s*/, '').trim()).filter(Boolean);
  return `<figure class="cs-diagram cs-dg cs-dg-flow"><div class="cs-dg-axis">${nodes.map((label, i) =>
    `${i ? '<span class="cs-dg-arrow" aria-hidden="true">→</span>' : ''}<div class="cs-dg-box">${esc(label)}</div>`).join('')}</div><figcaption>${esc(title)}</figcaption></figure>`;
}

// 诊断用：把未捕获异常与堆栈打到控制台，便于定位"null setting"类报错（不影响行为）
try {
  globalThis.addEventListener('error', (event) => {
    if (event?.error) console.error('[chapter-studio] 未捕获异常', event.error);
  });
  globalThis.addEventListener('unhandledrejection', (event) => {
    if (event?.reason) console.error('[chapter-studio] 未处理 Promise', event.reason);
  });
} catch (e) { /* ignore */ }

export function previewContent(value) {
  const lines = String(value || '').split('\n');
  const html = [];
  let text = [];
  const flush = () => { if (text.length) { html.push(specContentHtml(text.join('\n'))); text = []; } };
  for (let index = 0; index < lines.length; index++) {
    // 表名行：`**表：xxx**` 渲染为表格上方的表名
    const caption = lines[index].trim().match(/^\*\*表\s*[：:]\s*(.+?)\*\*$/);
    const image = lines[index].trim().match(/^!\[([^\]]*)\]\((\/api\/studio\/materials\/\d+\/image)\)$/);
    const heading = lines[index].match(/^(#{1,6})\s+(.+)$/);
    if (lines[index].startsWith(':::diagram')) {
      flush();
      const head = lines[index].trim();
      const body = [];
      while (++index < lines.length && lines[index].trim() !== ':::') body.push(lines[index]);
      html.push(renderDiagramFigure(head, body));
    } else if (caption) {
      flush(); html.push(`<p class="cs-tbl-cap">表：${esc(caption[1])}</p>`);
    } else if (image) {
      flush(); html.push(`<figure><img src="${esc(image[2])}" alt="${esc(image[1])}" loading="lazy"><figcaption>${esc(image[1])}</figcaption></figure>`);
    } else if (heading) {
      flush(); html.push(`<h${Math.min(heading[1].length + 1,6)}>${esc(heading[2])}</h${Math.min(heading[1].length + 1,6)}>`);
    } else text.push(lines[index]);
  }
  flush();
  return html.join('') || '<p class="cs-muted">暂无内容</p>';
}
// 自检用：直接调用渲染函数（避免依赖当前是否预览态）
try { globalThis.__csPreview = previewContent; } catch (e) { /* ignore */ }

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

        <div class="cs-agent-slot" data-cs="agents"></div>
        <div class="cs-toolbar">
          <button data-action="ai" class="cs-primary" title="按章节大纲逐节编写整章（已有内容的节保留）">编写整章</button>
          <button data-action="save" disabled>保存</button>
          <div class="cs-view-switch" role="group" aria-label="正文显示方式"><button data-action="edit" aria-pressed="true">编辑</button><button data-action="preview" aria-pressed="false">预览</button></div>
          <div class="cs-toolbar-right">
            <button type="button" class="cs-insert-btn" data-open-insert title="从官方来源 / 素材 / 结构模板插入内容">插入…</button>
            <details class="cs-more"><summary>更多 ▾</summary><div class="cs-more-menu">
              <button data-action="project-todo">全书待办</button>
              <button data-action="writing-settings">编写设置</button>
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

      </main>
      <aside class="cs-aside cs-inspector" data-cs="inspector" data-panel="write"><nav class="cs-inspector-tabs" aria-label="本章助手"><button data-panel="write" aria-pressed="true">编写</button><button data-panel="materials" aria-pressed="false">素材</button></nav><div data-cs="aside"></div></aside>
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

  let materialLib = null;

  /** 拉素材库数据（项目级素材 + 本章引用） */
  async function loadMaterialLibrary() {
    if (!active) return;
    try {
      const [citeList, lib, pend, boundList] = await Promise.all([
        fetch(`/api/blocks/${active.id}/citations`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/projects/${projectId}/materials/library?status=confirmed&limit=80`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/projects/${projectId}/materials/library?status=pending&limit=80`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/blocks/${active.id}/materials/bound`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
      ]);
      materialLib = { confirmed: lib?.items || [], counts: lib?.counts || {}, pending: pend?.items || [],
                      bound: boundList?.items || [], citations: citeList || { points: [], missing: [], summary: {} } };
    } catch (error) { materialLib = null; }
  }

  /** 素材页签（方案 C + M3）：① 本章引用（主） ② 素材库（折叠） ③ 上传 */
  function materialLibraryHtml() {
    const lib = materialLib || { confirmed: [], pending: [], bound: [], citations: { points: [], missing: [], summary: {} } };
    const citations = lib.citations || { points: [], missing: [], summary: {} };
    const summary = citations.summary || {};
    const byKind = summary.by_kind || {};

    // ① 引用清单：按小节分组
    const groups = new Map();
    for (const point of (citations.points || [])) {
      const key = point.section || '（正文）';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(point);
    }
    const kindLabel = { upload: '招标文件', web: '联网来源', doc: '法规/标准', unknown: '未匹配' };
    const kindClass = { upload: 'is-upload', web: 'is-web', doc: 'is-doc', unknown: 'is-unknown' };
    const pointsHtml = [...groups.entries()].map(([sectionName, points]) => `
      <details class="cs-cite-group">
        <summary class="cs-cite-sec"><span>${esc(sectionName)}</span>
          <em>引用 ${points.length} 处</em></summary>
        <div class="cs-cite-body-list">
        ${points.map((point) => `<div class="cs-cite-row">
          <span class="cs-cite-kind ${kindClass[point.kind] || 'is-unknown'}">${esc(kindLabel[point.kind] || '来源')}</span>
          <div class="cs-cite-body">
            <p class="cs-cite-label">${esc(point.source_label)}</p>
            <p class="cs-cite-quote">${esc(String(point.quote || '').slice(0, 46))}</p>
            <small>第 ${point.paragraph} 段${point.locator && point.locator !== point.source_label ? ` · ${esc(point.locator)}` : ''}</small>
          </div>
          <button type="button" class="cs-todo-ignore" data-cite-locate="${esc(sectionName)}|${esc(String(point.quote || ''))}">定位</button>
        </div>`).join('')}
        </div>
      </details>`).join('');
    const missingHtml = (citations.missing || []).length ? `
      <details class="cs-cite-group is-missing-group">
        <summary class="cs-cite-sec"><span>未标来源</span><em>${citations.missing.length} 处</em></summary>
        <div class="cs-cite-body-list">
        ${(citations.missing || []).slice(0, 6).map((item) => `<div class="cs-cite-row is-missing">
          <span class="cs-cite-kind is-missing">缺来源</span>
          <div class="cs-cite-body"><p class="cs-cite-quote">${esc(String(item.quote || '').slice(0, 50))}</p>
            <small>${esc(item.section || '正文')} 第 ${item.paragraph} 段</small></div>
          <button type="button" class="cs-todo-ignore" data-cite-locate="${esc(item.section || '')}|${esc(String(item.quote || ''))}">定位</button>
        </div>`).join('')}
        </div>
      </details>` : '';

    // ② 素材库（折叠）：按来源类型分区
    const pickId = (item) => (item.chunk_kind === 'cite' ? -item.chunk_id : item.chunk_id);
    const uploadItems = (lib.confirmed || []).filter((item) => item.source_type !== 'web');
    const webItems = (lib.confirmed || []).filter((item) => item.source_type === 'web');
    const pickHtml = (items, empty) => (items.length ? items.slice(0, 30).map((item) => `
      <label class="cs-ml-item is-pick">
        <input type="checkbox" data-ml-pick="${pickId(item)}" />
        <div class="cs-ml-body"><b>${esc(item.filename || '素材')}</b>
          <p>${esc(String(item.text || '').slice(0, 70))}…</p>
          <small>${item.page ? `第 ${item.page} 页` : '网页片段'}</small></div>
      </label>`).join('') : `<p class="cs-muted">${empty}</p>`);

    return `<div class="cs-ml">
      <div class="cs-ml-head"><h4>本章引用 <small>${summary.citations || 0} 处 · 未标来源 ${summary.missing || 0} 处</small></h4></div>
      <p class="cs-ml-sum">招标文件 ${byKind.upload || 0} · 联网来源 ${byKind.web || 0} · 法规标准 ${byKind.doc || 0}</p>
      ${pointsHtml || '<p class="cs-muted">正文里还没有【来源：…】标注</p>'}
      ${missingHtml}
      <details class="cs-ml-fold">
        <summary>素材库（${lib.counts?.confirmed || 0} 段）— 选择素材应用到本章</summary>
        <div class="cs-ml-search"><input type="text" data-ml-q placeholder="搜索素材…" /></div>
        <p class="cs-cite-sec">我上传的（${uploadItems.length}）</p>
        <div class="cs-ml-list">${pickHtml(uploadItems, '还没有上传素材')}</div>
        <p class="cs-cite-sec">联网搜索的（${webItems.length}）</p>
        <div class="cs-ml-list">${pickHtml(webItems, '还没有联网搜索的素材')}</div>
        <div class="cs-ml-acts"><button type="button" class="cs-cp-primary" data-ml-apply>应用到本章</button></div>
      </details>
    </div>`;
  }

  /** 素材页签顶部：本章"需要你上传的材料"（招标硬性要求才有；没有就不显示） */
  function materialRequestsHtml() {
    const tasks = (planBrief?.deliveryTasks?.need_user || [])
      .filter((task) => task.category === 'upload' || task.action?.type === 'upload_material');
    if (!tasks.length) return '';
    return `<div class="cs-mat-requests">
      <h4>本章需要你上传 <small>${tasks.length} 份（招标要求提供）</small></h4>
      ${tasks.map((task) => `<div class="cs-mat-request">
        <div class="cs-mat-request-head"><b>${esc(task.title)}</b>
          <button type="button" class="cs-gate-act" data-task-act="${esc(task.action.type)}" data-task-id="${esc(task.id)}">上传</button></div>
        <p class="cs-mat-request-why">${esc(task.why || '')}</p>
        ${(task.materials_required || []).length ? `<ul>${task.materials_required.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>` : ''}
        ${task.material_format ? `<p class="cs-mat-request-meta">格式：${esc(task.material_format)}</p>` : ''}
        ${task.fallback ? `<button type="button" class="cs-todo-ignore" data-task-fallback="${esc(task.id)}">${esc(task.fallback.label)}</button>` : ''}
      </div>`).join('')}
    </div>`;
  }

  /** 这一节的"写法"（= 该节提示词）：优先用保存过的覆盖值，否则用系统默认 */
  function practiceOf(section) {
    const saved = (planBrief?.practiceOverrides || {})[String(section.title)];
    if (saved) return saved;
    const how = section.how || {};
    const parts = [how.structure, how.style, (how.pitfalls || []).length ? `避免：${(how.pitfalls || []).join('、')}` : '']
      .filter(Boolean);
    return parts.length ? parts.join('\n') : '（按本节要点展开，先结论后分点，落到本项目具体工作）';
  }

  /** 保存某节写法（批量编写时按此执行） */
  async function saveSectionPractice(index) {
    const outline = planBrief?.outline || [];
    const section = outline[index];
    const area = host.querySelector(`[data-ol-how="${index}"]`);
    if (!section || !area) return;
    const text = String(area.value || '').trim();
    try {
      await json(`/api/projects/${projectId}/chapters/${encodeURIComponent(active.block_id)}/plan/practice`, {
        method: 'PUT',
        body: { section: section.title, practice: text },
      });
      planBrief = planBrief || {};
      planBrief.practiceOverrides = { ...(planBrief.practiceOverrides || {}), [String(section.title)]: text };
      status(`已保存「${section.title}」的写法；点「按写法批量编写」即按此执行`);
    } catch (error) { status(`保存失败：${error.message}`); }
  }

  /** 依据 ↔ 正文对应 + 本章计划摘要（待办数）。 */
  async function loadBasis(blockKey) {
    if (!blockKey) { basisInfo = null; planBrief = null; return; }
    try {
      const [basisResp, planResp] = await Promise.all([
        fetch(`/api/projects/${projectId}/basis?threadId=${encodeURIComponent(threadId)}&blockId=${encodeURIComponent(blockKey)}`, {signal: AbortSignal.timeout(20000)}),
        fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockKey)}/plan?threadId=${encodeURIComponent(threadId)}&_=${Date.now()}`, {signal: AbortSignal.timeout(30000), cache: 'no-store'}),
      ]);
      basisInfo = basisResp.ok ? await basisResp.json() : null;
      if (planResp.ok) {
        const plan = await planResp.json();
        planBrief = {
          todo: plan.todo || [],
          gaps: plan.material_gaps || [],
          generationGaps: plan.generation_gaps || [],
          todoItems: plan.todo_items || [],
          deliveryTasks: plan.delivery_tasks || null,
          outline: plan.outline || [],
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
  async function openChapterPlan(blockId, options = {}) {
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
    const bodyText = String(editor?.value || '');
    data.outline_done = (data.outline || [])
      .map((sec) => sec.title)
      .filter((title) => new RegExp(`^##\\s+${String(title).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}\\s*$`, 'm').test(bodyText));
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

          </div>
          ${wordPlan.advice ? `<p class="cs-cp-warn-line">${esc(wordPlan.advice)}</p>` : ''}
        </div>



        <details class="cs-cp-block" data-cp-sec="outline" open>
          <summary><b><span class="cs-cp-idx">1</span>章节大纲</b><em>${(data.outline || []).length ? `${(data.outline || []).length} 节` : '未生成（0 节）'}</em>
            <button type="button" class="cs-cp-link" data-cp-build-outline title="按评分点与交付物重新拆分章节（会覆盖现有大纲）">${(data.outline || []).length ? '重做大纲' : '生成大纲'}</button></summary>
          <div class="cs-cp-fold-body">
            ${(data.outline || []).length ? `<div class="cs-ol-batch">
              <button type="button" class="cs-cp-primary" data-ol-batch-write>按写法批量编写本章</button>
              <span class="cs-dim">按每节的「写法」逐节生成（可先调整写法再点）</span>
            </div>` : ''}
            ${(data.outline || []).length ? (data.outline || []).map((sec, i) => {
              const basis = (sec.basis || []).map((b) => `${esc(b.ref)}${b.use ? `（${esc(b.use)}）` : ''}`).join(' · ');
              const how = sec.how || {};
              const deliv = (sec.deliverables || []).map((d) => {
                if (d.kind === 'text') return `文字 ${d.words || 600} 字`;
                if (d.kind === 'table') return `表格「${esc(d.title)}」${(d.cols || []).length ? `（${(d.cols || []).map((c) => esc(c)).join('/')}）` : ''}`;
                return `图示「${esc(d.title)}」（${{layered: '层次图', flow: '流程图', timeline: '时间线'}[d.type] || '层次图'}）`;
              }).join(' · ');
              return `<details class="cs-ol-item">
                <summary class="cs-ol-row">
                  <span class="cs-ol-idx">${i + 1}</span>
                  <span class="cs-ol-main"><b>${esc(sec.title)}</b>
                    <small>${(sec.points || []).map((x) => esc(x)).join(' · ')}</small></span>
                  <span class="cs-ol-meta">${sec.words || 900} 字</span>
                </summary>
                <div class="cs-ol-detail">
                  <div class="cs-ol-how">
                    <p class="cs-ol-how-title"><b>写法</b><span class="cs-dim">（这一节怎么写 = 该节的提示词，可改）</span></p>
                    <textarea class="cs-ol-how-text" data-ol-how="${i}" rows="4">${esc(practiceOf(sec))}</textarea>
                    <div class="cs-ol-how-acts">
                      <button type="button" class="cs-cp-ghost" data-ol-how-reset="${i}">恢复默认</button>
                      <button type="button" class="cs-cp-primary" data-ol-how-save="${i}">保存写法</button>
                    </div>
                  </div>
                  <p><b>交付物</b>${deliv || '文字'}</p>
                </div>
              </details>`;
            }).join('') : '<p class="cs-cp-empty">还没有大纲。点「生成大纲」先出 6~10 节（编写入口在右栏「进度」）。</p>'}
          </div>
        </details>







        ${(isParent || isCatalog) ? `<details class="cs-cp-block" data-cp-sec="children" open>
          <summary><b><span class="cs-cp-idx">5</span>子节统筹</b><em>${children.length} 节 · 共 ${children.reduce((sum, c) => sum + (c.pages || 0), 0)} 页</em></summary>
          ${isParent ? '<p class="cs-cp-howto">本章只写总述（300–500 字）：先全貌 → 3 个关键结论 → 子节索引，细节见下列各节。</p>' : ''}
          ${children.map((child) => `<div class="cs-cp-kid" data-cp-kid="${esc(child.block_id)}" role="button" tabindex="0">
              <span class="cs-cp-kid-title">${esc(child.title)}</span>
              <span class="cs-cp-kid-bar"><i style="width:${child.pages ? Math.min(100, Math.round((child.written_pages || 0) / child.pages * 100)) : 0}%"></i></span>
              <small>${child.written_pages || 0} / ${child.pages || '—'} 页</small>
              <em class="${child.has_content ? 'ok' : ''}">${child.has_content ? '有正文' : '未开始'}</em>
            </div>`).join('')}
        </details>` : ''}
        <details class="cs-cp-block" data-cp-sec="layout">
          <summary><b><span class="cs-cp-idx">2</span>章节类型</b><em>影响生成</em></summary>
          <div class="cs-cp-fold-body">
            <label class="cs-cp-kind-row">本章类型
              <select data-cp-layout>${Object.entries(context?.layouts || {}).map(([id, l]) => `<option value="${id}" ${id === ((context?.profile || {}).layout || '') ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
              <span class="cs-dim">决定本章按哪类章节编制（影响提示词与结构）</span>
            </label>
          </div>
        </details>

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
      // 生成大纲（P0-1）
      body.querySelector('[data-cp-build-outline]')?.addEventListener('click', async (event) => {
        const btn = event.currentTarget; btn.disabled = true; const label = btn.textContent; btn.textContent = '生成中…';
        try {
          const resp = await fetch(`/api/blocks/${active.id}/studio/outline`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({thread_id: threadId, regenerate: btn.textContent === '重新生成'}), signal: AbortSignal.timeout(180000),
          });
          const out = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(out.detail || `HTTP ${resp.status}`);
          data.outline = out.outline || [];
          status(`已生成 ${data.outline.length} 节大纲`);
          await render();
        } catch (error) { status(error.message); btn.disabled = false; btn.textContent = label; }
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
  let autoSaveTimer = null, saveState = 'idle', savedAt = 0;
  const fmtClock = (ts) => { try { return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } };

  /** 章头状态胶囊：未保存 / 保存中 / 已保存·时间 / 保存失败 / 已确认完成 */
  function paintSaveState() {
    const el = get('chapter-head')?.querySelector('[data-save-state]');
    if (!el) return;
    const completed = profile[active?.id]?.writing_status === 'complete';
    let html = '';
    if (saveState === 'saving') html = '<span class="cs-save-dot is-saving"></span>保存中…';
    else if (saveState === 'error') html = '<span class="cs-save-dot is-error"></span>保存失败 <button type="button" class="cs-save-act" data-action="save">重试</button>';
    else if (completed) html = '<span class="cs-save-dot is-done"></span>已确认完成';
    else if (dirty) html = '<span class="cs-save-dot is-dirty"></span>未保存 · 刚刚改动 <button type="button" class="cs-save-act" data-action="save">保存</button>';
    else if (savedAt) html = `<span class="cs-save-dot is-saved"></span>已保存 · ${fmtClock(savedAt)}`;
    el.innerHTML = html;
  }

  /** 计划自动保存：停止输入 2 秒后落库 */
  function scheduleAutoSave() {
    if (autoSaveTimer) globalThis.clearTimeout(autoSaveTimer);
    autoSaveTimer = globalThis.setTimeout(async () => {
      autoSaveTimer = null;
      if (!dirty || !active) return;
      if (live.active && live.pending) return;          // AI 未结束不自动保存
      await save({ auto: true });
    }, 2000);
  }

  async function save(options = {}) {
    const auto = options.auto === true;
    if(live.active&&live.pending){if(!auto)status('图文表尚未完成，编制进度已保留；完成或移除未完成项后可保存正文。');return false;}
    if (!active || !dirty) return true;
    const block = active, value = editor.value;
    saveState = 'saving'; paintSaveState();
    if (!auto) status('正在保存…');
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
      saveState = 'saved'; savedAt = Date.now();
      const wasComplete = profile[block.id]?.writing_status === 'review' && stateOf(block) === '草稿';
      paintSaveState();
      host.querySelector('[data-action="save"]').disabled=!dirty;
      if (wasComplete) status('本章已退回待确认（内容有改动）');
      else status(''); return true;
    } catch (error) {
      saveState = 'error'; paintSaveState();
      status(`保存失败：${error.message}。本地草稿已保留。`); return false;
    }
  }
  const changed = () => {
    dirty=true;
    if(profile[active.id]?.writing_status==='complete'){
      profile[active.id].writing_status='review';context.profile.writing_status='review';paintTree();stats();
    }
    saveState = 'idle'; paintSaveState();
    host.querySelector('[data-action="save"]').disabled=false;
    sessionStorage.setItem(draftKey(active),JSON.stringify({content:editor.value,base:active.content || ''}));
    status('');
    scheduleAutoSave();
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
  /** 读取本章计划（含大纲） */
  async function fetchChapterPlan(blockKey) {
    const resp = await fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockKey)}/plan?threadId=${encodeURIComponent(threadId)}&_=${Date.now()}`, {cache: 'no-store', signal: AbortSignal.timeout(30000)});
    return resp.ok ? resp.json() : {};
  }

  /** 写一节（只影响这一节） */
    /** 把某节在正文里替换为新内容（只影响这一节） */
  function mergeSectionText(content, title, text) {
    const esc = String(title).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // 匹配到"下一个 ## 标题"或"字符串结尾"；不要用 \s*$（会在一行末尾就断开，导致内容重复）
    const re = new RegExp(`(^##[ \\t]+${esc}[ \\t]*\\n?)([\\s\\S]*?)(?=^##[ \\t]+|$(?![\\s\\S]))`, 'm');
    if (re.test(content)) return content.replace(re, `## ${title}\n\n${text}\n\n`);
    return `${content.replace(/\s*$/, '')}\n\n## ${title}\n\n${text}`;
  }

  /** 预览区里点亮正在编写的这一节（标题 + 其后内容） */
  function highlightWritingSection(hostEl, title) {
    if (!hostEl) return;
    const heading = [...hostEl.querySelectorAll('h2,h3,h4')].find((h) => (h.textContent || '').trim() === String(title).trim());
    if (!heading) return;
    heading.classList.add('cs-sec-writing');
    let node = heading.nextElementSibling;
    while (node && !/^H[1-6]$/.test(node.tagName)) { node.classList.add('cs-sec-writing'); node = node.nextElementSibling; }
  }

  /** 导出前的阻塞确认：列出阻塞待办，允许"带风险导出" */
  function openRiskExportDialog(items) {
    return new Promise((resolve) => {
      const dlg = globalThis.document.createElement('dialog');
      dlg.className = 'cs-plan-dialog';
      dlg.innerHTML = `<header><h3>还有 ${items.length} 项阻塞交付</h3><button type="button" data-rx-close>×</button></header>
        <div class="cs-plan-body">
          <p class="cs-warning">导出的是已保存版本；下面这些阻塞项建议先处理，也可以带风险导出。</p>
          <ul class="cs-rx-list">${items.slice(0, 8).map((x) => `<li>${esc(x.title)}<small>${esc(x.group_label || '')}${x.evidence ? ` · ${esc(String(x.evidence).slice(0, 50))}` : ''}</small></li>`).join('')}${items.length > 8 ? `<li>…还有 ${items.length - 8} 项</li>` : ''}</ul>
        </div>
        <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-rx-cancel>取消</button>
          <button type="button" class="cs-cp-primary" data-rx-go>带风险导出</button></footer>`;
      const done = (value) => { dlg.close(); resolve(value); };
      dlg.addEventListener('click', (event) => {
        if (event.target.closest('[data-rx-close]') || event.target.closest('[data-rx-cancel]')) { done(false); return; }
        if (event.target.closest('[data-rx-go]')) done(true);
      });
      dlg.addEventListener('close', () => dlg.remove());
      globalThis.document.body.appendChild(dlg);
      dlg.showModal();
    });
  }

  /** 重写本节前：输入优化方向（提示词） */
  function openRewriteDialog(sec, onStart) {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog cs-rewrite-dialog';
    const history = (sec.prompts || []).slice(-3).reverse();
    dlg.innerHTML = `<header><h3>重写「${esc(sec.title)}」</h3><button type="button" aria-label="关闭" data-rw-close>×</button></header>
      <div class="cs-plan-body">
        ${agentLogoHtml('rewrite', '正在重写本节')}
        <p class="cs-rw-facts">目标 ${sec.words || 900} 字 · 交付物 ${(sec.deliverables || []).map((d) => d.title || d.kind).join(' / ') || '正文'}</p>
        <p class="cs-rw-basis">依据：${(sec.basis || []).map((b) => esc(b.ref)).join(' · ') || '继承本章依据'}</p>
        <label class="cs-rw-label">优化方向（提示词）</label>
        <textarea data-rw-input rows="4" placeholder="例：语气更务实，突出广东电网牵头责任；每条政策后补一句“对本项目的含义”">${esc((sec.prompts || []).slice(-1)[0] || '')}</textarea>
        <div class="cs-rw-chips">
          ${['更务实', '更技术', '压缩 20%', '扩写 30%', '补一个对比表', '每条措施写到谁做/何时完成'].map((x) => `<button type="button" class="cs-ins-chip" data-rw-chip="${esc(x)}">${esc(x)}</button>`).join('')}
        </div>
        ${history.length ? `<p class="cs-rw-history">历史：${history.map((x) => `<button type="button" class="cs-mat-link" data-rw-chip="${esc(x)}">${esc(x.slice(0, 18))}</button>`).join(' ')}</p>` : ''}
        <p class="cs-rw-warn">此操作会覆盖本节现有内容（已存版本，可回滚）</p>
      </div>
      <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-rw-cancel>取消</button>
        <button type="button" class="cs-cp-primary" data-rw-start>开始重写</button></footer>`;
    const input = dlg.querySelector('[data-rw-input]');
    dlg.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-rw-chip]');
      if (chip) { input.value = input.value ? `${input.value}；${chip.dataset.rwChip}` : chip.dataset.rwChip; return; }
      if (event.target.closest('[data-rw-close]') || event.target.closest('[data-rw-cancel]')) { dlg.close(); return; }
      if (event.target.closest('[data-rw-start]')) {
        const value = input.value.trim();
        dlg.close();
        onStart(value);
      }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 全书交付检查（v3 §4.4）：按根因汇总 + 批量入口，不列 96 章 */
  async function openProjectTodoDialog() {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>全书交付检查</h3><button type="button" data-pt-close>×</button></header>
      <div class="cs-plan-body"><p class="cs-muted">正在汇总…</p></div>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-pt-close]')) { dlg.close(); return; }
      const batch = event.target.closest('[data-pt-batch]');
      if (batch) {
        const kind = batch.dataset.ptBatch;
        if (kind === 'batch_write') status('批量编写：请一章一章确认（当前引擎不支持跨章批量，避免误覆盖）');
        else if (kind === 'confirm_list') status('未确认章节：在目录里逐章「确认本章完成」');
        else if (kind === 'upload_once') showPanel('materials');
        else if (kind === 'system_search') status('系统会在下次生成时自动补依据');
        return;
      }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
    try {
      const resp = await fetch(`/api/projects/${projectId}/todo/overview?thread_id=${encodeURIComponent(threadId)}&_=${Date.now()}`,
        { cache: 'no-store', signal: AbortSignal.timeout(180000) });
      const data = await resp.json();
      if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      const summary = data.summary || {};
      const roots = data.roots || [];
      dlg.querySelector('.cs-plan-body').innerHTML = `
        <p class="cs-muted">空章节 ${summary.empty_chapters || 0} 章 · 未确认 ${summary.unconfirmed_chapters || 0} 章 · 系统待补依据 ${summary.generation_gaps || 0} 条</p>
        ${roots.length ? roots.map((root) => `<div class="cs-task${root.impact === 'export_block' ? ' is-export' : root.impact === 'completion_block' ? ' is-completion' : ''}">
            <div class="cs-task-head"><b>${esc(root.title)}</b>
              <span class="cs-task-impact">${esc({ export_block: '不处理不能导出', completion_block: '不处理不能确认完成', quality: '影响评分', suggestion: '可选优化' }[root.impact] || '')}</span></div>
            <div class="cs-task-acts">
              <button type="button" class="cs-task-act" data-pt-batch="${esc(root.action || 'view')}">${
                root.action === 'batch_write' ? '批量编写…' : root.action === 'upload_once' ? '上传一次，全部生效' :
                root.action === 'confirm_list' ? '查看清单' : root.action === 'system_search' ? '系统先查' : '查看'}</button>
            </div>
          </div>`).join('') : '<p class="cs-todo-ok">✓ 没有跨章阻塞项</p>'}`;
    } catch (error) {
      dlg.querySelector('.cs-plan-body').innerHTML = `<p class="cs-warning">汇总失败：${esc(error.message)}</p>`;
    }
  }

  /** 建议式改写  /** 建议式改写：给出 2~3 个替代表述，选一条就地替换 */
  function openSuggestionDialog(item) {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>${esc(item.title)}</h3><button type="button" data-sg-close>×</button></header>
      <div class="cs-plan-body">
        <p class="cs-muted">原文：${esc(String(item.sentence || item.evidence || '').slice(0, 100))}</p>
        <p class="cs-rw-label">选一种改法</p>
        <div class="cs-mp-list">
          ${(item.suggestions || []).map((s, i) => `<label class="cs-mp-item"><input type="radio" name="sg" value="${i}" ${i === 0 ? 'checked' : ''} /><span>${esc(s)}</span></label>`).join('')}
        </div>
      </div>
      <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-sg-cancel>取消</button>
        <button type="button" class="cs-cp-primary" data-sg-apply>应用改写</button></footer>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-sg-close]') || event.target.closest('[data-sg-cancel]')) { dlg.close(); return; }
      if (!event.target.closest('[data-sg-apply]')) return;
      const picked = dlg.querySelector('input[name="sg"]:checked');
      const index = picked ? Number(picked.value) : 0;
      const sentence = String(item.sentence || '');
      const choice = (item.suggestions || [])[index] || '';
      dlg.close();
      const result = applySuggestion(sentence, choice);
      if (!result.applied) { status('没能在正文里定位到这句话，请手动修改'); return; }
      editor.value = result.content;
      changed();
      await save();
      status(`已应用改写：${choice}`);
      await loadBasis(active.block_id);
      paintAside();
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 把"把「X」改为「Y」"落到正文：替换该句里的 X */
  function applySuggestion(sentence, choice) {
    const text = String(editor.value || '');
    const m = /把「(.+?)」改为「(.+?)」/.exec(String(choice || ''));
    if (!m || !sentence) return { applied: false, content: text };
    const [, from, to] = m;
    if (!sentence.includes(from)) return { applied: false, content: text };
    const replaced = sentence.replaceAll(from, to);
    if (!text.includes(sentence)) return { applied: false, content: text };
    return { applied: true, content: text.replace(sentence, replaced) };
  }

  /** 选模型（按角色）→ 写「章节覆盖」（仅本章），并刷新生效值 */
  function openModelPickerDialog(role, options, current) {
    const meta = AGENTS[role] || AGENTS.text;
    const label = role === 'rewrite' ? '文字精修' : (meta.name || role);
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>${esc(label)} 用哪个模型</h3><button type="button" data-mp-close>×</button></header>
      <div class="cs-plan-body">
        <p class="cs-muted">保存后仅对**本章**生效（章节覆盖）；要全局生效请用「更多 → 编写设置」。</p>
        <div class="cs-mp-list">
          <label class="cs-mp-item"><input type="radio" name="mp" value="" ${current ? '' : 'checked'} /> 跟随项目默认</label>
          ${options.map((m) => `<label class="cs-mp-item"><input type="radio" name="mp" value="${esc(m)}" ${m === current ? 'checked' : ''} />
            <b>${esc(m)}</b><small>${/pro/i.test(m) ? '强 · 精修/规划推荐' : /flash/i.test(m) ? '快 · 日常编写推荐' : '可选'}</small></label>`).join('')}
        </div>
      </div>
      <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-mp-cancel>取消</button>
        <button type="button" class="cs-cp-primary" data-mp-save>保存（仅本章）</button></footer>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-mp-close]') || event.target.closest('[data-mp-cancel]')) { dlg.close(); return; }
      if (!event.target.closest('[data-mp-save]')) return;
      const picked = dlg.querySelector('input[name="mp"]:checked');
      const value = picked ? picked.value : '';
      dlg.close();
      try {
        const resp = await fetch(`/api/blocks/${active.id}/writing-settings`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(value ? { settings: { [role]: value } } : { settings: {}, reset: true }),
          signal: AbortSignal.timeout(20000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
        await loadWritingModels();
        paintAside();
        status(value ? `本章「${label}」已改为 ${value}` : `本章「${label}」已跟随项目默认`);
      } catch (error) { status(`保存失败：${error.message}`); }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 编写设置（项目默认 + 5 个角色，可设为项目默认/重置本章） */
  async function openWritingSettingsDialog() {
    const [models, settingsResp] = await Promise.all([loadModelOptions(), loadWritingModels()]);
    const info = settingsResp || {};
    const blockSettings = info.settings || {};
    const effective = info.effective || {};
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    const roles = [['chief', '主编', '规划/校验'], ['text', '文字', '每节正文'], ['rewrite', '文字精修', '带原文改写'],
                   ['diagram', '图片', '示意图'], ['table', '表格', '表格']];
    dlg.innerHTML = `<header><h3>编写设置</h3><button type="button" data-ws-close>×</button></header>
      <div class="cs-plan-body">
        <p class="cs-muted">章节覆盖优先于项目默认；这里改的是**项目默认**（全项目生效）。</p>
        <div class="cs-ws-list">
          ${roles.map(([role, label, duty]) => `<label class="cs-ws-row">
              <span class="cs-ws-name"><b>${label}</b><small>${duty}</small></span>
              <select data-ws-role="${role}">
                ${models.map((m) => `<option value="${esc(m)}" ${((effective[role] || {}).used === m) ? 'selected' : ''}>${esc(m)}</option>`).join('')}
              </select>
              ${blockSettings[role] ? `<em class="cs-ws-badge">本章覆盖</em>` : ''}
            </label>`).join('')}
        </div>
        <p class="cs-dim">改完点「设为项目默认」；只影响本章请点头像旁的「换模型」。</p>
      </div>
      <footer class="cs-ins-foot">
        <button type="button" class="cs-cp-ghost" data-ws-reset>重置本章覆盖</button>
        <button type="button" class="cs-cp-primary" data-ws-save>设为项目默认</button>
      </footer>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-ws-close]')) { dlg.close(); return; }
      if (event.target.closest('[data-ws-reset]')) {
        try {
          await fetch(`/api/blocks/${active.id}/writing-settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: {}, reset: true }) });
          await loadWritingModels(); status('已清除本章覆盖，回到项目默认'); dlg.close(); paintAside();
        } catch (error) { status(error.message); }
        return;
      }
      if (!event.target.closest('[data-ws-save]')) return;
      const settings = {};
      for (const select of dlg.querySelectorAll('[data-ws-role]')) {
        if (select.value) settings[select.dataset.wsRole] = select.value;
      }
      try {
        const resp = await fetch(`/api/projects/${projectId}/writing-settings`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ settings }), signal: AbortSignal.timeout(20000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
        await loadWritingModels();
        dlg.close(); paintAside();
        status('已设为项目默认（全项目生效）');
      } catch (error) { status(`保存失败：${error.message}`); }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 一屏审阅本章图片（图集） */
  function openFigureReviewDialog() {
    const outline = planBrief?.outline || [];
    const bodyText = String(editor?.value || '');
    const figures = parseFigures(bodyText);
    const pending = pendingFigures(outline);
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog cs-fig-review';
    dlg.innerHTML = `<header><h3>本章图片（${figures.length + pending.length}）</h3><button type="button" aria-label="关闭" data-fr-close>×</button></header>
      <div class="cs-plan-body">
        <nav class="cs-ins-tabs">
          <button type="button" data-fr-filter="all" aria-pressed="true">全部</button>
          <button type="button" data-fr-filter="ai" aria-pressed="false">AI 生成</button>
          <button type="button" data-fr-filter="material" aria-pressed="false">素材</button>
          <button type="button" data-fr-filter="pending" aria-pressed="false">待补</button>
        </nav>
        <div class="cs-fig-grid" data-fr-grid></div>
      </div>`;
    const grid = dlg.querySelector('[data-fr-grid]');
    const render = (filter) => {
      const items = [
        ...figures.map((f) => ({ ...f, group: f.kind })),
        ...pending.map((d) => ({ kind: 'pending', group: 'pending', title: d.title, section: d.section, type: d.type })),
      ].filter((x) => filter === 'all' || x.group === filter);
      grid.innerHTML = items.length ? items.map((f) => `<div class="cs-fig-card">
          <div class="cs-fig-thumb" data-zoom-card="${esc(f.id || '')}" title="点击放大">${f.kind === 'ai'
            ? renderDiagramFigure(`:::diagram type=${f.type} ${f.title}`, (f.raw || '').split('\n').slice(1, -1))
            : f.kind === 'material' && f.url
              ? `<img src="${esc(f.url)}" alt="${esc(f.title)}" loading="lazy" />`
              : '<div class="cs-fig-missing">待补</div>'}</div>
          <p class="cs-fig-name">${esc(f.title)}</p>
          <p class="cs-fig-sub">${esc(f.section || '')}${f.kind === 'ai' ? ` · ${{layered:'层次图',flow:'流程图',timeline:'时间线'}[f.type] || '图'} · ${f.nodes || 0} 节点` : f.kind === 'material' ? ' · 素材图' : ' · 待补'}</p>
          <div class="cs-fig-card-acts">${f.kind === 'ai'
            ? `<button type="button" class="cs-todo-act" data-fr-adjust="${f.id}">调整</button>`
            : f.kind === 'material'
              ? `<button type="button" class="cs-todo-act" data-fr-view="${f.id}">查看</button>`
              : `<button type="button" class="cs-todo-act" data-fr-create="${esc(f.section)}">生成</button>`}
            <button type="button" class="cs-todo-act" data-fr-locate="${esc(f.section || '')}">定位</button>
          </div>
        </div>`).join('') : '<p class="cs-mat-empty">没有符合条件的图片</p>';
    };
    dlg.addEventListener('click', (event) => {
      if (event.target.closest('[data-fr-close]')) { dlg.close(); return; }
      const tab = event.target.closest('[data-fr-filter]');
      if (tab) {
        for (const b of dlg.querySelectorAll('[data-fr-filter]')) b.setAttribute('aria-pressed', String(b === tab));
        render(tab.dataset.frFilter);
        return;
      }
      const locate = event.target.closest('[data-fr-locate]');
      if (locate) { dlg.close(); void select(active.block_id); globalThis.setTimeout(() => locateSectionInEditor(locate.dataset.frLocate), 600); return; }
      const view = event.target.closest('[data-fr-view]');
      if (view) { const f = figures.find((x) => x.id === view.dataset.frView); if (f?.url) globalThis.open(f.url, '_blank'); return; }
      const zoomCard = event.target.closest('[data-zoom-card]');
      if (zoomCard && !event.target.closest('button')) {
        const f = figures.find((x) => x.id === zoomCard.dataset.zoomCard);
        if (f) { openFigureZoom(f); return; }
      }
      const adjust = event.target.closest('[data-fr-adjust]');
      if (adjust) { const f = figures.find((x) => x.id === adjust.dataset.frAdjust); dlg.close(); if (f) openFigureAdjustDialog(f); }
    });
    render('all');
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 图片放大查看（审阅图集里点缩略图）——用 dialog 保证盖在模态层之上 */
  function openFigureZoom(figure) {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-fig-zoom';
    dlg.innerHTML = `<div class="cs-fig-zoom-inner" data-zoom-inner>
        <header><b>${esc(figure.title || '图片')}</b><span>${esc(figure.section || '')}</span>
          <button type="button" class="cs-fig-zoom-close" data-zoom-close aria-label="关闭">×</button></header>
        <div class="cs-fig-zoom-body"></div>
      </div>`;
    const bodyEl = dlg.querySelector('.cs-fig-zoom-body');
    if (figure.kind === 'material' && figure.url) {
      bodyEl.innerHTML = `<img src="${esc(figure.url)}" alt="${esc(figure.title)}" />`;
    } else if (figure.raw) {
      bodyEl.innerHTML = renderDiagramFigure(`:::diagram type=${figure.type} ${figure.title}`, String(figure.raw).split('\n').slice(1, -1));
    } else {
      bodyEl.innerHTML = '<p class="cs-mat-empty">这张图还没有内容</p>';
    }
    dlg.addEventListener('click', (event) => {
      if (event.target.closest('[data-zoom-close]')) { dlg.close(); return; }
      if (!event.target.closest('[data-zoom-inner]')) dlg.close();   // 点遮罩关闭
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 调整 AI 图：提示词 + 图型 → 重新生成 → 替换正文里的该图块 */
  function openFigureAdjustDialog(figure) {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>调整图片「${esc(figure.title)}」</h3><button type="button" data-fa-close>×</button></header>
      <div class="cs-plan-body">
        ${agentLogoHtml('diagram', '正在调整图片')}
        <p class="cs-muted">当前：${{layered:'层次图',flow:'流程图',timeline:'时间线'}[figure.type] || '图'} · ${figure.nodes} 节点 ·
          依据本节正文重新生成（不改动正文）</p>
        <textarea data-fa-input rows="4" placeholder="例：改为时间线，按 2024/2025/2026 三个阶段展示；节点不超过 6 个"></textarea>
        <div class="cs-ins-cols">图型
          <select data-fa-type>
            <option value="auto">自动</option><option value="layered">层次图</option>
            <option value="flow">流程图</option><option value="timeline">时间线</option>
          </select>
        </div>
        <div class="cs-ins-actions"><button type="button" class="cs-cp-ghost" data-fa-cancel>取消</button>
          <button type="button" class="cs-cp-primary" data-fa-run>重新生成</button></div>
        <div class="cs-ins-result" data-fa-result></div>
      </div>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-fa-close]') || event.target.closest('[data-fa-cancel]')) { dlg.close(); return; }
      if (!event.target.closest('[data-fa-run]')) return;
      const btn = dlg.querySelector('[data-fa-run]');
      const result = dlg.querySelector('[data-fa-result]');
      const hint = dlg.querySelector('[data-fa-input]').value.trim();
      const type = dlg.querySelector('[data-fa-type]').value;
      const body = sectionText(editor.value, figure.section).slice(0, 3000);
      const text = `${body}${hint ? `\n\n【图的要求】${hint}` : ''}`.trim();
      if (text.length < 10) { result.innerHTML = '<p class="cs-warning">本节内容太少，无法生成图</p>'; return; }
      btn.disabled = true; btn.textContent = '生成中…';
      try {
        const resp = await fetch(`/api/blocks/${active.id}/studio/diagram`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text, title: figure.title, type, model: modelChoice.get('diagram') || ''}),
          signal: AbortSignal.timeout(120000),
        });
        const out = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof out.detail === 'string' ? out.detail : `HTTP ${resp.status}`);
        result.innerHTML = `<pre class="cs-ins-preview">${esc(out.markdown)}</pre>
          <div class="cs-ins-actions"><button type="button" class="cs-cp-primary" data-fa-apply>替换这张图</button></div>`;
        result.querySelector('[data-fa-apply]').onclick = () => {
          let updated = replaceFigureBlock(String(editor.value || ''), figure, out.markdown);
          updated = ensureChartReference(updated, figure.section, out.markdown);
          editor.value = updated;
          changed && changed();
          status('图片已替换（在正文里，记得保存；原图可在版本里找回）');
          paintAside();
          dlg.close();
        };
        btn.disabled = false; btn.textContent = '重新生成';
      } catch (error) {
        result.innerHTML = `<p class="cs-warning">${esc(error.message)}</p>`;
        btn.disabled = false; btn.textContent = '重新生成';
      }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 在编辑器里定位到某节标题 */
  function locateSectionInEditor(title) {
    const text = String(editor?.value || '');
    const idx = text.indexOf(`## ${title}`);
    if (idx < 0) { status('没找到该节标题'); return; }
    const lineEnd = text.indexOf('\n', idx) === -1 ? text.length : text.indexOf('\n', idx);
    editor.focus();
    editor.setSelectionRange(idx, lineEnd);
    editor.scrollTop = Math.max(0, Math.round(editor.scrollHeight * (idx / Math.max(1, text.length))) - 80);
    status(`已定位到「${title}」`);
  }

  /** 插入图表后，确保本节正文有一句引用（"如表/图所示"），否则图/表是孤立的 */
  function ensureChartReference(content, sectionTitle, chartMarkdown) {
    const text = String(content || '');
    if (!sectionTitle) return text;
    const section = sectionText(text, sectionTitle);
    if (!section) return text;
    const isTable = String(chartMarkdown || '').trim().startsWith('**表');
    if (/如(下)?[表图]|见[表图]|[表图]\s*\d/.test(section)) return text;   // 已有引用
    const line = isTable ? '如下表所示，' : '如下图所示，';
    // 在该节正文的第一段末尾补一句
    const pattern = new RegExp(`(^##[ \\t]+${String(sectionTitle).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[ \\t]*\\n)([\\s\\S]*?)(?=^##[ \\t]+|$(?![\\s\\S]))`, 'm');
    return text.replace(pattern, (all, head, body) => `${head}${body.trimEnd()}\n\n${line}\n\n`);
  }

  /** 取某节正文 */
  function sectionText(content, title) {
    const esc = String(title).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`^##[ \\t]+${esc}[ \\t]*\\n([\\s\\S]*?)(?=^##[ \\t]+|$(?![\\s\\S]))`, 'm');
    const m = re.exec(String(content || ''));
    return m ? m[1].trim() : '';
  }

  /** 替换正文里某个图块（按起始行定位，避免误替换） */
  function replaceFigureBlock(content, figure, markdown) {
    const lines = String(content || '').split('\n');
    const start = (figure.start >= 0 && lines[figure.start] && lines[figure.start].trim().startsWith(':::diagram')) ? figure.start : -1;
    if (start >= 0) {
      let end = start + 1;
      while (end < lines.length && lines[end].trim() !== ':::') end += 1;
      return [...lines.slice(0, start), ...markdown.split('\n'), ...lines.slice(end + 1)].join('\n');
    }
    return `${String(content).replace(/\s*$/, '')}\n\n${markdown}\n`;
  }

  const AGENTS = {
    chief: { icon: '🧭', name: '主编', color: '#4f46e5' },
    text: { icon: '✍', name: '文字编写', color: '#2563eb' },
    diagram: { icon: '🖼', name: '图片', color: '#d97706' },
    table: { icon: '▦', name: '表格', color: '#059669' },
  };

  /** 「编写整章」代价预估：节数 / 图表数 / 预计调用与时长 */
  function confirmChapterWriteCost(sections, charts, calls) {
    return new Promise((resolve) => {
      const dlg = globalThis.document.createElement('dialog');
      dlg.className = 'cs-plan-dialog';
      const minutes = Math.max(1, Math.round(calls * 0.6));
      dlg.innerHTML = `<header><h3>开始编写整章？</h3><button type="button" data-cw-cancel2>×</button></header>
        <div class="cs-plan-body">
          <p>本章还有 <b>${sections}</b> 节没写${charts ? `，其中需要生成 <b>${charts}</b> 个图表` : ''}。</p>
          <p class="cs-muted">预计调用模型 <b>${calls}</b> 次，耗时约 <b>${minutes}</b> 分钟；中途可「停止」，已完成的节会保留。</p>
          <p class="cs-muted">生成结果先落在草稿区，确认后再保存进正文。</p>
        </div>
        <footer class="cs-ins-foot">
          <button type="button" class="cs-cp-ghost" data-cw-cancel>取消</button>
          <button type="button" class="cs-cp-primary" data-cw-go>开始编写</button>
        </footer>`;
      const done = (value) => { dlg.close(); resolve(value); };
      dlg.addEventListener('click', (event) => {
        if (event.target.closest('[data-cw-cancel]') || event.target.closest('[data-cw-cancel2]')) { done(false); return; }
        if (event.target.closest('[data-cw-go]')) done(true);
      });
      dlg.addEventListener('close', () => dlg.remove());
      globalThis.document.body.appendChild(dlg);
      dlg.showModal();
    });
  }

  /** 章节级 SSE 作业：一条 EventSource 驱动 4 头像与正文流式（v3 M2）。 */
  async function runChapterWriteJob(slot) {
    if (!active) return { started: false };
    const states = { chief: 'queued', text: 'queued', diagram: 'queued', table: 'queued' };
    const models = {};
    const paint = (running) => {
      if (!slot) return;
      slot.innerHTML = agentBarHtml(states, models) + agentActionsHtml(running !== false);
    };
    paint();
    let jobId = '';
    try {
      const resp = await fetch(`/api/projects/${projectId}/studio/chapter-writes`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, block_id: String(active.block_id || ''), settings_override: {} }),
        signal: AbortSignal.timeout(30000),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      jobId = data.job_id;
      currentJobId = jobId;
      if (!jobId) throw new Error('作业创建失败');
    } catch (error) {
      if (slot) slot.innerHTML = '';
      return { started: false, error };
    }
    let lastIndex = 0;
    const pending = new Map();
    const flush = () => {
      if (!pending.size) return;
      const parts = [...pending.entries()].sort((a, b) => a[0] - b[0]).map((entry) => entry[1]).join('');
      const text = parts;
      pending.clear();
      if (text) editor.value = String(editor.value || '') + text;
    };
    return await new Promise((resolve) => {
      const source = new EventSource(`/api/projects/${projectId}/studio/chapter-writes/${jobId}/stream`);
      let finished = false;
      const finish = (result) => {
        if (finished) return;
        finished = true;
        source.close();
        resolve(result);
      };
      source.addEventListener('job', (event) => {
        const data = JSON.parse(event.data || '{}');
        status(`编写中：${data.stage || ''}`);
      });
      source.addEventListener('plan', (event) => {
        const data = JSON.parse(event.data || '{}');
        Object.assign(models, data.models || {});
        states.chief = 'done';
        paint(true);
        status(`计划就绪：${(data.sections || []).length} 节`);
      });
      source.addEventListener('role', (event) => {
        const data = JSON.parse(event.data || '{}');
        if (data.role && data.state) states[data.role] = data.state === 'queued' ? 'queued' : data.state;
        if (data.model) models[data.role] = data.model;
        paint(true);
      });
      source.addEventListener('delta', (event) => {
        const data = JSON.parse(event.data || '{}');
        const index = Number(data.index || 0);
        lastIndex = index;
        pending.set(index, (pending.get(index) || '') + String(data.text || ''));
        if (pending.size > 40) flush();
      });
      source.addEventListener('todo', (event) => {
        const data = JSON.parse(event.data || '{}');
        const items = data.items || [];
        if (items.length) status(`发现 ${items.length} 个待核对项：${items[0].title || ''}`);
      });
      source.addEventListener('chart', (event) => {
        const data = JSON.parse(event.data || '{}');
        const kind = data.role === 'diagram' ? '图' : '表';
        if (data.state === 'working') status(`正在生成${kind}「${data.title || ''}」…`);
        else if (data.state === 'done') status(`${kind}「${data.title || ''}」已生成 ✓`);
        else if (data.state === 'failed') status(`${kind}「${data.title || ''}」失败：${data.error || ''}`);
      });
      source.addEventListener('section', (event) => {
        const data = JSON.parse(event.data || '{}');
        flush();
        status(`已完成第 ${Number(data.index || 0) + 1} 节${data.words ? ` · ${data.words} 字` : ''}`);
      });
      source.addEventListener('done', async (event) => {
        const data = JSON.parse(event.data || '{}');
        flush();
        states.chief = 'done'; states.text = 'done'; states.diagram = 'done'; states.table = 'done';
        paint();
        try {
          if (data.content) editor.value = data.content;
          const fresh = await json(`/api/blocks/${active.id}/studio`);
          context = fresh;
          if (fresh.block) editor.value = fresh.block.content || editor.value;
          await loadBasis(active.block_id);
          paintAside();
        } catch (error) { /* ignore */ }
        status(data.saved ? '整章已写入正文（记得核对）' : (data.reason || '已生成建议稿，未覆盖正文'));
        if (!data.saved && data.content) {
          const proposal = data.content;
          const dlg = globalThis.document.createElement('dialog');
          dlg.className = 'cs-plan-dialog';
          dlg.innerHTML = `<header><h3>本次生成的是建议稿</h3><button type="button" data-pp-close>×</button></header>
            <div class="cs-plan-body"><p class="cs-muted">${esc(data.reason || '正文在生成期间被修改，未自动覆盖')}。
              可以打开预览后决定是否写入。</p></div>
            <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-pp-cancel>不写入</button>
              <button type="button" class="cs-cp-primary" data-pp-apply>写入正文</button></footer>`;
          dlg.addEventListener('click', async (event) => {
            if (event.target.closest('[data-pp-close]') || event.target.closest('[data-pp-cancel]')) { dlg.close(); return; }
            if (!event.target.closest('[data-pp-apply]')) return;
            editor.value = proposal; changed(); await save(); dlg.close();
            status('建议稿已写入正文（可从版本回滚）');
          });
          dlg.addEventListener('close', () => dlg.remove());
          globalThis.document.body.appendChild(dlg);
          dlg.showModal();
        }
        if (Array.isArray(data.sections)) lastSections = data.sections;
        currentJobId = '';
        paintAside();
        globalThis.setTimeout(() => { if (slot) slot.innerHTML = ''; }, 5000);
        finish({ started: true, jobId, saved: !!data.saved });
      });
      source.addEventListener('error', (event) => {
        if (finished) return;                        // done 之后服务端正常关闭也会触发 error，忽略
        let message = '';
        try { message = JSON.parse(event.data || '{}').message || ''; } catch (e) { /* ignore */ }
        status(message ? `编写失败：${message}` : '作业流中断，可在「更多 → 重新连接」恢复');
        if (slot) slot.innerHTML = '';
        finish({ started: true, jobId, error: message || 'stream error' });
      });
    });
  }

  /** 编写整章时的 3 个头像（主编 / 图片 / 表格）*/
  function agentBarHtml(states, models) {
    const roles = ['chief', 'text', 'diagram', 'table'];
    return `<div class="cs-agent-bar">${roles.map((role) => {
      const meta = AGENTS[role];
      const state = states?.[role] || 'idle';
      const label = { idle: '等待', running: '工作中', done: '完成', skip: '跳过', error: '失败' }[state] || '等待';
      const model = (models && models[role]) || writingModels[role] || modelChoice.get(role) || '生效中';
      return `<div class="cs-agent${state === 'running' ? ' is-running' : ''}${state === 'done' ? ' is-done' : ''}${state === 'error' ? ' is-error' : ''}">
          <span class="cs-agent-icon" style="--agent:${meta.color}">${meta.icon}</span>
          <span class="cs-agent-meta"><b>${meta.name}</b><small>${label} · ${esc(model)}</small></span>
          <button type="button" class="cs-agent-model" data-agent-model="${role}" title="换模型">…</button>
        </div>`;
    }).join('')}</div>`;
  }

  /** 头像条下方的作业操作：停止 / 重试 */
  function agentActionsHtml(running) {
    if (!currentJobId) return '';
    return `<div class="cs-agent-actions">
        ${running ? '<button type="button" class="cs-agent-op" data-job-stop>停止</button>'
                  : '<button type="button" class="cs-agent-op" data-job-retry>重试未完成的节</button>'}
        <small>作业 ${esc(currentJobId.slice(0, 8))}</small>
      </div>`;
  }

  /** 单角色 logo（重写/图调整时） */
  function agentLogoHtml(role, text) {
    const meta = AGENTS[role] || AGENTS.text;
    const model = writingModels[role] || modelChoice.get(role) || '生效中';
    return `<div class="cs-agent-logo" data-agent-logo="${role}">
        <span class="cs-agent-icon is-pulse" style="--agent:${meta.color}">${meta.icon}</span>
        <span class="cs-agent-meta"><b>${meta.name}</b><small>${esc(text || '')} · ${esc(model)}</small></span>
        <button type="button" class="cs-agent-model" data-agent-model="${role}" title="换模型">换模型</button>
      </div>`;
  }

  let gateInfo = null;
  let pendingMaterialSection = '';      // 从待办「上传」进入时，记住要绑定到哪一节

  /** 打开章节时：系统静默清理内部语法/术语（不需要用户点），有清理则刷新正文 */
  async function autoCleanChapter() {
    try {
      const resp = await fetch(`/api/blocks/${active.id}/delivery-gate/auto`, { method: 'POST', signal: AbortSignal.timeout(30000) });
      if (!resp.ok) { await refreshDeliveryGate(); return; }
      const data = await resp.json();
      if (data.changed) {
        editor.value = data.content || editor.value;
        context = await json(`/api/blocks/${active.id}/studio`);
        status(`已自动清理 ${data.changes?.length || 0} 类内部写法（可回滚）`);
      }
      gateInfo = { summary: data.remaining, blocking: data.blocking || [] };
      paintGateBar();
    } catch (error) {
      await refreshDeliveryGate();
    }
  }

  /** 拉章级交付门（内部语法/术语/占位符），在编辑区上方提示 */
  async function refreshDeliveryGate() {
    if (!active) return;
    try {
      const resp = await fetch(`/api/blocks/${active.id}/delivery-gate`, { cache: 'no-store', signal: AbortSignal.timeout(20000) });
      gateInfo = resp.ok ? await resp.json() : null;
    } catch (error) { gateInfo = null; }
    paintGateBar();
  }

  function paintGateBar() {
    // 交付门已并入待办：不再单独显示提醒条，此处保留空实现（数据供导出前检查用）
    const bar = host.querySelector('[data-cs="gate"]');
    if (!bar) return;
    const summary = (gateInfo || {}).summary || {};
    const total = summary.blocking || 0;
    if (!total) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    const kinds = summary.by_kind || {};
    const parts = [];
    if (kinds.syntax) parts.push(`内部语法 ${kinds.syntax} 处`);
    if (kinds.term) parts.push(`内部术语 ${kinds.term} 处`);
    if (kinds.placeholder) parts.push(`占位符 ${kinds.placeholder} 处`);
    bar.innerHTML = `<span class="cs-gate-dot"></span>
      <span class="cs-gate-text">本章有 <b>${total}</b> 处不能交付的内容：${parts.join(' · ')}（导出前必须清零）</span>
      ${summary.auto_fixable ? `<button type="button" class="cs-gate-act" data-gate-fix>一键清理 ${summary.auto_fixable} 处</button>` : ''}
      <button type="button" class="cs-gate-act" data-gate-show>在待办里处理</button>`;
  }

  /** 让"正在编写"的这一节保持在视野里：首次居中，之后跟随底部 */
  function keepWritingVisible(previewEl, { center = false } = {}) {
    if (!previewEl) return;
    const parts = previewEl.querySelectorAll('.cs-sec-writing');
    if (!parts.length) return;
    const target = center ? parts[0] : parts[parts.length - 1];
    try {
      target.scrollIntoView({ behavior: center ? 'smooth' : 'auto', block: center ? 'center' : 'nearest' });
    } catch (e) { /* 老浏览器忽略 */ }
  }

  /** 流式写一节：切到预览 → 高亮该节 → 逐字落入 → 完成后回正文 */
  async function streamWriteSection(blockIdNum, sec, onProgress, extraPrompt = '', role = 'text') {
    const base = String(editor.value || '');
    let acc = '';
    paintView(true);                       // 预览态（可高亮）
    const renderLive = (center = false) => {
      const previewEl = host.querySelector('[data-cs="preview"]');
      if (!previewEl) return;
      previewEl.innerHTML = previewContent(mergeSectionText(base, sec.title, acc || '正在编写…'));
      highlightWritingSection(previewEl, sec.title);
      keepWritingVisible(previewEl, { center });
    };
    renderLive(true);                      // 首次：把该节拉到屏幕中央
    const params = new URLSearchParams({
      thread_id: threadId,
      title: sec.title,
      points: (sec.points || []).join('||'),
      words: String(sec.words || 900),
      prompt: String(extraPrompt || ''),
      role: String(role || 'text'),
      model: modelChoice.get(role === 'text' ? 'text' : role) || '',
    });
    try {
      const finalContent = await new Promise((resolve, reject) => {
        const source = new EventSource(`/api/blocks/${blockIdNum}/studio/section/stream?${params.toString()}`);
        let lastPaint = 0;
        source.addEventListener('delta', (event) => {
          try { acc += JSON.parse(event.data).text || ''; } catch (e) { /* ignore */ }
          const now = Date.now();
          if (now - lastPaint > 120) { lastPaint = now; renderLive(false); onProgress && onProgress(acc.length); }
        });
        source.addEventListener('done', (event) => {
          source.close();
          let data = {};
          try { data = JSON.parse(event.data); } catch (e) { /* ignore */ }
          resolve(data.content || mergeSectionText(base, sec.title, acc));
        });
        source.addEventListener('error', () => { source.close(); reject(new Error('推流中断，请重试')); });
      });
      if (typeof finalContent === 'string') {
        editor.value = finalContent;
        changed && changed();
        const previewEl = host.querySelector('[data-cs="preview"]');
        keepWritingVisible(previewEl, { center: false });
      }
      return finalContent;
    } finally {
      paintView(false);                     // 回到编辑态
    }
  }

  const TODO_GROUPS = [
    ['content', '内容'], ['source', '来源'], ['budget', '篇幅'], ['figure', '图表'],
  ];

  /** 各角色的模型选择（本次/记住），key: text|rewrite|diagram|table|chief */
  const MODEL_ROLES = [['chief', '主编'], ['text', '文字编写'], ['rewrite', '文字精修'], ['diagram', '图片生成'], ['table', '表格']];
  let writingModels = {};          // 服务端生效值：{role: used}
  let lastSections = [];            // 最近一次作业的节级留痕（模型/字数/耗时）
  let currentJobId = '';           // 当前章节级作业（停止/重试用）

  /** 读服务端设置（章节覆盖 + 生效值） */
  async function loadWritingModels() {
    if (!active) return {};
    try {
      const resp = await fetch(`/api/blocks/${active.id}/writing-settings`, { cache: 'no-store', signal: AbortSignal.timeout(20000) });
      const data = await resp.json();
      const effective = data.effective || {};
      writingModels = {};
      for (const [role, info] of Object.entries(effective)) writingModels[role] = info.used || '';
      return { effective, settings: data.settings || {}, available: data.available || [] };
    } catch (error) { return {}; }
  }

  const modelChoice = {
    read() {
      try { return JSON.parse(globalThis.localStorage.getItem(`cs-model-choice:${projectId}`) || '{}'); } catch (e) { return {}; }
    },
    get(role) { return this.read()[role] || ''; },
    set(role, value) {
      const all = this.read();
      if (value) all[role] = value; else delete all[role];
      try { globalThis.localStorage.setItem(`cs-model-choice:${projectId}`, JSON.stringify(all)); } catch (e) { /* ignore */ }
    },
  };
  let modelOptions = [];

  async function loadModelOptions() {
    try {
      const data = await json('/api/configs');
      modelOptions = (data.configs || []).map((c) => c.model).filter(Boolean);
    } catch (e) { modelOptions = []; }
    return modelOptions;
  }

  /** 在 deliveryTasks 里找一张卡 */
  function findDeliveryTask(id) {
    const tasks = planBrief?.deliveryTasks || {};
    for (const zone of ['need_user', 'system', 'suggestion']) {
      const hit = (tasks[zone] || []).find((x) => x.id === id);
      if (hit) return hit;
    }
    return null;
  }

  let locateObserver = null, locateTimer = null;

  /** 在预览里持续贴高亮：即使预览被重新渲染也会重新找到并点亮（4 秒） */
  function paintLocateHighlight(needle, section) {
    const previewEl = host.querySelector('[data-cs="preview"]');
    if (!previewEl) return false;
    const blocks = [...previewEl.querySelectorAll('p, li, h2, h3, h4, td')];
    const normalized = (el) => (el.textContent || '').replace(/\s+/g, '');
    const hit = (needle && blocks.find((el) => normalized(el).includes(needle)))
      || (section && blocks.find((el) => normalized(el) === section.replace(/\s+/g, '')));
    if (!hit) return false;
    previewEl.querySelectorAll('.cs-locate-hit').forEach((el) => el.classList.remove('cs-locate-hit'));
    hit.classList.add('cs-locate-hit');
    let heading = hit;
    while (heading && !/^H[1-4]$/.test(heading.tagName)) heading = heading.previousElementSibling;
    if (heading) heading.classList.add('cs-locate-hit');
    try { hit.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { /* ignore */ }
    return true;
  }

  function startLocateHighlight(needle, section) {
    if (locateObserver) { locateObserver.disconnect(); locateObserver = null; }
    if (locateTimer) { globalThis.clearTimeout(locateTimer); locateTimer = null; }
    paintLocateHighlight(needle, section);
    const previewEl = host.querySelector('[data-cs="preview"]');
    if (previewEl && globalThis.MutationObserver) {
      locateObserver = new globalThis.MutationObserver(() => paintLocateHighlight(needle, section));
      locateObserver.observe(previewEl, { childList: true, subtree: true });
    }
    locateTimer = globalThis.setTimeout(() => {
      if (locateObserver) { locateObserver.disconnect(); locateObserver = null; }
      const el = host.querySelector('[data-cs="preview"]');
      el?.querySelectorAll('.cs-locate-hit').forEach((node) => node.classList.remove('cs-locate-hit'));
    }, 6000);
  }

  /** 定位到任务目标：**留在编辑区**选中原句并滚到那一行；已在预览则同时点亮该段 */
  function locateTaskTarget(task) {
    const target = task.target || {};
    const section = String(target.section || '');
    const quote = String(target.quote || '');
    if (!section && !quote) { status('这条没有定位信息，请直接在正文里找'); return; }

    const text = String(editor.value || '');
    // 1) 在正文里按原句定位（允许空白差异）
    let at = quote ? text.indexOf(quote) : -1;
    if (at < 0 && quote) {
      const compact = text.replace(/\s+/g, '');
      const needle = quote.replace(/\s+/g, '').slice(0, 12);
      const compactAt = compact.indexOf(needle);
      if (compactAt >= 0) {
        // 把"去空白后的位置"映射回原文位置
        let seen = 0, index = 0;
        for (; index < text.length; index += 1) {
          if (!/\s/.test(text[index])) {
            if (seen === compactAt) break;
            seen += 1;
          }
        }
        at = index;
      }
    }
    // 2) 找不到原句 → 退到"节标题"
    if (at < 0 && section) at = text.indexOf(`## ${section}`);
    if (at < 0) {
      status(`没在正文里找到这句话（${section || '本章'}）；原句：${quote.slice(0, 24)}… 可手动搜索`);
      return;
    }

    // 3) 留在编辑态并选中（可见、可直接改）
    paintView(false);
    editor.hidden = false;
    editor.focus();
    const end = at + Math.min(quote ? quote.length : (section ? section.length + 3 : 20), 200);
    editor.setSelectionRange(at, end);
    // 4) 滚动到选区附近（按比例估算，textarea 无法直接滚到行）
    const ratio = at / Math.max(1, text.length);
    editor.scrollTop = Math.max(0, Math.round((editor.scrollHeight - editor.clientHeight) * ratio) - 60);
    // 5) 若用户本来就在预览里，也把预览那段点亮（不强制切预览）
    if (previewVisible) {
      const previewEl = host.querySelector('[data-cs="preview"]');
      if (previewEl && !previewEl.innerHTML.trim()) previewEl.innerHTML = previewContent(text);
      startLocateHighlight(quote.replace(/\s+/g, '').slice(0, 10), section);
    }
    status(`已定位并选中：${section || '正文'} · 原句「${quote.slice(0, 26) || section}…」—— 可直接改`);
  }

  /** 任务动作分发：上传 / 选说法 / 查看 / 定位 / 精简 */
  async function runDeliveryAction(task) {
    if (!task) return;
    const type = (task.action || {}).type || '';
    if (type === 'upload_material') {
      pendingMaterialSection = String((task.target || {}).section || '');
      showPanel('materials');
      status(`请上传：${task.title}；上传后待办会自动复检`);
      globalThis.setTimeout(async () => {
        try {
          const resp = await fetch(`/api/projects/${projectId}/todo/recheck?block_id=${encodeURIComponent(String(active.block_id || ''))}`, { method: 'POST' });
          if (resp.ok) { await loadBasis(active.block_id); paintAside(); }
        } catch (error) { /* ignore */ }
      }, 20000);
      return;
    }
    if (type === 'choose') { locateTaskTarget(task); openDecideDialog(task); return; }
    if (type === 'locate') { locateTaskTarget(task); return; }
    if (type === 'condense_preview') { locateTaskTarget(task); status('精简预览将在「重写」面板里完成'); return; }
    if (type === 'gate_placeholder') {
      showPanel('write');
      status('在待办里逐条处理：占位符需要你补资料，或删掉这句断言');
      return;
    }
    if (type === 'add_citation') {
      locateTaskTarget(task);
      const btn = globalThis.document.querySelector('[data-cite-open]');
      if (btn) { globalThis.setTimeout(() => btn.click(), 700); status('已定位到这段引用；打开检索，选中来源后会插到引用处'); }
      else status('已定位到该段引用，请补【来源：…】');
      return;
    }
    if (type === 'search_now') {
      const btn = globalThis.document.querySelector('[data-cite-open]');
      if (btn) { btn.click(); status('已打开检索：把缺的依据逐条搜进本章（查到会自动挂上）'); }
      else status('已记录：系统会去公开来源检索这些依据');
      return;
    }
    if (type === 'enrich_citation') {
      status('系统正在补齐引用文号/标准号…');
      try {
        const resp = await fetch(`/api/blocks/${active.id}/studio/citation-enrich`, { method: 'POST', signal: AbortSignal.timeout(30000) });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
        if (!data.changed) { status('引用信息已完整，无需补齐'); return; }
        editor.value = data.content || editor.value;
        context = await json(`/api/blocks/${active.id}/studio`);
        await loadBasis(active.block_id); paintAside();
        await save();
        status(`已补齐引用（${(data.changes || []).join(' · ')}）并保存`);
      } catch (error) { status(`补齐失败：${error.message}`); }
      return;
    }
    if (type === 'soften_claim') {
      const sectionTitle = String(task.target?.section || '');
      const outline = planBrief?.outline || [];
      const section = outline.find((sec) => String(sec.title) === sectionTitle) || outline[0];
      if (!section) { status('没找到对应小节'); return; }
      status(`系统正在把「${section.title}」里需要举证的地方改成概括表述…`);
      try {
        await streamWriteSection(active.id, section, null,
          '把本节中需要业绩/材料举证的地方改为概括表述（例如"我方具备同类项目实施经验"），不要留任何占位符，也不要编造具体项目名称与数字；保留其余内容。',
          'rewrite');
        await save();
        await loadBasis(active.block_id); paintAside();
        status('已改为概括表述并保存（之后补到材料可再重写这一节）');
      } catch (error) { status(`改写失败：${error.message}`); }
      return;
    }
    if (type === 'rewrite_now') {
      const key = (task.action.payload || {}).key || '';
      const want = key === '指标' ? '目标' : '差距';
      const outline = planBrief?.outline || [];
      const section = outline.find((sec) => String(sec.title).includes(want)) || outline[0];
      if (!section) { status('没找到对应小节，请先在「本章计划」确认大纲'); return; }
      const extra = key === '指标'
        ? '重写本节：每个目标必须给出「指标名 + 目标值/阈值 + 验收口径 + 测量方法」，保留原有引用与数字。'
        : '重写本节：把「拟/将/待/有待」等含糊表述改成明确做法或指标，保留原有引用与数字。';
      status(`系统正在按契约重写「${section.title}」…`);
      await streamWriteSection(active.id, section, null, extra, 'rewrite');
      await save();
      await loadBasis(active.block_id); paintAside();
      status(`已重写「${section.title}」并保存（不满意：更多 → 版本历史可回退）`);
      return;
    }
    if (type === 'generate_chart' || type === 'write_now' || type === 'fix_now') {
      await runTaskQuickFix(task, type);
      return;
    }
    if (type.startsWith('system_')) { status(`系统任务：${task.title}`); return; }
    locateTaskTarget(task);
  }

  /** 卡上候选：直接应用一个改法（服务端按 locator 替换 + 写版本） */
  async function applyDecideOption(task, label) {
    const decide = (planBrief?.todoItems || []).find((x) => x.group === 'decide' && x.suggestions?.includes(label))
      || (planBrief?.todoItems || []).find((x) => x.group === 'decide');
    const quote = String((decide && decide.sentence) || task.target?.quote || '').trim();
    let replacement = (String(label).match(/改为「(.+?)」/) || [null, ''])[1]
      || (String(label).match(/拟按(.+?)。/) || [null, ''])[1];
    if (!replacement) {
      if (/删除/.test(label)) replacement = '';
      else if (/【待确认】/.test(label)) replacement = null;
    }
    if (replacement === null || !quote || replacement === undefined) {
      locateTaskTarget(task);
      status('这条需要你手动改写，已定位到原句');
      return;
    }
    try {
      const resp = await fetch(`/api/projects/${projectId}/todo/apply`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ block_id: String(active.block_id || ''), task_id: task.id,
                               section: task.target?.section || '', quote, replacement }),
        signal: AbortSignal.timeout(30000),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      const fresh = await json(`/api/blocks/${active.id}/studio`);
      context = fresh;
      if (fresh.block) editor.value = fresh.block.content || '';
      await loadBasis(active.block_id); paintAside();
      status('已按你选的改法替换并写版本，待办已重算');
    } catch (error) {
      status(`应用失败：${error.message}（可点「定位」手动改）`);
    }
  }

  /** 任务卡的"现在就做"：生成图表 / 补写该节 / 统一表述 */
  async function runTaskQuickFix(task, type) {
    try {
      if (type === 'generate_chart') {
      await generateAndInsertChart(task);
      return;
    }
    if (type === 'generate_chart_legacy') {
        const kind = (task.action.payload || {}).kind;
        const title = (task.action.payload || {}).title || '';
        const section = (planBrief?.outline || []).find((s) => (s.deliverables || []).some((d) => d.title === title));
        if (kind === 'figure') {
          openFigureAdjustDialog({ id: 'new', kind: 'ai', section: section ? section.title : '',
                                   title: title || '新图示', type: 'layered', nodes: 0, raw: '' });
        } else {
          status('表格生成：请在「插入 → AI 生成表格」里补一张'); showPanel('write');
        }
        return;
      }
      if (type === 'write_now') {
        const title = (task.action.payload || {}).title || '';
        const section = (planBrief?.outline || []).find((s) => String(s.title).includes(title.replace(/[「」]/g, '')));
        if (!section) { status('没找到对应节，请在「本章计划」里确认大纲'); return; }
        status(`正在补写「${section.title}」…`);
        await streamWriteSection(active.id, section);
        await loadBasis(active.block_id); paintAside();
        await save();
        status('已补写并保存（不满意：更多 → 版本历史可回退）');
        return;
      }
      if (type === 'fix_now') {
        const proof = (planBrief?.todoItems || []).find((x) => x.group === 'proof' && x.fix);
        if (!proof) { status('没有可自动统一的项'); return; }
        const pairs = (proof.fix.replacements || []);
        let text = String(editor.value || ''); let n = 0;
        for (const [find, repl] of pairs) {
          if (!find || find === repl) continue;
          const before = text.length;
          text = text.split(find).join(repl);
          if (text.length !== before) n += 1;
        }
        if (!n) { status('没有可替换的内容'); return; }
        editor.value = text; changed(); await save();
        status(`已统一 ${n} 类表述（已写版本，可撤销）`);
        return;
      }
    } catch (error) { status(error.message); }
  }

  /** 决策抽屉：把该类所有决定项列出来，逐条选 */
  function openDecideDialog(task) {
    const items = (planBrief?.todoItems || []).filter((x) => x.group === 'decide');
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>${esc(task.title)}</h3><button type="button" data-dd-close>×</button></header>
      <div class="cs-plan-body">
        ${items.length ? items.map((item, index) => `<div class="cs-decide-item">
            <p class="cs-task-why">${esc(item.evidence || item.title || '')}</p>
            <div class="cs-mp-list">${(item.suggestions || []).map((s, i) => `<label class="cs-mp-item">
                <input type="radio" name="dd-${index}" value="${i}" ${i === 0 ? 'checked' : ''} /><span>${esc(s)}</span></label>`).join('') || '<p class="cs-muted">这条没有候选，请直接在正文里改。</p>'}</div>
            <button type="button" class="cs-task-act" data-dd-apply="${index}">应用这条</button>
          </div>`).join('') : '<p class="cs-muted">没有需要决定的项目。</p>'}
      </div>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-dd-close]')) { dlg.close(); return; }
      const apply = event.target.closest('[data-dd-apply]');
      if (!apply) return;
      const index = Number(apply.dataset.ddApply);
      const item = items[index];
      const picked = dlg.querySelector(`input[name="dd-${index}"]:checked`);
      const choice = picked ? (item.suggestions || [])[Number(picked.value)] : '';
      dlg.close();
      if (!item || !choice) { status('这条没有候选，请手动修改'); return; }
      const quote = String(item.sentence || '').trim();
      const replacement = (String(choice).match(/改为「(.+?)」/) || [null, ''])[1];
      if (!quote || !replacement) { status('这条没有可自动应用的改法，请手动修改'); return; }
      try {
        const resp = await fetch(`/api/projects/${projectId}/todo/apply`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ block_id: String(active.block_id || ''), task_id: task.id,
                                 section: (task.target || {}).section || '', quote, replacement }),
          signal: AbortSignal.timeout(30000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
        const fresh = await json(`/api/blocks/${active.id}/studio`);
        context = fresh;
        if (fresh.block) editor.value = fresh.block.content || '';
        await loadBasis(active.block_id);
        paintAside();
        status('已应用（服务端替换并写版本），待办已重新计算');
      } catch (error) { status(`应用失败：${error.message}`); }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 「重新编写本模块…」：选范围 + 填提示词 + 强度 → 直接重写并保存（优化的唯一入口） */
  async function openRecomposeDialog() {
    const outline = planBrief?.outline || [];
    if (!outline.length) { status('还没有大纲：请先生成「本章计划」'); return; }
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>重新编写本模块</h3><button type="button" data-rc-close>×</button></header>
      <div class="cs-plan-body">
        <label class="cs-field"><span>重写范围</span>
          <select data-rc-scope>
            ${outline.map((sec, index) => `<option value="${index}">第 ${index + 1} 节 · ${esc(sec.title)}</option>`).join('')}
          </select></label>
        <label class="cs-field"><span>提示词（怎么改）</span>
          <textarea data-rc-prompt rows="4" placeholder="例如：写得更具体，把每个目标加上指标与验收口径；语言更简洁；补充安全设计细节…"></textarea></label>
        <label class="cs-field"><span>改动强度</span>
          <select data-rc-strength>
            <option value="conservative">保守：只改表述，不动结构</option>
            <option value="normal" selected>正常：按提示词改写这一节</option>
            <option value="aggressive">大改：可以重排结构与篇幅</option>
          </select></label>
        <p class="cs-muted">重写会覆盖这一节，并自动保存；不满意可在「更多 → 版本历史」回退。</p>
      </div>
      <footer class="cs-ins-foot">
        <button type="button" class="cs-cp-ghost" data-rc-cancel>取消</button>
        <button type="button" class="cs-cp-primary" data-rc-go>开始重写</button>
      </footer>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-rc-close]') || event.target.closest('[data-rc-cancel]')) { dlg.close(); return; }
      if (!event.target.closest('[data-rc-go]')) return;
      const index = Number(dlg.querySelector('[data-rc-scope]').value || 0);
      const prompt = String(dlg.querySelector('[data-rc-prompt]').value || '').trim();
      const strength = dlg.querySelector('[data-rc-strength]').value;
      const section = outline[index];
      dlg.close();
      const extra = [prompt, strength === 'conservative' ? '保持现有结构与篇幅，只改表述。'
        : strength === 'aggressive' ? '可以重排结构、调整篇幅，但必须保留引用与数字。' : ''].filter(Boolean).join(' ');
      status(`正在按提示词重写「${section.title}」…`);
      try {
        await streamWriteSection(active.id, section, null, extra, 'rewrite');
        await save();
        await loadBasis(active.block_id); paintAside();
        status(`已重写「${section.title}」并保存`);
      } catch (error) { status(`重写失败：${error.message}`); }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 「生成表格/图示」：系统直接生成并插入正文（不留占位、不需确认） */
  async function generateAndInsertChart(task) {
    const payload = task.action?.payload || {};
    const title = String(payload.title || '');
    const kind = String(payload.kind || '');
    const sectionTitle = String(task.target?.section || '');
    const role = kind === 'figure' ? 'diagram' : 'table';
    status(`正在生成${role === 'diagram' ? '图示' : '表格'}「${title}」并插入正文…`);
    try {
      const body = {
        block_id: active.id,
        thread_id: threadId,
        title,
        section: sectionTitle,
        role,
        deliverable: { kind, title, type: payload.type || 'layered', nodes: payload.nodes || 0 },
      };
      const resp = await fetch(`/api/projects/${projectId}/studio/${role === 'diagram' ? 'diagram' : 'table'}`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          signal: AbortSignal.timeout(180000) });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      const markdown = data.markdown || data.content || '';
      if (!markdown) throw new Error('没有生成出内容');
      let text = String(editor.value || '');
      // ① 有占位就替换占位；② 否则插入到该节末尾（并补引用句）
      const placeholder = new RegExp(`\\[\\[(FIGURE|TABLE):${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\]\\]`);
      if (placeholder.test(text)) {
        text = text.replace(placeholder, markdown);
      } else {
        text = insertIntoSection(text, sectionTitle, markdown);
      }
      text = ensureChartReference(text, sectionTitle, markdown);
      editor.value = text;
      changed();
      await save();
      await loadBasis(active.block_id); paintAside();
      status(`已生成并插入「${title}」（不满意：更多 → 版本历史回退）`);
    } catch (error) {
      status(`生成失败：${error.message}（该处已按完整文字处理，章节仍可交付）`);
    }
  }

  /** 把 markdown 插到某节末尾 */
  function insertIntoSection(content, sectionTitle, markdown) {
    const text = String(content || '');
    if (!sectionTitle) return `${text.trimEnd()}\n\n${markdown}\n`;
    const pattern = new RegExp(`(^##[ \\t]+${String(sectionTitle).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[ \\t]*\\n[\\s\\S]*?)(?=^##[ \\t]+|$(?![\\s\\S]))`, 'm');
    if (!pattern.test(text)) return `${text.trimEnd()}\n\n${markdown}\n`;
    return text.replace(pattern, (all) => `${all.trimEnd()}\n\n${markdown}\n\n`);
  }

  /** 系统产出的确认条：保留 / 撤销（不静默改稿） */
  function showProposalBar(title, desc, onKeep, onRevert) {
    return new Promise((resolve) => {
      const bar = host.querySelector('[data-cs="proposal"]') || (() => {
        const el = globalThis.document.createElement('div');
        el.className = 'cs-proposal-bar';
        el.dataset.cs = 'proposal';
        host.querySelector('.cs-toolbar')?.after(el);
        return el;
      })();
      bar.hidden = false;
      bar.innerHTML = `<span class="cs-proposal-title">${esc(title)}</span>
        <span class="cs-proposal-desc">${esc(desc || '')}</span>
        <button type="button" class="cs-gate-act" data-proposal-keep>保留</button>
        <button type="button" class="cs-gate-act" data-proposal-revert>撤销这版</button>`;
      const finish = async (keep) => {
        bar.hidden = true; bar.innerHTML = '';
        try { keep ? await onKeep() : await onRevert(); } catch (error) { status(error.message); }
        resolve(keep);
      };
      bar.addEventListener('click', (event) => {
        if (event.target.closest('[data-proposal-keep]')) void finish(true);
        if (event.target.closest('[data-proposal-revert]')) void finish(false);
      });
    });
  }

  /** 撤销最近一次 AI 写入（恢复上一版本） */
  async function restoreLastRevision() {
    try {
      const resp = await fetch(`/api/blocks/${active.id}/revisions`, { cache: 'no-store' });
      const data = await resp.json();
      const revisions = data.revisions || data || [];
      const target = revisions[0];
      const rn = target && (target.revision_no ?? target.rev_no ?? target.rn);
      if (!rn) { status('没有可撤销的版本'); return; }
      const restore = await fetch(`/api/blocks/${active.id}/revisions/${rn}/restore`, { method: 'POST' });
      if (!restore.ok) throw new Error(`HTTP ${restore.status}`);
      const fresh = await json(`/api/blocks/${active.id}/studio`);
      context = fresh;
      if (fresh.block) editor.value = fresh.block.content || '';
      await loadBasis(active.block_id); paintAside();
      status('已撤销这版，恢复到上一个版本');
    } catch (error) { status(`撤销失败：${error.message}`); }
  }

  /** 待办 v3：三区（需要你处理 / 系统处理中 / 建议）+ 「下一件」 */
  function deliveryTasksHtml(tasks) {
    const need = (tasks.need_user || []).filter((x) => !todoIgnored.has(x.id));
    const system = (tasks.system || []).filter((x) => !todoIgnored.has(x.id));
    const suggest = (tasks.suggestion || []).filter((x) => !todoIgnored.has(x.id));
    const summary = tasks.summary || {};
    const cardHtml = (task, opts) => {
      const impactClass = task.impact === 'export_block' ? ' is-export'
        : task.impact === 'completion_block' ? ' is-completion' : '';
      const locate = task.target && (task.target.section || task.target.quote);
      const occ = task.occurrences || [];
      const occurrenceHint = occ.length > 1 ? `<p class="cs-task-loc">共 ${occ.length} 处：${occ.map((o, i) => `第${i + 1}处（${esc(o.section || '正文')}）`).join(' · ')}</p>` : '';
      return `<div class="cs-task${impactClass}">
          <div class="cs-task-head"><b>${esc(task.title)}</b>
            <span class="cs-task-impact">${esc(task.impact_text || '')}</span></div>
          ${task.why ? `<p class="cs-task-why">${esc(task.why)}</p>` : ''}
          ${locate ? `<p class="cs-task-loc">${esc(task.target.section || '正文')}${task.target.quote ? ` · ${esc(String(task.target.quote).slice(0, 28))}` : ''}</p>` : ''}
          ${occurrenceHint}
          ${occ.length > 1 ? `<div class="cs-task-acts"><button type="button" class="cs-todo-ignore" data-task-next="${esc(task.id)}">下一处 →</button></div>` : ''}
          ${(task.options || []).length ? `<div class="cs-task-opts">${task.options.slice(0, 3).map((opt, i) => `
              <button type="button" class="cs-task-opt" data-task-apply="${esc(task.id)}" data-opt-index="${i}">${esc(opt.label)}</button>`).join('')}
            </div>` : ''}
          ${(task.materials_required || []).length ? `<div class="cs-task-materials">
            <p class="cs-task-mat-title">需要你上传：</p>
            <ul>${task.materials_required.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
            ${task.material_usage ? `<p class="cs-task-mat-meta">用在哪：${esc(task.material_usage)}</p>` : ''}
            ${task.material_format ? `<p class="cs-task-mat-meta">格式：${esc(task.material_format)}</p>` : ''}
          </div>` : ''}
          <div class="cs-task-acts">
            <button type="button" class="cs-task-act" data-task-act="${esc(task.action.type)}" data-task-id="${esc(task.id)}">${esc(task.action.label || '去处理')}</button>
            ${task.fallback ? `<button type="button" class="cs-todo-ignore" data-task-fallback="${esc(task.id)}">${esc(task.fallback.label || '没有这份材料')}</button>` : ''}
            ${locate && (task.action || {}).type !== 'locate' ? `<button type="button" class="cs-todo-ignore" data-task-locate="${esc(task.id)}">定位</button>` : ''}
            ${opts && opts.ignore ? `<button type="button" class="cs-todo-ignore" data-task-ignore="${esc(task.id)}" title="忽略">×</button>` : ''}
          </div>
        </div>`;
    };
    const next = need.slice().sort((a, b) => impactRank(a.impact) - impactRank(b.impact))[0];
    const score = summary.readiness;
    const scoreText = summary.readiness_text || '';
    const scoreHtml = (typeof score === 'number')
      ? `<div class="cs-readiness ${score >= 90 ? 'is-ok' : score >= 70 ? 'is-warn' : 'is-bad'}">
           <b>${score}</b><span>交付就绪度 · ${esc(scoreText)}</span></div>`
      : '';
    if (!need.length) {
      return `${scoreHtml}<h4>待办</h4><p class="cs-todo-ok">本章没有需要你处理的事</p>`;
    }
    return `${scoreHtml}<h4>待办 <small>需要你 ${need.length} 件</small></h4>
      ${next ? `<div class="cs-task-next">${cardHtml(next, { ignore: false })}</div>` : ''}
      ${need.length ? `<div class="cs-todo-group"><p class="cs-todo-glabel">需要你处理 ${need.length}</p>
        ${need.filter((x) => x.id !== (next && next.id)).map((x) => cardHtml(x, { ignore: true })).join('')}</div>` : ''}
      ${system.length ? `<details class="cs-todo-auto"><summary>系统处理中 ${system.length}（不用你操作）</summary>
        <div class="cs-todo-auto-body">${system.map((x) => cardHtml(x, {})).join('')}</div></details>` : ''}
      ${suggest.length ? `<details class="cs-todo-auto"><summary>建议优化 ${suggest.length}（不影响交付）</summary>
        <div class="cs-todo-auto-body">${suggest.map((x) => cardHtml(x, { ignore: true })).join('')}</div></details>` : ''}
      ${(!need.length && !system.length && !suggest.length) ? '<p class="cs-todo-ok">✓ 距离交付没有待办项</p>' : ''}`;
  }

  const impactRank = (impact) => ({ export_block: 0, completion_block: 1, quality: 2, suggestion: 3 }[impact] ?? 4);

  const todoIgnored = new Set();
  const todoIgnoreKey = () => `cs-todo-ignored-${active?.id || ''}`;
  function hydrateIgnored() {
    todoIgnored.clear();
    try {
      for (const id of JSON.parse(globalThis.localStorage.getItem(todoIgnoreKey()) || '[]')) todoIgnored.add(id);
    } catch (e) { /* ignore */ }
  }
  function persistIgnored() {
    try { globalThis.localStorage.setItem(todoIgnoreKey(), JSON.stringify([...todoIgnored])); } catch (e) { /* ignore */ }
  }
  const TODO_GROUPS_V2 = [
    ['gate', '交付门槛'], ['user', '你要给的'], ['decide', '要你拍板的'],
    ['proof', '要你核对的'], ['search', '系统去挖的'],
  ];

  function todoItemsHtml() {
    const all = (planBrief?.todoItems || []).filter((x) => x && !todoIgnored.has(x.id));
    if (!all.length) return '';
    const main = all.filter((x) => x.group !== 'auto');
    const auto = all.filter((x) => x.group === 'auto');
    const groups = TODO_GROUPS_V2.map(([key, label]) => {
      const rows = main.filter((x) => x.group === key);
      if (!rows.length) return '';
      const blocked = rows.some((x) => x.severity === 'block');
      return `<div class="cs-todo-group"><p class="cs-todo-glabel">${label} ${rows.length}${blocked ? ' · <b class="cs-todo-block">阻塞</b>' : ''}</p>
        ${rows.map((x) => `<div class="cs-todo-row">
            <span class="cs-todo-idx${x.severity === 'block' ? ' is-block' : ''}">•</span>
            <span class="cs-todo-text"><b>${esc(x.title)}</b>${x.evidence ? `<small>${esc(x.evidence)}</small>` : ''}
              <small class="cs-todo-src">来自：${esc(x.source || '系统')}${x.severity === 'block' ? ' · 阻塞交付' : ''}</small></span>
            <button type="button" class="cs-todo-act" data-todo2-act="${esc(x.action)}" data-todo2-id="${esc(x.id)}">${esc(x.action_label)}</button>
            <button type="button" class="cs-todo-ignore" data-todo2-ignore="${esc(x.id)}" title="忽略这一条">×</button>
          </div>`).join('')}</div>`;
    }).join('');
    const autoHtml = auto.length ? `<details class="cs-todo-auto"><summary>系统可自动修复 ${auto.length} 项</summary>
        ${auto.map((x) => `<div class="cs-todo-row"><span class="cs-todo-idx">·</span>
            <span class="cs-todo-text"><b>${esc(x.title)}</b>${x.evidence ? `<small>${esc(x.evidence)}</small>` : ''}</span>
            <button type="button" class="cs-todo-act" data-todo2-act="${esc(x.action)}" data-todo2-id="${esc(x.id)}">${esc(x.action_label)}</button></div>`).join('')}
      </details>` : '';
    return `<h4>待办 <small>${main.length} 项${auto.length ? ` · 可自动修 ${auto.length}` : ''}</small></h4>${groups || '<p class="cs-todo-ok">✓ 没有需要你处理的事项</p>'}${autoHtml}`;
  }

  /** 右栏「待办」页签标题上的计数徽标（口径与待办页签一致，排除素材类） */
  function paintTodoBadge() {
    const el = host.querySelector('[data-todo-badge]');
    if (!el) return;
    const tasks = planBrief?.deliveryTasks;
    const items = (planBrief?.todoItems || []).filter((x) => !todoIgnored.has(x.id));
    const count = tasks
      ? (tasks.need_user || []).filter((x) => !todoIgnored.has(x.id)).length
      : items.filter((x) => x.group !== 'auto').length;
    el.textContent = count ? String(count) : ''; el.hidden = !count;
  }

  /** 右栏「待办」页签：按处理模块分组，每条 = 模块 + 带数字的问题 + 一个动作 */
  function todoPaneHtml() {
    if (planBrief?.deliveryTasks) {
      return `<section data-pane="write">${deliveryTasksHtml(planBrief.deliveryTasks)}</section>`;
    }
    if ((planBrief?.todoItems || []).length) {
      return `<section data-pane="write">${todoItemsHtml()}</section>`;
    }
    const todos = (planBrief?.todo || []).filter((x) => (x.module || 'content') !== 'material');
    if (!todos.length) {
      return `<section data-pane="write"><h4>待办</h4>
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
    const checks = planBrief?.generationGaps || [];
    const checkGroup = checks.length
      ? `<div class="cs-todo-group"><p class="cs-todo-glabel"><i class="m-source"></i>依据核对 ${checks.length}
            <small>（表格里的【待补充】留在正文，按表填写）</small></p>
          ${checks.map((g) => `<div class="cs-todo-row">
              <span class="cs-todo-idx">·</span>
              <span class="cs-todo-text">${esc(g.text)}</span>
              <button type="button" class="cs-todo-act" data-gap-abate="${esc(g.id)}">去核对</button>
            </div>`).join('')}</div>`
      : '';
    return `<section data-pane="write"><h4>待办 <small>${todos.length + checks.length} 项 · 按优先级</small></h4>${groups}${checkGroup}</section>`;
  }

  /** 节级留痕：由哪个角色/模型生成、耗时（没有则空） */
  function sectionTrace(title) {
    const record = lastSections.find((item) => item.title === title);
    if (!record) return '';
    const parts = [`由 ${record.role === 'text' ? '文字' : record.role} ${record.model || ''} 生成`];
    if (record.words) parts.push(`${record.words} 字`);
    if (record.ms) parts.push(`${(record.ms / 1000).toFixed(1)}s`);
    return parts.join(' · ');
  }
  function sectionTraceShort(title) {
    const record = lastSections.find((item) => item.title === title);
    if (!record || !record.model) return '';
    return ` · ${record.model.replace(/^deepseek-/, '')}`;
  }

  /** 某节正文的字数（粗略：该 ## 节到下一个 ## 之间） */
  function sectionWordCount(content, title) {
    const esc = String(title).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`^##[ \\t]+${esc}[ \\t]*\\n([\\s\\S]*?)(?=^##[ \\t]+|$(?![\\s\\S]))`, 'm');
    const m = re.exec(String(content || ''));
    return m ? m[1].replace(/\s+/g, '').length : 0;
  }

  /** 交付物里要求、但正文里还没有的图/表（待补） */
  function pendingFigures(outline) {
    const bodyText = String(editor?.value || '');
    const present = parseFigures(bodyText);
    const pending = [];
    for (const sec of outline || []) {
      for (const d of sec.deliverables || []) {
        if (d.kind !== 'figure') continue;
        const hit = present.some((f) => f.section === sec.title && String(f.title).includes(String(d.title || '').slice(0, 4)));
        if (!hit) pending.push({ ...d, section: sec.title });
      }
    }
    return pending;
  }

  /** 解析正文里的图片：按节归属，区分 AI 生成（:::diagram）与素材（:::figure / ![]()） */
  function parseFigures(content) {
    const lines = String(content || '').split('\n');
    const figures = [];
    let section = '';
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i].trim();
      const head = /^##\s+(.+)$/.exec(line);
      if (head) { section = head[1].trim(); continue; }
      if (line.startsWith(':::diagram')) {
        const body = [];
        let j = i + 1;
        while (j < lines.length && lines[j].trim() !== ':::') { body.push(lines[j]); j += 1; }
        const m = /^:::diagram(?:\s+type=(\w+))?\s*(.*)$/.exec(line);
        const type = ['layered', 'flow', 'timeline'].includes(m && m[1]) ? m[1] : 'flow';
        const title = ((m && m[2]) || '结构关系图').trim();
        const nodes = type === 'layered'
          ? body.filter((b) => b.trim()).map((b) => b.replace(/^-+\s*/, ''))
          : (type === 'timeline'
              ? body.filter((b) => b.trim()).map((b) => (b.split(/[|:：、]/)[1] || b).trim())
              : body.join(' ').replaceAll('→', '->').split('->').map((x) => x.trim()).filter(Boolean));
        figures.push({ id: `fig-${figures.length + 1}`, kind: 'ai', section, type, title, nodes: nodes.length,
                       start: i, end: j, raw: lines.slice(i, j + 1).join('\n') });
        i = j;
        continue;
      }
      if (line.startsWith(':::figure')) {
        const body = [];
        let j = i + 1;
        while (j < lines.length && lines[j].trim() !== ':::') { body.push(lines[j]); j += 1; }
        const img = body.join(' ').match(/!\[([^\]]*)\]\(([^)]+)\)/);
        figures.push({ id: `img-${figures.length + 1}`, kind: 'material', section,
                       title: (line.slice(9).trim() || (img && img[1]) || '图片'),
                       url: img ? img[2] : '', start: i, end: j });
        i = j;
      }
    }
    return figures;
  }

  /** 右栏「进度」页签：章节编写进度（大纲进度 + 编写动作） */
  function progressPaneHtml() {
    const outline = planBrief?.outline || [];
    const bodyText = String(editor?.value || '');
    const escRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const escRe2 = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const done = new Set(outline.map((s) => s.title).filter((title) =>
      sectionWordCount(bodyText, title) >= 20));
    if (!outline.length) {
      return `<section data-pane="write"><h4>章节编写进度</h4>
        <p class="cs-todo-ok">还没有章节大纲 · 去章头「本章计划」生成大纲后开始编写</p></section>`;
    }
    const figures = parseFigures(bodyText);
    const figureCount = figures.length + pendingFigures(outline).length;
    const rows = outline.map((sec, i) => {
      const written = done.has(sec.title);
      const words = written ? sectionWordCount(bodyText, sec.title) : 0;
      const secFigs = figures.filter((f) => f.section === sec.title);
      const secPending = pendingFigures([sec]);
      const figBlock = (secFigs.length || secPending.length)
        ? `<details class="cs-sec-figs"><summary>🖼 ${secFigs.length + secPending.length}</summary>
             ${secFigs.map((f) => `<div class="cs-fig-row">
                 <span class="cs-fig-main" title="${esc(f.title)}">${esc(f.title)}</span>
                 <span class="cs-fig-meta">${f.kind === 'ai' ? `${({layered:'层次图',flow:'流程图',timeline:'时间线'})[f.type] || '图'} · ${f.nodes} 节点` : '素材图'}</span>
                 <span class="cs-fig-acts">${f.kind === 'ai'
                   ? `<button type="button" class="cs-todo-act" data-fig-adjust="${f.id}">调整</button>`
                   : `<button type="button" class="cs-todo-act" data-fig-view="${f.id}">查看</button>`}
                   <button type="button" class="cs-todo-act" data-fig-locate="${f.id}">定位</button></span>
               </div>`).join('')}
             ${secPending.map((d, k) => `<div class="cs-fig-row cs-fig-pending">
                 <span class="cs-fig-main">${esc(d.title)}</span>
                 <span class="cs-fig-meta">待补 · ${d.type === 'table' ? '表格' : '图'}</span>
                 <span class="cs-fig-acts"><button type="button" class="cs-todo-act" data-fig-create="${i}:${k}">生成</button></span>
               </div>`).join('')}
           </details>`
        : '';
      return `<div class="cs-sec-block">
          <div class="cs-todo-row">
            <span class="cs-todo-idx">${i + 1}</span>
            <span class="cs-todo-text" title="${esc(sectionTrace(sec.title))}"><b>${esc(sec.title)}</b>
              <small style="display:block;color:#94a3b8;font-size:10.5px">${written ? `已写 · ${words} 字` : '未写'}${sectionTraceShort(sec.title)}</small></span>
            <button type="button" class="cs-todo-act" data-aside-write-section="${i}">${written ? '重写' : '编写'}</button>
          </div>
          ${figBlock}
        </div>`;
    }).join('');
    return `<section data-pane="write"><h4>章节编写进度 <small>${done.size}/${outline.length} 节已写</small></h4>
      <div class="cs-progress-actions">
        <button type="button" class="cs-progress-btn" data-aside-write-all>${done.size ? '继续编写（补未写）' : '开始编写整章'}</button>
        <button type="button" class="cs-progress-btn" data-fig-review>审阅本章图片 ${figureCount}</button>
      </div>
      ${rows}</section>`;
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
            <small>把选中的文字 / 本章要点抽成图：层次 / 流程 / 时间线，自动判断结构</small></div>
          <button type="button" class="cs-ins-chip" data-ins-generate="info">生成</button></div>
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>AI 生成架构图</b>
            <small>按"接入 → 服务 → 数据"分层生成（等同层次图）</small></div>
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
        ${isTable
          ? '<div class="cs-ins-cols">列数 <select data-ins-cols><option>3</option><option>4</option><option>5</option></select></div>'
          : '<div class="cs-ins-cols">图型 <select data-ins-dtype><option value="auto">自动（推荐）</option><option value="layered">层次图</option><option value="flow">流程图</option><option value="timeline">时间线</option></select></div>'}
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
            : {text, title: active.title, type: dlg.querySelector('[data-ins-dtype]')?.value || 'auto'};
          const resp = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: AbortSignal.timeout(120000)});
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          const typeLabel = {flow: '流程图', layered: '层次图', timeline: '时间线'}[data.type] || '';
          result.innerHTML = `<p class="cs-muted">${kind === 'table' ? '表格草稿' : `${typeLabel || '图示'} · 节点 ${(data.nodes || []).length} 个`} · 模型 ${esc(data.model || '')}</p>
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
    saveState='saved'; savedAt=Date.now(); paintSaveState();
    paintTree();stats();get('completion').close();pendingCompletion=null;
    status('本章已确认完成；修改正文后将退回待核对。');
  }
  // TODO(挂起·编写模块): 技术规范书**关键要求（★标星 / mandatory）**需在「本章编制依据」里
  // 置顶并标星、给出计数。业务口径：挂载是"编写时的参考依据"，不参与目录生成。
  // 详见 docs/design/原型到代码-落点对照.md 「四、【挂起·待办】编写模块：技术规范书关键要求」。
  let planRetryPending = false;
  const paintAside = () => {
    profile[active.id]=context.profile;paintTree();stats();paintTodoBadge();
    // 自愈：计划数据还没到（或拉取失败）→ 补拉一次后重绘
    if (!planBrief && !planRetryPending && active?.block_id) {
      planRetryPending = true;
      void loadBasis(active.block_id).then(() => { planRetryPending = false; paintAside(); });
    }
    const layout = context.layouts[context.profile.layout];
    const basisKey = String(active.block_id);
    const basisCovered = basisInfo?.block_id === basisKey ? basisInfo : null;
    get('aside').innerHTML = `${todoPaneHtml()}
      ${progressPaneHtml()}
      <section data-pane="materials">${materialRequestsHtml()}${materialLibraryHtml()}${(() => { const gaps = planBrief?.gaps || []; const lackTotal = gaps.reduce((sum, g) => sum + (g.lack || 0), 0); const title = gaps.length ? `素材缺口 <small>缺 ${gaps.length} 类 · 共 ${lackTotal} 项</small>` : `素材缺口 <small>没有缺口</small>`; return `<h4>${title}</h4>${gaps.length ? gapGroupsHtml() : `<p class="cs-mat-empty">计划要求的素材已齐，可直接生成。</p>`}`; })()}
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
      get('chapter-head').innerHTML=`<h3>${esc(block.title)}</h3><span class="cs-chapter-state">${headState === '目录' ? '' : esc(headState)}</span><span class="cs-save-state" data-save-state></span>${budgetLineHtml(block)}<span class="cs-plan-slot" data-plan-slot></span>`;
      paintPlanAction(block);
      host.querySelector('[data-action="save"]').disabled=true;
      saveState='idle'; savedAt=0; if(autoSaveTimer){globalThis.clearTimeout(autoSaveTimer);autoSaveTimer=null;} paintSaveState();
      hydrateIgnored();
      void loadWritingModels().then(() => paintAside());
      void autoCleanChapter();
      void loadMaterialLibrary().then(() => paintAside());
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
    // 「进度」页签：开始/继续编写整章（同一引擎）
    if(event.target.closest('[data-aside-write-all]') && active){
      const btn=event.target.closest('[data-aside-write-all]');
      const label=btn.textContent; btn.disabled=true;
      try{
        const plan=await fetchChapterPlan(active.block_id);
        const outline=plan.outline||[];
        const bodyNow=String(editor.value||'');
        // 「已写」= 该节实义字数 ≥20（排除「（本节待写）」这类占位）
        const doneSet=new Set(outline.map(s=>s.title).filter(title=>sectionWordCount(bodyNow,title)>=20));
        const missing=outline.filter(s=>!doneSet.has(s.title));
        if(!outline.length) throw new Error('还没有章节大纲：请先在「本章计划」生成大纲');
        for(let i=0;i<missing.length;i+=1){
          btn.textContent=`编写中 ${i+1}/${missing.length}`;
          status(`正在编写「${missing[i].title}」…`);
          await streamWriteSection(active.id, missing[i]);
        }
        const result={written:missing.length,total:outline.length};
        status(result.written?`已补写 ${result.written} 节（共 ${result.total} 节）；请核对后保存`:'整章已按大纲写完');
        changed && changed();
        const fresh=await json(`/api/blocks/${active.id}/studio`); context=fresh;
        if(fresh.block)editor.value=fresh.block.content||'';
        paintAside();
      }catch(error){status(error.message);}
      btn.disabled=false; btn.textContent=label;
      return;
    }
    if(event.target.closest('[data-fig-review]')){openFigureReviewDialog();return;}
    const figAdjust=event.target.closest('[data-fig-adjust]');
    if(figAdjust && active){
      const f=parseFigures(String(editor?.value||'')).find(x=>x.id===figAdjust.dataset.figAdjust);
      if(f) openFigureAdjustDialog(f);
      return;
    }
    const figView=event.target.closest('[data-fig-view]');
    if(figView && active){
      const f=parseFigures(String(editor?.value||'')).find(x=>x.id===figView.dataset.figView);
      if(f?.url) globalThis.open(f.url,'_blank');
      return;
    }
    const figLocate=event.target.closest('[data-fig-locate]');
    if(figLocate && active){
      const f=parseFigures(String(editor?.value||'')).find(x=>x.id===figLocate.dataset.figLocate);
      if(f) locateSectionInEditor(f.section);
      return;
    }
    const figCreate=event.target.closest('[data-fig-create]');
    if(figCreate && active){
      const [si]=String(figCreate.dataset.figCreate).split(':');
      const sec=(planBrief?.outline||[])[Number(si)];
      if(sec) openFigureAdjustDialog({id:'new',kind:'ai',section:sec.title,title:`${sec.title}图示`,type:'layered',nodes:0,raw:''});
      return;
    }
    const secBtn=event.target.closest('[data-aside-write-section]');
    if(secBtn && active){
      const sec=(planBrief?.outline||[])[Number(secBtn.dataset.asideWriteSection)];
      if(!sec) return;
      const doWrite = async (prompt) => {
        const label0=secBtn.textContent; secBtn.disabled=true; secBtn.textContent='编写中…';
        try{
          status(`正在编写「${sec.title}」…（正文预览区高亮该节）`);
          secBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          await streamWriteSection(active.id,sec,(chars)=>status(`正在编写「${sec.title}」… 已 ${chars} 字`),prompt,'rewrite');
          const fresh=await json(`/api/blocks/${active.id}/studio`); context=fresh;
          if(fresh.block)editor.value=fresh.block.content||'';
          status(`已${label0.startsWith('重写')?'重写':'编写'}「${sec.title}」；记得保存`);
          paintAside();
        }catch(error){status(error.message);}
        secBtn.disabled=false; secBtn.textContent=label0;
      };
      if(secBtn.textContent.trim()==='重写'){ openRewriteDialog(sec, doWrite); }
      else { doWrite(''); }
      return;
    }
    if(event.target.closest('[data-job-stop]') && currentJobId){
      try{ await fetch(`/api/projects/${projectId}/studio/chapter-writes/${currentJobId}/cancel`,{method:'POST'});
        status('已停止后续调度；已完成的节保留'); }catch(error){ status(error.message); }
      return;
    }
    if(event.target.closest('[data-job-retry]') && currentJobId){
      try{ await fetch(`/api/projects/${projectId}/studio/chapter-writes/${currentJobId}/retry`,{method:'POST'});
        status('已重新排队，只补未写的节'); }catch(error){ status(error.message); }
      return;
    }
    const modelBtn=event.target.closest('[data-agent-model]');
    if(modelBtn){
      event.preventDefault();
      const role=modelBtn.dataset.agentModel;
      const options=await loadModelOptions();
      const current=modelChoice.get(role)||'';
      openModelPickerDialog(role, options, current);
      return;
    }
    const taskApply=event.target.closest('[data-task-apply]');
    if(taskApply && active){
      const task=findDeliveryTask(taskApply.dataset.taskApply);
      const index=Number(taskApply.dataset.optIndex||0);
      const option=(task.options||[])[index];
      if(!task || !option){status('这条没有可选项');return;}
      await applyDecideOption(task, option.label);
      return;
    }
    const citeLocate=event.target.closest('[data-cite-locate]');
    if(citeLocate){
      const [section, quote] = String(citeLocate.dataset.citeLocate||'').split('|');
      locateTaskTarget({ target: { section, quote } });
      return;
    }
    const mlApply=event.target.closest('[data-ml-apply]');
    if(mlApply && active){
      const ids=[...host.querySelectorAll('[data-ml-pick]:checked')].map((el)=>Number(el.dataset.mlPick));
      if(!ids.length){status('先在素材库里勾选要用的片段');return;}
      try{
        const resp=await fetch(`/api/blocks/${active.id}/materials/bind`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chunk_ids:ids,section:'',mode:'cite'})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside();
        status(`已把 ${data.added||ids.length} 段素材应用到本章`);
      }catch(error){status(`应用失败：${error.message}`);}
      return;
    }
    const mlConfirm=event.target.closest('[data-ml-confirm]');
    if(mlConfirm){
      const ids=[...host.querySelectorAll('[data-ml-pending]:checked')].map((el)=>Number(el.dataset.mlPending));
      if(!ids.length){status('先勾选要确认的片段');return;}
      try{
        const resp=await fetch(`/api/projects/${projectId}/materials/chunks`,{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({chunk_ids:ids,action:'confirm'})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside();
        status(`已确认 ${data.changed||ids.length} 段为素材`);
      }catch(error){status(`确认失败：${error.message}`);}
      return;
    }
    const mlUnbind=event.target.closest('[data-ml-unbind]');
    if(mlUnbind && active){
      try{
        await fetch(`/api/blocks/${active.id}/materials/bound/${mlUnbind.dataset.mlUnbind}`,{method:'DELETE'});
        await loadMaterialLibrary(); paintAside();
        status('已从本章移除该素材');
      }catch(error){status(error.message);}
      return;
    }
    const olBatch=event.target.closest('[data-ol-batch-write]');
    if(olBatch){
      host.querySelectorAll('dialog').forEach((d) => d.close());
      const headBtn = globalThis.document.querySelector('[data-action="ai"]');
      if(headBtn){ headBtn.click(); }
      else { status('请用章头的「编写整章」按写法批量生成'); }
      return;
    }
    const howSave=event.target.closest('[data-ol-how-save]');
    if(howSave){ await saveSectionPractice(Number(howSave.dataset.olHowSave || 0)); return; }
    const howReset=event.target.closest('[data-ol-how-reset]');
    if(howReset){
      const index=Number(howReset.dataset.olHowReset||0);
      const area=host.querySelector(`[data-ol-how="${index}"]`);
      const section=(planBrief?.outline||[])[index];
      if(area && section){
        const how=section.how||{};
        const parts=[how.structure, how.style, (how.pitfalls||[]).length?`避免：${(how.pitfalls||[]).join('、')}`:''].filter(Boolean);
        area.value = parts.length ? parts.join('\n') : '（按本节要点展开，先结论后分点，落到本项目具体工作）';
        status('已恢复系统默认写法（点「保存写法」生效）');
      }
      return;
    }
    const taskFallback=event.target.closest('[data-task-fallback]');
    if(taskFallback && active){
      const task=findDeliveryTask(taskFallback.dataset.taskFallback);
      if(task) await runDeliveryAction(task);
      return;
    }
    const taskIgnore=event.target.closest('[data-task-ignore]');
    if(taskIgnore && active){
      const task = findDeliveryTask(taskIgnore.dataset.taskIgnore);
      const isBlocking = task && task.impact !== 'suggestion';
      if (isBlocking && !globalThis.confirm('这条不处理，导出时会再次提醒；确定忽略？')) return;
      try{
        await fetch(`/api/projects/${projectId}/todo/state`,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({block_id:String(active.block_id||''),task_id:taskIgnore.dataset.taskIgnore,action:'ignore'})});
        await loadBasis(active.block_id); paintAside();
        status('已忽略（服务端记录，导出前仍会提醒）');
      }catch(error){status(error.message);}
      return;
    }
    const taskNext=event.target.closest('[data-task-next]');
    if(taskNext && active){
      const task=findDeliveryTask(taskNext.dataset.taskNext);
      const occ=(task && task.occurrences) || [];
      if(occ.length){
        taskNext._cursor = ((taskNext._cursor || 0) + 1) % occ.length;
        const o = occ[taskNext._cursor];
        locateTaskTarget({ ...task, target: { ...(task.target||{}), section: o.section, quote: o.quote } });
        taskNext.textContent = `下一处 →（第 ${taskNext._cursor + 1}/${occ.length}）`;
      }
      return;
    }
    const taskLocate=event.target.closest('[data-task-locate]');
    if(taskLocate && active){
      const task=findDeliveryTask(taskLocate.dataset.taskLocate);
      if(task) locateTaskTarget(task);
      return;
    }
    const taskAct=event.target.closest('[data-task-act]');
    if(taskAct && active){
      const task=findDeliveryTask(taskAct.dataset.taskId);
      await runDeliveryAction(task);
      return;
    }
    const todo2Ignore=event.target.closest('[data-todo2-ignore]');
    if(todo2Ignore){todoIgnored.add(todo2Ignore.dataset.todo2Ignore);persistIgnored();paintAside();return;}
    const todo2Act=event.target.closest('[data-todo2-act]');
    if(todo2Act && active){
      const action=todo2Act.dataset.todo2Act;
      const item=(planBrief?.todoItems||[]).find(x=>x.id===todo2Act.dataset.todo2Id);
      try{
        if(action==='upload'){showPanel('materials');return;}
        if(action==='search'){document.querySelector('[data-cite-open]')?.click();return;}
        if(action==='autofix'){
          const hit=/「(.+?)」/.exec(item?.title||'');
          if(hit){
            const plan=await fetchChapterPlan(active.block_id);
            const sec=(plan.outline||[]).find(s=>s.title===hit[1])||(plan.outline||[]).find(s=>hit[1].includes(s.title));
            if(sec){status(`正在补写「${sec.title}」…`);await streamWriteSection(active.id,sec);await loadBasis(active.block_id);paintAside();return;}
          }
          status('这一项需要手动处理');return;
        }
        if(action==='decide'){
          if(item?.suggestions?.length){ openSuggestionDialog(item); return; }
          const ev=String(item?.evidence||'');
          const hit=/^(.+?)：/.exec(ev);
          const sec=(planBrief?.outline||[]).find(s=>hit && s.title===hit[1]);
          if(sec){locateSectionInEditor(sec.title);}
          status(`需要你拍板：${item?.title||''}${item?.evidence?`（${item.evidence.slice(0,60)}）`:''}`);
          return;
        }
        if(action==='locate'){
          if(item?.fix){
            const pairs=item.fix.replacements||[];
            let text=String(editor.value||''); let n=0;
            for(const [find,repl] of pairs){
              if(!find||find===repl) continue;
              const before=text.length;
              text=text.split(find).join(repl);
              if(text.length!==before) n+=1;
            }
            if(n){editor.value=text;changed();await save();status(`已${item.fix.label||'统一表述'}（共 ${n} 类）；可撤销`);await loadBasis(active.block_id);paintAside();return;}
            status('没有可自动替换的内容');return;
          }
          const ev=String(item?.evidence||'');
          const hit=/^(.+?)：/.exec(ev);
          const sec=(planBrief?.outline||[]).find(s=>hit && s.title===hit[1]);
          if(sec) locateSectionInEditor(sec.title);
          status(`请核对：${item?.title||''}`);
          return;
        }
        if(action==='go'){
          if(/完成条件|正文为空/.test(item?.title||'')){document.querySelector('[data-action="complete"]')?.click();return;}
          status(item?.evidence||item?.title||'请按提示处理');return;
        }
      }catch(error){status(error.message);}
      return;
    }
    const asideTodo=event.target.closest('[data-aside-todo]');
    if(asideTodo && active){
      const action=asideTodo.dataset.asideTodo;
      if(action==='find'){openCitationDialog(String(active.block_id), asideTodo.dataset.gapKind || '');return;}
      if(action==='condense'){status('篇幅超额：可删减重复论述，或让 AI 重新精简本章');return;}
      // regen：直接触发本章重编
      host.querySelector('[data-action="ai"]')?.click();
      return;
    }
    const abate=event.target.closest('[data-gap-abate]');
    if(abate && active){
      const item=(planBrief?.generationGaps||[]).find(x=>String(x.id)===abate.dataset.gapAbate);
      const query=String(item?.text||'').replace(/^依据核对：/,'').slice(0,30);
      openCitationDialog(String(active.block_id), query);
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
        // 与「进度」页签的「继续编写（补未写）」同一套逻辑：逐节流式写入并高亮
        showPanel('write');
        const slot = get('agents');
        // 代价预估：先算待写节数，让用户知道要花多少
        try {
          const plan = await fetchChapterPlan(active.block_id);
          const outline = plan.outline || [];
          const bodyNow = String(editor.value || '');
          const pending = outline.filter((sec) => sectionWordCount(bodyNow, sec.title) < 20);
          if (!outline.length) { status('还没有章节大纲：请先在「本章计划」生成大纲'); return; }
          if (!pending.length) { status('整章已按大纲写完；要改某节请用「进度 → 重写」'); return; }
          const charts = pending.reduce((sum, sec) => sum + (sec.deliverables || []).filter((d) => d.kind !== 'text').length, 0);
          const calls = pending.length + charts;
          const ok = await confirmChapterWriteCost(pending.length, charts, calls);
          if (!ok) return;
        } catch (error) {
          status(`无法预估编写量：${error.message}`);
          return;
        }
        const headBtnEarly = globalThis.document.querySelector('[data-action="ai"]');
        const headLabelEarly = headBtnEarly ? headBtnEarly.textContent : '';
        const engine = await runChapterWriteJob(slot);
        if (engine.started) {
          if (headBtnEarly && headLabelEarly) headBtnEarly.textContent = headLabelEarly;
          return;
        }
        // 应急通道：章节级作业建不起来（后端旧版本/接口异常）时，退回前端逐节循环
        status('作业通道不可用，改用逐节编写…');
        const states = { chief: 'running', text: 'idle', diagram: 'idle', table: 'idle' };
        const paintAgents = () => { if (slot) slot.innerHTML = agentBarHtml(states); };
        void loadModelOptions();
        paintAgents();
        const headBtn = globalThis.document.querySelector('[data-action="ai"]');
        const headLabel = headBtn ? headBtn.textContent : '';
        try {
          const plan = await fetchChapterPlan(active.block_id);
          const outline = plan.outline || [];
          if (!outline.length) { if (slot) slot.innerHTML = ''; throw new Error('还没有章节大纲：请先在「本章计划」生成大纲'); }
          const bodyNow = String(editor.value || '');
          const missing = outline.filter((sec) => sectionWordCount(bodyNow, sec.title) < 20);
          if (!missing.length) { status('整章已按大纲写完；要改某节请用「进度 → 重写」'); return; }
          for (let i = 0; i < missing.length; i += 1) {
            if (headBtn) headBtn.textContent = `编写中 ${i + 1}/${missing.length}`;
            status(`正在编写「${missing[i].title}」…（${i + 1}/${missing.length}）`);
            const sec = missing[i];
            const needsTable = (sec.deliverables || []).some((d) => d.kind === 'table');
            const needsFigure = (sec.deliverables || []).some((d) => d.kind === 'figure');
            states.chief = 'running';
            states.text = 'running';
            states.table = needsTable ? 'running' : 'skip';
            states.diagram = needsFigure ? 'running' : 'skip';
            paintAgents();
            await streamWriteSection(active.id, sec);
            const bodyNow = String(editor.value || '');
            if (needsTable) {
              states.table = /^\s*\|/m.test(sectionText(bodyNow, sec.title)) ? 'done' : 'skip';
              paintAgents();
            }
            if (needsFigure) {
              states.diagram = /:::diagram/.test(sectionText(bodyNow, sec.title)) ? 'done' : 'skip';
              paintAgents();
            }
          }
          states.chief = 'done'; states.text = 'done'; states.table = 'done'; states.diagram = 'done'; paintAgents();
          globalThis.setTimeout(() => { if (slot) slot.innerHTML = ''; }, 4000);
          const fresh = await json(`/api/blocks/${active.id}/studio`);
          context = fresh;
          if (fresh.block) editor.value = fresh.block.content || '';
          paintAside();
          status(`已按大纲编写 ${missing.length} 节；请核对后保存`);
        } catch (error) {
          console.log('[chapter-studio] 编写整章失败', error);
          status(error && error.message ? error.message : String(error));
          if (slot) slot.innerHTML = '';
        } finally {
          if (headBtn && headLabel) headBtn.textContent = headLabel;
        }
        return;
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
      if(action==='link'){await json(`/api/blocks/${active.id}/studio/materials`,send('PUT',{material_id:Number(button.dataset.id),section:pendingMaterialSection||''}));pendingMaterialSection='';context=await json(`/api/blocks/${active.id}/studio`);paintAside();}
      if(action==='project-todo') { openProjectTodoDialog(); return; }
      if(action==='recompose') { await openRecomposeDialog(); return; }
      if(action==='writing-settings') { await openWritingSettingsDialog(); return; }
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
      })()}<p>本章已保存。请核对以下内容，再决定是否标记完成。</p>${(check.gaps.length||check.issues.length)?'<p class="cs-warning">下面这些问题仍在：你可以先修，也可以「带风险确认完成」（导出前会再提醒）。</p>':''}${check.gaps.length?`<details><summary>待补充 ${check.gaps.length} 项 · 查看明细</summary><ul>${check.gaps.map(g=>`<li>${esc(g)}</li>`).join('')}</ul></details>`:''}${check.issues.length?`<details><summary>AI 检查意见 ${check.issues.length} 项 · 查看明细</summary><ul>${check.issues.map(i=>`<li>${esc(i.point)}${i.suggestion?`：${esc(i.suggestion)}`:''}</li>`).join('')}</ul></details>`:''}${(() => {
          const blocking = (planBrief?.todoItems || []).filter((x) => x.severity === 'block' && x.group !== 'auto');
          if (!blocking.length) return '';
          return `<details open><summary>待办里仍有 ${blocking.length} 项阻塞交付</summary><ul>${blocking.slice(0, 6).map((x) => `<li>${esc(x.title)}${x.group_label ? `（${esc(x.group_label)}）` : ''}</li>`).join('')}${blocking.length > 6 ? `<li>…还有 ${blocking.length - 6} 项</li>` : ''}</ul></details>`;
        })()}<p class="cs-muted">${esc(check.notice)} 确认仅表示人工核对状态，不会自动修复问题。</p>`;
          const risky = check.gaps.length || check.issues.length;
          const confirmBtn = get('completion').querySelector('[data-action="completion-confirm"]');
          if (confirmBtn) confirmBtn.textContent = risky ? `带风险确认完成（${check.gaps.length + check.issues.length} 项待处理）` : '已核对，确认本章完成';
          get('completion').showModal();
        }finally{button.disabled=false;}
      }
      if(action==='completion-cancel'){get('completion').close();pendingCompletion=null;}
      if(action==='completion-confirm'&&pendingCompletion){
        button.disabled=true;try{await confirmComplete(pendingCompletion);}finally{button.disabled=false;}
      }
      if(action==='export-chapter'||action==='export-group') {
        if(!await save())return;
        await refreshDeliveryGate();
        const gateState = (gateInfo || {}).summary || {};
        if ((gateState.blocking || 0) > 0) {
          // 交付门未清零 → 用待办同一套阻塞确认（可以带风险导出）
          const blocking = (planBrief?.deliveryTasks?.need_user || []).filter((x) => x.impact === 'export_block');
          const ok = await openRiskExportDialog(
            blocking.length ? blocking
                            : [{ title: `${gateState.blocking} 处不能交付的内容`, group_label: '交付门', evidence: '占位符/内部语法/内部术语' }],
          );
          if (!ok) return;
        }
        const blocking=(planBrief?.todoItems||[]).filter(x=>x.severity==='block');
        if(blocking.length){
          const ok=await openRiskExportDialog(blocking);
          if(!ok)return;
        }
        const scope=action==='export-chapter'?'chapter':'group';
        const selected=exportScope(blocks,active.id,scope);
        status(`正在提交 ${selected.length} 个目录节点的已保存版本…`);
        await exportDocument({projectId,threadId,template:document.template,scope,blockId:active.id,slot:get('export-status')});
      }
    } catch(error){status(error.name==='TimeoutError'?'请求超时，原正文保留，请重试。':error.message);}
  };
  const onKeydown = event => {
    if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === 's') {
      event.preventDefault();
      if (dirty) void save();
    }
  };
  globalThis.document.addEventListener('keydown', onKeydown);
  const beforeUnload = event => {if(dirty){event.preventDefault();event.returnValue='';}};
  window.addEventListener('beforeunload',beforeUnload);
  const controller={host,select,dispose(){disposed=true;team.dispose();window.removeEventListener('beforeunload',beforeUnload);},save};
  const team={refresh(){},show(){},dispose(){}};   // 「本章编制进度」区块已移除（用户要求）
  currentStudio=controller;
  paintTree();stats();
  // 「最近导出任务」区块已移除（用户要求）
  await select((blocks.find(b=>units.has(b.id)&&!b.content?.trim())||blocks[0])?.id);
  return controller;
}