'use strict';

async function openKnowledgeDialog(){
  const dialog=$('#knowledgeDialog');
  dialog.innerHTML='<p class="loading">正在读取知识库…</p>';
  if(!dialog.open)dialog.showModal();
  try{
    const [data,limits]=await Promise.all([api('knowledge/list'),api('knowledge/limits')]);
    renderKnowledgeDialog(dialog,data.entries||[],limits);
  }catch(error){
    dialog.innerHTML='<p class="danger">'+esc(error.message)+'</p><div class="actions"><button id="closeKnowledgeError">关闭</button></div>';
    $('#closeKnowledgeError').onclick=()=>dialog.close();
  }
}

function uploadKnowledgeFile(file,metadata,onProgress){
  return new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest();let timer,polling=false;
    const stop=()=>clearInterval(timer);
    request.open('POST','/teacher/api/knowledge/upload-file?'+new URLSearchParams({name:file.name,...metadata}));
    request.setRequestHeader('Content-Type','application/octet-stream');
    request.setRequestHeader('X-Teacher-Token',state.token);
    request.upload.onprogress=event=>onProgress(`正在上传：${(event.loaded/1024/1024).toFixed(1)} / ${(file.size/1024/1024).toFixed(1)} MB`);
    request.upload.onload=()=>{
      onProgress('上传完成，正在读取目录与章节…');
      timer=setInterval(async()=>{if(polling)return;polling=true;try{
        const progress=await api('knowledge/import-status?key='+encodeURIComponent(metadata.key)),c=progress.counts||{};
        const messages={waiting:'正在准备读取教材…',directory:'目录已读取，正在核对章节标题…',reading:`正在读取正文：${c.page||0}/${c.pages||'—'} 页`,chunking:`正在梳理 ${c.chapters||0} 个章节并建立内容块…`,assets:'内容块已建立，正在提取并关联教材素材…',ready:`整理完成：${c.chapters||0} 个章节，${c.chunks||0} 个内容块`};
        onProgress(messages[progress.phase]||'正在整理教材…');
      }catch{}finally{polling=false;}},1500);
    };
    request.onload=()=>{
      stop();
      try{
        const result=JSON.parse(request.responseText);
        if(request.status<200||request.status>=300)throw new Error(result.error||'教材导入失败');
        resolve(result);
      }catch(error){reject(error);}
    };
    request.onerror=()=>{stop();reject(new Error('连接中断。教材可能仍在处理中，请先刷新知识库检查结果；重试会复用本次请求。'));};
    request.onabort=()=>{stop();reject(new Error('上传已中断'));};
    request.send(file);
  });
}

function renderKnowledgeDialog(dialog,entries,limits){
  const roots=entries.filter(entry=>entry.storage_mode!=='scope');
  dialog.innerHTML=`<div class="dialog-head"><h2>知识库</h2><div><button type="button" id="refreshKnowledge" class="icon" aria-label="刷新知识库" title="刷新">↻</button><button type="button" id="closeKnowledge" class="icon" aria-label="关闭知识库">×</button></div></div>
  <p class="muted">上传教材后，先读取目录并梳理章节，再按概念、例题和练习题组整理内容。备课时展开教材选择章节，原件与图片共用。</p>
  <p class="muted">PDF 单个上限 ${limits.max_pdf_mb}MB / ${limits.max_pdf_pages} 页；DOCX、TXT、MD 单个上限 ${limits.max_other_mb}MB。目录识别和分块在本机完成，不调用模型；「AI 描述」会调用已配置的视觉模型。</p>
  <div class="vision-bar"><span id="visionStatusText">AI 图片描述：读取中</span><button type="button" id="openVisionSettings">视觉模型设置</button></div>
  <form id="knowledgeForm" class="knowledge-upload"><div class="grid"><label>名称<input name="title" maxlength="120" placeholder="例如：数学三年级上册（整册）"></label><label>学科<input name="subject" maxlength="30" placeholder="例如：数学"></label><label>年级<input name="grade" maxlength="30" placeholder="例如：三年级"></label><label>文件<input name="file" type="file" accept=".txt,.md,.docx,.pdf" required><small>文字型 PDF 支持整册上传，目录与内容块会自动整理。</small></label></div><p id="knowledgeUploadStatus" role="status" aria-live="polite"></p><div class="actions"><button type="submit" class="primary">上传并建立知识库</button></div></form>
  <section id="knowledgePdfPanel"></section>
  <section class="card knowledge-entries"><h3>已建立的知识库</h3>${roots.length?roots.map(entry=>{
    const canExtract=/\.(pdf|docx)$/i.test(entry.original_name||'');
    return `<div class="knowledge-entry"><div><strong>${esc(entry.title)}</strong><small>${esc(entry.subject||'通用学科')} · ${esc(entry.grade||'通用年级')} · ${entry.pdf_pages?`${entry.pdf_pages} 页 · `:''}${entry.characters} 字 · ${entry.chunk_count} 段${entry.asset_count?` · ${entry.asset_count} 张素材`:''}</small>${entry.source_book?`<small>来源：${esc(entry.source_book.title)} · PDF 第 ${entry.source_book.start_page}—${entry.source_book.end_page} 页</small>`:''}${entry.import_warnings?.length?`<small class="danger">${esc(entry.import_warnings.join('；'))}</small>`:''}${entry.used_by.lessons?`<small>已被 ${entry.used_by.lessons} 门课程使用，暂不能删除</small>`:''}</div><div class="knowledge-entry-actions">${/\.pdf$/i.test(entry.original_name||'')&&!entry.source_book?`<button type="button" data-knowledge-pdf="${esc(entry.id)}">查看目录与分块</button>`:''}<button type="button" data-knowledge-text="${esc(entry.id)}">查看全文</button>${entry.asset_count?`<button type="button" data-knowledge-assets="${esc(entry.id)}">查看素材</button><button type="button" data-knowledge-describe="${esc(entry.id)}">AI 描述</button>`:canExtract?`<button type="button" data-knowledge-refresh="${esc(entry.id)}">提取素材</button>`:''}<button type="button" data-knowledge-delete="${esc(entry.id)}" class="danger" ${entry.used_by.lessons||entry.used_by.versions||entries.some(child=>child.scope?.root_id===entry.id)?'disabled':''}>删除</button></div></div>`;
  }).join(''):'<p class="muted">还没有知识库。上传整册教材后即可展开章节选择备课范围。</p>'}</section><section id="knowledgeAssetsPanel"></section>`;
  $('#closeKnowledge').onclick=()=>dialog.close();$('#refreshKnowledge').onclick=()=>openKnowledgeDialog();
  dialog.querySelectorAll('[data-knowledge-pdf]').forEach(button=>bindAction(button,'click',()=>showKnowledgePdf(button.dataset.knowledgePdf)));
  dialog.querySelectorAll('[data-knowledge-assets]').forEach(button=>bindAction(button,'click',()=>showKnowledgeAssets(button.dataset.knowledgeAssets)));
  dialog.querySelectorAll('[data-knowledge-text]').forEach(button=>bindAction(button,'click',()=>showKnowledgeText(button.dataset.knowledgeText)));
  dialog.querySelectorAll('[data-knowledge-describe]').forEach(button=>bindAction(button,'click',async()=>{
    await openVisionQueue(button.dataset.knowledgeDescribe);
  }));
  $('#openVisionSettings').onclick=openVisionSettings;void updateVisionStatus();
  dialog.querySelectorAll('[data-knowledge-refresh]').forEach(button=>bindAction(button,'click',async()=>{
    await api('knowledge/refresh',{id:button.dataset.knowledgeRefresh});
    await openKnowledgeDialog();await showKnowledgeAssets(button.dataset.knowledgeRefresh);
  }));
  $('#knowledgeForm').elements.namedItem('file').onchange=()=>{delete $('#knowledgeForm').dataset.importKey;};
  bindAction($('#knowledgeForm'),'submit',async event=>{
    const form=event.target,file=form.elements.namedItem('file').files[0],status=$('#knowledgeUploadStatus');
    const progress=message=>{status.textContent=message;};
    try{
      if(!file)throw new Error('请选择文件。');
      const maxMb=/\.pdf$/i.test(file.name)?limits.max_pdf_mb:limits.max_other_mb;
      if(file.size>maxMb*1024*1024)throw new Error(`该类型教材不能超过 ${maxMb}MB。`);
      if(!form.dataset.importKey)form.dataset.importKey=uid();
      // Metadata is frozen across connection retries so idempotency remains valid.
      const signature=[file.name,file.size,file.lastModified,...['title','subject','grade'].map(name=>form.elements.namedItem(name).value)].join('|');
      if(form.dataset.signature!==signature){form.dataset.importKey=uid();form.dataset.signature=signature;}
      const result=await uploadKnowledgeFile(file,{...Object.fromEntries(['title','subject','grade'].map(name=>[name,form.elements.namedItem(name).value])),key:form.dataset.importKey},progress);
      if(typeof refreshCreateKnowledge==='function')await refreshCreateKnowledge();
      await openKnowledgeDialog();
      if(result.pdf_pages)await showKnowledgePdf(result.knowledge_id);
    }catch(error){progress(error.message);throw error;}
  });
  dialog.querySelectorAll('[data-knowledge-delete]').forEach(button=>bindAction(button,'click',async()=>{
    if(!confirm('删除这个知识库？已建立的分段和原文会一并删除，此操作不可撤销。'))return;
    await api('knowledge/delete',{id:button.dataset.knowledgeDelete});
    if(typeof refreshCreateKnowledge==='function')await refreshCreateKnowledge();
    await openKnowledgeDialog();
  }));
}

async function showKnowledgePdf(knowledgeId){
  const panel=$('#knowledgePdfPanel');if(!panel)return;
  panel.innerHTML='<p class="loading">正在读取章节与内容块…</p>';
  try{
    const [data,library]=await Promise.all([api('knowledge/index?knowledge='+encodeURIComponent(knowledgeId)),api('knowledge/list')]);
    const scopes=(library.entries||[]).filter(entry=>entry.scope?.root_id===knowledgeId);
    const chapters=data.chapters||[];
    panel.innerHTML=`<section class="card"><div class="dialog-head"><h3>${esc(data.title)} · 章节与内容块</h3><button type="button" id="closeKnowledgePdf" class="icon" aria-label="收起章节">×</button></div><p class="muted">已整理 ${chapters.length} 个章节 / 范围、${data.chunk_count} 个内容块。原件与素材统一保存，章节通过页码引用。</p><p class="muted">${data.directory_confirmed?'目录已与正文标题核对；下方内容梳理来自原文小节和摘录，请按授课需要检查。':'未识别到可靠目录，当前保留全文范围。可在下方指定章节页码。'} 页码是 PDF 文件从 1 开始的页序。</p><div class="chapter-overviews">${chapters.map((chapter,index)=>`<article><h4>${esc(chapter.title)}</h4><small>PDF 第 ${chapter.start_page}—${chapter.end_page} 页 · ${chapter.chunk_count} 个内容块${chapter.bookmark_page&&chapter.bookmark_page!==chapter.start_page?` · 原书签 ${chapter.bookmark_page} 页，已校正`:''}</small>${chapter.overview.topics.length?`<p>${esc(chapter.overview.topics.join(' / '))}</p>`:''}<p class="muted">${esc(chapter.overview.content_types.join('、'))}</p><details><summary>查看原文摘录</summary><p>${esc(chapter.overview.excerpt||'本章主要通过图示呈现，可查看素材。')}</p></details><button type="button" data-chapter-chunks="${index}">查看内容块</button><button type="button" data-chapter-assets="${index}">查看本章素材</button></article>`).join('')}</div><details class="manual-scope"><summary>按指定页码添加教学范围</summary><form id="bookUnitForm"><p class="muted">只增加范围引用，正文、图片与整册共用。</p><div class="grid"><label>范围名称<input name="title" required maxlength="120" placeholder="例如：毫米和分米的认识"></label><label>起始页<input name="start_page" type="number" min="1" max="${data.pdf_pages}" required></label><label>结束页<input name="end_page" type="number" min="1" max="${data.pdf_pages}" required></label></div><button type="submit" class="primary">添加范围</button></form></details><p id="bookUnitStatus" role="status"></p><details class="scope-management"><summary>管理教学范围引用（${scopes.length} 项）</summary>${scopes.map(scope=>`<div class="knowledge-picker-row"><span>${esc(scope.title)}<small>PDF 第 ${scope.scope.start_page}—${scope.scope.end_page} 页${scope.used_by.lessons||scope.used_by.versions?' · 已被课程使用':''}</small></span><button type="button" data-delete-scope="${esc(scope.id)}" ${scope.used_by.lessons||scope.used_by.versions?'disabled':''}>移除引用</button></div>`).join('')}</details><section id="knowledgeChunksPanel"></section></section>`;
    $('#closeKnowledgePdf').onclick=()=>{panel.innerHTML='';};
    panel.querySelectorAll('[data-delete-scope]').forEach(button=>bindAction(button,'click',async()=>{
      if(!confirm('移除这个教学范围引用？整册原件和共享素材会保留。'))return;
      await api('knowledge/delete',{id:button.dataset.deleteScope});await refreshCreateKnowledge();await openKnowledgeDialog();await showKnowledgePdf(knowledgeId);
    }));
    panel.querySelectorAll('[data-chapter-chunks]').forEach(button=>bindAction(button,'click',()=>showBookChunks(knowledgeId,chapters[Number(button.dataset.chapterChunks)].id)));
    panel.querySelectorAll('[data-chapter-assets]').forEach(button=>{
      const describe=document.createElement('button');describe.type='button';describe.textContent='AI 描述本章';describe.dataset.chapterDescribe=button.dataset.chapterAssets;
      button.after(describe);bindAction(describe,'click',()=>openVisionQueue(knowledgeId,chapters[Number(button.dataset.chapterAssets)].id));
    });
    panel.querySelectorAll('[data-chapter-assets]').forEach(button=>bindAction(button,'click',async()=>{
      const chapter=chapters[Number(button.dataset.chapterAssets)];
      const scope=await api('knowledge/unit',{id:knowledgeId,start_page:chapter.start_page,end_page:chapter.end_page,title:chapter.title,key:`chapter_${knowledgeId}_${chapter.id}`});
      await showKnowledgeAssets(scope.knowledge_id);
    }));
    bindAction($('#bookUnitForm'),'submit',async event=>{
      const values=Object.fromEntries(new FormData(event.target)),start=Number(values.start_page),end=Number(values.end_page);
      try{
        const titleHash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(values.title.trim())))].map(value=>value.toString(16).padStart(2,'0')).join('').slice(0,16);
        await api('knowledge/unit',{id:knowledgeId,start_page:start,end_page:end,title:values.title.trim(),key:`manual_${knowledgeId}_${start}_${end}_${titleHash}`});
        if(typeof refreshCreateKnowledge==='function')await refreshCreateKnowledge();
        $('#bookUnitStatus').textContent='已添加教学范围，未复制原文件或图片。';
      }catch(error){$('#bookUnitStatus').textContent=error.message;throw error;}
    });
    panel.scrollIntoView({block:'nearest',behavior:'smooth'});
  }catch(error){panel.innerHTML='<p class="danger">'+esc(error.message)+'</p>';}
}

async function showBookChunks(knowledgeId,chapterId,offset=0){
  const panel=$('#knowledgeChunksPanel');if(!panel)return;
  const result=await api('knowledge/chunks?'+new URLSearchParams({knowledge:knowledgeId,chapter:chapterId,offset,limit:20}));
  const labels={concept:'概念与说明',example:'例题与解答',practice:'练习题组',review:'整理复习',reading:'拓展阅读'};
  const cards=result.chunks.map(chunk=>`<article class="source-chunk"><strong>${esc(labels[chunk.kind]||chunk.kind)} · ${chunk.characters} 字</strong><small>${esc(chunk.section_title)} · PDF 第 ${chunk.page_refs.join('、')} 页${chunk.part_count>1?` · 连续内容 ${chunk.part_index}/${chunk.part_count}`:''} · ${chunk.asset_ids.length} 个配图引用</small>${chunk.scope_continuation_incomplete?'<p class="danger">当前页码范围未包含此连续内容的全部分片，使用前请核对上下文。</p>':''}<pre>${esc(chunk.text)}</pre></article>`).join('');
  if(!offset)panel.innerHTML=`<h4>原文内容块 · 共 ${result.total} 块</h4><p class="muted">每块保留章节、类型、页码和素材引用；例题、解答及练习题组优先完整保留。</p><div id="sourceChunkCards"></div><div id="moreChunks"></div>`;
  $('#sourceChunkCards').insertAdjacentHTML('beforeend',cards);
  const next=offset+result.chunks.length;
  $('#moreChunks').innerHTML=next<result.total?'<button type="button" id="loadMoreChunks">加载更多内容块</button>':'';
  if($('#loadMoreChunks'))bindAction($('#loadMoreChunks'),'click',()=>showBookChunks(knowledgeId,chapterId,next));
  panel.scrollIntoView({block:'nearest',behavior:'smooth'});
}

async function showKnowledgeAssets(knowledgeId){
  const panel=$('#knowledgeAssetsPanel');if(!panel)return;
  panel.innerHTML='<p class="muted">正在读取素材…</p>';
  try{
    const [listData,assetData]=await Promise.all([api('knowledge/list'),api('knowledge/assets?knowledge='+encodeURIComponent(knowledgeId))]);
    const entry=(listData.entries||[]).find(item=>item.id===knowledgeId),assets=assetData.assets||[];
    const kindLabel={scene:'情景导入',diagram:'图表/示意',question:'教材留空题'};
    panel.innerHTML=`<section class="card"><div class="dialog-head"><h3>${esc(entry?.title||'教材素材')} · ${assets.length} 张素材</h3><button type="button" id="closeKnowledgeAssets" class="icon" aria-label="收起素材">×</button></div><p class="muted">情景图保留画面和对话框，图表页保留钟面/时间线示意，教材留空题是原文区域截图。图注和描述都可以修改。</p><div class="asset-filter"><label>类型<select id="assetKindFilter"><option value="">全部</option><option value="scene">情景导入</option><option value="diagram">图表/示意</option><option value="question">教材留空题</option></select></label></div><div class="knowledge-gallery">${assets.length?assets.map(asset=>`<figure data-asset="${esc(asset.id)}" data-kind="${esc(asset.kind||'scene')}" title="${esc(asset.context||'')}"><img loading="lazy" src="/teacher/api/knowledge/asset?knowledge=${encodeURIComponent(knowledgeId)}&name=${encodeURIComponent(asset.file)}" alt="${esc(asset.caption||'教材素材')}"><figcaption>第 ${asset.page||'—'} 页${asset.source_page?`（原册 PDF 第 ${asset.source_page} 页）`:''} · ${esc(kindLabel[asset.kind]||'素材')} · ${esc(asset.caption||'未识别图注')}</figcaption>${asset.dialogue?`<p class="asset-dialogue">对话框/场景文字：${esc(asset.dialogue)}</p>`:''}${asset.description?`<p class="asset-description">${esc(asset.description)}</p>`:''}<details><summary>编辑图注 / 描述 / 对话</summary><label>图注<textarea data-caption>${esc(asset.caption||'')}</textarea></label><label>对话框/场景文字<textarea data-dialogue>${esc(asset.dialogue||'')}</textarea></label><label>图片描述<textarea data-description>${esc(asset.description||'')}</textarea></label><button type="button" data-asset-save="${esc(asset.id)}">保存</button></details></figure>`).join(''):'<p class="muted">这份知识库还没有拆出素材。</p>'}</div></section>`;
    $('#closeKnowledgeAssets').onclick=()=>{panel.innerHTML='';};
    $('#assetKindFilter').onchange=event=>{
      const kind=event.target.value;
      panel.querySelectorAll('.knowledge-gallery figure').forEach(figure=>{figure.hidden=!!kind&&figure.dataset.kind!==kind;});
    };
    panel.querySelectorAll('[data-asset-save]').forEach(button=>bindAction(button,'click',async()=>{
      const figure=button.closest('figure');
      await api('knowledge/asset/update',{knowledge:knowledgeId,asset:button.dataset.assetSave,
        caption:$('[data-caption]',figure).value,dialogue:$('[data-dialogue]',figure).value,
        description:$('[data-description]',figure).value});
      notice('素材图注已保存。');await showKnowledgeAssets(knowledgeId);
    }));
  }catch(error){panel.innerHTML='<p class="danger">'+esc(error.message)+'</p>';}
}

async function updateVisionStatus(){
  const element=$('#visionStatusText');if(!element)return;
  try{
    const status=await api('vision/settings');
    element.textContent=`AI 图片描述：${status.configured?'已配置':'未配置'} · ${status.model}${status.enabled?'':'（已停用）'}`;
  }catch(error){element.textContent='AI 图片描述：读取失败';}
}

async function openVisionSettings(){
  const dialog=$('#visionDialog');
  dialog.innerHTML='<p class="loading">正在读取视觉模型设置…</p>';
  if(!dialog.open)dialog.showModal();
  try{
    const status=await api('vision/settings');
    dialog.innerHTML=`<form id="visionForm"><div class="dialog-head"><h2>视觉模型设置</h2><button type="button" id="closeVision" class="icon" aria-label="关闭视觉设置">×</button></div><p class="muted">用于给教材插图生成图片描述。API Key 保存在本机 DSH 凭据库，页面不回显，也不会写入课程数据库或提示词。</p><label>密钥来源<select name="credential_source"><option value="shared" ${status.credential_source==='shared'?'selected':''}>沿用主模型密钥 · SiliconFlow</option><option value="separate" ${status.credential_source==='separate'?'selected':''}>独立视觉密钥</option></select></label><label id="visionKeyLabel">独立 API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="留空保留已有独立密钥"></label><label>模型<input name="model" value="${esc(status.model)}" required></label><label>Base URL<input name="base_url" value="${esc(status.base_url)}" required></label><label>HTTP 代理（选填）<input name="proxy_url" value="${esc(status.proxy_url||'')}" placeholder="http://127.0.0.1:7890"></label><label class="check"><input name="enabled" type="checkbox" ${status.enabled?'checked':''}>启用 AI 图片描述</label><p class="muted">当前：${status.configured?'密钥已配置':'密钥未配置'}。视觉模型只用于补充图片描述，不参与教学设计或学情判断。</p><div id="visionResult"></div><div class="actions"><button type="button" id="checkVisionApi">检查已保存的连接</button><button type="submit" class="primary">保存视觉设置</button></div></form>`;
    $('#closeVision').onclick=()=>dialog.close();
    const visionForm=$('#visionForm'),source=visionForm.elements.namedItem('credential_source');
    const keyVisibility=()=>{$('#visionKeyLabel').hidden=source.value==='shared';visionForm.elements.namedItem('api_key').disabled=source.value==='shared';};
    source.onchange=keyVisibility;keyVisibility();
    visionForm.elements.namedItem('base_url').onchange=()=>{try{if(new URL(visionForm.elements.namedItem('base_url').value).origin!=='https://api.siliconflow.cn'){source.value='separate';keyVisibility();}}catch{}};
    bindAction($('#checkVisionApi'),'click',async()=>{if(visionForm.elements.namedItem('api_key').value)throw new Error('请先保存视觉设置');const result=await api('vision/models');$('#visionResult').textContent=result.model_available?'接口认证通过，所选视觉模型可见；未发送图片。':'接口认证通过，但所选视觉模型不可见，请核对模型名称。';});

    bindAction($('#visionForm'),'submit',async event=>{
      const form=event.target,data=Object.fromEntries(new FormData(form));
      data.enabled=form.elements.namedItem('enabled').checked;
      await api('vision/settings',data);
      dialog.close();await updateVisionStatus();notice('视觉模型设置已保存。');
    });
  }catch(error){
    dialog.innerHTML='<p class="danger">'+esc(error.message)+'</p><div class="actions"><button id="closeVisionError">关闭</button></div>';
    $('#closeVisionError').onclick=()=>dialog.close();
  }
}

async function showKnowledgeText(knowledgeId){
  const panel=$('#knowledgeAssetsPanel');if(!panel)return;
  panel.innerHTML='<p class="muted">正在读取全文…</p>';
  try{
    const data=await api('knowledge/text?knowledge='+encodeURIComponent(knowledgeId));
    panel.innerHTML=`<section class="card"><div class="dialog-head"><h3>${esc(data.title)} · 全文</h3><button type="button" id="closeKnowledgeText" class="icon" aria-label="收起全文">×</button></div><p class="muted">${data.characters} 字 · ${data.chunk_count} 段${data.truncated?' · 仅显示前 20 万字':''}。这是知识库抽取的原文，模型实际只收到命中的相关段落。</p><pre class="knowledge-text">${esc(data.text)}</pre></section>`;
    $('#closeKnowledgeText').onclick=()=>{panel.innerHTML='';};
  }catch(error){panel.innerHTML='<p class="danger">'+esc(error.message)+'</p>';}
}

$('#knowledgeBtn').onclick=openKnowledgeDialog;
