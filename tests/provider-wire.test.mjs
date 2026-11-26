import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../runtime/package.json',import.meta.url));
const {streamSimple}=await import(new URL('../runtime/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',import.meta.url));
const {normalizeContext}=await import(new URL('../runtime/node_modules/@earendil-works/pi-ai/dist/utils/transcript.js',import.meta.url));
const {resolveRetryPolicy}=await import(new URL('../runtime/node_modules/@deepseek-ai/dsh-llm/lib/index.js',import.meta.url));
const yaml=require('yaml');
const patch=yaml.parse(await readFile(new URL('../config/windows-teacher.patch.yml',import.meta.url),'utf8'));
const provider=patch.find(row=>row.insert)?.insert.find(row=>row.id==='llm-pi-ai').config.providers.siliconflow;

test('teacher provider disables hidden upstream retries and bounds stream inactivity',()=>{
  assert.equal(resolveRetryPolicy(provider.retryPolicy,'siliconflow').maxRetries,0);
  assert.ok(provider.streamIdleTimeoutMs<=120000);
});

test('actual pi-ai wire uses supported SiliconFlow fields and preserves streamed tool calls',async()=>{
  let payload;
  const declaration={name:'teacher_read_revision_targets',description:'读取指定页面',parameters:{type:'object',properties:{targets:{type:'array',items:{type:'string'}}},required:['targets'],additionalProperties:false}};
  const events=[{choices:[{index:0,delta:{role:'assistant',content:''},finish_reason:null}]},
    {choices:[{index:0,delta:{content:null,tool_calls:[{index:0,id:'call_probe',type:'function',function:{name:declaration.name,arguments:''}}]},finish_reason:null}]},
    {choices:[{index:0,delta:{content:null,tool_calls:[{index:0,id:null,type:null,function:{name:'',arguments:'{"targets":["slide:S01"]}'}}]},finish_reason:null}]},
    {choices:[{index:0,delta:{content:null},finish_reason:'tool_calls'}],usage:{prompt_tokens:190,completion_tokens:25,total_tokens:215}}];
  const model={id:provider.models[0].id,name:'synthetic',provider:'siliconflow',api:'openai-completions',baseUrl:provider.baseURL,reasoning:false,input:['text'],contextWindow:262144,maxTokens:32768,
    cost:{input:0,output:0,cacheRead:0,cacheWrite:0},compat:provider.models[0].compat};
  const context=normalizeContext({systemPrompt:'调用指定工具。',messages:[{role:'user',content:'读取测试页。',timestamp:0}],tools:[declaration]});
  const output=streamSimple(model,context,{apiKey:'synthetic-offline-key',maxTokens:128,maxRetries:0,fetch:async(_url,options)=>{
    payload=JSON.parse(options.body);
    return new Response(events.map(event=>'data: '+JSON.stringify(event)+'\n\n').join('')+'data: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
  }});
  const result=await output.result();
  assert.equal(payload.max_tokens,128);assert.equal(payload.max_completion_tokens,undefined);assert.equal(payload.store,undefined);
  assert.equal(payload.tools[0].function.name,declaration.name);
  assert.equal(result.stopReason,'toolUse');const call=result.content.find(c=>c.type==='toolCall');
  assert.equal(call.name,declaration.name);assert.deepEqual(call.arguments,{targets:['slide:S01']});
});
