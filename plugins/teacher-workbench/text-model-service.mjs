import {readFile,writeFile,rename} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {validateApiKey,validateModelId,listApiModels} from './api-client.mjs';
export const DEFAULT_TEXT_MODEL='Qwen/Qwen3-30B-A3B-Instruct-2507';
export function createTextModelService({ctx,root}){
  const file=path.join(root,'config/teacher-model.json');
  async function config(){try{return {provider:'siliconflow',model:DEFAULT_TEXT_MODEL,...JSON.parse(await readFile(file,'utf8'))};}catch(e){if(e.code!=='ENOENT')throw e;return {provider:'siliconflow',model:DEFAULT_TEXT_MODEL};}}
  async function status(){const info=await ctx.credentials.describe('SILICONFLOW_API_KEY');return {...await config(),configured:!!info.configured};}
  async function registeredModels(){const models=typeof ctx.llm?.listModels==='function'?await ctx.llm.listModels('siliconflow'):[];return [...new Set([...(models||[]).map(row=>row.id||row.model), (await config()).model].filter(Boolean))];}
  async function save(data){
    const model=validateModelId(data.model),key=data.api_key?validateApiKey(data.api_key):null;
    if(key&&key.length<10)throw new Error('API Key 长度不正确');
    const supported=await registeredModels();if(!supported.includes(model))throw new Error('当前备课通道尚未接入该模型。已接入：'+supported.join('、'));
    if(key)await ctx.credentials.set('SILICONFLOW_API_KEY',key);
    const temporary=file+'.'+randomUUID()+'.tmp';await writeFile(temporary,JSON.stringify({provider:'siliconflow',model},null,2));await rename(temporary,file);return status();
  }
  async function testConnection(){
    const selected=await config(),credential=await ctx.credentials.resolve('SILICONFLOW_API_KEY');
    if(!credential?.value)throw new Error('请先保存 SiliconFlow API Key');
    const result=await listApiModels({root,url:'https://api.siliconflow.cn/v1/models',key:credential.value,timeoutMs:30000});
    if(!result.models.includes(selected.model))throw new Error('接口认证成功，但当前账号的模型列表没有 '+selected.model+'，请核对模型权限与名称。');
    return {...result,selected_model:selected.model,configured:true,credential_validated:true,model_available:true,tool_calling_verified:false};
  }
  return {config,status,save,testConnection};
}
