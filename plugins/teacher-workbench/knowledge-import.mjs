import {mkdir,open,rm} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export function createKnowledgeImport({root,call}){
  async function limits(){return call('knowledge_import_limits');}
  async function upload(req,metadata){
    const filename=path.basename(String(metadata.name||'')),suffix=path.extname(filename).toLowerCase();
    if(!filename||/[<>:"/\\|?*]/.test(filename)||!['.pdf','.docx','.txt','.md'].includes(suffix))throw new Error('请选择文件名有效的PDF、DOCX、TXT或MD教材');
    if(!/^[A-Za-z][A-Za-z0-9_-]{0,100}$/.test(metadata.key||''))throw new Error('教材导入请求标识不正确');
    const settings=await limits(),maxMb=suffix==='.pdf'?settings.max_pdf_mb:settings.max_other_mb,maxBytes=maxMb*1024*1024;
    const oversized=()=>Object.assign(new Error(`该类型教材上限为${maxMb}MB`),{status:413});
    if(Number(req.headers['content-length']||0)>maxBytes)throw oversized();
    const directory=path.resolve(root,'inputs','upload_'+randomUUID()),input=path.join(directory,filename);
    const inputsRoot=path.resolve(root,'inputs')+path.sep;
    if(!directory.startsWith(inputsRoot))throw new Error('教材临时目录超出工作区');
    await mkdir(directory,{recursive:true});let file,total=0;
    try{
      file=await open(input,'wx');
      for await(const part of req){
        total+=part.length;if(total>maxBytes)throw oversized();
        let offset=0;
        while(offset<part.length){const {bytesWritten}=await file.write(part,offset,part.length-offset);if(!bytesWritten)throw new Error('教材文件写入未完成');offset+=bytesWritten;}
      }
      await file.close();file=null;
      if(!total)throw new Error('教材文件不能为空');
      return await call('knowledge_create',{path:input,meta:{title:metadata.title,subject:metadata.subject,grade:metadata.grade},key:metadata.key});
    }finally{
      await file?.close().catch(()=>{});
      // This UUID-named directory is owned only by this upload; imported data
      // has its own durable original copy before cleanup.
      if(directory.startsWith(inputsRoot)&&/^upload_[a-f0-9-]+$/.test(path.basename(directory)))await rm(directory,{recursive:true,force:true}).catch(()=>{});
    }
  }
  return {limits,upload};
}
