import {spawn} from 'node:child_process';
import {randomUUID, randomBytes} from 'node:crypto';
import {readFile, writeFile, mkdir, readdir, rename, realpath, rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createPptService} from './ppt-service.mjs';
import {createVisionService} from './vision-service.mjs';
import {createVisionQueue} from './vision-queue.mjs';
import {createImageService} from './image-service.mjs';
import {buildTeachingTask} from './prompts.mjs';
import {createTuning,installSampling,validationFeedback} from './tuning.mjs';
import {packTeachingContext,objectToolSchema,estimateTokens} from './context.mjs';
import {createMaterialDelivery} from './material-delivery.mjs';
import {createKnowledgeImport} from './knowledge-import.mjs';
import {createDesignDrafts} from './design-drafts.mjs';
import {modelEndError,failureFromError,publicFailureFields,lastAttemptFailure} from './task-failures.mjs';
import {mountTeacherAgent} from './teacher-agent.mjs';
import {apiRuntimeFor,installApiScheduling} from './api-runtime.mjs';
import {createTextModelService} from './text-model-service.mjs';

export const name = 'teacher-workbench';
export const inject = ['webServer', 'agents', 'agentPresets', 'tools', 'credentials', 'llm', 'connection'];
const here = path.dirname(fileURLToPath(import.meta.url));
const allowedFiles = new Set(['课件.pptx','教案.docx','逐字稿.docx','检测题与答案.docx','内容预览.html','教学设计.json','信息密度.json','修改说明.md','交付包.zip','交付清单.json']);
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.zip':'application/zip','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'};
const textBlock = text => [{type:'text', text}];
const idPattern = /^[A-Za-z][A-Za-z0-9_-]{0,100}$/;
function validId(id) { if (typeof id !== 'string' || !idPattern.test(id)) throw new Error('标识格式不正确'); return id; }
function messageOf(error) { return String(error?.message || error).replace(/sk-[\w-]+/g, '[已隐藏密钥]'); }

export async function bridge(root, op, data = {}, signal) {
  const runtime = JSON.parse(await readFile(path.join(root,'config','runtime.json'),'utf8'));
  return new Promise((resolve,reject) => {
    const child = spawn(runtime.python_executable, ['-X','utf8',path.join(root,'tools','dsh_bridge.py'),root], {
      cwd:root, windowsHide:true, shell:false, env:{...process.env,PYTHONUTF8:'1'}, signal
    });
    let output='', errors='';
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { output += chunk; if(output.length > 16000000) child.kill(); });
    child.stderr.on('data', chunk => { errors=(errors+chunk).slice(-4000); });
    child.on('error',reject);
    child.on('close', () => { try { const result=JSON.parse(output); if(!result.ok) {const error=new Error(typeof result.error==='string'?result.error:JSON.stringify(result.error));error.code=result.error_code;throw error;} resolve(result.result); } catch(error) { const failure=new Error(output ? messageOf(error) : (errors || '本地业务进程未返回结果'));failure.code=error.code;reject(failure); } });
    child.stdin.on('error',()=>{}); child.stdin.end(JSON.stringify({op,data}));
  });
}

export async function apply(ctx) {
  const root = process.env.TEACHER_PREP_ROOT || path.resolve(here,'../..');
  const jobDir = path.join(root,'data','dsh-jobs');
  await mkdir(jobDir,{recursive:true});
  const token=randomBytes(32).toString('hex'), jobs=new Map(), handles=new Map(), controllers=new Map();
  const jobList=()=>[...jobs.values()].sort((a,b)=>a.created_at.localeCompare(b.created_at));
  // Browser-facing job records omit manifests, absolute directories and other
  // large internals; status polling must stay small as course history grows.
  const publicJob=job=>({id:job.id,kind:job.kind,target:job.target,status:job.status,phase:job.phase,stage:job.stage,
    error:job.error,model:job.model,version_id:job.version_id,ppt_run_id:job.ppt_run_id,ppt_remote_id:job.ppt_remote_id,
    ppt_status:job.ppt_status,prompt_ready:job.prompt_ready,ppt_candidate_ready:job.ppt_candidate_ready,
    created_at:job.created_at,completed_at:job.completed_at,
    ...(job.ppt_result?{ppt_result:{valid:job.ppt_result.valid,run_id:job.ppt_result.run_id}}:{}),
    ...(job.delivery?{delivery:{valid:job.delivery.valid,run_id:job.delivery.run_id,pages:job.delivery.pages}}:{}),
    ...(job.optimization?{optimization:job.optimization}:{}),...publicFailureFields(job)});
  let startGate=Promise.resolve();
  const serialStart=action=>{const result=startGate.then(action);startGate=result.catch(()=>{});return result;};
  const persist=async job => { const target=path.join(jobDir,validId(job.id)+'.json'); const temporary=target+'.'+randomUUID()+'.tmp'; await writeFile(temporary,JSON.stringify(job,null,2)); await rename(temporary,target); };
  for (const filename of await readdir(jobDir)) {
    if(!/^task_[\w-]+\.json$/.test(filename)) continue;
    const job=JSON.parse(await readFile(path.join(jobDir,filename),'utf8'));
    if(['queued','running'].includes(job.status)) {job.status='interrupted';job.error='工作台已重启。已保存的版本仍在，请检查后重试未完成的操作。';await persist(job);}
    jobs.set(job.id,job);
  }
  const call=(op,data,signal)=>bridge(root,op,data,signal);
  const apiRuntime=apiRuntimeFor(root);installApiScheduling(ctx,apiRuntime);ctx.effect(()=>()=>apiRuntime.dispose());
  const tuning=createTuning({root});
  const knowledgeImport=createKnowledgeImport({root,call});
  const designDrafts=createDesignDrafts(root);
  const ppt=createPptService({ctx,root,call,pluginDir:here,persist,handles});
  const materials=createMaterialDelivery({call,ppt,jobs:jobList});
  const vision=createVisionService({ctx,root,call});
  const visionQueue=await createVisionQueue({root,vision,call});
  ctx.effect(()=>()=>visionQueue.dispose());
  const imageGeneration=createImageService({ctx,root});
  const textModels=createTextModelService({ctx,root}),settings=textModels.config,modelStatus=textModels.status;
  const knowledgeQuery=request=>[request.title,request.subject,request.grade,...(request.constraints||[]),request.prior_knowledge,request.difficulties].filter(Boolean).join('\n');
  const knowledgeContext=(ids,query,tune)=>ids?.length?call('knowledge_context',{ids,query,max_chars:tune.context.retrieval_chars}):{entries:[],excerpts:[]};
  async function jobContext(kind,target,configuration) {
    const tune=configuration||await tuning.load();
    if(['ppt','prompt','repair_ppt','complete','local_ppt'].includes(kind)){
      const version=await call('get',{kind:'version',id:target});
      if(kind!=='complete'&&version.status!=='ready')throw new Error('请先导出该版本的基础材料');
      return {version_id:target};
    }
    if(kind==='generate'){
      const lesson=await call('get',{kind:'lesson',id:target}),query=knowledgeQuery(lesson.payload);
      const sources=lesson.payload.source_ids?.length?(await call('source-context',{ids:lesson.payload.source_ids,query,max_chars:tune.context.source_chars})).sources:[];
      const classProfile=lesson.payload.class_id?await call('class_profile',{id:lesson.payload.class_id,query,limit:tune.context.history_records}):null;
      const knowledge=await knowledgeContext(lesson.payload.knowledge_ids||[],query,tune);
      return {request:lesson.payload,sources,class_profile:classProfile,knowledge};
    }
    const decision=await call('get',{kind:'decision',id:target});
    if(!decision.payload.teacher_confirmed||!decision.payload.choices.some(c=>['accept','modify'].includes(c.action)))throw new Error('请先由教师选择要采纳或修改的建议。');
    const feedback=await call('get',{kind:'feedback',id:decision.feedback_id});
    const parent=await call('get',{kind:'version',id:feedback.version_id});
    const lesson=await call('get',{kind:'lesson',id:feedback.lesson_id});
    const query=[parent.design.title,parent.design.subject,parent.design.grade,feedback.payload.raw_text,feedback.payload.requested_changes,...(feedback.payload.teacher_keep||[])].filter(Boolean).join('\n');
    const classProfile=lesson.payload.class_id?await call('class_profile',{id:lesson.payload.class_id,query,limit:tune.context.history_records}):null;
    const knowledge=await knowledgeContext(parent.design.knowledge_ids||[],query,tune);
    const parentOutline=await call('revision-outline',{id:parent.id});
    return {decision:decision.payload,feedback:feedback.payload,analysis:feedback.analysis,parent_outline:parentOutline,
      revision_context:{mode:'incremental',full_design_estimated_tokens:estimateTokens(parent.design),outline_estimated_tokens:estimateTokens(parentOutline)},
      parent_version_id:parent.id,lesson_request:lesson.payload,class_profile:classProfile,knowledge};
  }
  async function run(job) {
    let handle, deadline;
    const controller=new AbortController();controllers.set(job.id,controller);
    try {
      job.status='running';job.phase='design';job.stage='正在准备教学依据';await persist(job);
      const config=await settings();
      job.provider=config.provider;
      job.model=config.model;
      job.tuning=await tuning.load();
      let context=await jobContext(job.kind,job.target,job.tuning);
      if(job.kind==='local_ppt') {
        job.version_id=job.target;job.phase='presentation';job.stage='正在按所选教材素材生成课件';await persist(job);
        await ppt.composeManual(job,job.target,job.assets_by_page,controller.signal);
        job.phase='validation';job.stage='正在校验并打包整套教学材料';await persist(job);
        job.delivery=await materials.prepare(job.target,job.ppt_result?.run_id||null);
        job.status='completed';job.phase='completed';job.stage='整套教学材料已完成';job.completed_at=new Date().toISOString();return;
      }
      if(job.kind==='complete') {
        job.version_id=job.target;job.phase='documents';job.stage='正在整理本课教案、讲稿与检测题';await persist(job);
        await call('export',{id:job.target});await call('check',{id:job.target});
        await ppt.complete(job,job.target,jobList(),controller.signal,{restart:Boolean(job.restart)});
        job.phase='validation';job.stage='正在校验并打包整套教学材料';await persist(job);
        job.delivery=await materials.prepare(job.target,job.ppt_result?.run_id||null);
        job.status='completed';job.phase='completed';job.stage='整套教学材料已完成';job.completed_at=new Date().toISOString();return;
      }
      if(job.kind==='ppt'||job.kind==='prompt'||job.kind==='repair_ppt') {
        if(job.kind==='repair_ppt')await ppt.repair(job,job.target,controller.signal);
        else await ppt.execute(job,job.target,{promptOnly:job.kind==='prompt',signal:controller.signal});
        if(job.ppt_status==='ready')job.delivery=await materials.prepare(job.target,job.ppt_result?.run_id||null);
        job.status='completed';job.phase='completed';job.stage=job.ppt_status==='ready'?'整套教学材料已完成':'课件提示词已生成，可交给PPT服务使用';job.completed_at=new Date().toISOString();return;
      }
      const schema=await call('schema',{kind:job.kind==='generate'?'design':'revision_patch'});
      const stage=job.kind==='generate'?'design':'revision',profile=job.tuning.stages[stage];
      const packed=await packTeachingContext(job.kind,context,schema,job.tuning);context=packed.context;
      job.context_report={...(job.context_report||{}),[stage]:packed.report};await persist(job);
      const editsSchema=job.kind==='generate'?null:await call('schema',{kind:'revision_edits'});
      const readTargets=new Set();let detailTokens=0;
      if(job.kind==='revise')job.optimization={mode:'incremental',read_entity_count:0,estimated_detail_tokens:0,
        full_design_estimated_tokens:context.revision_context.full_design_estimated_tokens,
        outline_estimated_tokens:context.revision_context.outline_estimated_tokens};
      let submitted=false,validationFailures=0;
      const rejectSubmission=error=>{
        const feedback=validationFeedback(error,++validationFailures,job.tuning.max_validation_attempts);
        job.last_validation_error=feedback.error;
        if(validationFailures>=job.tuning.max_validation_attempts)handle?.agent.cancel({kind:'hook',reason:'教学设计校验重试已达到上限'});
        return JSON.stringify(feedback);
      };
      const toolName=job.kind==='generate'?'teacher_submit_design':'teacher_submit_revision_patch';
      const payloadName=job.kind==='generate'?'design':'revision';
      handle=await ctx.agents.create({sessionId:'teacher_'+randomUUID().replaceAll('-',''),meta:{cwd:root,agentPreset:'teacher'},agentOptions:{provider:config.provider||'siliconflow',model:config.model,maxTokens:profile.max_tokens},
        setup: async agentCtx => {
          await mountTeacherAgent(ctx,agentCtx);
          installSampling(agentCtx,profile,async applied=>{job.sampling={...(job.sampling||{}),[stage]:applied};await persist(job);});
          agentCtx.tools.restrict({allow:[]});
          agentCtx.tools.register({name:toolName,description:`在 ${payloadName} 字段提交${job.kind==='generate'?'完整教学设计':'增量修订（operations仅含实际修改字段，不能提交完整design）'}，严格校验并保存。失败时根据错误修正。`,
            parameters:objectToolSchema(payloadName,schema),
            output:{schema:{type:'string'},render:(_args,value)=>textBlock(value)},
            execute:async(args,exec)=>{
              if(submitted){exec.concludeTurn?.();return JSON.stringify({ok:true,version_id:job.version_id,note:'已保存，无需重复提交'});}
              const payload=args?.[payloadName];
              if(!payload||typeof payload!=='object') return rejectSubmission(new Error(`请在 ${payloadName} 字段提交完整对象，不要提交空参数`));
              job.stage='正在校验教学设计';await persist(job);
              try {
                if(job.kind==='revise')for(const operation of payload.operations||[]){
                  if(['update','remove'].includes(operation.op)&&!readTargets.has(operation.target))throw new Error('修改前请先用teacher_read_revision_targets读取原文：'+operation.target);
                }
                await designDrafts.save(job,payloadName,payload,validationFailures+1);
                job.design_draft_saved=true;
                const result=await call(job.kind==='generate'?'save_design':'revise-patch',{id:job.target,payload,key:job.id},exec.signal);
                if(job.optimization){job.optimization.changed_targets=result.changes.length;job.optimization.patch_estimated_tokens=estimateTokens(payload);}
                submitted=true;job.version_id=result.version_id;job.stage='教学设计已保存';await persist(job);
                exec.concludeTurn?.();
                return JSON.stringify(job.kind==='revise'?{ok:true,version_id:result.version_id,parent_id:result.parent_id,
                  number:result.number,status:result.status,changed_targets:result.changes.map(c=>c.target),note:'已本地合并为完整新版，未修改内容保留。'}:{ok:true,...result});
              } catch(error) { return rejectSubmission(error); }
            }});
          if(job.kind==='revise'){
            agentCtx.tools.register({name:'teacher_read_revision_targets',description:'按需读取原教学实体与字段结构，每次1—8个target。修改已有实体前必须读取；避免反复读取无关讲稿。',
              parameters:{type:'object',properties:{targets:{type:'array',items:{type:'string'},minItems:1,maxItems:8}},required:['targets'],additionalProperties:false},
              output:{schema:{type:'string'},render:(_args,value)=>textBlock(value)},execute:async(args,exec)=>{
                try{const result=await call('revision-read',{id:job.target,targets:args.targets},exec.signal),tokens=estimateTokens(result);
                  if(packed.report.estimated_input_tokens+detailTokens+tokens>profile.input_budget_tokens)throw new Error('按需读取将超过本次输入预算，请减少读取内容或提高修订输入预算');
                  for(const row of result.entities)readTargets.add(row.target);detailTokens+=tokens;
                  job.optimization.read_entity_count=readTargets.size;job.optimization.estimated_detail_tokens=detailTokens;await persist(job);return JSON.stringify(result);
                }catch(error){return JSON.stringify({error:messageOf(error)});}
              }});
            agentCtx.tools.register({name:'teacher_preview_revision_patch',description:'在patch字段提交parent_version_id和operations。本地合并并校验，返回实际变化target与字段名，供填写change_links；不返回整份设计。',
              parameters:objectToolSchema('patch',editsSchema),output:{schema:{type:'string'},render:(_args,value)=>textBlock(value)},
              execute:async(args,exec)=>{try{return JSON.stringify(await call('revision-preview',{id:job.target,payload:args.patch},exec.signal));}catch(error){return JSON.stringify({error:messageOf(error)});}}});
          }
        }});
      handles.set(job.id,handle);job.session_id=handle.agent.id;job.stage='正在生成整套教学设计';await persist(job);
      const prompt=await buildTeachingTask(job.kind,schema,context);
      handle.agent.followup({id:randomUUID(),role:'user',content:textBlock(prompt),source:{kind:'user'}});
      let timedOut=false;
      deadline=setTimeout(()=>{timedOut=true;handle.agent.cancel({kind:'hook',reason:'教师工作台生成超过15分钟'});},900000);
      await handle.agent.whenIdle();clearTimeout(deadline);
      if(!submitted) {
        if(validationFailures>=job.tuning.max_validation_attempts)throw new Error('教学设计校验达到重试上限：'+job.last_validation_error);
        const events=handle.agent.session.snapshotEvents();
        const end=[...events].reverse().find(e=>e.type==='turn/end');
        job.diagnostic=end?.data||null;
        job.request_failure=lastAttemptFailure(events);
        throw modelEndError(end?.data?.reason,{timedOut,provider:job.provider,stage:job.kind==='revise'?'修订设计':'教学设计',requestFailure:job.request_failure});
      }
      job.phase='documents';job.stage='正在整理教案、逐字稿和检测题';await persist(job);
      await call('export',{id:job.version_id});
      const check=await call('check',{id:job.version_id});
      if(!check.valid) throw new Error('材料校验未通过');
      handles.delete(job.id);await handle.dispose();handle=null;
      await ppt.execute(job,job.version_id,{automatic:true,signal:controller.signal});
      job.phase='validation';job.stage='正在校验并打包整套教学材料';await persist(job);
      job.delivery=await materials.prepare(job.version_id,job.ppt_result?.run_id||null);
      job.status='completed';job.phase='completed';job.stage='整套教学材料已完成';job.completed_at=new Date().toISOString();
    } catch(error) {
      job.status=job.cancel_requested?'cancelled':'failed';job.stage=job.cancel_requested?'任务已停止':'生成未完成';
      const failure=job.cancel_requested?null:failureFromError(error);if(failure)job.failure=failure;
      job.error=job.cancel_requested?'已保存的教学设计和旧版材料保留，可检查后继续。':failure?.message||messageOf(error);
    }
    finally {clearTimeout(deadline);handles.delete(job.id);controllers.delete(job.id);if(handle)await handle.dispose().catch(()=>{});await persist(job);}
  }
  async function readBody(req,limit=36*1024*1024) {let chunks=[],size=0;for await(const chunk of req){size+=chunk.length;if(size>limit)throw new Error('请求内容过大');chunks.push(chunk);}return JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}');}
  const send=(res,status,value)=>{res.statusCode=status;res.setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(value));};
  async function handler(req,res) {
    res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
    res.setHeader('Referrer-Policy','same-origin');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'");
    try {
      const origin=ctx.webServer.getOrigin(), expected=new URL(origin);
      if(req.headers.host!==expected.host || (req.headers.origin && req.headers.origin!==origin)) return send(res,403,{error:'仅接受本机工作台来源'});
      const url=new URL(req.url,origin), route=url.pathname.replace(/\/$/,'');
      if(req.method==='GET' && route==='/teacher/console') {
        res.statusCode=302;res.setHeader('Location',ctx.connection.authenticatedUrl(origin));res.end();return;
      }
      if(req.method==='GET' && ['/teacher','/teacher/app.js','/teacher/feedback.js','/teacher/forms.js','/teacher/ppt.js','/teacher/tuning.js','/teacher/knowledge.js','/teacher/vision-queue.js','/teacher/style.css'].includes(route)) {
        const filename=route==='/teacher'?'index.html':route.split('/').pop();
        res.setHeader('Content-Type',mime[path.extname(filename)]);res.end(await readFile(path.join(here,'web',filename)));return;
      }
      if(req.method==='GET' && route==='/teacher/api/bootstrap') return send(res,200,{token,model:await modelStatus(),ppt:await ppt.status(),vision:await vision.status(),jobs:jobList().map(publicJob),root,host:'DeepSeek Harness',platform:process.platform,pid:process.pid});
      if(req.method==='GET' && route==='/teacher/api/tuning')return send(res,200,await tuning.status());
      if(req.method==='GET' && route==='/teacher/api/context-report'){const job=jobs.get(validId(url.searchParams.get('job')));if(!job)throw new Error('任务不存在');return send(res,200,{job_id:job.id,sampling:job.sampling||{},profiles:job.tuning?.stages||{},context:job.context_report||{}});}
      if(req.method==='GET' && route==='/teacher/api/vision/settings')return send(res,200,await vision.status());
      if(req.method==='GET' && route==='/teacher/api/vision/queue')return send(res,200,{jobs:visionQueue.list()});
      if(req.method==='GET' && route==='/teacher/api/vision/queue/preview')return send(res,200,await visionQueue.preview({knowledge_id:validId(url.searchParams.get('knowledge')),chapter_id:url.searchParams.get('chapter')||null}));
      if(req.method==='GET' && route==='/teacher/api/image/settings')return send(res,200,await imageGeneration.status());
      if(req.method==='GET' && route==='/teacher/api/vision/models')return send(res,200,await vision.listModels());
      if(req.method==='GET' && route==='/teacher/api/ppt/settings')return send(res,200,await ppt.status());
      if(req.method==='GET' && route==='/teacher/api/ppt/runs')return send(res,200,jobList().filter(j=>j.version_id===url.searchParams.get('version')&&j.ppt_run_id).map(publicJob));
      if(req.method==='GET' && route==='/teacher/api/ppt/pages')return send(res,200,await call('ppt-pages',{version_id:validId(url.searchParams.get('version'))}));
      if(req.method==='GET' && route==='/teacher/api/delivery'){
        const {receipt,...result}=await materials.describe(validId(url.searchParams.get('version')));return send(res,200,result);
      }
      if(req.method==='GET' && route==='/teacher/api/ppt/file') {
        const file=await ppt.getFile(validId(url.searchParams.get('run')),url.searchParams.get('name'));
        res.setHeader('Content-Type',mime[path.extname(file)]||'text/plain; charset=utf-8');
        res.setHeader('Content-Disposition',`attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}`);res.end(await readFile(file));return;
      }
      if(req.method==='GET' && route==='/teacher/api/overview') return send(res,200,await call('overview'));
      if(req.method==='GET' && route==='/teacher/api/knowledge/limits')return send(res,200,await knowledgeImport.limits());
      if(req.method==='GET' && route==='/teacher/api/knowledge/index')return send(res,200,await call('knowledge_index',{id:validId(url.searchParams.get('knowledge'))}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/chunks')return send(res,200,await call('knowledge_chunks',{id:validId(url.searchParams.get('knowledge')),chapter:url.searchParams.get('chapter'),offset:Number(url.searchParams.get('offset')||0),limit:Number(url.searchParams.get('limit')||30)}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/import-status')return send(res,200,await call('knowledge_import_status',{key:validId(url.searchParams.get('key'))}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/pdf')return send(res,200,await call('knowledge_pdf_structure',{id:validId(url.searchParams.get('knowledge'))}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/list') return send(res,200,await call('knowledge_list'));
      if(req.method==='GET' && route==='/teacher/api/knowledge/assets') return send(res,200,await call('knowledge_assets',{id:validId(url.searchParams.get('knowledge'))}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/text') return send(res,200,await call('knowledge_text',{id:validId(url.searchParams.get('knowledge'))}));
      if(req.method==='GET' && route==='/teacher/api/knowledge/asset') {
        const file=(await call('knowledge_asset_path',{id:validId(url.searchParams.get('knowledge')),name:url.searchParams.get('name')})).path;
        res.setHeader('Content-Type',mime[path.extname(file).toLowerCase()]||'application/octet-stream');
        res.setHeader('Cache-Control','private, max-age=60');res.end(await readFile(file));return;
      }
      if(req.method==='GET' && route==='/teacher/api/classes') return send(res,200,await call('classes_list'));
      if(req.method==='GET' && route==='/teacher/api/class-profile') return send(res,200,await call('class_profile',{id:validId(url.searchParams.get('id'))}));
      if(req.method==='GET' && route==='/teacher/api/entity') return send(res,200,await call('get',{kind:url.searchParams.get('kind'),id:validId(url.searchParams.get('id'))}));
      if(req.method==='GET' && route==='/teacher/api/density') return send(res,200,await call('density',{id:validId(url.searchParams.get('id'))}));
      if(req.method==='GET' && route==='/teacher/api/jobs') return send(res,200,jobList().map(publicJob));
      if(req.method==='GET' && route==='/teacher/api/api-policy') return send(res,200,await apiRuntime.status());
      if(req.method==='GET' && route==='/teacher/api/file') {
        const versionId=validId(url.searchParams.get('version'));
        const filename=url.searchParams.get('name');
        if(!allowedFiles.has(filename)) throw new Error('无可下载文件');
        const base=await realpath(path.join(root,'deliverables'));
        const file=await realpath(await materials.file(versionId,filename));
        const relative=path.relative(base,file);if(relative.startsWith('..')||path.isAbsolute(relative))throw new Error('文件不在交付目录');
        res.setHeader('Content-Type',mime[path.extname(filename)]||'text/plain; charset=utf-8');
        if(filename!=='内容预览.html')res.setHeader('Content-Disposition',`attachment; filename*=UTF-8''${encodeURIComponent(filename)}`);
        // Preview exports escape all teacher/model content, and cannot run scripts.
        if(filename==='内容预览.html')res.setHeader('Content-Security-Policy',"sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'");
        res.end(await readFile(file));return;
      }
      if(req.method!=='POST')return send(res,404,{error:'未找到该入口'});
      if(req.headers['x-teacher-token']!==token)return send(res,403,{error:'页面已过期，请刷新工作台'});
      if(route==='/teacher/api/knowledge/upload-file'){
        if(!(req.headers['content-type']||'').startsWith('application/octet-stream'))return send(res,415,{error:'教材上传需要application/octet-stream'});
        return send(res,200,await knowledgeImport.upload(req,Object.fromEntries(url.searchParams)));
      }
      if(!(req.headers['content-type']||'').startsWith('application/json'))return send(res,415,{error:'需要 JSON 请求'});
      const data=await readBody(req);
      let result;
      if(route==='/teacher/api/tuning')result=await tuning.save(data);
      else if(route==='/teacher/api/api-policy')result=await apiRuntime.configure(data);
      else if(route==='/teacher/api/ppt/settings')result=await ppt.saveSettings(data);
      else if(route==='/teacher/api/image/settings')result=await imageGeneration.saveSettings(data);
      else if(route==='/teacher/api/image/test-connection')result=await imageGeneration.testConnection();
      else if(route==='/teacher/api/ppt/test-connection')result=await ppt.testConnection();
      else if(route==='/teacher/api/vision/settings')result=await vision.saveSettings(data);
      else if(route==='/teacher/api/vision/queue/start')result=await visionQueue.start({...data,knowledge_id:validId(data.knowledge_id)});
      else if(route==='/teacher/api/vision/queue/control')result=await visionQueue.control(validId(data.id),data.action,data);
      else if(route==='/teacher/api/knowledge/describe')result={queued:true,job:await visionQueue.start({knowledge_id:validId(data.id),key:data.key||'legacy_'+randomUUID(),max_images:Math.max(1,Math.min(24,Number(data.limit)||6))}),described:0,errors:[]};
      else if(route==='/teacher/api/settings')result=await textModels.save(data);
      else if(route==='/teacher/api/test-connection')result=await textModels.testConnection();
      else if(route==='/teacher/api/create')result=await call('create',{payload:data.payload,key:validId(data.key)});
      else if(route==='/teacher/api/lesson/update') {
        const lessonId=validId(data.id),key=validId(data.key);
        result=await serialStart(async()=>{
          let lesson;
          try{lesson=await call('lesson_context',{id:lessonId});}catch(error){throw new Error('课程不存在');}
          const versionIds=new Set(lesson.versions.map(v=>v.id));
          const active=[...jobs.values()].find(j=>['queued','running'].includes(j.status)&&(j.target===lessonId||versionIds.has(j.target)||(j.version_id&&versionIds.has(j.version_id))));
          if(active)throw new Error('该课程正在生成，请完成后再修改课程信息。');
          return call('update_lesson',{id:lessonId,payload:data.payload,key});
        });
      }
      else if(route==='/teacher/api/lesson/delete') {
        const lessonId=validId(data.id);
        result=await serialStart(async()=>{
          const active=[...jobs.values()].find(j=>['queued','running'].includes(j.status));
          if(active)throw new Error('还有生成任务在执行，请先停止后再删除课程。');
          let lesson;
          try{lesson=await call('lesson_context',{id:lessonId});}catch(error){throw new Error('课程不存在');}
          const versionIds=new Set(lesson.versions.map(v=>v.id));
          const related=[...jobs.values()].filter(j=>j.target===lessonId||versionIds.has(j.target)||(j.version_id&&versionIds.has(j.version_id)));
          for(const job of jobs.values()){
            if(job.kind==='revise'&&!related.includes(job)){
              try{
                const decision=await call('get',{kind:'decision',id:job.target});
                const feedback=await call('get',{kind:'feedback',id:decision.feedback_id});
                if(feedback.lesson_id===lessonId)related.push(job);
              }catch(error){/* decision may already be gone; it cannot belong to another lesson here */}
            }
          }
          const deleted=await call('delete_lesson',{id:lessonId});
          const runIds=new Set();
          for(const job of related){
            if(job.ppt_run_id)runIds.add(job.ppt_run_id);
            if(['ppt','prompt','repair_ppt','complete'].includes(job.kind))runIds.add(job.id);
            jobs.delete(job.id);
            await rm(path.join(jobDir,job.id+'.json'),{force:true}).catch(()=>{});
            await designDrafts.remove(job.id);
          }
          for(const runId of runIds){
            if(/^task_[\w-]{1,90}$/.test(runId))await rm(path.join(root,'data','ppt-runs',runId),{recursive:true,force:true}).catch(()=>{});
          }
          for(const versionId of versionIds)await rm(path.join(root,'data','locks',versionId+'.lock'),{force:true}).catch(()=>{});
          return {...deleted,jobs_removed:related.length};
        });
      }
      else if(route==='/teacher/api/feedback')result=await call('feedback',{id:validId(data.id),payload:data.payload,key:validId(data.key)});
      else if(route==='/teacher/api/decide')result=await call('decide',{id:validId(data.id),payload:data.payload,key:validId(data.key)});
      else if(route==='/teacher/api/export'){await call('export',{id:validId(data.id)});result=await call('check',{id:data.id});}
      else if(route==='/teacher/api/upload') {
        const filename=path.basename(String(data.name||''));const extension=path.extname(filename).toLowerCase();
        if(!['.txt','.md','.pdf','.docx'].includes(extension))throw new Error('支持 TXT、MD、DOCX 和文字型 PDF');
        const bytes=Buffer.from(data.content_base64||'','base64');if(bytes.length>25*1024*1024||!bytes.length)throw new Error('资料应为非空文件，且小于25MB');
        if(/[<>:"/\\|?*]/.test(filename))throw new Error('资料文件名包含 Windows 不支持的字符');
        const input=path.join(root,'inputs','upload_'+randomUUID(),filename);await mkdir(path.dirname(input),{recursive:true});await writeFile(input,bytes);
        result=await call('ingest',{payload:input,key:'upload_'+randomUUID()});result.original_name=filename;
      } else if(route==='/teacher/api/knowledge/unit') {
        result=await call('knowledge_unit',{id:validId(data.id),start_page:data.start_page,end_page:data.end_page,title:data.title,key:validId(data.key)});
      } else if(route==='/teacher/api/knowledge/upload') {
        const filename=path.basename(String(data.name||''));const extension=path.extname(filename).toLowerCase();
        if(!['.txt','.md','.pdf','.docx'].includes(extension))throw new Error('知识库支持 TXT、MD、DOCX 和文字型 PDF');
        const bytes=Buffer.from(data.content_base64||'','base64');if(bytes.length>25*1024*1024||!bytes.length)throw new Error('知识库文件应为非空文件，且小于25MB');
        if(/[<>:"/\\|?*]/.test(filename))throw new Error('知识库文件名包含 Windows 不支持的字符');
        const input=path.join(root,'inputs','upload_'+randomUUID(),filename);await mkdir(path.dirname(input),{recursive:true});await writeFile(input,bytes);
        result=await call('knowledge_create',{path:input,meta:{title:data.title,subject:data.subject,grade:data.grade},key:validId(data.key)});
        result.original_name=filename;
      } else if(route==='/teacher/api/knowledge/delete') {
        result=await call('knowledge_delete',{id:validId(data.id)});
      } else if(route==='/teacher/api/knowledge/asset/update') {
        result=await call('knowledge_asset_update',{id:validId(data.knowledge),asset_id:String(data.asset||''),fields:{caption:data.caption,dialogue:data.dialogue,description:data.description}});
      } else if(route==='/teacher/api/knowledge/refresh') {
        result=await call('knowledge_refresh',{id:validId(data.id)});
      } else if(route==='/teacher/api/class/update') {
        result=await call('class_update',{id:validId(data.id),payload:data.payload,key:validId(data.key)});
      } else if(route==='/teacher/api/start') {
        result=await serialStart(async()=>{
        if(!['generate','revise','ppt','prompt','repair_ppt','complete','local_ppt'].includes(data.kind))throw new Error('未知任务类型');validId(data.target);validId(data.key);
        const restart=data.restart===true;
        if(data.kind!=='complete'&&restart)throw new Error('只有继续生成整套材料时可以重新制作课件');
        let job=[...jobs.values()].find(j=>j.request_key===data.key);
        if(job && (job.kind!==data.kind||job.target!==data.target||Boolean(job.restart)!==restart||(job.source_run_id||null)!==(data.source_run_id||null)||(job.resume_run_id||null)!==(data.resume_run_id||null)))throw new Error('重复请求标识与原任务不符');
        if(!job){
          if(!['repair_ppt','complete','local_ppt'].includes(data.kind)&&!(await modelStatus()).configured)throw new Error('请先打开模型设置，在本机填写 SiliconFlow API Key。');
          if(data.kind==='complete'&&(data.resume_run_id||data.source_run_id))throw new Error('继续生成整套材料时无需选择中间任务');
          if(data.kind==='local_ppt'&&(!data.assets_by_page||typeof data.assets_by_page!=='object'||Array.isArray(data.assets_by_page)))throw new Error('请先选择每页使用的教材素材');
          if(data.kind==='repair_ppt'&&(!data.resume_run_id||data.source_run_id))throw new Error('本地修复需要选择本版已有的候选课件');
          if(data.kind==='ppt'){const provider=await ppt.status();if(provider.provider!=='native'&&!provider.configured)throw new Error('请先保存课件服务配置');}
          const active=[...jobs.values()].find(j=>['queued','running'].includes(j.status));if(active)throw new Error('已有生成任务在执行，请完成或停止后继续。');
          await jobContext(data.kind,data.target);
          job={id:'task_'+randomUUID().replaceAll('-',''),request_key:data.key,kind:data.kind,target:data.target,status:'queued',stage:'等待生成',created_at:new Date().toISOString()};
          if(data.kind==='complete'&&restart)job.restart=true;
          if(data.kind==='local_ppt')job.assets_by_page=data.assets_by_page;
          if(data.resume_run_id){validId(data.resume_run_id);const original=jobList().find(j=>j.ppt_run_id===data.resume_run_id&&j.version_id===data.target);if(!original||!['ppt','prompt','repair_ppt'].includes(data.kind))throw new Error('不能继续其他教学版本的课件任务');if(data.kind==='repair_ppt'&&!jobList().some(j=>j.ppt_run_id===data.resume_run_id&&j.ppt_candidate_ready))throw new Error('该任务尚未下载候选课件');job.resume_run_id=data.resume_run_id;}
          if(data.source_run_id){validId(data.source_run_id);const original=jobList().find(j=>j.ppt_run_id===data.source_run_id&&j.version_id===data.target&&j.prompt_ready);if(!original)throw new Error('找不到本版已保存的提示词');job.source_run_id=data.source_run_id;}
          jobs.set(job.id,job);await persist(job);void run(job);
        }
        return publicJob(job);
        });
      } else if(route==='/teacher/api/cancel') {validId(data.id);const controller=controllers.get(data.id);if(!controller)throw new Error('该任务未在生成中');jobs.get(data.id).cancel_requested=true;controller.abort();handles.get(data.id)?.agent.cancel({kind:'user'});result={requested:true};}
      else return send(res,404,{error:'未找到操作'});
      send(res,200,result);
    } catch(error){send(res,error.status||(error.code==='materials_not_ready'?409:400),{error:messageOf(error),
      code:error.code||error.failure?.code||'request_failed',...(error.request_id?{request_id:error.request_id}:{}),
      ...(Number.isFinite(error.retryAfterMs)?{retry_after_ms:error.retryAfterMs}:{})});}
  }
  ctx.effect(()=>ctx.webServer.register({kind:'prefix',path:'/teacher',handler}));
}
