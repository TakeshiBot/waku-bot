export function setupProviders({api,toast,onChange}){
  const $=id=>document.getElementById(id),dialog=$('api-settings');
  let settings=null,viewing=null,editingProvider=null,editingModel=null,busy=false,loading=false;
  function node(tag,className,text){const value=document.createElement(tag);if(className)value.className=className;if(text!==undefined)value.textContent=text;return value;}
  function error(message=''){$('api-settings-error').textContent=message;$('api-settings-error').hidden=!message;}
  function lock(value){busy=value;dialog.querySelectorAll('button,input').forEach(input=>{if(input.id!=='close-api-settings')input.disabled=value;});}
  function currentProvider(){return settings?.providers.find(provider=>provider.id===viewing);}
  function accept(data){settings=data;if(!data.providers.some(provider=>provider.id===viewing))viewing=data.selected_provider || data.providers[0]?.id || null;onChange(data.enabled);}
  function resetModel(){editingModel=null;$('model-name').value='';$('model-form-label').textContent='Thêm model';$('save-model').textContent='Thêm model';$('cancel-model').hidden=true;}
  function render(){
    if(!settings)return;const active=settings.providers.find(provider=>provider.id===settings.selected_provider),model=active?.models.find(model=>model.id===settings.selected_model);
    $('api-current').textContent=model?`Đang dùng: ${active.name} · ${model.model}`:active?`${active.name} · Chọn hoặc thêm model để dùng AI.`:'Chưa chọn API và model. Upload thủ công vẫn dùng bình thường.';
    const list=$('provider-list');list.replaceChildren();
    for(const provider of settings.providers){
      const row=node('div','provider-row'+(provider.id===settings.selected_provider?' active':'')),label=node('label'),radio=node('input'),description=node('span');radio.type='radio';radio.name='active-provider';radio.value=provider.id;radio.checked=provider.id===settings.selected_provider;radio.disabled=busy;
      description.append(node('strong','',provider.name),node('small','',provider.base_url));label.append(radio,description);
      radio.addEventListener('change',()=>choose(provider.id));
      radio.addEventListener('click',()=>{if(provider.id===settings.selected_provider&&viewing!==provider.id){viewing=provider.id;resetModel();render();}});
      const edit=node('button','text-button','Sửa');edit.type='button';edit.disabled=busy;edit.setAttribute('aria-label','Sửa API '+provider.name);edit.addEventListener('click',()=>showProviderForm(provider));row.append(label,edit);list.append(row);
    }
    if(!settings.providers.length)list.append(node('p','api-note','Chưa có API nào.'));
    $('provider-form').hidden=true;$('provider-empty').hidden=Boolean(currentProvider());$('provider-detail').hidden=!currentProvider();
    const provider=currentProvider();if(!provider)return;
    $('provider-detail-name').textContent=provider.name;$('provider-detail-url').textContent=provider.base_url;$('model-count').textContent=provider.models.length+' model';
    const models=$('model-list');models.replaceChildren();
    for(const model of provider.models){
      const row=node('div','model-row'+(model.id===settings.selected_model?' active':'')),label=node('label'),radio=node('input');radio.type='radio';radio.name='active-model';radio.value=model.id;radio.checked=model.id===settings.selected_model;radio.disabled=busy;radio.addEventListener('change',()=>choose(provider.id,model.id));label.append(radio,node('span','',model.model));
      const edit=node('button','text-button','Sửa');edit.type='button';edit.disabled=busy;edit.setAttribute('aria-label','Sửa model '+model.model);edit.addEventListener('click',()=>{editingModel=model.id;$('model-name').value=model.model;$('model-form-label').textContent='Sửa model';$('save-model').textContent='Lưu model';$('cancel-model').hidden=false;$('model-name').focus();});
      const remove=node('button','danger-link','Xóa');remove.type='button';remove.disabled=busy;remove.setAttribute('aria-label','Xóa model '+model.model);remove.addEventListener('click',async()=>{if(!confirm('Xóa model '+model.model+'?'))return;await mutate('/v1/ai/models/'+model.id,'DELETE');});row.append(label,edit,remove);models.append(row);
    }
    if(!provider.models.length)models.append(node('p','api-note','Chưa có model. Thêm Model ID bên dưới rồi tích chọn để dùng.'));
  }
  async function mutate(path,method,body,after){
    if(busy)return;error();lock(true);
    try{accept(await api(path,{method,...(body?{body:JSON.stringify(body)}:{})}));resetModel();after?.();render();return true;}
    catch(cause){error(cause.message);return false;}
    finally{lock(false);}
  }
  async function choose(providerId,modelId){
    const before=viewing;viewing=providerId;const ok=await mutate('/v1/ai/selection','PUT',{provider_id:providerId,...(modelId?{model_id:modelId}:{})});if(!ok){viewing=before;render();}
  }
  function showProviderForm(provider){
    if(busy)return;error();editingProvider=provider?.id || null;if(provider)viewing=provider.id;
    $('provider-form').hidden=false;$('provider-detail').hidden=true;$('provider-empty').hidden=true;$('provider-form-title').textContent=provider?'Sửa API':'Thêm API';
    $('provider-name').value=provider?.name || '';$('provider-url').value=provider?.base_url || '';$('provider-key').value='';$('provider-key').required=!provider;
    $('provider-key').placeholder=provider?'Đã lưu key · để trống để giữ nguyên':'Nhập API key';$('provider-name').focus();
  }
  $('open-api-settings').addEventListener('click',async()=>{
    if(loading||busy)return;error();dialog.showModal();$('api-current').textContent='Đang mở cấu hình…';loading=true;lock(true);
    try{accept(await api('/v1/ai/settings'));resetModel();render();}catch(cause){error(cause.message);}finally{loading=false;lock(false);}
  });
  $('close-api-settings').addEventListener('click',()=>dialog.close());
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
