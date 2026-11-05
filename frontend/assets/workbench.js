import {workspace, subscribe, refreshWorkspace, workspaceUrl, confirmChapterAction, openDialog, showHistory, statusLabels, esc} from './workspace.js?v=20261016-v466';
// 章节状态/依据徽标与「素材库」共用一份投影（同一棵目录树，四个环节都一样）
import {chapterBadges, parseEvidence} from './workspace-model.mjs?v=20261016-v466';
// 左栏章节树：**三个模式共用一份**行标记（2026-10-13 归一）。
// 此前编辑器侧自己拼 `.doc-outline-item`、大纲侧拼 `.ot-row`，是两套实现；
// 数据投影本就共用（workspace-model），这里把标记也收敛掉。
// 行上仍保留 `doc-outline-item` 类与 data-block-id 属性，兼容 mode-editor.js 里
// 既有的 10 处选择器（改动面最小化）。
import {chapterNode, renderChapterTree, chapterBadgesHtml} from './chapter-tree.mjs?v=20261016-v466';
// 左栏树的**唯一宿主**：由 mode-dispatch 在模式切换时把 #blocks-list 搬到当前模式的
// 左栏位置。归一前这里是 `.doc-outline`，与大纲侧 `#blocks-list` 各渲一棵（实测
// .ot-node 有 120 个 = 两棵各 60）。动态解析而不是加载时捕获，就是为了让宿主可搬。
const hostEl = () => document.getElementById('blocks-list') || document.querySelector('.doc-outline');
let selectedId = new URLSearchParams(location.search).get('blockId');
let limit = 150, initialSelection = false;
// 搜索框放进共用的 .chap-search 槽位（与「素材库」同位置同尺寸）
const tools=document.getElementById('ws-search-slot')||document.createElement('div');
tools.innerHTML='<input type="search" aria-label="搜索章节" placeholder="搜索章节名称或问题" />';
const search=tools.querySelector('input'), chipsBox=document.querySelector('.outline-pane .chap-chips');
// 与「素材库」的筛选 chips 同一套样式与位置（左栏顶部）。词表按**本阶段关心的事**不同：
// 素材库关心"有没有依据"，撰写关心"写没写 / 评审状态" —— 选项不同，组件与位置一致。
const FILTERS=[['all','全部'],['attention','需要处理'],['empty','待撰写'],['material','待补料'],['stale','待复审'],['passed','已通过']];
let filterMode='all';
function filterCount(mode){
  return workspace.chapters.filter(b=>{
    if(mode==='all')return true;
    if(mode==='attention')return ['material','revise','stale','error','historical','unreviewed'].includes(b.status);
    return b.status===mode;
  }).length;
}
function renderChips(){
  chipsBox.innerHTML=FILTERS.map(([v,l])=>`<button type="button" class="chap-chip" role="tab" data-filter="${v}" aria-selected="${filterMode===v}">${l}<span class="chap-chip-count">${filterCount(v)}</span></button>`).join('');
}
chipsBox.addEventListener('click',e=>{const c=e.target.closest('[data-filter]');if(!c)return;filterMode=c.dataset.filter;limit=150;renderChips();renderOutline();});
// 搜索框现在常显（原来靠栏头那个放大镜按钮切换）。这一行以前直接给 null 挂事件 ——
// 元素一删，整个模块在这一行抛 TypeError 中断，后面的目录渲染/标题/徽标全不执行
// （实测：树为空、顶部标题永远 "—"，而且控制台只报了个 SSE 404，很难查）。
document.querySelector('.outline-search-btn')?.addEventListener('click',()=>search.focus());
search.oninput=()=>{limit=150;renderOutline();};
function editableBlock(b) {
  return {...b,id:b.db_id || b.block_id,kind:'content',source:b.source || '[]'};
}
// 右栏顶部展示当前章节标题（父级 › 本章）；原来 workbench.html 里找的是已不存在的
// #section-title，`if (sTitle)` 一挡就静默失效了 —— 标题栏一直是「撰写正文」。
function renderEditorTopbar(b) {
  const t=document.getElementById('et-title'); if(t) t.textContent=b?.title||'—';
  const p=document.getElementById('et-parent'); if(p) p.textContent=b?.parent_title||'';
  const badge=document.getElementById('et-auto');
  if(badge) badge.textContent = b?.content ? '' : '基于应标要求自动生成';
}

function selectChapter(id) {
  const b=workspace.chapters.find(b=>b.block_id===id);if(!b)return;
  if(window.__editorDirty?.()) {
    const dialog=openDialog('保留未保存修改', '<p>当前章节未保存。取消后可继续编辑并保存，或放弃修改后切换章节。</p><footer><button data-stay>继续编辑</button><button data-discard>放弃修改并切换</button></footer>');
    dialog.querySelector('[data-stay]').onclick=()=>dialog.close();
    dialog.querySelector('[data-discard]').onclick=()=>{window.__discardEditorChanges?.();dialog.close();selectChapter(id);};
    return;
  }
  const block=editableBlock(b);
  if (typeof window.__onBlockSelected !== 'function') return;
  if(window.__onBlockSelected(block)===false)return;
  selectedId=id;initialSelection=true;
  renderEditorTopbar(b);
  const url=new URL(location.href);url.searchParams.set('blockId',id);history.replaceState(null,'',url);
  hostEl().querySelectorAll('[data-block-id-str]').forEach(el=>el.classList.toggle('active',el.dataset.blockIdStr===id));
  renderContext();
}
let outlineIndex = new Map();

/** 编辑器侧的徽标：依据数来自 evidence，覆盖数来自本阶段拉到的应答条目。
 *  **拼装**交给共享的 chapterBadgesHtml —— 状态文案（chapterStage/CHAPTER_STAGE_LABELS）
 *  同源，避免两边各写一套后漂移（历史上漂过 "#undefined"）。 */
function editorBadges(b) {
  const evs = parseEvidence(b);
  const items = ((window.__responseItems || {})[String(b.block_id)] || []).filter(it => !it.informative);
  const covered = new Set(evs.flatMap(e => e.item_ids || []));
  const coverage = items.length
    ? { covered: items.filter(it => covered.has(it.item_id)).length, total: items.length }
    : null;
  // 编辑器看的是**撰写状态**（待撰写/待补料/待复审…），与筛选 chips 同一套词表；
  // 不传 label 会退化成提炼词表，与 chips 对不上。
  return chapterBadgesHtml(b, { evidenceCount: evs.length, coverage, status: b.status, label: statusLabels[b.status] });
}

function renderOutline() {
  // ⚠️ 共享宿主只有一个（#blocks-list 由 mode-dispatch 在模式间搬运）。
  // 本模块只能在自己是当前模式时渲染，否则会把「要求与大纲 / 素材库」的目录
  // 覆盖成写作版样式 —— 实测刷新后目录变成 `doc-outline-item / 待撰写 / 无编辑按钮`，
  // 与点「拆解评分点」当场生成的样式不一致（用户报的正是这个）。
  if ((document.body.dataset.view || '') !== 'editor') return;
  window.__extractBlockMap=new Map(workspace.chapters.map(b=>[b.block_id,editableBlock(b)]));
  window.__allBlocks=[...window.__extractBlockMap.values()];
  const q=search.value.toLowerCase().trim();
  const list=workspace.chapters.filter(b=>(!q||`${b.title} ${b.issues.map(i=>i.point).join(' ')}`.toLowerCase().includes(q)) && (filterMode==='all'||(filterMode==='attention'?['material','revise','stale','error','historical','unreviewed'].includes(b.status):b.status===filterMode)));
  renderChips();
  // 嵌套树（与大两侧一致）：改造前这里是"扁平 + level-N 缩进 + .outline-group 分组"，
  // 统一后缩进/折叠/祖先可见性只有一套语义。
  outlineIndex = renderChapterTree(hostEl(), list, {
    makeNode: (b) => chapterNode(b, {
      selected: b.block_id === selectedId,
      showEdit: false,           // 撰写时不改目录结构
      showSub: false,            // 行内摘要让位给标题
      selectedClass: 'active',   // mode-editor.js 按 .doc-outline-item.active 定位
      rowSelectedClass: 'active',
      rowClasses: ['doc-outline-item', `level-${Math.min(4, Number(b.level) || 1)}`],
      rowData: { blockId: b.db_id || '' },
      titleClass: 'outline-item-label',
      appendHtml: '<span class="outline-item-dot" hidden></span>',
      badgesHtml: editorBadges(b),
    }).node,
    emptyHtml: '<p class="ws-muted" style="padding:12px">暂无符合条件的章节</p>',
  });
  if(list.length>limit){const more=document.createElement('button');more.className='ws-button';more.textContent=`显示更多（${limit}/${list.length}）`;more.onclick=()=>{limit+=150;renderOutline();};hostEl().append(more);}
  document.getElementById('outline-progress-badge').textContent=workspace.chapters.length;
  const n=workspace.chapters.filter(b=>b.content.trim()).length;
  document.getElementById('outline-progress-text').textContent=`${n} / ${workspace.chapters.length}`;
  document.getElementById('outline-progress-bar').style.width=`${workspace.chapters.length?n/workspace.chapters.length*100:0}%`;
}

hostEl().addEventListener('click',e=>{const item=e.target.closest('[data-block-id-str]');if(item)selectChapter(item.dataset.blockIdStr);});

// 展开/收起全部（与「素材库」目录栏头的同名按钮同职责）
// 展开/收起全部：归一后树是嵌套的 .ot-node（不再是扁平的 .outline-group），
// 与「要求与大纲 / 素材库」同一套折叠语义（open 类）。
document.getElementById('btn-outline-expand-all')?.addEventListener('click', (e) => {
  const nodes=[...hostEl().querySelectorAll('.ot-node')];
  const anyOpen=nodes.some(n=>n.classList.contains('open'));
  nodes.forEach(n=>n.classList.toggle('open', !anyOpen));
  e.currentTarget.textContent = anyOpen ? '展开全部' : '收起全部';
});

// 收起目录：左栏缩成一条 44px 的竖条，编辑区占满整个宽度；再点右侧「»」恢复。
// 状态记 localStorage（用户摆好的工作台布局不该每次进来都重置）。
(function initOutlineCollapse(){
  const KEY='workbench:outlineCollapsed';
  const apply=(collapsed)=>{
    document.body.classList.toggle('outline-collapsed', collapsed);
    const restore=document.getElementById('btn-expand-outline');
    if(restore) restore.hidden = !collapsed;
  };
  let collapsed=false;
  try{ collapsed = localStorage.getItem(KEY)==='1'; }catch{}
  apply(collapsed);
  document.getElementById('btn-collapse-outline')?.addEventListener('click',()=>{
    collapsed=true; apply(true);
    try{ localStorage.setItem(KEY,'1'); }catch{}
  });
  document.getElementById('btn-expand-outline')?.addEventListener('click',()=>{
    collapsed=false; apply(false);
    try{ localStorage.setItem(KEY,'0'); }catch{}
  });
})();
let context;
// 评审状态：写进编辑区顶部「本章要求」条的评审区（右栏已取消 —— 那一栏原本塞了
// 第 1/2/4 步的内容，是"四个环节界面不清晰"的根因）。
// **有 error 必须显示原因**：以前这里写「暂无具体评审意见」而左栏标「评审异常」，
// 两者放在一起看自相矛盾（实测用户截图）。
function renderContext() {
  const b=workspace.chapters.find(b=>b.block_id===selectedId);if(!b)return;
  const host=document.getElementById('req-bar-review');
  if(!host)return;
  const status = `<span class="ws-chapter-status" data-status="${b.status}">${statusLabels[b.status]}</span>`;
  let detail;
  if(b.reviewError) detail = `<span class="req-bar-err" title="${esc(b.reviewError)}">评审未完成：${esc(String(b.reviewError).slice(0,50))}</span>`;
  else if(b.issues.length) detail = `<span>${b.issues.length} 条评审意见</span>`;
  else detail = b.content.trim() ? '<span>暂无评审意见</span>' : '';   // 没正文时状态标签已写"待撰写"
  host.innerHTML = `${status}${detail}
    ${b.issues.length?`<a href="${esc(workspaceUrl('review',b.block_id))}">去质量检查 →</a>`:''}
    <button type="button" class="ws-button" data-review ${!b.content.trim()||workspace.busy?'disabled':''}>复审</button>
    <button type="button" class="ws-button" data-history ${!b.db_id?'disabled':''}>版本</button>`;
  host.querySelector('[data-review]').onclick=()=>{
    if(window.__editorDirty?.()){(window.__ui?.alert ?? window.alert)('请先保存正文，再发起复审。');return;}
    confirmChapterAction([b.block_id],'review');
  };
  host.querySelector('[data-history]').onclick=()=>showHistory(b.db_id);
}
subscribe(()=>{
  renderOutline();
  if(!initialSelection){const target=workspace.chapters.find(b=>b.block_id===selectedId)||workspace.chapters.find(b=>b.content.trim())||workspace.chapters[0];if(target)selectChapter(target.block_id);}
  else {
    renderContext();
    const b=workspace.chapters.find(b=>b.block_id===selectedId);
    if(b)window.__refreshEditorChapter?.(editableBlock(b));
  }
  const locked=workspace.busy;
  const editor=document.getElementById('ced-editable');
  if(editor)editor.contentEditable=String(!locked);
  document.getElementById('btn-batch-generate').disabled=locked;
});
window.__loadBlocks=refreshWorkspace;
window.addEventListener('block:done',()=>refreshWorkspace());
window.addEventListener('workspace:editor-ready',()=>{if(workspace.ready&&!initialSelection){const b=workspace.chapters.find(b=>b.block_id===selectedId)||workspace.chapters[0];if(b)selectChapter(b.block_id);}});


// 三模式合并：模式切换时由 mode-dispatch 调用（只在编辑器模式下渲染共享宿主）。
window.__renderEditorTree = renderOutline;
