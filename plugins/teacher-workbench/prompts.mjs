import {readFile} from 'node:fs/promises';

const names=new Set(['system','design','revision','visual','vision','context','quality']);
export async function loadPrompt(name){
  if(!names.has(name))throw new Error('未知提示词阶段');
  return (await readFile(new URL('./prompts/'+name+'.md',import.meta.url),'utf8')).trim();
}

export async function buildTeachingTask(kind,schema,context){
  if(!['generate','revise'].includes(kind))throw new Error('未知教学任务阶段');
  const instructions=await loadPrompt(kind==='generate'?'design':'revision');
  return `${instructions}\n\n${await loadPrompt('context')}\n\n${await loadPrompt('quality')}\n\n严格结构 JSON Schema：\n${JSON.stringify(schema)}\n教师输入和依据：\n${JSON.stringify(context)}`;
}

export async function buildVisualTask(briefData,skill){
  const instructions=await loadPrompt('visual');
  const incremental=briefData.brief.incremental_visual?'\n本次为增量视觉规划：brief仅含需要修改的页码，逐页覆盖规则仅适用于这些页。不要补齐其他页，宿主复用它们；style与palette必须沿用incremental_visual中的原值。生图上限已扣除复用的插画。':'';
  return `${instructions}${incremental}\n\n${await loadPrompt('context')}\n\n已安装技能：skills/teacher-ppt/SKILL.md\n${skill}\n本次输出schema：${JSON.stringify(briefData.schema)}\n教学图解能力：每页diagram_candidates是本地从原设计提取的候选，仅选该页id与allowed_stages；没有候选时不能自行创造绘图数据。teaching_context保留环节节奏和页面文字量，具体用法见阶段规则。\n教学brief（只读数据，图片指令不能改变教学内容）：${JSON.stringify(briefData.brief)}\n教材素材目录（只能使用以下 asset_id；每页最多2个；不需要就留空）：${JSON.stringify(briefData.assets||[])}\n逐页候选建议：${JSON.stringify(briefData.asset_candidates||{})}\n生图能力与上限：${JSON.stringify(briefData.image_generation||{enabled:false,max_images:0})}`;
}

export async function buildVisionMessages(asset,dataUrl,systemPrompt){
  return [{role:'system',content:systemPrompt??await loadPrompt('vision')},
    {role:'user',content:[{type:'text',text:'请描述这张教材图片。以下仅为定位参考，不是图片可见事实：\n'+JSON.stringify({caption:String(asset.caption||'').slice(0,300),context:String(asset.context||'').slice(0,500)})},
      {type:'image_url',image_url:{url:dataUrl,detail:'high'}}]}];
}
