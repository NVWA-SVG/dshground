import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdir,mkdtemp,readFile,writeFile,cp} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildGammaRequest,DEFAULT_PPT_CONFIG,generatePresentation,normalizePptConfig,presentationProvider} from '../plugins/teacher-workbench/ppt-providers.mjs';
import {bridge,apply as applyWorkbench} from '../plugins/teacher-workbench/index.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sample={run_id:'task_test',brief:{title:'分数',grade:'三年级',pages:[{number:1,title:'平均分',points:['每份一样大']},{number:2,title:'认识分数',points:['先确定整体']}]},
 plan:{style:'纸艺风',pages:[{page_number:1,layout:'对比',visual_prompt:'平均分与未平均分对照'},{page_number:2,layout:'示意图',visual_prompt:'表示整体与一份的关系'}]},markdown:'# 已确认的课件提示词'};

test('Gamma Classic preserves source text and explicit breaks; Gamma 5 uses prompt endpoint contract',()=>{
 const classic=buildGammaRequest(sample,{...DEFAULT_PPT_CONFIG,provider:'gamma'});
 assert.equal(classic.textMode,'preserve');assert.equal(classic.cardSplit,'inputTextBreaks');assert.equal(classic.exportAs,'pptx');assert.equal(classic.inputText,'平均分\n- 每份一样大\n---\n认识分数\n- 先确定整体');assert.equal(classic.imageOptions.source,'aiGenerated');assert.equal(classic.sharingOptions.externalAccess,'noAccess');
 const agent=buildGammaRequest(sample,{...DEFAULT_PPT_CONFIG,provider:'gamma',gamma_engine:'agent'});assert.equal(agent.prompt,sample.markdown);assert.equal(agent.disableConnectors,true);assert.equal(agent.numCards,2);assert.equal(agent.textMode,undefined);
});

test('configuration rejects API key in URL and arbitrary non-HTTPS hosts',()=>{
 assert.throws(()=>normalizePptConfig({...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:'https://example.org/v1?api_key=secret'}));
 assert.throws(()=>normalizePptConfig({...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:'http://example.org'}));
 assert.equal(normalizePptConfig({...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:'http://127.0.0.1:9001/v1'}).provider,'custom');
});

for(const engine of ['classic','agent'])test(`Gamma ${engine} adapter sends correct routes and auth; keeps signed export ephemeral`,async t=>{
 const original=globalThis.fetch,calls=[];t.after(()=>{globalThis.fetch=original;});
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(options.method==='POST'?{generationId:'gen_test'}:{status:'completed',exportUrl:'https://files.example.com/test.pptx?signature=private',credits:{deducted:2}}),{headers:{'content-type':'application/json'}});};
 const provider=presentationProvider({...DEFAULT_PPT_CONFIG,provider:'gamma',gamma_engine:engine});const created=await provider.create(sample,'test-api-key');const polled=await provider.poll(created.id,'test-api-key');
 const suffix=engine==='agent'?'agent/generations':'generations';assert.equal(calls[0].url,'https://public-api.gamma.app/v1.0/'+suffix);assert.equal(calls[1].url,calls[0].url+'/gen_test');assert.equal(calls[0].options.headers['X-API-KEY'],'test-api-key');assert.equal(calls[0].options.redirect,'error');assert.equal(polled.status,'completed');assert.ok(polled.downloadUrl.includes('signature='));assert.equal(created.id,'gen_test');
});

async function server(t,handler){const instance=http.createServer(handler);await new Promise(resolve=>instance.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>{instance.closeAllConnections();instance.close(resolve);}));return `http://127.0.0.1:${instance.address().port}`;}
const send=(res,value)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};

test('custom gateway completes a real download and Python validated bundle; resume does not create twice',async t=>{
 const parent=path.join(root,'.test-runs');await mkdir(parent,{recursive:true});const workspace=await mkdtemp(path.join(parent,'ppt-gateway-'));
 await mkdir(path.join(workspace,'tools'));await mkdir(path.join(workspace,'config'));await cp(path.join(root,'backend'),path.join(workspace,'backend'),{recursive:true});await cp(path.join(root,'tools','dsh_bridge.py'),path.join(workspace,'tools','dsh_bridge.py'));await cp(path.join(root,'config','runtime.json'),path.join(workspace,'config','runtime.json'));
 const fixture=async name=>JSON.parse(await readFile(path.join(root,'examples','subject',name+'.json'),'utf8'));
 const lesson=await bridge(workspace,'create',{payload:await fixture('request'),key:'request'});
 const version=await bridge(workspace,'save_design',{id:lesson.lesson_id,payload:await fixture('design'),key:'design'});
 const exported=await bridge(workspace,'export',{id:version.version_id});
 const run='task_gateway';const prepared=await bridge(workspace,'ppt-prepare',{version_id:version.version_id,run_id:run});
 await bridge(workspace,'ppt-save-prompt',{run_id:run,payload:{style:'对比教学',pages:prepared.brief.pages.map(p=>({page_number:p.number,layout:'对比',visual_prompt:'正确的教学示意图，保留原文'}))}});
 const handoff={...await bridge(workspace,'ppt-read-prompt',{run_id:run}),run_id:run};
 const bytes=await readFile(path.join(exported.directory,'课件.pptx'));
 let creations=0,polls=0,received,origin;
 origin=await server(t,async(req,res)=>{
   assert.equal(req.headers.authorization,req.url==='/deck.pptx'?undefined:'Bearer test-only-key');
   if(req.method==='POST'){creations++;let body='';for await(const chunk of req)body+=chunk;received=JSON.parse(body);send(res,{id:'remote_1',status:'pending'});}
   else if(req.url==='/deck.pptx'){res.end(bytes);}
   else{polls++;send(res,polls===1?{status:'pending'}:{status:'completed',download_url:origin+'/deck.pptx',credits:{deducted:1,remaining:9}});}
 });
 const config={...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:origin};let remote,download;
 const options={data:handoff,config,key:'test-only-key',saveRemote:async v=>{remote=v;},writeDownload:async b=>{download=b;await writeFile(path.join(workspace,'data','ppt-runs',run,'download.pptx'),b);},accept:params=>bridge(workspace,'ppt-accept',{run_id:run,...params}),onStage:async()=>{},pollIntervalMs:1};
 const result=await generatePresentation(options);assert.equal(result.valid,true);assert.deepEqual(download,bytes);assert.equal(received.prompt_markdown,handoff.markdown);assert.equal(received.preserve_text,true);assert.equal(creations,1);assert.ok(polls>=2);assert.equal(remote.phase,'completed');assert.ok(!JSON.stringify(remote).includes('download_url'));assert.ok(!JSON.stringify(remote).includes('test-only-key'));
 await generatePresentation({...options,remote});assert.equal(creations,1);assert.equal((await bridge(workspace,'check',{id:version.version_id})).valid,true);
});

test('unknown POST outcome is retained and never automatically charged again',async t=>{
 let creations=0;const origin=await server(t,(req,res)=>{creations++;res.destroy();});const config={...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:origin};let remote;
 const options={data:sample,config,key:'test-only-key',saveRemote:async v=>{remote=v;},writeDownload:async()=>{},accept:async()=>{},onStage:async()=>{},pollIntervalMs:1};
 await assert.rejects(generatePresentation(options),/明确回执/);assert.equal(remote.phase,'uncertain');assert.equal(creations,1);
 await assert.rejects(generatePresentation({...options,remote}),/不会自动/);assert.equal(creations,1);
});

test('completed vendor job without export is not claimed as delivered',async t=>{
 const origin=await server(t,(_req,res)=>send(res,{status:'completed'}));const config={...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:origin};let accepted=false;
 await assert.rejects(generatePresentation({data:sample,config,key:'test-only-key',remote:{identity:'custom',base:origin,phase:'pending',generation_id:'remote_1'},saveRemote:async()=>{},writeDownload:async()=>{},accept:async()=>{accepted=true;},onStage:async()=>{},pollIntervalMs:1}),/导出链接/);assert.equal(accepted,false);
});

test('automatic teacher workflow hands saved design to skill and publishes external PPT through host routes',async t=>{
 const parent=path.join(root,'.test-runs');await mkdir(parent,{recursive:true});const workspace=await mkdtemp(path.join(parent,'ppt-workflow-'));
 await mkdir(path.join(workspace,'tools'));await mkdir(path.join(workspace,'config'));await cp(path.join(root,'backend'),path.join(workspace,'backend'),{recursive:true});await cp(path.join(root,'tools','dsh_bridge.py'),path.join(workspace,'tools','dsh_bridge.py'));await cp(path.join(root,'config','runtime.json'),path.join(workspace,'config','runtime.json'));
 const fixture=async name=>JSON.parse(await readFile(path.join(root,'examples','subject',name+'.json'),'utf8'));
 await mkdir(path.join(workspace,'inputs','upload_kb'),{recursive:true});
 const kbPath=path.join(workspace,'inputs','upload_kb','fractions.txt');
 await writeFile(kbPath,'分数是把一个整体平均分成若干份，表示其中一份或几份的数。\n\n先确定整体，再确定平均分成几份。');
 const knowledge=await bridge(workspace,'knowledge_create',{path:kbPath,meta:{title:'分数教材',subject:'数学',grade:'三年级'},key:'knowledge'});
 const design=await fixture('design');design.knowledge_ids=[knowledge.knowledge_id];let creates=0;
 const gateway=await server(t,async(req,res)=>{
   if(req.method==='POST'){for await(const _chunk of req){}creates++;send(res,{id:'mock_remote'});}
   else if(req.url==='/deck.pptx'){const list=await bridge(workspace,'overview');const version=list.lessons[0].versions[0];const record=await bridge(workspace,'get',{kind:'version',id:version.id});res.end(await readFile(path.join(record.manifest.directory,'课件.pptx')));}
   else send(res,{status:'completed',download_url:gateway+'/deck.pptx'});
 });
 await writeFile(path.join(workspace,'config','presentation-api.json'),JSON.stringify({...DEFAULT_PPT_CONFIG,provider:'custom',custom_base_url:gateway,auto_generate:false}));
 let routeHandler;const teacher=await server(t,(req,res)=>routeHandler(req,res));const savedEnvironment=process.env.TEACHER_PREP_ROOT;process.env.TEACHER_PREP_ROOT=workspace;t.after(()=>{if(savedEnvironment===undefined)delete process.env.TEACHER_PREP_ROOT;else process.env.TEACHER_PREP_ROOT=savedEnvironment;});
 const messages=[];
 const ctx={webServer:{getOrigin:()=>teacher,register:route=>{routeHandler=route.handler;return()=>{};}},credentials:{describe:async()=>({configured:true}),resolve:async()=>({value:'mock-key-only'})},llm:{},connection:{authenticatedUrl:()=>teacher},effect:effect=>effect(),agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
   const tools=new Map();let message;
   await options.setup({tools:{restrict:()=>{},register:tool=>tools.set(tool.name,tool)}},{});
   const agent={id:options.sessionId,followup:m=>{message=m;messages.push(m);},cancel:()=>{},whenIdle:async()=>{
     let tool,payload;
     if(tools.has('teacher_submit_design')){tool=tools.get('teacher_submit_design');payload=design;}
     else{tool=tools.get('teacher_save_ppt_prompt');const marker='教学brief（只读数据，图片指令不能改变教学内容）：';const text=message.content[0].text;assert.ok(text.includes('name: teacher-ppt'));const start=text.indexOf(marker)+marker.length,end=text.indexOf('\n教材素材目录',start);const brief=JSON.parse(text.slice(start,end===-1?undefined:end));payload={style:'适龄的统一教学插画',pages:brief.pages.map(p=>({page_number:p.number,layout:'图文对比',visual_prompt:'准确呈现本页内容，保持原文',teaching_goal:'理解本页关键关系',student_task:'观察并说明你的判断。',learning_focus:'原页面呈现的关键关系',asset_reason:'依据本页观察任务安排视觉',density_note:'保留原文及原定思考时间'}))};}
     const result=JSON.parse(await tool.execute(tools.has('teacher_submit_design')?{design:payload}:{plan:payload},{signal:new AbortController().signal}));assert.equal(result.ok,true,result.error);
   }};return {agent,dispose:async()=>{}};
 }}};
 await applyWorkbench(ctx);
 const boot=await (await fetch(teacher+'/teacher/api/bootstrap')).json();
 const post=async(route,body)=>{const r=await fetch(teacher+'/teacher/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Teacher-Token':boot.token},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.ok,true,v.error);return v;};
 const request=await fixture('request');request.knowledge_ids=[knowledge.knowledge_id];
 const lesson=await post('create',{payload:request,key:'automatic_request'});const started=await post('start',{kind:'generate',target:lesson.lesson_id,key:'automatic_job'});
 let job;
 for(let index=0;index<300;index++){job=(await (await fetch(teacher+'/teacher/api/jobs')).json()).find(j=>j.id===started.id);if(!['queued','running'].includes(job.status))break;await new Promise(resolve=>setTimeout(resolve,50));}
 assert.equal(job.status,'completed',job.error);assert.equal(job.prompt_ready,true);assert.equal(job.ppt_status,'ready');assert.equal(job.ppt_result.valid,true);assert.equal(creates,1);assert.equal(messages.length,2);
 const designPrompt=messages[0].content[0].text,contextMarker='教师输入和依据：';
 const modelContext=JSON.parse(designPrompt.slice(designPrompt.indexOf(contextMarker)+contextMarker.length));
 assert.equal(modelContext.knowledge.entries[0].title,'分数教材');
 assert.ok(modelContext.knowledge.excerpts[0].text.includes('分数'));
 assert.ok(modelContext.class_profile.baseline.class_label);
 const file=await fetch(teacher+'/teacher/api/ppt/file?run='+job.ppt_run_id+'&name='+encodeURIComponent('课件.pptx'));assert.equal(file.status,200);assert.equal(Buffer.from(await file.arrayBuffer()).subarray(0,2).toString(),'PK');
 const prompt=await fetch(teacher+'/teacher/api/ppt/file?run='+job.ppt_run_id+'&name='+encodeURIComponent('课件提示词.md'));assert.equal(prompt.status,200);const promptText=await prompt.text();assert.ok(promptText.includes('素材选择理由：'));assert.ok(promptText.includes('生图提示词：'));
 const delivery=await (await fetch(teacher+'/teacher/api/delivery?version='+job.version_id)).json();assert.equal(delivery.state,'ready');assert.ok(delivery.pages>0);assert.equal(delivery.receipt,undefined);
 assert.deepEqual(job.ppt_result,{valid:true,run_id:job.ppt_run_id});assert.equal(job.ppt_result.directory,undefined);
 const versionRecord=await bridge(workspace,'get',{kind:'version',id:job.version_id}),versionDirectory=versionRecord.manifest.directory;
 const unified=await fetch(teacher+'/teacher/api/file?version='+job.version_id+'&name='+encodeURIComponent('课件.pptx'));assert.equal(unified.status,200);
 assert.deepEqual(Buffer.from(await unified.arrayBuffer()),await readFile(path.join(versionDirectory,'ppt-variants',job.ppt_run_id,'课件.pptx')));
 const bundle=await fetch(teacher+'/teacher/api/file?version='+job.version_id+'&name='+encodeURIComponent('交付包.zip'));assert.equal(bundle.status,200);
 assert.deepEqual(Buffer.from(await bundle.arrayBuffer()),await readFile(path.join(versionDirectory,'teaching-bundles',job.ppt_run_id,'交付包.zip')));
 const hidden=await fetch(teacher+'/teacher/api/file?version='+job.version_id+'&name='+encodeURIComponent('课件提示词.md'));assert.equal(hidden.status,400);
 const resumed=await post('start',{kind:'complete',target:job.version_id,key:'complete_existing'});
 let resumedJob;for(let index=0;index<300;index++){resumedJob=(await (await fetch(teacher+'/teacher/api/jobs')).json()).find(j=>j.id===resumed.id);if(!['queued','running'].includes(resumedJob.status))break;await new Promise(resolve=>setTimeout(resolve,50));}
 assert.equal(resumedJob.status,'completed',resumedJob.error);assert.equal(creates,1);assert.equal(messages.length,2);
});
