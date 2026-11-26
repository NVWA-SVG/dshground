import {readFile,writeFile,rename} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export const DEFAULT_API_POLICY=Object.freeze({max_concurrent:1,min_interval_ms:1000,rate_limit_cooldown_ms:60000});
export function normalizeApiPolicy(raw={}){
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new Error('API 节流设置格式不正确');
  const result={...DEFAULT_API_POLICY};
  for(const [key,value] of Object.entries(raw)){
    const range={max_concurrent:[1,4],min_interval_ms:[0,10000],rate_limit_cooldown_ms:[1000,300000]}[key];
    if(!range||!Number.isInteger(value)||value<range[0]||value>range[1])throw new Error('API 节流参数不正确：'+key);
    result[key]=value;
  }
  return result;
}
export function apiBucket(base,model){return (new URL(base).origin==='https://api.siliconflow.cn'?'siliconflow':new URL(base).origin)+':'+model;}
const aborted=()=>new DOMException('请求已取消','AbortError');
export class ApiRuntime{
  constructor(root){this.file=path.join(root,'config/api-policy.json');this.buckets=new Map();this.closed=false;this.admitted=0;}
  async policy(){
    this.loading??=(async()=>{try{return normalizeApiPolicy(JSON.parse(await readFile(this.file,'utf8')));}catch(e){if(e.code==='ENOENT')return {...DEFAULT_API_POLICY};throw e;}})();
    return {...await this.loading};
  }
  async configure(raw){
    if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new Error('API 节流设置格式不正确');
    const value=normalizeApiPolicy({...await this.policy(),...raw}),temporary=this.file+'.'+randomUUID()+'.tmp';
    await writeFile(temporary,JSON.stringify(value,null,2));await rename(temporary,this.file);this.loading=Promise.resolve(value);
    for(const bucket of this.buckets.values()){bucket.policy=value;this.pump(bucket);}return this.status();
  }
  async status(){return {policy:await this.policy(),scope:'per_model',active:[...this.buckets.values()].reduce((n,b)=>n+b.active,0),
    waiting:[...this.buckets.values()].reduce((n,b)=>n+b.queue.length,0),admitted_requests:this.admitted,ambiguous_request_retries:0};}
  async acquire(key,signal){
    const policy=await this.policy();signal?.throwIfAborted();if(this.closed)throw aborted();
    let bucket=this.buckets.get(key);if(!bucket){bucket={active:0,next:0,cooldown:0,queue:[],policy};this.buckets.set(key,bucket);}
    bucket.policy=policy;
    return new Promise((resolve,reject)=>{
      const entry={resolve,reject,signal};
      entry.abort=()=>{const index=bucket.queue.indexOf(entry);if(index>=0){bucket.queue.splice(index,1);reject(signal.reason||aborted());this.pump(bucket);}};
      signal?.addEventListener('abort',entry.abort,{once:true});bucket.queue.push(entry);this.pump(bucket);
    });
  }
  pump(bucket){
    clearTimeout(bucket.timer);bucket.timer=null;
    if(this.closed||!bucket.queue.length)return;
    const policy=bucket.policy;
    if(bucket.active>=policy.max_concurrent)return;
    const delay=Math.max(bucket.next,bucket.cooldown)-Date.now();
    if(delay>0){bucket.timer=setTimeout(()=>this.pump(bucket),Math.min(delay,300000));return;}
    const entry=bucket.queue.shift();entry.signal?.removeEventListener('abort',entry.abort);
    if(entry.signal?.aborted){entry.reject(entry.signal.reason||aborted());this.pump(bucket);return;}
    bucket.active++;this.admitted++;bucket.next=Date.now()+policy.min_interval_ms;let released=false;
    entry.resolve(()=>{if(released)return;released=true;bucket.active--;this.pump(bucket);});this.pump(bucket);
  }
  async rateLimited(key,retryAfterMs){
    const policy=await this.policy();let bucket=this.buckets.get(key);
    if(!bucket){bucket={active:0,next:0,cooldown:0,queue:[],policy};this.buckets.set(key,bucket);}
    const delay=Number.isFinite(retryAfterMs)&&retryAfterMs>0?retryAfterMs:policy.rate_limit_cooldown_ms;
    bucket.cooldown=Math.max(bucket.cooldown,Date.now()+delay);this.pump(bucket);
  }
  dispose(){this.closed=true;for(const bucket of this.buckets.values()){clearTimeout(bucket.timer);for(const entry of bucket.queue){entry.signal?.removeEventListener('abort',entry.abort);entry.reject(aborted());}bucket.queue=[];}}
}
const runtimes=new Map();
export function apiRuntimeFor(root){const key=path.resolve(root);let runtime=runtimes.get(key);if(!runtime||runtime.closed){runtime=new ApiRuntime(key);runtimes.set(key,runtime);}return runtime;}

export function installApiScheduling(ctx,runtime){
  if(typeof ctx.on!=='function')return;
  // Keep DSH's immutable requests and stream chunks intact. The hook only
  // coordinates admission and observes real failures; it never replays a call.
  ctx.on('llm/stream',async function*(options,next){
    if(options.provider!=='siliconflow'){yield* next();return;}
    const key='siliconflow:'+options.model,release=await runtime.acquire(key,options.signal);
    try{
      for await(const chunk of next()){
        const failure=chunk.type==='finish'&&chunk.reason?.kind==='error'&&(chunk.reason.failure||chunk.reason.error);
        if(failure&&(failure.status===429||failure.code==='RATE_LIMIT'||/\b429\b/.test(failure.message||'')))await runtime.rateLimited(key,failure.providerRetryAfterMs);
        yield chunk;
      }
    }catch(e){if(e.status===429||e.code==='RATE_LIMIT')await runtime.rateLimited(key,e.providerRetryAfterMs);throw e;}
    finally{release();}
  });
}
