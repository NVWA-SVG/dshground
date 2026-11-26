import path from 'node:path';
import {jobError} from './task-failures.mjs';

export const TEACHER_FILES=new Set(['课件.pptx','教案.docx','逐字稿.docx','检测题与答案.docx','教学设计.json','信息密度.json','修改说明.md','内容预览.html','交付包.zip','交付清单.json']);

export function createMaterialDelivery({call,ppt,jobs}) {
  const pending=new Map();
  async function prepare(versionId,runId=null) {
    const key=versionId+':'+(runId||'native');
    if(!pending.has(key))pending.set(key,call('teaching-delivery',{version_id:versionId,run_id:runId}).finally(()=>pending.delete(key)));
    return pending.get(key);
  }
  async function describe(versionId) {
    const version=await call('get',{kind:'version',id:versionId});
    const related=jobs().filter(j=>j.version_id?j.version_id===versionId:j.target===versionId);
    const completed=related.filter(j=>j.status==='completed'&&(j.ppt_result?.valid||j.delivery?.valid)).at(-1);
    if(completed){
      const runId=completed.ppt_result?.run_id||completed.delivery?.run_id||null;
      const receipt=await prepare(versionId,runId);
      return {state:'ready',pages:receipt.pages,editable:true,receipt};
    }
    const active=related.findLast(j=>['queued','running'].includes(j.status));
    if(active)return {state:'generating',phase:active.phase||'design',stage:active.stage,job_id:active.id};
    const c=await ppt.status();
    if(c.provider==='native'&&version.status==='ready'&&!related.length){
      const receipt=await prepare(versionId);return {state:'ready',pages:receipt.pages,editable:true,receipt};
    }
    const canRestart=related.some(j=>['failed','interrupted','cancelled'].includes(j.status)&&(j.prompt_ready||j.ppt_remote_id||j.ppt_candidate_ready));
    if(c.provider!=='native'&&!c.configured)return {state:'needs_setup',message:'请先在生成设置中完成课件服务配置，或选择本地生成。',can_restart:canRestart};
    const stopped=related.findLast(j=>['failed','interrupted','cancelled'].includes(j.status));
    const fallback='不想调用外部课件服务时，可在「课件外观与服务」中选择本地生成。';
    return {state:stopped?'incomplete':'not_started',can_restart:canRestart,
      message:(stopped?'上次生成尚未完成。继续会优先复用已有课件任务；如果课件服务已经失败，可以选择重新制作课件。':'课程内容已保存。继续会完成本版最终课件与配套材料；使用外部课件服务时会产生一次课件生成任务。')+fallback,
      detail:jobError(stopped)};
  }
  async function file(versionId,name) {
    if(!TEACHER_FILES.has(name))throw new Error('无可下载文件');
    const result=await describe(versionId);
    if(result.state!=='ready'){const error=new Error('整套材料尚未完成，请等待或继续生成。');error.code='materials_not_ready';throw error;}
    return path.join(result.receipt.directory,name);
  }
  return {prepare,describe,file};
}
