'use strict';
let tuningSnapshot=null;
async function loadTuningSettings(){
  tuningSnapshot=await api('tuning');const c=tuningSnapshot.config;
  const labels={design:'教学设计',revision:'学情修订',visual:'视觉规划',vision:'教材识图'};
  $('#tuningFields').innerHTML=`<p class="muted">参数会固定到新任务中。较低温度适合需要稳定结构的内容；建议区间只是调试起点，实际影响取决于模型服务。</p>${Object.entries(labels).map(([stage,label])=>{
    const p=c.stages[stage],range=tuningSnapshot.suggested_temperature_ranges[stage];
    return `<div class="feedback-activity"><h3>${label}</h3><div class="grid"><label>Temperature（0—2）<input type="number" data-tuning-stage="${stage}" data-tuning-field="temperature" min="0" max="2" step="0.05" value="${p.temperature}" required><small>调试起点 ${range[0]}—${range[1]}</small></label><label>最大输出 tokens<input type="number" data-tuning-stage="${stage}" data-tuning-field="max_tokens" min="512" max="32768" step="1" value="${p.max_tokens}" required></label>${stage==='vision'?`<label>Top P（0.01—1）<input type="number" data-tuning-stage="vision" data-tuning-field="top_p" min="0.01" max="1" step="0.01" value="${p.top_p}" required><small>通常先单独调整温度或 Top P。</small></label>`:`<label>输入上下文预算（估算 tokens）<input type="number" data-tuning-stage="${stage}" data-tuning-field="input_budget_tokens" min="8000" max="160000" step="1" value="${p.input_budget_tokens}" required><small>包含规则与结构开销，必要的原设计与教师选择不会被截断。</small></label>`}</div></div>`;
  }).join('')}<p class="muted">当前文本通道开放 Temperature 与输出长度；Top P 仅在教材识图中开放。</p><h3>材料与学情筛选</h3><div class="grid">${Object.entries({retrieval_chars:['教材摘录字符上限',1000,48000],source_chars:['上传资料摘录字符上限',1000,64000],history_records:['最多参考的历史学情',1,20],assets_per_page:['每页候选素材数',1,8],max_assets:['本课候选素材总上限',2,100]}).map(([key,[label,min,max]])=>`<label>${label}<input type="number" data-context-field="${key}" value="${c.context[key]}" min="${min}" max="${max}" step="1" required></label>`).join('')}<label>单阶段校验失败上限<input id="validationAttempts" type="number" min="1" max="8" value="${c.max_validation_attempts}" required><small>达到后保留材料并报告原因，避免反复输出无效内容。</small></label></div><div class="actions"><button id="resetTuningDefaults" type="button">恢复调试起点</button></div>`;
  $('#resetTuningDefaults').onclick=async()=>{const result=await api('tuning',{stages:{design:{temperature:.3,max_tokens:32768,input_budget_tokens:64000},revision:{temperature:.2,max_tokens:32768,input_budget_tokens:96000},visual:{temperature:.5,max_tokens:16000,input_budget_tokens:32000},vision:{temperature:.1,top_p:1,max_tokens:1500}},context:{retrieval_chars:16000,source_chars:18000,history_records:8,assets_per_page:4,max_assets:32},max_validation_attempts:3});tuningSnapshot=result;await loadTuningSettings();$('#tuningStatus').textContent='已恢复调试起点。';};
}
async function saveTuningSettings(){
  if(!tuningSnapshot)return;
  const stages={},context={};
  document.querySelectorAll('[data-tuning-stage]').forEach(input=>{(stages[input.dataset.tuningStage]||={})[input.dataset.tuningField]=Number(input.value);});
  document.querySelectorAll('[data-context-field]').forEach(input=>context[input.dataset.contextField]=Number(input.value));
  tuningSnapshot=await api('tuning',{stages,context,max_validation_attempts:Number($('#validationAttempts').value)});
  $('#tuningStatus').textContent='调试参数已保存，新任务使用新参数。';
}
