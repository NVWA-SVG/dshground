import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {normalizeProxy,networkFailure,pptDispatcher} from '../plugins/teacher-workbench/ppt-network.mjs';
import {DEFAULT_PPT_CONFIG,presentationProvider,checkPresentationConnection,downloadPresentation} from '../plugins/teacher-workbench/ppt-providers.mjs';
const runtimeRequire=createRequire(new URL('../runtime/package.json',import.meta.url));

test('HTTP proxy normalization accepts local port and rejects embedded credentials',()=>{
 assert.equal(normalizeProxy('http://127.0.0.1:33210/'),'http://127.0.0.1:33210');assert.equal(normalizeProxy(''),'');
 for(const url of ['socks5://localhost:1080','http://user:secret@localhost:7890','http://localhost:7890?key=secret','http://localhost:7890/path'])assert.throws(()=>normalizeProxy(url));
});

test('network diagnostics distinguish DNS, timeout and refused proxy without echoing secrets',()=>{
 const timeout=Object.assign(new Error('sk-test-sensitive'),{cause:{code:'UND_ERR_CONNECT_TIMEOUT'}});
 assert.match(networkFailure(timeout),/连接超时/);assert.ok(!networkFailure(timeout).includes('sk-test-sensitive'));
 assert.match(networkFailure({cause:{code:'ECONNREFUSED'}},{proxy:true}),/代理端口/);
 assert.match(networkFailure({cause:{code:'ENOTFOUND'}}),/域名解析失败/);
 assert.match(networkFailure(timeout,{method:'POST'}),/未获得明确回执/);
});

test('configured proxy is used for connection test, create, poll and download with remote DNS',async t=>{
 const undici=runtimeRequire('undici'),original=undici.fetch,calls=[];t.after(()=>{undici.fetch=original;});
 undici.fetch=async(url,options)=>{calls.push({url:String(url),options});if(String(url).includes('file.invalid'))return new Response(Buffer.from('PK mock-pptx'));return new Response(JSON.stringify(options.method==='POST'?{generationId:'gen_network'}:{status:'completed',exportUrl:'https://file.invalid/deck.pptx',data:[]}),{headers:{'Content-Type':'application/json'}});};
 const config={...DEFAULT_PPT_CONFIG,provider:'gamma',proxy_url:'http://127.0.0.1:33210'};
 assert.equal((await checkPresentationConnection(config,'fake-key')).via_proxy,true);
 const provider=presentationProvider(config),data={brief:{grade:'三年级',pages:[{title:'原文',points:['要点']}]},plan:{style:'教学图文',pages:[{page_number:1,layout:'图文',visual_prompt:'示意'}]},markdown:'提示词'};
 await provider.create(data,'fake-key');await provider.poll('gen_network','fake-key');const bytes=await downloadPresentation('https://file.invalid/deck.pptx',config);
 assert.equal(bytes.subarray(0,2).toString(),'PK');assert.equal(calls.length,4);
 assert.ok(calls.every(call=>call.options.dispatcher===pptDispatcher(config)));assert.equal(calls[3].options.headers,undefined);
 await assert.rejects(downloadPresentation('https://127.0.0.1/private',config),/内网下载/);
});

test('invalid non-ASCII credential is rejected without echoing its value or making requests',async t=>{
 const original=globalThis.fetch;let called=false;t.after(()=>{globalThis.fetch=original;});globalThis.fetch=async()=>{called=true;};
 await assert.rejects(checkPresentationConnection({...DEFAULT_PPT_CONFIG,provider:'gamma'},'fake-key，'),error=>error.message.includes('中文符号')&&!error.message.includes('fake-key'));
 assert.equal(called,false);
});

for(const [status,expected] of [[401,/API Key无效/],[403,/账号权限/],[429,/过于频繁/]])test(`HTTP ${status} is not mislabeled as network disconnection`,async t=>{
 const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});globalThis.fetch=async()=>new Response('private error body',{status});
 await assert.rejects(checkPresentationConnection({...DEFAULT_PPT_CONFIG,provider:'gamma'},'fake-key'),expected);
});
