export function setupProviders({api,toast,onChange}){
  const $=id=>document.getElementById(id),dialog=$('api-settings');
  let settings=null,viewing=null,editingProvider=null,editingModel=null,busy=false,loading=false,testNotice=null;
  const testResults=new Map();
  function node(tag,className,text){const value=document.createElement(tag);if(className)value.className=className;if(text!==undefined)value.textContent=text;return value;}
  function iconButton(name,label,extra=''){
    const button=node('button','icon-button compact-icon '+extra),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS(svg.namespaceURI,'use');
    use.setAttribute('href','#i-'+name);svg.setAttribute('aria-hidden','true');svg.append(use);button.append(svg);button.type='button';button.disabled=busy;button.title=label;button.setAttribute('aria-label',label);return button;
  }
  function modelAction(editing=false){const button=$('save-model'),label=editing?'Lưu model':'Thêm model';button.title=label;button.setAttribute('aria-label',label);button.querySelector('use').setAttribute('href',editing?'#i-check':'#i-plus');}
  function error(message=''){$('api-settings-error').textContent=message;$('api-settings-error').hidden=!message;}
  function lock(value){busy=value;dialog.querySelectorAll('button,input,select').forEach(input=>{if(input.id!=='close-api-settings')input.disabled=value;});if(!value){$('provider-picker').disabled=!settings?.providers.length;$('provider-mobile-enabled').disabled=!currentProvider();}}
  function currentProvider(){return settings?.providers.find(provider=>provider.id===viewing);}
  function accept(data){settings=data;if(!data.providers.some(provider=>provider.id===viewing))viewing=data.selected_provider || data.providers[0]?.id || null;onChange(data.enabled);}
  function resetModel(){editingModel=null;$('model-name').value='';$('model-form-label').textContent='Thêm model';modelAction();$('cancel-model').hidden=true;}
  function render(){
    if(!settings)return;const active=settings.active_providers || [];
    const current=$('api-current');current.classList.remove('test-ok','test-failed');current.replaceChildren();
    if(testNotice){current.classList.add(testNotice.ok?'test-ok':'test-failed');current.textContent=testNotice.message;}
    else if(active.length)current.append(node('strong','',`${active.length} API đang bật · tối đa ${settings.parallelism} ảnh song song`));
    else current.textContent='Chưa bật API có model.';
    const list=$('provider-list'),picker=$('provider-picker');list.replaceChildren();picker.replaceChildren();
    for(const provider of settings.providers){
      const selected=provider.models.find(model=>model.id===provider.preferred_model_id)?.model || 'Chưa chọn model';
      const option=node('option','',(provider.enabled?'✓ ':'')+provider.name);option.value=provider.id;picker.append(option);
      const row=node('div','provider-row'+(provider.id===viewing?' viewing':'')),check=node('input'),description=node('button','provider-choice'),text=node('span');check.type='checkbox';check.name='active-provider';check.value=provider.id;check.checked=provider.enabled;check.disabled=busy;check.setAttribute('aria-label','Bật API '+provider.name);
      const modelName=node('small','',selected);modelName.title=selected;text.append(node('strong','',provider.name),modelName);description.append(text);description.type='button';description.disabled=busy;description.setAttribute('aria-label','Xem model của '+provider.name);description.setAttribute('aria-pressed',String(provider.id===viewing));
      check.addEventListener('change',()=>choose(provider.id,undefined,check.checked));
      description.addEventListener('click',()=>{viewing=provider.id;resetModel();render();});
      const edit=iconButton('edit','Sửa API '+provider.name);edit.addEventListener('click',()=>showProviderForm(provider));row.append(check,description,edit);list.append(row);
    }
    picker.value=viewing || '';picker.disabled=busy||!settings.providers.length;
    if(!settings.providers.length)list.append(node('p','api-note','Chưa có API nào.'));
    $('provider-form').hidden=true;$('provider-empty').hidden=Boolean(currentProvider());$('provider-detail').hidden=!currentProvider();
    const provider=currentProvider();$('provider-mobile-enabled').disabled=busy||!provider;$('provider-mobile-enabled').checked=Boolean(provider?.enabled);if(!provider)return;
    $('provider-detail-name').textContent=provider.name;$('provider-detail-model').textContent=provider.models.find(model=>model.id===provider.preferred_model_id)?.model || 'Chưa chọn model';$('provider-detail-model').title=$('provider-detail-model').textContent;$('model-count').textContent=provider.models.length+' model';
    $('provider-detail-state').textContent=!provider.enabled?'Đang tắt · chọn model rồi tích bật API để dùng.':!provider.preferred_model_id?'Đang bật · cần thêm hoặc chọn model vision.':provider.cooldown_until>Date.now()?'API đang tạm chờ do lỗi. Có thể sửa kết nối hoặc tắt/bật API để thử lại.':'Đang bật · dùng model đã chọn, tối đa 2 ảnh song song.';
    const models=$('model-list');models.replaceChildren();
    for(const model of provider.models){
      const row=node('div','model-row'+(model.id===provider.preferred_model_id?' active':'')),label=node('label'),radio=node('input'),name=node('span','',model.model);name.title=model.model;radio.type='radio';radio.name='active-model-'+provider.id;radio.value=model.id;radio.checked=model.id===provider.preferred_model_id;radio.disabled=busy;radio.addEventListener('change',()=>choose(provider.id,model.id));label.append(radio,name);
      const result=testResults.get(model.id),test=iconButton(result?(result.ok?'check':'alert'):'test','Test vision model '+model.model,'api-test-button'+(result?(result.ok?' test-ok':' test-failed'):''));
      if(result){test.title=model.model+': '+result.message;test.setAttribute('aria-label','Test lại vision model '+model.model+'. '+result.message);}
      test.addEventListener('click',()=>testModel(model,test));
      const edit=iconButton('edit','Sửa model '+model.model);edit.addEventListener('click',()=>{editingModel=model.id;$('model-name').value=model.model;$('model-form-label').textContent='Sửa model';modelAction(true);$('cancel-model').hidden=false;$('model-name').focus();});
      const remove=iconButton('trash','Xóa model '+model.model,'danger-icon');remove.addEventListener('click',async()=>{if(!confirm('Xóa model '+model.model+'?'))return;await mutate('/v1/ai/models/'+model.id,'DELETE');});
      const actions=node('div','model-actions');actions.append(test,edit,remove);row.append(label,actions);models.append(row);
    }
    if(!provider.models.length)models.append(node('p','api-note','Chưa có model. Thêm Model ID bên dưới rồi tích chọn để dùng.'));
  }
  async function mutate(path,method,body,after){
    if(busy)return;error();lock(true);
    try{accept(await api(path,{method,...(body?{body:JSON.stringify(body)}:{})}));testResults.clear();resetModel();after?.();render();return true;}
    catch(cause){error(cause.message);return false;}
    finally{lock(false);}
  }
  async function testModel(model,button){
    if(busy)return;error();lock(true);button.classList.add('is-testing');button.setAttribute('aria-busy','true');button.title='Đang test vision…';button.querySelector('use').setAttribute('href','#i-refresh');
    try{
      const result=await api('/v1/ai/models/'+model.id+'/test',{method:'POST',body:'{}'});testResults.set(model.id,result);
      testNotice={ok:result.ok,message:model.model+': '+result.message+(Number.isFinite(result.elapsed_ms)?` (${(result.elapsed_ms/1000).toFixed(1)} giây)`:'')};
    }catch(cause){testResults.set(model.id,{ok:false,message:cause.message});testNotice={ok:false,message:model.model+': '+cause.message};}
    finally{lock(false);render();}
  }
  async function choose(providerId,modelId,enabled){
    const ok=await mutate('/v1/ai/selection','PUT',{provider_id:providerId,...(modelId?{model_id:modelId}:{}),...(enabled!==undefined?{enabled}:{})});if(!ok)render();
  }
  $('provider-picker').addEventListener('change',()=>{viewing=$('provider-picker').value;resetModel();render();});
  $('provider-mobile-enabled').addEventListener('change',()=>{if(viewing)choose(viewing,undefined,$('provider-mobile-enabled').checked);});
  function showProviderForm(provider){
    if(busy)return;error();editingProvider=provider?.id || null;if(provider){viewing=provider.id;resetModel();render();}
    $('provider-form').hidden=false;$('provider-detail').hidden=true;$('provider-empty').hidden=true;$('provider-form-title').textContent=provider?'Sửa API':'Thêm API';
    $('provider-name').value=provider?.name || '';$('provider-url').value=provider?.base_url || '';$('provider-key').value='';$('provider-key').required=!provider;
    $('provider-key').placeholder=provider?'Đã lưu key · để trống để giữ nguyên':'Nhập API key';$('provider-name').focus();
  }
  $('open-api-settings').addEventListener('click',async()=>{
    if(loading||busy)return;error();testResults.clear();testNotice=null;dialog.showModal();$('api-current').textContent='Đang mở cấu hình…';loading=true;lock(true);
    try{accept(await api('/v1/ai/settings'));resetModel();render();}catch(cause){error(cause.message);}finally{loading=false;lock(false);}
  });
  $('close-api-settings').addEventListener('click',()=>dialog.close());
  let outside=false;
  const isOutside=event=>{const box=dialog.getBoundingClientRect();return event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom;};
  dialog.addEventListener('pointerdown',event=>outside=event.target===dialog&&isOutside(event));
  dialog.addEventListener('click',event=>{if(outside&&event.target===dialog&&isOutside(event))dialog.close();outside=false;});
  dialog.addEventListener('close',()=>{$('provider-key').value='';});
  $('add-provider').addEventListener('click',()=>showProviderForm());
  $('edit-provider').addEventListener('click',()=>showProviderForm(currentProvider()));
  $('cancel-provider').addEventListener('click',()=>{error();$('provider-key').value='';render();});
  $('provider-form').addEventListener('submit',async event=>{
    event.preventDefault();const previous=new Set(settings.providers.map(provider=>provider.id)),id=editingProvider;
    const body={name:$('provider-name').value.trim(),base_url:$('provider-url').value.trim(),...($('provider-key').value?{api_key:$('provider-key').value}:{})};
    const ok=await mutate('/v1/ai/providers'+(id?'/'+id:''),id?'PATCH':'POST',body,()=>viewing=id || settings.providers.find(provider=>!previous.has(provider.id))?.id || viewing);
    if(ok){$('provider-key').value='';toast(id?'Đã cập nhật API.':'Đã lưu API. Thêm model để dùng.');$('model-name').focus();}
  });
  $('delete-provider').addEventListener('click',async()=>{
    const provider=currentProvider();if(!provider||!confirm(`Xóa API ${provider.name} và toàn bộ model đã lưu của API này?`))return;
    if(await mutate('/v1/ai/providers/'+provider.id,'DELETE'))toast('Đã xóa API.');
  });
  $('model-form').addEventListener('submit',async event=>{
    event.preventDefault();const provider=currentProvider();if(!provider)return;
    const id=editingModel;if(await mutate(id?'/v1/ai/models/'+id:'/v1/ai/providers/'+provider.id+'/models',id?'PATCH':'POST',{model:$('model-name').value.trim()}))toast(id?'Đã cập nhật model.':'Đã lưu model.');
  });
  $('cancel-model').addEventListener('click',resetModel);
}
