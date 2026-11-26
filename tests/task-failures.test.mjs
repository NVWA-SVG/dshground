import test from 'node:test';
import assert from 'node:assert/strict';
import {providerFailure,modelEndError,publicFailureFields,jobError,lastAttemptFailure} from '../plugins/teacher-workbench/task-failures.mjs';
import {createMaterialDelivery} from '../plugins/teacher-workbench/material-delivery.mjs';

test('the actual SiliconFlow 402 end reason is shown as insufficient balance without leaking provider internals',()=>{
  const reason={kind:'error',error:{code:'PI_AI_ERROR',message:'402: {"code":30001,"message":"Sorry, your account balance is insufficient","data":null,"debug":"sk-private-test"}'}};
  const error=modelEndError(reason,{provider:'siliconflow'});assert.equal(error.failure.code,'insufficient_balance');assert.equal(error.failure.http_status,402);
  assert.match(error.message,/SiliconFlow.*余额/);assert.ok(!error.message.includes('sk-private-test'));assert.equal(error.failure.retryable,false);
});
for(const [status,code] of [[401,'authentication'],[403,'permission'],[404,'model_unavailable'],[429,'rate_limited'],[400,'request_configuration'],[413,'input_limit'],[503,'service_unavailable'],[504,'timeout']])test(`HTTP ${status} has a distinct actionable failure classification`,()=>{
  const failure=providerFailure(new Error('模型服务返回 HTTP '+status));assert.equal(failure.code,code);assert.equal(failure.http_status,status);
});
test('output exhaustion and missing submission are not reported as connection failures',()=>{
  assert.equal(modelEndError({kind:'max-tokens'}).failure.code,'output_limit');
  const missing=modelEndError({kind:'done'});assert.equal(missing.failure.code,'submission_missing');assert.ok(!missing.message.includes('检查模型连接'));
  assert.equal(providerFailure(new Error('页面最多400个字，等待500毫秒')),null);
});
test('historical failed jobs are translated for the UI without modifying audit records',()=>{
  const job={status:'failed',provider:'siliconflow',error:'模型未提交有效教学设计',diagnostic:{reason:{kind:'error',error:{message:'402: account balance is insufficient'}}}};
  const before=structuredClone(job),fields=publicFailureFields(job);assert.equal(fields.failure_code,'insufficient_balance');assert.match(fields.error,/余额/);assert.deepEqual(job,before);
  assert.deepEqual(publicFailureFields({...job,status:'completed'}),{});
});
test('material delivery details use the same provider failure explanation',async()=>{
  const stopped={status:'failed',version_id:'version_one',error:'模型未提交',diagnostic:{reason:{error:{message:'HTTP 402'}}}};
  const delivery=createMaterialDelivery({call:async()=>({status:'ready'}),ppt:{status:async()=>({provider:'native'})},jobs:()=>[stopped]});
  const result=await delivery.describe('version_one');assert.equal(result.state,'incomplete');assert.match(result.detail,/余额/);assert.equal(result.detail,jobError(stopped));
});

test('five recorded empty responses remain visible after the overall task is aborted',()=>{
  const failure={code:'EMPTY_RESPONSE',message:'model returned a completed response with no content'};
  const events=[{type:'assistant/attempt',data:{stream:[{type:'chunk',chunk:{type:'finish',reason:{kind:'error',failure}}}]}},
    {type:'llm/retry',data:{retry:5,failure}},{type:'turn/end',data:{reason:{kind:'aborted'}}}];
  assert.equal(lastAttemptFailure(events),failure);
  const error=modelEndError({kind:'aborted'},{timedOut:true,provider:'siliconflow',requestFailure:lastAttemptFailure(events)});
  assert.equal(error.failure.code,'empty_response');assert.match(error.message,/空内容/);assert.ok(!error.message.includes('余额'));
});

test('an empty response is detected without an outer retry event',()=>{
  const failure={code:'EMPTY_RESPONSE',message:'empty'};
  assert.equal(lastAttemptFailure([{type:'assistant/attempt',data:{stream:[{chunk:{type:'finish',reason:{kind:'error',failure}}}]}}]),failure);
  assert.equal(lastAttemptFailure([]),null);
});

test('reviewed historical diagnosis corrects the UI and preserves the original timeout audit',()=>{
  const job={status:'failed',error:'超时',failure:{code:'timeout',message:'超时'},diagnosis:{failure:providerFailure({code:'EMPTY_RESPONSE'},'siliconflow')}};
  const before=structuredClone(job);assert.equal(publicFailureFields(job).failure_code,'empty_response');assert.deepEqual(job,before);
});
