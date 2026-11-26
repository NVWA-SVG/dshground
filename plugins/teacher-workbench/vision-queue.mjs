import {mkdir,readFile,writeFile,rename,readdir} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const time=()=>new Date().toISOString();
const safe=error=>String(error?.message||error).replace(/sk-[\w-]+/g,'[已隐藏密钥]').slice(0,500);
async function read(file){try{return JSON.parse(await readFile(file,'utf8'));}catch(error){if(error.code==='ENOENT')return null;throw error;}}
async function atomic(file,value){const temp=file+'.'+randomUUID()+'.tmp';await writeFile(temp,JSON.stringify(value,null,2));await rename(temp,file);}
const finished=new Set(['done','cached']);

// One global runner with two workers. Persist intent before any remote request;
// cache the response before publishing it to the shared textbook description table.
export async function createVisionQueue({root,vision,call,concurrency=1,retryDelay=ms=>new Promise(r=>setTimeout(r,ms))}) {
  const base=path.join(root,'data','vision-queue'),directory=path.join(base,'jobs'),cacheDir=path.join(base,'cache');
  await mkdir(directory,{recursive:true});await mkdir(cacheDir,{recursive:true});
  const jobs=new Map();let gate=Promise.resolve(),writer=Promise.resolve(),runner=null,closed=false;
  const serial=action=>{const result=gate.then(action);gate=result.catch(()=>{});return result;};
  const persist=job=>{const snapshot=JSON.parse(JSON.stringify(job));const result=writer.then(()=>atomic(path.join(directory,job.id+'.json'),snapshot));writer=result.catch(()=>{});return result;};
  const cacheKey=(sha,prepared)=>hash([sha,prepared.config.base_url,prepared.config.model,prepared.sampling,prepared.systemPrompt]);
  const cached=(sha,prepared)=>read(path.join(cacheDir,cacheKey(sha,prepared)+'.json'));
  function publicJob(job){
    const counts={total:job.items.length,done:0,cached:0,pending:0,inflight:0,failed:0,uncertain:0,writeback_failed:0};
    for(const item of job.items){if(finished.has(item.status))counts.done++;if(item.status==='cached')counts.cached++;if(item.status==='pending')counts.pending++;if(['requesting','saving'].includes(item.status))counts.inflight++;if(item.status in counts&&!['pending','cached','done'].includes(item.status))counts[item.status]++;}
    return {id:job.id,knowledge_id:job.knowledge_id,root_id:job.root_id,chapter_id:job.chapter_id,scope_title:job.scope_title,
      model:job.prepared.config.model,status:job.status,created_at:job.created_at,updated_at:job.updated_at,
      message:job.message||'',api_requests:job.api_requests,counts,
      errors:job.items.filter(i=>i.error).slice(-8).map(i=>({page:i.asset.source_page||i.asset.page,error:i.error,status:i.status})),
      current:job.items.filter(i=>['requesting','saving'].includes(i.status)).map(i=>({page:i.asset.source_page||i.asset.page,caption:i.asset.caption||'教材插图',
        phase:i.phase||'requesting',started_at:i.started_at||null,elapsed_seconds:i.started_at?Math.max(0,Math.floor((Date.now()-Date.parse(i.started_at))/1000)):null}))};
  }
  const list=()=>[...jobs.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at)).map(publicJob);
  async function plan(data){
    const inventory=await call('knowledge_vision_inventory',{id:data.knowledge_id,chapter_id:data.chapter_id||null});
    const prepared=await vision.snapshot(),groups=new Map();let questions=0,described=0;
    for(const asset of inventory.assets){
      if(asset.kind==='question'){questions++;continue;}
      if(asset.description){described++;continue;}
      if(!/^[a-f0-9]{64}$/.test(asset.sha256||''))continue;
      if(!groups.has(asset.sha256))groups.set(asset.sha256,asset);
    }
    const inProgress=new Set(),uncertain=new Set(),writeback=new Set();
    const active=[...jobs.values()].find(j=>['queued','running','pausing'].includes(j.status));
    for(const job of jobs.values()){
      if(job.root_id!==inventory.root_id)continue;
      for(const item of job.items){
        if(!groups.has(item.asset.sha256))continue;
        if(['queued','running','pausing'].includes(job.status)&&['pending','requesting','saving'].includes(item.status))inProgress.add(item.asset.sha256);
        if(item.status==='uncertain')uncertain.add(item.asset.sha256);
        if(item.status==='writeback_failed')writeback.add(item.asset.sha256);
      }
    }
    let assets=[...groups.values()].filter(a=>!inProgress.has(a.sha256)&&!uncertain.has(a.sha256)&&!writeback.has(a.sha256));
    if(data.max_images!==undefined){const n=Number(data.max_images);if(!Number.isInteger(n)||n<1||n>2000)throw new Error('每次处理数量应在 1—2000 张之间');assets=assets.slice(0,n);}
    if(assets.length>2000)throw new Error('本次待处理图片超过 2000 张，请按章节启动');
    let cacheHits=0;for(const asset of assets)if(await cached(asset.sha256,prepared))cacheHits++;
    return {inventory,prepared,assets,summary:{knowledge_id:inventory.knowledge_id,root_id:inventory.root_id,
      scope_title:inventory.scope_title,book_title:inventory.book_title,chapters:inventory.chapters,
      questions,described,pending:groups.size,selected:assets.length,cache_hits:cacheHits,new_requests:assets.length-cacheHits,model:prepared.config.model,
      in_progress:inProgress.size,uncertain:uncertain.size,writeback_pending:writeback.size,
      active_job:active?{id:active.id,status:active.status}:null}};
  }
  async function publish(job,item,result){
    item.status='saving';await persist(job);
    try {
      await call('knowledge_vision_publish',{id:job.knowledge_id,sha256:item.asset.sha256,description:result.description,model:result.model,cache_key:cacheKey(item.asset.sha256,job.prepared)});
      item.status=item.from_cache?'cached':'done';delete item.error;
    }catch(error){item.status='writeback_failed';item.error='描述已保存在本地，写回素材库失败：'+safe(error);}
    job.updated_at=time();await persist(job);
  }
  // On restart, do not silently repeat requests whose outcome cannot be known.
  for(const filename of await readdir(directory)){
    if(!/^vision_[a-f0-9]+\.json$/.test(filename))continue;
    const job=await read(path.join(directory,filename));
    if(['queued','running','pausing'].includes(job.status)){job.status='paused';job.message='工作台已重启，任务已暂停。继续只处理尚未发送的图片。';}
    for(const item of job.items){
      if(['requesting','saving'].includes(item.status)){
        const result=item.result||await cached(item.asset.sha256,job.prepared);
        if(result){await atomic(path.join(cacheDir,cacheKey(item.asset.sha256,job.prepared)+'.json'),result);item.from_cache=true;await publish(job,item,result);}
        else {item.status='uncertain';item.error='重启前已发出请求，处理结果待确认；不会自动重复发送。';}
      }
    }
    jobs.set(job.id,job);await persist(job);
  }
  async function process(job,item){
    try {
      let result=await cached(item.asset.sha256,job.prepared);
      if(result){item.from_cache=true;return await publish(job,item,result);}
      // A paused job may have been superseded by another chapter job or a manual edit.
      if(job.current_described?.has(item.asset.sha256)){item.status='cached';await persist(job);return;}
      if(closed||job.status!=='running'){item.status='pending';await persist(job);return;}
      item.status='requesting';item.started_at=time();item.phase='preparing';job.api_requests++;item.attempts=(item.attempts||0)+1;await persist(job);
      if(closed||job.status!=='running'){item.status='pending';item.attempts--;job.api_requests--;await persist(job);return;}
      let description;
      try {description=await vision.caption(item.asset,{...job.prepared,onPhase:async phase=>{
        if(item.status!=='requesting')return;item.phase=phase;if(phase==='requesting')item.request_started_at=time();await persist(job);
      }});}
      catch(error){
        if(error.kind==='rate_limit'&&item.attempts<3&&job.status==='running'&&!closed){
          item.status='pending';item.not_before=Date.now()+Math.max(0,error.retryAfterMs||1000);
          job.backoff_until=Math.max(job.backoff_until||0,item.not_before);job.message='服务限流，正在等待后继续（每张最多重试两次）';await persist(job);return;
        }
        item.status=error.kind==='uncertain'?'uncertain':'failed';item.error=safe(error);
        if(error.kind==='setup'){job.status='pausing';job.message='模型设置或权限有误，已停止发送新请求。修正设置后请新建任务；已有描述会保留。';}
        await persist(job);return;
      }
      result={description,model:job.prepared.config.model,saved_at:time()};
      // Keep the response in job state too: even a cache-write failure must not cause a second API request.
      item.result=result;item.status='saving';await persist(job);
      try {await atomic(path.join(cacheDir,cacheKey(item.asset.sha256,job.prepared)+'.json'),result);}
      catch(error){item.status='writeback_failed';item.error='识图完成，本地缓存写入失败：'+safe(error);await persist(job);return;}
      await publish(job,item,result);
    }catch(error){item.status=item.result?'writeback_failed':item.status==='requesting'&&item.attempts?'uncertain':'failed';item.error=safe(error);await persist(job);}
  }
  async function work(job){
    job.status='running';job.message='正在识别教材插图';job.updated_at=time();
    // Transient set is excluded from persistence by JSON serialization.
    const inventory=await call('knowledge_vision_inventory',{id:job.knowledge_id,chapter_id:job.chapter_id||null});
    job.current_described=new Set(inventory.assets.filter(a=>a.description).map(a=>a.sha256));await persist(job);
    async function worker(){
      while(!closed&&job.status==='running'){
        const item=job.items.find(i=>i.status==='pending');if(!item)return;
        if((job.backoff_until||0)>Date.now()){
          // Short waits make pause/shutdown responsive even for a long Retry-After.
          await retryDelay(Math.min(500,job.backoff_until-Date.now()));continue;
        }
        // Claim synchronously before yielding so both workers cannot select the same item.
        item.status='requesting';await process(job,item);
      }
    }
    await Promise.all(Array.from({length:Math.max(1,Math.min(2,concurrency))},worker));
    delete job.current_described;
    job.status=job.status==='pausing'||closed?'paused':job.items.some(i=>!finished.has(i.status))?'needs_attention':'completed';
    if(job.status==='completed')job.message='本次图片描述已完成';
    else if(job.status==='paused'&&!job.message.includes('设置'))job.message='已暂停。已发出的请求已保存，继续将处理尚未发送的图片。';
    else if(job.status==='needs_attention')job.message='本次处理结束，部分图片需要检查或重试';
    job.updated_at=time();await persist(job);
  }
  function kick(){
    if(closed||runner)return;
    const job=[...jobs.values()].find(j=>j.status==='queued');if(!job)return;
    runner=work(job).catch(async error=>{job.status='paused';job.message='队列已暂停：'+safe(error);await persist(job);})
      .finally(()=>{runner=null;kick();});
  }
  async function start(data){return serial(async()=>{
    if(closed)throw new Error('工作台正在关闭，请稍后刷新');
    if(typeof data.key!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(data.key))throw new Error('请求标识不正确');
    const previous=[...jobs.values()].find(j=>j.request_key===data.key);
    if(previous){if(previous.knowledge_id!==data.knowledge_id||(previous.chapter_id||null)!==(data.chapter_id||null))throw new Error('请求标识与原任务范围不一致');return publicJob(previous);}
    const active=[...jobs.values()].find(j=>['running','queued','pausing'].includes(j.status));
    if(active){if(active.knowledge_id===data.knowledge_id&&(active.chapter_id||null)===(data.chapter_id||null))return publicJob(active);throw new Error('已有识图任务正在执行，请先暂停它，或使用“优先本章”');}
    const p=await plan(data);if(p.summary.new_requests)await vision.ready();
    const job={id:'vision_'+randomUUID().replaceAll('-',''),request_key:data.key,knowledge_id:data.knowledge_id,root_id:p.inventory.root_id,
      chapter_id:data.chapter_id||null,scope_title:p.summary.scope_title,prepared:p.prepared,status:'queued',created_at:time(),updated_at:time(),api_requests:0,
      items:p.assets.map(asset=>({asset,status:'pending',attempts:0}))};
    jobs.set(job.id,job);await persist(job);kick();return publicJob(job);
  });}
  async function control(id,action,data={}){return serial(async()=>{
    const job=jobs.get(id);if(!job)throw new Error('找不到识图任务');
    if(action==='pause'){
      if(job.status==='queued')job.status='paused';else if(job.status==='running')job.status='pausing';
      job.message='正在暂停，等待已发出的请求保存结果';
    }else if(action==='priority'){
      const inventory=await call('knowledge_vision_inventory',{id:job.knowledge_id,chapter_id:data.chapter_id});
      if(!data.chapter_id)throw new Error('请先选择要优先处理的章节');
      const selected=new Set(inventory.assets.map(a=>a.sha256));
      job.items.sort((a,b)=>Number(selected.has(b.asset.sha256))-Number(selected.has(a.asset.sha256)));
      job.message='已将所选章节尚未发送的图片排在前面';
    }else{
      if(!['resume','retry_failed','retry_uncertain','save_cached','reconcile'].includes(action))throw new Error('未知队列操作');
      if(['running','pausing','queued'].includes(job.status))throw new Error('请等待当前识别完成或暂停后操作');
      if([...jobs.values()].some(j=>j.id!==id&&['running','pausing','queued'].includes(j.status)))throw new Error('请先暂停正在执行的另一个任务');
      // Writeback-only recovery never sends a model request, even if pending work exists.
      if(action==='reconcile'){
        const inventory=await call('knowledge_vision_inventory',{id:job.knowledge_id,chapter_id:job.chapter_id||null});
        const existing=new Set(inventory.assets.filter(a=>a.description).map(a=>a.sha256));
        for(const item of job.items)if(!finished.has(item.status)&&existing.has(item.asset.sha256)){
          item.status='cached';item.resolved_by='existing_description';delete item.error;
        }
        job.status=job.items.every(i=>finished.has(i.status))?'completed':'needs_attention';
        job.message='已核对本地素材库描述，未发送新识图请求';
      }else if(action==='save_cached'){
        for(const item of job.items.filter(i=>i.status==='writeback_failed')){
          const result=item.result||await cached(item.asset.sha256,job.prepared);
          if(result){await atomic(path.join(cacheDir,cacheKey(item.asset.sha256,job.prepared)+'.json'),result);item.from_cache=true;await publish(job,item,result);}
        }
        job.status=job.items.every(i=>finished.has(i.status))?'completed':'paused';
      }else{
        if(action!=='resume')for(const item of job.items)if(item.status===(action==='retry_failed'?'failed':'uncertain')){item.status='pending';item.attempts=0;delete item.error;}
        if(job.items.some(i=>i.status==='pending'))await vision.ready();
        job.status='queued';job.message='等待继续识别';
      }
    }
    job.updated_at=time();await persist(job);kick();return publicJob(job);
  });}
  async function dispose(){closed=true;for(const job of jobs.values())if(['running','queued'].includes(job.status)){job.status=job.status==='running'?'pausing':'paused';await persist(job);}if(runner)await runner;await writer;}
  return {preview:async data=>(await plan(data)).summary,list,start,control,dispose};
}
