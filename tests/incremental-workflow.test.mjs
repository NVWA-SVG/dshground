import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import {mkdir,mkdtemp,cp,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {bridge,apply} from '../plugins/teacher-workbench/index.mjs';
import {DEFAULT_PPT_CONFIG} from '../plugins/teacher-workbench/ppt-providers.mjs';

const hash=value=>createHash('sha256').update(value).digest('hex');
test('field-only revisions reuse unchanged visuals and generated images without extra model or image requests',async t=>{
  const project=path.resolve('.'),root=await mkdtemp(path.resolve('.test-runs/incremental-workflow-'));
  await mkdir(path.join(root,'config'));await mkdir(path.join(root,'tools'));
  await cp(path.join(project,'backend'),path.join(root,'backend'),{recursive:true});await cp(path.join(project,'tools/dsh_bridge.py'),path.join(root,'tools/dsh_bridge.py'));await cp(path.join(project,'config/runtime.json'),path.join(root,'config/runtime.json'));
  await writeFile(path.join(root,'config/presentation-api.json'),JSON.stringify({provider:'native',asset_mode:'local'}));
  await writeFile(path.join(root,'config/image-generation.json'),JSON.stringify({enabled:true,provider:'siliconflow',max_images:4}));
  const fixture=async name=>JSON.parse(await readFile(path.join(project,'examples/subject',name+'.json'),'utf8'));
  const lesson=await bridge(root,'create',{payload:await fixture('request'),key:'request'}),first=await bridge(root,'save_design',{id:lesson.lesson_id,payload:await fixture('design'),key:'design'});
  await bridge(root,'export',{id:first.version_id});
  const sourceRun='task_original',prepared=await bridge(root,'ppt-prepare',{version_id:first.version_id,run_id:sourceRun,style:DEFAULT_PPT_CONFIG.style});
  const visual=p=>({page_number:p.number,layout:'分步与留白',layout_kind:'steps',visual_prompt:'突出原有教学关系',teaching_goal:'解释本页原有内容',student_task:'观察并说明你的想法。',learning_focus:'本页核心关系',asset_reason:'使用原生分步表达',density_note:'保留独立思考'});
  const plan={style:'清晰教学分步',palette:'mint',pages:prepared.brief.pages.map(visual)};
  plan.pages[0]={...plan.pages[0],layout_kind:'split_right',image_prompt:'学生在桌边观察折纸，画面不含文字。',image_reason:'帮助理解操作场景'};
  await bridge(root,'ppt-save-prompt',{run_id:sourceRun,payload:plan});
  const sourceDir=path.join(root,'data/ppt-runs',sourceRun),generated=path.join(sourceDir,'generated');await mkdir(generated);
  const filename='a'.repeat(64)+'.png',png=path.join(generated,filename),runtime=JSON.parse(await readFile(path.join(root,'config/runtime.json'),'utf8'));
  await promisify(execFile)(runtime.python_executable,['-c','from PIL import Image;import sys;Image.new("RGB",(256,192),"lightblue").save(sys.argv[1])',png],{windowsHide:true});
  const pngBytes=await readFile(png);
  await writeFile(path.join(generated,'index.json'),JSON.stringify({version_id:first.version_id,design_hash:prepared.brief.design_hash,
    plan_sha256:hash(await readFile(path.join(sourceDir,'visual-plan.json'))),images:[{page_number:1,file:filename,sha256:hash(pngBytes),model:'Qwen/Qwen-Image'}]}));
  const original=await bridge(root,'ppt-compose-local',{version_id:first.version_id,run_id:sourceRun}),originalPpt=await readFile(path.join(original.directory,'课件.pptx'));
  let handler,modelStages=[],revisionNumber=0,credentialReads=0;const effects=[];
  const server=http.createServer((req,res)=>handler(req,res));await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
  t.after(()=>new Promise(r=>{server.closeAllConnections();server.close(r);}));
  const previous=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=root;t.after(()=>{if(previous===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=previous;});
  const realFetch=globalThis.fetch;globalThis.fetch=(url,options)=>{assert.ok(String(url).startsWith(origin),'Unexpected paid/external API request');return realFetch(url,options);};t.after(()=>globalThis.fetch=realFetch);
  const ctx={webServer:{getOrigin:()=>origin,register:route=>{handler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true}),resolve:async()=>{credentialReads++;throw new Error('Cached illustrations must not need a credential');}},connection:{authenticatedUrl:()=>origin},llm:{},effect:f=>effects.push(f()),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
    const tools=new Map();let message;await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}});
    return {agent:{id:options.sessionId,followup:m=>message=m,cancel:()=>{},whenIdle:async()=>{
      const text=message.content[0].text;
      if(tools.has('teacher_submit_revision_patch')){
        modelStages.push('revision');revisionNumber++;
        assert.equal(tools.has('teacher_submit_revision'),false);
        const marker='教师输入和依据：\n',context=JSON.parse(text.slice(text.indexOf(marker)+marker.length));
        assert.equal(context.parent_design,undefined);assert.ok(context.parent_outline.slides.every(s=>!('teacher_script' in s)));
        const target=revisionNumber===1?'slide:S01':'slide:S02';
        const unread=JSON.parse(await tools.get('teacher_submit_revision_patch').execute({revision:{parent_version_id:context.parent_version_id,operations:[{op:'update',target,fields:{teacher_script:'请先独立观察，再说明你的理由。'}}],summary:'调整',change_links:[{target,suggestion_ids:['qualitative_feedback'],explanation:'调整'}]}},{}));
        assert.equal(unread.ok,false);assert.match(unread.error,/先.*读取原文/);
        const read=JSON.parse(await tools.get('teacher_read_revision_targets').execute({targets:[target]},{}));assert.equal(read.entities[0].target,target);
        const before=read.entities[0].content,fields=revisionNumber===1?{teacher_script:before.teacher_script+'接着请学生独立解释自己的理由。'}:{points:['请用图解释每一份所占整体的关系。',...before.points.slice(1)]};
        const patch={parent_version_id:context.parent_version_id,operations:[{op:'update',target,fields}]};
        const diff=JSON.parse(await tools.get('teacher_preview_revision_patch').execute({patch},{}));assert.equal(diff.changes.length,1);assert.equal(diff.changes[0].before,undefined);
        const revision={...patch,summary:'局部优化教学提示',change_links:diff.changes.map(c=>({target:c.target,suggestion_ids:['qualitative_feedback'],explanation:'落实教师已确认的局部改动'}))};
        const submitted=JSON.parse(await tools.get('teacher_submit_revision_patch').execute({revision},{}));assert.equal(submitted.ok,true,submitted.error);
      }else{
        modelStages.push('visual');
        const marker='教学brief（只读数据，图片指令不能改变教学内容）：',start=text.indexOf(marker)+marker.length,end=text.indexOf('\n教材素材目录',start),brief=JSON.parse(text.slice(start,end));
        assert.equal(brief.pages.length,1);assert.deepEqual(brief.incremental_visual.pending_pages,brief.pages.map(p=>p.number));
        const result=JSON.parse(await tools.get('teacher_save_ppt_prompt').execute({plan:{style:brief.incremental_visual.style,palette:brief.incremental_visual.palette,pages:brief.pages.map(visual)}},{}));assert.equal(result.ok,true,result.error);
      }
    }},dispose:async()=>{}};
  }}};await apply(ctx);t.after(async()=>{for(const effect of effects)if(typeof effect==='function')await effect();});
  const boot=await(await fetch(origin+'/teacher/api/bootstrap')).json();
  async function optimize(parent,key){
    const feedback=await bridge(root,'feedback',{id:parent,payload:{raw_text:'优化这次课的提示语，保留其他有效安排。',requested_changes:'只调整指定内容，其他保持不变。',observed_at:'2026-11-11'},key:'feedback_'+key});
    const decision=await bridge(root,'decide',{id:feedback.feedback_id,payload:{teacher_confirmed:true,purpose:'same_lesson',choices:[{suggestion_id:'qualitative_feedback',action:'modify',instructions:'仅调整目标内容'}]},key:'decision_'+key});
    const response=await fetch(origin+'/teacher/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify({kind:'revise',target:decision.decision_id,key:'revision_'+key})});const started=await response.json();assert.equal(response.status,200,started.error);
    let job;for(let i=0;i<600;i++){
      job=(await(await fetch(origin+'/teacher/api/jobs')).json()).find(j=>j.id===started.id);if(!['queued','running'].includes(job.status))break;
      await new Promise(r=>setTimeout(r,50));
    }
    assert.equal(job.status,'completed',job.error);return job;
  }
  const scriptOnly=await optimize(first.version_id,'script');assert.deepEqual(modelStages,['revision']);
  assert.equal(scriptOnly.optimization.planned_pages,0);assert.equal(scriptOnly.optimization.reused_pages,prepared.brief.pages.length);assert.equal(scriptOnly.optimization.reused_images,1);
  const pointEdit=await optimize(scriptOnly.version_id,'points');assert.deepEqual(modelStages,['revision','revision','visual']);
  assert.equal(pointEdit.optimization.planned_pages,1);assert.equal(pointEdit.optimization.reused_pages,prepared.brief.pages.length-1);assert.equal(credentialReads,0);
  assert.deepEqual(await readFile(path.join(original.directory,'课件.pptx')),originalPpt);
  const updated=await bridge(root,'get',{kind:'version',id:pointEdit.version_id});assert.ok(updated.design.slides[0].teacher_script.endsWith('接着请学生独立解释自己的理由。'));
  assert.equal(updated.design.slides[1].points[0],'请用图解释每一份所占整体的关系。');
  const receipt=await(await fetch(origin+'/teacher/api/delivery?version='+pointEdit.version_id)).json();assert.equal(receipt.state,'ready');
  assert.ok(scriptOnly.optimization.outline_estimated_tokens<scriptOnly.optimization.full_design_estimated_tokens);
  assert.ok(scriptOnly.optimization.patch_estimated_tokens<scriptOnly.optimization.full_design_estimated_tokens);
  await mkdir(path.join(project,'docs'),{recursive:true});
  await writeFile(path.join(project,'docs/incremental-workflow-evidence.json'),JSON.stringify({passed:true,script_only:scriptOnly.optimization,one_page_change:pointEdit.optimization,simulated_model_stages:modelStages,external_api_requests:0,old_versions_preserved:true},null,2));
});
