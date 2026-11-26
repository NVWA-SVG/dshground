import {setTimeout as delay} from 'node:timers/promises';
import {lookup} from 'node:dns/promises';
import {isIP} from 'node:net';
import {normalizeProxy,pptFetch,networkFailure} from './ppt-network.mjs';

export const DEFAULT_PPT_CONFIG = Object.freeze({provider:'native',gamma_engine:'classic',quality:'standard',
  theme_id:'',image_model:'',style:'适合本年级的清晰教学插画，统一配色，图文结合，留出提问和独立思考空间。',
  custom_base_url:'',proxy_url:'',auto_generate:true,asset_mode:'auto'});

export function validBaseUrl(value) {
  const u=new URL(value);
  if(u.username||u.password||u.search||u.hash||!(u.protocol==='https:'||(u.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(u.hostname))))throw new Error('接口地址需要 HTTPS；本机网关可使用 http://127.0.0.1。地址不能携带密钥或查询参数。');
  return u.href.replace(/\/$/,'');
}

export function normalizePptConfig(raw) {
  if(!['native','gamma','custom'].includes(raw.provider))throw new Error('请选择本地、Gamma或自定义PPT服务');
  if(!['classic','agent'].includes(raw.gamma_engine))throw new Error('Gamma生成模式不正确');
  if(!['lite','standard','max'].includes(raw.quality))throw new Error('Gamma质量档位不正确');
  if(!['auto','provider','local'].includes(raw.asset_mode||'auto'))throw new Error('课件配图来源不正确');
  const config={...DEFAULT_PPT_CONFIG,provider:raw.provider,gamma_engine:raw.gamma_engine,quality:raw.quality,
    theme_id:String(raw.theme_id||'').trim(),image_model:String(raw.image_model||'').trim(),
    style:String(raw.style||DEFAULT_PPT_CONFIG.style).trim(),custom_base_url:raw.custom_base_url?validBaseUrl(raw.custom_base_url):'',
    proxy_url:normalizeProxy(raw.proxy_url),auto_generate:raw.auto_generate!==false,asset_mode:raw.asset_mode||'auto'};
  if(config.style.length>1000||config.theme_id.length>128||config.image_model.length>100)throw new Error('视觉风格或模型参数过长');
  if(config.provider==='custom'&&!config.custom_base_url)throw new Error('请填写自定义PPT网关地址');
  return config;
}

export async function readBounded(response,limit,signal) {
  if(Number(response.headers.get('content-length')||0)>limit)throw new Error('服务返回的文件过大');
  const reader=response.body?.getReader();if(!reader)throw new Error('服务没有返回内容');
  let chunks=[],size=0;
  try {while(true){signal?.throwIfAborted();const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>limit)throw new Error('服务返回的内容超过大小限制');chunks.push(value);}}
  finally{await reader.cancel().catch(()=>{});}
  return Buffer.concat(chunks);
}

async function requestJson(url,{method='GET',key,authHeader='X-API-KEY',body,signal,config={}}={}) {
  if(typeof key!=='string'||!/^[\x21-\x7e]+$/.test(key))throw new Error('API Key包含空格、换行或中文符号，请只粘贴完整密钥后重新保存。');
  const combined=signal?AbortSignal.any([signal,AbortSignal.timeout(60000)]):AbortSignal.timeout(60000);
  let response;
  try{response=await pptFetch(url,{method,headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{}),[authHeader]:authHeader==='Authorization'?'Bearer '+key:key},body:body?JSON.stringify(body):undefined,signal:combined,redirect:'error'},config);}
  catch(error){signal?.throwIfAborted();throw new Error(networkFailure(error,{method,proxy:!!config.proxy_url}));}
  if(!response.ok){await response.body?.cancel();const messages={401:'接口已连接，但API Key无效或已失效，请重新保存密钥。',402:'接口已连接，但当前API可用额度不足或需要开通计费。',403:'接口已连接，但账号权限或API可用额度不允许此操作。',407:'代理要求身份验证，请检查本机代理设置。',429:'接口请求过于频繁，请稍后重试。'};throw new Error(`PPT服务返回 HTTP ${response.status}。${messages[response.status]||'请检查主题、模型参数或供应商服务状态。'}系统不会自动重发创建请求。`);}
  const data=await readBounded(response,2*1024*1024,combined);
  try{return JSON.parse(data.toString('utf8'));}catch{throw new Error('PPT服务未返回有效JSON');}
}

export function buildGammaRequest(data,config) {
  const common={format:'presentation',exportAs:'pptx',sharingOptions:{externalAccess:'noAccess'},...(config.theme_id?{themeId:config.theme_id}:{})};
  if(config.gamma_engine==='agent')return {...common,prompt:data.markdown,numCards:data.brief.pages.length,quality:config.quality,disableConnectors:true,
    additionalInstructions:'生成16:9简体中文教学课件。必须按提示词逐页保留标题与所有正文要点，不加封面目录或致谢。设计说明不显示为正文。标题与正文采用原生可编辑文字，不能整页图片化。'};
  const perPage=Math.max(20,Math.floor((4000-data.plan.style.length)/data.plan.pages.length));
  const instructions=['16:9教学课件。逐页原文完整保留，不额外增加页面。只改视觉呈现，不新增知识。使用原生文字与教学图形，配图避免错误数学数量。',data.plan.style,
    ...data.plan.pages.map(p=>`第${p.page_number}页：${p.layout}；${p.visual_prompt.slice(0,perPage)}`)].join('\n').slice(0,5000);
  return {...common,inputText:data.brief.pages.map(p=>[p.title,...p.points.map(t=>'- '+t)].join('\n')).join('\n---\n'),
    textMode:'preserve',cardSplit:'inputTextBreaks',additionalInstructions:instructions,
    textOptions:{language:'zh-cn',audience:data.brief.grade},cardOptions:{dimensions:'16x9'},
    imageOptions:{source:'aiGenerated',style:data.plan.style,...(config.image_model?{model:config.image_model}:{})}};
}

// Provider contract: create(data, key, signal) -> {id}; poll(id,key,signal) ->
// {status:'pending'|'completed'|'failed', downloadUrl?, credits?}. Export URLs
// are transient capabilities; they must never be written to public job records.
export function presentationProvider(config) {
  if(config.provider==='gamma') {
    const endpoint='https://public-api.gamma.app/v1.0/'+(config.gamma_engine==='agent'?'agent/generations':'generations');
    return {identity:'gamma:'+config.gamma_engine,credential:'GAMMA_API_KEY',base:endpoint,
      create:async(data,key,signal)=>{const result=await requestJson(endpoint,{method:'POST',key,body:buildGammaRequest(data,config),signal,config});return {id:result.generationId};},
      poll:async(id,key,signal)=>{const r=await requestJson(endpoint+'/'+encodeURIComponent(id),{key,signal,config});return {status:r.status,downloadUrl:r.exportUrl,credits:r.credits};}};
  }
  if(config.provider==='custom') {
    const base=validBaseUrl(config.custom_base_url),endpoint=base+'/generations';
    return {identity:'custom',credential:'CUSTOM_PPT_API_KEY',base,
      create:async(data,key,signal)=>{const r=await requestJson(endpoint,{method:'POST',key,authHeader:'Authorization',signal,config,
        body:{client_request_id:data.run_id,prompt_markdown:data.markdown,slides:data.brief.pages.map(p=>({page_number:p.number,title:p.title,points:p.points})),visual_plan:data.plan,assets:(data.assets||[]).map(asset=>({id:asset.id,knowledge_id:asset.knowledge_id,page:asset.page,file:asset.file,caption:asset.caption})),format:'pptx',aspect_ratio:'16:9',language:'zh-cn',preserve_text:true}});return {id:r.id};},
      poll:async(id,key,signal)=>{const r=await requestJson(endpoint+'/'+encodeURIComponent(id),{key,authHeader:'Authorization',signal,config});return {status:r.status==='running'?'pending':r.status,downloadUrl:r.download_url,credits:r.credits};}};
  }
  throw new Error('请先在课件生成设置中选择外部PPT服务');
}

export async function checkPresentationConnection(config,key,signal) {
  const url=config.provider==='gamma'?'https://public-api.gamma.app/v1.0/themes':validBaseUrl(config.custom_base_url)+'/health';
  await requestJson(url,{key,authHeader:config.provider==='gamma'?'X-API-KEY':'Authorization',signal,config});
  return {ok:true,provider:config.provider,via_proxy:!!config.proxy_url};
}

function privateAddress(address) {
  if(address.includes(':'))return address==='::1'||address==='::'||/^(fc|fd|fe80|::ffff:)/i.test(address);
  const p=address.split('.').map(Number);return p[0]===0||p[0]===10||p[0]===127||p[0]>=224||p[0]===169&&p[1]===254||p[0]===172&&p[1]>=16&&p[1]<=31||p[0]===192&&p[1]===168;
}

export async function downloadPresentation(url,config,signal) {
  let current=new URL(url);
  const explicitlyAllowed=config.provider==='custom'?new URL(config.custom_base_url).origin:null;
  for(let hop=0;hop<5;hop++) {
    const sameGateway=current.origin===explicitlyAllowed;
    if(current.username||current.password||!['https:','http:'].includes(current.protocol)||(current.protocol!=='https:'&&!sameGateway))throw new Error('PPT下载地址不符合要求');
    if(!sameGateway) {
      const host=current.hostname.replace(/^\[|\]$/g,'');
      if(host==='localhost'||host.endsWith('.localhost')||isIP(host)&&privateAddress(host))throw new Error('拒绝供应商返回的内网下载地址');
      // CONNECT proxies resolve the destination remotely; local DNS may be unavailable.
      if(!config.proxy_url){const addresses=await lookup(host,{all:true});if(addresses.some(a=>privateAddress(a.address)))throw new Error('拒绝供应商返回的内网下载地址');}
    }
    const combined=signal?AbortSignal.any([signal,AbortSignal.timeout(120000)]):AbortSignal.timeout(120000);
    let response;try{response=await pptFetch(current,{signal:combined,redirect:'manual'},config);}
    catch(error){signal?.throwIfAborted();throw new Error(networkFailure(error,{proxy:!!config.proxy_url}));}
    if([301,302,303,307,308].includes(response.status)){const location=response.headers.get('location');await response.body?.cancel();if(!location)throw new Error('下载重定向缺少目标');current=new URL(location,current);continue;}
    if(!response.ok)throw new Error(`PPT下载失败 HTTP ${response.status}；可继续查询原任务，无需重新付费生成。`);
    const bytes=await readBounded(response,50*1024*1024,combined);
    if(bytes.subarray(0,2).toString()!=='PK')throw new Error('下载内容不是PPTX文件');
    return bytes;
  }
  throw new Error('PPT下载重定向次数过多');
}

export async function generatePresentation({data,config,key,remote,saveRemote,writeDownload,accept,signal,onStage,pollIntervalMs=5000,timeoutMs=900000}) {
  const provider=presentationProvider(config);
  if(remote?.identity&&(remote.identity!==provider.identity||remote.base!==provider.base))throw new Error('该任务属于另一个服务地址或Gamma模式，请恢复原设置再继续');
  if(remote?.phase==='submitting'||remote?.phase==='uncertain')throw new Error('上次创建请求结果不明确。请先在供应商后台核对；系统不会自动再次创建收费任务。');
  if(!remote?.generation_id) {
    await saveRemote({identity:provider.identity,base:provider.base,phase:'submitting'});
    let created;
    try{created=await provider.create(data,key,signal);if(typeof created.id!=='string'||!/^[A-Za-z0-9_-]{1,150}$/.test(created.id))throw new Error('供应商没有返回有效任务编号');}
    catch(error){await saveRemote({identity:provider.identity,base:provider.base,phase:'uncertain'});throw error;}
    remote={identity:provider.identity,base:provider.base,phase:'pending',generation_id:created.id};await saveRemote(remote);
  }
  const started=Date.now();
  while(Date.now()-started<timeoutMs) {
    signal?.throwIfAborted();await onStage('PPT服务正在生成排版与配图');
    const result=await provider.poll(remote.generation_id,key,signal);
    if(result.status==='failed'){await saveRemote({...remote,phase:'failed'});throw new Error('PPT供应商生成失败；请先检查其任务记录与额度，再决定是否新建。');}
    if(result.status==='completed') {
      if(!result.downloadUrl)throw new Error('供应商已生成，但PPTX导出链接尚不可用；可继续查询本任务，系统不会重新创建。');
      await onStage('正在下载并校验图文课件');
      await writeDownload(await downloadPresentation(result.downloadUrl,config,signal));
      const credits=result.credits&&typeof result.credits==='object'?Object.fromEntries(['deducted','remaining'].filter(k=>Number.isFinite(result.credits[k])).map(k=>[k,result.credits[k]])):null;
      await saveRemote({...remote,phase:'downloaded',credits});
      const accepted=await accept({provider:provider.identity,remote_id:remote.generation_id,credits});
      await saveRemote({...remote,phase:'completed',credits});return accepted;
    }
    if(result.status!=='pending')throw new Error('PPT服务返回未知任务状态');
    await delay(pollIntervalMs,undefined,{signal});
  }
  throw new Error('PPT服务等待超时，远端任务编号已保留；可继续查询，避免重复生成扣费。');
}
