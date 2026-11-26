'use strict';
let pptSettingsSnapshot=null;
$('#pptServiceDetails').append($('#pptProxyField').content.cloneNode(true));

async function openPptSettings(){
  try{
    notice();pptSettingsSnapshot=await api('ppt/settings');const image=await api('image/settings');const form=$('#pptSettingsForm');
    form.image_enabled.checked=image.enabled;
    for(const [field,key] of Object.entries({image_provider:'provider',image_model_name:'model',image_base_url:'base_url',image_size:'image_size',image_steps:'num_inference_steps',image_max_images:'max_images',image_proxy_url:'proxy_url',image_api_format:'api_format'}))form.elements.namedItem(field).value=image[key]??'';
    updateImageApiFormat();
    form.image_api_key.value='';$('#imageKeyStatus').textContent=image.configured?'生图密钥已配置，留空保留':'请保存生图服务密钥';$('#imageConnectionResult').textContent='';
    for(const key of ['provider','asset_mode','gamma_engine','quality','theme_id','image_model','style','custom_base_url','proxy_url'])form.elements.namedItem(key).value=pptSettingsSnapshot[key]||'';
    form.elements.namedItem('api_key').value='';
    $('#pptKeyStatus').textContent=pptSettingsSnapshot.configured?'当前服务密钥已配置，留空保留':'当前服务尚未配置密钥';
    $('#pptConnectionResult').textContent='';$('#pptServiceDetails').open=pptSettingsSnapshot.provider!=='native'&&!pptSettingsSnapshot.configured;updatePptSettingFields();$('#pptSettingsDialog').showModal();
  }catch(error){notice(error.message);}
}
function updatePptSettingFields(){const provider=$('#pptSettingsForm').elements.namedItem('provider').value;$('#pptCredentialFields').hidden=provider==='native';$('#gammaOptions').hidden=provider!=='gamma';$('#customOptions').hidden=provider!=='custom';$('#testPptConnection').disabled=provider==='native';}
function updateImageApiFormat(){const f=$('#pptSettingsForm'),official=f.image_provider.value==='siliconflow';if(official)f.image_api_format.value='siliconflow';f.image_api_format.disabled=official;f.image_steps.disabled=f.image_api_format.value==='openai';}
$('#pptSettingsForm [name="image_provider"]').onchange=updateImageApiFormat;$('#pptSettingsForm [name="image_api_format"]').onchange=updateImageApiFormat;
const pptSettingsButton=document.createElement('button');pptSettingsButton.id='pptSettingsBtn';pptSettingsButton.textContent='▧ 课件外观与服务';pptSettingsButton.onclick=openPptSettings;$('#settingsBtn').after(pptSettingsButton);
$('#closePptSettings').onclick=()=>$('#pptSettingsDialog').close();$('#pptSettingsForm [name="provider"]').onchange=()=>{updatePptSettingFields();$('#pptKeyStatus').textContent='保存后会显示所选服务的配置状态';};
bindAction($('#pptSettingsForm'),'submit',async e=>{const data=Object.fromEntries(new FormData(e.target));data.auto_generate=true;
const image=await api('image/settings',{enabled:e.target.image_enabled.checked,provider:data.image_provider,model:data.image_model_name,base_url:data.image_base_url,image_size:data.image_size,num_inference_steps:Number(e.target.image_steps.value),max_images:Number(data.image_max_images),proxy_url:data.image_proxy_url,api_key:data.image_api_key,api_format:data.image_api_format||'siliconflow'});
e.target.image_api_key.value='';$('#imageKeyStatus').textContent=image.configured?'生图密钥已配置，不会回显':'请保存生图服务密钥';
pptSettingsSnapshot=await api('ppt/settings',data);e.target.elements.namedItem('api_key').value='';$('#pptKeyStatus').textContent=pptSettingsSnapshot.configured?'密钥已配置，不会回显':'当前未配置外部服务密钥';$('#pptConnectionResult').textContent='设置已保存，创建课程时将自动生成整套材料。';if(state.tab==='materials'&&$('#materialsDelivery'))await renderMaterialDelivery($('#materialsDelivery'),state.version);});
bindAction($('#testImageConnection'),'click',async()=>{if($('#pptSettingsForm').image_api_key.value)throw new Error('请先保存设置再检查');$('#imageConnectionResult').textContent='正在检查已保存的生图模型…';try{const result=await api('image/test-connection',{});$('#imageConnectionResult').textContent='模型可用：'+result.model+'，未创建生图任务。';}catch(error){$('#imageConnectionResult').textContent=error.message;}});
bindAction($('#testPptConnection'),'click',async()=>{const form=$('#pptSettingsForm');if(form.elements.namedItem('api_key').value)throw new Error('请先保存课件设置，再检查连接。');$('#pptConnectionResult').textContent='正在检查连接…';try{await api('ppt/test-connection',{});$('#pptConnectionResult').textContent='连接检查通过，未创建收费生成任务。';}catch(e){$('#pptConnectionResult').textContent=e.message;}});

async function ensureMaterialSettings(){
  const settings=await api('ppt/settings');
  if(settings.provider!=='native'&&!settings.configured){await openPptSettings();throw new Error('请先保存课件服务设置，再创建课程。');}
  const image=await api('image/settings');if(image.enabled&&!image.configured){await openPptSettings();throw new Error('请先保存生图服务密钥，再创建课程。');}
}

async function renderMaterialDelivery(element,version){
  if(!element||!version)return;
  const request=uid();element.dataset.deliveryRequest=request;
  try{
    const result=await api('delivery?version='+encodeURIComponent(version.id));
    if(!element.isConnected||state.version?.id!==version.id||element.dataset.deliveryRequest!==request)return;
    const signature=JSON.stringify(result),previousState=element.dataset.deliveryState;
    element.dataset.deliveryState=result.state;element.dataset.deliveryVersion=version.id;
    if(element.dataset.deliverySignature===signature&&previousState===result.state)return;
    element.dataset.deliverySignature=signature;
    if(result.state==='ready'){
      element.innerHTML=`<p class="delivery-ready"><span>✓</span> 整套教学材料已就绪</p><p class="muted">课件、教案、逐字稿和检测题已生成并校验。教学课件共 ${result.pages} 页，可在 PowerPoint 中编辑。</p><div class="downloads"><a data-material-ppt href="${fileLink('课件.pptx')}">↓ 教学课件.pptx</a><a href="${fileLink('教案.docx')}">↓ 教案.docx</a><a href="${fileLink('逐字稿.docx')}">↓ 逐字稿.docx</a><a href="${fileLink('检测题与答案.docx')}">↓ 检测题与答案.docx</a><a class="bundle-download" href="${fileLink('交付包.zip')}">↓ 下载整套材料</a></div><small>上课前请核对教材、知识内容与本班适用情况。</small>`;
      const pageCount=element.closest('#tabContent')?.querySelector('.stat-grid .stat:nth-child(3) b');if(pageCount)pageCount.textContent=result.pages;
    }else if(result.state==='generating'){
      const steps=['design','documents','presentation','validation'],current=Math.max(0,steps.indexOf(result.phase));
      element.innerHTML=`<p><strong>正在生成整套教学材料</strong></p><ol class="material-progress">${['设计课堂内容','编写配套材料','制作教学课件','校对与打包'].map((label,index)=>`<li class="${index<current?'done':index===current?'current':''}">${index<current?'✓ ':''}${label}</li>`).join('')}</ol><p class="muted">${esc(teacherStage({phase:result.phase,status:'running'}))}，完成后会在这里提供整套下载。刷新或关闭窗口后可继续查看。</p><button id="cancelMaterials">停止生成</button>`;
      bindAction($('#cancelMaterials'),'click',async()=>{await api('cancel',{id:result.job_id});await refreshJobs();});
    }else{
      const canRestart=result.can_restart&&result.state!=='needs_setup';
      element.innerHTML=`<p><strong>${result.state==='needs_setup'?'请完成生成设置':'整套材料尚未完成'}</strong></p><p class="muted">${esc(result.message||'课程内容已保存，继续生成即可完成本版材料。')}</p>${result.detail?`<details><summary>查看未完成的原因</summary><p class="danger">${esc(result.detail)}</p></details>`:''}<div class="actions"><button class="primary" id="completeMaterials">${result.state==='needs_setup'?'打开生成设置':'继续生成整套材料'}</button>${canRestart?'<button id="restartMaterials">重新制作课件</button>':''}</div>${canRestart?'<small>仅在继续后仍然失败时使用。会沿用已保存的课件设计并新建一次课件任务，可能产生课件服务费用。</small>':''}`;
      bindAction($('#completeMaterials'),'click',async()=>{if(result.state==='needs_setup'){await openPptSettings();return;}element.dataset.deliveryState='generating';await api('start',{kind:'complete',target:version.id,key:uid()});await refreshJobs();});
      if(canRestart)bindAction($('#restartMaterials'),'click',async()=>{if(!confirm('重新制作会沿用已保存的课件设计并新建一次课件任务，可能产生课件服务费用。确定继续吗？'))return;element.dataset.deliveryState='generating';await api('start',{kind:'complete',target:version.id,key:uid(),restart:true});await refreshJobs();});
    }
  }catch(error){
    if(element.isConnected&&state.version?.id===version.id){delete element.dataset.deliverySignature;element.innerHTML='<p class="danger">'+esc(error.message)+'</p><button id="reloadMaterials">重新读取材料</button>';bindAction($('#reloadMaterials'),'click',()=>renderMaterialDelivery(element,version));}
  }
}
