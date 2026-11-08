const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={text:'正文 Agent',table:'表格 Agent',diagram:'图示 Agent'};

export function mergeWritingBlocks(blocks,edits={},removed=[]){
  return blocks.filter(b=>!removed.includes(b.id)).map(b=>({...b,content:Object.hasOwn(edits,b.id)?edits[b.id]:b.content}));
}

export function withManualBlocks(base,custom){
  const result=[...base];
  for(const block of custom){
    const index=result.findIndex(b=>b.id===block.after);
    result.splice(index<0?result.length:index+1,0,block);
  }
  return result;
}

export function mountLiveWriting(host,{editor,render,onChange,scope}){
  let jobId=null,blocks=[],jobStatus='',edits=Object.create(null),removed=[],lastValue='',gaps=[];
  const nodes=new Map();
  let custom=[],focused=null,lastJob=null,selectionInput=null;
  const key=()=>`chapter-live:${scope}:${jobId}`;
  const merged=()=>mergeWritingBlocks(blocks,edits,removed);
  // 事实缺口不进正文（正文始终是完整正常的内容）：gaps 只在待办/质检里呈现
  const value=()=>merged().filter(b=>b.content).map(b=>b.content).join('\n\n');
  const sync=()=>{
    editor.value=value();
    sessionStorage.setItem(key(),JSON.stringify({edits,removed,custom}));
    if(editor.value!==lastValue){lastValue=editor.value;onChange();}
  };
  function update(job){
    if(!job.blocks?.length)return false;
    if(jobId!==job.id){
      jobId=job.id;nodes.clear();host.innerHTML='';
      let saved;try{saved=JSON.parse(sessionStorage.getItem(key())||'{}');}catch{saved={};}
      edits=Object.assign(Object.create(null),saved.edits||{});removed=saved.removed||[];custom=saved.custom||[];focused=null;lastValue=editor.value;
    }
    lastJob=job;blocks=withManualBlocks(job.blocks,custom);jobStatus=job.status;gaps=job.artifacts?.chief?.plan?.gaps||[];
    host.hidden=false;editor.hidden=true;
    for(const [index,block] of merged().entries()){
      let node=nodes.get(block.id);
      if(!node){
        node=document.createElement('section');node.className='cl-block';node.dataset.id=block.id;
        node.innerHTML=`<header><strong>${esc(block.title)}</strong><span></span><button data-live="edit">编辑</button><button data-live="remove" aria-label="移除此内容块">移除</button></header><div class="cl-body"></div>`;
        nodes.set(block.id,node);host.append(node);
      }
      if(host.children[index]!==node)host.insertBefore(node,host.children[index]||null);
      if(block.kind==='heading'){
        node.className='cl-heading';node.textContent=block.title;continue;
      }
      const own=Object.hasOwn(edits,block.id),state=own?'人工编辑已保留':block.state==='done'?'已完成':block.state==='stopped'?'已停止':block.state==='failed'?'未完成，可重试':block.state==='running'?(block.kind==='diagram'?'正在绘制关系图':block.kind==='table'?'正在编制表格':'正在编写'):'等待编制';
      node.querySelector('header span').textContent=`${block.manual?'手动添加':labels[block.kind]} · ${state}`;
      node.classList.toggle('cl-complete',block.state==='done'&&!own);
      const body=node.querySelector('.cl-body');
      if(own){
        if(!body.children.length){
          body.innerHTML='<textarea aria-label="编辑本块内容"></textarea>';
          const input=body.firstElementChild;
          input.value=block.kind==='text'?block.content.replace('### '+block.title+'\n','').trimStart():block.content;
          input.style.height=Math.max(100,input.scrollHeight)+'px';
        }
        continue;
      }
      if(block.kind==='text'){
        let input=body.querySelector('textarea');
        if(!input){body.innerHTML='<textarea aria-label="本节正文"></textarea>';input=body.firstElementChild;}
        const text=(block.content||'').startsWith('### '+block.title+'\n')?block.content.slice(('### '+block.title+'\n').length).trimStart():block.content||'';
        if(input.value!==text){input.value=text;input.style.height='auto';input.style.height=Math.max(100,input.scrollHeight)+'px';}
        input.placeholder=job.status==='failed'?'本节尚未完成，已收到的文字保留。':'正文 Agent 正在准备输出…';
      }else if(block.state==='done'){
        if(body.dataset.content!==block.content){body.innerHTML=render(block.content);body.dataset.content=block.content;}
      }else{
        body.innerHTML=`<div class="cl-placeholder"><b>${block.kind==='diagram'?'图示占位':'表格占位'} · ${esc(block.title)}</b><p>${esc(block.purpose)}</p><small>${esc(state)}；完成后在此替换</small>${block.state==='failed'?'<button type="button" data-retry-chart>重试这一项</button>':''}</div>`;
      }
    }
    for(const [id,node] of nodes)if(removed.includes(id)){node.remove();nodes.delete(id);}
    sync();return true;
  }
  host.addEventListener('focusin',event=>{const node=event.target.closest('.cl-block');if(node)focused=node.dataset.id;if(event.target.tagName==='TEXTAREA')selectionInput=event.target;});
  host.oninput=event=>{
    const node=event.target.closest('.cl-block');if(!node||event.target.tagName!=='TEXTAREA')return;
    const block=blocks.find(b=>b.id===node.dataset.id);
    edits[node.dataset.id]=block.kind==='text'?(block.heading === undefined ? '### '+block.title+'\n\n' : '')+event.target.value:event.target.value;event.target.style.height='auto';event.target.style.height=Math.max(100,event.target.scrollHeight)+'px';
    node.querySelector('header span').textContent='人工编辑已保留 · 本块停止自动更新';sync();
  };
  host.onclick=event=>{
    if(event.target.closest('[data-retry-chart]')){
      const block=blocks.find(item=>item.id===event.target.closest('.cl-block').dataset.id);
      host.dispatchEvent(new CustomEvent('chapter-chart-retry',{bubbles:true,detail:{jobId,index:block.section_index,title:block.title,role:block.kind}}));
      return;
    }
    const button=event.target.closest('[data-live]');if(!button)return;
    const node=button.closest('.cl-block'),block=merged().find(b=>b.id===node.dataset.id);focused=block.id;
    if(button.dataset.live==='remove'){removed.push(block.id);node.remove();nodes.delete(block.id);sync();return;}
    const body=node.querySelector('.cl-body');
    if(body.querySelector('textarea')){
      if(block.kind==='text'){body.querySelector('textarea').focus();return;}
      body.innerHTML=render(edits[block.id]||block.content);button.textContent='编辑';return;
    }
    edits[block.id]=Object.hasOwn(edits,block.id)?edits[block.id]:block.content||'### '+block.title+'\n\n';
    body.innerHTML='<textarea aria-label="编辑本块 Markdown"></textarea>';body.firstElementChild.value=edits[block.id];body.firstElementChild.focus();sync();
    button.textContent='预览';
  };
  function add(kind,content){
    if(!jobId||['queued','running'].includes(jobStatus))return false;
    const title={text:'补充说明',table:'补充表格',diagram:'补充关系图'}[kind];
    const block={id:'manual-'+crypto.randomUUID(),kind,title,content:'### '+title+'\n\n'+content,state:'done',manual:true,after:focused||merged().at(-1)?.id};
    custom.push(block);edits[block.id]=block.content;update(lastJob);focused=block.id;
    nodes.get(block.id)?.querySelector('textarea')?.focus();return true;
  }
  function selectionRange(){
    const input=selectionInput;
    if(!input || !host.contains(input) || input.selectionEnd<=input.selectionStart)return null;
    const blockId=input.closest('.cl-block')?.dataset.id;
    const current=merged().filter(block=>block.content);
    const index=current.findIndex(block=>block.id===blockId);
    if(index<0)return null;
    const content=current[index].content;
    const offset=content.indexOf(input.value);
    if(offset<0)return null;
    const prefix=current.slice(0,index).map(block=>block.content).join('\n\n');
    const start=(index ? prefix.length+2 : 0)+offset+input.selectionStart;
    const end=start+input.selectionEnd-input.selectionStart;
    const body=value();
    return {before:body.slice(0,start),selection:body.slice(start,end),after:body.slice(end)};
  }
  return {update,add,selectionRange,isActiveFor:id=>jobId===id, get hasEdits(){return Object.keys(edits).length > 0 || removed.length > 0 || custom.length > 0;},get pending(){return !!jobId&&blocks.some(b=>!removed.includes(b.id)&&b.state!=='done'&&(b.kind!=='text'||['queued','running'].includes(jobStatus)));},
    get active(){return !!jobId;},get status(){return jobStatus;},show:()=>{host.hidden=false;editor.hidden=true;},hide:()=>{host.hidden=true;},
    reset(){selectionInput=null;jobId=null;blocks=[];custom=[];focused=null;lastJob=null;nodes.clear();host.innerHTML='';host.hidden=true;}};
}
