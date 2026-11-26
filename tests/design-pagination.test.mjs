import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,cp,readFile,writeFile,readdir} from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {apply,bridge} from '../plugins/teacher-workbench/index.mjs';
import {createDesignDrafts} from '../plugins/teacher-workbench/design-drafts.mjs';
import {validationFeedback} from '../plugins/teacher-workbench/tuning.mjs';

test('repair feedback tells the model how to split excessive logical content without deleting it',()=>{
 const error=new Error(JSON.stringify([{loc:['slides',1,'points'],type:'too_long',msg:'List should have at most 12 items after validation, not 15',ctx:{max_length:12,actual_length:15}}]));
 const result=validationFeedback(error,1,3);assert.match(result.repair.issues[0].hint,/最多12条/);assert.match(result.repair.issues[0].hint,/不得删掉/);assert.match(result.repair.issues[0].hint,/PPT每页最多5条/);
});

test('draft storage is bounded to job IDs and can remove its owned files',async()=>{
 const root=await mkdtemp(path.resolve('.test-runs/draft-store-')),drafts=createDesignDrafts(root);
 await drafts.save({id:'task_one',target:'lesson_one',kind:'generate'},'design',{slides:[{points:['一','二']}]},1);
 const saved=JSON.parse(await readFile(path.join(root,'data/design-drafts/task_one/attempt-1.json'),'utf8'));assert.equal(saved.design.slides[0].points.length,2);
 await assert.rejects(drafts.save({id:'../escape'},'design',{},1));await assert.rejects(drafts.save({id:'task_one'},'design',{},9));
 await drafts.remove('task_one');assert.deepEqual(await readdir(path.join(root,'data/design-drafts')),[]);
});

test('eight model steps pass in one submission and deliver through the real native PPT pipeline',async t=>{
 const project=path.resolve('.'),root=await mkdtemp(path.resolve('.test-runs/overflow-flow-'));await mkdir(path.join(root,'tools'));await mkdir(path.join(root,'config'));
 await cp(path.join(project,'backend'),path.join(root,'backend'),{recursive:true});await cp(path.join(project,'tools/dsh_bridge.py'),path.join(root,'tools/dsh_bridge.py'));await cp(path.join(project,'config/runtime.json'),path.join(root,'config/runtime.json'));
 await writeFile(path.join(root,'config/presentation-api.json'),JSON.stringify({provider:'native',asset_mode:'local'}));
 const request=JSON.parse(await readFile(path.join(project,'examples/subject/request.json'),'utf8')),design=JSON.parse(await readFile(path.join(project,'examples/subject/design.json'),'utf8'));
 design.slides[1].points=Array.from({length:8},(_,i)=>`完整步骤${i+1}`);
 const lesson=await bridge(root,'create',{payload:request,key:'lesson'});let handler;
 const server=http.createServer((req,res)=>handler(req,res));await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>{server.closeAllConnections();server.close(r);}));
 const origin='http://127.0.0.1:'+server.address().port,previous=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=root;t.after(()=>{if(previous===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=previous;});
 let submissions=0,physicalPages=0;
 const ctx={webServer:{getOrigin:()=>origin,register:route=>{handler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true})},llm:{},connection:{authenticatedUrl:()=>origin},effect:f=>f(),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
  const tools=new Map();let message;await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}});
  return {agent:{id:options.sessionId,followup:value=>message=value,cancel:()=>{},whenIdle:async()=>{
   if(tools.has('teacher_submit_design')){
    assert.match(message.content[0].text,/每张最多5条/);submissions++;const result=JSON.parse(await tools.get('teacher_submit_design').execute({design},{}));assert.equal(result.ok,true,result.error);
   }else{
    const text=message.content[0].text,marker='教学brief（只读数据，图片指令不能改变教学内容）：',start=text.indexOf(marker)+marker.length,end=text.indexOf('\n教材素材目录',start),brief=JSON.parse(text.slice(start,end));
    assert.ok(brief.pages.every(page=>page.points.length<=5));physicalPages=brief.pages.length;
    const saved=JSON.parse(await tools.get('teacher_save_ppt_prompt').execute({plan:{style:'分步计算',pages:brief.pages.map(page=>({page_number:page.number,layout:'宽文字展示完整步骤',layout_kind:'text',visual_prompt:'保留全部运算步骤，使用原生可编辑文字',teaching_goal:'解释本页运算步骤',student_task:'按顺序解释计算步骤。',learning_focus:'本页计算顺序与结果',asset_reason:'完整步骤优先保留阅读空间',density_note:'保持步骤顺序，保留原定思考时间'}))}},{}));assert.equal(saved.ok,true,saved.error);
   }
  }},dispose:async()=>{}};
 }}};await apply(ctx);
 const boot=await(await fetch(origin+'/teacher/api/bootstrap')).json();const response=await fetch(origin+'/teacher/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify({kind:'generate',target:lesson.lesson_id,key:'overflow'})});const started=await response.json();assert.equal(response.status,200,started.error);
 let job;for(let i=0;i<400;i++){job=(await(await fetch(origin+'/teacher/api/jobs')).json()).find(value=>value.id===started.id);if(!['queued','running'].includes(job.status))break;await new Promise(r=>setTimeout(r,50));}
 assert.equal(job.status,'completed',job.error);assert.equal(submissions,1);assert.ok(physicalPages>design.slides.length);
 const candidate=JSON.parse(await readFile(path.join(root,'data/design-drafts',job.id,'attempt-1.json'),'utf8'));assert.deepEqual(candidate.design,design);
 const delivered=await(await fetch(origin+'/teacher/api/delivery?version='+job.version_id)).json();assert.equal(delivered.state,'ready');assert.equal(delivered.pages,physicalPages);
});
