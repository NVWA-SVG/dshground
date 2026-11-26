import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createImageService,normalizeImageConfig,imageRequestBody} from '../plugins/teacher-workbench/image-service.mjs';
const PNG=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6kAAAAABJRU5ErkJggg==','base64');
test('custom OpenAI image APIs get standard size and n fields without SiliconFlow-only options',()=>{
 const config=normalizeImageConfig({provider:'custom',base_url:'https://images.example.invalid/v1',api_format:'openai'});
 const body=imageRequestBody(config,'synthetic illustration');assert.equal(body.size,config.image_size);assert.equal(body.n,1);assert.equal(body.image_size,undefined);assert.equal(body.num_inference_steps,undefined);
 assert.equal(normalizeImageConfig({provider:'siliconflow',api_format:'openai'}).api_format,'siliconflow');
});
async function setup(t,prompts=['教学情景']){
  const root=await mkdtemp(path.resolve('.test-runs/images-'));await mkdir(path.join(root,'config'));
  const dir=path.join(root,'data','ppt-runs','task_images');await mkdir(dir,{recursive:true});
  await writeFile(path.join(root,'config','image-generation.json'),JSON.stringify({enabled:true,provider:'custom',base_url:'http://127.0.0.1:9999/v1',max_images:4}));
  await writeFile(path.join(dir,'brief.json'),JSON.stringify({version_id:'version_one',design_hash:'hash',grade:'三年级'}));
  await writeFile(path.join(dir,'visual-plan.json'),JSON.stringify({style:'纸艺',pages:prompts.map((image_prompt,index)=>({page_number:index+1,image_prompt}))}));
  const ctx={credentials:{resolve:async()=>({value:'private-test-key'}),describe:async()=>({configured:true})}};
  const service=createImageService({ctx,root});const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  return {root,dir,service};
}
test('mixed image API cache reuses one generated image across pages and resumes without more requests',async t=>{
  const {dir,service}=await setup(t,['情景图','情景图']);let creates=0,downloads=0;
  globalThis.fetch=async(url,options)=>{if(options.method==='POST'){creates++;const body=JSON.parse(options.body);assert.equal(body.model,'Qwen/Qwen-Image');assert.equal(body.batch_size,undefined);assert.ok(!options.headers['X-Enable-Watermark']);return Response.json({images:[{url:'http://127.0.0.1:9999/image.png?private-signature=hidden'}]});}downloads++;assert.equal(options.headers,undefined);return new Response(PNG);};
  assert.equal((await service.generate('task_images')).generated,2);
  await service.generate('task_images');assert.equal(creates,1);assert.equal(downloads,1);
  const index=JSON.parse(await readFile(path.join(dir,'generated','index.json'),'utf8'));assert.equal(index.images.length,2);assert.equal(index.images[0].file,index.images[1].file);
  const ledger=await readFile(path.join(dir,'generated','ledger.json'),'utf8');assert.ok(!ledger.includes('private-test-key'));assert.ok(!ledger.includes('private-signature'));
});
test('failed image download resumes original result instead of generating again',async t=>{
  const {service}=await setup(t);let creates=0,downloads=0;
  globalThis.fetch=async(_url,options)=>{if(options.method==='POST'){creates++;return Response.json({images:[{url:'http://127.0.0.1:9999/image.png'}]});}downloads++;return downloads===1?new Response('',{status:503}):new Response(PNG);};
  await assert.rejects(service.generate('task_images'),/下载失败/);await service.generate('task_images');assert.equal(creates,1);assert.equal(downloads,2);
});
test('uncertain paid image request is never submitted again on continue',async t=>{
  const {service}=await setup(t);let creates=0;globalThis.fetch=async()=>{creates++;throw new Error('timeout');};
  await assert.rejects(service.generate('task_images'),/明确回执/);await assert.rejects(service.generate('task_images'),/可能已计费/);assert.equal(creates,1);
});
test('image budget is checked before requesting any image',async t=>{
  const {service}=await setup(t,['a','b','c','d','e']);globalThis.fetch=()=>{throw new Error('must not call');};
  await assert.rejects(service.generate('task_images'),/超过每课生图上限/);
});
test('SiliconFlow credentials stay on its official endpoint and settings never expose a key',async t=>{
  assert.equal(normalizeImageConfig({provider:'siliconflow',base_url:'https://other.example/v1',api_key:'secret'}).base_url,'https://api.siliconflow.cn/v1');
  assert.equal(normalizeImageConfig({api_key:'secret'}).api_key,undefined);
  const {service}=await setup(t);assert.equal((await service.status()).api_key,undefined);
});

test('the exact effective image prompt stored for MD inspection is sent to the provider',async t=>{
  const {dir,service}=await setup(t,['白底学生交流插画']);
  const bytes=await readFile(path.join(dir,'visual-plan.json')),effective='教学插画，统一纸艺风格。白底学生交流插画。不要文字、公式或答案。';
  await writeFile(path.join(dir,'image-requests.json'),JSON.stringify({version_id:'version_one',design_hash:'hash',plan_sha256:createHash('sha256').update(bytes).digest('hex'),requests:[{page_number:1,raw_prompt:'白底学生交流插画',effective_prompt:effective}]}));
  let submitted;globalThis.fetch=async(_url,options)=>{if(options.method==='POST'){submitted=JSON.parse(options.body).prompt;return Response.json({images:[{url:'http://127.0.0.1:9999/image.png'}]});}return new Response(PNG);};
  await service.generate('task_images');assert.equal(submitted,effective);
});

test('an image request manifest for another saved plan is rejected before any paid request',async t=>{
  const {dir,service}=await setup(t);await writeFile(path.join(dir,'image-requests.json'),JSON.stringify({version_id:'version_other',design_hash:'hash',plan_sha256:'wrong',requests:[]}));
  let calls=0;globalThis.fetch=async()=>{calls++;throw new Error('must not send');};
  await assert.rejects(service.generate('task_images'),/请求清单.*不一致/);assert.equal(calls,0);
});
