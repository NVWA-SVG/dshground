import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,readdir} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createVisionQueue} from '../plugins/teacher-workbench/vision-queue.mjs';
import {createVisionService} from '../plugins/teacher-workbench/vision-service.mjs';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const sha=i=>createHash('sha256').update(String(i)).digest('hex');
async function until(check){for(let i=0;i<400;i++){if(await check())return;await sleep(10);}throw new Error('Timed out');}
async function fixture(t,{count=12,caption,publish,concurrency=2}={}){
  await mkdir('.test-runs',{recursive:true});const root=await mkdtemp(path.resolve('.test-runs/vision-queue-'));
  const assets=Array.from({length:count},(_,i)=>({id:'asset_'+i,knowledge_id:'knowledge_book',sha256:sha(i),kind:'scene',file:i+'.png',source_page:i+1}));
  let requests=0,active=0,peak=0;const saved=new Map();
  const vision={snapshot:async()=>({config:{base_url:'https://example.invalid/v1',model:'mock-model'},sampling:{temperature:.1},systemPrompt:'visible facts only'}),ready:async()=>{},caption:async asset=>{
    requests++;active++;peak=Math.max(peak,active);try{if(caption)return await caption(asset,requests);await sleep(10);return '图片描述 '+asset.id;}finally{active--;}
  }};
  const call=async(op,data)=>{
    if(op==='knowledge_vision_inventory')return {knowledge_id:data.id,root_id:'knowledge_book',scope_title:'测试教材',book_title:'测试教材',chapters:[{id:'chapter_1',title:'前半册',start_page:1,end_page:6},{id:'chapter_2',title:'后半册',start_page:7,end_page:12}],assets:assets.filter(a=>!data.chapter_id||(data.chapter_id==='chapter_1'?a.source_page<=6:a.source_page>6)).map(a=>({...a,description:saved.get(a.sha256)}))};
    if(op==='knowledge_vision_publish'){if(publish)await publish(data);saved.set(data.sha256,data.description);return {saved:true};}
    throw new Error('Unexpected '+op);
  };
  const q=await createVisionQueue({root,vision,call,concurrency});t.after(()=>q.dispose());
  return {q,root,assets,vision,call,saved,get requests(){return requests;},get peak(){return peak;}};
}
const start=f=>f.q.start({knowledge_id:'knowledge_book',key:'test_start'});
const done=f=>until(()=>['completed','needs_attention','paused'].includes(f.q.list()[0]?.status));

test('one start handles all pictures with global concurrency two and excludes described/questions/duplicates',async t=>{
  const f=await fixture(t);f.assets.push({...f.assets[0],id:'duplicate'});f.assets[1].description='已有';f.saved.set(f.assets[1].sha256,'已有');f.assets[2].kind='question';
  const p=await f.q.preview({knowledge_id:'knowledge_book'});assert.equal(p.new_requests,10);assert.equal(f.requests,0);
  const job=await start(f);assert.equal((await start(f)).id,job.id);await done(f);
  assert.equal(f.requests,10);assert.equal(f.peak,2);assert.equal(f.q.list()[0].counts.done,10);
  assert.ok(!JSON.stringify(f.q.list()).includes('systemPrompt'));
});

test('pause drains in-flight requests; resume only sends the remaining pictures',async t=>{
  let release;const blocker=new Promise(r=>release=r);const f=await fixture(t,{caption:async()=>{await blocker;return '图中可见钟面';}});
  const job=await start(f);await until(()=>f.requests===2);await f.q.control(job.id,'pause');assert.equal(f.q.list()[0].status,'pausing');release();await done(f);
  assert.equal(f.requests,2);assert.equal(f.q.list()[0].counts.pending,10);
  await f.q.control(job.id,'resume');await done(f);assert.equal(f.requests,12);
});

test('cache survives a new queue process and is reused across book IDs without another model call',async t=>{
  const f=await fixture(t,{count:2});await start(f);await done(f);await f.q.dispose();f.saved.clear();
  const q=await createVisionQueue({root:f.root,vision:f.vision,call:f.call});t.after(()=>q.dispose());
  assert.equal((await q.preview({knowledge_id:'knowledge_other'})).cache_hits,2);
  await q.start({knowledge_id:'knowledge_other',key:'other_book'});await until(()=>q.list()[0].status==='completed');assert.equal(f.requests,2);
});

test('writeback failure is recovered from durable cache without a model request',async t=>{
  let fail=true;const f=await fixture(t,{count:1,publish:async()=>{if(fail)throw new Error('database busy');}});
  const job=await start(f);await done(f);assert.equal(f.q.list()[0].counts.writeback_failed,1);
  fail=false;await f.q.control(job.id,'save_cached');assert.equal(f.q.list()[0].status,'completed');assert.equal(f.requests,1);
});

test('restart marks sent requests uncertain and does not resume paid work automatically',async t=>{
  const f=await fixture(t,{count:3});const p=await f.vision.snapshot();const dir=path.join(f.root,'data/vision-queue/jobs');
  const job={id:'vision_abcdef',request_key:'crash',knowledge_id:'knowledge_book',root_id:'knowledge_book',prepared:p,status:'running',created_at:new Date().toISOString(),api_requests:1,items:f.assets.map((asset,i)=>({asset,status:i===0?'requesting':'pending',attempts:i===0?1:0}))};
  await writeFile(path.join(dir,job.id+'.json'),JSON.stringify(job));const q=await createVisionQueue({root:f.root,vision:f.vision,call:f.call});t.after(()=>q.dispose());
  assert.equal(q.list()[0].status,'paused');assert.equal(q.list()[0].counts.uncertain,1);assert.equal(f.requests,0);
  await q.control(job.id,'resume');await until(()=>q.list()[0].status==='needs_attention');assert.equal(f.requests,2);
  await q.control(job.id,'retry_uncertain');await until(()=>q.list()[0].status==='completed');assert.equal(f.requests,3);
});

test('uncertain responses are not automatically retried; definite failures require a retry action',async t=>{
  const f=await fixture(t,{count:2,caption:async(asset,n)=>{if(n<=2)throw Object.assign(new Error('mock error'),{kind:asset.source_page===1?'uncertain':'failed'});return '已恢复';}});
  const job=await start(f);await done(f);assert.equal(f.requests,2);assert.equal(f.q.list()[0].counts.uncertain,1);
  await f.q.control(job.id,'retry_failed');await done(f);assert.equal(f.requests,3);assert.equal(f.q.list()[0].counts.uncertain,1);
  await f.q.control(job.id,'retry_uncertain');await done(f);assert.equal(f.requests,4);
});

test('credential error pauses subsequent work and explicit rate limits retry at most twice',async t=>{
  const f=await fixture(t,{count:6,concurrency:1,caption:async()=>{throw Object.assign(new Error('unauthorized'),{kind:'setup'});}});
  await start(f);await done(f);assert.equal(f.requests,1);assert.equal(f.q.list()[0].status,'paused');
  const g=await fixture(t,{count:1,caption:async()=>{throw Object.assign(new Error('limited'),{kind:'rate_limit',retryAfterMs:1});}});
  await start(g);await done(g);assert.equal(g.requests,3);assert.equal(g.q.list()[0].counts.failed,1);
});

test('priority moves pending chapter images ahead and another active job is rejected',async t=>{
  let release;const blocker=new Promise(r=>release=r),order=[];
  const f=await fixture(t,{caption:async asset=>{order.push(asset.source_page);await blocker;return '图中有钟';}});
  const job=await start(f);await until(()=>f.requests===2);
  await assert.rejects(f.q.start({knowledge_id:'knowledge_other',key:'another'}),/已有/);
  await f.q.control(job.id,'priority',{chapter_id:'chapter_2'});release();await done(f);assert.equal(order[2],7);
});

test('chapter boundaries and per-task quantity cap limit spending scope',async t=>{
  const f=await fixture(t);const p=await f.q.preview({knowledge_id:'knowledge_book',chapter_id:'chapter_2'});assert.equal(p.pending,6);
  await f.q.start({knowledge_id:'knowledge_book',chapter_id:'chapter_2',max_images:3,key:'chapter'});await done(f);assert.equal(f.requests,3);
});

test('real caption service verifies image hash locally and classifies HTTP and timeout errors',async t=>{
  const f=await fixture(t,{count:0});await mkdir(path.join(f.root,'config'),{recursive:true});const file=path.join(f.root,'test.png');await writeFile(file,'test bytes');
  const service=createVisionService({root:f.root,ctx:{credentials:{resolve:async()=>({value:'test-secret'})}},call:async()=>({path:file})});
  const p=await service.snapshot(),asset={file:'test.png',sha256:sha('wrong')};let calls=0;const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  globalThis.fetch=async()=>{calls++;return new Response('',{status:429,headers:{'Retry-After':'2'}});};
  await assert.rejects(service.caption(asset,p),/变化/);assert.equal(calls,0);delete asset.sha256;
  await assert.rejects(service.caption(asset,p),e=>e.kind==='rate_limit'&&e.retryAfterMs===2000);
  globalThis.fetch=async()=>{throw new Error('timeout with sk-secret');};
  await assert.rejects(service.caption(asset,p),e=>e.kind==='uncertain'&&!e.message.includes('sk-secret'));
});

test('preview reserves active and uncertain pictures so a fresh start cannot silently resend them',async t=>{
  let release;const blocker=new Promise(r=>release=r);
  const f=await fixture(t,{count:1,caption:async()=>{await blocker;throw Object.assign(new Error('timeout'),{kind:'uncertain'});}});
  await start(f);await until(()=>f.requests===1);
  let p=await f.q.preview({knowledge_id:'knowledge_book'});assert.equal(p.pending,1);assert.equal(p.in_progress,1);assert.equal(p.new_requests,0);assert.ok(p.active_job);
  release();await done(f);p=await f.q.preview({knowledge_id:'knowledge_book'});assert.equal(p.uncertain,1);assert.equal(p.selected,0);assert.equal(p.new_requests,0);
});

test('reconcile a locally completed description ends the old uncertain task without a paid request',async t=>{
  const f=await fixture(t,{count:1,caption:async()=>{throw Object.assign(new Error('timeout'),{kind:'uncertain'});}});
  const job=await start(f);await done(f);f.saved.set(f.assets[0].sha256,'本地复核后补充的描述');
  await f.q.control(job.id,'reconcile');assert.equal(f.requests,1);assert.equal(f.q.list()[0].status,'completed');assert.equal(f.q.list()[0].counts.done,1);
});

for(const stall of ['fetch','body','preflight'])test(`whole-operation deadline bounds stalled ${stall} even when cancellation is ignored`,async t=>{
  const f=await fixture(t,{count:0});const file=path.join(f.root,'test.png');await writeFile(file,'test bytes');
  let release,calls=0,signal;const blocker=new Promise(r=>release=r),original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  globalThis.fetch=async(_url,options)=>{calls++;signal=options.signal;if(stall==='fetch')await blocker;return {ok:true,text:async()=>{if(stall==='body')await blocker;return JSON.stringify({choices:[{message:{content:'迟到的图片描述'}}]});}};};
  const service=createVisionService({root:f.root,requestTimeoutMs:40,ctx:{credentials:{resolve:async()=>({value:'mock-key'})}},call:async()=>{if(stall==='preflight')await blocker;return {path:file};}});
  const prepared=await service.snapshot(),started=Date.now();
  await assert.rejects(service.caption({file:'test.png'},prepared),error=>error.kind==='uncertain'&&error.code==='vision_timeout');
  assert.ok(Date.now()-started<1500);if(signal)assert.equal(signal.aborted,true);release();await sleep(25);
  assert.equal(calls,stall==='preflight'?0:1);
});
