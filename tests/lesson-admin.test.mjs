import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdir,mkdtemp,writeFile,cp,access,readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bridge,apply as applyWorkbench} from '../plugins/teacher-workbench/index.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const exists=async target=>{try{await access(target);return true;}catch{return false;}};

test('deleting a course removes its versions, delivered files, job records and presentation runs',async t=>{
  const workspace=await mkdtemp(path.join(root,'.test-runs','lesson-admin-'));
  await mkdir(path.join(workspace,'tools'));await mkdir(path.join(workspace,'config'));
  await cp(path.join(root,'backend'),path.join(workspace,'backend'),{recursive:true});
  await cp(path.join(root,'tools','dsh_bridge.py'),path.join(workspace,'tools','dsh_bridge.py'));
  await cp(path.join(root,'config','runtime.json'),path.join(workspace,'config','runtime.json'));
  const fixture=async name=>JSON.parse(await readFile(path.join(root,'examples','subject',name+'.json'),'utf8'));
  const lesson=await bridge(workspace,'create',{payload:await fixture('request'),key:'create'});
  const version=await bridge(workspace,'save_design',{id:lesson.lesson_id,payload:await fixture('design'),key:'design'});
  const exported=await bridge(workspace,'export',{id:version.version_id});
  const runDirectory=path.join(workspace,'data','ppt-runs','task_fake');
  await mkdir(runDirectory,{recursive:true});
  await writeFile(path.join(runDirectory,'brief.json'),JSON.stringify({version_id:version.version_id}));
  const jobDirectory=path.join(workspace,'data','dsh-jobs');
  await mkdir(jobDirectory,{recursive:true});
  const jobFile=path.join(jobDirectory,'task_fake.json');
  await writeFile(jobFile,JSON.stringify({id:'task_fake',request_key:'fake',kind:'generate',target:lesson.lesson_id,status:'completed',stage:'done',created_at:'2026-01-01T00:00:00.000Z',version_id:version.version_id,ppt_run_id:'task_fake',ppt_result:{valid:true,run_id:'task_fake'}}));

  let routeHandler;const server=http.createServer((req,res)=>routeHandler(req,res));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const previousRoot=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=workspace;
  t.after(()=>{if(previousRoot===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=previousRoot;});
  const ctx={webServer:{getOrigin:()=>origin,register:route=>{routeHandler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true}),resolve:async()=>({value:'test-only-key'})},llm:{},connection:{authenticatedUrl:()=>origin},effect:effect=>effect(),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async()=>{throw new Error('not used');}}};
  await applyWorkbench(ctx);
  const boot=await (await fetch(origin+'/teacher/api/bootstrap')).json();
  const response=await fetch(origin+'/teacher/api/lesson/delete',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify({id:lesson.lesson_id,key:'delete'})});
  const result=await response.json();
  assert.equal(response.status,200,result.error);
  assert.equal(result.deleted,true);
  assert.equal(await exists(exported.directory),false);
  assert.equal(await exists(runDirectory),false);
  assert.equal(await exists(jobFile),false);
  assert.equal((await bridge(workspace,'overview')).lessons.length,0);
});
