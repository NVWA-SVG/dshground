// Keep provider internals and credentials out of teacher-facing task errors.
function errorText(value,depth=0){
  if(depth>4||value==null)return '';
  if(typeof value==='string')return value.slice(0,6000);
  return ['message','error','cause','code'].map(key=>errorText(value[key],depth+1)).filter(Boolean).join(' ');
}
const failure=(code,message,http_status=null,retryable=false)=>({code,message,http_status,retryable});
function statusOf(error,depth=0){if(!error||depth>4||typeof error!=='object')return null;for(const key of ['status','http_status'])if(Number.isInteger(error[key])&&error[key]>=400&&error[key]<=599)return error[key];return statusOf(error.error,depth+1)||statusOf(error.cause,depth+1)||statusOf(error.failure,depth+1);}
export function providerFailure(error,provider=''){
  const text=errorText(error),service=provider==='siliconflow'?'SiliconFlow':'模型或生成服务';
  const match=text.match(/\bHTTP\s*(400|401|402|403|404|408|413|422|429|500|502|503|504)\b|^\s*(400|401|402|403|404|408|413|422|429|500|502|503|504)\s*[:：]/i);
  const status=statusOf(error)||(match?Number(match[1]||match[2]):/\bRATE_LIMIT\b/.test(text)?429:null);
  if(status===402||/account balance is insufficient|insufficient (?:balance|credits)|余额不足|可用额度不足/i.test(text))return failure('insufficient_balance',`${service}账户余额或可用额度不足（HTTP 402），本次请求未完成。请补足额度或更换有可用额度的 API Key 后重试。`,402);
  if(status===401)return failure('authentication',`${service}未通过密钥验证（HTTP 401）。请在模型设置中检查或更换 API Key。`,401);
  if(status===403)return failure('permission',`${service}拒绝了当前账号或模型的访问权限（HTTP 403）。请核对模型使用权限。`,403);
  if(status===404)return failure('model_unavailable',`${service}找不到所请求的模型或接口（HTTP 404）。请核对模型名称与接口设置。`,404);
  if(status===429){const tpm=/TPM|tokens? per minute/i.test(text);return {...failure('rate_limited',`${service}${tpm?'触发每分钟 token 用量限制（TPM，HTTP 429）':'请求受到限流（HTTP 429）'}。请等待冷却后再试；可减少本课资料或调低输出上限。当前任务不会自动反复提交。`,429,true),...(tpm?{limit_type:'TPM'}:{})};}
  if(status===400||status===422)return failure('request_configuration',`${service}拒绝了本次请求参数（HTTP ${status}）。请核对模型名称、输出上限与接口兼容性。`,status);
  if(status===413)return failure('input_limit',`${service}认为本次输入过大（HTTP 413）。请缩小本课教材或资料范围后重试。`,413);
  if(/EMPTY_RESPONSE|completed response with no content/i.test(text))return failure('empty_response',`${service}返回了空内容（EMPTY_RESPONSE），未提交可保存的材料。请稍后手动重试，或核对所选模型的工具调用兼容性。`,null,true);
  if(status===408||status===504||/timed?\s*out|ETIMEDOUT|TimeoutError|连接超时/i.test(text))return failure('timeout',`${service}请求超时，尚未保存完整材料。请检查网络并在确认后手动重试。`,status,true);
  if([500,502,503].includes(status))return failure('service_unavailable',`${service}暂时无法完成请求（HTTP ${status}）。请稍后手动重试。`,status,true);
  if(/ECONNREFUSED|ENOTFOUND|EAI_AGAIN|fetch failed|连接失败|网络中断/i.test(text))return failure('connection',`${service}连接失败。请检查网络、代理与接口地址后重试。`,null,true);
  return null;
}
// A later cancellation must not hide concrete failures already recorded by the adapter.
export function lastAttemptFailure(events=[]){
  for(const event of [...events].reverse()){
    if(event.type==='llm/retry'&&event.data?.failure)return event.data.failure;
    if(event.type==='assistant/attempt'){
      for(const row of [...(Array.isArray(event.data?.stream)?event.data.stream:[])].reverse()){
        const reason=row.chunk?.type==='finish'?row.chunk.reason:null;
        if(reason?.kind==='error'&&(reason.failure||reason.error))return reason.failure||reason.error;
      }
    }
  }
  return null;
}
export function modelEndError(reason,{timedOut=false,provider='',stage='教学设计',requestFailure=null}={}){
  let detail=providerFailure(reason?.error,provider)||providerFailure(requestFailure,provider);
  if(!detail&&timedOut)detail=failure('timeout',`${stage}生成超过等待时间，尚未保存完整结果。请检查服务状态后手动重试。`,null,true);
  if(!detail&&reason?.kind==='max-tokens')detail=failure('output_limit',`本次${stage}生成达到输出上限，尚未保存完整结果。请减少本课范围或资料，或调整输出上限后重试。`);
  if(!detail&&reason?.kind==='error')detail=failure('model_request_failed',`${stage}的模型请求失败，尚未提交完整结果。请核对服务状态；原始原因保留在本机任务记录中。`);
  if(!detail)detail=failure('submission_missing',`模型已结束本次回复，但没有提交可保存的${stage}。请重试生成，或缩小本课范围后再试。`,null,true);
  const error=new Error(detail.message);error.failure=detail;return error;
}
export const failureFromError=error=>error?.failure||providerFailure(error);
export function storedFailure(job){
  if(!job||job.status!=='failed')return null;
  if(job.diagnosis?.failure?.code&&job.diagnosis.failure.message)return job.diagnosis.failure;
  if(job.failure?.code&&job.failure?.message)return job.failure;
  return providerFailure(job.diagnostic?.reason?.error,job.provider)||providerFailure(job.request_failure,job.provider)||providerFailure(job.error);
}
export const jobError=job=>storedFailure(job)?.message||job?.error||null;
export function publicFailureFields(job){const f=storedFailure(job);return f?{error:f.message,failure_code:f.code,failure_http_status:f.http_status,failure_retryable:f.retryable}:{};}
