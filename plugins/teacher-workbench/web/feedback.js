'use strict';
// Session-only drafts: switching tabs must not erase a teacher's unfinished input.
const feedbackDrafts = new Map();
const feedbackOptions = (values, selected) => Object.entries(values).map(([value, label]) =>
  `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`).join('');
const feedbackValue = (row, name) => row.querySelector(`[name="${name}"]`).value;
const feedbackNumber = (row, name) => feedbackValue(row, name) === '' ? null : Number(feedbackValue(row, name));
function feedbackToday() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
function readFeedbackForm(form) {
  const payload = {raw_text:feedbackValue(form,'raw_text'), next_lesson_focus:feedbackValue(form,'next_lesson_focus'),
    requested_changes:form.dataset.legacyRequestedChanges||'',record_mode:'future_teaching',
    observed_at:feedbackValue(form,'observed_at'), teacher_keep:lines(feedbackValue(form,'teacher_keep')),
    participation:[], timings:[], observations:[], assessments:[]};
  if(form.dataset.supersedesFeedbackId)payload.supersedes_feedback_id=form.dataset.supersedesFeedbackId;
  if(form.dataset.originalObservedAt?.slice(0,10)===payload.observed_at)payload.observed_at=form.dataset.originalObservedAt;
  const retained=JSON.parse(form.dataset.retainedRecords||'{"assessments":[],"observations":[]}');
  const observationIds=new Set([...form.querySelectorAll('[data-observation-id]')].map(row=>row.dataset.observationId).filter(Boolean).concat(retained.observations.map(o=>o.id)));
  form.querySelectorAll('[data-activity]').forEach((row, index) => {
    const activity_id=row.dataset.activity, basis=feedbackValue(row,'basis'), actual_minutes=feedbackNumber(row,'minutes');
    // Former records can have different bases for time, participation and observation.
    // Retain those distinctions unless the teacher explicitly changes the selector.
    const original=JSON.parse(row.dataset.originalBases||'{}');
    const recordBasis=kind=>basis===row.dataset.initialBasis?(original[kind]||basis):basis;
    const participating=feedbackNumber(row,'participating'), eligible=feedbackNumber(row,'eligible'), text=feedbackValue(row,'observation').trim();
    if(actual_minutes !== null)payload.timings.push({activity_id,actual_minutes,basis:recordBasis('timing')});
    if(participating !== null || eligible !== null)payload.participation.push({activity_id,participating,eligible,basis:recordBasis('participation'),notes:row.dataset.participationNotes||''});
    if(text){
      let id=row.dataset.observationId;
      if(!id){let sequence=index+1;while(observationIds.has('O'+sequence))sequence++;id='O'+sequence;observationIds.add(id);}
      payload.observations.push({id,activity_id,text,basis:recordBasis('observation')});
    }
  });
  form.querySelectorAll('[data-question]').forEach(row => {
    const count=feedbackNumber(row,'count'), total=feedbackNumber(row,'total'), errors=lines(feedbackValue(row,'errors'));
    if(count !== null || total !== null || errors.length){
      const independent=feedbackValue(row,'independent');
      payload.assessments.push({question_id:row.dataset.question,[row.dataset.mode==='rubric'?'met_criteria':'correct']:count,
        total,errors,independent:independent===''?null:independent==='true',timing:feedbackValue(row,'timing'),
        days_after:feedbackNumber(row,'days_after'),basis:feedbackValue(row,'basis')});
    }
  });
  payload.assessments.push(...retained.assessments);payload.observations.push(...retained.observations);
  return payload;
}
async function renderFeedback(element) {
  const versionId=state.version.id, records=state.overview.feedback.filter(f=>f.version_id===versionId&&!f.superseded);
  if(state.feedback?.version_id!==versionId)state.feedback=null;
  element.innerHTML=`<section class="card feedback-card"><h2>课后记录</h2>
    <p class="muted">记录本节课表现，供后续同班备课参考。</p>
    ${records.length?`<label>查看本版已有反馈<select id="feedbackSelect"><option value="">填写一条新反馈</option>${records.map(f=>`<option value="${esc(f.id)}" ${state.feedback?.id===f.id?'selected':''}>${esc(f.created)} · ${esc(f.id.slice(-8))}</option>`).join('')}</select></label>`:''}
    <div id="feedbackBody"></div></section>`;
  if(records.length)$('#feedbackSelect').onchange=async event=>{
    try {
      const record=event.target.value?await api('entity?kind=feedback&id='+encodeURIComponent(event.target.value)):null;
      if(state.tab!=='feedback'||state.version?.id!==versionId)return;
      state.feedback=record;await renderFeedbackBody();
    }catch(error){notice(error.message);}
  };
  await renderFeedbackBody();
}
async function renderFeedbackBody() {
  const container=$('#feedbackBody');if(!container)return;
  if(state.feedback){await renderAnalysis(container);return;}
  const version=state.version, d=version.design, draft=feedbackDrafts.get(version.id)||{};
  const observedDate=/^\d{4}-\d{2}-\d{2}/.exec(draft.observed_at||'')?.[0]||feedbackToday();
  const retained={assessments:(draft.assessments||[]).filter((r,i,all)=>all.findIndex(x=>x.question_id===r.question_id)!==i),
    observations:(draft.observations||[]).filter((r,i,all)=>all.findIndex(x=>x.activity_id===r.activity_id)!==i)};
  const value=(text)=>esc(text??'');
  container.innerHTML=`<form id="feedbackForm">
    <div class="feedback-main-fields">
      <label>本节课表现<textarea name="raw_text" maxlength="12000" required placeholder="例如：体验活动参与很好，但部分学生独立做题时仍混淆分和秒。">${value(draft.raw_text)}</textarea></label>
      <label>下次教学关注（选填）<textarea name="next_lesson_focus" maxlength="12000" placeholder="例如：新课开始先用一道辨析题检查，再根据作答提供分步提示。">${value(draft.next_lesson_focus)}</textarea></label>
    </div>
    <details id="feedbackDate"><summary>日期：${value(observedDate)}</summary>
      <label>授课 / 观察日期<input name="observed_at" type="date" value="${value(observedDate)}" required></label></details>
    <details id="feedbackAdvanced" ${draft.advanced_open?'open':''}><summary>补充数据（选填）</summary>
      <label>有效做法或保留要求（选填）<textarea name="teacher_keep" placeholder="例如：实物操作帮助学生理解。">${value((draft.teacher_keep||[]).join('\n'))}</textarea></label>
      <p class="muted">有记录再填；空白代表未记录。填写 0 代表已观察为零，发言次数不等于参与人数。</p>
      ${retained.assessments.length||retained.observations.length?`<p class="note">本表展示每题首次检测和每个环节首条观察，另外 ${retained.assessments.length} 次检测、${retained.observations.length} 条观察会随本次输入完整保留。</p>`:''}
      <h3>课堂环节记录</h3>
      ${d.activities.map(a=>{
        const t=draft.timings?.find(x=>x.activity_id===a.id),p=draft.participation?.find(x=>x.activity_id===a.id),o=draft.observations?.find(x=>x.activity_id===a.id);
        return `<div class="feedback-activity" data-activity="${esc(a.id)}" data-initial-basis="${esc(t?.basis||p?.basis||o?.basis||'observed')}" data-original-bases="${esc(JSON.stringify({timing:t?.basis,participation:p?.basis,observation:o?.basis}))}" data-participation-notes="${esc(p?.notes||'')}" data-observation-id="${esc(o?.id||'')}"><h3>${esc(a.title)} <small>计划 ${num(a.duration_minutes)} 分钟</small></h3>
          <div class="compact-grid"><label>实际用时 / 分钟<input name="minutes" type="number" min="0.1" max="480" step="0.1" value="${value(t?.actual_minutes)}"></label>
          <label>去重参与人数<input name="participating" type="number" min="0" value="${value(p?.participating)}"></label>
          <label>本环节应参与人数<input name="eligible" type="number" min="1" placeholder="不自动推断" value="${value(p?.eligible)}"></label>
          <label>记录依据<select name="basis">${feedbackOptions(basis,t?.basis||p?.basis||o?.basis||'observed')}</select></label></div>
          <label>具体观察<textarea name="observation">${value(o?.text)}</textarea></label></div>`;
      }).join('')}
      <h3>目标对应的学习证据</h3>
      ${d.questions.map(q=>{
        const r=draft.assessments?.find(x=>x.question_id===q.id);
        return `<div class="feedback-question" data-question="${esc(q.id)}" data-mode="${esc(q.mode)}"><p><b>${esc(q.id)}</b> · ${esc(q.prompt)}</p>
          ${q.mode==='rubric'?`<small>评价要点：${q.criteria.map(esc).join('；')}</small>`:''}
          <div class="compact-grid"><label>${q.mode==='rubric'?'达到要点人数':'正确人数'}<input name="count" type="number" min="0" value="${value(q.mode==='rubric'?r?.met_criteria:r?.correct)}"></label>
          <label>实际作答人数<input name="total" type="number" min="1" value="${value(r?.total)}"></label>
          <label>检测时点<select name="timing">${feedbackOptions(timing,r?.timing||q.timing)}</select></label>
          <label>距上课天数<input name="days_after" type="number" min="0" max="3650" placeholder="延迟复测必填" value="${value(r?.days_after)}"></label>
          <label>独立完成<select name="independent">${feedbackOptions({'':'未确认',true:'是',false:'否'},r?.independent==null?'':String(r.independent))}</select></label>
          <label>记录依据<select name="basis">${feedbackOptions(basis,r?.basis||'observed')}</select></label></div>
          <label>典型错误或表达困难（每行一条）<textarea name="errors">${value((r?.errors||[]).join('\n'))}</textarea></label></div>`;
      }).join('')}
    </details>
    <div class="actions"><button type="submit" class="primary" ${version.status!=='ready'?'disabled':''}>保存课堂记录</button></div>
  </form>`;
  const form=$('#feedbackForm');
  form.dataset.originalObservedAt=draft.observed_at||'';
  form.dataset.legacyRequestedChanges=draft.requested_changes||'';
  form.dataset.supersedesFeedbackId=draft.supersedes_feedback_id||'';
  form.dataset.retainedRecords=JSON.stringify(retained);
  const remember=()=>feedbackDrafts.set(version.id,{...readFeedbackForm(form),advanced_open:$('#feedbackAdvanced',form).open});
  form.addEventListener('input',remember);form.addEventListener('change',remember);
  $('#feedbackAdvanced',form).addEventListener('toggle',remember);
  $('#feedbackDate input',form).addEventListener('change',event=>{$('#feedbackDate summary',form).textContent='日期：'+event.target.value;});
  bindAction(form,'submit',async()=>{
    const saved=await api('feedback',{id:version.id,payload:readFeedbackForm(form),key:uid()});
    feedbackDrafts.delete(version.id);
    const record=await api('entity?kind=feedback&id='+encodeURIComponent(saved.feedback_id));
    await loadOverview();if(state.version?.id!==version.id||state.tab!=='feedback')return;
    state.feedback=record;await renderTab();
  });
}
function feedbackEvidenceTable(a,d) {
  return `<div class="table-wrap"><table><thead><tr><th>环节</th><th>计划 / 实际密度</th><th>参与情况</th><th>目标对应检测</th></tr></thead><tbody>${a.density.map(r=>{
    const p=a.participation.find(x=>x.activity_id===r.activity_id),results=a.assessment_results.filter(x=>d.questions.find(q=>q.id===x.question_id)?.activity_id===r.activity_id);
    return `<tr><td>${esc(r.title)}</td><td>${num(r.planned_points_per_activity_minute)} / ${num(r.actual_points_per_activity_minute)}<small>点/环节分钟；思考练习 ${num(r.planned_thinking_minutes)} 分钟</small></td>
      <td>${p?`${p.participating??'未知'} / ${p.eligible??'未知'} · ${percent(p.participation_fraction)}<small>${esc(basis[p.basis]||p.basis)}</small>`:'未记录'}</td>
      <td>${results.map(x=>`${esc(x.question_id)}：${percent(x.correct_fraction??x.met_criteria_fraction)}<small>${esc(timing[x.timing]||x.timing)} · ${esc(basis[x.basis]||x.basis)} · ${x.independent===true?'独立完成':x.independent===false?'非独立完成':'独立条件未确认'}</small>`).join('')||'未记录'}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function renderLearningFeatures(features) {
  if(!features?.length)return '<p class="muted">保存更多具体表现后，这里会显示有来源的课堂线索。</p>';
  const grouped=new Map();for(const feature of features){if(!grouped.has(feature.label))grouped.set(feature.label,[]);grouped.get(feature.label).push(feature);}
  return [...grouped].map(([label,items])=>`<div class="evidence-item"><strong>${esc(label)}</strong>${items.map(item=>`<p>${esc(item.text)}</p><small>${(item.sources||[]).map(source=>`${esc(source.lesson_title)} · ${esc(source.observed_at)} · ${esc(source.basis==='teacher_report'?'教师原话':source.basis==='teacher_preference'?'教师偏好':basis[source.basis]||source.basis)}`).join('；')}</small>`).join('')}</div>`).join('');
}
async function renderAnalysis(container) {
  const f=state.feedback,a=f.analysis,version=state.version,classId=state.lesson.request.class_id;
  container.innerHTML=`<div class="feedback-review"><h3>课堂记录已保存</h3>
    <p class="muted">已关联本班学情。下次备课时，系统会结合新课目标参考相关记录。</p>
    <p class="feedback-original">${esc(f.payload.raw_text)}</p>
    ${f.payload.next_lesson_focus?`<p class="feedback-original"><b>下次教学关注：</b>${esc(f.payload.next_lesson_focus)}</p>`:''}
    <div class="actions"><button type="button" id="prepareNextLesson" class="primary">为本班备下一课</button><button type="button" id="showClassLearning">查看班级学情</button><button type="button" id="editFeedbackInput">更正这条记录</button><button type="button" id="addFeedbackInput">再记一条</button></div>
    <details><summary>本次记录的用时、参与与检测</summary><p class="muted">参与和学习结果分开查看；空白不补成数据。</p>${feedbackEvidenceTable(a,version.design)}</details>
    <div id="feedbackClassFeatures" class="profile-evidence"><p class="muted">正在读取班级特点…</p></div>
    ${f.payload.requested_changes?`<details><summary>历史本课调整意愿</summary><p class="feedback-original">${esc(f.payload.requested_changes)}</p></details>`:''}
    </div>`;
  $('#prepareNextLesson').onclick=()=>newLesson(classId||'');
  $('#showClassLearning').onclick=openClassProfile;
  $('#editFeedbackInput').onclick=async()=>{
    feedbackDrafts.set(version.id,{...structuredClone(f.payload),supersedes_feedback_id:f.id});state.feedback=null;await renderFeedbackBody();
  };
  $('#addFeedbackInput').onclick=async()=>{feedbackDrafts.delete(version.id);state.feedback=null;await renderFeedbackBody();};
  if(classId){
    const profile=await api('class-profile?id='+encodeURIComponent(classId));
    if(state.feedback?.id!==f.id||state.tab!=='feedback')return;
    $('#feedbackClassFeatures').innerHTML=`<h3>班级特点 · 待持续核对</h3><p class="muted">以下是有来源的课堂线索，单次表现不作为固定能力标签。</p>${renderLearningFeatures(profile.characteristics)}`;
  }else $('#feedbackClassFeatures').innerHTML='<p class="muted">这门课程尚未关联班级。</p>';
}
