import {readFile,writeFile,rename} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {buildVisionMessages,loadPrompt} from './prompts.mjs';
import {createTuning} from './tuning.mjs';
import {apiJson,listApiModels,validateApiKey,validateModelId} from './api-client.mjs';
import {validBaseUrl} from './ppt-providers.mjs';
import {normalizeProxy} from './ppt-network.mjs';

export const DEFAULT_VISION_CONFIG = Object.freeze({
  enabled: true,
  base_url: 'https://api.siliconflow.cn/v1',
  model: 'Qwen/Qwen3-VL-30B-A3B-Thinking',
  credential_source:'shared',proxy_url:'',
});

async function readOptional(file) {
  try { return JSON.parse(await readFile(file, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; return null; }
}

function safeMessage(error) {
  return String(error?.message || error).replace(/sk-[\w-]+/g, '[已隐藏密钥]');
}

export function createVisionService({ctx,root,call,requestTimeoutMs=120000}) {
  const file = path.join(root, 'config', 'vision-api.json');
  const normalize=raw=>{
    const base_url=validBaseUrl(String(raw.base_url??DEFAULT_VISION_CONFIG.base_url).trim());
    const credential_source=raw.credential_source||(new URL(base_url).origin==='https://api.siliconflow.cn'?'shared':'separate');
    if(!['shared','separate'].includes(credential_source))throw new Error('请选择视觉模型的密钥来源');
    if(credential_source==='shared'&&new URL(base_url).origin!=='https://api.siliconflow.cn')throw new Error('其他视觉接口请选择独立密钥');
    return {enabled:raw.enabled!==false,base_url,model:validateModelId(raw.model||DEFAULT_VISION_CONFIG.model),credential_source,proxy_url:normalizeProxy(raw.proxy_url)};
  };
  const config = async () => normalize(await readOptional(file)||{});
  const credentialName=c=>normalize(c).credential_source==='shared'?'SILICONFLOW_API_KEY':'VISION_API_KEY';
  async function status() {
    const c = await config();
    const credential = await ctx.credentials.describe(credentialName(c));
    return {...c, configured: credential.configured};
  }
  async function saveSettings(data) {
    const current = await config();
    const next=normalize({...current,...data});
    if (data.api_key) {
      const key = validateApiKey(data.api_key);
      if(next.credential_source==='shared')throw new Error('共享模式请在主模型设置中更新密钥；视觉独立密钥请选择“独立密钥”');
      if(key.length<10)throw new Error('视觉模型 API Key 长度不正确');
      await ctx.credentials.set('VISION_API_KEY', key);
    }
    const temporary = file + '.' + randomUUID() + '.tmp';
    await writeFile(temporary, JSON.stringify(next, null, 2));
    await rename(temporary, file);
    return status();
  }
  async function snapshot() {
    const c=await config();
    return {config:c,sampling:(await createTuning({root}).load()).stages.vision,systemPrompt:await loadPrompt('vision')};
  }
  async function ready() {
    const c=await status();
    if(!c.enabled||!c.configured)throw new Error('请先启用视觉模型并配置所选来源的密钥');
  }
  async function captionAsset(asset, {config:c,sampling,systemPrompt,signal,onPhase}) {
    const credential=await ctx.credentials.resolve(credentialName(c));
    signal.throwIfAborted();
    if(!credential?.value)throw Object.assign(new Error('请先保存视觉模型密钥'),{kind:'setup'});
    const key=credential.value;
    const resolved = await call('knowledge_asset_path', {id: asset.knowledge_id, name: asset.file},signal);
    signal.throwIfAborted();
    const bytes = await readFile(resolved.path);
    signal.throwIfAborted();
    if(asset.sha256&&createHash('sha256').update(bytes).digest('hex')!==asset.sha256)throw new Error('图片内容已变化，请重新检查素材');
    const extension = path.extname(asset.file).toLowerCase();
    const mime = extension === '.jpg' || extension === '.jpeg' ? 'image/jpeg'
      : extension === '.webp' ? 'image/webp' : 'image/png';
    const dataUrl = `data:${mime};base64,${bytes.toString('base64')}`;
    const messages=await buildVisionMessages(asset,dataUrl,systemPrompt);
    signal.throwIfAborted();
    await onPhase?.('waiting');
    const {data}=await apiJson({root,url:c.base_url.replace(/\/$/, '')+'/chat/completions',key,method:'POST',
      config:c,model:c.model,signal,timeoutMs:requestTimeoutMs,
      body:{model:c.model,messages,max_tokens:sampling.max_tokens,temperature:sampling.temperature,top_p:sampling.top_p},
      onStart:()=>onPhase?.('requesting'),onHeaders:()=>onPhase?.('reading')});
    signal.throwIfAborted();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content!=='string'||!content.trim()) throw Object.assign(new Error('视觉模型未返回可用描述，本张请求结果待确认'),{kind:'uncertain'});
    return String(content).replace(/^```(?:markdown)?\s*/i, '').replace(/```\s*$/, '').trim().slice(0, 800);
  }
  async function caption(asset,prepared){
    const controller=new AbortController();let timer;
    // AbortSignal alone cannot bound a stalled transport that ignores cancellation.
    // Race the entire operation (local preflight, headers and response body), and
    // prevent late preflight completion from starting a new paid request.
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{
      const error=Object.assign(new Error(`本张识图超过 ${Math.ceil(requestTimeoutMs/1000)} 秒，已停止等待；服务端处理结果待确认，不会自动重发`),{kind:'uncertain',code:'vision_timeout'});
      reject(error);controller.abort(error);
    },requestTimeoutMs);});
    try{return await Promise.race([captionAsset(asset,{...prepared,signal:controller.signal}),deadline]);}
    finally{clearTimeout(timer);}
  }
  async function listModels() {
    const c=await config(),credential=await ctx.credentials.resolve(credentialName(c));
    if(!credential?.value)throw new Error('请先配置视觉模型密钥');
    const result=await listApiModels({root,url:c.base_url+'/models',key:credential.value,config:c,timeoutMs:30000});
    return {...result,selected_model:c.model,model_available:result.models.includes(c.model)};
  }
  async function describe(knowledgeId, {limit = 6} = {}) {
    const c = await config();
    if (!c.enabled) throw new Error('视觉图片描述未启用');
    const credential = await ctx.credentials.resolve(credentialName(c));
    if (!credential?.value) throw new Error('请先配置所选来源的视觉模型密钥');
    const {assets} = await call('knowledge_assets', {id: knowledgeId});
    const pending = assets.filter(asset => asset.kind !== 'question' && !asset.description)
      .slice(0, Math.max(1, Math.min(24, Number(limit) || 6)));
    if (!pending.length) return {described: 0, remaining: 0, errors: []};
    const prepared=await snapshot();
    const updates = [], errors = [];
    let index = 0, setupFailed=false;
    async function worker() {
      while (index < pending.length&&!setupFailed) {
        const asset = pending[index++];
        try {
          updates.push({id: asset.id, description: await caption(asset,prepared), model: c.model});
        } catch (error) {
          if(error.kind==='setup')setupFailed=true;
          errors.push({id: asset.id, error: safeMessage(error)});
        }
      }
    }
    await Promise.all([worker(), worker()]);
    if (updates.length) await call('knowledge_asset_describe', {id: knowledgeId, updates});
    const remaining = (await call('knowledge_assets', {id: knowledgeId})).assets
      .filter(asset => asset.kind !== 'question' && !asset.description).length;
    return {described: updates.length, remaining, errors};
  }
  return {status, saveSettings, listModels, describe, config,snapshot,ready,caption};
}
