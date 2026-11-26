'use strict';
window.TeacherForms=(()=>{
  const initialized=new WeakSet();let sequence=0;
  const controls=form=>[...form.elements].filter(field=>field.matches?.('input,select,textarea')&&!field.disabled&&field.type!=='hidden'&&!field.readOnly);
  const labelFor=field=>field.labels?.[0]||field.closest('label');
  const nameFor=field=>field.dataset.validationLabel||[...(labelFor(field)?.childNodes||[])].filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent).join('').trim().replace(/\s*[（(]选填[）)]/g,'')||field.name||'此项';
  function errorNode(field){
    if(!field.id)field.id='teacher-field-'+(++sequence);
    let error=document.getElementById(field.id+'-error');
    if(!error){error=document.createElement('small');error.id=field.id+'-error';error.className='field-error';error.hidden=true;(labelFor(field)||field.parentElement).append(error);}
    return error;
  }
  function showError(field,message){
    const error=errorNode(field);error.textContent=message;error.hidden=!message;
    field.classList.toggle('field-invalid',!!message);
    const descriptions=new Set((field.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));
    if(message){field.setAttribute('aria-invalid','true');descriptions.add(error.id);}
    else{field.removeAttribute('aria-invalid');descriptions.delete(error.id);}
    if(descriptions.size)field.setAttribute('aria-describedby',[...descriptions].join(' '));else field.removeAttribute('aria-describedby');
  }
  function messageFor(field){
    const label=nameFor(field),value=String(field.value||'');
    if(field.validity?.badInput)return `${label}请输入有效数字。`;
    if(field.required){
      if(field.type==='checkbox'&&!field.checked||field.type==='radio'&&field.validity?.valueMissing)return '请勾选此项确认。';
      if(field.type==='file'&&!field.files?.length)return `请选择${label}。`;
      if(!['checkbox','radio','file'].includes(field.type)&&!value.trim())return `${field.tagName==='SELECT'?'请选择':'请填写'}${label}。`;
    }
    if(field.validity?.rangeUnderflow||field.validity?.rangeOverflow){
      if(field.min&&field.max)return `${label}请输入 ${field.min}—${field.max} 之间的数值。`;
      return `${label}超出允许范围。`;
    }
    if(field.validity?.stepMismatch)return `${label}请输入符合步长要求的数值。`;
    if(field.validity?.typeMismatch)return `请填写有效的${label}。`;
    if(field.validity?.tooLong)return `${label}最多填写 ${field.maxLength} 个字符。`;
    if(field.validity?.tooShort)return `${label}至少填写 ${field.minLength} 个字符。`;
    return field.validity&&!field.validity.valid?`请检查${label}的内容。`:'';
  }
  function requirements(form){
    const type=form.elements.namedItem('course_type'),subject=form.elements.namedItem('subject');
    if(type&&subject){const wasRequired=subject.required;type.required=true;subject.required=type.value==='subject';if(wasRequired&&!subject.required)showError(subject,'');}
    for(const field of controls(form)){
      const label=labelFor(field);if(!label)continue;
      if(!field.id)field.id='teacher-field-'+(++sequence);
      let mark=[...label.querySelectorAll('.required-marker')].find(node=>node.dataset.field===field.id);
      if(field.required&&!mark){mark=document.createElement('span');mark.className='required-marker';mark.dataset.field=field.id;mark.textContent=' *';mark.setAttribute('aria-hidden','true');label.append(mark);}
      if(mark)mark.hidden=!field.required;
    }
  }
  function summary(form,invalid){
    let node=form.querySelector('.form-validation-summary');
    if(!node){node=document.createElement('p');node.className='form-validation-summary';node.setAttribute('role','alert');form.prepend(node);}
    node.hidden=!invalid.length;node.textContent=invalid.length?'请先补齐或修改：'+[...new Set(invalid.map(nameFor))].join('、')+'。':'';
  }
  function validate(form,{focus=true}={}){
    requirements(form);const invalid=[];
    for(const field of controls(form)){const message=messageFor(field);showError(field,message);if(message)invalid.push(field);}
    summary(form,invalid);
    if(focus&&invalid.length){let parent=invalid[0].parentElement;while(parent&&parent!==form){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
      invalid[0].focus({preventScroll:true});invalid[0].scrollIntoView({block:'center'});}
    return !invalid.length;
  }
  function attach(form){
    if(!form||initialized.has(form))return;initialized.add(form);form.noValidate=true;requirements(form);
    form.addEventListener('focusout',event=>{if(controls(form).includes(event.target))showError(event.target,messageFor(event.target));});
    const update=event=>{
      requirements(form);
      if(form.dataset.validationSubmitted==='true')validate(form,{focus:false});
      else if(controls(form).includes(event.target)&&event.target.hasAttribute('aria-invalid'))showError(event.target,messageFor(event.target));
    };
    form.addEventListener('input',update);form.addEventListener('change',update);
  }
  return {attach,validate:form=>{form.dataset.validationSubmitted='true';return validate(form);}};
})();
