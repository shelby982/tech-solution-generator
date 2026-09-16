import {workspace, subscribe, refreshWorkspace, workspaceUrl, confirmChapterAction, openDialog, showHistory, statusLabels, esc} from './workspace.js?v=20260916-loop2';
const nav = document.querySelector('.doc-outline');
let selectedId = new URLSearchParams(location.search).get('blockId');
let limit = 150, initialSelection = false;
const tools = document.createElement('div');tools.className='ws-outline-tools';
tools.innerHTML='<input type="search" aria-label="搜索章节" placeholder="搜索章节名称或问题" /><select aria-label="筛选章节"><option value="all">全部章节</option><option value="attention">需要处理</option><option value="empty">待撰写</option><option value="stale">待复审</option><option value="material">待补料</option><option value="passed">已通过</option></select>';
nav.before(tools);
const search=tools.querySelector('input'), filter=tools.querySelector('select');
document.querySelector('.outline-search-btn').onclick=()=>search.focus();
search.oninput=filter.onchange=()=>{limit=150;renderOutline();};
function editableBlock(b) {
  return {...b,id:b.db_id || b.block_id,kind:'content',source:b.source || '[]'};
}
function selectChapter(id) {
  const b=workspace.chapters.find(b=>b.block_id===id);if(!b)return;
  if(window.__editorDirty?.()) {
    const dialog=openDialog('保留未保存修改', '<p>当前章节尚未保存。取消后可继续编辑并保存，或放弃修改后切换章节。</p><footer><button data-stay>继续编辑</button><button data-discard>放弃修改并切换</button></footer>');
    dialog.querySelector('[data-stay]').onclick=()=>dialog.close();
    dialog.querySelector('[data-discard]').onclick=()=>{window.__discardEditorChanges?.();dialog.close();selectChapter(id);};
    return;
  }
  const block=editableBlock(b);
  if (typeof window.__onBlockSelected !== 'function') return;
  if(window.__onBlockSelected(block)===false)return;
  selectedId=id;initialSelection=true;
  const url=new URL(location.href);url.searchParams.set('blockId',id);history.replaceState(null,'',url);
  nav.querySelectorAll('[data-block-id-str]').forEach(el=>el.classList.toggle('active',el.dataset.blockIdStr===id));
  renderContext();
}
function renderOutline() {
  window.__extractBlockMap=new Map(workspace.chapters.map(b=>[b.block_id,editableBlock(b)]));
  window.__allBlocks=[...window.__extractBlockMap.values()];
  const q=search.value.toLowerCase().trim();
  const list=workspace.chapters.filter(b=>(!q||`${b.title} ${b.issues.map(i=>i.point).join(' ')}`.toLowerCase().includes(q)) && (filter.value==='all'||(filter.value==='attention'?['material','revise','stale','error','historical','unreviewed'].includes(b.status):b.status===filter.value)));
  nav.innerHTML=list.slice(0,limit).map(b=>`<button type="button" class="doc-outline-item level-${Math.min(4,Number(b.level)||1)}${b.block_id===selectedId?' active':''}" data-block-id-str="${esc(b.block_id)}" data-block-id="${esc(b.db_id || '')}" title="${esc(b.title)}"><span class="outline-item-label">${esc(b.title)}</span><span class="ws-chapter-status" data-status="${b.status}">${statusLabels[b.status]}</span><span class="outline-item-dot" hidden></span></button>`).join('')||'<p class="ws-muted" style="padding:12px">暂无符合条件的章节</p>';
  if(list.length>limit){const more=document.createElement('button');more.className='ws-button';more.textContent=`显示更多（${limit}/${list.length}）`;more.onclick=()=>{limit+=150;renderOutline();};nav.append(more);}
  document.getElementById('outline-progress-badge').textContent=workspace.chapters.length;
  const n=workspace.chapters.filter(b=>b.content.trim()).length;
  document.getElementById('outline-progress-text').textContent=`${n} / ${workspace.chapters.length}`;
  document.getElementById('outline-progress-bar').style.width=`${workspace.chapters.length?n/workspace.chapters.length*100:0}%`;
}
nav.addEventListener('click',e=>{const item=e.target.closest('[data-block-id-str]');if(item)selectChapter(item.dataset.blockIdStr);});
let context;
function renderContext() {
  const b=workspace.chapters.find(b=>b.block_id===selectedId);if(!b)return;
  if(!context){context=document.createElement('section');context.className='ws-editor-review';document.querySelector('[aria-label="参考与素材"]')?.prepend(context);}
  context.innerHTML=`<h3>本章质量检查 <span class="ws-chapter-status" data-status="${b.status}">${statusLabels[b.status]}</span></h3>
    ${b.stale?'<p>正文已变更，请复审验证修改结果。</p>':''}
    ${b.issues.slice(0,3).map(i=>`<p>${esc(i.point)}</p>`).join('') || '<p>暂无具体评审意见。</p>'}
    <a href="${esc(workspaceUrl('review',b.block_id))}">查看全部意见与正文对比 →</a><p><button type="button" class="ws-button" data-review ${!b.content.trim()||workspace.busy?'disabled':''}>复审本章</button> <button type="button" class="ws-button" data-history ${!b.db_id?'disabled':''}>版本</button></p>`;
  context.querySelector('[data-review]').onclick=()=>{
    if(window.__editorDirty?.()){alert('请先保存正文，再发起复审。');return;}
    confirmChapterAction([b.block_id],'review');
  };
  context.querySelector('[data-history]').onclick=()=>showHistory(b.db_id);
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
