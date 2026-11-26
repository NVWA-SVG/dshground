import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {lookup} from 'node:dns/promises';
import {isIP} from 'node:net';
import {validBaseUrl,readBounded} from './ppt-providers.mjs';
import {normalizeProxy,pptFetch} from './ppt-network.mjs';
import {apiJson,listApiModels,validateApiKey} from './api-client.mjs';

export const DEFAULT_IMAGE_CONFIG=Object.freeze({enabled:false,provider:'siliconflow',base_url:'https://api.siliconflow.cn/v1',
  model:'Qwen/Qwen-Image',image_size:'1472x1140',num_inference_steps:50,max_images:4,proxy_url:'',api_format:'siliconflow'});
const hash=value=>createHash('sha256').update(value).digest('hex');
const optional=async file=>{try{return JSON.parse(await readFile(file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;return null;}};
const atomic=async(file,value)=>{const tmp=file+'.'+randomUUID()+'.tmp';await writeFile(tmp,JSON.stringify(value,null,2));await rename(tmp,file);};
export function normalizeImageConfig(raw){
  const c={...DEFAULT_IMAGE_CONFIG,...raw};
  if(!['siliconflow','custom'].includes(c.provider))throw new Error('生图服务类型不正确');
  c.api_format=c.provider==='siliconflow'?'siliconflow':c.api_format;
  if(!['siliconflow','openai'].includes(c.api_format))throw new Error('生图请求格式不正确');
  c.enabled=c.enabled===true;c.base_url=c.provider==='siliconflow'?DEFAULT_IMAGE_CONFIG.base_url:validBaseUrl(c.base_url);
  c.model=String(c.model||'').trim();
  if(!/^[A-Za-z0-9._:/-]{1,120}$/.test(c.model))throw new Error('请填写生图模型名称');
  if(!/^\d{3,4}x\d{3,4}$/.test(c.image_size)||c.image_size.split('x').some(n=>+n<256||+n>4096))throw new Error('生图尺寸需为256至4096像素，例如1472x1140');
  c.max_images=Number(c.max_images);c.num_inference_steps=Number(c.num_inference_steps);
  if(!Number.isInteger(c.max_images)||c.max_images<1||c.max_images>12)throw new Error('每课生图上限为1至12张');
  if(!Number.isInteger(c.num_inference_steps)||c.num_inference_steps<1||c.num_inference_steps>100)throw new Error('生图步数需为1至100');
  c.proxy_url=normalizeProxy(c.proxy_url);
  return Object.fromEntries(Object.keys(DEFAULT_IMAGE_CONFIG).map(key=>[key,c[key]]));
}
function privateAddress(ip){if(ip.includes(':'))return ip==='::1'||ip==='::'||/^(fc|fd|fe80|::ffff:)/i.test(ip);const p=ip.split('.').map(Number);return [0,10,127].includes(p[0])||p[0]>=224||p[0]===169&&p[1]===254||p[0]===172&&p[1]>=16&&p[1]<=31||p[0]===192&&p[1]===168;}
async function imageDownload(url,c,signal){
  let current=new URL(url);
  for(let hop=0;hop<5;hop++){
    const local=c.provider==='custom'&&current.origin===new URL(c.base_url).origin;
    if(current.username||current.password||(current.protocol!=='https:'&&!local))throw new Error('生图下载地址不符合要求');
    if(!local){const host=current.hostname.replace(/^\[|\]$/g,'');if(host==='localhost'||host.endsWith('.localhost')||isIP(host)&&privateAddress(host))throw new Error('拒绝生图服务返回的内网地址');if(!c.proxy_url&&(await lookup(host,{all:true})).some(a=>privateAddress(a.address)))throw new Error('拒绝生图服务返回的内网地址');}
    const response=await pptFetch(current,{signal,redirect:'manual'},c);
    if([301,302,303,307,308].includes(response.status)){const next=response.headers.get('location');await response.body?.cancel();if(!next)throw new Error('生图下载跳转缺少地址');current=new URL(next,current);continue;}
    if(!response.ok)throw new Error('插画下载失败 HTTP '+response.status+'，继续生成会优先下载已有图片');
    return readBounded(response,20*1024*1024,signal);
  }
  throw new Error('生图下载跳转过多');
}
function extension(bytes){if(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return '.png';if(bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return '.jpg';if(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP')return '.webp';throw new Error('生图服务返回的内容不是PNG、JPEG或WebP图片');}
export function imageRequestBody(config,prompt){return config.api_format==='openai'?{model:config.model,prompt,size:config.image_size,n:1}:{model:config.model,prompt,image_size:config.image_size,num_inference_steps:config.num_inference_steps};}

export function createImageService({ctx,root}){
  const file=path.join(root,'config','image-generation.json');
  const config=async()=>normalizeImageConfig(await optional(file)||{});
  const credentialName=c=>c.provider==='siliconflow'?'SILICONFLOW_API_KEY':'IMAGE_API_KEY';
  async function status(){const c=await config();const info=await ctx.credentials?.describe?.(credentialName(c));return {...c,configured:!!info?.configured};}
  async function saveSettings(data){const c=normalizeImageConfig({...await config(),...data});if(data.api_key){const key=validateApiKey(data.api_key);if(key.length<10)throw new Error('生图密钥格式不正确');await ctx.credentials.set(credentialName(c),key);}await atomic(file,c);return status();}
  async function testConnection(){
    const c=await config(),key=await ctx.credentials.resolve(credentialName(c));if(!key?.value)throw new Error('请先保存生图服务密钥');
    const result=await listApiModels({root,url:c.base_url+'/models',key:key.value,config:c,timeoutMs:30000});
    if(!result.models.includes(c.model))throw new Error('当前账号模型列表中没有所选生图模型，请核对名称');
    return {ok:true,model:c.model,paid_generation:false,request_id:result.request_id};
  }
  async function generate(runId,{signal,onStage=async()=>{}}={}){
    if(!/^task_[\w-]{1,90}$/.test(runId))throw new Error('生图任务编号不正确');
    const dir=path.join(root,'data','ppt-runs',runId),planBytes=await readFile(path.join(dir,'visual-plan.json'));
    const plan=JSON.parse(planBytes),brief=JSON.parse(await readFile(path.join(dir,'brief.json'),'utf8'));
    const pages=plan.pages.filter(p=>p.image_prompt?.trim());if(!pages.length)return {generated:0};
    const settings=await config();if(!settings.enabled)throw new Error('本页方案需要AI插画，请先启用生图服务后继续');
    const directory=path.join(dir,'generated');await mkdir(directory,{recursive:true});const ledgerFile=path.join(directory,'ledger.json');
    let ledger=await optional(ledgerFile);
    if(ledger&&ledger.plan_sha256!==hash(planBytes))throw new Error('已保存的生图任务与视觉方案不同');
    if(!ledger){ledger={plan_sha256:hash(planBytes),config:settings,items:{}};await atomic(ledgerFile,ledger);}
    const c=ledger.config;let key;
    const manifest=await optional(path.join(dir,'image-requests.json'));
    if(manifest&&(manifest.version_id!==brief.version_id||manifest.design_hash!==brief.design_hash||manifest.plan_sha256!==hash(planBytes)
      ||!Array.isArray(manifest.requests)||manifest.requests.length!==pages.length||new Set(manifest.requests.map(r=>r.page_number)).size!==pages.length))throw new Error('生图请求清单与已保存的课件方案不一致');
    const requests=pages.map(p=>{
      if(manifest){const request=manifest.requests.find(r=>r.page_number===p.page_number);
        if(!request||request.raw_prompt!==p.image_prompt||typeof request.effective_prompt!=='string'||!request.effective_prompt.includes(p.image_prompt))throw new Error('生图请求缺少本页原始提示词');
        return {page:p.page_number,prompt:request.effective_prompt};}
      return {page:p.page_number,prompt:`教学插画，适合${brief.grade}。统一视觉风格：${plan.style}。${p.image_prompt}\n只生成辅助插画，不生成整页课件，不把标题、题干、公式、答案或标签画进图中；不添加本课之外的事实。`};
    });
    const unique=new Set(requests.map(r=>hash(r.prompt)));
    if(unique.size>settings.max_images)throw new Error(`视觉方案需要${unique.size}张插画，超过每课生图上限${settings.max_images}；请提高上限或重新制作课件`);
    const images=[];
    for(const request of requests){
      signal?.throwIfAborted();const id=hash(request.prompt);let item=ledger.items[id];
      await onStage(`正在制作教学插画 ${images.length+1}/${requests.length}`);
      if(item?.status==='ready'){
        if(hash(await readFile(path.join(directory,item.file)))!==item.sha256)throw new Error('已保存插画被修改，请核对原文件');
      }else{
        if(item&&['requesting','uncertain'].includes(item.status))throw new Error('上次生图请求没有明确回执，可能已计费。请先核对供应商记录；需要新请求时使用“重新制作课件”。');
        if(item?.status!=='download'){
          key ||= await ctx.credentials.resolve(credentialName(c));if(!key?.value)throw new Error('请先配置生图服务密钥');
          item={status:'requesting'};ledger.items[id]=item;await atomic(ledgerFile,ledger);
          try{
            const {data,request_id}=await apiJson({root,url:c.base_url+'/images/generations',key:key.value,method:'POST',
              body:imageRequestBody(c,request.prompt),
              signal,config:c,model:c.model,timeoutMs:240000,maxBytes:28*1024*1024});
            const result=data.images?.[0]||data.data?.[0];
            if(result?.b64_json)item.base64=result.b64_json;else if(result?.url)item.url=result.url;else throw new Error('missing image');
            if(request_id)item.request_id=request_id;
            item.status='download';await atomic(ledgerFile,ledger);
          }catch(error){
            item.status=error.status&&error.status<500?'rejected':'uncertain';await atomic(ledgerFile,ledger);
            if(error.status)throw error;
            throw new Error('生图请求未获得明确回执，已暂停并保留任务；继续不会自动重发，请先核对供应商记录。');
          }
        }
        const downloadSignal=signal?AbortSignal.any([signal,AbortSignal.timeout(90000)]):AbortSignal.timeout(90000);
        const bytes=item.base64?Buffer.from(item.base64,'base64'):await imageDownload(item.url,c,downloadSignal);
        if(bytes.length>20*1024*1024)throw new Error('插画文件超过20MB');
        const filename=id+extension(bytes),temporary=path.join(directory,filename+'.tmp');await writeFile(temporary,bytes);await rename(temporary,path.join(directory,filename));
        item={status:'ready',file:filename,sha256:hash(bytes),model:c.model};ledger.items[id]=item;await atomic(ledgerFile,ledger);
      }
      images.push({page_number:request.page,file:item.file,sha256:item.sha256,model:c.model});
    }
    await atomic(path.join(directory,'index.json'),{version_id:brief.version_id,design_hash:brief.design_hash,plan_sha256:hash(planBytes),images});
    return {generated:images.length};
  }
  return {config,status,saveSettings,testConnection,generate};
}
