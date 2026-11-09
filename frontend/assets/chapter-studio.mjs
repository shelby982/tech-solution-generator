import {reverseAIChange, selectedRange, rebaseSelection} from './ai-revision.mjs?v=20261016-v503';
import {mountLiveWriting} from './chapter-live.mjs?v=20261016-v503';
import {specContentHtml} from './spec-content.mjs?v=20261016-v503';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = async (url, options = {}) => {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试');
  return data;
};
const send = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
const LAYOUT_GUIDES = {
  solution: {desc:'按“需求回应—技术路线—模块说明—验证交付”组织，突出架构、接口与可验证性。', required:['需求回应','技术路线','模块说明','验证交付'], prompt:'本章按技术方案类型拆节：先回应招标需求，再说明技术路线与架构，随后按模块说明功能与接口，最后给出验证方式与交付物。每一节都要能对应到评分要求或技术规范条款，避免写成通用介绍。'},
  understanding: {desc:'按“背景—目标—范围—重难点”组织，突出对招标需求的理解深度与针对性。', required:['背景','目标','范围','重难点'], prompt:'本章按项目理解类型拆节：先说明项目背景与建设动因，再写目标、建设范围和边界，最后分析重难点与应对思路。要引用招标文件和技术规范书的实际条款，避免泛泛而谈。'},
  implementation: {desc:'按“实施阶段—进度里程碑—交付保障”组织，突出工期、组织与风险控制。', required:['实施阶段','进度里程碑','交付保障'], prompt:'本章按实施计划类型拆节：按实施阶段展开，明确里程碑、工期安排、组织分工、质量与风险控制、验收交付。每节要有可执行的动作和时间点。'},
  evidence: {desc:'按“响应说明—真实信息—证明清单”组织，人员、证书、业绩必须有对应材料。', required:['响应说明','真实信息','证明清单'], prompt:'本章按人员/业绩证明类型拆节：先写响应说明，再列真 实人员、证书、业绩信息，最后给出证明材料清单和对应页码。没有材料的事实不要编造。'},
  point_to_point: {desc:'按“原文条款—逐项响应—证明材料”组织，保持条款顺序，不遗漏、不合并。', required:['原文条款','逐项响应','证明材料'], prompt:'本章按点对点应答类型拆节：按技术规范书原文顺序保留条款和编号，逐条给出响应、证明材料和偏离说明，不合并、不改写条款顺序。'},
};


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
export async function mountChapterStudio(host, {projectId, threadId, onStats, getModel, searchPoints = {}}) {
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
  const reviewStates = document.review_states || {};
  const draftKey = b => `chapter-draft:${threadId}:${b.id}`;
  host.innerHTML = `
    <div class="cs-document-head"><div hidden><strong>章节编写</strong><span data-cs="progress"></span></div>
      <div class="cs-document" hidden><select data-cs="template">${Object.entries(document.templates).map(([key,tt])=>`<option value="${key}" ${key===document.template?'selected':''}>${esc(tt.label)}</option>`).join('')}</select><span class="cs-muted" data-cs="format-summary"></span><a data-cs="format-sample" target="_blank" rel="noopener"></a></div>
    </div>
    <div class="cs-workspace">
      <aside class="cs-tree"><div class="cs-tree-label">应答目录</div><input type="search" data-cs="search" placeholder="搜索章节或评分点" aria-label="搜索章节或评分点"><div data-cs="tree"></div></aside>
      <main class="cs-main">
        <div class="cs-chapter-heading" data-cs="chapter-head"></div>

        <div data-cs="overview" hidden></div>
        <div class="cs-toolbar">
          <button data-action="ai" hidden aria-hidden="true" tabindex="-1">AI 编制</button>
          <span class="cs-plan-slot" data-plan-slot></span>
          <button data-action="save" disabled>保存</button><button data-action="submit-review">提交评审</button>
          <div class="cs-view-switch" role="group" aria-label="正文显示方式"><button data-action="edit" aria-pressed="true">编辑</button><button data-action="preview" aria-pressed="false">预览</button></div>
          <div class="cs-toolbar-right">
            <button type="button" class="cs-insert-btn" data-open-insert title="从官方来源 / 素材 / 结构模板插入内容">添加内容</button>
            <button type="button" data-open-panel="write" aria-controls="cs-chapter-inspector" aria-expanded="false">编制进度</button>
            <details class="cs-more"><summary>更多 ▾</summary><div class="cs-more-menu">
              <button data-action="writing-settings">编写设置</button>
              <button data-action="delivery">前往评审与交付</button>
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
      <aside class="cs-aside cs-inspector" id="cs-chapter-inspector" data-cs="inspector" hidden>
        <div class="cs-inspector-head"><strong data-cs="panel-title"></strong><button type="button" data-close-panel aria-label="关闭侧栏">关闭</button></div>
        <section data-pane="write"><div class="cs-agent-slot" data-cs="agents"></div></section>
        <div data-cs="aside"></div>
      </aside>
    </div>`;
  const get = key => host.querySelector(`[data-cs="${key}"]`);
  const editor = get('editor');
  let panelTrigger;
  const panelTitles = {write: '编制进度', materials: '素材', issues: '待处理'};
  const showPanel = (panel, trigger) => {
    const inspector = get('inspector');
    if (trigger) panelTrigger = trigger;
    inspector.hidden = !panel;
    inspector.dataset.panel = panel || '';
    get('panel-title').textContent = panelTitles[panel] || '';
    host.querySelectorAll('[data-open-panel]').forEach(button =>
      button.setAttribute('aria-expanded', String(button.dataset.openPanel === panel)));
    if (panel && window.innerWidth <= 980) inspector.scrollIntoView({block: 'nearest'});
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
  let planBrief = null;      // 本章计划摘要（待办数），用于章头按钮角标

  let materialLib = null;
  let materialEvidence = null;
  let pendingEvidenceNeed = null;
  let writingReadiness = null;

  /** 拉素材库数据（项目级素材 + 本章引用） */
  async function loadMaterialLibrary() {
    if (!active) return;
    try {
      const [recommendList, citeList, lib, pend, boundList, board] = await Promise.all([
        fetch(`/api/blocks/${active.id}/materials/recommend?limit=8`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/blocks/${active.id}/citations`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/projects/${projectId}/materials/library?status=confirmed&limit=80`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/projects/${projectId}/materials/library?status=pending&limit=80`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/blocks/${active.id}/materials/bound`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
        fetch(`/api/blocks/${active.id}/materials/evidence-board?threadId=${encodeURIComponent(threadId)}`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null),
      ]);
      materialEvidence = board;
      materialLib = { confirmed: lib?.items || [], counts: lib?.counts || {}, pending: pend?.items || [],
                      files: lib?.files || [], bound: boundList?.items || [],
                      citations: citeList || { points: [], missing: [], summary: {} },
                      recommend: recommendList || { items: [] } };
    } catch (error) { materialLib = null; materialEvidence = null; }
  }

  /** 素材库：两页签（我的文件 / 联网来源）+ 展开挑片段（勾选=素材）+ 应用到本章（选小节） */
  async function openMaterialAdminDialog() {
    const state = { tab: 'file', openId: '', chunks: [], visible: [], sources: [],
                    q: '', filter: '', sections: (planBrief?.outline || []).map((s) => s.title) };
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog cs-madmin';
    const closeAll = () => dlg.querySelectorAll('dialog').forEach((d) => d.close());

    const loadSources = async () => {
      const resp = await fetch(`/api/projects/${projectId}/material-sources?kind=${state.tab}`, { cache: 'no-store' });
      state.sources = resp.ok ? (await resp.json()).items || [] : [];
    };
    const loadChunks = async (sourceId) => {
      const resp = await fetch(`/api/projects/${projectId}/material-sources/${state.tab}/${sourceId}/chunks?limit=150`, { cache: 'no-store' });
      state.chunks = resp.ok ? (await resp.json()).items || [] : [];
      // 图片素材（仅文件出处）
      state.images = [];
      if (state.tab === 'file') {
        const imageResp = await fetch(`/api/projects/${projectId}/materials/${sourceId}/images`, { cache: 'no-store' });
        state.images = imageResp.ok ? (await imageResp.json()).items || [] : [];
      }
    };

    const render = () => {
      const srcHtml = state.sources.length ? state.sources.map((src) => `
        <div class="cs-msrc">
          <div class="cs-msrc-head">
            <b>${esc(src.title || '出处')}</b>
            <small>${src.chunks} 段 · 已选 ${src.picked}</small>
            <button type="button" class="cs-gate-act" data-msrc-open="${src.id}">${String(state.openId) === String(src.id) ? '收起' : '展开'}</button>
            <button type="button" class="cs-todo-ignore" data-msrc-del="${src.id}">删除</button>
          </div>
          ${String(state.openId) === String(src.id) ? `<div class="cs-msrc-tools">
              <input type="search" data-msrc-q placeholder="在本出处里搜索…" value="${esc(state.q || '')}" />
              <span class="cs-msrc-filters">
                ${[['', '全部'], ['picked', '只看已选'], ['data', '含数字'], ['clause', '条款'], ['table', '表格'], ['clean', '排除表单']]
                  .map(([key, label]) => `<button type="button" class="cs-msrc-filter ${String(state.filter || '') === key ? 'is-on' : ''}" data-msrc-filter="${key}">${label}</button>`).join('')}
              </span>
              <span class="cs-msrc-batch">
                <button type="button" class="cs-gate-act" data-msrc-all="1">全选</button>
                <button type="button" class="cs-gate-act" data-msrc-all="0">清空选择</button>
                <em data-msrc-count>已选 ${state.chunks.filter((c) => c.picked).length}</em>
              </span>
            </div>${(state.images || []).length ? `<div class="cs-msrc-images">
              <p class="cs-cite-sec">图片（${state.images.length}）</p>
              <div class="cs-msrc-image-list">
                ${state.images.map((img) => `<figure class="cs-msrc-image">
                  <img src="/api/studio/images/${img.id}/file" alt="${esc(img.caption || '图片')}" loading="lazy" />
                  <figcaption>${esc(img.caption || '图片')}${img.page ? ` · 第 ${img.page} 页` : ''}</figcaption>
                </figure>`).join('')}
              </div>
            </div>` : ''}<div class="cs-msrc-chunks">
            ${(() => {
              const visible = (state.chunks || []).filter((chunk) => {
                const q = String(state.q || '').trim();
                if (q && !String(chunk.text || '').includes(q)) return false;
                const filter = String(state.filter || '');
                const tags = chunk.tags || [];
                if (filter === 'picked') return chunk.picked;
                if (filter === 'data') return tags.includes('data') || /\d/.test(String(chunk.text || ''));
                if (filter === 'clause') return tags.includes('clause');
                if (filter === 'table') return tags.includes('table');
                if (filter === 'clean') return !tags.includes('boilerplate');
                return true;
              });
              state.visible = visible;
              if (!visible.length) return '<p class="cs-muted">当前筛选下没有片段</p>';
              return visible.slice(0, 200).map((chunk) => `
                <label class="cs-ml-item is-pick">
                  <input type="checkbox" data-mpick="${chunk.chunk_id}" ${chunk.picked ? 'checked' : ''} />
                  <div class="cs-ml-body"><p>${esc(String(chunk.text || '').slice(0, 90))}…</p>
                    <small>${(chunk.tags || []).includes('boilerplate') ? '⚠表单 · ' : ''}${chunk.path ? `${esc(String(chunk.path).slice(0, 28))} · ` : ''}${chunk.page ? `第 ${chunk.page} 页` : '网页片段'}</small></div>
                </label>`).join('');
            })()}
          </div>` : ''}
        </div>`).join('') : '<p class="cs-muted">还没有内容，点右上角「添加素材」</p>';

      dlg.innerHTML = `<header><h3>素材库</h3><button type="button" data-madmin-close>×</button></header>
        <div class="cs-madmin-tabs">
          <button type="button" data-mtab="file" class="${state.tab === 'file' ? 'is-on' : ''}">我的文件</button>
          <button type="button" data-mtab="web" class="${state.tab === 'web' ? 'is-on' : ''}">联网来源</button>
          <span class="cs-madmin-add">
            <button type="button" class="cs-gate-act" data-madmin-add>添加素材 ▾</button>
            <span class="cs-madmin-menu" hidden>
              <button type="button" class="cs-gate-act" data-madmin-upload>上传文件…</button>
              <button type="button" class="cs-gate-act" data-madmin-search>联网搜索…</button>
            </span>
          </span>
        </div>
        <div class="cs-plan-body">
          <input type="file" multiple hidden data-madmin-file accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.md,.png,.jpg,.jpeg" />
          <div class="cs-msrc-pick-preview" hidden data-pick-preview></div>
          <div class="cs-msrc-list">${srcHtml}</div>
        </div>
        <footer class="cs-ins-foot">
          <select class="cs-madmin-section" data-madmin-section>
            <option value="">整章</option>
            ${state.sections.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('')}
          </select>
          <button type="button" class="cs-cp-ghost" data-madmin-close2>关闭</button>
          <button type="button" class="cs-cp-primary" data-madmin-apply>应用到本章</button>
        </footer>`;
    };

    const paintDiagnosis = async () => {
      try {
        const resp = await fetch(`/api/projects/${projectId}/materials/source-diagnosis`, { cache: 'no-store' });
        const data = resp.ok ? await resp.json() : null;
        const textEl = dlg.querySelector('[data-diag-text]');
        const suggestEl = dlg.querySelector('[data-diag-suggest]');
        if (!data) { if (textEl) textEl.textContent = '诊断失败'; return; }
        if (textEl) {
          textEl.textContent = data.total
            ? `共 ${data.total} 段 · 可用正文型 ${data.usable} 段（${data.usable_ratio}%）· 已选素材 ${data.selected} 条`
            : '还没有上传文件';
        }
        if (suggestEl && (data.suggestions || []).length) {
          suggestEl.hidden = false;
          suggestEl.textContent = data.suggestions[0];
        }
      } catch (error) { /* ignore */ }
    };
    dlg._csState = state;
    dlg._csRender = render;
    render();
    await loadSources(); render();
    dlg.addEventListener('close', () => dlg.remove());

    dlg.addEventListener('click', async (event) => {
      const closeBtn = event.target.closest('[data-madmin-close]') || event.target.closest('[data-madmin-close2]');
      if (closeBtn) { dlg.close(); return; }
      if (event.target.closest('[data-madmin-add]')) {
        const menu = dlg.querySelector('.cs-madmin-menu');
        if (menu) menu.hidden = !menu.hidden;
        return;
      }
      if (event.target.closest('[data-madmin-upload]')) { dlg.querySelector('[data-madmin-file]')?.click(); return; }
      if (event.target.closest('[data-auto-pick]')) {
        const box = dlg.querySelector('[data-pick-preview]');
        if (box) { box.hidden = false; box.innerHTML = '<p class="cs-muted">正在按类型与章节质量筛选…</p>'; }
        try {
          const resp = await fetch(`/api/projects/${projectId}/materials/auto-pick?per_type=6`, { method: 'POST' });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
          const items = data.items || [];
          if (box) {
            box.innerHTML = `<div class="cs-pick-head"><b>智能备料建议 ${items.length} 条</b>
              <span class="cs-dim">${Object.entries(data.by_type || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</span>
              ${items.length ? '<button type="button" class="cs-cp-primary" data-auto-pick-apply>全部加入素材</button>' : ''}</div>
              ${items.length ? items.map((item) => `<div class="cs-pick-row">
                <span class="cs-pick-tag">${(item.tags || []).includes('data') ? '数据' : (item.tags || []).includes('policy') ? '政策' : (item.tags || []).includes('clause') ? '条款' : '资质'}</span>
                <span class="cs-pick-text">${esc(String(item.text || '').slice(0, 110))}…</span>
              </div>`).join('') : '<p class="cs-muted">没有筛出可用片段：建议补充技术规范书/评分标准（见上方诊断建议）</p>'}
              ${items.length ? `<div class="cs-pick-actions"><button type="button" class="cs-cp-ghost" data-auto-pick-hide>收起</button></div>` : ''}`;
            dlg._csPickIds = items.map((item) => item.chunk_id);
          }
        } catch (error) { status(`智能备料失败：${error.message}`); }
        return;
      }
      if (event.target.closest('[data-auto-pick-hide]')) {
        const box = dlg.querySelector('[data-pick-preview]');
        if (box) { box.hidden = true; box.innerHTML = ''; }
        return;
      }
      if (event.target.closest('[data-auto-pick-apply]')) {
        const ids = dlg._csPickIds || [];
        if (!ids.length) return;
        try {
          const resp = await fetch(`/api/projects/${projectId}/materials/auto-pick?per_type=6&apply=true`, { method: 'POST' });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          await loadSources(); render(); bindPickerTools(); paintDiagnosis();
          status(`已加入 ${data.picked || ids.length} 条素材`);
        } catch (error) { status(`加入失败：${error.message}`); }
        return;
      }
      if (event.target.closest('[data-madmin-search]')) {
        dlg.close();
        const btn = globalThis.document.querySelector('[data-cite-open]');
        if (btn) btn.click(); else status('请用「插入 → 官方来源」检索公开素材');
        return;
      }
      const tabBtn = event.target.closest('[data-mtab]');
      if (tabBtn) { state.tab = tabBtn.dataset.mtab; state.openId = ''; state.chunks = []; await loadSources(); render(); return; }
      const openBtn = event.target.closest('[data-msrc-open]');
      if (openBtn) {
        const id = String(openBtn.dataset.msrcOpen || '');
        if (state.openId === id) { state.openId = ''; state.chunks = []; render(); return; }
        state.openId = id; state.q = ''; state.filter = '';
        await loadChunks(id); render();
        bindFileInput();
        bindPickerTools();
        return;
      }
      const delBtn = event.target.closest('[data-msrc-del]');
      if (delBtn) {
        const id = String(delBtn.dataset.msrcDel || '');
        if (!globalThis.confirm('删除后，该出处里已选的素材一起失效（记录保留）。确定删除？')) return;
        const delResp = await fetch(`/api/projects/${projectId}/material-sources/${state.tab}/${id}`, { method: 'DELETE' });
        if (!delResp.ok) { status('删除失败，请重试'); return; }
        state.openId = ''; state.chunks = [];
        await loadSources(); render(); bindFileInput();
        status('已删除出处，其下素材标为失效');
        return;
      }
      if (event.target.closest('[data-madmin-apply]')) {
        const pickedIds = [...dlg.querySelectorAll('[data-mpick]:checked')].map((el) => Number(el.dataset.mpick));
        const section = String(dlg.querySelector('[data-madmin-section]')?.value || '');
        if (!pickedIds.length) { status('先在出处里勾选片段（勾选即素材）'); return; }
        const signed = state.tab === 'web' ? pickedIds.map((id) => -id) : pickedIds;
        try {
          const resp = await fetch(`/api/blocks/${active.id}/materials/bind`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chunk_ids: signed, section, mode: 'cite' }),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
          dlg.close();
          await loadMaterialLibrary(); paintAside();
          status(`已应用 ${data.added || signed.length} 段素材${section ? `到「${section}」` : '到整章'}`);
        } catch (error) { status(`应用失败：${error.message}`); }
        return;
      }
    });

    // 勾选 = 素材（立即写 selected）
    dlg.addEventListener('change', async (event) => {
      const box = event.target.closest('[data-mpick]');
      if (!box) return;
      try {
        await fetch(`/api/projects/${projectId}/material-sources/pick`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chunk_ids: [Number(box.dataset.mpick)], picked: box.checked, source_kind: state.tab }),
        });
        status(box.checked ? '已选为素材' : '已取消');
      } catch (error) { status(error.message); }
    });

    const bindFileInput = () => {
      const input = dlg.querySelector('[data-madmin-file]');
      if (!input) return;
      input.onchange = async (event) => {
        for (const file of event.target.files || []) {
          status(`正在上传 ${file.name}…`);
          try {
            const form = new FormData();
            form.append('file', file);
            await json(`/api/blocks/${active.id}/studio/materials`, { method: 'POST', body: form });
          } catch (error) { status(`${file.name}：${error.message}`); return; }
        }
        await loadSources(); render(); bindFileInput();
        status('已上传，解析完成后可在「我的文件」里展开挑片段');
      };
    };

    bindFileInput();
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 片段挑选器交互（v3）：搜索 / 筛选 / 批量全选（在素材库弹窗内） */
  function bindPickerTools() {
    const dlg = globalThis.document.querySelector('dialog.cs-madmin') || globalThis.document.querySelector('dialog.cs-plan-dialog');
    if (!dlg) return;
    const state = dlg._csState;
    if (!state) return;
    const rerender = () => { dlg._csRender(); bindPickerTools(); };

    dlg.querySelectorAll('[data-msrc-filter]').forEach((btn) => {
      btn.onclick = () => { state.filter = btn.dataset.msrcFilter || ''; rerender(); };
    });
    const search = dlg.querySelector('[data-msrc-q]');
    if (search) {
      search.oninput = () => {
        state.q = search.value;
        // 只重绘列表，避免输入框失焦
        const visible = (state.chunks || []).filter((chunk) => {
          const q = String(state.q || '').trim();
          if (q && !String(chunk.text || '').includes(q)) return false;
          const filter = String(state.filter || '');
          const tags = chunk.tags || [];
          if (filter === 'picked') return chunk.picked;
          if (filter === 'data') return tags.includes('data') || /\d/.test(String(chunk.text || ''));
          if (filter === 'clause') return tags.includes('clause');
          if (filter === 'table') return tags.includes('table');
          if (filter === 'clean') return !tags.includes('boilerplate');
          return true;
        });
        state.visible = visible;
        const list = dlg.querySelector('.cs-msrc-chunks');
        if (list) {
          list.innerHTML = visible.length ? visible.slice(0, 200).map((chunk) => `
            <label class="cs-ml-item is-pick">
              <input type="checkbox" data-mpick="${chunk.chunk_id}" ${chunk.picked ? 'checked' : ''} />
              <div class="cs-ml-body"><p>${esc(String(chunk.text || '').slice(0, 90))}…</p>
                <small>${(chunk.tags || []).includes('boilerplate') ? '⚠表单 · ' : ''}${chunk.path ? `${esc(String(chunk.path).slice(0, 28))} · ` : ''}${chunk.page ? `第 ${chunk.page} 页` : '网页片段'}</small></div>
            </label>`).join('') : '<p class="cs-muted">当前筛选下没有片段</p>';
        }
      };
    }
    dlg.querySelectorAll('[data-msrc-all]').forEach((btn) => {
      btn.onclick = async () => {
        const wanted = btn.dataset.msrcAll === '1';
        const visible = state.visible || state.chunks || [];
        const ids = visible.map((chunk) => chunk.chunk_id);
        if (!ids.length) return;
        try {
          await fetch(`/api/projects/${projectId}/material-sources/pick`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chunk_ids: ids, picked: wanted, source_kind: state.tab }),
          });
          visible.forEach((chunk) => { chunk.picked = wanted; });
          status(wanted ? `已选 ${ids.length} 条为素材` : `已取消 ${ids.length} 条`);
          rerender();
        } catch (error) { status(`批量操作失败：${error.message}`); }
      };
    });
    dlg.querySelectorAll('[data-mpick]').forEach((box) => {
      box.onchange = async () => {
        try {
          await fetch(`/api/projects/${projectId}/material-sources/pick`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chunk_ids: [Number(box.dataset.mpick)], picked: box.checked, source_kind: state.tab }),
          });
          const chunk = (state.chunks || []).find((item) => String(item.chunk_id) === String(box.dataset.mpick));
          if (chunk) chunk.picked = box.checked;
          const counter = dlg.querySelector('[data-msrc-count]');
          if (counter) counter.textContent = `已选 ${state.chunks.filter((c) => c.picked).length}`;
          status(box.checked ? '已选为素材' : '已取消');
        } catch (error) { status(error.message); }
      };
    });
  }

  /** 素材页签（方案 C + M3）：① 本章引用（主） ② 素材库（弹窗） */
  let chapterReview = null;
  let reviewHistory = [];
  let reviewDelta = null;
  let reviewIssueStates = {};      // 归一化问题文本 → status（open/fixed/verified）
  let reviewIssueEvidence = {};    // 归一化问题文本 → evidence（原文/口径）
  let precheckResult = null;       // L0 规则预检
  let deliveryVerdict = null;      // 统一交付判断
  let rubricResult = null;         // 章节达标线
  let practiceCandidates = [];     // 评审写法候选

  /** 达标线 + 写法候选（评审 v4） */
  async function loadRubricAndPractices() {
    if (!active) return;
    try {
      const [rubric, practices] = await Promise.all([
        fetch(`/api/blocks/${active.id}/studio/review/rubric`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch(`/api/blocks/${active.id}/studio/practice-candidates`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      rubricResult = rubric;
      practiceCandidates = practices?.candidates || [];
    } catch (error) { rubricResult = null; practiceCandidates = []; }
  }

  /** 达标线卡片：按章类型给出满分要素 + 逐条核对 */
  function rubricHtml() {
    const rubric = rubricResult;
    if (!rubric || !(rubric.items || []).length) return '';
    const met = rubric.met_count || 0;
    const total = rubric.total || 0;
    return `<details class="cs-rubric">
      <summary><b>达标线 ${met}/${total}</b>
        <span class="cs-dim">${met < total ? `未达 ${total - met} 项 · ` : ''}${esc(rubric.chapter_type === 'generic' ? '通用清单' : (rubric.chapter_type || '') + '类')} · 加权 ${rubric.ratio || 0}%</span></summary>
      ${rubric.items.map((item) => `<div class="cs-rubric-row ${item.met ? 'is-met' : 'is-miss'}">
        <span class="cs-rubric-mark">${item.met ? '✓' : '✗'}</span>
        <div><p><b>${esc(item.name)}</b> ${esc(item.need)}</p>
          <small>${item.met ? esc(String(item.evidence || '已满足')) : `建议：${esc(item.hint || '')}`}</small></div>
      </div>`).join('')}
    </details>`;
  }

  /** 写法候选：评审 strengths → 采用为写法 */
  function practiceCandidatesHtml() {
    if (!practiceCandidates.length) return '';
    return `<details class="cs-practice">
      <summary><b>评审建议写法 ${practiceCandidates.length} 条</b> <span class="cs-dim">采用后写入本节写法，下次生成会遵循</span></summary>
      ${practiceCandidates.map((item, index) => `<div class="cs-practice-row">
        <p>${esc(String(item.text || '').slice(0, 120))}</p>
        <button type="button" class="cs-gate-act" data-practice-adopt="${index}">采用为写法</button>
      </div>`).join('')}
    </details>`;
  }

  /** L0 规则预检 + 统一交付判断（v3） */
  async function loadPrecheckAndVerdict() {
    if (!active) return;
    try {
      const [pre, verdict] = await Promise.all([
        fetch(`/api/blocks/${active.id}/studio/review/precheck`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch(`/api/blocks/${active.id}/delivery-verdict`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      precheckResult = pre;
      deliveryVerdict = verdict;
    } catch (error) { precheckResult = null; deliveryVerdict = null; }
  }

  /** 统一交付判断条（一个数字 + 三条必办） */
  function deliveryVerdictHtml() {
    const verdict = deliveryVerdict;
    if (!verdict) return '';
    const cls = { red: 'is-red', yellow: 'is-yellow', green: 'is-green' }[verdict.level] || 'is-yellow';
    const top = verdict.top3 || [];
    return `<div class="cs-verdict ${cls}">
      <div class="cs-verdict-head">
        <b>交付判断 ${verdict.score}</b>
        <span class="cs-verdict-label">${esc(verdict.label)}</span>
        ${verdict.can_submit ? '<span class="cs-verdict-done">可以提交</span>' : '<span class="cs-verdict-todo">未达可提交</span>'}
        <span class="cs-dim">规则 ${verdict.explain?.rule ?? '-'} · 评审 ${verdict.explain?.review ?? '未评'} · 规则预检 ${(verdict.explain?.precheck?.critical ?? 0)} 硬伤 · 否决 ${verdict.explain?.fatal ?? 0}</span>
      </div>
      ${top.length ? `<ol class="cs-verdict-list">${top.map((item) => `<li>${esc(item.title)} <small>${esc(item.why)}</small></li>`).join('')}</ol>` : '<p class="cs-verdict-ok">没有必办事项</p>'}
    </div>`;
  }

  /** L0 规则预检条 */
  function precheckHtml() {
    const pre = precheckResult;
    if (!pre) return '';
    const counts = pre.counts || {};
    const critical = counts.critical || 0;
    const high = counts.high || 0;
    const medium = counts.medium || 0;
    if (!critical && !high && !medium) {
      return `<div class="cs-precheck is-pass"><b>规则预检通过</b><span class="cs-dim">${pre.body_len || 0} 字 · ${pre.sections || 0} 节</span></div>`;
    }
    const items = pre.items || [];
    return `<details class="cs-precheck ${critical ? 'is-fail' : 'is-warn'}">
      <summary><b>规则预检：${critical} 个硬伤 · ${high} 个问题 · ${medium} 个提醒</b>
        <span class="cs-dim">（秒级，未调模型）</span></summary>
      ${items.map((item) => `<div class="cs-precheck-row">
        <span class="cs-precheck-sev is-${esc(item.severity)}">${item.severity === 'critical' ? '硬伤' : item.severity === 'high' ? '问题' : '提醒'}</span>
        <div><p>${esc(item.message)}${item.section ? `（${esc(String(item.section).slice(0, 14))}）` : ''}</p>
          ${item.hint ? `<small>${esc(item.hint)}</small>` : ''}</div>
      </div>`).join('')}
    </details>`;
  }

  /** 拉本章评审（最近一次） */
  async function loadChapterReview() {
    if (!active) return;
    const reviewBlock = active;
    try {
      const resp = await fetch(`/api/blocks/${reviewBlock.id}/studio/review`, { cache: 'no-store' });
      const data = resp.ok ? await resp.json() : null;
      if (active?.id !== reviewBlock.id) return;
      chapterReview = data?.review || null;
      if (chapterReview) reviewStates[reviewBlock.id] = {freshness:chapterReview.freshness,satisfied:chapterReview.satisfied};
    } catch (error) { chapterReview = null; }
    try {
      const resp = await fetch(`/api/blocks/${reviewBlock.id}/studio/review/history?limit=10`, { cache: 'no-store' });
      const data = resp.ok ? await resp.json() : null;
      if (active?.id !== reviewBlock.id) return;
      reviewHistory = data?.items || [];
      reviewDelta = reviewHistory.length ? reviewHistory[0].delta : null;
    } catch (error) { reviewHistory = []; reviewDelta = null; }
    try {
      const resp = await fetch(`/api/blocks/${reviewBlock.id}/studio/review/issues`, { cache: 'no-store' });
      const data = resp.ok ? await resp.json() : null;
      if (active?.id !== reviewBlock.id) return;
      reviewIssueRows = data?.items || [];
      reviewIssueStates = {};
      reviewIssueEvidence = {};
      (data?.items || []).forEach((item) => {
        const key = normalizeIssuePoint(item.point);
        reviewIssueStates[key] = item.status;
        if (item.evidence) reviewIssueEvidence[key] = item.evidence;
      });
    } catch (error) { reviewIssueStates = {}; }
    try {
      const resp = await fetch(`/api/blocks/${reviewBlock.id}/studio/review/scoring-points`, { cache: 'no-store' });
      const data = resp.ok ? await resp.json() : null;
      if (active?.id === reviewBlock.id) scoringPointReview = data;
    } catch (error) { scoringPointReview = null; }
  }

  let reviewIssueRows = [];
  let scoringPointReview = null;
  let materialNeeds = { count: 0, items: [] };

  /** 拉"评审要求补充的材料"（issue → 素材联动） */
  async function loadMaterialNeeds() {
    try {
      const resp = await fetch(`/api/blocks/${active.id}/materials/needs`, { cache: 'no-store' });
      materialNeeds = resp.ok ? await resp.json() : { count: 0, items: [] };
    } catch (error) { materialNeeds = { count: 0, items: [] }; }
  }

  /** 与后端 review_issues.normalize_point 同口径（用于匹配 issue 状态徽标） */
  function normalizeIssuePoint(point) {
    return String(point || '').toLowerCase()
      .replace(/[\s，。；：、！？,.;:!?"'“”‘’（）()\[\]【】<>《》\-—_/\\|]+/g, '')
      .replace(/\d+/g, '#')
      .slice(0, 40);
  }

  /** 评分点关键词核对：评审的主结果 */
  function scoringPointHtml() {
    const data = scoringPointReview;
    if (!data || !(data.points || []).length) return '';
    return `<details class="cs-sp" open>
      <summary><b>评分点关键词核对 ${data.matched || 0}/${data.total || 0}</b>
        <span>关键词是否在本章正文出现 · 覆盖 ${Math.round((data.coverage || 0) * 100)}%</span></summary>
      ${data.points.map((point) => `<div class="cs-sp-point">
        <div class="cs-sp-head"><b>${esc(point.factor || '')}${point.factor && point.item ? ' · ' : ''}${esc(point.item || '')}</b>
          <em>${esc(String(point.points ?? ''))} 分</em>
          <span class="cs-sp-status ${point.status === '已回应' ? 'is-ok' : point.status === '部分回应' ? 'is-warn' : 'is-bad'}">${esc(point.status || '未回应')}</span></div>
        <p>${esc(String(point.criterion || '').slice(0, 160))}</p>
        <div class="cs-sp-keywords">${(point.keywords || []).map((kw) => `<span class="cs-sp-kw ${kw.matched ? 'is-ok' : 'is-miss'}" title="${esc(kw.evidence || '正文未出现')}">${kw.matched ? '✓' : '✗'} ${esc(kw.keyword)}</span>`).join('')}</div>
      </div>`).join('')}
    </details>`;
  }

  /** 编写页签：评审卡片（分数 + 评审按钮 + 意见列表） */
  function reviewBlockHtml() {
    const review = chapterReview;
    const sevLabel = { critical: '严重', high: '高', medium: '中', low: '低' };
    if (!review) {
      return `<div class="cs-review">
        <div class="cs-review-head"><b>评审</b><span class="cs-dim">还没有评审过</span>
          <button type="button" class="cs-gate-act" data-review-run>评审本章</button></div>
        <p class="cs-muted">按评分口径检查本章是否真正满足要求，并给出问题清单（不改正文）。</p>
      </div>`;
    }
    const issues = review.issues || [];
    const preCounts = precheckResult?.counts || {};
    const materialCount = (materialNeeds.items || []).length || (review.needs_material || []).length;
    return `<div class="cs-review">
      <div class="cs-review-summary">
        ${(scoringPointReview?.points || []).length
          ? `<span class="cs-review-sum-item">评分点 ${scoringPointReview.matched || 0}/${scoringPointReview.total || 0}</span>
             <span class="cs-review-sum-item ${preCounts.critical ? 'is-bad' : ''}">硬伤 ${preCounts.critical || 0}</span>`
          : `<span class="cs-review-sum-item is-bad">硬伤 ${preCounts.critical || 0}</span>
             <span class="cs-review-sum-item">评审问题 ${issues.length}</span>`}
      </div>
      ${scoringPointHtml()}
      <div class="cs-review-head">
        <div class="cs-review-score">
          <b>${review.freshness === 'stale' ? '正文或依据有变 · 待复核' : review.freshness === 'unknown' ? '历史评审 · 待确认' : '当前版本评审 ' + review.score}</b>
          <span>${review.stage === 'final' ? '终评' : '编写评审'} · 技术 ${review.tech_score} · 合规 ${review.comp_score}${reviewHistory.length > 1 ? ` · 第 ${reviewHistory.length} 轮` : ''}${review.tier === 'fast' ? ' · 本次=快评' : ''}</span>
        </div>
        <div class="cs-review-actions">
          <button type="button" class="cs-gate-act" data-review-run>重新评审</button>
          <label class="cs-auto-review" title="应用补丁/重写完成后自动重评一次">
            <input type="checkbox" data-auto-review ${globalThis.localStorage.getItem('cs_auto_review') === 'off' ? '' : 'checked'} />
            自动复评
          </label>
        </div>
      </div>
      ${precheckHtml()}
      ${rubricHtml()}
      ${reviewHistory.length > 1 ? `<details class="cs-review-fold">
        <summary>评审历史（${reviewHistory.length} 轮，最近 ${esc(String(reviewHistory[0].created_at || '').slice(5, 16))}）</summary>
        ${reviewHistory.map((item) => `<div class="cs-review-hist">
          <span>${esc(String(item.created_at || '').slice(5, 16))}</span>
          <b>${item.score}</b>
          <em>${item.delta === null ? '首轮' : (item.delta >= 0 ? `↑${item.delta}` : `↓${Math.abs(item.delta)}`)}</em>
          <small>${item.stage === 'final' ? '终评' : '编写评审'} · 技术 ${item.tech_score} · 合规 ${item.comp_score} · 问题 ${item.issues_count}</small>
        </div>`).join('')}
      </details>` : ''}
      ${issues.length && !(scoringPointReview?.points || []).length ? `<details class="cs-review-fold" ${issues.length <= 3 ? 'open' : ''}>
        <summary>问题清单（${issues.length}）</summary>
        ${issues.map((issue, index) => `<div class="cs-review-issue">
          <span class="cs-review-sev is-${esc(String(issue.severity || 'medium').toLowerCase())}">${sevLabel[String(issue.severity || '').toLowerCase()] || '中'}</span>
          ${(() => {
            const status = reviewIssueStates[normalizeIssuePoint(issue.point)] || 'open';
            const map = { open: ['未修', 'is-open'], fixed: ['已修待验', 'is-fixed'], verified: ['已验', 'is-verified'] };
            const [label, cls] = map[status] || map.open;
            return `<span class="cs-issue-state ${cls}">${label}</span>`;
          })()}
          <div class="cs-review-body">
            <p>${esc(String(issue.point || '').slice(0, 120))}</p>
            ${issue.suggestion ? `<small>建议：${esc(String(issue.suggestion).slice(0, 90))}</small>` : ''}
            ${issue.needs_material ? '<small class="cs-review-mat">需补料</small>' : ''}
            ${(() => {
              const ev = reviewIssueEvidence[normalizeIssuePoint(issue.point)] || null;
              if (!ev) return '';
              if (!ev.locatable) return '<small class="cs-review-ev is-miss">依据：无法定位到原文</small>';
              return `<small class="cs-review-ev">依据：${esc(String(ev.quote || '').slice(0, 70))}${ev.criterion ? ` · 口径「${esc(String(ev.criterion).slice(0, 30))}」` : ''}</small>`;
            })()}
          </div>
          <div class="cs-review-issue-actions">
            ${(() => {
              const row = reviewIssueRows.find((x) => normalizeIssuePoint(x.point) === normalizeIssuePoint(issue.point));
              if (!row) return '';
              return `<button type="button" class="cs-todo-ignore" data-issue-locate="${esc(row.fingerprint)}">定位</button>
                ${row.status === 'verified' ? '' : `<button type="button" class="cs-todo-ignore" data-issue-verify="${esc(row.fingerprint)}">复核</button>`}`;
            })()}
            ${issue.needs_material ? '' : `<button type="button" class="cs-todo-ignore" data-review-rewrite="${index}">按此重写</button>`}
          </div>
        </div>`).join('')}
      </details>` : '<p class="cs-todo-ok">✓ 评审没有发现问题</p>'}
      ${practiceCandidatesHtml()}
    </div>`;
  }

  function materialLibraryHtml() {
    const lib = materialLib || { bound: [] };
    const bound = lib.bound || [];
    return `<div class="cs-material-simple">
      <div class="cs-material-card">
        <div class="cs-material-card-head"><b>用户上传</b><span>文件</span></div>
        <p>上传文件后解析为片段；勾选需要的片段，应用到本章某一节。不做正确性判定。</p>
        <div class="cs-material-actions">
          <button type="button" class="cs-cp-primary" data-material-admin>上传 / 选片段</button>
        </div>
      </div>
      <div class="cs-material-card">
        <div class="cs-material-card-head"><b>联网搜索</b><span>公开来源</span></div>
        <p>搜索公开来源并选择片段；应用逻辑与上传一致，不做缺口判断。</p>
        <div class="cs-material-actions">
          <button type="button" class="cs-cp-primary" data-cite-search>联网搜索</button>
        </div>
      </div>
      ${bound.length ? `<details class="cs-material-bound"><summary>已应用到本章（${bound.length}）</summary>
        ${bound.map((item) => `<div class="cs-material-bound-row"><span>${esc(item.filename || '素材')}${item.section ? ` · ${esc(item.section)}` : ''}</span><button type="button" class="cs-todo-ignore" data-ml-unbind="${item.chunk_id}">移除</button></div>`).join('')}
      </details>` : ''}
    </div>`;
  }

  /** 素材页签顶部：本章"需要你上传的材料"（招标硬性要求才有；没有就不显示） */
  let planMaterials = null;

  /** 拉"每节建议素材"（计划阶段确认用） */
  async function loadPlanMaterials() {
    if (!active) return;
    try {
      const resp = await fetch(`/api/blocks/${active.id}/plan/material-suggestions?per_section=3`,
                              { cache: 'no-store', signal: AbortSignal.timeout(60000) });
      planMaterials = resp.ok ? await resp.json() : null;
    } catch (error) { planMaterials = null; }
  }

  /** 计划面板里每节的素材块（建议 + 已确认） */
  function planMaterialHtml(index, sectionTitle) {
    const item = (planMaterials?.sections || []).find((s) => String(s.section) === String(sectionTitle));
    if (!item) return '';
    const candidates = item.candidates || [];
    if (!candidates.length) {
      return `<div class="cs-ol-mat"><p class="cs-ol-mat-title"><b>素材</b><span class="cs-dim">素材库没有匹配到的片段（本节将写概括表述）</span></p></div>`;
    }
    return `<div class="cs-ol-mat">
      <p class="cs-ol-mat-title"><b>素材</b><span class="cs-dim">建议 ${candidates.length} 条 · 已确认 ${item.confirmed_count}${
        (() => {
          const cited = ((materialLib?.citations?.points) || []).filter((p) => String(p.section) === String(sectionTitle)).length;
          return cited ? ` · 正文引用 ${cited} 处` : '';
        })()}</span></p>
      ${candidates.map((c, i) => `<label class="cs-ol-mat-row">
        <input type="checkbox" data-ol-mat="${index}" data-mat-chunk="${c.chunk_id}" ${c.confirmed ? 'checked' : ''} />
        <span class="cs-ol-mat-body">
          <span class="cs-ol-mat-text">${esc(String(c.text || '').slice(0, 90))}${(c.text || '').length > 90 ? '…' : ''}</span>
          <small>${(c.tags || []).includes('boilerplate') ? '⚠ 表单/范本类 · ' : ''}${c.source_kind === 'web' ? '联网' : '文件'}${c.page ? ` · 第 ${c.page} 页` : ''} · ${esc(c.reason || '')}</small>
          ${(c.text || '').length > 90 ? `<details class="cs-ol-mat-more"><summary>看全文</summary><p>${esc(c.text)}</p></details>` : ''}
        </span>
      </label>`).join('')}
      <div class="cs-ol-mat-acts">
        <button type="button" class="cs-cp-primary" data-ol-mat-apply="${index}">采用所选</button>
        <button type="button" class="cs-cp-ghost" data-ol-mat-clear="${index}">清空本节素材</button>
      </div>
    </div>`;
  }

  /** 采用某节的所选素材 */
  async function applySectionMaterials(index) {
    const outline = planBrief?.outline || [];
    const section = outline[index];
    if (!section) return;
    const boxes = [...host.querySelectorAll(`[data-ol-mat="${index}"]`)];
    const ids = boxes.filter((el) => el.checked).map((el) => Number(el.dataset.matChunk));
    try {
      await json(`/api/blocks/${active.id}/plan/material-suggestions/apply`, {
        method: 'POST', body: { section: section.title, chunk_ids: ids, mode: 'auto' },
      });
      await loadPlanMaterials(); await loadMaterialLibrary();
      status(ids.length ? `已确认「${section.title}」的 ${ids.length} 条素材` : `已清空「${section.title}」的素材`);
      openChapterPlan();
    } catch (error) { status(`确认失败：${error.message}`); }
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
      status(`已保存「${section.title}」的生成提示词；批量编写时按此执行`);
    } catch (error) { status(`保存失败：${error.message}`); }
  }

  /** 依据 ↔ 正文对应 + 本章计划摘要（待办数）。 */
  async function loadBasis(blockKey) {
    if (!blockKey) { planBrief = null; return; }
    try {
      const [planResp, readyResp] = await Promise.all([
        fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockKey)}/plan?threadId=${encodeURIComponent(threadId)}&_=${Date.now()}`, {signal: AbortSignal.timeout(30000), cache: 'no-store'}),
        fetch(`/api/blocks/${encodeURIComponent(active?.id || '')}/writing-readiness?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(20000), cache:'no-store'}).catch(() => null),
      ]);
      writingReadiness = readyResp?.ok ? await readyResp.json() : null;
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
      if ((planBrief?.outline || []).length && writingReadiness?.status === 'no_outline') {
        try {
          const readyResp = await fetch(`/api/blocks/${encodeURIComponent(active?.id || '')}/writing-readiness?threadId=${encodeURIComponent(threadId)}`, {cache:'no-store'});
          if (readyResp.ok) { writingReadiness = await readyResp.json(); }
        } catch (error) { /* ignore */ }
      }
    } catch (e) { planBrief = null; }
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
      await loadPlanMaterials();
      const resp = await fetch(`/api/projects/${projectId}/chapters/${encodeURIComponent(blockId)}/plan?threadId=${encodeURIComponent(threadId)}`, {signal: AbortSignal.timeout(30000)});
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(payload.detail || `HTTP ${resp.status}`);
      try {
        const planId = payload.plan_id || '';
        const [coverageResp, rebuildResp] = await Promise.all([
          planId ? fetch(`/api/projects/${projectId}/writing-plans/${encodeURIComponent(planId)}/coverage?threadId=${encodeURIComponent(threadId)}`).catch(() => null) : null,
          fetch(`/api/projects/${projectId}/studio/context-rebuild?threadId=${encodeURIComponent(threadId)}`).catch(() => null),
        ]);
        const coverage = coverageResp?.ok ? await coverageResp.json() : null;
        const rebuild = rebuildResp?.ok ? await rebuildResp.json() : null;
        payload.coverage = (coverage?.chapters || []).find((c) => String(c.block_id) === String(blockId)) || null;
        payload.context_stale = (rebuild?.stale || []).find((b) => String(b.block_id) === String(blockId)) || null;
      } catch (error) { /* 覆盖率/上下文版本失败不阻断计划 */ }
      // chapter_plan 可能在此期间自动补建了大纲；重新取一次就绪度，避免章头仍显示“未生成大纲”。
      if ((payload.outline || []).length && writingReadiness?.status === 'no_outline') {
        try {
          const readyResp = await fetch(`/api/blocks/${encodeURIComponent(active?.id || '')}/writing-readiness?threadId=${encodeURIComponent(threadId)}`, {cache:'no-store'});
          if (readyResp.ok) { writingReadiness = await readyResp.json(); paintReadinessChip(); }
        } catch (error) { /* ignore */ }
      }
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
      const requirements = data.requirements || [];
      const reqCoverage = data.coverage?.requirements || [];
      const specRequirements = requirements.filter((r) => r.source === 'spec' || r.spec_requirement_id);
      const normalRequirements = requirements.filter((r) => !(r.source === 'spec' || r.spec_requirement_id));
      const coveredCount = reqCoverage.filter((r) => r.responded).length;
      const staleContext = data.context_stale;
      const hasOutline = (data.outline || []).length > 0;
      const directoryLocked = !!context?.directory_locked;
      const layoutId = (context?.profile || {}).layout || data.layout || 'solution';
      const layoutGuide = LAYOUT_GUIDES[layoutId] || LAYOUT_GUIDES.solution;
      const planLayout = String(data.layout || '');
      const layoutStale = hasOutline && !!planLayout && planLayout !== layoutId;
      const layoutPrompt = String((context?.profile || {}).layout_prompt || layoutGuide.prompt || '');
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
        <details class="cs-cp-block" data-cp-sec="type" open>
          <summary><b><span class="cs-cp-idx">1</span>章节类型与结构</b><em>${esc(layoutGuide.desc ? ((context?.layouts || {})[layoutId]?.label || layoutId) : layoutId)}</em></summary>
          <div class="cs-cp-fold-body cs-cp-type">
            <label class="cs-cp-kind-row">本章类型
              <select data-cp-layout>${Object.entries(context?.layouts || {}).map(([id, l]) => `<option value="${id}" ${id === layoutId ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
            </label>
            <p class="cs-cp-type-desc">生成提示词（本章大纲）</p>
            <textarea class="cs-cp-type-prompt" data-cp-layout-prompt rows="5" placeholder="填写本章大纲的生成提示词">${esc(layoutPrompt)}</textarea>
            <div class="cs-cp-type-actions">
              <button type="button" class="cs-cp-ghost" data-cp-layout-prompt-reset>恢复默认</button>
              <button type="button" class="cs-cp-primary" data-cp-layout-prompt-save>保存提示词</button>
            </div>
            <p class="cs-cp-type-desc" style="margin-top:12px">本章统一要求（每一节都要遵守）</p>
            <textarea class="cs-cp-type-prompt" data-cp-chapter-req rows="4"
              placeholder="例：全章口径统一；每节先结论后依据；禁止出现待补充/待填写；不得编造未提供的数字与业绩。">${esc(context?.chapter_requirement || '')}</textarea>
            <div class="cs-cp-type-actions">
              <button type="button" class="cs-cp-ghost" data-cp-chapter-req-clear>清空</button>
              <button type="button" class="cs-cp-primary" data-cp-chapter-req-save>保存统一要求</button>
            </div>
            <p class="cs-cp-type-state ${layoutStale ? 'is-warn' : ''}">${hasOutline ? (layoutStale ? '类型与大纲不一致：类型已变更，建议按新类型重建大纲。' : '类型与大纲：一致。') : '还没有大纲：先按当前类型生成章节大纲。'}</p>
          </div>
        </details>

        <details class="cs-cp-block" data-cp-sec="outline" open>
          <summary><b><span class="cs-cp-idx">2</span>章节大纲</b><em>${hasOutline ? `${data.outline.length} 节` : '未生成（0 节）'}</em>
            ${(directoryLocked && hasOutline) ? '' : `<button type="button" class="cs-cp-link" data-cp-build-outline title="按评分点与交付物重新拆分章节（会覆盖现有大纲）">${hasOutline ? '重做大纲' : '生成大纲'}</button>`}</summary>
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
                  <span class="cs-ol-meta">${(sec.requirement_ids || []).length ? `要求 ${(sec.requirement_ids || []).length} · ` : '章节级 · '}${sec.words || 900} 字</span>
                </summary>
                <div class="cs-ol-detail">
                  <div class="cs-ol-how">
                    <p class="cs-ol-how-title"><b>本节写法</b><span class="cs-dim">（只影响这一节；章级统一要求在上面 ① 会自动叠加）</span></p>
                    <textarea class="cs-ol-how-text" data-ol-how="${i}" rows="4">${esc(practiceOf(sec))}</textarea>
                    <div class="cs-ol-how-acts">
                      <button type="button" class="cs-cp-ghost" data-ol-how-save="${i}">保存</button>
                      <button type="button" class="cs-cp-primary" data-ol-how-gen="${i}">重新编写该小节</button>
                    </div>
                  </div>
                  ${planMaterialHtml(i, sec.title)}
                  <p><b>交付物</b>${deliv || '文字'}</p>
                </div>
              </details>`;
            }).join('') : '<p class="cs-cp-empty">还没有大纲。点「生成大纲」先出 6~10 节（编写入口在右栏「进度」）。</p>'}
          </div>
        </details>






        ${(isParent || isCatalog) ? `<details class="cs-cp-block" data-cp-sec="children" open>
          <summary><b><span class="cs-cp-idx">3</span>子节统筹</b><em>${children.length} 节 · 共 ${children.reduce((sum, c) => sum + (c.pages || 0), 0)} 页</em></summary>
          ${isParent ? '<p class="cs-cp-howto">本章只写总述（300–500 字）：先全貌 → 3 个关键结论 → 子节索引，细节见下列各节。</p>' : ''}
          ${children.map((child) => `<div class="cs-cp-kid" data-cp-kid="${esc(child.block_id)}" role="button" tabindex="0">
              <span class="cs-cp-kid-title">${esc(child.title)}</span>
              <span class="cs-cp-kid-bar"><i style="width:${child.pages ? Math.min(100, Math.round((child.written_pages || 0) / child.pages * 100)) : 0}%"></i></span>
              <small>${child.written_pages || 0} / ${child.pages || '—'} 页</small>
              <em class="${child.has_content ? 'ok' : ''}">${child.has_content ? '有正文' : '未开始'}</em>
            </div>`).join('')}
        </details>` : ''}
        `;
      bindEvents();
      // 计划弹窗内的批量/提示词按钮挂在 dialog 上，host.onclick 捕获不到。
      if (!dialog.dataset.actionBound) {
        dialog.dataset.actionBound = '1';
        dialog.addEventListener('click', async (event) => {
          if (event.target.closest('[data-ol-batch-write],[data-ol-how-save],[data-ol-how-gen],[data-ol-mat-apply],[data-ol-mat-clear]')) {
            await host.onclick(event);
          }
        });
      }
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
      body.querySelector('[data-cp-rebuild-context]')?.addEventListener('click', async () => {
        if (!globalThis.confirm('按新上下文重写本章？会覆盖现有正文，但会写版本记录，可回滚。')) return;
        dialog.close();
        await runChapterWriteJob(null, {forceRewrite: true});
      });
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
            body: JSON.stringify({thread_id: threadId, regenerate: (data.outline || []).length > 0}), signal: AbortSignal.timeout(180000),
          });
          const out = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(out.detail || `HTTP ${resp.status}`);
          data.outline = out.outline || [];
          status(`已生成 ${data.outline.length} 节大纲`);
          await render();
        } catch (error) { status(error.message); btn.disabled = false; btn.textContent = label; }
      });
      body.querySelector('[data-cp-layout-prompt-reset]')?.addEventListener('click', () => {
        const guide = LAYOUT_GUIDES[(context?.profile || {}).layout] || LAYOUT_GUIDES.solution;
        const area = body.querySelector('[data-cp-layout-prompt]');
        if (area) area.value = guide.prompt || '';
        status('已恢复当前类型的默认提示词（点「保存提示词」生效）');
      });
      body.querySelector('[data-cp-chapter-req-save]')?.addEventListener('click', async (event) => {
        const area = body.querySelector('[data-cp-chapter-req]');
        const value = String(area?.value || '').trim();
        const btn = event.currentTarget;
        btn.disabled = true;
        try {
          const updated = await json(`/api/blocks/${active.id}/studio`, send('PUT', {
            layout: (context?.profile || {}).layout,
            chapter_requirement: value,
            writing_status: (context?.profile || {}).writing_status,
          }));
          context.profile = {...context.profile, ...updated};
          if (context) context.chapter_requirement = value;
          status(value ? `已保存本章统一要求（${value.length} 字，每节写作都会遵守）` : '已清空本章统一要求');
        } catch (error) { status(`保存失败：${error.message}`); }
        finally { btn.disabled = false; }
      });
      body.querySelector('[data-cp-chapter-req-clear]')?.addEventListener('click', async () => {
        const area = body.querySelector('[data-cp-chapter-req]');
        if (area) area.value = '';
        try {
          const updated = await json(`/api/blocks/${active.id}/studio`, send('PUT', {
            layout: (context?.profile || {}).layout,
            chapter_requirement: '',
            writing_status: (context?.profile || {}).writing_status,
          }));
          context.profile = {...context.profile, ...updated};
          if (context) context.chapter_requirement = '';
          status('已清空本章统一要求');
        } catch (error) { status(`清空失败：${error.message}`); }
      });
      body.querySelector('[data-cp-layout-prompt-save]')?.addEventListener('click', async () => {
        const area = body.querySelector('[data-cp-layout-prompt]');
        const prompt = String(area?.value || '').trim();
        try {
          const updated = await json(`/api/blocks/${active.id}/studio`, send('PUT', {
            layout: (context?.profile || {}).layout,
            layout_prompt: prompt,
            writing_status: (context?.profile || {}).writing_status,
          }));
          context.profile = {...context.profile, ...updated};
          status('已保存本章大纲生成提示词');
        } catch (error) { status(`保存失败：${error.message}`); }
      });
      body.querySelector('[data-cp-layout]')?.addEventListener('change', async (event) => {
        const previous = (context?.profile || {}).layout;
        const target = event.target.value;
        const oldPlanLayout = data.layout || previous;
        const hasOutline = (data.outline || []).length > 0;
        const targetPrompt = (LAYOUT_GUIDES[target] || LAYOUT_GUIDES.solution).prompt || '';
        const promptArea = body.querySelector('[data-cp-layout-prompt]');
        if (promptArea) promptArea.value = targetPrompt;
        try {
          const updated = await json(`/api/blocks/${active.id}/studio`,
            send('PUT', {layout: target, layout_prompt: targetPrompt, writing_status: (context?.profile || {}).writing_status}));
          context.profile = {...context.profile, ...updated};
          const changed = !!oldPlanLayout && oldPlanLayout !== target;
          const rebuild = hasOutline && changed
            ? globalThis.confirm(`本章类型将从「${(context.layouts[oldPlanLayout] || {}).label || oldPlanLayout}」改为「${(context.layouts[target] || {}).label || target}」。\n\n是否按新类型重建大纲？\n取消则保留现有大纲，并标记为“类型与大纲不一致”。`)
            : false;
          if (hasOutline && changed && !rebuild) {
            // 保留旧大纲，仅改类型；render 会显示不一致状态。
            data.layout = oldPlanLayout;
            await render();
            status('类型已切换；现有大纲未重建，建议后续按新类型重建');
            return;
          }
          data.layout = target;
          if (hasOutline && changed && rebuild) {
            const resp = await fetch(`/api/blocks/${active.id}/studio/outline`, {
              method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({thread_id: threadId, regenerate:true, allow_locked:true}), signal: AbortSignal.timeout(180000),
            });
            const out = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(out.detail || `HTTP ${resp.status}`);
            data.outline = out.outline || [];
          }
          await render();
          status(`本章类型已改为：${(context.layouts[target] || {}).label || target}${hasOutline && changed ? '；大纲已按新类型重建' : ''}`);
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
        const allRequirements = data.requirements || [];
        const editableRequirements = allRequirements.filter((requirement) => !(requirement.source === 'spec' || requirement.spec_requirement_id));
        const readOnlySpecs = allRequirements.filter((requirement) => requirement.source === 'spec' || requirement.spec_requirement_id);
        const rows = editableRequirements.map((requirement) => `<div class="cs-req-row" data-cp-req-row>
            <select data-cp-req-type>${['text','table','figure','material'].map((kind) => `<option value="${kind}" ${requirement.type === kind ? 'selected' : ''}>${kindLabel[kind]}</option>`).join('')}</select>
            <input type="text" data-cp-req-text value="${esc(requirement.text)}" />
            <button type="button" class="cs-req-del" data-cp-req-del>×</button>
          </div>`).join('');
        edit.hidden = false;
        edit.innerHTML = `<h5 class="cs-cp-sub">编辑本章要求</h5>${readOnlySpecs.length ? '<p class="cs-dim">技术规范条款来自技术规范书，只读，不在此编辑。</p>' : ''}${rows || '<p class="cs-dim">还没有要求</p>'}
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
          })).filter((item) => item.text).concat(readOnlySpecs);
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

  /** 设计版图表：Diagram Design 技能（架构/分层/流程/时间线/矩阵…）。
   * 只产出一张图片资产：HTML/SVG 源在服务端，正文插入 PNG。
   */

  /** 生产级部署拓扑：正文 → 结构化 spec → 确定性 SVG/PNG。 */

  /** 生产级图模板：分层/数据流/时序/甘特/矩阵/鱼骨/金字塔/雷达/组织。 */
  async function openProductionDiagramDialog(defaultTemplate = 'layered') {
    if (!active) return;
    const TEMPLATES = [
      ['layered', '分层架构图'], ['data_flow', '数据流图'], ['sequence', '时序图'],
      ['gantt', '甘特/阶段图'], ['matrix', '矩阵对照图'], ['fishbone', '鱼骨图'],
      ['pyramid', '金字塔/目标分层'], ['radar', '能力雷达'], ['org', '组织/职责图'],
    ];
    const outline = (planBrief?.outline || []).filter((s) => s && s.title);
    const sectionNames = outline.map((s) => String(s.title));
    const defaultSection = sectionNames[0] || active.title || '本章';
    const sectionFor = (name) => outline.find((s) => String(s.title) === name) || null;
    const scoreRefsFor = (sec) => (sec?.basis || [])
      .filter((b) => b && String(b.type || '') === 'score_point' && b.ref)
      .map((b) => String(b.ref));
    const sectionBodyFor = (name) => {
      const escapeReg = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const lines = String(editor.value || '').split(/\n/);
      const re = new RegExp('^\\s{0,3}#{1,4}\\s*' + escapeReg(name) + '\\s*$');
      const start = lines.findIndex((line) => re.test(line));
      if (start < 0) return '';
      const body = [];
      for (const line of lines.slice(start + 1)) {
        if (/^\s{0,3}#{1,4}\s+/.test(line)) break;
        body.push(line);
      }
      return body.join('\n').trim().slice(0, 8000);
    };
    const figureNo = active.block_id
      ? `图 ${String(active.block_id).replace(/^s/, '')}-${(String(editor.value || '').match(/:::figure\b/g) || []).length + 1}`
      : '图';
    const labelOf = (key) => (TEMPLATES.find(([k]) => k === key) || [, '生产图'])[1];
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog cs-design-dialog';
    dlg.innerHTML = `<header><h3>生产级图模板</h3><button type="button" data-pr-close>×</button></header>
      <div class="cs-plan-body">
        <p class="cs-muted">选模板 → 从正文抽事实 → 代码确定性渲染；不会让模型即兴画线。</p>
        <div class="cs-dd-row">
          <label class="cs-field"><span>图模板</span><select data-pr-template>${TEMPLATES.map(([key, label]) => `<option value="${esc(key)}"${key === defaultTemplate ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select></label>
          <label class="cs-field"><span>对应章节</span><select data-pr-section>${(sectionNames.length ? sectionNames : [defaultSection]).map((name) => `<option value="${esc(name)}"${name === defaultSection ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></label>
        </div>
        <label class="cs-field"><span>图号 / 图名</span><input data-pr-title maxlength="160" value="${esc(figureNo + ' ' + labelOf(defaultTemplate))}" /></label>
        <label class="cs-field"><span>关联评分点 / 条款（可选，不必填）</span><input data-pr-score maxlength="360" value="" placeholder="不填也可以；只有明确关联时才填" /></label>
        <label class="cs-field"><span>结论（可选）</span><input data-pr-claim maxlength="300" placeholder="例：目标与现状差距集中在数据治理与安全合规" /></label>
        <label class="cs-field"><span>正文来源（自动带出，可修改）</span><textarea data-pr-text rows="7" maxlength="12000">${esc(sectionBodyFor(defaultSection))}</textarea></label>
        <p class="cs-muted" data-pr-hint>抽取不足会报错，不会硬画；标签会限制在 18 字内。</p>
        <div class="cs-plan-actions"><button type="button" class="cs-cp-ghost" data-pr-cancel>取消</button>
          <button type="button" class="cs-cp-primary" data-pr-run>生成图</button></div>
        <div data-pr-result></div>
      </div>`;
    const templateEl = dlg.querySelector('[data-pr-template]');
    const sectionEl = dlg.querySelector('[data-pr-section]');
    const titleEl = dlg.querySelector('[data-pr-title]');
    const scoreEl = dlg.querySelector('[data-pr-score]');
    const claimEl = dlg.querySelector('[data-pr-claim]');
    const textEl = dlg.querySelector('[data-pr-text]');
    const resultEl = dlg.querySelector('[data-pr-result]');
    const runBtn = dlg.querySelector('[data-pr-run]');
    let titleAuto = titleEl.value;
    const showError = (message) => {
      resultEl.innerHTML = `<p class="cs-warning">生成失败：${esc(String(message || '未知错误'))}</p>
        <div class="cs-plan-actions"><button type="button" class="cs-cp-ghost" data-pr-retry>重试</button></div>`;
      resultEl.querySelector('[data-pr-retry]')?.addEventListener('click', () => start());
    };
    const showDone = (data) => {
      const checks = data.checks || {};
      resultEl.innerHTML = `<div class="cs-design-preview"><img src="${esc(data.image_url)}?t=${Date.now()}" alt="${esc(data.title || titleEl.value)}" /></div>
        <p class="cs-muted">模板 ${esc(checks.template || templateEl.value)} · ${esc(data.width || '')}×${esc(data.height || '')}</p>
        <div class="cs-plan-actions"><button type="button" class="cs-cp-ghost" data-pr-again>重新抽取</button>
          ${data.preview_url ? `<a class="cs-cp-ghost" href="${esc(data.preview_url)}" target="_blank" rel="noreferrer">查看源</a>` : ''}
          ${data.asset_id ? `<a class="cs-cp-ghost" href="/api/studio/diagrams/${esc(data.asset_id)}/svg" download>下载 SVG</a>` : ''}
          <button type="button" class="cs-cp-primary" data-pr-insert>插入正文</button></div>`;
      resultEl.querySelector('[data-pr-again]')?.addEventListener('click', () => start());
      resultEl.querySelector('[data-pr-insert]')?.addEventListener('click', () => {
        const title = String(data.title || titleEl.value || '图表').trim();
        insertAtCursor(`:::figure ${title}\n![${title.replaceAll(']', '')}](${data.image_url})\n:::`);
        dlg.close();
        status('已插入生产级图表');
      });
    };
    const poll = async (jobId) => {
      for (let i = 0; i < 120; i += 1) {
        if (!dlg.isConnected) return;
        await new Promise((resolve) => globalThis.setTimeout(resolve, 1500));
        let data;
        try {
          const resp = await fetch(`/api/blocks/${active.id}/studio/diagram-assets/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
          data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        } catch (error) { showError(error.message); return; }
        if (data.status === 'failed') { showError(data.error || '图表生成失败'); return; }
        if (data.status === 'done') { showDone(data); return; }
        resultEl.innerHTML = `<p class="cs-muted">${esc(data.stage || '生成中')}… <span class="cs-dim">（抽取 + 渲染通常 20–60 秒）</span></p>`;
      }
      showError('生成超时，请重试');
    };
    async function start() {
      const text = String(textEl.value || '').trim();
      if (text.length < 20) { resultEl.innerHTML = '<p class="cs-warning">正文太短，无法抽取图内容；请补充本节正文。</p>'; return; }
      runBtn.disabled = true; runBtn.textContent = '生成中…';
      resultEl.innerHTML = '<p class="cs-muted">正在抽取图事实并渲染（20–60 秒）…</p>';
      try {
        const resp = await fetch(`/api/blocks/${active.id}/studio/production-diagrams`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            template: templateEl.value,
            title: String(titleEl.value || '图表').trim(),
            section: String(sectionEl.value || '').trim(),
            claim: String(claimEl.value || '').trim(),
            score_refs: String(scoreEl.value || '').split(/[、,，;；\n]/).map((x) => x.trim()).filter(Boolean),
            source_refs: [],
            text,
          }),
          signal: AbortSignal.timeout(30000),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
        await poll(data.job_id);
      } catch (error) { showError(error.message); }
      finally { if (dlg.isConnected) { runBtn.disabled = false; runBtn.textContent = '生成图'; } }
    }
    const syncTemplate = () => {
      const next = `${figureNo} ${labelOf(templateEl.value)}`;
      if (!titleEl.value || titleEl.value === titleAuto) { titleEl.value = next; titleAuto = next; }
    };
    templateEl.addEventListener('change', syncTemplate);
    sectionEl.addEventListener('change', () => {
      textEl.value = sectionBodyFor(sectionEl.value);
    });
    dlg.addEventListener('click', (event) => {
      if (event.target.closest('[data-pr-close]') || event.target.closest('[data-pr-cancel]')) dlg.close();
      if (event.target.closest('[data-pr-run]')) start();
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 目录级编写计划（C1：降级为"汇总 + 批量"）——只看工作量与状态；编辑与确认都在章计划里。 */
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
  const stateOf = block => {
    if (!units.has(block.id)) return '目录';
    if (!block.content?.trim()) return '未开始';
    const review = reviewStates[block.id];
    if (!review) return '草稿';
    if (review.freshness === 'unknown') return '待确认';
    if (review.freshness === 'stale') return '待复核';
    return review.satisfied === 'satisfied' ? '已通过' : '待处理';
  };
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
  const expandedChapters = new Set(blocks.map(block=>block.id));
  const chapterParents = new Map();
  const parentStack = [];
  for (const block of blocks) {
    while (parentStack.length && Number(parentStack.at(-1).level) >= Number(block.level)) parentStack.pop();
    chapterParents.set(block.id, parentStack.map(parent => parent.id));
    parentStack.push(block);
  }
  const branches = new Set([...chapterParents.values()].map(parents => parents.at(-1)));
  const paintTree = () => {
    const keyword = get('search').value.trim().toLowerCase();
    const visible = blocks.filter(block => keyword
      ? [block.title, ...(searchPoints[block.block_id] || [])].some(label => label.toLowerCase().includes(keyword))
      : chapterParents.get(block.id).every(parent => expandedChapters.has(parent)));
    get('tree').innerHTML = visible.map(b => {
      const st = stateOf(b), c = budgetOf(b);
      const target = c && Number(c.pages) ? Number(c.pages) : 0;
      const pg = target ? Math.min(100, Math.round(pagesOf(b) / target * 100)) : null;
      // title 带状态与预算，悬浮可读；行内不再显示文字，保证名称完整
      // 口径与章头/章节类型行一致：父章节按**子树合计**，并给出"差多少"
      const bs = budgetStats(b);
      const tips = [b.title, st,
        bs.target ? `预算 ${bs.target} 页 · 已写 ${bs.pages.toFixed(1)} 页 · ${bs.gapText}${bs.isParent ? '（子树合计）' : ''}` : '',
      ].filter(Boolean).join(' · ');
      const depth = Math.min(5, Math.max(0, (Number(b.level) || 1) - 1));
      const expanded = expandedChapters.has(b.id);
      return `<div class="cs-tree-row" style="--depth:${depth}">
        ${!keyword && branches.has(b.id) ? `<button type="button" class="cs-tree-toggle" data-tree-toggle="${b.id}" aria-expanded="${expanded}" aria-label="${expanded ? '折叠' : '展开'}${esc(b.title)}">${expanded ? '▾' : '▸'}</button>` : '<span class="cs-tree-spacer"></span>'}
        <button class="cs-node ${active?.id===b.id?'on':''}" data-chapter="${b.id}" data-st="${esc(st)}"
          ${active?.id===b.id ? 'aria-current="true"' : ''}
          ${pg === null ? '' : `data-pg="1" style="--pg:${pg}%"`}
          title="${esc(tips)}"><span>${esc(b.title)}</span><small>${esc(st)}</small></button>
      </div>`;
    }).join('') || '<p class="cs-tree-empty">没有匹配的章节或评分点</p>';
  };
  let autoSaveTimer = null, saveState = 'idle', savedAt = 0;
  const fmtClock = (ts) => { try { return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } };

  function readinessChipHtml() {
    const r = writingReadiness;
    if (!r) return '';
    if (r.status === 'no_outline' || r.has_outline === false) return '<button type="button" class="cs-readiness is-bad cs-readiness-action" data-readiness-plan>未生成大纲 · 先生成大纲</button>';
    const total = (r.sections || []).length;
    const ready = (r.sections || []).filter((x) => x.status === 'ready').length;
    return `<span class="cs-readiness ${ready === total ? 'is-ok' : 'is-warn'}">可写 ${ready}/${total}</span>`;
  }
  function paintReadinessChip() {
    const el = get('chapter-head')?.querySelector('[data-readiness]');
    if (el) el.innerHTML = readinessChipHtml();
  }

  /** 章头状态胶囊：未保存 / 保存中 / 已保存·时间 / 保存失败 / 已确认完成 */
  function paintSaveState() {
    const el = get('chapter-head')?.querySelector('[data-save-state]');
    if (!el) return;
    const completed = reviewStates[active?.id]?.freshness === 'current' && reviewStates[active?.id]?.satisfied === 'satisfied';
    let html = '';
    if (saveState === 'saving') html = '<span class="cs-save-dot is-saving"></span>保存中…';
    else if (saveState === 'error') html = '<span class="cs-save-dot is-error"></span>保存失败 <button type="button" class="cs-save-act" data-action="save">重试</button>';
    else if (completed) html = '<span class="cs-save-dot is-done"></span>当前版本已通过';
    else if (dirty) html = '<span class="cs-save-dot is-dirty"></span>未保存 · 刚刚改动';
    else if (savedAt) html = `<span class="cs-save-dot is-saved"></span>已保存 · ${fmtClock(savedAt)}`;
    el.innerHTML = html;
  }

  /** 计划自动保存：停止输入 2 秒后落库 */
  function scheduleAutoSave() {
    if (autoSaveTimer) globalThis.clearTimeout(autoSaveTimer);
    autoSaveTimer = globalThis.setTimeout(async () => {
      autoSaveTimer = null;
      if (!dirty || !active) return;
      if (writingBusy || (live.active && live.pending)) return;          // AI 未结束不自动保存
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
      const savedBlock = await json(`/api/blocks/${block.id}`, send('PUT',{content:value,expected_content:block.content || ''}));
      block.content = savedBlock.content ?? value;
      if (reviewStates[block.id]) reviewStates[block.id].freshness = 'stale';
      if (value.trim()) units.add(block.id);
      if (profile[block.id]?.writing_status==='complete') profile[block.id].writing_status='review';
      if (active.id===block.id && editor.value===value) {
        if(block.content!==value){live.reset();editor.value=block.content;paintView(previewVisible);}
        dirty=false; sessionStorage.removeItem(draftKey(block));
      }
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
    paintAIAction();
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
    const body = String(content || '');
    const target = normalizeSectionTitle(title);
    // 找所有标题行（## / **加粗**），按"归一化标题"匹配，替换到下一个标题之前
    const headingRe = /^(##[ \t]+.+|\*\*.{1,60}?\*\*)[ \t]*$/gm;
    const marks = [];
    let match;
    while ((match = headingRe.exec(body))) {
      marks.push({ line: match[0].trim(), start: match.index, end: match.index + match[0].length });
    }
    for (let i = 0; i < marks.length; i += 1) {
      const name = normalizeSectionTitle(marks[i].line);
      const hit = name === target || (target.length >= 4 && (name.includes(target) || target.includes(name)));
      if (!hit) continue;
      const stop = i + 1 < marks.length ? marks[i + 1].start : body.length;
      const heading = marks[i].line.startsWith('#') ? `## ${marks[i].line.replace(/^#+[ \t]+/, '')}` : marks[i].line;
      return `${body.slice(0, marks[i].start)}${heading}\n\n${text}\n\n${body.slice(stop)}`.replace(/\s+$/, '\n');
    }
    return `${body.replace(/\s*$/, '')}\n\n## ${title}\n\n${text}`;
  }

  /** 标题归一化：去加粗/编号前缀/尾部标点（与后端 normalize_title 同口径） */
  function normalizeSectionTitle(raw) {
    return String(raw || '')
      .trim()
      .replace(/^\*+|\*+$/g, '')
      .replace(/^#+[ \t]+/, '')
      .replace(/^\d+(?:\.\d+)*[.、]?\s*/, '')
      .replace(/[\s:：。，,、.]+$/, '')
      .trim();
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
    const [models, settingsResp, qualityResp] = await Promise.all([
      loadModelOptions(), loadWritingModels(),
      fetch(`/api/projects/${projectId}/diagram-settings`, { cache: 'no-store' }).then((r) => r.ok ? r.json() : null).catch(() => null),
    ]);
    const info = settingsResp || {};
    const blockSettings = info.settings || {};
    const effective = info.effective || {};
    const qualityInfo = qualityResp || { quality: 'fast', options: [] };
    const qualityOptions = qualityInfo.options?.length ? qualityInfo.options : [
      { key: 'fast', label: '快速结构图（默认）' },
      { key: 'design_architecture', label: '架构/分层图用设计版' },
      { key: 'design_all', label: '全部图用设计版（较慢）' },
    ];
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
        <label class="cs-ws-row">
          <span class="cs-ws-name"><b>图表质量</b><small>章节批量编写时使用</small></span>
          <select data-ws-quality>
            ${qualityOptions.map((item) => `<option value="${esc(item.key)}" ${qualityInfo.quality === item.key ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}
          </select>
        </label>
        <p class="cs-dim">改完点「设为项目默认」；只影响本章请点头像旁的「换模型」。设计版单图约 15–60 秒。</p>
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
        const quality = dlg.querySelector('[data-ws-quality]')?.value || 'fast';
        await fetch(`/api/projects/${projectId}/diagram-settings`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quality }), signal: AbortSignal.timeout(20000),
        });
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
          <button type="button" data-fr-filter="ai" aria-pressed="false">结构图</button>
          <button type="button" data-fr-filter="design" aria-pressed="false">设计图</button>
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
            : (f.kind === 'material' || f.kind === 'design') && f.url
              ? `<img src="${esc(f.url)}" alt="${esc(f.title)}" loading="lazy" />`
              : '<div class="cs-fig-missing">待补</div>'}</div>
          <p class="cs-fig-name">${esc(f.title)}</p>
          <p class="cs-fig-sub">${esc(f.section || '')}${f.kind === 'ai' ? ` · ${{layered:'层次图',flow:'流程图',timeline:'时间线'}[f.type] || '图'} · ${f.nodes || 0} 节点` : f.kind === 'material' ? ' · 素材图' : f.kind === 'design' ? ' · 设计图' : ' · 待补'}</p>
          <div class="cs-fig-card-acts">${f.kind === 'ai'
            ? `<button type="button" class="cs-todo-act" data-fr-adjust="${f.id}">调整</button>`
            : (f.kind === 'material' || f.kind === 'design')
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
  async function runChapterWriteJob(slot, options = {}) {
    if (!active) return {started: false};
    const block = options.block || active;
    const token = selecting;
    if (active?.id !== block.id) return {started:true};
    if (!options.jobId && !await save()) return {started:true};
    if (active?.id !== block.id || token !== selecting) return {started:true};
    const baseline = options.baseContent ?? block.content ?? '';
    if(options.selection){
      options.selection=rebaseSelection(options.selection,block.content || '');
      if(!options.selection)throw new Error('保存后的正文格式有变，选区无法唯一定位，请重新选择');
    }
    let jobId = options.jobId;
    if(!jobId){
      writingBusy=true;
      host.querySelector('[data-action="ai"]').disabled=true;
      host.querySelector('[data-action="submit-review"]').disabled=true;
      try {
        const data = await json(`/api/projects/${projectId}/studio/chapter-writes`, send('POST', {
          thread_id:threadId, block_id:block.block_id, expected_content:block.content || '',
          force_rewrite:!!options.forceRewrite, clean_rewrite:!!options.cleanRewrite, generic:!!options.generic,
          section_titles:options.sections || [], instruction:options.instruction || '', selection:options.selection || null,
        }));
        jobId=data.job_id;
      } catch(error) {
        if(active?.id===block.id && token===selecting){writingBusy=false;host.querySelector('[data-action="ai"]').disabled=false;host.querySelector('[data-action="submit-review"]').disabled=false;}
        throw error;
      }
      if(active?.id!==block.id || token!==selecting)return {started:true,jobId,background:true};
    }
    currentJobId = jobId;
    writingBusy = true;
    host.querySelector('[data-action="ai"]').disabled = true;
    host.querySelector('[data-action="submit-review"]').disabled = true;
    paintAIAction();
    const target = block.id;
    const isCurrent = () => !disposed && active?.id === target && currentJobId === jobId && token === selecting;
    const states = {chief: 'running', text: 'queued', diagram: 'queued', table: 'queued'};
    const models = {};
    return new Promise(resolve => {
      const source = new EventSource(`/api/projects/${projectId}/studio/chapter-writes/${jobId}/stream`);
      let finished = false;
      const finish = result => {
        if (finished) return;
        finished = true; source.close(); jobStreams.delete(source);
        if(isCurrent()){writingBusy=false;host.querySelector('[data-action="ai"]').disabled=false;host.querySelector('[data-action="submit-review"]').disabled=false;paintAIAction();}
        resolve(result);
      };
      jobStreams.set(source,()=>finish({started:true,jobId,background:true}));
      source.addEventListener('snapshot', event => {
        if (!isCurrent()) return;
        const job = JSON.parse(event.data);
        live.update(job); paintView(previewVisible);
        if (previewVisible) get('preview').innerHTML = previewContent(editor.value);
        const counts = job.blocks.reduce((result, item) => {
          result[item.kind] = (result[item.kind] || 0) + (item.state === 'done' ? 1 : 0);
          return result;
        }, {});
        status(`编制中 · 已完成正文块 ${counts.text || 0} · 表 ${counts.table || 0} · 图 ${counts.diagram || 0}`);
      });
      source.addEventListener('plan', event => {
        if(!isCurrent())return;
        const data=JSON.parse(event.data);Object.assign(models,data.models || {});models.chief='依据当前编写计划';states.chief='done';
        if(slot)slot.innerHTML=agentBarHtml(states,models)+agentActionsHtml(true);
      });
      source.addEventListener('role', event => {
        if (!isCurrent()) return;
        const data = JSON.parse(event.data);
        states[data.role] = ({working:'running',failed:'error',skipped:'skip'})[data.state] || data.state; models[data.role] = data.model;
        if (slot) slot.innerHTML = agentBarHtml(states, models) + agentActionsHtml(true);
      });
      source.addEventListener('job', event => {
        if (!isCurrent()) return;
        const data = JSON.parse(event.data);
        if (data.status === 'cancelled') {
          status('已停止，已生成内容保留');
          void json(`/api/projects/${projectId}/studio/chapter-writes/${jobId}`).then(job=>{
            if(!isCurrent() || !job.snapshot?.editing)return;
            pendingProposals.set(block.id,job.content || editor.value);live.reset();editor.value=block.content || '';dirty=false;paintView(false);
            if(autoSaveTimer){clearTimeout(autoSaveTimer);autoSaveTimer=null;}
            get('recovery').hidden=false;get('recovery').innerHTML='<span>修改已停止，原文已保留。</span><button type="button" data-compare-proposal>查看未完成结果</button>';
          });
          finish({started:true,jobId});
        } else status(data.stage || '编制中');
      });
      source.addEventListener('done', async event => {
        if (finished) return;
        const data = JSON.parse(event.data);
        if (isCurrent()) {
          if(Array.isArray(data.sections))lastSections=data.sections;
          if(data.saved){const history={before:baseline,after:data.content};aiBaselines.set(block.id,history);sessionStorage.setItem(`ai-change:${threadId}:${block.id}`,JSON.stringify(history));}
          if (data.saved) block.content = data.content;
          if (live.active && live.isActiveFor(jobId)) {
            if (data.saved && !live.hasEdits && !live.pending) {
              live.reset(); editor.hidden = false; editor.value = data.content;
              dirty = false; sessionStorage.removeItem(draftKey(block));
            } else changed();
          } else if (!dirty) {
            editor.value = data.content || block.content || '';
            if (!data.saved && editor.value !== block.content) changed();
          }
          const own = live.hasEdits;
          status(own ? '编制已结束，人工修改已保留，请保存正文' : data.reason || '编制已完成');
          if (!data.saved) {
            pendingProposals.set(block.id, live.hasEdits ? editor.value : data.content);
            if(options.selection || options.forceRewrite){live.reset();editor.value=block.content || '';dirty=false;sessionStorage.removeItem(draftKey(block));if(autoSaveTimer){clearTimeout(autoSaveTimer);autoSaveTimer=null;}}
            get('recovery').hidden = false;
            get('recovery').innerHTML = '<span>生成结果已保留，请核对当前版本。</span><button type="button" data-compare-proposal>查看变更</button>';
          }
          await loadBasis(block.block_id); if (isCurrent()) {paintAside(); paintView(previewVisible);}
          if (slot) slot.innerHTML = agentBarHtml(states,models);
        }
        finish({started:true,jobId,saved:!!data.saved});
      });
      source.addEventListener('error', event => {
        if (finished) return;
        let data; try {data = JSON.parse(event.data);} catch {}
        if (data?.scope === 'section' || data?.scope === 'chart') return;
        if (isCurrent()) {
          const partial = editor.value;
          void json(`/api/projects/${projectId}/studio/chapter-writes/${jobId}`).then(job=>{
            if(!isCurrent())return;
            const snapshot=job.snapshot || {};
            if(snapshot.editing){
              pendingProposals.set(block.id,job.content || partial);
              live.reset();editor.value=block.content || '';dirty=false;paintView(false);
              sessionStorage.removeItem(draftKey(block));
              if(autoSaveTimer){clearTimeout(autoSaveTimer);autoSaveTimer=null;}
              get('recovery').hidden=false;
              get('recovery').innerHTML='<span>修改未完成，原文已保留。</span><button type="button" data-compare-proposal>查看未完成结果</button>';
            }
            status(data?.message || '连接中断，结果已保留，可重试或核对变更');
          }).catch(()=>status('连接中断，原文与任务进度已保留'));
        }
        finish({started:true,jobId,error:data?.message || '连接中断'});
      });
    });
  }

  /** 编写整章时的 3 个头像（主编 / 图片 / 表格）*/
  function agentBarHtml(states, models) {
    const roles = ['chief', 'text', 'diagram', 'table'];
    return `<div class="cs-agent-bar">${roles.map((role) => {
      const meta = AGENTS[role];
      const state = states?.[role] || 'idle';
      const label = { idle: '等待', queued: '等待', running: '工作中', working: '工作中', done: '完成', skipped: '跳过', skip: '跳过', failed: '失败', error: '失败' }[state] || '等待';
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
  async function streamWriteSection(blockIdNum, sec, onProgress, extraPrompt = '', role = 'text', generic = false) {
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
      generic: generic ? 'true' : 'false',
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
          lastSectionWrite = data || null;
          resolve(data.content || mergeSectionText(base, sec.title, acc));
        });
        source.addEventListener('error', (event) => {
          source.close();
          let data = {};
          try { data = JSON.parse(event.data || '{}'); } catch (e) { /* ignore */ }
          reject(new Error(data.message || '推流中断，请重试'));
        });
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
  const jobStreams = new Map();
  const pendingProposals = new Map();
  const aiBaselines = new Map();
  let currentJobId = '';           // 当前章节级作业（停止/重试用）
  let lastSectionWrite = null;     // 最近一次节级写作：used_evidence / coverage

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
    if (type === 'review_rewrite') {
      const index = Number(((task.action || {}).payload || {}).issue_index || 0);
      const issue = (chapterReview?.issues || [])[index];
      if (!issue) { status('这条评审意见已失效，请重新评审'); return; }
      await openReviewPatchDialog(issue, index);
      return;
    }
    if (type === 'review_need') {
      await openMaterialAdminDialog();
      return;
    }
    if (type === 'review_search') {
      const query = String(((task.action || {}).payload || {}).query || '');
      try { await navigator.clipboard.writeText(query); status(`已复制检索词「${query}」`); } catch (error) { status(`检索词：${query}`); }
      const citeBtn = globalThis.document.querySelector('[data-cite-open]');
      if (citeBtn) citeBtn.click();
      return;
    }
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
      showPanel('issues');
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
          '把本节中需要业绩/材料举证的地方改为概括表述（例如"拟依据本项目规范组织实施"），不要留任何占位符，也不要编造具体项目名称与数字；保留其余内容。',
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
          status('表格生成：请在「添加内容 → AI 生成表格」里补一张'); showPanel('write');
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

  /** 改完自动复评审：应用补丁 / 重写完成后重跑一次，直接看分数变化（可关） */
  async function autoReviewAfterEdit(label) {
    if (!active) return;
    try {
      const settings = globalThis.localStorage.getItem('cs_auto_review');
      if (settings === 'off') { status(`${label}；已关闭自动复评审`); return; }
      status(`${label}；正在复评审（约 30~60 秒）…`);
      const resp = await fetch(`/api/blocks/${active.id}/studio/review`, { method: 'POST', signal: AbortSignal.timeout(300000) });
      if (!resp.ok) { status(`${label}；复评审失败，可手动点「重新评审」`); return; }
      const data = await resp.json().catch(() => ({}));
      await loadChapterReview(); await loadPrecheckAndVerdict(); await loadRubricAndPractices(); await loadBasis(active.block_id); paintAside();
      const delta = reviewDelta;
      const trend = delta === null || delta === undefined ? '' : (delta >= 0 ? `（↑${delta}）` : `（↓${Math.abs(delta)}）`);
      status(`${label}；复评审 ${data.score ?? '-'} 分${trend}`);
    } catch (error) {
      status(`${label}；复评审中断：${error.message}`);
    }
  }

  /** 评审 issue 定点重写：只改问题对应的那一段（before/after 对照，确认后落库） */
  async function openReviewPatchDialog(issue, issueIndex) {
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    const head = `<header><h3>定点重写</h3><button type="button" data-rp-close>×</button></header>`;
    const body = (inner) => `<div class="cs-plan-body">${inner}</div>`;
    const issueHtml = `<p class="cs-rp-issue"><span class="cs-review-sev is-${esc(String(issue.severity || 'medium').toLowerCase())}">${esc(issue.severity || 'medium')}</span>
      ${esc(String(issue.point || '').slice(0, 160))}</p>
      ${issue.suggestion ? `<p class="cs-rp-sug">建议：${esc(String(issue.suggestion).slice(0, 160))}</p>` : ''}`;
    dlg.innerHTML = `${head}${body(`${issueHtml}<p class="cs-muted">正在定位问题片段并生成改法…</p>`)}
      <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-rp-close2>取消</button></footer>`;
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();

    const load = async () => {
      const resp = await fetch(`/api/blocks/${active.id}/studio/review/patch`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issue_index: issueIndex }),
        signal: AbortSignal.timeout(240000),
      });
      return resp.json().catch(() => ({}));
    };

    const render = (data) => {
      if (!data?.found) {
        dlg.innerHTML = `${head}${body(`${issueHtml}
          <p class="cs-warning">${esc(data?.hint || '没能定位到这段原文，无法定点修改')}</p>`)}
          <footer class="cs-ins-foot">
            <button type="button" class="cs-cp-ghost" data-rp-close3>关闭</button>
            <button type="button" class="cs-cp-primary" data-rp-whole>整节重写</button>
          </footer>`;
        return;
      }
      dlg._patch = data;
      dlg.innerHTML = `${head}${body(`${issueHtml}
        <div class="cs-rp-cols">
          <div class="cs-rp-col is-before"><p class="cs-rp-label">原文</p><p class="cs-rp-text">${esc(data.before)}</p></div>
          <div class="cs-rp-col is-after"><p class="cs-rp-label">改成</p><p class="cs-rp-text">${esc(data.after)}</p></div>
        </div>
        ${data.section ? `<p class="cs-muted">所在小节：${esc(data.section)}</p>` : ''}`)}
        <footer class="cs-ins-foot">
          <button type="button" class="cs-cp-ghost" data-rp-close3>取消</button>
          <button type="button" class="cs-cp-ghost" data-rp-regenerate>换一种改法</button>
          <button type="button" class="cs-cp-primary" data-rp-apply>应用到正文</button>
        </footer>`;
    };

    try { render(await load()); } catch (error) {
      render({ found: false, hint: `生成失败：${error.message}` });
    }

    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-rp-close]') || event.target.closest('[data-rp-close2]') || event.target.closest('[data-rp-close3]')) { dlg.close(); return; }
      if (event.target.closest('[data-rp-whole]')) {
        dlg.close();
        await openRecomposeDialog({ prefillPrompt: `${issue.point || ''}${issue.suggestion ? `（建议：${issue.suggestion}）` : ''}` });
        return;
      }
      if (event.target.closest('[data-rp-regenerate]')) {
        dlg.innerHTML = `${head}${body(`${issueHtml}<p class="cs-muted">正在重新生成…</p>`)}<footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-rp-close3>取消</button></footer>`;
        try { render(await load()); } catch (error) { render({ found: false, hint: `生成失败：${error.message}` }); }
        return;
      }
      if (event.target.closest('[data-rp-apply]')) {
        const patch = dlg._patch;
        if (!patch) return;
        try {
          const resp = await fetch(`/api/blocks/${active.id}/studio/review/apply`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ before: patch.before, after: patch.after, issue_index: issueIndex }),
            signal: AbortSignal.timeout(60000),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.applied) throw new Error(data.hint || data.detail || `HTTP ${resp.status}`);
          editor.value = data.content || editor.value;
          context = await json(`/api/blocks/${active.id}/studio`);
          dlg.close();
          await loadBasis(active.block_id); paintAside();
          void autoReviewAfterEdit('已应用到正文（写了版本，可回退）');
        } catch (error) { status(`应用失败：${error.message}`); }
      }
    });
    dlg.addEventListener('close', () => dlg.remove());
  }

  /** 调整某一节的素材（时机②：重写前确认） */
  async function openSectionMaterialDialog(sectionTitle) {
    await loadPlanMaterials();
    const item = (planMaterials?.sections || []).find((s) => String(s.section) === String(sectionTitle));
    const candidates = item?.candidates || [];
    const dlg = globalThis.document.createElement('dialog');
    dlg.className = 'cs-plan-dialog';
    dlg.innerHTML = `<header><h3>「${esc(sectionTitle)}」的素材</h3><button type="button" data-smat-close>×</button></header>
      <div class="cs-plan-body">
        <p class="cs-muted">生成/重写这一节时，只注入这里确认的素材。</p>
        ${candidates.length ? candidates.map((c) => `<label class="cs-ol-mat-row">
            <input type="checkbox" data-smat="${c.chunk_id}" ${c.confirmed ? 'checked' : ''} />
            <span class="cs-ol-mat-body">
              <span class="cs-ol-mat-text">${esc(String(c.text || '').slice(0, 70))}…</span>
              <small>${(c.tags || []).includes('boilerplate') ? '⚠ 表单/范本类 · ' : ''}${c.source_kind === 'web' ? '联网' : '文件'}${c.page ? ` · 第 ${c.page} 页` : ''} · ${esc(c.reason || '')}</small>
            </span>
          </label>`).join('') : '<p class="cs-muted">素材库没有匹配到片段：本节会写概括表述</p>'}
      </div>
      <footer class="cs-ins-foot">
        <button type="button" class="cs-cp-ghost" data-smat-close2>取消</button>
        <button type="button" class="cs-cp-primary" data-smat-save>保存本节素材</button>
      </footer>`;
    dlg.addEventListener('click', async (event) => {
      if (event.target.closest('[data-smat-close]') || event.target.closest('[data-smat-close2]')) { dlg.close(); return; }
      if (!event.target.closest('[data-smat-save]')) return;
      const ids = [...dlg.querySelectorAll('[data-smat]:checked')].map((el) => Number(el.dataset.smat));
      try {
        await json(`/api/blocks/${active.id}/plan/material-suggestions/apply`, {
          method: 'POST', body: { section: sectionTitle, chunk_ids: ids, mode: 'auto' },
        });
        await loadPlanMaterials(); await loadMaterialLibrary();
        dlg.close();
        status(ids.length ? `「${sectionTitle}」已确认 ${ids.length} 条素材` : `已清空「${sectionTitle}」的素材`);
      } catch (error) { status(`保存失败：${error.message}`); }
    });
    dlg.addEventListener('close', () => dlg.remove());
    globalThis.document.body.appendChild(dlg);
    dlg.showModal();
  }

  /** 「重新编写本模块…」：选范围 + 填提示词 + 强度 → 直接重写并保存（优化的唯一入口） */
  async function openRecomposeDialog(options = {}) {
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
          <textarea data-rc-prompt rows="4" placeholder="例如：写得更具体，把每个目标加上指标与验收口径；语言更简洁；补充安全设计细节…">${esc(options.prefillPrompt || '')}</textarea></label>
        <div class="cs-field cs-rc-mat">
          <span>本节素材</span>
          <span class="cs-rc-mat-row">
            <b data-rc-mat-count>加载中…</b>
            <button type="button" class="cs-gate-act" data-rc-mat-adjust>调整素材…</button>
          </span>
          <small class="cs-dim">生成/重写这一节时只注入这里确认的素材</small>
        </div>
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
    // 填充"本节素材"数量 + 调整按钮
    const refreshMatCount = async () => {
      await loadPlanMaterials();
      const index = Number(dlg.querySelector('[data-rc-scope]')?.value || 0);
      const title = (planBrief?.outline || [])[index]?.title || '';
      const item = (planMaterials?.sections || []).find((s) => String(s.section) === String(title));
      const countEl = dlg.querySelector('[data-rc-mat-count]');
      if (countEl) {
        countEl.textContent = !item ? '（未知）'
          : item.confirmed_count ? `${item.confirmed_count} 条已确认`
          : (item.candidates || []).length ? `未确认（建议 ${item.candidates.length} 条）` : '无匹配素材（将写概括表述）';
      }
    };
    void refreshMatCount();
    dlg.addEventListener('change', (event) => {
      if (event.target.closest('[data-rc-scope]')) void refreshMatCount();
    });
    dlg.addEventListener('click', async (event) => {
      const adjust = event.target.closest('[data-rc-mat-adjust]');
      if (adjust) {
        const index = Number(dlg.querySelector('[data-rc-scope]')?.value || 0);
        const title = (planBrief?.outline || [])[index]?.title || '';
        if (title) await openSectionMaterialDialog(title);
        await refreshMatCount();
        return;
      }
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
        void autoReviewAfterEdit(`已重写「${section.title}」并保存`);
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
  /** 撤销最近一次 AI 写入（恢复上一版本） */
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
    const verdictHtml = deliveryVerdictHtml();
    if (!need.length) {
      return `<div class="cs-delivery-block"><h4>交付门槛</h4>${verdictHtml}<p class="cs-todo-ok">本章没有需要你处理的事</p></div>`;
    }
    return `<div class="cs-delivery-block"><h4>交付门槛 <small>需要你 ${need.length} 件</small></h4>${verdictHtml}
      ${next ? `<div class="cs-task-next">${cardHtml(next, { ignore: false })}</div>` : ''}
      ${need.length ? `<div class="cs-todo-group"><p class="cs-todo-glabel">需要你处理 ${need.length}</p>
        ${need.filter((x) => x.id !== (next && next.id)).map((x) => cardHtml(x, { ignore: true })).join('')}</div>` : ''}
      ${system.length ? `<details class="cs-todo-auto"><summary>系统处理中 ${system.length}（不用你操作）</summary>
        <div class="cs-todo-auto-body">${system.map((x) => cardHtml(x, {})).join('')}</div></details>` : ''}
      ${suggest.length ? `<details class="cs-todo-auto"><summary>建议优化 ${suggest.length}（不影响交付）</summary>
        <div class="cs-todo-auto-body">${suggest.map((x) => cardHtml(x, { ignore: true })).join('')}</div></details>` : ''}
      ${(!need.length && !system.length && !suggest.length) ? '<p class="cs-todo-ok">✓ 距离交付没有待办项</p>' : ''}</div>`;
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
    // 交付/补料任务不再放进「待处理」；这里只保留评审入口，不显示任务数。
    const el = host.querySelector('[data-todo-badge]');
    if (!el) return;
    el.textContent = ''; el.hidden = true;
  }

  /** 右栏「待办」页签：按处理模块分组，每条 = 模块 + 带数字的问题 + 一个动作 */
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
        const imgUrl = img ? img[2] : '';
        figures.push({ id: `img-${figures.length + 1}`,
                       kind: /\/api\/studio\/diagrams\//.test(imgUrl) ? 'design' : 'material', section,
                       title: (line.slice(9).trim() || (img && img[1]) || '图片'),
                       url: imgUrl, start: i, end: j });
        i = j;
      }
    }
    return figures;
  }

  /** 按原文 4-Agent 流水线编写本章：主编 → 文字/表格/图表 → 双评审。 */
  async function runTeamChapterJob(block) {
    if (!block || writingBusy) return;
    writingBusy = true;
    showPanel('write');
    const slot = get('agents');
    if (slot) slot.innerHTML = '<div class="cs-team-run">已启动章节主编、文字、表格、图表与评审 Agent…</div>';
    status('已启动 4-Agent 章节编制…');
    try {
      const started = await json(`/api/projects/${projectId}/studio/writing-jobs`,
        send('POST', {thread_id: threadId, block_ids: [block.id]}));
      const jobId = (started.jobs || [])[0];
      if (!jobId) throw new Error('未能创建编制任务');
      let lastStage = '';
      for (;;) {
        await new Promise((resolve) => globalThis.setTimeout(resolve, 1200));
        const job = await json(`/api/projects/${projectId}/studio/writing-jobs/${jobId}`);
        if (job.stage && job.stage !== lastStage) { lastStage = job.stage; status(job.stage); }
        const rows = (job.blocks || []).map((item) => `<div class="cs-team-row"><span>${esc(item.title || '')}</span><em>${esc(item.state || '')}</em></div>`).join('');
        if (slot) slot.innerHTML = `<div class="cs-team-run"><b>${esc(job.stage || '编制中')}</b>${rows}</div>`;
        const liveText = (job.blocks || []).map((item) => item.content || '').filter(Boolean).join('\n\n');
        if (liveText) {
          get('editor').hidden = true;
          get('preview').hidden = false;
          get('preview').innerHTML = previewContent(liveText);
        }
        if (job.status === 'done') {
          if (job.saved) { await select(block.id); status('4-Agent 编制完成，正文已保存'); }
          else status('4-Agent 编制完成；正文已有内容，结果作为建议保留，请核对');
          break;
        }
        if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error || '编制任务已停止');
      }
    } catch (error) {
      status(`章节编制失败：${error.message}`);
    } finally {
      writingBusy = false;
    }
  }

  /** 本节提示词：改完可保存，并可直接按提示词重新生成本节 */
  async function openSectionPromptDialog(index) {
    const section = (planBrief?.outline || [])[index];
    if (!section || !active) return;
    const practice = practiceOf(section);
    const dialog = globalThis.document.createElement('dialog'); dialog.className = 'cs-plan-dialog cs-sec-prompt-dialog';
    dialog.innerHTML = `<header><h3>本节提示词 · ${esc(section.title)}</h3></header><div class="cs-plan-body">
      <label>生成提示词<textarea data-sec-prompt-text rows="7" placeholder="填写本节要写什么、按什么结构写、要避免什么">${esc(practice)}</textarea></label>
      <p class="cs-dim">保存后，本节生成/重写都会使用这段提示词；重新生成只替换本节正文。</p></div>
      <footer class="cs-sec-prompt-foot">
        <button type="button" class="cs-cp-ghost" data-cancel>取消</button>
        <button type="button" class="cs-cp-ghost" data-save>仅保存</button>
        <button type="button" class="cs-cp-primary" data-save-gen>保存并重新生成本节</button>
      </footer>`;
    const save = async () => {
      const text = String(dialog.querySelector('[data-sec-prompt-text]')?.value || '').trim();
      await json(`/api/projects/${projectId}/chapters/${encodeURIComponent(active.block_id)}/plan/practice`,
        send('PUT', {section: section.title, practice: text}));
      if (!planBrief) planBrief = {};
      planBrief.practiceOverrides = {...(planBrief.practiceOverrides || {}), [section.title]: text};
      return text;
    };
    dialog.onclick = async event => {
      if (event.target.closest('[data-cancel]')) { dialog.close(); return; }
      if (event.target.closest('[data-save]')) {
        try { await save(); status(`已保存「${section.title}」的提示词`); dialog.close(); } catch (error) { status(error.message); }
        return;
      }
      const gen = event.target.closest('[data-save-gen]');
      if (!gen) return;
      gen.disabled = true;
      try {
        await save();
        dialog.close();
        await streamWriteSection(active.id, section, (chars) => status(`正在按提示词重写「${section.title}」… 已 ${chars} 字`), '', 'rewrite', false);
        status(`「${section.title}」已按新提示词重新生成`);
      } catch (error) { status(error.message); gen.disabled = false; }
    };
    dialog.addEventListener('close', () => dialog.remove());
    globalThis.document.body.append(dialog); dialog.showModal();
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
                 <span class="cs-fig-meta">${f.kind === 'ai' ? `${({layered:'层次图',flow:'流程图',timeline:'时间线'})[f.type] || '图'} · ${f.nodes} 节点` : f.kind === 'design' ? '设计图' : '素材图'}</span>
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
            <span class="cs-sec-acts"><button type="button" class="cs-todo-act" data-sec-prompt="${i}">提示词</button><button type="button" class="cs-todo-act" data-aside-write-section="${i}">${written ? '重写' : '编写'}</button></span>
          </div>
          ${figBlock}
        </div>`;
    }).join('');
    return `<section data-pane="write"><h4>章节编写进度 <small>${done.size}/${outline.length} 节已写</small></h4>
      <div class="cs-progress-actions">
        <button type="button" class="cs-progress-btn" data-fig-review>审阅本章图片 ${figureCount}</button>
      </div>
      ${rows}</section>`;
  }

  /** 右栏「素材」页签：三分法 —— 已有可用 / 公开资料可补 / 必须你上传 */
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
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>生产级图模板</b>
            <small>分层 / 数据流 / 时序 / 甘特 / 矩阵 / 鱼骨 / 金字塔 / 雷达 / 组织职责</small></div>
          <button type="button" class="cs-ins-chip" data-ins-prod="layered">生成</button></div>
        <div class="cs-ins-entry"><div class="cs-ins-entry-main"><b>快速结构图</b>
            <small>把文字抽成可编辑的层次 / 流程 / 时间线（纯文本，可手改）</small></div>
          <button type="button" class="cs-ins-chip" data-ins-generate="info">生成</button></div>
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
      const prod = el.closest('[data-ins-prod]');
      if (prod) {
        dlg.close();
        openProductionDiagramDialog(prod.dataset.insProd || 'layered');
        return;
      }
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
    profile[active.id]=context.profile;paintTree();stats();paintTodoBadge();paintReadinessChip();
    // 自愈：计划数据还没到（或拉取失败）→ 补拉一次后重绘
    if (!planBrief && !planRetryPending && active?.block_id) {
      planRetryPending = true;
      void loadBasis(active.block_id).then(() => { planRetryPending = false; paintAside(); });
    }
    get('aside').innerHTML = `<section data-pane="issues">${reviewBlockHtml()}</section>
      ${progressPaneHtml()}
      <section data-pane="materials">
        ${materialLibraryHtml()}
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
    const uploadInput = get('upload');
    if (uploadInput) uploadInput.onchange = async event => {
      const block=active;
      for (const file of event.target.files) {
        status(`正在上传 ${file.name}…`);
        try { const form=new FormData();form.append('file',file);await json(`/api/blocks/${block.id}/studio/materials`,{method:'POST',body:form}); }
        catch(error) { status(`${file.name}：${error.message}`); return; }
      }
      if(active.id===block.id) { context=await json(`/api/blocks/${block.id}/studio`);paintAside(); }
      status('素材已关联本章；文档后台解析后，核对摘录即可用于编写。');
    };
    const evidenceFile = host.querySelector('[data-evidence-file]');
    if (evidenceFile) evidenceFile.onchange = async event => {
      const block = active;
      for (const file of event.target.files || []) {
        status(`正在上传 ${file.name}…`);
        try {
          const form = new FormData(); form.append('file', file);
          await json(`/api/blocks/${block.id}/studio/materials`, {method: 'POST', body: form});
        } catch (error) { status(`${file.name}：${error.message}`); return; }
      }
      if (active.id === block.id) { await loadMaterialLibrary(); paintAside(); }
      if (pendingEvidenceNeed) {
        status('已上传；解析完成后在素材库选片段，采用到当前要求。');
        await openMaterialAdminDialog();
      } else {
        status('已上传；解析完成后从素材库选片段，采用到对应要求。');
      }
    };
  };
  /** 工具栏「编写计划」：只在当前章属于某个一级目录时出现（动作与树无关，树只看结构）。 */
  function paintPlanAction(block) {
    const slot = host.querySelector('[data-plan-slot]');
    if (!slot || !block) return;
    // 本章大纲/提示词入口常驻：不依赖目录是否确认，也不依赖当前章是否属于某个计划单元。
    slot.innerHTML = `<button type="button" class="cs-plan-btn" data-plan="${esc(block.block_id)}" `
      + `title="本章计划：章节类型、大纲、每节生成提示词">本章计划</button>`;
  }

  function paintAIAction() {
    const button = host.querySelector('[data-action="ai"]');
    const undo = host.querySelector('[data-action="undo-ai"]');
    if (undo) undo.disabled = writingBusy || !active || !aiBaselines.get(active.id)?.after;
    if (button && !writingBusy) {button.textContent = editor.value.trim() ? 'AI 修改' : 'AI 编制';button.title = editor.value.trim() ? '选择选区或当前章节正文修改，目录固定' : '按应答策划确认的当前章节编制正文与图表';}
  }

  function renderWorkspaceRole() {
    const scope = context.scope || document.node_scopes?.[active.id] || {};
    const structure = scope.role === 'structure' && !editor.value.trim();
    host.querySelector('.cs-tree-label').textContent=context.directory_locked ? '已确认应答目录' : '应答目录';
    get('overview').hidden = !structure;
    host.querySelector('.cs-toolbar').hidden = structure;
    editor.hidden = structure; get('preview').hidden = true;
    if (structure) {
      const children = blocks.filter(block => (scope.unit_ids || []).includes(block.id));
      const pending = children.filter(block => (scope.pending_ids || []).includes(block.id));
      get('overview').innerHTML = `<h3>下属章节 · ${children.length} 个编写单元</h3>
        <p><button type="button" class="cs-plan-btn" data-plan="${esc(block.block_id)}">本章计划</button>
        已写 ${children.length - pending.length} · 待写 ${pending.length}</p>
        <button type="button" data-next-chapter="${pending[0]?.id || children[0]?.id || ''}" ${children.length ? '' : 'disabled'}>进入下一待写章节</button>
        <button type="button" class="cs-primary" data-batch-write ${pending.length ? '' : 'disabled'}>编制未写子章</button>
        ${children.map(block => `<div class="cs-overview-row"><button type="button" data-chapter="${block.id}">${esc(block.title)}</button><span>${block.content?.trim() ? '已有正文' : '待写'}</span></div>`).join('')}`;
    }
    const old = get('chapter-head').querySelector('[data-task-summary]'); old?.remove();
    get('chapter-head').insertAdjacentHTML('beforeend', `<small data-task-summary>评分要求 ${(context.points || []).length} 项 · 规范条款 ${(context.requirements || []).length} 条${context.missing?.length ? ' · 须补证明' : ''}</small>`);
    paintAIAction();
  }

  async function openBatchDialog() {
    const root = active;
    const payload = {thread_id: threadId, root_block_id: root.block_id, preview: true};
    const result = await json(`/api/projects/${projectId}/studio/batch-write`, send('POST', payload));
    const dialog = globalThis.document.createElement('dialog'); dialog.className = 'cs-plan-dialog';
    dialog.innerHTML = `<header><h3>编制未写子章</h3></header><div class="cs-plan-body"><p>范围：${esc(root.title)} · ${result.targets.length} 章，其中 ${result.targets.filter(item => item.auto_outline).length} 章将按已确认章节及关联要求编制。</p>
      ${result.targets.map(item => `<p>${esc(item.title)} · ${item.auto_outline ? '按本章要求编制' : '按已有计划编制'}${item.evidence_required ? ' · 需核对证明' : ''}</p>`).join('')}<div data-batch-result></div></div>
      <footer><button type="button" data-cancel>取消</button><button type="button" data-start>开始编制</button></footer>`;
    dialog.onclick = async event => {
      if (event.target.closest('[data-cancel]')) dialog.close();
      const button = event.target.closest('[data-start]');
      if (!button) return;
      button.disabled = true;
      try {
        const started = await json(`/api/projects/${projectId}/studio/batch-write`, send('POST', {...payload, preview:false}));
        dialog.querySelector('[data-batch-result]').innerHTML = `<p>已启动 ${started.created.length} 章；未启动 ${started.skipped.length} 章。任务继续在后台执行。</p>${started.skipped.map(item => `<p>${esc(item.title)} · ${esc(item.reason)}</p>`).join('')}`;
      } catch (error) {dialog.querySelector('[data-batch-result]').textContent=error.message;button.disabled=false;}
    };
    dialog.addEventListener('close', () => dialog.remove()); globalThis.document.body.append(dialog); dialog.showModal();
  }

  async function openScopeDialog() {
    const block = active;
    const range = live.active ? live.selectionRange() : (!previewVisible ? selectedRange(editor.value,editor.selectionStart,editor.selectionEnd) : null);
    const selection = range?.selection || '';
    const original = editor.value;
    if (!writingReadiness?.has_outline) {
      status('请先制定本章大纲，再按大纲编写内容');
      await openChapterPlan(block.block_id);
      return;
    }
    let plan = {};
    try { plan = await fetchChapterPlan(block.block_id) || {}; } catch (error) { status(`读取本章计划失败，按正文标题生成范围：${error.message}`); }
    const sections = plan.outline || [];
    if (active?.id !== block.id) return;
    const dialog = globalThis.document.createElement('dialog'); dialog.className = 'cs-plan-dialog';
    dialog.innerHTML = `<header><h3>AI 修改 · ${esc(block.title)}</h3></header><div class="cs-plan-body">
      <label>修改范围<select data-scope>${selection ? '<option value="selection" selected>选中的文字</option>' : ''}${sections.map((section,index) => `<option value="${index}">小节 · ${esc(section.title)}</option>`).join('')}<option value="chapter" ${selection ? '' : 'selected'}>重写本章正文（目录固定）</option></select></label>
      ${selection ? `<p>选区原文 · ${selection.length} 字</p><blockquote>${esc(selection.slice(0,160))}${selection.length>160?'…':''}</blockquote>` : ''}<label>修改要求<textarea data-instruction placeholder="例如补充实施步骤、压缩篇幅，或突出评分要求"></textarea></label><p>结果在正文中展示；重写本章会清空旧正文，版本历史可回滚。</p></div><footer><button type="button" data-cancel>取消</button><button type="button" data-start>开始修改</button></footer>`;
    dialog.onclick = async event => {
      if(event.target.closest('[data-cancel]'))dialog.close();
      const button = event.target.closest('[data-start]'); if(!button)return;
      const scope = dialog.querySelector('[data-scope]').value;
      if(!scope)return;
      const instruction = dialog.querySelector('[data-instruction]').value.trim() || '根据本节要求完善应答，保留有依据的事实';
      if(scope === 'chapter' && !globalThis.confirm(`将清空并重写「${block.title}」的正文，目录标题、层级和顺序固定；旧正文不再拼接，版本历史仍保留可回滚。确认开始？`))return;
      if(active?.id !== block.id){dialog.close();return;}
      button.disabled=true;
      try {
        if(editor.value!==original){status('正文已变化，请重新选择修改范围');dialog.close();return;}
        if(!await save())return;
        dialog.close();
        await runChapterWriteJob(get('agents'), {block, forceRewrite:scope !== 'append',
          cleanRewrite: scope === 'chapter',
          sections: /^\d+$/.test(scope) ? [sections[Number(scope)].title] : [], instruction,
          selection:scope==='selection' ? range : null});
      } catch(error){status(error.message);} finally {button.disabled=false;}
    };
    dialog.addEventListener('close',()=>dialog.remove());globalThis.document.body.append(dialog);dialog.showModal();
  }

  async function compareProposal() {
    const block = active;
    const proposal = pendingProposals.get(block.id);
    const local = editor.value;
    const restoring = proposal === aiBaselines.get(block.id)?.before;
    if(proposal === undefined){status('没有待采用变更');return;}
    const latest = await json(`/api/projects/${projectId}/blocks?threadId=${encodeURIComponent(threadId)}`);
    const current = latest.find(item=>item.id===block.id);
    if(!current || active?.id!==block.id)return;
    const dialog = globalThis.document.createElement('dialog');dialog.className='cs-plan-dialog';
    dialog.innerHTML='<header><h3>核对正文变更</h3></header><div class="cs-plan-body cs-compare"><label>当前服务器正文<textarea readonly data-before></textarea></label><label>本次生成结果<textarea readonly data-after></textarea></label></div><footer><button type="button" data-cancel>保留当前正文</button><button type="button" data-apply>采用本次结果</button></footer>';
    dialog.querySelector('h3').textContent=restoring?'核对修改前的完整版本':'核对正文变更';
    const beforeLabel=dialog.querySelector('[data-before]').parentElement;
    beforeLabel.firstChild.textContent='当前编辑内容';
    const afterLabel=dialog.querySelector('[data-after]').parentElement;
    afterLabel.firstChild.textContent=restoring?'修改前完整正文（采用后替换全文）':'待采用结果（采用后替换全文）';
    dialog.querySelector('[data-apply]').textContent=restoring?'恢复这份旧版本':'采用本次结果';
    dialog.querySelector('[data-before]').value=local;dialog.querySelector('[data-after]').value=proposal;
    dialog.onclick=async event=>{
      if(event.target.closest('[data-cancel]'))dialog.close();
      const button=event.target.closest('[data-apply]');if(!button)return;
      if(active?.id!==block.id){dialog.close();return;}
      if(editor.value!==local){status('核对期间正文又有变化，请重新打开变更核对');dialog.close();return;}
      button.disabled=true;
      try{
        const updated=await json(`/api/blocks/${block.id}`,send('PUT',{content:proposal,expected_content:current.content||''}));
        const history={before:current.content||'',after:updated.content};aiBaselines.set(block.id,history);sessionStorage.setItem(`ai-change:${threadId}:${block.id}`,JSON.stringify(history));
        live.reset();block.content=updated.content;editor.value=updated.content;dirty=false;paintView(false);
        pendingProposals.delete(block.id);get('recovery').hidden=true;paintTree();paintAIAction();dialog.close();
      }catch(error){status(error.message);button.disabled=false;}
    };
    dialog.addEventListener('close',()=>dialog.remove());globalThis.document.body.append(dialog);dialog.showModal();
  }

  async function openProjectFacts() {
    const data=await json(`/api/projects/${projectId}/studio/facts`);
    const dialog=globalThis.document.createElement('dialog');dialog.className='cs-plan-dialog';
    dialog.innerHTML='<header><h3>项目统一资料</h3></header><div class="cs-plan-body"><p>每行填写一项「名称=内容」，例如项目名称、系统名称、项目经理和计划日期。所有章节共同使用，修改后已评审章节需复核。</p><textarea data-facts rows="12"></textarea><p data-facts-result></p></div><footer><button type="button" data-cancel>关闭</button><button type="button" data-save>保存资料</button></footer>';
    dialog.querySelector('[data-facts]').value=Object.entries(data.facts||{}).map(([key,value])=>key+'='+value).join('\n');
    dialog.onclick=async event=>{
      if(event.target.closest('[data-cancel]'))dialog.close();
      const button=event.target.closest('[data-save]');if(!button)return;
      const facts={};
      for(const line of dialog.querySelector('[data-facts]').value.split('\n').filter(line=>line.trim())){
        const index=line.indexOf('=');if(index<1){dialog.querySelector('[data-facts-result]').textContent='请按「名称=内容」填写每一行';return;}
        facts[line.slice(0,index).trim()]=line.slice(index+1).trim();
      }
      button.disabled=true;
      try{const result=await json(`/api/projects/${projectId}/studio/facts`,send('PUT',{facts}));dialog.querySelector('[data-facts-result]').innerHTML=`已保存，${result.affected.length} 个已有正文章节将使用新资料复核。<details><summary>查看受影响章节</summary>${result.affected.map(item=>`<span class="cs-fact-affected">${esc(item.title)}</span>`).join('')}</details>`;await loadChapterReview();paintAside();}
      catch(error){dialog.querySelector('[data-facts-result]').textContent=error.message;}finally{button.disabled=false;}
    };
    dialog.addEventListener('close',()=>dialog.remove());globalThis.document.body.append(dialog);dialog.showModal();
  }

  async function select(id) {
    jobStreams.forEach(detach=>detach());
    if(live.active&&live.pending){sessionStorage.setItem(draftKey(active),JSON.stringify({content:editor.value,base:active.content || ''}));live.reset();dirty=false;}
    if (!await save()) return;
    const block=blocks.find(b=>String(b.block_id)===String(id) || String(b.id)===String(id));
    if (!block) return;
    const token=++selecting;
    status('正在加载本章依据与素材…');
    try {
      const data=await json(`/api/blocks/${block.id}/studio`);
      if(disposed || token!==selecting) return;
      get('completion').close();pendingCompletion=null;
      live.reset();lastSections=[];get('agents').innerHTML='';currentJobId='';writingBusy=false;host.querySelector('[data-action="ai"]').disabled=false;host.querySelector('[data-action="submit-review"]').disabled=false;previewVisible=false;
      if(data.block)Object.assign(block,data.block);
      try{const history=JSON.parse(sessionStorage.getItem(`ai-change:${threadId}:${block.id}`));if(history)aiBaselines.set(block.id,history);}catch{}
      active=block;context=data;dirty=false;
      chapterParents.get(block.id).forEach(parent => expandedChapters.add(parent));
      profile[block.id]=data.profile;
      await loadBasis(block.block_id);   // 依据 ↔ 正文对应（评分点/条款是否已回应）
      if (disposed || token !== selecting) return;
      editor.value=block.content||''; editor.hidden=false;get('preview').hidden=true;paintView(false);
      const headState = stateOf(block);
      // 「目录」是结构节点标记，不是编写状态 —— 不在章头显示（用户反馈）
      get('chapter-head').innerHTML=`<h3>${esc(block.title)}</h3>${headState && headState !== '目录' && headState !== '未开始' ? `<span class="cs-chapter-state">${esc(headState)}</span>` : ''}<span class="cs-save-state" data-save-state></span><button type="button" data-open-panel="materials" aria-controls="cs-chapter-inspector" aria-expanded="false">素材</button>${budgetLineHtml(block)}`;
      showPanel();
      paintPlanAction(block);
      renderWorkspaceRole();
      host.querySelector('[data-action="save"]').disabled=true;
      saveState='idle'; savedAt=0; if(autoSaveTimer){globalThis.clearTimeout(autoSaveTimer);autoSaveTimer=null;} paintSaveState();
      hydrateIgnored();
      void loadWritingModels().then(() => paintAside());
      void refreshDeliveryGate();
      void loadChapterReview().then(() => {if(active?.id===block.id)paintAside();});
      void json(`/api/blocks/${block.id}/studio/writing-job`).then(job => {
        if (active?.id !== block.id || !job || !['queued','running','failed','done'].includes(job.status)) return;
        currentJobId = job.id;
        lastSections=job.artifacts?.sections || [];
        if(job.status==='done' && job.saved){
          if(job.snapshot?.editing && job.content && block.content!==job.snapshot.base_content){aiBaselines.set(block.id,{before:job.snapshot.base_content,after:job.content});}
          else if(block.content===job.snapshot?.base_content){aiBaselines.delete(block.id);sessionStorage.removeItem(`ai-change:${threadId}:${block.id}`);}
          paintAIAction();return;
        }
        if(job.snapshot?.editing && (['failed','cancelled'].includes(job.status) || (job.status==='done' && !job.saved))){pendingProposals.set(block.id,job.content);get('recovery').hidden=false;get('recovery').innerHTML='<span>修改未完成，原文已保留。</span><button type="button" data-compare-proposal>查看未完成结果</button>';return;}
        if(job.snapshot?.base_content !== (block.content || '') && !['queued','running'].includes(job.status)){
          pendingProposals.set(block.id,job.content);get('recovery').hidden=false;get('recovery').innerHTML='<span>另有编制结果可核对。</span><button type="button" data-compare-proposal>查看变更</button>';return;
        }
        if(job.blocks?.length && (job.status !== 'done' || job.blocks.some(item=>item.state!=='done'))){live.update(job);paintView(false);}
        if(['queued','running'].includes(job.status))void runChapterWriteJob(get('agents'),{jobId:job.id,baseContent:job.snapshot?.base_content});
        else if(job.status !== 'done' || live.pending)status('上次编制未完成，进度已恢复，可重试未完成项');
      }).catch(()=>{});
      void loadPrecheckAndVerdict().then(() => paintAside());
      void loadRubricAndPractices().then(() => paintAside());
      void loadMaterialNeeds().then(() => paintAside());
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
    if(event.target.closest('[data-batch-write]')){await openBatchDialog();return;}
    const next=event.target.closest('[data-next-chapter]');if(next){await select(next.dataset.nextChapter);return;}
    if(event.target.closest('[data-project-facts]')){await openProjectFacts();return;}
    if(event.target.closest('[data-compare-proposal]')){await compareProposal();return;}
    const locate=event.target.closest('[data-issue-locate]');
    if(locate){
      const issue=reviewIssueRows.find(item=>item.fingerprint===locate.dataset.issueLocate);
      const evidence=issue?.evidence||{};
      const quote=evidence.quote || evidence.text || issue?.point || '';
      live.hide();editor.hidden=false;get('preview').hidden=true;
      const index=editor.value.indexOf(quote);
      if(index>=0){editor.focus();editor.setSelectionRange(index,index+quote.length);status('已定位对应正文');}
      else {status('未找到精确段落，请依据该问题核对本章：'+(issue?.point||''));}
      return;
    }
    const verify=event.target.closest('[data-issue-verify]');
    if(verify){
      if(!await save())return;
      const block=active;verify.disabled=true;
      try{const result=await json(`/api/blocks/${block.id}/studio/review/verify-issue`,send('POST',{fingerprint:verify.dataset.issueVerify}));
        if(active?.id===block.id){await loadChapterReview();paintAside();status((result.resolved?'该项已通过复核：':'该项仍需处理：')+result.reason);}}
      catch(error){status(error.message);}finally{verify.disabled=false;}return;
    }

    const toggle = event.target.closest('[data-tree-toggle]');
    if (toggle) {
      const id = Number(toggle.dataset.treeToggle);
      if (!expandedChapters.delete(id)) expandedChapters.add(id);
      paintTree();
      get('tree').querySelector(`[data-tree-toggle="${id}"]`)?.focus();
      return;
    }
    const panel = event.target.closest('[data-open-panel]');
    if (panel) {
      showPanel(panel.getAttribute('aria-expanded') === 'true' ? null : panel.dataset.openPanel, panel);
      return;
    }
    if (event.target.closest('[data-close-panel]')) {
      showPanel();
      panelTrigger?.focus();
      return;
    }
    const asidePlan=event.target.closest('[data-aside-open-plan]');
    if(asidePlan){await openChapterPlan(asidePlan.dataset.asideOpenPlan);return;}
    const segButton=event.target.closest('[data-cite-segments]');
    if(segButton){openCitationSegments(segButton.dataset.citeSegments);return;}
    const citeButton=event.target.closest('[data-add-citation]');
    if(citeButton){openCitationDialog(citeButton.dataset.addCitation);return;}
    const specLink=event.target.closest('[data-spec]');
    if(specLink){globalThis.document.dispatchEvent(new CustomEvent('writing-specs:open',{detail:{layout:specLink.dataset.spec}}));return;}
    const readinessPlan=event.target.closest('[data-readiness-plan]');
    if(readinessPlan){await openChapterPlan(String(active?.block_id || ''));return;}
    const planButton=event.target.closest('[data-plan]');
    if(planButton){await openChapterPlan(String(active?.block_id || ''));return;}
    const chapter=event.target.closest('[data-chapter]');
    if(chapter) {await select(chapter.dataset.chapter);return;}
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
    const promptBtn=event.target.closest('[data-sec-prompt]');
    if(promptBtn && active){ await openSectionPromptDialog(Number(promptBtn.dataset.secPrompt)); return; }
    const secBtn=event.target.closest('[data-aside-write-section]');
    if(secBtn && active){
      const sec=(planBrief?.outline||[])[Number(secBtn.dataset.asideWriteSection)];
      if(!sec) return;
      const doWrite = async (prompt, generic=false) => {
        const label0=secBtn.textContent; secBtn.disabled=true; secBtn.textContent='编写中…';
        try{
          status(`正在编写「${sec.title}」…（正文预览区高亮该节）`);
          secBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          await streamWriteSection(active.id,sec,(chars)=>status(`正在编写「${sec.title}」… 已 ${chars} 字`),prompt,'rewrite',generic);
          const fresh=await json(`/api/blocks/${active.id}/studio`); context=fresh;
          if(fresh.block)editor.value=fresh.block.content||'';
          await loadBasis(active.block_id);
          status(`已${label0.startsWith('重写')?'重写':'编写'}「${sec.title}」`);
          paintAside();
        }catch(error){status(error.message);}
        secBtn.disabled=false; secBtn.textContent=label0;
      };
      if(secBtn.textContent.trim()==='重写'){ openRewriteDialog(sec, doWrite); }
      else { await doWrite(''); }
      return;
    }
    if(event.target.closest('[data-job-stop]') && currentJobId){
      try{ await fetch(`/api/projects/${projectId}/studio/chapter-writes/${currentJobId}/cancel`,{method:'POST'});
        status('已停止后续调度；已完成的节保留'); }catch(error){ status(error.message); }
      return;
    }
    if(event.target.closest('[data-job-retry]') && currentJobId){
      try{ await json(`/api/projects/${projectId}/studio/chapter-writes/${currentJobId}/retry`,{method:'POST'});
        void runChapterWriteJob(get('agents'),{jobId:currentJobId});
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
      if(String(option.type||'')==='review_search'){
        const query=String((option.payload||{}).query||'');
        try{ await navigator.clipboard.writeText(query); status(`已复制检索词「${query}」`); }
        catch(error){ status(`检索词：${query}`); }
        const citeBtn=globalThis.document.querySelector('[data-cite-open]');
        if(citeBtn) citeBtn.click();
        return;
      }
      await applyDecideOption(task, option.label);
      return;
    }
    const evUpload=event.target.closest('[data-evidence-upload]');
    if(evUpload){
      pendingEvidenceNeed=null;
      host.querySelector('[data-evidence-file]')?.click();
      return;
    }
    const needUpload=event.target.closest('[data-evidence-need-upload]');
    if(needUpload){
      pendingEvidenceNeed={section:needUpload.dataset.evidenceNeedSection||'', needId:needUpload.dataset.evidenceNeedUpload||''};
      host.querySelector('[data-evidence-file]')?.click();
      return;
    }
    const needEvidenceSearch=event.target.closest('[data-evidence-need-search]');
    if(needEvidenceSearch && active){
      openCitationDialog(active.block_id, String(needEvidenceSearch.dataset.evidenceNeedSearch||''));
      return;
    }
    const needIgnore=event.target.closest('[data-evidence-ignore]');
    if(needIgnore && active){
      const reason=globalThis.prompt('忽略这条证据需求的原因（可留空）','非强制/已有其他证据');
      if(reason===null) return;
      try{
        const resp=await fetch(`/api/blocks/${active.id}/materials/needs/action`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({section:needIgnore.dataset.evidenceIgnoreSection||'',
                               need_id:needIgnore.dataset.evidenceIgnore||'',
                               action:'ignore',reason:String(reason||'').slice(0,300)})});
        if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside(); status('已忽略该证据需求');
      }catch(error){status(`忽略失败：${error.message}`);}
      return;
    }
    const needRestore=event.target.closest('[data-evidence-restore]');
    if(needRestore && active){
      try{
        const resp=await fetch(`/api/blocks/${active.id}/materials/needs/action`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({section:needRestore.dataset.evidenceRestoreSection||'',
                               need_id:needRestore.dataset.evidenceRestore||'',
                               action:'restore'})});
        if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside(); status('已恢复该证据需求');
      }catch(error){status(`恢复失败：${error.message}`);}
      return;
    }
    if(event.target.closest('[data-material-admin]')){ pendingEvidenceNeed=null; await openMaterialAdminDialog(); return; }
    if(event.target.closest('[data-cite-search]') && active){ openCitationDialog(active.block_id); return; }
    const fillBtn=event.target.closest('[data-cite-fill]');
    if(fillBtn){
      const [section, quote] = String(fillBtn.dataset.citeFill||'').split('|');
      const label = globalThis.prompt('补哪个来源？（可写「招标文件（第X页）」或来源名）', '招标文件');
      if(!label) return;
      try{
        const resp=await fetch(`/api/blocks/${active.id}/citations/fill`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({section,quote,source_label:label})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        const fresh=await json(`/api/blocks/${active.id}/studio`);
        context=fresh; if(fresh.block) editor.value=fresh.block.content||'';
        await loadMaterialLibrary(); paintAside();
        status(`已补来源：${data.label}`);
      }catch(error){status(`补来源失败：${error.message}`);}
      return;
    }
    const exBtn=event.target.closest('[data-cite-excerpt]');
    if(exBtn){
      const [kind, id]=String(exBtn.dataset.citeExcerpt||'').split('|');
      try{
        const resp=await fetch(`/api/blocks/${active.id}/citations/${kind}/${id}/excerpt`);
        const data=await resp.json();
        if(!resp.ok)throw new Error(data.detail||'读取失败');
        const dlg=globalThis.document.createElement('dialog');
        dlg.className='cs-plan-dialog';
        dlg.innerHTML=`<header><h3>原��</h3><button type="button" data-ex-close>×</button></header>
          <div class="cs-plan-body"><p class="cs-muted">${esc(data.title||'')}${data.page?` · 第 ${data.page} 页`:''}${data.url?` · ${esc(data.url)}`:''}</p>
          <pre class="cs-excerpt-text">${esc(String(data.text||'').slice(0,1200))}</pre></div>
          <footer class="cs-ins-foot"><button type="button" class="cs-cp-ghost" data-ex-close2>关闭</button></footer>`;
        dlg.addEventListener('click',(event)=>{ if(event.target.closest('[data-ex-close]')||event.target.closest('[data-ex-close2]'))dlg.close(); });
        dlg.addEventListener('close',()=>dlg.remove());
        globalThis.document.body.appendChild(dlg); dlg.showModal();
      }catch(error){status(`看原文失败：${error.message}`);}
      return;
    }
    const autoReviewBox=event.target.closest('[data-auto-review]');
    if(autoReviewBox){
      try{ globalThis.localStorage.setItem('cs_auto_review', autoReviewBox.checked?'on':'off'); }catch(error){ /* ignore */ }
      status(autoReviewBox.checked?'已开启：改完自动复评审':'已关闭自动复评审');
      return;
    }
    const adoptBtn=event.target.closest('[data-practice-adopt]');
    if(adoptBtn){
      const candidate=practiceCandidates[Number(adoptBtn.dataset.practiceAdopt||0)];
      if(!candidate){status('候选已失效，请重新评审');return;}
      try{
        const resp=await fetch(`/api/blocks/${active.id}/studio/practice-candidates/apply`,{
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({section:'', text:candidate.text})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(data.detail||`HTTP ${resp.status}`);
        status(`已写入整章写法（${data.section}）：下次生成本章会遵循`);
      }catch(error){status(`采用失败：${error.message}`);}
      return;
    }
    if(event.target.closest('[data-need-upload]')){
      await openMaterialAdminDialog();
      return;
    }
    const needSearch=event.target.closest('[data-need-search]');
    if(needSearch){
      const query=String(needSearch.dataset.needSearch||'');
      try{ await navigator.clipboard.writeText(query); status(`已复制检索词「${query}」，在"插入 → 官方来源"里粘贴搜索`); }
      catch(error){ status(`检索词：${query}`); }
      const citeBtn=globalThis.document.querySelector('[data-cite-open]');
      if(citeBtn) citeBtn.click();
      return;
    }
    if(event.target.closest('[data-review-run]')){
      const btn=event.target.closest('[data-review-run]');
      btn.disabled=true; status('正在评审本章（技术 + 合规，约 30~60 秒）…');
      try{
        const resp=await fetch(`/api/blocks/${active.id}/studio/review`,{method:'POST',signal:AbortSignal.timeout(240000)});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadChapterReview(); await loadPrecheckAndVerdict(); await loadRubricAndPractices(); await loadBasis(active.block_id); paintAside();
        status(`评审完成：${data.score} 分（技术 ${data.tech_score} / 合规 ${data.comp_score}），问题 ${(data.issues||[]).length} 条`);
      }catch(error){status(`评审失败：${error.message}`);}
      finally{btn.disabled=false;}
      return;
    }
    const rwBtn=event.target.closest('[data-review-rewrite]');
    if(rwBtn && chapterReview){
      const index=Number(rwBtn.dataset.reviewRewrite||0);
      const issue=(chapterReview.issues||[])[index];
      if(!issue){status('这条意见已失效，请重新评审');return;}
      await openReviewPatchDialog(issue, index);
      return;
    }
    const evBind=event.target.closest('[data-evidence-bind]');
    if(evBind && active){
      const signed=Number(evBind.dataset.evidenceBind);
      try{
        const resp=await fetch(`/api/blocks/${active.id}/materials/bind`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chunk_ids:[signed],section:evBind.dataset.evidenceSection||'',
                               requirement_id:evBind.dataset.evidenceNeed||'',mode:'data'})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside();
        status(`已采用到「${evBind.dataset.evidenceSection||'本章'}」`);
      }catch(error){status(`采用失败：${error.message}`);}
      return;
    }
    const matReparse=event.target.closest('[data-material-reparse]');
    if(matReparse){
      try{
        const resp=await fetch(`/api/materials/${matReparse.dataset.materialReparse}/reparse`,{method:'POST'});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside();
        status('已重新排队解析');
      }catch(error){status(`重试失败：${error.message}`);}
      return;
    }
    const recBtn=event.target.closest('[data-mrec]');
    if(recBtn && active){
      const [rawId, kind] = String(recBtn.dataset.mrec||'').split('|');
      const chunkId=Number(rawId);
      try{
        await fetch(`/api/projects/${projectId}/material-sources/pick`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chunk_ids:[chunkId],picked:true,source_kind:kind==='web'?'web':'file'})});
        const signed = kind==='web' ? [-chunkId] : [chunkId];
        await fetch(`/api/blocks/${active.id}/materials/bind`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chunk_ids:signed,section:'',mode:'cite'})});
        await loadMaterialLibrary(); paintAside();
        status('已采用为本章素材');
      }catch(error){status(`采用失败：${error.message}`);}
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
          body:JSON.stringify({chunk_ids:ids,section:pendingEvidenceNeed?.section||'',
                               requirement_id:pendingEvidenceNeed?.needId||'',mode:'data'})});
        const data=await resp.json().catch(()=>({}));
        if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
        await loadMaterialLibrary(); paintAside();
        host.querySelectorAll('dialog').forEach((d) => d.close());
        pendingEvidenceNeed=null;
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
    if(olBatch && active){
      host.querySelectorAll('dialog').forEach((d) => d.close());
      await runTeamChapterJob(active);
      return;
    }
    const matApply=event.target.closest('[data-ol-mat-apply]');
    if(matApply){ await applySectionMaterials(Number(matApply.dataset.olMatApply||0)); return; }
    const matClear=event.target.closest('[data-ol-mat-clear]');
    if(matClear){
      const index=Number(matClear.dataset.olMatClear||0);
      host.querySelectorAll(`[data-ol-mat="${index}"]`).forEach((el)=>{el.checked=false;});
      await applySectionMaterials(index);
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
      if(action==='undo-ai'){
        if(writingBusy || live.pending){status('请先停止编制后撤销');return;}
        const history=aiBaselines.get(active.id);
        if(history?.after!==null && history?.after!==undefined){
          const reverted=reverseAIChange(history.before,history.after,editor.value);
          if(reverted.conflict){
            pendingProposals.set(active.id,history.before);
            status('AI 改动与后续编辑重叠，已保留当前正文；请查看变更后决定是否恢复整份旧版本');
            get('recovery').hidden=false;get('recovery').innerHTML='<span>撤销存在重叠，请核对整份旧版本。</span><button type="button" data-compare-proposal>查看变更</button>';return;
          }
          live.reset();editor.hidden=false;editor.value=reverted.content;aiBaselines.delete(active.id);sessionStorage.removeItem(`ai-change:${threadId}:${active.id}`);changed();paintAIAction();status('已撤销本次 AI 改动，范围外的后续编辑已保留');
        }return;
      }
      if(action==='delivery' && await save()) {
        globalThis.document.querySelector('.sr-rail-item[data-stage="5"]')?.click();
      }
      if(action==='quality'){openQualityDialog();return;}
      if(action==='diagram'){openDiagramDialog();return;}
      if(action==='submit-review'){if(writingBusy){status('编制结束后可提交评审');return;}if(await save()){showPanel('issues');host.querySelector('[data-review-run]')?.click();}return;}
      if(action==='ai'){
        if (editor.value.trim() && !button.dataset.append) {await openScopeDialog();return;}
        const slot = get('agents');
        const chapterId = active.id;
        let generic = false;
        // 代价预估：先算待写节数，让用户知道要花多少
        try {
          const plan = await fetchChapterPlan(active.block_id);
          const outline = plan.outline?.length ? plan.outline : [{title:active.title,deliverables:plan.tasks || []}];
          const bodyNow = String(editor.value || '');
          const pending = outline.filter((sec) => sectionWordCount(bodyNow, sec.title) < 20);
          if (!outline.length) { status('还没有章节大纲：先打开「本章计划」生成大纲'); await openChapterPlan(active.block_id); return; }
          if (!pending.length) { status('整章已按大纲写完；要改某节请用「编制进度 → 重写」'); return; }
          if (active?.id !== chapterId) return;
          const charts = pending.reduce((sum, sec) => sum + (sec.deliverables || []).filter((d) => d.kind !== 'text').length, 0);
          const calls = pending.length + charts;
          if (active?.id !== chapterId) return;
          if (bodyNow.trim() && !await confirmChapterWriteCost(pending.length, charts, calls)) return;
          status(`准备编写 ${pending.length} 节 · 预计 ${charts} 项图表`);
        } catch (error) {
          status(`无法预估编写量：${error.message}`);
          return;
        }
        if (active?.id !== chapterId) return;
        const headBtnEarly = host.querySelector('[data-action="ai"]');
        const headLabelEarly = headBtnEarly ? headBtnEarly.textContent : '';
        const engine = await runChapterWriteJob(slot, {generic});
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
          if (!missing.length) { status('整章已按大纲写完；要改某节请用「编制进度 → 重写」'); return; }
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
      if(action==='review-batch') {
        const nodes=[...host.querySelectorAll('.cs-node[data-chapter]')]
          .filter((el)=>String(el.dataset.st||'')!=='empty')
          .map((el)=>Number(el.dataset.chapter)).filter(Boolean);
        const targets=nodes.length?nodes.slice(0,3):[active.id];
        if(!globalThis.confirm(`将评审 ${targets.length} 章（每章约 30~60 秒），继续？`)) return;
        let done=0;
        for(const blockId of targets){
          status(`正在评审第 ${done+1}/${targets.length} 章…`);
          try{
            const resp=await fetch(`/api/blocks/${blockId}/studio/review`,{method:'POST',signal:AbortSignal.timeout(300000)});
            if(resp.ok) done++;
          }catch(error){ /* 跳过失败的章 */ }
        }
        await loadChapterReview(); await loadBasis(active.block_id); paintAside();
        status(`批量评审完成：${done}/${targets.length} 章`);
        return;
      }
      if(action==='plan-materials-all') {
        if(!globalThis.confirm('为全项目空章节逐节匹配素材并自动采用（不改已写正文），继续？')) return;
        status('正在为全项目生成素材建议…');
        try{
          const resp=await fetch(`/api/projects/${projectId}/plan/material-suggestions/apply-all`,{method:'POST',signal:AbortSignal.timeout(300000)});
          const data=await resp.json().catch(()=>({}));
          if(!resp.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${resp.status}`);
          await loadPlanMaterials();
          status(`已完成：${data.blocks||0} 章 / ${data.sections_applied||0} 节采用素材建议`);
        }catch(error){status(`批量采用失败：${error.message}`);}
        return;
      }
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
  host.addEventListener('chapter-chart-retry', async event => {
    const {jobId,index,title,role}=event.detail;const block=active;
    status(`正在重试「${title}」…`);
    try{const job=await json(`/api/projects/${projectId}/studio/chapter-writes/${jobId}/retry-chart`,send('POST',{index,title,role}));
      if(active?.id===block.id){live.update(job);paintView(previewVisible);status('图表重试结束，请核对后保存');}}
    catch(error){status(error.message);}
  });
  const controller={host,select,dispose(){disposed=true;jobStreams.forEach(detach=>detach());team.dispose();window.removeEventListener('beforeunload',beforeUnload);},save};
  const team={refresh(){},show(){},dispose(){}};   // 「本章编制进度」区块已移除（用户要求）
  currentStudio=controller;
  paintTree();stats();
  // 「最近导出任务」区块已移除（用户要求）
  await select((blocks.find(b=>units.has(b.id)&&!b.content?.trim())||blocks[0])?.id);
  return controller;
}