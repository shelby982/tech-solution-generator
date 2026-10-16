const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,body){const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'编制操作失败');return data;}
const roles={chief:'章节主编',text:'正文',table:'表格',diagram:'图示',wang_anshi:'王安石技术审校',bao_zheng:'包拯合规审校'};

export function representativeChapters(blocks,unitIds){
  const rules=[/总体架构|技术架构|技术实现方案|项目技术工作方案/,/实施计划|进度计划分解|进度计划|计划工期/,/自主可控/];
  const chosen=[];
  for(const rule of rules){const row=blocks.find(b=>unitIds.has(b.id)&&rule.test(b.title)&&!chosen.some(x=>x.id===b.id));if(row)chosen.push(row);}
  return chosen;
}

export function writingPhase(job){
  if(job.status==='done')return 3;
  if(/审校|检查/.test(job.stage))return 2;
  if(/专业|产物/.test(job.stage))return 1;
  return 0;
}

export function canDisplayResult({dirty,automatic,activeId,blockId,currentContent,base}){
  return !dirty && (!automatic || (activeId===blockId && currentContent===base));
}

export function collaborationHtml(job){
  const blocks=job.blocks||[],artifacts=job.artifacts||{},plan=artifacts.chief?.plan;
  const count=kind=>{const items=blocks.filter(b=>b.kind===kind);return `${items.filter(b=>b.state==='done').length}/${items.length}`;};
  const states=artifacts.role_states||{},reviews=artifacts.reviews||artifacts.review_progress||{};
  const roleLine=(role,label)=>{
    const tasks=blocks.filter(b=>b.kind===role);if(!tasks.length)return '';
    const done=tasks.every(b=>b.state==='done'),running=states[role]==='running';
    const chars=tasks.reduce((sum,b)=>sum+(b.characters||0),0);
    return `<li><b>${label}</b><span>${done?'已交付':states[role]==='failed'?'未完成':job.status==='cancelled'?'已停止':running?'正在编制':'等待编制'}${role==='text'&&running?` · 已收到 ${chars} 字符`:''}</span><small>${tasks.map(t=>esc(t.title)).join('、')}</small></li>`;
  };
  return `<div class="ct-collaboration">${plan?`<p><b>内容交付 ${blocks.filter(b=>b.state==='done').length}/${blocks.length}</b><br>文字 ${count('text')} · 图示 ${count('diagram')} · 表格 ${count('table')}</p><details><summary>编制思路与内容安排</summary><p>${esc(plan.objective)}</p>${blocks.map(b=>`<p><b>${esc(b.title)}</b><br>${esc(b.purpose)}</p>`).join('')}${plan.gaps?.length?`<p class="cs-warning">待补充 ${plan.gaps.length} 项 · 具体缺口见编排与检查</p>`:''}</details>`:''}<ul><li><b>章节主编</b><span>${plan?'已形成编排；按计划合稿':job.status==='running'?'正在组织结构':'等待编排'}</span></li>${roleLine('text','正文 Agent')}${roleLine('diagram','图示 Agent')}${roleLine('table','表格 Agent')}${[['wang_anshi','技术审校 Agent'],['bao_zheng','合规审校 Agent']].map(([role,label])=>`<li><b>${label}</b><span>${reviews[role]?`已检查 · ${reviews[role].finding.issues.length} 项意见`:/审校/.test(job.stage)&&job.status==='running'?'正在检查':'等待正文完成'}</span></li>`).join('')}</ul></div>`;
}

export function mountChapterTeam(host,{projectId,threadId,getActive,getModel,beforeStart,onResult,onProgress,getPreparation,onState}){
  let disposed=false,timer,loading=false,pendingRefresh=false,starting=false;
  const watched=new Map(),delivered=new Set();
  host.innerHTML=`<h4>本章编制进度</h4><div class="ct-preparation"></div><div class="ct-status" role="status"></div><div class="ct-jobs"></div><details class="ct-settings"><summary>高级设置：模型分工</summary><p class="cs-muted">默认跟随顶部模型，按内容需要分工；图表不强制生成。</p><div class="ct-models"></div></details><details class="ct-history"><summary>历史任务</summary><div data-team-history></div></details><div class="ct-detail"></div>`;
  const status=message=>host.querySelector('.ct-status').textContent=message;
  const jobs=host.querySelector('.ct-jobs'),detail=host.querySelector('.ct-detail');
  api('/api/configs').then(data=>{
    if(disposed)return;
    const models=[...new Set((data.configs||[]).filter(c=>c.provider!=='typesafe').map(c=>c.model))];
    host.querySelector('.ct-models').innerHTML=Object.entries(roles).map(([key,name])=>`<label>${name}<select data-role="${key}"><option value="">跟随顶部模型</option>${models.map(m=>`<option>${esc(m)}</option>`).join('')}</select></label>`).join('');
  }).catch(error=>status(error.message));

  const renderJob=(j,history=false)=>{
    const phase=writingPhase(j),done=j.status==='done',failed=j.status==='failed';
    return `<article>${history?`<strong>${esc(j.title||'章节已删除')}</strong>`:''}<p>${j.status==='cancelled'?'已停止，已完成内容保留':failed?'编制未完成':done?'内容已生成 · 待核对':j.status==='queued'?'等待编制':'正在'+['编排内容','生成图文表','检查内容'][phase]+'…'}</p>${j.error?`<details><summary>未完成原因</summary><p class="cs-warning">${esc(j.error)}</p></details>`:''}${done?`<button data-team="open" data-job="${j.id}">在编辑区打开</button>`:''}${['queued','running'].includes(j.status)?`<button data-team="cancel" data-job="${j.id}">停止编制</button>`:''}${(failed||j.status==='cancelled')?`<button data-team="retry" data-job="${j.id}">重试</button>`:''}<button data-team="detail" data-job="${j.id}">检查意见</button></article>`;
  };
  async function refresh(){
    if(disposed||!host.isConnected)return;
    if(loading){pendingRefresh=true;return;}
    clearTimeout(timer);loading=true;
    try{
      const result=await api(`/api/projects/${projectId}/studio/writing-jobs?threadId=${encodeURIComponent(threadId)}`);
      const current=result.jobs.find(j=>j.block_id===getActive()?.id);
      const preparation=getPreparation?.();
      host.querySelector('.ct-preparation').innerHTML=preparation?`<details><summary>本章准备 · 评分 ${preparation.points.length} 项 · 规范 ${preparation.requirements.length} 条 · 已核对素材 ${preparation.materials.filter(m=>m.confirmed).length} 份</summary>${preparation.points.map(p=>`<p><b>${esc(p.item||p.factor)} · ${p.points??'—'} 分</b><br>${esc(p.criterion)}</p>`).join('')}<p>无证明素材时可先编制拟实施方案；人员、业绩、证书与实测结果不编造。</p><button data-team="materials">补充本章素材</button></details>`:'';
      jobs.innerHTML=current?renderJob(current):'<p class="cs-muted">本章尚未开始 AI 编制。</p>';
      host.querySelector('[data-team-history]').innerHTML=result.jobs.filter(j=>j.id!==current?.id).map(j=>renderJob(j,true)).join('')||'<p class="cs-muted">暂无历史任务</p>';
      onState?.(starting?{status:'queued'}:current);
      if(current){
        const full=await api(`/api/projects/${projectId}/studio/writing-jobs/${current.id}`);
        if(disposed)return;
        if(['queued','running','failed','cancelled'].includes(full.status)||watched.has(full.id))onProgress?.(full);
        const article=jobs.querySelector('article');
        if(article)article.insertAdjacentHTML('beforeend',collaborationHtml(full));
      }
      for(const job of result.jobs){
        if(job.status==='done'&&watched.has(job.id)&&!delivered.has(job.id)){
          delivered.add(job.id);
          const full=await api(`/api/projects/${projectId}/studio/writing-jobs/${job.id}`);
          const applied=await onResult?.(full,{automatic:true,base:watched.get(job.id)});
          if(!disposed)status(applied?'已显示在正文编辑区，请核对后保存。':'编制已完成；从任务中打开结果，人工编辑保持不变。');
        }
      }
      if(result.jobs.some(j=>['queued','running'].includes(j.status)))timer=setTimeout(refresh,800);
    }catch(error){status(error.message);timer=setTimeout(refresh,5000);}
    finally{loading=false;if(pendingRefresh){pendingRefresh=false;refresh();}}
  }

  async function start(){
    if(starting)return;
    starting=true;onState?.({status:'queued'});
    try{
        if(!await beforeStart())return;
        const selected=getActive();if(!selected)throw new Error('请先选择章节');
        const base=selected.content||'',models={default:getModel?.()||''};
        for(const select of host.querySelectorAll('[data-role]'))if(select.value)models[select.dataset.role]=select.value;
        const result=await api(`/api/projects/${projectId}/studio/writing-jobs`,{thread_id:threadId,block_ids:[selected.id],models,auto_save_empty:false});
        for(const id of result.jobs)watched.set(id,base);
        status(`正在编制「${selected.title}」，完成后显示在编辑区。`);await refresh();
    }catch(error){status(error.message);}
    finally{starting=false;refresh();}
  }

  host.onclick=async event=>{
    const button=event.target.closest('[data-team]');if(!button)return;
    button.disabled=true;
    try{
      const action=button.dataset.team;
      if(action==='materials'){host.closest('.cs-inspector').querySelector('[data-panel=materials]').click();}
      if(action==='cancel'){await api(`/api/projects/${projectId}/studio/writing-jobs/${button.dataset.job}/cancel`,{});await refresh();}
      if(action==='retry'){await api(`/api/projects/${projectId}/studio/writing-jobs/${button.dataset.job}/retry`,{});await refresh();}
      if(action==='detail'||action==='open'){
        const job=await api(`/api/projects/${projectId}/studio/writing-jobs/${button.dataset.job}`);
        if(action==='open'){await onResult?.(job);return;}
        const artifacts=job.artifacts,plan=artifacts.chief?.plan;
        detail.innerHTML=`<h4>检查意见与待补充</h4>${plan?.gaps?.length?plan.gaps.map(g=>`<p class="cs-warning">${esc(g)}</p>`).join(''):''}${Object.entries(artifacts.reviews||artifacts.review_progress||{}).map(([key,value])=>`<h5>${esc(roles[key])}</h5>${value.finding.issues.map(i=>`<p class="cs-warning">${esc(i.point)}<br>${esc(i.suggestion)}</p>`).join('')||'<p>未报告问题，承诺与事实仍需人工核对。</p>'}`).join('')||'<p>暂无检查结果。</p>'}`;
      }
    }catch(error){status(error.message);}
    finally{button.disabled=false;}
  };
  return {start,refresh,dispose(){disposed=true;clearTimeout(timer);}};
}
