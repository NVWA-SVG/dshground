import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,cp,readFile} from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {writeFile} from 'node:fs/promises';
import {apply as applyWorkbench,bridge} from '../plugins/teacher-workbench/index.mjs';
import {normalizeTuning,createTuning,installSampling,validationFeedback} from '../plugins/teacher-workbench/tuning.mjs';
import {packTeachingContext,packVisualContext,objectToolSchema} from '../plugins/teacher-workbench/context.mjs';

test('sampling ranges reject invalid values and unsupported text top_p',async()=>{
  for(const value of [-1,2.1,NaN,'0.4'])assert.throws(()=>normalizeTuning({stages:{design:{temperature:value}}}));
  assert.throws(()=>normalizeTuning({stages:{design:{top_p:.8}}}),/未开放/);
  const root=await mkdtemp(path.resolve('.test-runs/tuning-'));await mkdir(path.join(root,'config'));
  const tuning=createTuning({root});await tuning.save({stages:{design:{temperature:.45}}});
  const saved=await tuning.load();assert.equal(saved.stages.design.temperature,.45);assert.equal(saved.stages.visual.temperature,.5);
  assert.equal((await tuning.status()).capabilities.text_top_p,false);
});

test('sampling is installed in the actual DSH request waterfall without mutating frozen headers',async()=>{
  let hook,applied;
  installSampling({on:(name,handler)=>{assert.equal(name,'agent/request');hook=handler;}},{temperature:.25,max_tokens:12000},value=>applied=value);
  const original=Object.freeze({provider:'siliconflow',model:'test',temperature:1,maxTokens:1000});
  const effective=await hook({},async()=>original);
  assert.equal(original.temperature,1);assert.equal(effective.provider,'siliconflow');assert.equal(effective.model,'test');
  assert.equal(effective.temperature,.25);assert.equal(effective.maxTokens,12000);assert.deepEqual(applied,{temperature:.25,max_tokens:12000});
});

test('context packing preserves teacher constraints and selects complete excerpts within budget',async()=>{
  const raw={request:{title:'分数',duration_minutes:40,class_size:null},sources:[{id:'source_one',name:'资料',excerpts:[{chunk:1,text:'无关材料'.repeat(40000)}]}],knowledge:{entries:[{id:'knowledge_one'}],excerpts:[{knowledge_id:'knowledge_one',chunk:2,text:'分母表示平均分成的份数。'}]},class_profile:{baseline:{class_size:null},evidence:[],lessons:[]}};
  const before=structuredClone(raw);const packed=await packTeachingContext('generate',raw,{type:'object'},normalizeTuning());
  assert.deepEqual(raw,before);assert.deepEqual(packed.context.request,raw.request);assert.equal(packed.context.class_profile.baseline.class_size,null);
  assert.equal(packed.context.sources[0].excerpts.length,0);assert.equal(packed.context.knowledge.excerpts.length,1);
  assert.equal(packed.report.omitted.source,1);assert.ok(packed.report.estimated_input_tokens<=packed.report.input_budget_tokens);
});

test('an oversized parent design is refused rather than silently truncated',async()=>{
  const raw={parent_design:{teacher_script:'必须完整保留'.repeat(50000)},decision:{choices:[]},feedback:{raw_text:'原文'},analysis:{suggestions:[]}};
  await assert.rejects(packTeachingContext('revise',raw,{type:'object'},normalizeTuning()),/必要上下文超过输入预算/);
});

test('class features cannot survive budget pruning without their source records',async()=>{
  const raw={request:{title:'下一课分数比较'},class_profile:{baseline:{class_size:null},lessons:[],
    evidence:[{feedback_id:'feedback_kept',raw_text:'分母仍需辨析',next_lesson_focus:'先做一道独立辨析题'},
      {feedback_id:'feedback_omitted',raw_text:'长记录'.repeat(20000)}],
    characteristics:[{category:'learning',text:'分母仍需辨析',sources:[{feedback_id:'feedback_kept'}]},
      {category:'pacing',text:'必须跟随原记录',sources:[{feedback_id:'feedback_omitted'}]}]}};
  const result=await packTeachingContext('generate',raw,{type:'object'},normalizeTuning());
  assert.deepEqual(result.context.class_profile.evidence.map(row=>row.feedback_id),['feedback_kept']);
  assert.deepEqual(result.context.class_profile.characteristics.map(row=>row.category),['learning']);
  assert.equal(result.context.class_profile.evidence[0].next_lesson_focus,'先做一道独立辨析题');
});

test('revision context retains full teacher choices while presenting only adopted suggestions',async()=>{
  const raw={parent_design:{title:'分数'},decision:{choices:[{suggestion_id:'a',action:'accept'},{suggestion_id:'b',action:'reject'}]},feedback:{raw_text:'学生观察原文'},analysis:{suggestions:[{id:'a',observation:'甲'},{id:'b',observation:'乙'}]}};
  const {context}=await packTeachingContext('revise',raw,{type:'object'},normalizeTuning());
  assert.deepEqual(context.decision,raw.decision);assert.deepEqual(context.analysis.suggestions.map(s=>s.id),['a']);assert.equal(raw.analysis.suggestions.length,2);
});

test('visual context excludes full scripts and narrows assets without changing the saved brief',async()=>{
  const raw={schema:{type:'object'},brief:{version_id:'version_one',pages:[{number:1,title:'认识秒',activity_title:'观察钟面',points:['秒针走一圈是60秒'],script:'完整讲稿'.repeat(1000)}]},assets:[{id:'asset_clock',caption:'钟面与秒针',context:'秒针走一圈'},{id:'asset_ball',caption:'足球场',context:'足球运动'}],image_generation:{enabled:true,max_images:4}};
  const {data,report}=await packVisualContext(raw,raw.schema,normalizeTuning());
  assert.equal(data.brief.pages[0].script,undefined);assert.ok(raw.brief.pages[0].script.length>1000);
  assert.deepEqual(data.assets.map(a=>a.id),['asset_clock']);assert.deepEqual(data.brief.pages[0].points,raw.brief.pages[0].points);
  assert.ok(report.after_context_tokens<report.before_context_tokens);
});

test('visual budget includes repeated candidate IDs while preserving complete teaching context',async()=>{
  const pages=Array.from({length:20},(_,i)=>({number:i+1,title:'观察钟面',activity_title:'观察秒针',points:['观察钟面和秒针'],script:'完整讲稿不进视觉上下文',teaching_context:{activity_minutes:5,activity_new_knowledge_count:1},diagram_candidates:[{id:'diagram_'+i,kind:'concepts',labels:['钟面','秒针'],allowed_stages:['setup']}]}));
  const raw={schema:{type:'object'},brief:{version_id:'v_test',pages},assets:[],image_generation:{enabled:false,max_images:0}};
  const baseline=await packVisualContext(raw,raw.schema,normalizeTuning());
  const budget=Math.max(8000,baseline.report.estimated_input_tokens+2500);
  raw.assets=Array.from({length:32},(_,i)=>({id:'asset_'+String(i).padStart(2,'0')+'_'+('x'.repeat(80)),caption:'钟面秒针',description:'观察钟面秒针的变化。'.repeat(50),context:'观察钟面和秒针'}));
  const tuning=normalizeTuning({stages:{visual:{input_budget_tokens:budget}},context:{assets_per_page:8,max_assets:32}});
  const {data,report}=await packVisualContext(raw,raw.schema,tuning);
  assert.ok(report.estimated_input_tokens<=budget);assert.ok(data.assets.length>0&&data.assets.length<8);
  assert.deepEqual(data.brief.pages.map(p=>p.teaching_context),pages.map(p=>p.teaching_context));
  assert.deepEqual(data.brief.pages.map(p=>p.diagram_candidates),pages.map(p=>p.diagram_candidates));
  const allowed=new Set(data.assets.map(a=>a.id));assert.ok(Object.values(data.asset_candidates).flat().every(id=>allowed.has(id)));
});

test('tool envelopes carry the backend schema definitions at their actual reference root',()=>{
  const schema={type:'object',properties:{activity:{$ref:'#/$defs/Activity'}},$defs:{Activity:{type:'object',properties:{id:{type:'string'}}}}};
  const tool=objectToolSchema('design',schema);assert.deepEqual(tool.$defs,schema.$defs);assert.equal(tool.properties.design.properties.activity.$ref,'#/$defs/Activity');assert.equal(tool.properties.design.$defs,undefined);
});

test('repair feedback returns field locations and budget without repeating invalid input',()=>{
  const error=new Error(JSON.stringify([{loc:['slides',0,'title'],msg:'too long',type:'string_too_long',input:'PRIVATE_INPUT'.repeat(1000)}]));
  const result=validationFeedback(error,2,3);assert.equal(result.repair.remaining_attempts,1);assert.equal(result.repair.issues[0].path,'slides.0.title');assert.ok(!JSON.stringify(result).includes('PRIVATE_INPUT'));
});

test('debug API validates and persists stage settings and exposes only context diagnostics',async t=>{
  const root=await mkdtemp(path.resolve('.test-runs/tuning-api-'));await mkdir(path.join(root,'config'));await mkdir(path.join(root,'data','dsh-jobs'),{recursive:true});
  await writeFile(path.join(root,'data','dsh-jobs','task_saved.json'),JSON.stringify({id:'task_saved',kind:'generate',target:'lesson_one',status:'completed',created_at:'2026-11-09',tuning:normalizeTuning(),sampling:{design:{temperature:.3,max_tokens:32768}},context_report:{design:{estimated_input_tokens:2000,mandatory_preserved:true}},raw_input:'private material'}));
  let handler;const server=http.createServer((req,res)=>handler(req,res));await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>{server.closeAllConnections();server.close(r);}));
  const origin='http://127.0.0.1:'+server.address().port,previous=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=root;
  t.after(()=>{if(previous===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=previous;});
  const ctx={webServer:{getOrigin:()=>origin,register:route=>{handler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true})},connection:{authenticatedUrl:()=>origin},llm:{},agents:{},effect:f=>f()};await applyWorkbench(ctx);
  const boot=await(await fetch(origin+'/teacher/api/bootstrap')).json();
  const save=body=>fetch(origin+'/teacher/api/tuning',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify(body)});
  assert.equal((await save({stages:{design:{temperature:3}}})).status,400);
  const response=await save({stages:{design:{temperature:.25}}});assert.equal(response.status,200);
  const settings=await(await fetch(origin+'/teacher/api/tuning')).json();assert.equal(settings.config.stages.design.temperature,.25);
  const report=await(await fetch(origin+'/teacher/api/context-report?job=task_saved')).json();assert.equal(report.sampling.design.temperature,.3);assert.equal(report.context.design.mandatory_preserved,true);assert.ok(!JSON.stringify(report).includes('private material'));
});

test('repeated invalid submissions stop at the configured limit and retain an actionable error',async t=>{
  const project=path.resolve('.'),root=await mkdtemp(path.resolve('.test-runs/repair-limit-'));
  await mkdir(path.join(root,'config'));await mkdir(path.join(root,'tools'));
  await cp(path.join(project,'backend'),path.join(root,'backend'),{recursive:true});await cp(path.join(project,'tools','dsh_bridge.py'),path.join(root,'tools','dsh_bridge.py'));await cp(path.join(project,'config','runtime.json'),path.join(root,'config','runtime.json'));
  const request=JSON.parse(await readFile(path.join(project,'examples','subject','request.json'),'utf8'));
  const lesson=await bridge(root,'create',{payload:request,key:'create'});
  let handler,cancelled=false,attempts=0;
  const server=http.createServer((req,res)=>handler(req,res));await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>{server.closeAllConnections();server.close(r);}));
  const origin='http://127.0.0.1:'+server.address().port,previous=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=root;
  t.after(()=>{if(previous===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=previous;});
  const ctx={webServer:{getOrigin:()=>origin,register:route=>{handler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true})},connection:{authenticatedUrl:()=>origin},llm:{},effect:f=>f(),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
    const tools=new Map();await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}});
    return {agent:{id:options.sessionId,followup:()=>{},cancel:()=>{cancelled=true;},whenIdle:async()=>{
      while(!cancelled&&attempts<10){attempts++;const result=JSON.parse(await tools.get('teacher_submit_design').execute({},{}));assert.equal(result.ok,false);assert.equal(result.repair.remaining_attempts,3-attempts);}
    }},dispose:async()=>{}};
  }}};await applyWorkbench(ctx);
  const boot=await(await fetch(origin+'/teacher/api/bootstrap')).json();const started=await(await fetch(origin+'/teacher/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify({kind:'generate',target:lesson.lesson_id,key:'invalid_submission_job'})})).json();
  let job;for(let i=0;i<200;i++){job=(await(await fetch(origin+'/teacher/api/jobs')).json()).find(j=>j.id===started.id);if(!['queued','running'].includes(job.status))break;await new Promise(r=>setTimeout(r,50));}
  assert.equal(job.status,'failed');assert.equal(cancelled,true);assert.equal(attempts,3);assert.match(job.error,/校验达到重试上限/);
});
