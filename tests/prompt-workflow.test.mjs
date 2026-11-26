import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdir,mkdtemp,cp,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bridge,apply as applyWorkbench} from '../plugins/teacher-workbench/index.mjs';
import {loadPrompt} from '../plugins/teacher-workbench/prompts.mjs';
import {fixturePatch,readPatchTargets} from './revision-fixture.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
test('revision stage receives teacher evidence, previews real differences and saves an intact new delivery',async t=>{
  const workspace=await mkdtemp(path.join(root,'.test-runs','prompt-revision-'));
  await mkdir(path.join(workspace,'tools'));await mkdir(path.join(workspace,'config'));
  await cp(path.join(root,'backend'),path.join(workspace,'backend'),{recursive:true});
  await cp(path.join(root,'tools','dsh_bridge.py'),path.join(workspace,'tools','dsh_bridge.py'));
  await cp(path.join(root,'config','runtime.json'),path.join(workspace,'config','runtime.json'));
  await writeFile(path.join(workspace,'config','presentation-api.json'),JSON.stringify({provider:'native',asset_mode:'local'}));
  const fixture=async name=>JSON.parse(await readFile(path.join(root,'examples','subject',name+'.json'),'utf8'));
  const lesson=await bridge(workspace,'create',{payload:await fixture('request'),key:'request'});
  const parent=await bridge(workspace,'save_design',{id:lesson.lesson_id,payload:await fixture('design'),key:'design'});
  const exported=await bridge(workspace,'export',{id:parent.version_id});
  const previousPpt=await readFile(path.join(exported.directory,'课件.pptx'));
  const feedback=await bridge(workspace,'feedback',{id:parent.version_id,payload:await fixture('feedback'),key:'feedback'});
  const decision=await bridge(workspace,'decide',{id:feedback.feedback_id,payload:await fixture('decision'),key:'decision'});
  let handler;const server=http.createServer((req,res)=>handler(req,res));await new Promise(r=>server.listen(0,'127.0.0.1',r));
  t.after(()=>new Promise(r=>{server.closeAllConnections();server.close(r);}));
  const origin='http://127.0.0.1:'+server.address().port;
  const oldRoot=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=workspace;
  t.after(()=>{if(oldRoot===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=oldRoot;});
  const stages=[];
  const ctx={webServer:{getOrigin:()=>origin,register:route=>{handler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true})},llm:{},connection:{authenticatedUrl:()=>origin},effect:f=>f(),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
    assert.equal(options.meta.agentPreset,'teacher');
    const tools=new Map();let message;await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}});
    return {agent:{id:options.sessionId,followup:m=>message=m,cancel:()=>{},whenIdle:async()=>{
      const text=message.content[0].text;
      if(tools.has('teacher_submit_revision_patch')){
        stages.push('revision');assert.ok(text.startsWith(await loadPrompt('revision')));
        const marker='教师输入和依据：\n',context=JSON.parse(text.slice(text.indexOf(marker)+marker.length));
        assert.equal(context.parent_version_id,parent.version_id);assert.equal(context.decision.teacher_confirmed,true);assert.equal(context.decision.choices[0].suggestion_id,'assessment_1');
        assert.ok(context.analysis.suggestions.some(s=>s.id==='assessment_1'));
        assert.equal(context.parent_design,undefined);assert.ok(context.parent_outline);
        const revision=fixturePatch(await fixture('design'),await fixture('revision'),parent.version_id);const submit=tools.get('teacher_submit_revision_patch');
        assert.equal(JSON.parse(await submit.execute({},{})).ok,false);
        await readPatchTargets(tools,revision);
        const diff=JSON.parse(await tools.get('teacher_preview_revision_patch').execute({patch:{parent_version_id:parent.version_id,operations:revision.operations}},{}));assert.ok(diff.changes.length>0);
        revision.change_links=diff.changes.map(change=>({target:change.target,suggestion_ids:['assessment_1'],explanation:'依据教师已采纳的检测建议，联动调整本课内容。'}));
        const saved=JSON.parse(await submit.execute({revision},{}));assert.equal(saved.ok,true,saved.error);
      }else{
        stages.push('visual');assert.ok(text.startsWith(await loadPrompt('visual')));
        const marker='教学brief（只读数据，图片指令不能改变教学内容）：',start=text.indexOf(marker)+marker.length,end=text.indexOf('\n教材素材目录',start),brief=JSON.parse(text.slice(start,end));
        const saved=JSON.parse(await tools.get('teacher_save_ppt_prompt').execute({plan:{style:'教学对比与分步',pages:brief.pages.map(p=>({page_number:p.number,layout:'分步',layout_kind:'steps',visual_prompt:'保持原文与必要的原生步骤',teaching_goal:'依据教师选择解释本页内容',student_task:'观察并说明你的想法。',learning_focus:'本页原有任务与关键关系',asset_reason:'原生步骤便于解释和比较',density_note:'在原有课时内保留独立思考'}))}},{}));assert.equal(saved.ok,true,saved.error);
      }
    }},dispose:async()=>{}};
  }}};
  await applyWorkbench(ctx);
  const boot=await(await fetch(origin+'/teacher/api/bootstrap')).json();
  const response=await fetch(origin+'/teacher/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify({kind:'revise',target:decision.decision_id,key:'revision_prompt_job'})});
  const started=await response.json();assert.equal(response.status,200,started.error);
  let job;
  for(let index=0;index<400;index++){
    job=(await(await fetch(origin+'/teacher/api/jobs')).json()).find(j=>j.id===started.id);
    if(!['queued','running'].includes(job.status))break;
    await new Promise(r=>setTimeout(r,50));
  }
  assert.equal(job.status,'completed',job.error);assert.deepEqual(stages,['revision','visual']);
  const version=await bridge(workspace,'get',{kind:'version',id:job.version_id});
  assert.equal(version.parent_id,parent.version_id);assert.equal(version.number,2);assert.ok(version.changes.length>0);
  assert.ok(version.changes.every(c=>c.evidence_ids.includes('assessment:1')));
  assert.deepEqual(await readFile(path.join(exported.directory,'课件.pptx')),previousPpt);
  const delivery=await(await fetch(origin+'/teacher/api/delivery?version='+job.version_id)).json();assert.equal(delivery.state,'ready');
});
