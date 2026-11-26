import {loadPrompt} from './prompts.mjs';

// A conservative byte-based estimate, not the provider's exact tokenizer.
export const estimateTokens=value=>Math.ceil(Buffer.byteLength(typeof value==='string'?value:JSON.stringify(value),'utf8')/2);
const words=value=>{const text=String(value||'').toLowerCase(),set=new Set(text.match(/[a-z]{2,}|\d+/g)||[]);for(const run of text.match(/[\u4e00-\u9fff]+/g)||[]){for(let i=0;i<run.length-1;i++)set.add(run.slice(i,i+2));for(const char of run)if('秒分时米克升角数'.includes(char)||run.length===1)set.add(char);}return set;};
export function relevance(query,text){const q=words(query),t=words(text);let score=0;for(const word of q)if(t.has(word))score++;return score;}
const size=value=>estimateTokens(value);

export async function packTeachingContext(kind,raw,schema,tuning){
  const stage=kind==='generate'?'design':'revision',profile=tuning.stages[stage],context=structuredClone(raw);
  if(kind==='revise'&&context.analysis?.suggestions){
    const accepted=new Set(context.decision.choices.filter(c=>['accept','modify'].includes(c.action)).map(c=>c.suggestion_id));
    context.analysis.suggestions=context.analysis.suggestions.filter(s=>accepted.has(s.id));
    context.analysis_scope='只提供教师已采纳或修改的建议，完整原记录仍保存在本地。';
  }
  const base=estimateTokens(await loadPrompt('system'))+estimateTokens(await loadPrompt(stage))+estimateTokens(await loadPrompt('context'))+estimateTokens(await loadPrompt('quality'))+3*size(schema)+2048;
  const available=profile.input_budget_tokens-base;
  const supplements={sources:context.sources||[],knowledge:context.knowledge?.excerpts||[],history:context.class_profile?.evidence||[]};
  const characteristics=context.class_profile?.characteristics||[];
  if(context.sources)context.sources=context.sources.map(s=>({...s,excerpts:[]}));
  if(context.knowledge)context.knowledge.excerpts=[];
  if(context.class_profile){context.class_profile.evidence=[];context.class_profile.characteristics=[];context.class_profile.lessons=(context.class_profile.lessons||[]).slice(0,20);}
  if(available<0||size(context)>available)throw new Error(`本次${stage}的必要上下文超过输入预算，请在高级调试中提高预算或缩小本课范围；原设计与教师选择未被截断。`);
  const candidates=[];
  // Interleave categories so a long source cannot consume the whole lesson context.
  const sourcePassages=supplements.sources.flatMap(s=>(s.excerpts||[]).map(p=>({source:s,passage:p})));
  const max=Math.max(supplements.knowledge.length,sourcePassages.length,supplements.history.length);
  for(let i=0;i<max;i++){
    if(supplements.knowledge[i])candidates.push({kind:'knowledge',value:supplements.knowledge[i]});
    if(sourcePassages[i])candidates.push({kind:'source',value:sourcePassages[i]});
    if(supplements.history[i])candidates.push({kind:'history',value:supplements.history[i]});
  }
  const omitted={knowledge:0,source:0,history:0};
  for(const item of candidates){
    const value=item.kind==='source'?item.value.passage:item.value;
    if(size(context)+size(value)+64>available){omitted[item.kind]++;continue;}
    if(item.kind==='knowledge')context.knowledge.excerpts.push(value);
    if(item.kind==='history')context.class_profile.evidence.push(value);
    if(item.kind==='source')context.sources.find(s=>s.id===item.value.source.id).excerpts.push(value);
  }
  // A feature is only a pointer to selected records. Never leave an unsourced
  // summary after its evidence was removed by the input budget.
  if(context.class_profile){
    const retained=new Set(context.class_profile.evidence.map(row=>row.feedback_id));
    for(const feature of characteristics){
      const sources=(feature.sources||[]).filter(row=>retained.has(row.feedback_id));
      if(!sources.length)continue;
      const value={...feature,sources};
      if(size(context)+size(value)+256<=available)context.class_profile.characteristics.push(value);
    }
  }
  const report={stage,estimate_method:'utf8_bytes_divided_by_2_conservative_estimate',input_budget_tokens:profile.input_budget_tokens,
    estimated_input_tokens:base+size(context),before_context_tokens:size(raw),after_context_tokens:size(context),mandatory_preserved:true,
    selected:{knowledge_excerpts:context.knowledge?.excerpts?.length||0,source_excerpts:context.sources?.reduce((n,s)=>n+s.excerpts.length,0)||0,history_records:context.class_profile?.evidence?.length||0},omitted,
    knowledge_refs:(context.knowledge?.excerpts||[]).map(e=>({knowledge_id:e.knowledge_id,chunk:e.chunk,page:e.page})),
    feedback_refs:(context.class_profile?.evidence||[]).map(e=>e.feedback_id)};
  context.context_manifest={omitted,interpretation:'仅提供本次筛选后的材料摘录与相关历史记录；未出现的信息不代表不存在，不得补成事实。'};
  // Reserve enough framing overhead for the small manifest and tool feedback.
  if(base+size(context)>profile.input_budget_tokens)throw new Error('上下文预算不足以保留必要标记，请提高输入预算');
  return {context,report:{...report,estimated_input_tokens:base+size(context)}};
}

export async function packVisualContext(data,schema,tuning){
  const c=tuning.context,profile=tuning.stages.visual;
  const brief={...data.brief,pages:data.brief.pages.map(page=>{
    const {script,...rest}=page;return {...rest,teaching_intent:page.activity_title||page.title};
  })};
  const catalog=(data.assets||[]).map(a=>({...a,caption:String(a.caption||'').slice(0,180),description:String(a.description||'').slice(0,240),dialogue:String(a.dialogue||'').slice(0,160),question_text:String(a.question_text||'').slice(0,240),context:String(a.context||'').slice(0,200)}));
  const ranked=new Map(),candidates={};
  for(const page of brief.pages){
    const query=[page.title,...page.points,page.activity_title].join(' ');
    const matches=catalog.map(asset=>({asset,score:relevance(query,[asset.caption,asset.description,asset.dialogue,asset.question_text,asset.context].join(' '))})).filter(a=>a.score>0).sort((a,b)=>b.score-a.score||a.asset.id.localeCompare(b.asset.id)).slice(0,c.assets_per_page);
    candidates[page.number]=matches.map(m=>m.asset.id);
    for(const match of matches)if(!ranked.has(match.asset.id)||ranked.get(match.asset.id).score<match.score)ranked.set(match.asset.id,match);
  }
  const fallback=ranked.size===0&&catalog.length>0;
  if(fallback){for(const asset of catalog.slice(0,Math.min(c.assets_per_page,c.max_assets)))ranked.set(asset.id,{asset,score:0});for(const page of brief.pages)candidates[page.number]=[...ranked.keys()];}
  const base=estimateTokens(await loadPrompt('system'))+estimateTokens(await loadPrompt('visual'))+estimateTokens(await loadPrompt('context'))+3*size(schema)+2048;
  const candidateMap=assets=>{const allowed=new Set(assets.map(a=>a.id));return Object.fromEntries(Object.entries(candidates).map(([page,ids])=>[page,ids.filter(id=>allowed.has(id))]));};
  const result={...data,brief,assets:[],asset_candidates:candidateMap([])};
  if(base+size(result)>profile.input_budget_tokens)throw new Error('课件必要页面信息超过视觉输入预算，请提高预算；原文与页序未被截断。');
  for(const match of [...ranked.values()].sort((a,b)=>b.score-a.score||a.asset.id.localeCompare(b.asset.id)).slice(0,c.max_assets)){
    const proposed=[...result.assets,match.asset],mapping=candidateMap(proposed);
    // A figure ID can occur on many pages. Count its complete candidate mapping
    // before admission, not only the one catalog entry.
    if(base+size({...result,assets:proposed,asset_candidates:mapping})>profile.input_budget_tokens)continue;
    result.assets=proposed;result.asset_candidates=mapping;
  }
  if(base+size(result)>profile.input_budget_tokens)throw new Error('视觉素材候选目录超过预算，请提高视觉预算或减少候选数');
  return {data:result,report:{stage:'visual',estimate_method:'utf8_bytes_divided_by_2_conservative_estimate',input_budget_tokens:profile.input_budget_tokens,
    estimated_input_tokens:base+size(result),before_context_tokens:size(data),after_context_tokens:size(result),mandatory_preserved:true,
    full_scripts_excluded:true,total_assets:catalog.length,selected_assets:result.assets.length,candidate_selection:fallback?'fallback_no_lexical_match':'lexical_match',asset_candidates:result.asset_candidates}};
}

export function objectToolSchema(field,schema){
  const {$defs,...object}=schema;
  return {type:'object',properties:{[field]:object},required:[field],additionalProperties:false,...($defs?{$defs}:{})};
}
