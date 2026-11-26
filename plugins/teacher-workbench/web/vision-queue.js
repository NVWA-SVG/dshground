'use strict';

async function openVisionQueue(knowledgeId,chapterId=null){
  const dialog=$('#visionQueueDialog');let timer,loading=false,previewSequence=0,closed=false;
  dialog.innerHTML='<p class="loading">正在读取识图范围与任务…</p>';if(!dialog.open)dialog.showModal();
  const cleanup=()=>{closed=true;clearInterval(timer);};dialog.addEventListener('close',cleanup,{once:true});
  try{
    const initial=await api('vision/queue/preview?'+new URLSearchParams({knowledge:knowledgeId}));
    dialog.innerHTML=`<div class="dialog-head"><h2>教材图片 · AI 描述</h2><button type="button" id="closeVisionQueue" class="icon" aria-label="关闭识图任务">×</button></div>
      <p>${esc(initial.book_title)}</p><p class="muted">一次启动后在后台逐张识别，最多同时处理两张。已有描述会跳过，相同图片会复用已保存的识别结果；教材留空题保留原图。</p>
      <label>本次识别范围<select id="visionQueueScope"><option value="">请选择章节或整册</option>${initial.chapters.map(chapter=>`<option value="${esc(chapter.id)}">${esc(chapter.title)}（第 ${chapter.start_page}—${chapter.end_page} 页）</option>`).join('')}<option value="whole">${esc(initial.scope_title)} · 全部待描述插图</option></select></label>
      <div id="visionQueuePreview" role="status">选择范围后显示本次需要调用模型的图片数。</div><div class="actions"><button type="button" id="visionQueueStart" class="primary" disabled>启动识别</button></div>
      <p class="muted">识图请求会消耗 SiliconFlow 额度。关闭本窗口后任务继续执行；暂停会等待已经发出的请求保存。工作台重启后需手动继续。</p>
      <h3>已保存的识图任务</h3><div id="visionQueueJobs" aria-live="polite"></div><p id="visionQueueError" class="danger" role="status"></p>`;
    $('#closeVisionQueue').onclick=()=>dialog.close();
    const scope=$('#visionQueueScope'),start=$('#visionQueueStart');let selection=null,requestKey=uid(),lastProgress='';
    async function updatePreview(){
      const sequence=++previewSequence;selection=null;start.disabled=true;
      if(!scope.value){$('#visionQueuePreview').textContent='请先选择章节或整册。';return;}
      $('#visionQueuePreview').textContent='正在统计图片…';
      try{
        const p=await api('vision/queue/preview?'+new URLSearchParams({knowledge:knowledgeId,...(scope.value==='whole'?{}:{chapter:scope.value})}));
        if(sequence!==previewSequence||closed)return;selection=p;requestKey=uid();
        $('#visionQueuePreview').innerHTML=`<p><strong>${esc(p.scope_title)}</strong>：尚未保存描述 ${p.pending} 张，其中已在任务中 ${p.in_progress||0} 张、结果待确认 ${p.uncertain||0} 张、仅待保存 ${p.writeback_pending||0} 张。</p><p>本次可启动 ${p.selected} 张，其中 ${p.cache_hits} 张可复用缓存，预计新请求 ${p.new_requests} 张。</p>${p.uncertain?'<p class="muted">待确认图片请使用下方原任务的单独重试按钮，已完成的图片不会重新识别。</p>':''}<p class="muted">跳过已有描述 ${p.described} 张、教材留空题 ${p.questions} 张。视觉模型：${esc(p.model)}</p>`;
        start.textContent=p.active_job?'已有任务正在执行':p.selected===0?'本范围暂无新图片':p.new_requests?`启动识别（${p.new_requests} 张新请求）`:'保存缓存中的描述';start.disabled=!!p.active_job||p.selected===0;
      }catch(error){$('#visionQueuePreview').textContent=error.message;}
    }
    scope.onchange=updatePreview;
    if(chapterId&&initial.chapters.some(c=>c.id===chapterId))scope.value=chapterId;
    else if(!initial.chapters.length)scope.value='whole';
    if(scope.value)await updatePreview();
    start.onclick=async()=>{
      if(!selection||start.disabled)return;start.disabled=true;
      try{
        await api('vision/queue/start',{knowledge_id:knowledgeId,chapter_id:scope.value==='whole'?null:scope.value,key:requestKey});
        notice('识图任务已保存并在后台执行。');await refreshJobs();
      }catch(error){$('#visionQueueError').textContent=error.message;}
      finally{await updatePreview();}
    };
    const labels={queued:'等待执行',running:'正在识别',pausing:'正在暂停',paused:'已暂停',completed:'已完成',needs_attention:'部分图片待处理'};
    async function refreshJobs(){
      if(loading||closed)return;loading=true;
      try{
        const data=await api('vision/queue');if(closed)return;
        const jobs=data.jobs.filter(j=>j.root_id===initial.root_id);
        $('#visionQueueJobs').innerHTML=jobs.length?jobs.map(job=>{
          const c=job.counts,active=['running','pausing','queued'].includes(job.status);
          return `<article class="vision-queue-job" data-vision-job="${esc(job.id)}"><div class="dialog-head"><strong>${esc(job.scope_title)}</strong><span>${labels[job.status]||esc(job.status)}</span></div>
            <progress max="${Math.max(1,c.total)}" value="${c.done}" aria-label="识图进度"></progress><p>已保存 ${c.done}/${c.total} 张 · 缓存复用 ${c.cached} 张 · 等待 ${c.pending} 张 · 处理中 ${c.inflight} 张</p>
            <p class="muted">本任务已尝试发送 ${job.api_requests} 次请求。${esc(job.message)}</p>
            ${job.current.map(item=>`<small>正在处理第 ${item.page||'—'} 页${item.elapsed_seconds!=null?` · 已等待 ${item.elapsed_seconds} 秒`:''} · ${item.phase==='preparing'?'正在读取本机素材':item.phase==='waiting'?'正在等待接口配额':item.phase==='reading'?'正在读取识图结果':'正在等待识图响应'}：${esc(item.caption)}</small>`).join('')}
            ${c.failed||c.uncertain||c.writeback_failed?`<p class="danger">失败 ${c.failed} 张 · 结果待确认 ${c.uncertain} 张 · 仅待保存 ${c.writeback_failed} 张</p>`:''}
            ${job.errors.length?`<details><summary>查看需要处理的图片</summary>${job.errors.map(e=>`<p>第 ${e.page||'—'} 页：${esc(e.error)}</p>`).join('')}</details>`:''}
            ${!active&&(c.failed||c.uncertain||c.writeback_failed)?'<button data-vision-action="reconcile">检查本地已补全描述（不调用模型）</button>':''}
            <div class="actions">${active?`<button data-vision-action="pause" ${job.status==='pausing'?'disabled':''}>暂停</button>`:`${c.pending?'<button data-vision-action="resume">继续未发送图片</button>':''}${c.failed?'<button data-vision-action="retry_failed">重试失败图片</button>':''}${c.writeback_failed?'<button data-vision-action="save_cached">仅重新保存描述（不调用模型）</button>':''}${c.uncertain?'<button data-vision-action="retry_uncertain">重新发送待确认图片（可能重复计费）</button>':''}`}
            ${!job.chapter_id&&c.pending?'<button data-vision-action="priority">优先处理上方所选章节</button>':''}</div></article>`;
        }).join(''):'<p class="muted">暂无识图任务。</p>';
        const progress=JSON.stringify(jobs.map(j=>[j.id,j.status,j.counts]));
        if(progress!==lastProgress){lastProgress=progress;if(scope.value)await updatePreview();}
        $('#visionQueueJobs').querySelectorAll('[data-vision-action]').forEach(button=>bindAction(button,'click',async()=>{
          const action=button.dataset.visionAction;
          try{
            if(action==='priority'&&(!scope.value||scope.value==='whole'))throw new Error('请在上方选择要优先处理的具体章节。');
            await api('vision/queue/control',{id:button.closest('[data-vision-job]').dataset.visionJob,action,chapter_id:scope.value==='whole'?null:scope.value});
            $('#visionQueueError').textContent='';await refreshJobs();await updatePreview();
          }catch(error){$('#visionQueueError').textContent=error.message;throw error;}
        }));
      }catch(error){if(!closed)$('#visionQueueError').textContent=error.message;}
      finally{loading=false;}
    }
    await refreshJobs();timer=setInterval(()=>void refreshJobs(),1800);
  }catch(error){dialog.innerHTML='<p class="danger">'+esc(error.message)+'</p><button id="closeVisionQueueError">关闭</button>';$('#closeVisionQueueError').onclick=()=>dialog.close();}
}
