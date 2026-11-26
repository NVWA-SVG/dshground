import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {DEFAULT_PPT_CONFIG,normalizePptConfig,presentationProvider,generatePresentation,checkPresentationConnection} from './ppt-providers.mjs';
import {createImageService} from './image-service.mjs';
import {buildVisualTask} from './prompts.mjs';
import {createTuning,installSampling,validationFeedback} from './tuning.mjs';
import {packVisualContext,objectToolSchema} from './context.mjs';
import {modelEndError,lastAttemptFailure} from './task-failures.mjs';
import {mountTeacherAgent} from './teacher-agent.mjs';

async function readOptional(file) {try{return JSON.parse(await readFile(file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;return null;}}
async function atomicJson(file,value) {const tmp=file+'.'+randomUUID()+'.tmp';await writeFile(tmp,JSON.stringify(value,null,2));await rename(tmp,file);}

export function createPptService({ctx,root,call,pluginDir,persist,handles}) {
  const images=createImageService({ctx,root});
  const file=path.join(root,'config','presentation-api.json');
  const config=async()=>normalizePptConfig({...DEFAULT_PPT_CONFIG,...await readOptional(file)});
  const directory=id=>{if(!/^task_[\w-]{1,90}$/.test(id))throw new Error('无效课件任务编号');return path.join(root,'data','ppt-runs',id);};
  async function status() {
    const c=await config();const keyName=c.provider==='gamma'?'GAMMA_API_KEY':'CUSTOM_PPT_API_KEY';
    const credential=c.provider==='native'?{configured:false}:await ctx.credentials.describe(keyName);
    return {...c,configured:credential.configured};
  }
  async function saveSettings(data) {
    const c=normalizePptConfig({...data,auto_generate:true});
    if(data.api_key){if(c.provider==='native')throw new Error('请先选择Gamma或自定义服务再保存密钥');const value=String(data.api_key).trim();if(value.length<8||/[\r\n]/.test(value))throw new Error('API Key格式不正确');await ctx.credentials.set(c.provider==='gamma'?'GAMMA_API_KEY':'CUSTOM_PPT_API_KEY',value);}
    await atomicJson(file,c);return status();
  }
  async function testConnection() {
    const c=await config();const provider=presentationProvider(c);const credential=await ctx.credentials.resolve(provider.credential);
    if(!credential?.value)throw new Error('请先保存PPT服务密钥');
    return checkPresentationConnection(c,credential.value);
  }
  async function visualPlan(job,runId,briefData,signal) {
    const existing=await readOptional(path.join(directory(runId),'visual-plan.json'));
    if(existing)return;
    const imageConfig=await images.config();
    const reuse=briefData.brief.version_number>1?await call('ppt-incremental-plan',{run_id:runId},signal):null;
    if(job.kind==='revise'&&!reuse){job.optimization={...(job.optimization||{}),reused_pages:0,
      planned_pages:briefData.brief.pages.length,reuse_reason:'no_compatible_parent_plan'};await persist(job);}
    if(reuse){
      job.optimization={...(job.optimization||{}),reused_pages:reuse.reused_pages.length,planned_pages:reuse.pending_pages.length};await persist(job);
      if(!reuse.pending_pages.length){
        const result=await call('ppt-reuse-all',{run_id:runId,image_config:imageConfig},signal);
        job.optimization.reused_images=result.reused_images;await persist(job);return;
      }
      briefData=structuredClone(briefData);
      briefData.brief.pages=briefData.brief.pages.filter(p=>reuse.pending_pages.includes(p.number));
      briefData.brief.incremental_visual={reused_pages:reuse.reused_pages.length,pending_pages:reuse.pending_pages,style:reuse.style,palette:reuse.palette};
      // Enumerate only dirty pages in the model schema; retained plans never enter its context.
      briefData.schema=structuredClone(briefData.schema);
      briefData.schema.$defs.PageVisual.properties.page_number.enum=reuse.pending_pages;
      briefData.schema.properties.pages.minItems=reuse.pending_pages.length;briefData.schema.properties.pages.maxItems=reuse.pending_pages.length;
      briefData.schema.properties.style={...briefData.schema.properties.style,const:reuse.style};
      briefData.schema.properties.palette={...briefData.schema.properties.palette,const:reuse.palette};
      const usedImages=new Set(reuse.reused_pages.filter(p=>p.image_prompt?.trim()).map(p=>p.image_prompt)).size;
      const remaining=Math.max(0,(briefData.image_generation?.max_images||0)-usedImages);
      briefData.image_generation={enabled:!!briefData.image_generation?.enabled&&remaining>0,max_images:remaining};
    }
    const tune=job.tuning||await createTuning({root}).load(),profile=tune.stages.visual;
    const packed=await packVisualContext(briefData,briefData.schema||{},tune);briefData=packed.data;
    job.context_report={...(job.context_report||{}),visual:packed.report};await persist(job);
    const skill=await readFile(path.join(pluginDir,'skills','teacher-ppt','SKILL.md'),'utf8');
    let saved=false,handle,deadline,validationFailures=0,lastError;
    const reject=error=>{const value=validationFeedback(error,++validationFailures,tune.max_validation_attempts);lastError=value.error;if(validationFailures>=tune.max_validation_attempts)handle?.agent.cancel({kind:"hook",reason:"视觉方案校验重试达到上限"});return JSON.stringify(value);};
    try {
      handle=await ctx.agents.create({sessionId:'ppt_'+randomUUID().replaceAll('-',''),meta:{cwd:root,agentPreset:'teacher'},
        agentOptions:{provider:job.provider||'siliconflow',model:job.model||'Qwen/Qwen3-30B-A3B-Instruct-2507',maxTokens:profile.max_tokens},
        setup:async agentCtx=>{await mountTeacherAgent(ctx,agentCtx);installSampling(agentCtx,profile,async applied=>{job.sampling={...(job.sampling||{}),visual:applied};await persist(job);});agentCtx.tools.restrict({allow:[]});agentCtx.tools.register({name:'teacher_save_ppt_prompt',description:'在 plan 字段提交逐页视觉方案对象，生成课件提示词MD；不得改变固定教学正文。plan 必须严格符合本次 schema。',
          parameters:objectToolSchema('plan',briefData.schema||{type:'object'}),
          output:{schema:{type:'string'},render:(_a,v)=>[{type:'text',text:v}]},execute:async(args,exec)=>{
            if(saved){exec.concludeTurn?.();return JSON.stringify({ok:true});}
            const payload=args?.plan;
            if(!payload||typeof payload!=='object')return reject(new Error('请在 plan 字段提交完整视觉方案对象，不要提交空参数'));
            try{
              if(briefData.brief.planning_version>=2)for(const page of payload.pages||[]){
                for(const field of ['teaching_goal','student_task','learning_focus','asset_reason','density_note'])if(typeof page[field]!=='string'||!page[field].trim())throw new Error(`第${page.page_number}页缺少${field}；请具体说明本页教学任务、观察重点和视觉选择依据`);
              }
              const allowedAssets=new Set((briefData.assets||[]).map(a=>a.id));if((payload.pages||[]).some(p=>(p.asset_ids||[]).some(id=>!allowedAssets.has(id))))throw new Error('只能选择本次候选目录中的教材素材标识');
              const requested=(payload.pages||[]).filter(p=>p.image_prompt?.trim());
              if(requested.length&&(!briefData.image_generation?.enabled||requested.length>briefData.image_generation.max_images))return reject(new Error('请遵守本次生图权限和张数上限，删除不必要的image_prompt后重新提交'));
              if(reuse&&[...payload.pages||[]].map(p=>p.page_number).join(',')!==reuse.pending_pages.join(','))throw new Error('仅提交本次待规划页码：'+reuse.pending_pages.join(','));
              const result=await call(reuse?'ppt-save-incremental':'ppt-save-prompt',{run_id:runId,payload,...(reuse?{image_config:imageConfig}:{})},exec.signal);
              if(reuse){job.optimization.reused_images=result.reused_images;await persist(job);}saved=true;exec.concludeTurn?.();return JSON.stringify({ok:true,...result});}
            catch(e){return reject(e);}
          }});}});
      handles.set(job.id,handle);job.ppt_session_id=handle.agent.id;job.phase='presentation';job.stage='正在规划课件版式与配图';await persist(job);
      const abort=()=>handle.agent.cancel({kind:'user'});signal?.addEventListener('abort',abort,{once:true});
      deadline=setTimeout(()=>handle.agent.cancel({kind:'hook',reason:'课件提示词生成超时'}),600000);
      const prompt=await buildVisualTask(briefData,skill);
      try{handle.agent.followup({id:randomUUID(),role:'user',source:{kind:'user'},content:[{type:'text',text:prompt}]});await handle.agent.whenIdle();signal?.throwIfAborted();}
      finally{signal?.removeEventListener('abort',abort);}
      if(!saved&&validationFailures>=tune.max_validation_attempts)throw new Error('视觉方案校验达到重试上限：'+lastError);
      if(!saved){
        const events=handle.agent.session?.snapshotEvents?.()||[],end=[...events].reverse().find(e=>e.type==='turn/end');
        job.diagnostic=end?.data||null;
        job.request_failure=lastAttemptFailure(events);
        throw modelEndError(end?.data?.reason,{provider:job.provider||'siliconflow',stage:'课件视觉方案',requestFailure:job.request_failure});
      }
    } finally {clearTimeout(deadline);handles.delete(job.id);if(handle)await handle.dispose().catch(()=>{});}
  }
  async function execute(job,versionId,{promptOnly=false,automatic=false,signal}={}) {
    const c=await config();
    const runId=job.resume_run_id||job.id;
    job.ppt_run_id=runId;job.version_id=versionId;
    const already=await readOptional(path.join(directory(runId),'result.json'));
    if(already){job.ppt_status='ready';job.ppt_result=already;return already;}
    // A resumed request keeps its original immutable style and prompt.
    const original=await readOptional(path.join(directory(runId),'brief.json'));
    const prepared=await call('ppt-prepare',{version_id:versionId,run_id:runId,style:original?.requested_style??c.style},signal);
    if(job.source_run_id){const source=await call('ppt-read-prompt',{run_id:job.source_run_id},signal);if(source.brief.version_id!==versionId||source.brief.design_hash!==prepared.brief.design_hash)throw new Error('不能将其他教学版本的提示词用于本版课件');await call('ppt-save-prompt',{run_id:runId,payload:source.plan},signal);}
    const hasAssets=Boolean(prepared.assets?.length),assetMode=c.asset_mode||'auto';
    const wantsProvider=c.provider!=='native'&&(assetMode==='provider'||(assetMode==='auto'&&!hasAssets));
    const imageConfig=await images.config();
    prepared.image_generation={enabled:!wantsProvider&&imageConfig.enabled,max_images:!wantsProvider&&imageConfig.enabled?imageConfig.max_images:0};
    await visualPlan(job,runId,prepared,signal);
    job.prompt_ready=true;await persist(job);
    if(promptOnly){job.ppt_status='prompt_ready';return {run_id:runId,prompt_ready:true,external_generated:false};}
    if(!wantsProvider){
      if(imageConfig.enabled)await images.generate(runId,{signal,onStage:async stage=>{job.stage=stage;job.phase='presentation';await persist(job);}});
      job.stage='正在按页面方案合成教学课件';job.phase='presentation';await persist(job);
      const local=await call('ppt-compose-local',{run_id:runId,version_id:versionId},signal);
      if(local){job.ppt_status='ready';job.ppt_result=local;return local;}
      throw new Error('本地课件尚未完成，请继续生成整套材料');
    }
    const provider=presentationProvider(c);
    const credential=await ctx.credentials.resolve(provider.credential);
    if(!credential?.value){job.ppt_status='awaiting_configuration';throw new Error('课程内容已保存。请在生成设置中完成课件服务配置，再继续生成整套材料。');}
    const remoteFile=path.join(directory(runId),'remote.json');
    const remote=await readOptional(remoteFile);
    const data={...await call('ppt-read-prompt',{run_id:runId},signal),run_id:runId,assets:prepared.assets||[]};
    job.ppt_status='generating';await persist(job);
    const result=await generatePresentation({data,config:c,key:credential.value,remote,signal,
      saveRemote:async value=>{await atomicJson(remoteFile,value);job.ppt_remote_id=value.generation_id||null;await persist(job);},
      onStage:async stage=>{job.stage=stage;job.phase=/下载|校验/.test(stage)?'validation':'presentation';await persist(job);},
      writeDownload:async bytes=>{await writeFile(path.join(directory(runId),'download.pptx'),bytes);job.ppt_candidate_ready=true;await persist(job);},
      accept:async params=>{try{return await call('ppt-accept',{run_id:runId,...params},signal);}catch(error){
        if(c.provider!=='gamma'||error.code!=='ppt_text_mismatch')throw error;
        job.phase='validation';job.stage='正在校对课件正文与讲稿';await persist(job);
        return call('ppt-repair',{run_id:runId,...params},signal);
      }}});
    job.ppt_status='ready';job.ppt_result=result;return result;
  }
  async function repair(job,versionId,signal) {
    const runId=job.resume_run_id;
    const dir=directory(runId);
    const brief=await readOptional(path.join(dir,'brief.json'));
    if(!brief||brief.version_id!==versionId)throw new Error('不能修复其他教学版本的课件');
    const remote=await readOptional(path.join(dir,'remote.json'));
    if(!remote?.generation_id)throw new Error('缺少原课件任务记录，请检查候选文件');
    job.version_id=versionId;job.ppt_run_id=runId;job.ppt_remote_id=remote.generation_id;
    job.prompt_ready=true;job.ppt_candidate_ready=true;job.ppt_status='repairing';
    job.phase='validation';job.stage='正在校对课件正文与讲稿';await persist(job);
    const result=await call('ppt-repair',{run_id:runId,provider:remote.identity,remote_id:remote.generation_id,credits:remote.credits??null},signal);
    job.ppt_status='ready';job.ppt_result=result;
    await atomicJson(path.join(dir,'remote.json'),{...remote,phase:'completed'});
    return result;
  }
  async function composeManual(job,versionId,mapping,signal) {
    const runId=job.id;
    job.ppt_run_id=runId;job.version_id=versionId;
    job.phase='presentation';job.stage='正在按所选教材素材合成课件';job.ppt_status='composing';await persist(job);
    const result=await call('ppt-compose-manual',{version_id:versionId,run_id:runId,mapping},signal);
    if(!result)throw new Error('没有可用的教材素材：请先在知识库查看素材或选择其他图片。');
    job.prompt_ready=true;job.ppt_status='ready';job.ppt_result=result;return result;
  }
  async function complete(job,versionId,existing,signal,{restart=false}={}) {
    const runs=[...new Map(existing.filter(j=>j.version_id===versionId&&j.ppt_run_id).map(j=>[j.ppt_run_id,j])).values()].reverse();
    // Retry from a fresh run: reuse only the already-reviewed prompt, never the
    // failed remote task or candidate file. A new provider task may be charged.
    if(restart){
      const prompt=runs.find(j=>j.prompt_ready);
      if(prompt)job.source_run_id=prompt.ppt_run_id;
      job.restart=true;job.ppt_status='generating';
      return execute(job,versionId,{automatic:true,signal});
    }
    const accepted=runs.find(j=>j.ppt_result?.valid);
    if(accepted){job.ppt_run_id=accepted.ppt_run_id;job.ppt_result=accepted.ppt_result;job.ppt_status='ready';return accepted.ppt_result;}
    const candidate=runs.find(j=>j.ppt_candidate_ready);
    if(candidate){job.resume_run_id=candidate.ppt_run_id;return repair(job,versionId,signal);}
    const remote=runs.find(j=>j.ppt_remote_id);
    if(remote)job.resume_run_id=remote.ppt_run_id;
    else {
      const prompt=runs.find(j=>j.prompt_ready);
      if(prompt)job.resume_run_id=prompt.ppt_run_id;
    }
    return execute(job,versionId,{automatic:true,signal});
  }
  async function getFile(runId,name) {
    const dir=directory(runId);
    const promptNames=new Set(['课件提示词.md','visual-plan.json','brief.json','download.pptx']);
    if(promptNames.has(name))return path.join(dir,name);
    const result=await readOptional(path.join(dir,'result.json'));
    if(!result||!new Set([...result.manifest.files.map(f=>f.name),'交付清单.json','交付包.zip']).has(name))throw new Error('该任务没有此交付文件');
    const resolved=path.resolve(result.directory,name);const expected=path.resolve(root,'deliverables')+path.sep;if(!resolved.startsWith(expected))throw new Error('文件不在交付目录');return resolved;
  }
  return {status,saveSettings,testConnection,execute,repair,complete,composeManual,getFile,config};
}
