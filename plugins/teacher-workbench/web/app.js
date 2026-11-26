'use strict';
const $ = (selector,scope=document)=>scope.querySelector(selector);
const esc = value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid=()=> 'ui_'+crypto.randomUUID().replaceAll('-','');
const num=value=>value==null?'未记录':Number(value).toFixed(2).replace(/\.?0+$/,'');
const percent=value=>value==null?'未记录':`${(value*100).toFixed(1)}%`;
const lines=value=>String(value||'').split('\n').map(s=>s.trim()).filter(Boolean);
const json=value=>esc(JSON.stringify(value,null,2));
const kinds={introduction:'导入',explanation:'讲解',practice:'练习',discussion:'讨论',assessment:'检测',reflection:'反思'};
const basis={observed:'实测 / 观察',estimated:'估计'};
const timing={in_class:'课内检测',after_class:'课后检测',delayed:'延迟复测'};
const state={token:'',overview:{lessons:[],feedback:[],decisions:[]},lesson:null,version:null,tab:'materials',categoryFilter:'',sources:[],jobs:[],model:{},feedback:null,renderSequence:0,classes:[],knowledge:[],selectedKnowledge:new Set(),knowledgeTouched:false,referencesLoaded:false};
async function api(route,data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':state.token},body:JSON.stringify(data)};const response=await fetch('/teacher/api/'+route,options);const value=await response.json();if(!response.ok)throw new Error(value.error||'操作失败');return value;}
function notice(message=''){const element=$('#notice');element.textContent=message;element.hidden=!message;}
function bindAction(element,event,action){
  if(event==='submit'&&element?.matches('form'))window.TeacherForms?.attach(element);
  element?.addEventListener(event,async e=>{try{
    if(event==='submit'){e.preventDefault();if(window.TeacherForms&&!window.TeacherForms.validate(element))return;}
    notice();const buttons=element.matches('button')?[element]:[...element.querySelectorAll('button[type="submit"],button.primary')];buttons.forEach(b=>b.disabled=true);
    try{await action(e);}finally{buttons.forEach(b=>b.disabled=false);}
  }catch(error){notice(error.message);}});
}
function updateBadge(){const configured=state.model.configured;$('#modelBadge').textContent=configured?'Qwen · 已配置':'首次使用 · 请设置模型';$('#modelBadge').className='badge'+(configured?'':' warn');}
async function loadOverview(){state.overview=await api('overview');renderLessons();}
function renderLessons(){
  const nav=$('#lessons'),all=state.overview.lessons;
  const categories=[...new Set(all.map(l=>l.request.category||''))].sort((a,b)=>(a||'未分类').localeCompare(b||'未分类','zh-CN'));
  const values=categories.map(c=>c===''?'__none__':c);
  const filter=values.includes(state.categoryFilter)?state.categoryFilter:'';
  state.categoryFilter=filter;
  const filterHost=$('#categoryFilter');
  if(filterHost){
    filterHost.innerHTML=`<label>分类<select id="lessonCategoryFilter"><option value="">全部分类</option>${categories.map(c=>{const value=c===''?'__none__':c;return `<option value="${esc(value)}" ${value===filter?'selected':''}>${esc(c||'未分类')}</option>`;}).join('')}</select></label>`;
    $('#lessonCategoryFilter').onchange=event=>{state.categoryFilter=event.target.value;renderLessons();};
  }
  const shown=filter?all.filter(l=>(l.request.category||'')===(filter==='__none__'?'':filter)):all;
  if(!shown.length){nav.innerHTML=all.length?'<p class="muted">该分类下还没有课程。</p>':'<p class="muted">还没有课程，先创建一次备课。</p>';return;}
  const groups=new Map();
  for(const lesson of shown){const key=lesson.request.category||'';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(lesson);}
  nav.innerHTML=[...groups.entries()].map(([category,lessons])=>`${filter?'':`<div class="rail-group">${esc(category||'未分类')}<span>${lessons.length}</span></div>`}${lessons.map(l=>`<button class="lesson-item ${l.id===state.lesson?.id?'active':''}" data-lesson="${esc(l.id)}"><strong>${esc(l.request.title)}</strong><small>${esc(l.request.grade)} · ${l.request.course_type==='subject'?esc(l.request.subject):'主题课'} · ${l.versions.length} 个版本</small></button>`).join('')}`).join('');
  nav.querySelectorAll('[data-lesson]').forEach(b=>bindAction(b,'click',()=>selectLesson(b.dataset.lesson)));
}
async function selectLesson(id,versionId){clearPreviousFailureCards();const sequence=++state.renderSequence;$('#content').innerHTML='<div class="loading">正在读取课程…</div>';const lesson=state.overview.lessons.find(l=>l.id===id);if(!lesson)throw new Error('课程不存在，请刷新');state.lesson=lesson;state.feedback=null;state.tab='materials';let version=versionId||lesson.versions.at(-1)?.id;const record=version?await api(`entity?kind=version&id=${encodeURIComponent(version)}`):null;if(sequence!==state.renderSequence)return;state.version=record;if(record?.design?.knowledge_ids?.length&&!state.knowledge.length){try{const knowledgeData=await api('knowledge/list');state.knowledge=knowledgeData.entries||[];}catch(error){}}if(sequence!==state.renderSequence)return;localStorage.setItem('teacher-last-lesson',id);$('#pageTitle').textContent=lesson.request.title;renderLessons();await renderLesson();}
function newLesson(preferredClassId=state.lesson?.request.class_id||''){clearPreviousFailureCards();++state.renderSequence;state.lesson=null;state.version=null;state.sources=[];state.selectedKnowledge=new Set();state.knowledgeTouched=false;state.referencesLoaded=false;renderLessons();$('#pageTitle').textContent='让备课与学情连起来';$('#content').innerHTML=`<section class="card intro"><h2>输入本课需要，带着整套材料上课</h2><p>课件、教案、逐字稿和检测题来自同一份教学设计。课后记录教学成果与课堂特点，下一课结合学情因材施教。</p><div class="steps"><span>01 / 输入授课信息</span><span>02 / 查看材料与信息密度</span><span>03 / 记录成果，用于下一课</span></div></section><form id="createForm" class="card"><h2>这次准备讲什么</h2><div class="grid"><label>课程主题<input name="title" placeholder="例如：认识几分之一 / 面对同伴冲突" maxlength="80" required></label><label>分类（选填）<input name="category" list="categoryOptions" maxlength="40" placeholder="例如：三年级数学 / 班会"></label><label>课程类型<select name="course_type"><option value="subject">学科教学</option><option value="theme">班会 / 主题教育</option></select></label><label class="full">使用已有班级（选填）<select id="classSelect"><option value="">新建班级</option></select><input type="hidden" name="class_id" id="classId"><small>选择已有班级，后续备课会自动参考该班相关的课堂记录。</small></label><label>年级<input name="grade" placeholder="例如：小学三年级" maxlength="30" required></label><label>学科<input name="subject" placeholder="学科课必填；主题课可留空"></label><label>课时（分钟）<input name="duration_minutes" type="number" value="40" min="1" max="480" required></label><label>班级名称<input name="class_label" placeholder="例如：三年级一班" required></label><label>班级人数（选填）<input name="class_size" type="number" min="1" max="1000" placeholder="不知道可留空"></label><label>学生已学内容（选填）<textarea name="prior_knowledge" placeholder="只填写你已了解的情况"></textarea></label><label>已知学习困难（选填）<textarea name="difficulties" placeholder="例如：能读出分数，但对整体的理解不稳定"></textarea></label><label>授课要求 / 教材版本（选填）<textarea name="constraints" placeholder="每行一条：本课范围、教学目标、设备条件、需要保留的活动等"></textarea></label><label class="full">教材或参考资料（选填）<input id="sourceUpload" type="file" accept=".txt,.md,.docx,.pdf" multiple><small>支持文字型 PDF、DOCX、TXT、MD，单个不超过25MB；整册大文件请从左下角「知识库」导入。生成时本课资料会发送至你配置的 Qwen 模型。</small><span id="sources"></span></label><div class="full knowledge-field"><strong>知识库依据（选填）</strong><p class="muted">优先使用教材原文，减少模型自行延伸。系统会按学科/年级预选，并按本课主题检索相关段落。</p><div id="knowledgePicker"><p class="muted">正在读取知识库…</p></div></div></div><datalist id="categoryOptions">${[...new Set(state.overview.lessons.map(l=>l.request.category||'').filter(Boolean))].map(c=>`<option value="${esc(c)}">`).join('')}</datalist><div class="actions"><button type="submit" class="primary">创建课程并生成整套材料</button></div></form>`;
 bindAction($('#sourceUpload'),'change',async e=>{for(const file of e.target.files){if(file.size>25*1024*1024)throw new Error('单个资料不能超过25MB');const base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const source=await api('upload',{name:file.name,content_base64:base64});state.sources.push(source);$('#sources').innerHTML=state.sources.map(s=>`<span class="source-chip">${esc(s.original_name)} · ${s.characters} 字</span>`).join('');}e.target.value='';});
 bindAction($('#createForm'),'submit',async e=>{if(!state.model.configured){openSettings();throw new Error('请先在模型设置中保存 SiliconFlow API Key。');}await ensureMaterialSettings();if(!state.referencesLoaded)await loadCreateReferences();const form=new FormData(e.target);const payload=Object.fromEntries(form);if(payload.class_id==='')delete payload.class_id;payload.duration_minutes=Number(payload.duration_minutes);payload.class_size=payload.class_size?Number(payload.class_size):null;payload.prior_knowledge=payload.prior_knowledge||null;payload.difficulties=payload.difficulties||null;payload.constraints=lines(payload.constraints);payload.source_ids=state.sources.map(s=>s.source_id);payload.knowledge_ids=[...state.selectedKnowledge];const result=await api('create',{payload,key:uid()});await loadOverview();await selectLesson(result.lesson_id);await startJob('generate',result.lesson_id);});
 $('#createForm').dataset.preferredClassId=preferredClassId;
 void loadCreateReferences(preferredClassId);
}
async function loadCreateReferences(preferredClassId=''){
  const createForm=$('#createForm');
  try{
    const [classData,knowledgeData]=await Promise.all([api('classes'),api('knowledge/list')]);
    state.classes=classData.classes||[];state.knowledge=knowledgeData.entries||[];state.referencesLoaded=true;
    if(createForm!==$('#createForm'))return;
    preferredClassId=createForm?.dataset.preferredClassId??preferredClassId;
    const select=$('#classSelect');
    if(select){
      select.innerHTML='<option value="">新建班级</option>'+state.classes.map(item=>`<option value="${esc(item.id)}">${esc(item.class_label)} · ${esc(item.grade)} · ${item.lesson_count} 门课</option>`).join('');
      select.onchange=()=>{createForm.dataset.preferredClassId=select.value;applyClassSelection(select.value);};
      if(preferredClassId&&state.classes.some(item=>item.id===preferredClassId)){select.value=preferredClassId;applyClassSelection(preferredClassId);}
    }
    renderKnowledgePicker();
    const form=$('#createForm');
    if(form){form.subject.oninput=()=>{if(!state.knowledgeTouched)renderKnowledgePicker();};form.grade.oninput=()=>{if(!state.knowledgeTouched)renderKnowledgePicker();};}
  }catch(error){notice(error.message);}
}
function applyClassSelection(id){
  const form=$('#createForm');if(!form)return;
  const selected=state.classes.find(item=>item.id===id),classId=$('#classId');
  if(!selected){classId.value='';form.grade.readOnly=false;form.class_label.readOnly=false;return;}
  classId.value=selected.id;
  form.grade.value=selected.grade||'';form.class_label.value=selected.class_label||'';
  form.class_size.value=selected.class_size??'';form.prior_knowledge.value=selected.prior_knowledge||'';
  form.difficulties.value=selected.difficulties||'';
  form.grade.readOnly=true;form.class_label.readOnly=true;
  if(!state.knowledgeTouched)renderKnowledgePicker();
}
function matchingChapterScope(book,chapter){
  if(chapter.knowledge_id)return state.knowledge.find(entry=>entry.id===chapter.knowledge_id);
  const candidates=state.knowledge.filter(entry=>entry.storage_mode==='scope'&&entry.scope.root_id===book.id&&entry.scope.start_page===chapter.start_page&&entry.scope.end_page===chapter.end_page);
  return candidates.find(entry=>entry.title===chapter.title)||candidates[0];
}
function chapterChoices(book){
  const chapters=book.chapters||[];
  const custom=state.knowledge.filter(entry=>entry.scope?.root_id===book.id&&!chapters.some(chapter=>chapter.start_page===entry.scope.start_page&&chapter.end_page===entry.scope.end_page));
  return [...chapters,...custom.map(entry=>({id:entry.id,knowledge_id:entry.id,title:entry.title,start_page:entry.scope.start_page,end_page:entry.scope.end_page,chunk_count:entry.chunk_count}))];
}
async function ensureChapterScope(book,chapter){
  const existing=matchingChapterScope(book,chapter);if(existing)return existing.id;
  const scope=await api('knowledge/unit',{id:book.id,start_page:chapter.start_page,end_page:chapter.end_page,title:chapter.title,key:`chapter_${book.id}_${chapter.id}`});
  const data=await api('knowledge/list');state.knowledge=data.entries||[];return scope.knowledge_id;
}
function renderKnowledgePicker(){
  const host=$('#knowledgePicker'),form=$('#createForm');if(!host||!form)return;
  if(!state.knowledge.length){host.innerHTML='<p class="muted">还没有知识库。点击左下角「知识库」上传教材，系统会先读取目录再整理内容。</p>';return;}
  const books=state.knowledge.filter(entry=>entry.storage_mode!=='scope');
  if(!state.knowledgeTouched){
    const subject=form.subject.value.trim(),grade=form.grade.value.trim();
    const compatible=(value,target)=>!value||(!!target&&(value===target||value.includes(target)||target.includes(value)));
    state.selectedKnowledge=new Set(books.filter(entry=>compatible(entry.subject,subject)&&compatible(entry.grade,grade)).map(entry=>entry.id));
  }
  host.innerHTML=`<div class="knowledge-list structured-picker">${books.map(book=>`<div class="book-choice" data-book-choice="${esc(book.id)}"><div class="knowledge-picker-row"><label class="knowledge-item"><input type="checkbox" data-knowledge="${esc(book.id)}" ${state.selectedKnowledge.has(book.id)?'checked':''}><span><b>${esc(book.title)}</b><small>${esc(book.subject||'通用学科')} · ${esc(book.grade||'通用年级')} · ${book.chapter_count?`${book.chapter_count} 个章节 · `:''}${book.chunk_count} 个内容块</small></span></label><button type="button" data-knowledge-only="${esc(book.id)}">${book.chapter_count?'仅用整册':'仅使用此条'}</button></div>${chapterChoices(book).length?`<details ${state.knowledge.some(entry=>entry.scope?.root_id===book.id&&state.selectedKnowledge.has(entry.id))?'open':''}><summary>展开章节 · ${chapterChoices(book).length} 项</summary><div class="chapter-choices">${chapterChoices(book).map((chapter,index)=>{const scope=matchingChapterScope(book,chapter),checked=scope&&state.selectedKnowledge.has(scope.id);return `<div class="knowledge-picker-row"><label class="knowledge-item"><input type="checkbox" data-chapter-check="${index}" data-book="${esc(book.id)}" ${checked?'checked':''}><span><b>${esc(chapter.title)}</b><small>PDF 第 ${chapter.start_page}—${chapter.end_page} 页 · ${chapter.chunk_count} 个内容块</small></span></label><button type="button" data-chapter-only="${index}" data-book="${esc(book.id)}">仅用本章</button></div>`;}).join('')}</div></details>`:''}</div>`).join('')}</div><p class="muted">一本教材保存一份。选择章节后只检索该范围，整册不会同时勾选；跨章节复习可选择整册或多个章节。</p><button type="button" id="resetKnowledge" class="link-button">按当前学科/年级重选</button>`;
  host.querySelectorAll('[data-knowledge]').forEach(input=>input.onchange=()=>{
    state.knowledgeTouched=true;
    if(input.checked){for(const entry of state.knowledge)if(entry.scope?.root_id===input.dataset.knowledge)state.selectedKnowledge.delete(entry.id);state.selectedKnowledge.add(input.dataset.knowledge);}
    else state.selectedKnowledge.delete(input.dataset.knowledge);
    renderKnowledgePicker();
  });
  host.querySelectorAll('[data-knowledge-only]').forEach(button=>button.onclick=()=>{state.knowledgeTouched=true;state.selectedKnowledge=new Set([button.dataset.knowledgeOnly]);renderKnowledgePicker();});
  const changeChapter=async(element,only)=>{
    const book=state.knowledge.find(entry=>entry.id===element.dataset.book),index=Number(element.dataset.chapterOnly??element.dataset.chapterCheck),chapter=chapterChoices(book)[index];
    state.knowledgeTouched=true;
    if(!only&&!element.checked){const scope=matchingChapterScope(book,chapter);if(scope)state.selectedKnowledge.delete(scope.id);renderKnowledgePicker();return;}
    element.disabled=true;
    try{const id=await ensureChapterScope(book,chapter);if(only)state.selectedKnowledge=new Set([id]);else{state.selectedKnowledge.delete(book.id);state.selectedKnowledge.add(id);}renderKnowledgePicker();}
    catch(error){notice(error.message);element.disabled=false;}
  };
  host.querySelectorAll('[data-chapter-only]').forEach(button=>bindAction(button,'click',()=>changeChapter(button,true)));
  host.querySelectorAll('[data-chapter-check]').forEach(input=>input.onchange=()=>changeChapter(input,false));
  $('#resetKnowledge').onclick=()=>{state.knowledgeTouched=false;renderKnowledgePicker();};
}

async function refreshCreateKnowledge(){
  if(!$('#createForm'))return;
  const data=await api('knowledge/list');state.knowledge=data.entries||[];state.referencesLoaded=true;
  const valid=new Set(state.knowledge.map(entry=>entry.id));
  for(const id of [...state.selectedKnowledge])if(!valid.has(id))state.selectedKnowledge.delete(id);
  renderKnowledgePicker();
}
async function startJob(kind,target){const job=await api('start',{kind,target,key:uid()});if(kind==='revise')state.feedbackRevisionTask={id:job.id,lesson_id:state.lesson?.id};await refreshJobs();return job;}
function fileLink(name){return `/teacher/api/file?version=${encodeURIComponent(state.version.id)}&name=${encodeURIComponent(name)}`;}
async function renderLesson(){if(!state.lesson)return;const version=state.version;$('#content').innerHTML=`<div class="lesson-toolbar"><span class="muted">${esc(state.lesson.request.grade)} · ${esc(state.lesson.request.class_label)} · ${num(state.lesson.request.duration_minutes)} 分钟</span>${version?`<select id="versionSelect" aria-label="查看材料版本">${state.lesson.versions.map(v=>`<option value="${esc(v.id)}" ${v.id===version.id?'selected':''}>第 ${v.number} 版${v.status==='ready'?' · 材料已导出':' · 待导出'}</option>`).join('')}</select>`:''}<button id="classProfileBtn">班级学情</button><button id="editLesson">编辑课程</button><button id="deleteLesson" class="danger">删除</button></div></div>${version?`<nav class="tabs" aria-label="课程内容"><button data-tab="materials">教学材料</button><button data-tab="density">信息密度</button><button data-tab="feedback">课后记录</button><button data-tab="versions">材料版本</button></nav><div id="tabContent"></div>`:'<section class="card empty"><h2>课程信息已保存</h2><p>开始生成后，可以在这里查看课件、教案、逐字稿和检测题。</p><button class="primary" id="retryGenerate">生成整套材料</button></section>'}`;if($('#editLesson'))$('#editLesson').onclick=openLessonEdit;if($('#deleteLesson'))bindAction($('#deleteLesson'),'click',deleteLesson);if($('#classProfileBtn'))$('#classProfileBtn').onclick=openClassProfile;if(!version){const active=state.jobs.findLast(j=>j.target===state.lesson.id&&['queued','running'].includes(j.status));if(active){$('#retryGenerate').disabled=true;$('#retryGenerate').textContent='正在生成整套材料…';$('.empty h2').textContent='正在生成整套材料';}else bindAction($('#retryGenerate'),'click',()=>startJob('generate',state.lesson.id));return;}$('#versionSelect').addEventListener('change',e=>selectLesson(state.lesson.id,e.target.value));document.querySelectorAll('[data-tab]').forEach(b=>bindAction(b,'click',async()=>{state.tab=b.dataset.tab;await renderTab();}));await renderTab();}
function openLessonEdit(){
  const lesson=state.lesson;if(!lesson)return;
  const active=state.jobs.find(j=>['queued','running'].includes(j.status)&&(j.target===lesson.id||lesson.versions.some(v=>v.id===j.version_id)));
  if(active){notice('该课程正在生成，请完成后再修改课程信息。');return;}
  const request=lesson.request,full=!lesson.versions.length;
  const categories=[...new Set(state.overview.lessons.map(item=>item.request.category||'').filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  const dialog=$('#lessonDialog');
  dialog.innerHTML=`<form id="lessonForm"><div class="dialog-head"><h2>编辑课程</h2><button type="button" id="closeLessonDialog" class="icon" aria-label="关闭编辑">×</button></div><p class="muted">${full?'本课程尚无教学版本，可以修改全部备课信息。':'已有教学版本，只能修改课程名称、分类和班级名称；年级、学科、课时等会影响教学设计，请通过学情修订生成新版本。课程改名不会改写已生成材料中的课题。'}</p><label>课程主题<input name="title" value="${esc(request.title)}" maxlength="80" required></label><label>分类（选填）<input name="category" list="lessonCategoryOptions" maxlength="40" value="${esc(request.category||'')}" placeholder="例如：三年级数学 / 班会"></label><datalist id="lessonCategoryOptions">${categories.map(c=>`<option value="${esc(c)}">`).join('')}</datalist><label>班级名称<input name="class_label" value="${esc(request.class_label)}" required></label>${full?`<div class="grid"><label>课程类型<select name="course_type"><option value="subject" ${request.course_type==='subject'?'selected':''}>学科教学</option><option value="theme" ${request.course_type==='theme'?'selected':''}>班会 / 主题教育</option></select></label><label>年级<input name="grade" value="${esc(request.grade)}" maxlength="30" required></label><label>学科<input name="subject" value="${esc(request.subject||'')}" placeholder="学科课必填；主题课可留空"></label><label>课时（分钟）<input name="duration_minutes" type="number" value="${num(request.duration_minutes)}" min="1" max="480" required></label><label>班级人数（选填）<input name="class_size" type="number" min="1" max="1000" value="${request.class_size??''}"></label></div><label>学生已学内容<textarea name="prior_knowledge">${esc(request.prior_knowledge||'')}</textarea></label><label>已知学习困难<textarea name="difficulties">${esc(request.difficulties||'')}</textarea></label><label>授课要求 / 教材版本（每行一条）<textarea name="constraints">${esc((request.constraints||[]).join('\n'))}</textarea></label>`:''}<div class="actions"><button type="button" id="cancelLessonEdit">取消</button><button class="primary" type="submit">保存修改</button></div></form>`;
  $('#closeLessonDialog').onclick=()=>dialog.close();$('#cancelLessonEdit').onclick=()=>dialog.close();
  bindAction($('#lessonForm'),'submit',async event=>{
    const form=event.target,data=Object.fromEntries(new FormData(form));
    data.category=String(data.category||'').trim();
    if(full){data.duration_minutes=Number(data.duration_minutes);data.class_size=data.class_size?Number(data.class_size):null;data.prior_knowledge=data.prior_knowledge||null;data.difficulties=data.difficulties||null;data.constraints=lines(data.constraints);}
    await api('lesson/update',{id:lesson.id,payload:data,key:uid()});
    dialog.close();await loadOverview();await selectLesson(lesson.id,state.version?.id);notice('课程信息已更新。');
  });
  dialog.showModal();
}
async function deleteLesson(){
  const lesson=state.lesson;if(!lesson)return;
  if(!confirm(`删除「${lesson.request.title}」及其所有版本、教学材料和任务记录？此操作不可撤销。`))return;
  await api('lesson/delete',{id:lesson.id,key:uid()});
  localStorage.removeItem('teacher-last-lesson');
  state.lesson=null;state.version=null;state.feedback=null;
  await loadOverview();
  const next=state.overview.lessons[0];
  if(next)await selectLesson(next.id);else newLesson();
  notice('课程已删除。');
}
async function openAssetComposer(){
  if(!state.version)return;
  const version=state.version,dialog=$('#assetComposerDialog');
  dialog.innerHTML='<p class="loading">正在读取页面和教材素材…</p>';dialog.showModal();
  try{
    const data=await api('ppt/pages?version='+encodeURIComponent(version.id));
    if(!data.assets?.length){
      dialog.innerHTML='<p>本课知识库还没有拆出教材素材。请先在知识库「查看素材」或点「提取素材」。</p><div class="actions"><button id="closeAssetComposer">关闭</button></div>';
      $('#closeAssetComposer').onclick=()=>dialog.close();return;
    }
    const kindLabel={scene:'情景导入',diagram:'图表/示意',question:'教材留空题'};
    const option=asset=>`<option value="${esc(asset.id)}">第${asset.page||'—'}页 · ${esc(kindLabel[asset.kind]||'素材')} · ${esc((asset.question_text||asset.description||asset.caption||'').slice(0,80))}</option>`;
    const optionGroups=['scene','diagram','question'].map(kind=>{
      const items=data.assets.filter(asset=>(asset.kind||'scene')===kind);
      return items.length?`<optgroup label="${kindLabel[kind]}">${items.map(option).join('')}</optgroup>`:'';
    }).join('')+(()=>{const other=data.assets.filter(asset=>!['scene','diagram','question'].includes(asset.kind||'scene'));return other.length?`<optgroup label="其他">${other.map(option).join('')}</optgroup>`:'';})();
    dialog.innerHTML=`<form id="assetComposerForm"><div class="dialog-head"><h2>按页选择教材素材</h2><button type="button" id="closeAssetComposer" class="icon" aria-label="关闭素材选择">×</button></div><p class="muted">每页最多2张。情景图含画面和对话框，教材留空题是原题区域截图；保存后会重新制作本版教材图文课件，不调用外部PPT服务。素材图注可以在知识库中修改。</p><div class="asset-pages">${data.pages.map(page=>`<div class="asset-page" data-page="${page.number}"><h3>第${page.number}页 · ${esc(page.title)}</h3><p class="muted">${esc(page.points.join('；').slice(0,180))}</p><div class="asset-selects"><select data-slot="0"><option value="">不使用素材</option>${optionGroups}</select><select data-slot="1"><option value="">不使用素材</option>${optionGroups}</select><div class="asset-previews"></div></div></div>`).join('')}</div><div class="actions"><button type="button" id="cancelAssetComposer">取消</button><button type="submit" class="primary">按所选素材生成课件</button></div></form>`;
    $('#closeAssetComposer').onclick=()=>dialog.close();$('#cancelAssetComposer').onclick=()=>dialog.close();
    const updatePreviews=row=>{
      const selected=[...row.querySelectorAll('select')].map(select=>select.value).filter(Boolean);
      row.querySelector('.asset-previews').innerHTML=selected.map(id=>{
        const asset=data.assets.find(item=>item.id===id);
        return asset?`<img src="/teacher/api/knowledge/asset?knowledge=${encodeURIComponent(asset.knowledge_id)}&name=${encodeURIComponent(asset.file)}" alt="${esc(asset.caption||'')}">`:'';
      }).join('');
    };
    dialog.querySelectorAll('.asset-page').forEach(row=>{
      row.querySelectorAll('select').forEach(select=>select.onchange=()=>updatePreviews(row));
      updatePreviews(row);
    });
    bindAction($('#assetComposerForm'),'submit',async()=>{
      const mapping={};
      dialog.querySelectorAll('.asset-page').forEach(row=>{
        const ids=[...row.querySelectorAll('select')].map(select=>select.value).filter(Boolean);
        if(ids.length)mapping[row.dataset.page]=[...new Set(ids)].slice(0,2);
      });
      if(!Object.keys(mapping).length)throw new Error('请至少为某一页选择一张教材素材。');
      const panel=$('#materialsDelivery');if(panel)panel.dataset.deliveryState='generating';
      await api('start',{kind:'local_ppt',target:version.id,key:uid(),assets_by_page:mapping});
      dialog.close();await refreshJobs();notice('正在按所选教材素材生成课件，完成后材料会更新。');
    });
  }catch(error){
    dialog.innerHTML='<p class="danger">'+esc(error.message)+'</p><div class="actions"><button id="closeAssetComposer">关闭</button></div>';
    $('#closeAssetComposer').onclick=()=>dialog.close();
  }
}
async function openClassProfile(){
  const lesson=state.lesson;if(!lesson)return;
  const classId=lesson.request.class_id;
  if(!classId){notice('这门课程没有关联班级。');return;}
  const dialog=$('#classDialog');dialog.innerHTML='<p class="loading">正在读取班级学情…</p>';dialog.showModal();
  try{
    const profile=await api('class-profile?id='+encodeURIComponent(classId)),b=profile.baseline;
    dialog.innerHTML=`<div class="dialog-head"><h2>班级学情 · ${esc(b.class_label)}</h2><button type="button" id="closeClassDialog" class="icon" aria-label="关闭学情档案">×</button></div>
      <p class="muted">${profile.feedback_count} 条有效记录 · ${profile.lessons.length} 门课。新课按相关性参考，单次表现不作为固定能力标签。</p>
      <section class="profile-evidence"><h3>课堂特点与学习线索</h3>${renderLearningFeatures(profile.characteristics)}</section>
      <details><summary>班级基本情况（可修改）</summary><form id="classForm"><div class="grid"><label>班级名称<input name="class_label" value="${esc(b.class_label)}" required></label><label>年级<input name="grade" value="${esc(b.grade)}" required></label><label>班级人数（选填）<input name="class_size" type="number" min="1" max="1000" value="${b.class_size??''}"></label></div><label>已知学生情况<textarea name="prior_knowledge" placeholder="只填写已核实的情况">${esc(b.prior_knowledge||'')}</textarea></label><label>已知学习困难<textarea name="difficulties">${esc(b.difficulties||'')}</textarea></label><div class="actions"><button class="primary" type="submit">保存班级信息</button></div></form></details>
      <details><summary>查看原始课堂记录</summary>${profile.evidence.length?profile.evidence.map(item=>`<div class="evidence-item"><strong>${esc(item.lesson_title)}</strong><small>授课/观察 ${esc(item.observed_at)}</small><p>${esc(item.raw_text)}</p>${item.next_lesson_focus?`<p>下次关注：${esc(item.next_lesson_focus)}</p>`:''}${item.assessments.length?`<small>检测：${item.assessments.map(result=>`${esc(result.question_prompt||result.question_id)} ${result.correct??result.met_criteria??'未知'}/${result.total??'未知'}（${esc(timing[result.timing]||result.timing)} · ${esc(basis[result.basis]||result.basis)} · ${result.independent===true?'独立完成':result.independent===false?'非独立完成':'独立条件未确认'}）`).join('；')}</small>`:''}</div>`).join(''):'<p class="muted">授课后在「课后记录」保存学习表现，后续同班备课会参考。</p>'}</details>`;
    $('#closeClassDialog').onclick=()=>dialog.close();
    bindAction($('#classForm'),'submit',async event=>{
      const data=Object.fromEntries(new FormData(event.target));data.class_size=data.class_size?Number(data.class_size):null;
      data.prior_knowledge=data.prior_knowledge||null;data.difficulties=data.difficulties||null;
      await api('class/update',{id:classId,payload:data,key:uid()});dialog.close();await loadOverview();notice('班级信息已保存，后续同班备课会参考。');
    });
  }catch(error){dialog.innerHTML='<p class="danger">'+esc(error.message)+'</p><div class="actions"><button id="closeClassError">关闭</button></div>';$('#closeClassError').onclick=()=>dialog.close();}
}
async function renderTab(){document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));const element=$('#tabContent');if(!element)return;if(state.tab==='materials')await renderMaterials(element);if(state.tab==='density'){const versionId=state.version.id;element.innerHTML='<div class="loading">正在整理信息密度…</div>';const analysis=await api('density?id='+encodeURIComponent(versionId));if(state.version?.id===versionId&&state.tab==='density')renderDensity(element,analysis);}if(state.tab==='feedback')await renderFeedback(element);if(state.tab==='versions')await renderVersions(element);}
async function renderMaterials(element){
  const v=state.version,d=v.design;
  element.innerHTML=`<div class="stat-grid"><div class="stat"><small>教学目标</small><b>${d.goals.length}</b>个可检测目标</div><div class="stat"><small>教学环节</small><b>${d.activities.length}</b>共 ${num(d.duration_minutes)} 分钟</div><div class="stat"><small>课件内容页</small><b>${d.slides.length}</b>按课堂环节编排</div><div class="stat"><small>检测任务</small><b>${d.questions.length}</b>关联教学目标</div></div><section class="card" id="materialsCard"><h2>本课整套材料 · 第 ${v.number} 版</h2><div id="materialsDelivery" aria-live="polite"><p class="muted">正在读取教学材料…</p></div><div class="note">${esc(d.context_notes||'请结合实际教材和本班情况核对。')}</div>${d.knowledge_ids?.length?`<div class="note">知识库依据：${d.knowledge_ids.map(id=>esc(state.knowledge.find(entry=>entry.id===id)?.title||id)).join('、')}。生成时只提供相关段落，教材事实仍请核对。</div>`:''}${v.status==='ready'?'<div class="actions" style="justify-content:flex-start"><button id="openAssetComposer">按页选择教材素材</button></div>':''}<h3>教学目标</h3><ul>${d.goals.map(g=>`<li>${esc(g.description)}</li>`).join('')}</ul></section><section class="card"><h2>课程展开</h2>${d.activities.map(a=>`<details><summary>${esc(a.title)} · ${num(a.duration_minutes)} 分钟 · ${kinds[a.kind]}</summary><p>${esc(a.instructions)}</p>${d.slides.filter(s=>s.activity_id===a.id).map(s=>`<h3>${esc(s.title)}</h3><ul>${s.points.map(p=>`<li>${esc(p)}</li>`).join('')}</ul><p class="script">${esc(s.teacher_script)}</p>`).join('')}</details>`).join('')}</section>`;
  if($('#openAssetComposer'))$('#openAssetComposer').onclick=openAssetComposer;
  await renderMaterialDelivery($('#materialsDelivery'),v);
}
function renderDensity(element,a){const d=state.version.design;const total=d.knowledge_points.filter(k=>k.is_new).length;const thinking=d.activities.reduce((n,v)=>n+v.thinking_minutes,0);const max=Math.max(.01,...a.density.map(v=>v.planned_points_per_activity_minute||0));element.innerHTML=`<div class="stat-grid"><div class="stat"><small>本课首次引入知识点</small><b>${total}</b>需核对知识点划分</div><div class="stat"><small>思考与练习计划时间</small><b>${num(thinking)}</b>分钟 / 全课 ${num(d.duration_minutes)} 分钟</div><div class="stat"><small>有实际用时记录的环节</small><b>${a.density.filter(r=>r.actual_minutes!=null).length} / ${a.density.length}</b>在学情页补充</div><div class="stat"><small>课堂质量总分</small><b>—</b>不从单一指标推算</div></div><section class="card"><h2>信息密度如何分布</h2><p class="muted">计划密度 = 本环节首次引入知识点数 ÷ 整个环节计划分钟。实际密度使用教师记录的实际分钟，仍按设计中的知识点计数。</p><div class="density-chart">${a.density.map(r=>`<div class="bar-col" title="${esc(r.title)}：${num(r.planned_points_per_activity_minute)} 点/分钟"><b>${num(r.planned_points_per_activity_minute)}</b><div class="bar" style="height:${Math.round(105*(r.planned_points_per_activity_minute||0)/max)+2}px"></div><span>${esc(r.title)}</span></div>`).join('')}</div><div class="note">0 个新知识点的练习、讨论仍有学习价值。密度不等于认知负荷；需结合难度、推理步骤和学生学习证据解释。目前没有为本课程验证的“最优密度阈值”。</div><div class="table-wrap"><table><thead><tr><th>环节 / 类型</th><th>首次引入</th><th>计划 / 实际分钟</th><th>计划 / 实际点每分钟</th><th>思考练习 / 推理</th></tr></thead><tbody>${a.density.map(r=>`<tr><td>${esc(r.title)}<small>${kinds[r.kind]}</small></td><td>${r.new_knowledge_count}<small>${r.introduced_knowledge_ids.map(id=>esc(d.knowledge_points.find(k=>k.id===id)?.title||id)).join('、')||'无新增知识点'}</small></td><td>${num(r.planned_minutes)} / ${num(r.actual_minutes)}<small>${r.actual_time_basis?basis[r.actual_time_basis]:'尚无实际用时记录'}</small></td><td>${num(r.planned_points_per_activity_minute)} / ${num(r.actual_points_per_activity_minute)}</td><td>${num(r.planned_thinking_minutes)} 分钟 / ${r.reasoning_steps} 步<small>难度：${Object.values(r.difficulties).map(v=>({basic:'基础',intermediate:'中等',complex:'复杂'}[v])).join('、')||'未涉及知识点'}</small></td></tr>`).join('')}</tbody></table></div></section><section class="card"><h2>用学情检验下一次调整</h2><p>上课后，在「课后记录」写下本节课表现和下次教学关注。系统归档到本班学情，下一课按目标参考；实际用时、参与人数与检测结果有记录再补充。</p><button id="goFeedback">录入本课学情 →</button></section>`;$('#goFeedback').onclick=async()=>{state.tab='feedback';await renderTab();};}
// Feedback form and review flow are defined in feedback.js.
async function renderVersions(element){const v=state.version;element.innerHTML=`<section class="card"><h2>第 ${v.number} 版 · ${esc(v.summary)}</h2><p class="note">这里保留已生成的材料版本。课后记录单独归档到班级学情，不增加材料版本；后续新课在新课程中生成。已有历史修订仍可查看。</p><p class="muted">${esc(v.created)} · ${v.parent_id?'从已有版本修订':'首次生成'} · ${v.changes.length} 项实际变化</p>${v.changes.length?v.changes.map(c=>`<details><summary>${esc(c.target)} · ${esc(c.explanation||'内容调整')}</summary><p>依据：${(c.evidence_summary||[]).map(esc).join('；')||'请查看关联的教师选择'}</p><div class="diff"><div><small>修改前</small><pre>${json(c.before)}</pre></div><div><small>修改后</small><pre>${json(c.after)}</pre></div></div><p>下次验证：${(c.verify_next_time||[]).map(esc).join('；')}</p></details>`).join(''):'<p>本版是首次生成的完整教学材料。</p>'}</section><div id="densityDiff"></div>`;if(v.parent_id){const parent=await api('entity?kind=version&id='+encodeURIComponent(v.parent_id));const density=d=>d.activities.map(a=>({id:a.id,title:a.title,newPoints:a.introduces.length,minutes:a.duration_minutes,ratio:a.introduces.length/a.duration_minutes,thinking:a.thinking_minutes}));const before=density(parent.design),after=density(v.design);if(state.version?.id!==v.id||state.tab!=='versions')return;$('#densityDiff').innerHTML=`<section class="card"><h2>信息密度随版本的变化</h2><p class="muted">基于保留的环节标识对照。材料内容变了，密度数值也可能保持相同，变化本身不代表效果已提升。</p><div class="table-wrap"><table><thead><tr><th>环节</th><th>计划密度 修改前 → 修改后</th><th>用时 修改前 → 修改后</th><th>思考练习 修改前 → 修改后</th></tr></thead><tbody>${[...new Set([...before,...after].map(r=>r.id))].map(id=>{const b=before.find(r=>r.id===id),a=after.find(r=>r.id===id);return `<tr><td>${esc(a?.title||b.title)}</td><td>${b?num(b.ratio):'新增'} → ${a?num(a.ratio):'删除'}</td><td>${b?num(b.minutes):'—'} → ${a?num(a.minutes):'—'} 分钟</td><td>${b?num(b.thinking):'—'} → ${a?num(a.thinking):'—'} 分钟</td></tr>`;}).join('')}</tbody></table></div></section>`;}}
function teacherStage(job){
  if(job.status==='completed')return '整套教学材料已完成';
  if(['failed','interrupted','cancelled'].includes(job.status))return '整套材料尚未完成';
  return {design:'正在设计本课教学内容',documents:'正在编写教案、讲稿和检测题',presentation:'正在制作课件版式与配图',validation:'正在校对课件、讲稿并打包'}[job.phase]||'正在生成整套教学材料';
}
function clearPreviousFailureCards(){document.querySelectorAll('#tasks .task.failed,#tasks .task.interrupted,#tasks .task.cancelled').forEach(element=>element.remove());}
function revisionResultHint(job){const count=job.optimization?.reused_pages;return Number.isInteger(count)&&count>0?`优化版已保存，复用 ${count} 页视觉方案。`:'优化版材料已保存，修改依据已自动记录。';}
async function refreshJobs(){
  const previous=state.jobs;state.jobs=await api('jobs');
  const changed=state.jobs.filter(j=>j.version_id&&['completed','failed','interrupted','cancelled'].includes(j.status)&&previous.find(p=>p.id===j.id)?.status!==j.status);
  const visible=state.jobs.filter(j=>['queued','running'].includes(j.status)&&(!state.version||state.tab!=='materials'||j.version_id!==state.version.id));
  const revision=state.jobs.find(j=>j.id===state.feedbackRevisionTask?.id);
  if(revision&&state.lesson?.id===state.feedbackRevisionTask.lesson_id&&['completed','failed','interrupted','cancelled'].includes(revision.status)&&state.version?.id!==revision.version_id)visible.push(revision);
  if(!state.version&&state.lesson&&!state.lesson.versions.length){const failed=state.jobs.findLast(j=>j.target===state.lesson.id&&['failed','interrupted'].includes(j.status));if(!visible.length&&failed)visible.push(failed);}
  $('#tasks').innerHTML=visible.map(j=>`<div class="task ${esc(j.status)}"><div><strong>${esc(teacherStage(j))}</strong><small>${['queued','running'].includes(j.status)?'正在完成整套材料，可在此页等待。':j.status==='completed'?esc(revisionResultHint(j)):esc(j.error||'课程内容已保存，可继续生成。')}</small></div>${j.status==='completed'?`<button data-open-revision="${esc(j.version_id)}">查看优化版材料</button><button data-open-revision-changes="${esc(j.version_id)}">查看本次变化</button>`:j.status==='running'?`<button data-cancel="${esc(j.id)}">停止生成</button>`:['insufficient_balance','authentication','permission','model_unavailable','request_configuration'].includes(j.failure_code)?'<button data-task-model-settings>模型设置</button>':''}</div>`).join('');
  document.querySelectorAll('[data-open-revision],[data-open-revision-changes]').forEach(button=>bindAction(button,'click',async()=>{const showChanges=!!button.dataset.openRevisionChanges;await selectLesson(state.feedbackRevisionTask.lesson_id,button.dataset.openRevision||button.dataset.openRevisionChanges);if(showChanges){state.tab='versions';await renderTab();}await refreshJobs();}));
  document.querySelectorAll('[data-task-model-settings]').forEach(button=>button.onclick=openSettings);
  document.querySelectorAll('[data-cancel]').forEach(b=>bindAction(b,'click',()=>api('cancel',{id:b.dataset.cancel})));
  if(changed.length){
    await loadOverview();
    if(state.lesson){
      state.lesson=state.overview.lessons.find(l=>l.id===state.lesson.id)||state.lesson;
      const completed=changed.filter(j=>state.lesson.versions.some(v=>v.id===j.version_id)&&(j.status==='completed'||!state.version)).at(-1);
      if(completed&&(state.tab==='materials'||!state.version)){await selectLesson(state.lesson.id,completed.version_id);return;}
    }
  }
  if(state.lesson&&!state.version)await renderLesson();
  else if(state.tab==='materials'&&$('#materialsDelivery')?.dataset.deliveryState==='generating')await renderMaterialDelivery($('#materialsDelivery'),state.version);
}
async function openSettings(){try{$('#settingsForm [name="model"]').value=state.model.model||'Qwen/Qwen3-30B-A3B-Instruct-2507';await loadTuningSettings();const {policy}=await api('api-policy');$('#apiConcurrency').value=policy.max_concurrent;$('#apiInterval').value=policy.min_interval_ms;$('#apiCooldown').value=policy.rate_limit_cooldown_ms/1000;$('#settingsDialog').showModal();}catch(error){notice(error.message);}}
$('#newLesson').onclick=()=>{notice();newLesson();};$('#refresh').onclick=async()=>{try{await loadOverview();if(state.lesson)await selectLesson(state.lesson.id,state.version?.id);}catch(e){notice(e.message);}};$('#settingsBtn').onclick=openSettings;$('#closeSettings').onclick=()=>$('#settingsDialog').close();
bindAction($('#settingsForm'),'submit',async e=>{const data=Object.fromEntries(new FormData(e.target));state.model=await api('settings',data);await api('api-policy',{max_concurrent:Number($('#apiConcurrency').value),min_interval_ms:Number($('#apiInterval').value),rate_limit_cooldown_ms:Math.round(Number($('#apiCooldown').value)*1000)});await saveTuningSettings();e.target.api_key.value='';updateBadge();$('#connectionResult').textContent='设置已保存，密钥不会回显。';});
bindAction($('#testConnection'),'click',async()=>{const form=$('#settingsForm');if(form.api_key.value)throw new Error('请先保存设置，再检查连接。');$('#connectionResult').textContent='正在验证接口密钥与所选模型…';try{const result=await api('test-connection',{});$('#connectionResult').textContent='密钥与模型可见性检查通过：'+result.selected_model+'。未创建生成任务；工具调用能力和可用额度仍以实际生成为准。';}catch(e){$('#connectionResult').textContent=e.message;}});
(async()=>{try{const boot=await api('bootstrap');state.token=boot.token;state.model=boot.model;state.jobs=boot.jobs;updateBadge();await loadOverview();const last=localStorage.getItem('teacher-last-lesson');if(last&&state.overview.lessons.some(l=>l.id===last))await selectLesson(last);else if(state.overview.lessons.length)await selectLesson(state.overview.lessons[0].id);else newLesson();await refreshJobs();setInterval(()=>refreshJobs().catch(e=>notice('本机服务连接暂时中断：'+e.message)),4000);}catch(error){notice(error.message);$('#content').innerHTML='<div class="empty">工作台尚未就绪，请重新打开启动入口。</div>';}})();
