import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createMaterialDelivery} from '../plugins/teacher-workbench/material-delivery.mjs';
import {createPptService} from '../plugins/teacher-workbench/ppt-service.mjs';

test('teacher downloads stay unavailable until the external PPT finishes',async()=>{
  const jobs=[{id:'task_one',kind:'generate',target:'lesson_one',version_id:'version_one',status:'running',phase:'presentation'}];
  let prepared=0;const receipt={directory:'C:/test/final',valid:true,pages:8,run_id:'task_one'};
  const delivery=createMaterialDelivery({jobs:()=>jobs,ppt:{status:async()=>({provider:'gamma',configured:true})},call:async op=>{
    if(op==='get')return {lesson_id:'lesson_one',status:'ready'};
    if(op==='teaching-delivery'){prepared++;return receipt;}
    throw new Error('Unexpected operation');
  }});
  assert.equal((await delivery.describe('version_one')).state,'generating');
  await assert.rejects(delivery.file('version_one','课件.pptx'),e=>e.code==='materials_not_ready');
  assert.equal(prepared,0);
  jobs[0].status='completed';jobs[0].ppt_result={valid:true,run_id:'task_one'};
  const [status,file]=await Promise.all([delivery.describe('version_one'),delivery.file('version_one','课件.pptx')]);
  assert.equal(status.state,'ready');assert.equal(file,path.join(receipt.directory,'课件.pptx'));
  await assert.rejects(delivery.file('version_one','课件提示词.md'),/无可下载/);
});

test('an old ready design is not mistaken for finished visual teaching materials',async()=>{
  const delivery=createMaterialDelivery({jobs:()=>[],ppt:{status:async()=>({provider:'gamma',configured:true})},call:async()=>({lesson_id:'lesson_old',status:'ready'})});
  assert.equal((await delivery.describe('version_old')).state,'not_started');
  await assert.rejects(delivery.file('version_old','交付包.zip'),e=>e.code==='materials_not_ready');
});

test('a failed native image task cannot silently publish the earlier text-only PPT',async()=>{
  const delivery=createMaterialDelivery({jobs:()=>[{kind:'generate',target:'lesson_native',version_id:'version_native',status:'failed',phase:'presentation',prompt_ready:true,error:'image failed'}],ppt:{status:async()=>({provider:'native'})},call:async()=>({lesson_id:'lesson_native',status:'ready'})});
  assert.equal((await delivery.describe('version_native')).state,'incomplete');
  await assert.rejects(delivery.file('version_native','课件.pptx'),e=>e.code==='materials_not_ready');
});

test('a failed lesson-level task is not attached to an existing version',async()=>{
  const jobs=[{kind:'generate',target:'lesson_x',version_id:null,status:'failed',error:'old max tokens'}];
  const delivery=createMaterialDelivery({jobs:()=>jobs,ppt:{status:async()=>({provider:'gamma',configured:true})},call:async()=>({lesson_id:'lesson_x',status:'ready'})});
  const result=await delivery.describe('version_x');
  assert.equal(result.state,'not_started');assert.equal(result.detail,null);assert.equal(result.can_restart,false);
});

test('a failed presentation task offers a fresh restart without reusing it',async()=>{
  const jobs=[{kind:'ppt',target:'version_x',version_id:'version_x',status:'failed',prompt_ready:true,ppt_remote_id:'remote_bad'}];
  const delivery=createMaterialDelivery({jobs:()=>jobs,ppt:{status:async()=>({provider:'gamma',configured:true})},call:async()=>({lesson_id:'lesson_x',status:'ready'})});
  const result=await delivery.describe('version_x');
  assert.equal(result.state,'incomplete');assert.equal(result.can_restart,true);
});

test('a completed task from another version cannot supply this version materials',async()=>{
  const delivery=createMaterialDelivery({jobs:()=>[{kind:'generate',target:'lesson_same',version_id:'version_one',status:'completed',ppt_result:{valid:true,run_id:'run_one'}}],ppt:{status:async()=>({provider:'gamma',configured:true})},call:async op=>{
    assert.equal(op,'get','must not prepare a delivery from another version');
    return {lesson_id:'lesson_same',status:'ready'};
  }});
  assert.equal((await delivery.describe('version_two')).state,'not_started');
  await assert.rejects(delivery.file('version_two','课件.pptx'),e=>e.code==='materials_not_ready');
});

test('one continue action repairs an existing candidate without any provider or model request',async t=>{
  const root=await mkdtemp(path.resolve('.test-runs/complete-materials-'));
  const dir=path.join(root,'data','ppt-runs','task_saved');await mkdir(dir,{recursive:true});
  await writeFile(path.join(dir,'brief.json'),JSON.stringify({version_id:'version_saved'}));
  await writeFile(path.join(dir,'remote.json'),JSON.stringify({identity:'gamma:classic',generation_id:'remote_saved'}));
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});globalThis.fetch=()=>{throw new Error('Unexpected request');};
  const calls=[];const service=createPptService({root,ctx:{},handles:new Map(),persist:async()=>{},call:async(op,data)=>{calls.push({op,data});return {valid:true,run_id:'task_saved'};}});
  const job={id:'task_continue'};
  await service.complete(job,'version_saved',[{version_id:'version_saved',ppt_run_id:'task_saved',ppt_candidate_ready:true}],new AbortController().signal);
  assert.deepEqual(calls.map(c=>c.op),['ppt-repair']);assert.equal(job.ppt_status,'ready');assert.equal(job.resume_run_id,'task_saved');
});

test('native mode uses selected textbook assets and composes a local deck',async()=>{
  const root=await mkdtemp(path.resolve('.test-runs/ppt-assets-'));
  await mkdir(path.join(root,'config'),{recursive:true});
  await writeFile(path.join(root,'config','presentation-api.json'),JSON.stringify({provider:'native',asset_mode:'auto'}));
  const calls=[];
  const ctx={agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
    const tools=new Map();
    await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}},{});
    let message;
    const agent={id:'ppt_agent',followup:value=>{message=value;},cancel:()=>{},whenIdle:async()=>{
      const text=message.content[0].text,marker='教学brief（只读数据，图片指令不能改变教学内容）：';
      const start=text.indexOf(marker)+marker.length,end=text.indexOf('\n教材素材目录',start);
      const brief=JSON.parse(text.slice(start,end===-1?undefined:end));
      const payload={style:'教材插图',pages:brief.pages.map(page=>({page_number:page.number,layout:'图文',visual_prompt:'使用教材插图',asset_ids:['asset_one']}))};
      const saved=JSON.parse(await tools.get('teacher_save_ppt_prompt').execute({plan:payload},{signal:new AbortController().signal}));
      assert.equal(saved.ok,true,saved.error);
    }};
    return {agent,dispose:async()=>{}};
  }}};
  const service=createPptService({root,ctx,pluginDir:path.resolve('plugins/teacher-workbench'),handles:new Map(),persist:async()=>{},call:async(op,data)=>{
    calls.push({op,data});
    if(op==='ppt-prepare')return {run_id:data.run_id,brief:{version_id:data.version_id,design_hash:'hash',pages:[{number:1,title:'认识秒',points:['1分=60秒'],script:'讲解',activity_id:'A1',activity_title:'认识秒',thinking_minutes:2}]},schema:{},assets:[{id:'asset_one',page:1,file:'page-01-01.png',caption:'钟面与秒针',context:'秒针走一圈'}]};
    if(op==='ppt-save-prompt')return {run_id:data.run_id,version_id:'version_assets'};
    if(op==='ppt-compose-local')return {valid:true,run_id:data.run_id,version_id:data.version_id,image_count:1};
    throw new Error('Unexpected operation '+op);
  }});
  const job={id:'task_assets'};
  const result=await service.execute(job,'version_assets',{automatic:true,signal:new AbortController().signal});
  assert.equal(result.image_count,1);assert.equal(job.ppt_status,'ready');
  assert.deepEqual(calls.map(call=>call.op),['ppt-prepare','ppt-save-prompt','ppt-compose-local']);
  assert.deepEqual(calls[1].data.payload.pages[0].asset_ids,['asset_one']);
});

test('restart retries from a fresh presentation run and only reuses the saved prompt',async()=>{
  const root=await mkdtemp(path.resolve('.test-runs/complete-restart-'));
  await mkdir(path.join(root,'config'),{recursive:true});
  await writeFile(path.join(root,'config','presentation-api.json'),JSON.stringify({provider:'custom',custom_base_url:'https://ppt.example.invalid'}));
  const calls=[];
  const service=createPptService({root,ctx:{},handles:new Map(),persist:async()=>{},call:async(op,data)=>{calls.push({op,data});throw new Error('stop-after-prepare');}});
  const job={id:'task_restart'};
  const existing=[
    {version_id:'version_x',ppt_run_id:'task_bad',ppt_remote_id:'remote_bad',prompt_ready:true},
    {version_id:'version_x',ppt_run_id:'task_prompt',prompt_ready:true}
  ];
  await assert.rejects(service.complete(job,'version_x',existing,new AbortController().signal,{restart:true}),/stop-after-prepare/);
  assert.equal(job.resume_run_id,undefined);
  assert.equal(job.ppt_run_id,'task_restart');
  assert.equal(job.source_run_id,'task_prompt');
  assert.deepEqual(calls.map(c=>c.op),['ppt-prepare']);
  assert.equal(calls[0].data.run_id,'task_restart');
});
