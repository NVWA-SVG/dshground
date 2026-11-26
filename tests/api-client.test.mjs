import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import path from 'node:path';
import {apiJson,listApiModels,parseRetryAfter} from '../plugins/teacher-workbench/api-client.mjs';
import {apiRuntimeFor} from '../plugins/teacher-workbench/api-runtime.mjs';
import {createTextModelService,DEFAULT_TEXT_MODEL} from '../plugins/teacher-workbench/text-model-service.mjs';
import {createVisionService} from '../plugins/teacher-workbench/vision-service.mjs';
import {providerFailure} from '../plugins/teacher-workbench/task-failures.mjs';
async function setup(t){await mkdir('.test-runs',{recursive:true});const root=await mkdtemp(path.resolve('.test-runs/api-'));await mkdir(path.join(root,'config'));const runtime=apiRuntimeFor(root);await runtime.configure({min_interval_ms:0,rate_limit_cooldown_ms:1000});t.after(()=>runtime.dispose());const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);return {root,runtime};}

test('full Qwen IDs save atomically and invalid credentials or unregistered names do not mutate settings',async t=>{
 const {root}=await setup(t),writes=[];const ctx={llm:{listModels:async()=>[{id:DEFAULT_TEXT_MODEL}]},credentials:{describe:async()=>({configured:true}),set:async(name,value)=>writes.push(name)}};
 const service=createTextModelService({ctx,root});const result=await service.save({model:DEFAULT_TEXT_MODEL,api_key:'synthetic-key'});assert.equal(result.model,DEFAULT_TEXT_MODEL);assert.deepEqual(writes,['SILICONFLOW_API_KEY']);
 const saved=await readFile(path.join(root,'config/teacher-model.json'),'utf8');assert.ok(!saved.includes('synthetic-key'));
 await assert.rejects(service.save({model:'different/model',api_key:'another-test-key'}),/未接入/);await assert.rejects(service.save({model:DEFAULT_TEXT_MODEL,api_key:'bad-key，'}),/中文符号/);
 assert.equal(writes.length,1);assert.equal(await readFile(path.join(root,'config/teacher-model.json'),'utf8'),saved);
});

test('connection check authenticates the remote endpoint instead of trusting a static catalog',async t=>{
 const {root}=await setup(t);let calls=0;
 const service=createTextModelService({root,ctx:{llm:{listModels:async()=>[{id:DEFAULT_TEXT_MODEL}]},credentials:{resolve:async()=>({value:'synthetic-key'})}}});
 globalThis.fetch=async(url,options)=>{calls++;assert.equal(url,'https://api.siliconflow.cn/v1/models');assert.equal(options.method,'GET');assert.equal(options.redirect,'error');return new Response('secret-provider-body',{status:401});};
 await assert.rejects(service.testConnection(),e=>e.status===401&&!e.message.includes('secret-provider-body'));
 globalThis.fetch=async()=>{calls++;return Response.json({data:[{id:DEFAULT_TEXT_MODEL}]});};
 const result=await service.testConnection();assert.equal(calls,2);assert.equal(result.tool_calling_verified,false);assert.equal(result.paid_generation,false);assert.equal(result.credential_validated,true);
});

test('simultaneous model-list checks coalesce without caching a stale authentication result',async t=>{
 const {root}=await setup(t);let count=0;
 globalThis.fetch=async()=>{count++;await new Promise(r=>setTimeout(r,15));return Response.json({data:[{id:'synthetic/model'}]});};
 const options={root,url:'https://api.example.invalid/v1/models',key:'synthetic-key'};
 await Promise.all([listApiModels(options),listApiModels(options)]);assert.equal(count,1);await listApiModels(options);assert.equal(count,2);
});

test('quota rejection carries structured facts and never leaks provider body or credentials',async t=>{
 const {root}=await setup(t);globalThis.fetch=async()=>new Response(JSON.stringify({message:'TPM limit reached; sk-private-body'}),{status:429,headers:{'Retry-After':'2','x-siliconcloud-trace-id':'trace_test_1'}});
 await assert.rejects(apiJson({root,url:'https://api.siliconflow.cn/v1/chat/completions',key:'synthetic-key',method:'POST',model:'test-model',body:{}}),e=>e.status===429&&e.retryAfterMs===2000&&e.request_id==='trace_test_1'&&e.failure.limit_type==='TPM'&&!e.message.includes('sk-private'));
 assert.equal(providerFailure({status:402,message:'quota'}).code,'insufficient_balance');
 assert.equal(parseRetryAfter('garbage'),null);assert.equal(parseRetryAfter('2'),2000);assert.equal(parseRetryAfter(new Date(5000).toUTCString(),1000),4000);
 assert.equal(parseRetryAfter('600'),600000,'Do not retry earlier than the provider asked');
});

test('oversized, empty and HTML responses fail locally without a replay',async t=>{
 const {root}=await setup(t);let calls=0;
 for(const [body,options,code] of [['x'.repeat(101),{maxBytes:100},'response_too_large'],['',{},'empty_response'],['<html>private</html>',{},'invalid_response']]){
  globalThis.fetch=async()=>{calls++;return new Response(body);};
  await assert.rejects(apiJson({root,url:'https://api.example.invalid/v1/models',key:'synthetic-key',...options}),e=>e.code===code&&!e.message.includes('<html>'));
 }
 assert.equal(calls,3);
});

test('body timeout is bounded even when the transport ignores abort, and late replies cannot resend',async t=>{
 const {root}=await setup(t);let calls=0,release;const blocked=new Promise(r=>release=r);
 globalThis.fetch=async()=>{calls++;return {ok:true,text:()=>blocked};};
 await assert.rejects(apiJson({root,url:'https://api.example.invalid/v1/chat/completions',key:'synthetic-key',method:'POST',body:{},timeoutMs:30}),e=>e.code==='api_timeout'&&e.kind==='uncertain');
 release('{}');await new Promise(r=>setTimeout(r,5));assert.equal(calls,1);
});

test('a known HTTP authentication failure remains visible when its body stalls',async t=>{
 const {root}=await setup(t);let release;const blocked=new Promise(r=>release=r);
 globalThis.fetch=async()=>({ok:false,status:401,text:()=>blocked});
 await assert.rejects(apiJson({root,url:'https://api.example.invalid/v1/models',key:'synthetic-key',timeoutMs:30}),e=>e.status===401&&e.code==='authentication'&&e.kind==='setup');release('{}');
});

test('model scheduling bounds concurrency, keeps different models independent and cancels queued work',async t=>{
 const {runtime}=await setup(t);const release=await runtime.acquire('model-a');const controller=new AbortController();
 let admitted=false;const queued=runtime.acquire('model-a',controller.signal).then(value=>{admitted=true;return value;});
 const independent=await runtime.acquire('model-b');assert.equal(admitted,false);independent();controller.abort();await assert.rejects(queued,/abort/i);release();
 const next=await runtime.acquire('model-a');next();assert.equal((await runtime.status()).active,0);assert.equal((await runtime.status()).waiting,0);
});

test('429 cooldown holds later calls but does not retry the rejected call',async t=>{
 const {root,runtime}=await setup(t);let calls=0;globalThis.fetch=async()=>{calls++;return new Response('',{status:429});};
 await assert.rejects(apiJson({root,url:'https://api.siliconflow.cn/v1/chat/completions',key:'synthetic-key',model:'test-model',method:'POST',body:{}}),e=>e.status===429);
 const controller=new AbortController();const pending=runtime.acquire('siliconflow:test-model',controller.signal);controller.abort();await assert.rejects(pending);assert.equal(calls,1);
});

test('vision independent keys cannot overwrite the shared text/image key',async t=>{
 const {root}=await setup(t),writes=[];
 const service=createVisionService({root,ctx:{credentials:{set:async(name)=>writes.push(name),describe:async()=>({configured:true})}},call:async()=>{}});
 await assert.rejects(service.saveSettings({api_key:'synthetic-vision-key'}),/共享模式/);assert.deepEqual(writes,[]);
 const result=await service.saveSettings({credential_source:'separate',api_key:'synthetic-vision-key',base_url:'https://api.example.invalid/v1'});
 assert.deepEqual(writes,['VISION_API_KEY']);assert.equal(result.credential_source,'separate');assert.equal(result.api_key,undefined);
 await assert.rejects(service.saveSettings({credential_source:'shared'}),/独立密钥/);
});

test('vision HTTP 402 is a setup failure that stops queue consumption',async t=>{
 const {root}=await setup(t);globalThis.fetch=async()=>new Response('{}',{status:402});
 const service=createVisionService({root,ctx:{credentials:{resolve:async()=>({value:'synthetic-key'})}},call:async()=>({path:path.join(root,'config/api-policy.json')})});
 await assert.rejects(service.caption({file:'fake.png'},await service.snapshot()),e=>e.kind==='setup'&&e.status===402&&e.code==='insufficient_balance');
});
