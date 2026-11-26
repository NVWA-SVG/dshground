import {mkdir,writeFile,rename,rm} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export function createDesignDrafts(root){
  const base=path.resolve(root,'data','design-drafts');
  function directory(id){
    if(!/^[A-Za-z][A-Za-z0-9_-]{0,100}$/.test(id))throw new Error('设计草稿标识不正确');
    const target=path.resolve(base,id);
    if(!target.startsWith(base+path.sep))throw new Error('设计草稿目录越界');
    return target;
  }
  async function save(job,field,payload,attempt){
    if(!['design','revision'].includes(field)||!Number.isInteger(attempt)||attempt<1||attempt>8)throw new Error('设计草稿阶段不正确');
    const folder=directory(job.id);await mkdir(folder,{recursive:true});
    const destination=path.join(folder,`attempt-${attempt}.json`),temporary=destination+'.'+randomUUID()+'.tmp';
    await writeFile(temporary,JSON.stringify({job_id:job.id,target:job.target,kind:job.kind,attempt,created_at:new Date().toISOString(),[field]:payload},null,2));await rename(temporary,destination);
  }
  async function remove(id){await rm(directory(id),{recursive:true,force:true});}
  return {save,remove};
}
