import {randomUUID,createHash} from 'node:crypto';
import {pptFetch} from './ppt-network.mjs';
import {providerFailure} from './task-failures.mjs';
import {apiRuntimeFor,apiBucket} from './api-runtime.mjs';

export function validateApiKey(value){
  if(typeof value!=='string'||!value.trim()||!/^[\x21-\x7e]+$/.test(value.trim()))throw Object.assign(new Error('API Key 包含空格、换行或中文符号，请重新粘贴完整密钥。'),{kind:'setup',code:'invalid_credential'});
  return value.trim();
}
export function validateModelId(value){const model=String(value||'').trim();if(!/^[A-Za-z0-9._:/-]{1,160}$/.test(model))throw new Error('请填写有效模型名称，例如 Qwen/Qwen3-30B-A3B-Instruct-2507');return model;}
export function parseRetryAfter(value,now=Date.now()){
  if(!value)return null;const ms=/^\d+(?:\.\d+)?$/.test(value.trim())?Number(value)*1000:Date.parse(value)-now;
  return Number.isFinite(ms)&&ms>=0?Math.max(1000,ms):null;
}
function traceId(value){return typeof value==='string'&&/^[A-Za-z0-9._:-]{1,160}$/.test(value)&&!/^sk[-_]/i.test(value)?value:undefined;}
function apiError(message,fields={}){const error=new Error(message);Object.assign(error,fields);return error;}
async function boundedText(response,limit,signal){
  if(Number(response.headers?.get('content-length')||0)>limit)throw apiError('接口响应超过大小上限',{code:'response_too_large'});
  const reader=response.body?.getReader();
  if(!reader){const text=await response.text();if(Buffer.byteLength(text)>limit)throw apiError('接口响应超过大小上限',{code:'response_too_large'});return text;}
  let size=0;const chunks=[];const abort=()=>reader.cancel().catch(()=>{});signal.addEventListener('abort',abort,{once:true});
  try{while(true){signal.throwIfAborted();const part=await reader.read();signal.throwIfAborted();if(part.done)break;size+=part.value.byteLength;if(size>limit)throw apiError('接口响应超过大小上限',{code:'response_too_large'});chunks.push(part.value);}return Buffer.concat(chunks).toString('utf8');}
  finally{signal.removeEventListener('abort',abort);await reader.cancel().catch(()=>{});}
}
async function errorText(response,signal,limit){
  const controller=new AbortController();let timer,abort;
  const canceled=new Promise(resolve=>{abort=()=>resolve('');if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});});
  try{return await Promise.race([boundedText(response,limit,AbortSignal.any([signal,controller.signal])),canceled,new Promise(resolve=>{timer=setTimeout(()=>resolve(''),1000);})]);}
  catch{return '';}
  finally{clearTimeout(timer);signal.removeEventListener('abort',abort);controller.abort();}
}
export async function apiJson({root,url,key,method='GET',body,signal,config={},model,timeoutMs=60000,maxBytes=2*1024*1024,onStart,onHeaders}){
  key=validateApiKey(key);signal?.throwIfAborted();
  const runtime=apiRuntimeFor(root),bucket=model?apiBucket(url,model):null,controller=new AbortController();
  const combined=signal?AbortSignal.any([signal,controller.signal]):controller.signal;
  let timer,release,sent=false,response,knownFailure;
  const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{const e=knownFailure||apiError(method==='POST'&&sent?'请求超时，服务端处理结果待确认；不会自动重发。':'接口等待超时，本次未获得有效响应。',{code:'api_timeout',kind:method==='POST'&&sent?'uncertain':'timeout'});controller.abort(e);reject(e);},timeoutMs);});
  const operation=(async()=>{
    if(bucket)release=await runtime.acquire(bucket,combined);combined.throwIfAborted();
    await onStart?.();combined.throwIfAborted();sent=true;
    response=await pptFetch(url,{method,headers:{Accept:'application/json',Authorization:'Bearer '+key,'X-Trace-Id':randomUUID(),...(body!==undefined?{'Content-Type':'application/json'}:{})},
      ...(body!==undefined?{body:JSON.stringify(body)}:{}),signal:combined,redirect:'error'},config);
    if(combined.aborted){response.body?.cancel().catch(()=>{});combined.throwIfAborted();}
    const request_id=traceId(response.headers?.get('x-siliconcloud-trace-id')||response.headers?.get('x-request-id'));
    const retryAfterMs=parseRetryAfter(response.headers?.get('retry-after'));
    if(!response.ok){
      const headerFailure=providerFailure({status:response.status,message:'HTTP '+response.status},new URL(url).hostname==='api.siliconflow.cn'?'siliconflow':'');
      knownFailure=apiError(headerFailure?.message||'接口返回 HTTP '+response.status,{status:response.status,http_status:response.status,code:headerFailure?.code||'http_error',
        kind:response.status===429?'rate_limit':[400,401,402,403,404,422].includes(response.status)?'setup':response.status>=500?'uncertain':'failed',
        retryAfterMs,request_id,failure:headerFailure?{...headerFailure,...(request_id?{request_id}:{})}:null});
      if(response.status===429&&bucket)await runtime.rateLimited(bucket,retryAfterMs);
      const text=await errorText(response,combined,Math.min(maxBytes,65536));
      const detail=providerFailure({status:response.status,message:'HTTP '+response.status+' '+text},new URL(url).hostname==='api.siliconflow.cn'?'siliconflow':'');
      Object.assign(knownFailure,{message:detail?.message||knownFailure.message,code:detail?.code||knownFailure.code,
        retryAfterMs:retryAfterMs??(await runtime.policy()).rate_limit_cooldown_ms,
        failure:detail?{...detail,...(request_id?{request_id}:{}),...(retryAfterMs?{retry_after_ms:retryAfterMs}:{})}:null});
      throw knownFailure;
    }
    await onHeaders?.();combined.throwIfAborted();
    const text=await boundedText(response,maxBytes,combined);combined.throwIfAborted();
    if(!text.trim())throw apiError('接口返回空响应，未取得可保存的结果。',{code:'empty_response',kind:method==='POST'?'uncertain':'failed',request_id});
    let data;try{data=JSON.parse(text);}catch{throw apiError('接口未返回有效 JSON，请核对 Base URL 与接口协议。',{code:'invalid_response',kind:method==='POST'?'uncertain':'setup',request_id});}
    if(!data||typeof data!=='object'||Array.isArray(data))throw apiError('接口返回的数据结构不正确。',{code:'invalid_response',kind:method==='POST'?'uncertain':'setup',request_id});
    return {data,request_id};
  })();
  try{return await Promise.race([operation,deadline]);}
  catch(error){signal?.throwIfAborted();if(error.code==='response_too_large')error.kind=method==='POST'?'uncertain':'failed';if(error.kind||error.code==='api_timeout')throw error;throw apiError('接口连接或读取中断，请检查网络与代理。'+(sent&&method==='POST'?' 处理结果待确认，不会自动重发。':''),{code:'connection',kind:sent&&method==='POST'?'uncertain':'failed'});}
  finally{clearTimeout(timer);controller.abort();release?.();response?.body?.cancel().catch(()=>{});}
}
const modelRequests=new Map();
export async function listApiModels(options){
  const key=validateApiKey(options.key),id=createHash('sha256').update(JSON.stringify([options.url,options.config?.proxy_url||'',key])).digest('hex');
  if(modelRequests.has(id))return modelRequests.get(id);
  const request=(async()=>{const {data,request_id}=await apiJson({...options,key,method:'GET'});const rows=data.data??data.models;
    if(!Array.isArray(rows))throw apiError('模型列表格式不正确，请核对 /models 接口。',{code:'invalid_response',kind:'setup'});
    return {models:[...new Set(rows.map(row=>typeof row==='string'?row:row?.id||row?.name).filter(value=>typeof value==='string'))],request_id,paid_generation:false};})();
  modelRequests.set(id,request);try{return await request;}finally{modelRequests.delete(id);}
}
