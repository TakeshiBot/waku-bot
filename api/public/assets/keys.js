export function setupKeys({api,toast}){
  const $=id=>document.getElementById(id),dialog=$('service-keys');let busy=false;
  const node=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;};
  function lock(value){busy=value;dialog.querySelectorAll('button,input').forEach(el=>{if(el.id!=='close-service-keys')el.disabled=value;});}
  async function load(){
    const data=await api('/v1/keys'),list=$('service-key-list');list.replaceChildren();
    for(const key of data.keys){
      const row=node('div','service-key-row'),info=node('div'),title=node('strong','',key.name),details=node('small','',`${key.prefix}… · ${key.enabled?'Đang bật':'Đã tắt'}${key.last_used_at?' · Dùng lần cuối '+new Date(key.last_used_at).toLocaleString('vi-VN'):''}`);info.append(title,details);
      const toggle=node('button','secondary',key.enabled?'Tắt':'Bật');toggle.type='button';toggle.addEventListener('click',()=>mutate('/v1/keys/'+key.id,'PATCH',{enabled:!key.enabled}));
      const rename=node('button','text-button','Sửa');rename.type='button';rename.addEventListener('click',()=>{const name=prompt('Tên key dịch vụ',key.name);if(name?.trim())mutate('/v1/keys/'+key.id,'PATCH',{name:name.trim()});});
      const remove=node('button','danger-link','Xóa');remove.type='button';remove.addEventListener('click',()=>{if(confirm('Thu hồi và xóa key '+key.name+'?'))mutate('/v1/keys/'+key.id,'DELETE');});row.append(info,toggle,rename,remove);list.append(row);
    }
    if(!data.keys.length)list.append(node('p','api-note','Chưa tạo key dịch vụ.'));
  }
  async function mutate(path,method,body){if(busy)return;lock(true);try{await api(path,{method,...(body?{body:JSON.stringify(body)}:{})});await load();}catch(e){toast(e.message);}finally{lock(false);}}
  $('open-service-keys').addEventListener('click',async()=>{dialog.showModal();lock(true);try{await load();}catch(e){toast(e.message);}finally{lock(false);}});
  function clearCreated(){$('created-service-key').value='';$('service-key-created').hidden=true;$('service-key-name').value='';}
  $('close-service-keys').addEventListener('click',()=>{clearCreated();dialog.close();});dialog.addEventListener('close',clearCreated);
  let outside=false;const isOutside=e=>{const b=dialog.getBoundingClientRect();return e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom;};dialog.addEventListener('pointerdown',e=>outside=e.target===dialog&&isOutside(e));dialog.addEventListener('click',e=>{if(outside&&e.target===dialog&&isOutside(e))dialog.close();outside=false;});
  $('create-service-key').addEventListener('submit',async e=>{e.preventDefault();if(busy)return;lock(true);try{const data=await api('/v1/keys',{method:'POST',body:JSON.stringify({name:$('service-key-name').value.trim()})});if(dialog.open){$('created-service-key').value=data.key;$('service-key-created').hidden=false;}$('service-key-name').value='';await load();}catch(error){toast(error.message);}finally{lock(false);}});
  $('copy-service-key').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('created-service-key').value);toast('Đã sao chép key.');}catch{$('created-service-key').select();toast('Chọn và sao chép key trong ô.');}});
}
