import {createRequire} from 'node:module';
// Resolve the pinned runtime's dependency; plugins live outside runtime/node_modules.
const runtimeRequire=createRequire(new URL('../../runtime/package.json',import.meta.url));
const dispatchers=new Map();

export function normalizeProxy(value) {
  if(!value)return '';
  let url;try{url=new URL(String(value).trim());}catch{throw new Error('代理地址格式不正确，例如 http://127.0.0.1:7890');}
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw new Error('请填写不带账号密码的HTTP代理地址，例如 http://127.0.0.1:7890');
  return url.origin;
}

export function pptDispatcher(config={}) {
  const proxy=normalizeProxy(config.proxy_url);
  if(!proxy)return undefined;
  let dispatcher=dispatchers.get(proxy);
  if(!dispatcher){const {ProxyAgent}=runtimeRequire('undici');dispatcher=new ProxyAgent({uri:proxy,connectTimeout:15000});dispatchers.set(proxy,dispatcher);}
  return dispatcher;
}

export function pptFetch(url,options,config={}) {
  if(!config.proxy_url)return globalThis.fetch(url,options);
  // The installed Undici dispatcher and fetch must use the same version.
  // Node 24's built-in fetch uses an older dispatcher handler interface.
  return runtimeRequire('undici').fetch(url,{...options,dispatcher:pptDispatcher(config)});
}

export function networkFailure(error,{method='GET',proxy=false}={}) {
  const code=error?.cause?.code||error?.code||error?.cause?.errors?.[0]?.code;
  let message;
  if(['UND_ERR_CONNECT_TIMEOUT','ETIMEDOUT'].includes(code)||error?.name==='TimeoutError')message='连接超时，尚未完成接口验证；请检查网络'+(proxy?'或代理节点。':'，如使用代理请在课件生成设置中填写代理地址。');
  else if(['ENOTFOUND','EAI_AGAIN'].includes(code))message='域名解析失败；请检查网络或使用可用的HTTP代理。';
  else if(code==='ECONNREFUSED')message=proxy?'本机代理端口无法连接，请确认代理软件正在运行且端口正确。':'接口连接被拒绝，请检查服务地址和网络。';
  else if(['ECONNRESET','UND_ERR_SOCKET'].includes(code))message='网络连接被重置，请检查代理节点或网络。';
  else if(['UNABLE_TO_VERIFY_LEAF_SIGNATURE','SELF_SIGNED_CERT_IN_CHAIN','CERT_HAS_EXPIRED','DEPTH_ZERO_SELF_SIGNED_CERT'].includes(code))message='HTTPS证书校验失败，请检查系统时间及代理证书。';
  else message='网络请求未成功，请检查接口地址、网络和代理设置。';
  return method==='POST'?'创建请求未获得明确回执；可能已消耗供应商额度，请先在其后台核对，不要反复新建。'+message:message;
}
