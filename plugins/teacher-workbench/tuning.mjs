import {readFile,writeFile,rename} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export const DEFAULT_TUNING=Object.freeze({version:1,
  stages:{design:{temperature:.3,max_tokens:32768,input_budget_tokens:64000},revision:{temperature:.2,max_tokens:32768,input_budget_tokens:96000},visual:{temperature:.5,max_tokens:16000,input_budget_tokens:32000},vision:{temperature:.1,top_p:1,max_tokens:1500}},
  context:{retrieval_chars:16000,source_chars:18000,history_records:8,assets_per_page:4,max_assets:32},max_validation_attempts:3});
export const TUNING_RANGES={temperature:{min:0,max:2,step:.05},max_tokens:{min:512,max:32768,step:512},input_budget_tokens:{min:8000,max:160000,step:1000},top_p:{min:.01,max:1,step:.05},retrieval_chars:{min:1000,max:48000},source_chars:{min:1000,max:64000},history_records:{min:1,max:20},assets_per_page:{min:1,max:8},max_assets:{min:2,max:100},max_validation_attempts:{min:1,max:8}};
const scalar=(key,value,integer=false)=>{const range=TUNING_RANGES[key];if(typeof value!=='number'||!Number.isFinite(value)||value<range.min||value>range.max||(integer&&!Number.isInteger(value)))throw new Error(`${key}需在${range.min}至${range.max}之间${integer?'且为整数':''}`);return value;};
export function normalizeTuning(raw={}){
  for(const key of Object.keys(raw))if(!['version','stages','context','max_validation_attempts'].includes(key))throw new Error('未知调试配置字段：'+key);
  const result=structuredClone(DEFAULT_TUNING);
  for(const [stage,patch] of Object.entries(raw.stages||{})){
    if(!result.stages[stage])throw new Error('未知调试阶段');
    for(const [key,value] of Object.entries(patch)){
      if(!(key in result.stages[stage]))throw new Error(stage==='vision'?'该阶段不支持此参数':'当前文本通道未开放该参数：'+key);
      result.stages[stage][key]=scalar(key,value,key==='max_tokens'||key==='input_budget_tokens');
    }
  }
  for(const [key,value] of Object.entries(raw.context||{})){
    if(!(key in result.context))throw new Error('未知上下文参数');result.context[key]=scalar(key,value,true);
  }
  if(raw.max_validation_attempts!==undefined)result.max_validation_attempts=scalar('max_validation_attempts',raw.max_validation_attempts,true);
  return result;
}
export function createTuning({root}){
  const file=path.join(root,'config','agent-tuning.json');
  async function load(){try{return normalizeTuning(JSON.parse(await readFile(file,'utf8')));}catch(e){if(e.code!=='ENOENT')throw e;return normalizeTuning();}}
  async function save(patch){const old=await load();const merged={...old,...patch,stages:{...old.stages},context:{...old.context,...patch.context}};for(const [stage,fields] of Object.entries(patch.stages||{}))merged.stages[stage]={...old.stages[stage],...fields};const value=normalizeTuning(merged);const temporary=file+'.'+randomUUID()+'.tmp';await writeFile(temporary,JSON.stringify(value,null,2));await rename(temporary,file);return status();}
  async function status(){return {config:await load(),ranges:TUNING_RANGES,capabilities:{text_temperature:true,text_top_p:false,vision_temperature:true,vision_top_p:true},suggested_temperature_ranges:{design:[.2,.5],revision:[.1,.3],visual:[.4,.7],vision:[0,.2]}};}
  return {load,save,status};
}

// DSH AgentOptions only forwards maxTokens. Sampling must use this scoped waterfall.
export function installSampling(agentCtx,profile,onApplied){
  if(typeof agentCtx.on!=='function')return; // Lightweight test hosts may omit Cordis events.
  agentCtx.on('agent/request',async(_request,next)=>{
    const current=await next();const effective={...current,temperature:profile.temperature,maxTokens:profile.max_tokens};
    await onApplied?.({temperature:effective.temperature,max_tokens:effective.maxTokens});return effective;
  });
}

export function validationFeedback(error,attempt,maxAttempts){
  let issues;try{const parsed=JSON.parse(error.message);if(Array.isArray(parsed))issues=parsed.slice(0,10).map(e=>{
    const issue={path:(e.loc||[]).join('.'),message:String(e.msg||'校验失败').slice(0,300),type:e.type};
    if(e.type==='too_long'&&(e.loc||[]).includes('points'))issue.hint=`该逻辑页最多${e.ctx?.max_length||12}条要点；当前${e.ctx?.actual_length??'超量'}条。把内容按顺序分成多个逻辑页，保持activity_id与题目引用合法，新页面使用唯一ID并分配讲稿；不得删掉解题步骤。成品PPT每页最多5条，由宿主分页。`;
    if(String(e.msg||'').includes('缺少首次引入'))issue.hint='按错误中列出的知识点ID，找到实际首次讲解它的活动，将该ID加入该活动的introduces和knowledge_ids；每个新知识点只出现一次。只在教师已提供先备知识证据时才能把is_new改为false，不得伪造学情来绕过校验。';
    return issue;
  });}catch{}
  const message=issues?issues.map(e=>e.path+': '+e.message).join('；'):String(error.message||error).replace(/sk-[\w-]+/g,'[已隐藏密钥]').slice(0,1800);
  return {ok:false,error:message,repair:{attempt,remaining_attempts:Math.max(0,maxAttempts-attempt),instructions:'仅修正列出的错误，保留已正确内容和ID；提交修正后的完整结构化对象。',...(issues?{issues}:{})}};
}
