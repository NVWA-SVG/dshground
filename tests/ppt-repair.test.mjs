import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
import {createPptService} from '../plugins/teacher-workbench/ppt-service.mjs';
import {DEFAULT_PPT_CONFIG} from '../plugins/teacher-workbench/ppt-providers.mjs';

test('local repair uses the existing download with no model, provider or credential calls',async t=>{
  const root=await mkdtemp(path.resolve('.test-runs/ppt-local-repair-'));
  const runId='task_existing',versionId='version_existing';
  const dir=path.join(root,'data','ppt-runs',runId);await mkdir(dir,{recursive:true});
  await writeFile(path.join(dir,'brief.json'),JSON.stringify({version_id:versionId}));
  await writeFile(path.join(dir,'remote.json'),JSON.stringify({identity:'gamma:classic',generation_id:'remote_existing',phase:'downloaded',credits:{deducted:8}}));
  const original=globalThis.fetch;globalThis.fetch=()=>{throw new Error('Unexpected network request');};t.after(()=>{globalThis.fetch=original;});
  const calls=[],persisted=[];
  const service=createPptService({ctx:{credentials:{resolve:()=>{throw new Error('Unexpected credential resolution');}},agents:{create:()=>{throw new Error('Unexpected model call');}}},root,handles:new Map(),
    call:async(op,data)=>{calls.push({op,data});return {valid:true,local_text_repairs:[{page_number:2}]};},persist:async job=>persisted.push({...job})});
  const job={id:'task_local',resume_run_id:runId};const result=await service.repair(job,versionId);
  assert.equal(result.valid,true);assert.equal(calls.length,1);assert.equal(calls[0].op,'ppt-repair');
  assert.equal(calls[0].data.run_id,runId);assert.equal(calls[0].data.remote_id,'remote_existing');
  assert.equal(calls[0].data.credits.deducted,8);assert.equal(job.ppt_status,'ready');assert.equal(job.ppt_result,result);
  assert.equal(JSON.parse(await readFile(path.join(dir,'remote.json'),'utf8')).phase,'completed');
  await assert.rejects(service.repair({id:'task_wrong',resume_run_id:runId},'version_other'),/其他教学版本/);
});

test('Gamma text mismatch triggers strict local restoration without another creation request',async t=>{
  const root=await mkdtemp(path.resolve('.test-runs/ppt-auto-repair-'));
  await mkdir(path.join(root,'config'));const dir=path.join(root,'data','ppt-runs','task_auto');await mkdir(dir,{recursive:true});
  await writeFile(path.join(root,'config','presentation-api.json'),JSON.stringify({...DEFAULT_PPT_CONFIG,provider:'gamma'}));
  const brief={version_id:'version_auto',pages:[{number:1,title:'标题',points:['教学原文']}]};
  const plan={style:'清晰',pages:[{page_number:1,layout:'图文',visual_prompt:'教学插画'}]};
  await writeFile(path.join(dir,'visual-plan.json'),JSON.stringify(plan));
  let creates=0,ops=[];const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  globalThis.fetch=async(url,options)=>{
    if(options.method==='POST'){creates++;return Response.json({generationId:'remote_auto'});}
    if(String(url).includes('/generations/'))return Response.json({status:'completed',exportUrl:'https://93.184.216.34/deck.pptx',credits:{deducted:2}});
    return new Response(Buffer.from('PK-test-only'));
  };
  const service=createPptService({root,ctx:{credentials:{resolve:async()=>({value:'test-only-key'})}},handles:new Map(),persist:async()=>{},call:async(op,data)=>{
    ops.push(op);
    if(op==='ppt-prepare')return {brief};
    if(op==='ppt-read-prompt')return {brief,plan,markdown:'提示词'};
    if(op==='ppt-accept'){const error=new Error('Mismatch');error.code='ppt_text_mismatch';throw error;}
    if(op==='ppt-repair'){assert.equal(data.credits.deducted,2);return {valid:true,local_text_repairs:[{page_number:1}]};}
    throw new Error('Unexpected operation '+op);
  }});
  const job={id:'task_auto'};const result=await service.execute(job,'version_auto');
  assert.equal(result.valid,true);assert.equal(creates,1);assert.deepEqual(ops.slice(-2),['ppt-accept','ppt-repair']);
  assert.equal(job.ppt_status,'ready');assert.equal(job.ppt_candidate_ready,true);
});
