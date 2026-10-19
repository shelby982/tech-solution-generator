import { api } from './api.js?v=20261016-v273';

const pid = Number(new URLSearchParams(location.search).get('projectId'));
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roles = {main_rfp:'主招标文件',requirement:'主招标文件',spec:'技术规范书',scoring:'评分表',evaluation:'评审要素',source:'企业素材'};
let data, timer, replacing = null;
async function request(url, options) {
  const r = await fetch(url, options); const value = await r.json();
  if (!r.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '操作失败');
  return value;
}
function message(text) { $('alert').textContent = text; $('alert').classList.add('show'); }
async function action(fn) { try { await fn(); } catch(e) { message(e.message); } }
async function edit(p = null) {
  const d = $('package-editor');
  $('edit-id').value = p?.package_id || '';
  $('edit-target').value = p?.target_code || '';
  $('edit-code').value = p?.package_code || '';
  $('edit-name').value = p?.display_name || '';
  $('edit-kind').value = p && !p.package_code && p.target_code ? 'target' : (p?.kind || 'package');
  updateKindFields();
  d.showModal();
}
async function load() {
  data = await api.projects.intake(pid);
  $('title').textContent = `${data.project.name} · 项目准备`;
  const parsing = data.materials.some(m => ['pending','parsing'].includes(m.parse_status));
  document.querySelectorAll('.step').forEach((el,i) => el.classList.toggle('on', i === (!data.materials.length || parsing ? 0 : data.readiness.can_enter_workspace ? 2 : 1)));
  $('identify').disabled = parsing || !data.readiness.has_tender_document;
  $('identify').textContent = parsing ? '等待解析完成' : '识别 / 更新标包';
  $('files').innerHTML = data.materials.map(m => `<article class="file"><strong>${esc(m.filename)}</strong><div class="meta">${roles[m.role] || esc(m.role)} · ${{pending:'等待解析',parsing:'解析中',done:'解析完成',failed:'解析失败'}[m.parse_status] || '待解析'}</div><div class="actions"><a target="_blank" rel="noopener" href="/api/materials/${m.id}/file">查看原文</a><button data-replace="${m.id}">替换</button><button data-reparse="${m.id}" ${['pending','parsing'].includes(m.parse_status)?'disabled':''}>重新解析</button><button data-delete="${m.id}" ${['pending','parsing'].includes(m.parse_status)?'disabled':''}>删除</button></div></article>`).join('') || '<p class="empty">尚未上传文件</p>';
  $('files').querySelectorAll('[data-delete]').forEach(b => b.onclick = () => action(async()=>{
    const affected = data.packages.filter(p => p.source_material_ids.map(String).includes(b.dataset.delete));
    if (!confirm(`删除该文件？${affected.length ? `涉及 ${affected.length} 个标包，已确认结果将改为待复核。` : ''}已有成果保留。`)) return;
    await request(`/api/materials/${b.dataset.delete}`, {method:'DELETE'}); await load();
  }));
  $('files').querySelectorAll('[data-reparse]').forEach(b => b.onclick = () => action(async()=>{await request(`/api/materials/${b.dataset.reparse}/reparse`,{method:'POST'}); await load();}));
  $('files').querySelectorAll('[data-replace]').forEach(b => b.onclick = () => {
    replacing = data.materials.find(m => String(m.id) === b.dataset.replace);
    $('role').value = replacing.role; $('file').click();
    $('upload').textContent = `上传并替换：${replacing.filename}`;
  });
  $('files').querySelectorAll('.file').forEach((card,i)=>{
    const select=document.createElement('select');select.setAttribute('aria-label','修改文件分类');
    select.innerHTML=Object.entries(roles).filter(([key])=>key!=='requirement').map(([key,label])=>`<option value="${key}">${label}</option>`).join('');
    select.value=data.materials[i].role==='requirement'?'main_rfp':data.materials[i].role;
    select.onchange=()=>action(async()=>{await request(`/api/materials/${data.materials[i].id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:select.value})});await load();});
    card.querySelector('.actions').append(select);
  });
  const items = [...data.packages].sort((a,b)=>String(a.target_code).localeCompare(String(b.target_code),'zh-CN',{numeric:true}) || String(a.package_code).localeCompare(String(b.package_code),'zh-CN',{numeric:true}));
  $('packages').className = items.length ? '' : 'empty';
  $('packages').innerHTML = items.map(p => `<article class="pkg"><div class="row"><strong>${esc(p.display_name || (p.package_code ? `标包 ${p.package_code}` : `标的 ${p.target_code}`))}</strong><span class="status ${esc(p.status)}">${{confirmed:'已选择参与',needs_review:'来源已变更 · 待复核',candidate:'待核对'}[p.status] || '待核对'}</span></div><div class="meta">${p.target_code ? `所属标的 ${esc(p.target_code)} · ` : ''}${p.package_code ? `编号 ${esc(p.package_code)}` : '独立标的，请核对投标划分'} · ${p.confidence==='manual'?'人工维护':'系统候选，需核对原文'}</div><details class="evidence"><summary>查看 ${p.evidence.length} 条原文证据</summary>${p.evidence.map(e=>`<p>${esc(e.quote)}<br><a target="_blank" rel="noopener" href="/api/materials/${Number(e.material_id)}/file${e.page?'#page='+Number(e.page):''}">${esc(e.filename)}${e.page?' · 第 '+Number(e.page)+' 页':''}</a></p>`).join('') || '<p>人工补录，请核对招标文件。</p>'}</details><div class="actions"><button class="primary" data-enter="${p.package_id}">选择并进入</button></div></article>`).join('') || '尚无有效候选。请识别招标文件，或手动补录独立投标对象。';
  $('packages').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(items.find(p=>String(p.package_id)===b.dataset.edit)));
  let lastGroup = null;
  $('packages').querySelectorAll('.pkg').forEach((card,i)=>{
    const key=items[i].target_code || '';
    if(key!==lastGroup){const heading=document.createElement('h3');heading.style.cssText='font-size:14px;margin:20px 0 8px';heading.textContent=key?`标的 ${key}`:'所属标的待核对';card.before(heading);lastGroup=key;}
  });
  $('packages').querySelectorAll('[data-exclude]').forEach(b=>b.onclick=()=>action(async()=>{
    const p=items.find(p=>String(p.package_id)===b.dataset.exclude);
    if(!confirm('排除该候选？已有工作成果保留。')) return;
    await save(p.package_id,{...p,status:'archived'}); await load();
  }));
  $('packages').querySelectorAll('.pkg').forEach((card,i)=>{
    const p=items[i];const specs=data.materials.filter(m=>m.role==='spec');
    const field=document.createElement('fieldset');field.style.cssText='border:0;padding:12px 0;margin:0';
    field.innerHTML=`<legend style="font-size:13px;font-weight:600">对应技术规范书</legend>${specs.map(m=>`<label style="display:block;margin:8px 0;font-size:13px"><input type="checkbox" data-spec="${m.id}" ${p.spec_material_ids?.includes(m.id)?'checked':''} ${m.parse_status!=='done'?'disabled':''}> ${esc(m.filename)}</label>`).join('') || '<p class="meta">尚未上传技术规范书，可在左侧上传。</p>'}<label style="display:block;margin-top:10px;font-size:13px"><input type="checkbox" data-no-spec ${p.no_spec?'checked':''}> 本标包无独立技术规范书</label>`;
    field.querySelector('[data-no-spec]').onchange=e=>{if(e.target.checked)field.querySelectorAll('[data-spec]').forEach(c=>c.checked=false);};
    field.querySelectorAll('[data-spec]').forEach(c=>c.onchange=()=>{if(c.checked)field.querySelector('[data-no-spec]').checked=false;});
    card.querySelector('.actions').before(field);
  });
  $('packages').querySelectorAll('[data-enter]').forEach(b=>b.onclick=()=>action(async()=>{
    const p=items.find(p=>String(p.package_id)===b.dataset.enter);
    b.disabled=true;
    try {
    const card=b.closest('.pkg');
    const specIds=[...card.querySelectorAll('[data-spec]:checked')].map(c=>Number(c.dataset.spec));
    const noSpec=card.querySelector('[data-no-spec]').checked;
    if(!specIds.length && !noSpec) throw new Error('请选择对应技术规范书，或选择无独立技术规范书');
    await api.projects.confirmPackage(pid,p.package_id,{
      display_name:p.display_name || '',kind:p.package_code ? p.kind : 'target',
      spec_material_ids:specIds,no_spec:noSpec,
    });
    const ws=await api.projects.packageWorkspace(pid,b.dataset.enter);const q=new URLSearchParams({projectId:pid,packageId:b.dataset.enter,view:'outline'});
    if(ws.latest_run?.thread_id) q.set('threadId',ws.latest_run.thread_id);
    location.href='/three-mode?'+q;
    } finally {b.disabled=false;}
  }));
  clearTimeout(timer); if(parsing) timer=setTimeout(()=>action(load),1200);
}
async function save(id,payload) {
  return request(`/api/projects/${pid}/packages${id?'/'+id:''}`,{method:id?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
}
$('manual').onclick=()=>edit();
function updateKindFields(){
  const independent=$('edit-kind').value==='target';
  $('edit-code').parentElement.hidden=independent;
  $('edit-target').required=independent;
  $('edit-code').required=!independent;
}
$('edit-kind').onchange=updateKindFields;
$('package-form').onsubmit=e=>{e.preventDefault();action(async()=>{

  const independent=$('edit-kind').value==='target';
  const existing=data.packages.find(p=>String(p.package_id)===$('edit-id').value);
  await save($('edit-id').value,{target_code:$('edit-target').value,package_code:independent?'':$('edit-code').value,display_name:$('edit-name').value,kind:$('edit-kind').value,status:existing?.status==='confirmed'?'confirmed':'candidate'});
  $('package-editor').close(); await load();
});};
$('cancel-edit').onclick=()=>$('package-editor').close();
$('cancel-merge').onclick=()=>$('merge-dialog').close();
$('merge-form').onsubmit=e=>{e.preventDefault();action(async()=>{
  await request(`/api/projects/${pid}/packages/${$('merge-source').value}/merge`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination_id:Number($('merge-destination').value)})});
  $('merge-dialog').close();await load();message('重复候选已合并，请重新核对保留项。');
});};
$('identify').onclick=()=>action(async()=>{await api.projects.identifyPackages(pid);await load();message(data.packages.length ? `已识别 ${data.packages.length} 个候选，请在右侧核对并选择参与。` : '未识别到标包，请核对文件内容或手动添加。');});
$('upload').onclick=()=>action(async()=>{
  const f=$('file').files[0];if(!f) throw new Error('请选择文件');
  $('upload').disabled=true;
  try {
    const uploaded=await api.materials.upload(pid,f,$('role').value);
    if(replacing){
      message('新文件解析成功后才会删除旧文件，请保持页面打开。');
      for(let i=0;i<300;i++){
        await new Promise(resolve=>setTimeout(resolve,1000));
        const state=await api.projects.intake(pid);const m=state.materials.find(m=>m.id===uploaded.id);
        if(m?.parse_status==='failed') throw new Error('新文件解析失败，旧文件已保留。可删除失败文件后重试。');
        if(m?.parse_status==='done'){await request(`/api/materials/${replacing.id}`,{method:'DELETE'});replacing=null;break;}
        if(i===299) throw new Error('解析仍未完成，旧文件已保留，请稍后核对。');
      }
    }
    $('file').value='';$('upload').textContent='上传文件';await load();
  } finally {$('upload').disabled=false;}
});
action(load);
