const $=id=>document.getElementById(id);
const state={authenticated:false,maxMb:10,images:new Map(),cursor:null,query:'',rating:'',queue:[],pumping:false,paused:true,editing:null,listVersion:0,statsBusy:false};
const number=n=>new Intl.NumberFormat('vi-VN').format(n || 0);
const bytes=n=>n>=1073741824?(n/1073741824).toFixed(1)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';
const tagList=value=>value.trim().split(/[,\n]|\s+(?=#)/).map(x=>x.trim().replace(/^#/, '')).filter(Boolean);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let toastTimer,filterTimer,pollTimer;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4000);}
function element(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;}
function showLogin(message=''){
  state.authenticated=false;state.paused=true;clearInterval(pollTimer);$('app').hidden=true;$('login').hidden=false;$('login-error').textContent=message;
  $('editor').close();$('confirm-delete').close();
}
async function api(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{...(options.body && !(options.body instanceof FormData)?{'content-type':'application/json'}:{}),...options.headers},signal:options.signal || AbortSignal.timeout(30000)});
  let data;try{data=await response.json();}catch{throw new Error('Máy chủ chưa phản hồi đúng. Hãy thử lại sau.');}
  if(!response.ok){if(response.status===401 && state.authenticated)showLogin('Phiên đăng nhập đã hết hạn. Vui lòng nhập lại key.');const error=new Error(data.error?.message || 'Chưa thực hiện được thao tác.');error.status=response.status;error.retryAfter=Number(response.headers.get('retry-after')) || 2;throw error;}
  return data;
}
async function enter(){
  const session=await api('/v1/session');state.authenticated=true;state.maxMb=session.max_image_mb || 10;
  $('api-key').value='';$('login').hidden=true;$('app').hidden=false;$('upload-limits').textContent=`JPG, PNG, WebP, GIF, AVIF · tối đa ${state.maxMb} MB/ảnh`;
  await Promise.all([refreshStats(),loadImages(true)]);
  clearInterval(pollTimer);pollTimer=setInterval(()=>{if(state.authenticated && !document.hidden)refreshStats(true);},4500);
}
$('login-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;$('login-error').textContent='';
  try{await api('/v1/session',{method:'POST',headers:{authorization:'Bearer '+$('api-key').value.trim()}});await enter();}
  catch(error){$('login-error').textContent=error.message || 'Không kết nối được thư viện.';}
  finally{button.disabled=false;}
});
$('logout').addEventListener('click',async()=>{
  if(state.pumping && !confirm('Một số file đang tải. Đăng xuất sẽ dừng các file còn chờ. Tiếp tục?'))return;
  try{await api('/v1/session',{method:'DELETE'});}catch(error){toast(error.message);return;}
  showLogin();state.images.clear();state.queue.forEach(item=>{if(item.preview)URL.revokeObjectURL(item.preview);});state.queue=[];renderQueue();
});

async function refreshStats(updateGallery=false){
  if(state.statsBusy || !state.authenticated)return;state.statsBusy=true;
  try{
    const stats=await api('/v1/stats');$('stat-total').textContent=number(stats.images.total);$('nav-count').textContent=number(stats.images.total);$('stat-size').textContent=bytes(stats.images.bytes);
    const pending=Number(stats.ai_pending)+Number(stats.uploads_pending);$('stat-pending').textContent=number(pending);$('stat-pending-note').textContent=pending?`${number(stats.ai_pending)} AI · ${number(stats.uploads_pending)} upload`:'Mọi thứ đã sẵn sàng';
    if(pending)api('/v1/jobs/drain',{method:'POST'}).catch(()=>{});
    if(updateGallery)await loadImages(false);
  }catch(error){if(state.authenticated)$('list-status').textContent=error.message;}
  finally{state.statsBusy=false;}
}
async function loadImages(reset=false,more=false){
  const version=++state.listVersion,params=new URLSearchParams({limit:String(more?48:Math.min(100,Math.max(48,state.images.size)))});
  if(state.rating)params.set('rating',state.rating);if(state.query)params.set('q',state.query);if(more && state.cursor)params.set('before',state.cursor);
  if(reset){state.images.clear();$('list-status').textContent='Đang mở thư viện…';}
  try{
    const data=await api('/v1/images?'+params);if(version!==state.listVersion)return;
    if(reset)state.images.clear();
    for(const image of data.images)state.images.set(image.id,image);
    if(reset || more || state.images.size<=100)state.cursor=data.next;$('list-status').textContent='';renderGallery();
  }catch(error){if(version===state.listVersion)$('list-status').textContent=error.message;}
}
function renderGallery(){
  const gallery=$('gallery');gallery.replaceChildren();$('empty').hidden=state.images.size>0;
  if(!state.images.size){$('empty').querySelector('h3').textContent=state.query || state.rating?'Chưa có ảnh phù hợp.':'Một khởi đầu mới.';$('empty').querySelector('p').textContent=state.query || state.rating?'Thử từ khóa hoặc bộ lọc khác nhé.':'Thêm những tấm ảnh đầu tiên. Phần sắp xếp, cứ để Waku lo.';}
  $('visible-count').textContent=state.images.size?number(state.images.size):'';$('load-more').hidden=!state.cursor;
  for(const image of [...state.images.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))){
    const card=element('article','card '+image.rating),thumb=element('button','thumb');thumb.type='button';thumb.setAttribute('aria-label','Xem chi tiết '+(image.title || 'ảnh'));
    const img=element('img');img.src=new URL('/media/'+encodeURIComponent(image.id),location.origin).href;img.alt=image.title || 'Ảnh trong thư viện';img.loading='lazy';img.decoding='async';thumb.append(img);
    thumb.append(element('span','rating-label '+image.rating,image.rating==='safe'?'An toàn':image.rating==='r18'?'R18':'Chờ phân loại'));
    const copy=element('span','copy-mini');copy.append(icon('link'));copy.title='Mở chi tiết để sao chép link';thumb.append(copy);thumb.addEventListener('click',()=>openEditor(image.id));
    const title=element('button','card-title',image.title || (['pending','running'].includes(image.ai_status)?'AI đang đặt tên…':'Ảnh chưa có tên'));title.type='button';title.title=image.title || '';title.addEventListener('click',()=>openEditor(image.id));
    const tags=element('div','card-tags');for(const tag of image.tags.slice(0,2))tags.append(element('span','',tag));
    if(image.tags.length>2)tags.append(element('span','',`+${image.tags.length-2}`));
    if(['pending','running'].includes(image.ai_status))tags.append(element('span','ai-state busy','✦ AI'));
    else if(image.ai_status==='failed')tags.append(element('span','ai-state','AI lỗi'));
    card.append(thumb,title,tags);gallery.append(card);
  }
}
$('search').addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{state.query=$('search').value.trim();loadImages(true);},300);});
document.querySelectorAll('[data-rating]').forEach(button=>button.addEventListener('click',()=>{state.rating=button.dataset.rating;document.querySelectorAll('[data-rating]').forEach(b=>b.classList.toggle('active',b===button));loadImages(true);}));
$('reveal-r18').addEventListener('change',()=>{$('gallery').classList.toggle('reveal',$('reveal-r18').checked);});
$('load-more').addEventListener('click',()=>loadImages(false,true));$('refresh').addEventListener('click',()=>{loadImages(true);refreshStats();});
function setView(view){
  document.body.dataset.view=view;
  $('nav-library').classList.toggle('selected',view==='library');$('nav-upload').classList.toggle('selected',view==='upload');
  $('nav-library').setAttribute('aria-label','Kho ảnh');$('nav-upload').setAttribute('aria-label','Tải ảnh lên');
}
function focusUpload(){setView('upload');$('upload-panel').scrollTop=0;$('choose-files').focus({preventScroll:true});}
for(const id of ['upload-shortcut','nav-upload','empty-upload'])$(id).addEventListener('click',focusUpload);
$('nav-library').addEventListener('click',()=>{setView('library');$('library-results').scrollTop=0;});

function addFiles(files){
  let added=0;for(const file of files){
    const fingerprint=`${file.name}:${file.size}:${file.lastModified}`;if(state.queue.some(item=>item.fingerprint===fingerprint))continue;
    const valid=/\.(jpe?g|png|webp|gif|avif)$/i.test(file.name) || /^image\/(jpeg|png|webp|gif|avif)$/.test(file.type);
    const error=!valid?'Định dạng ảnh chưa được hỗ trợ.':file.size>state.maxMb*1048576?`Ảnh vượt ${state.maxMb} MB.`:'';
    state.queue.push({id:crypto.randomUUID(),fingerprint,file,name:file.name,preview:valid?URL.createObjectURL(file):null,status:error?'error':'queued',error,progress:0});added++;
  }
  renderQueue();if(added)toast(`Đã thêm ${added} ảnh vào hàng đợi.`);
}
$('choose-files').addEventListener('click',()=>$('file-input').click());$('file-input').addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('dragging');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',event=>addFiles(event.dataTransfer.files));
$('add-urls').addEventListener('click',()=>{
  const urls=$('image-urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);let added=0;
  for(const raw of urls){try{const url=new URL(raw);if(!['https:','http:'].includes(url.protocol) || url.username || url.password)throw new Error();if(state.queue.some(item=>item.url===url.href))continue;state.queue.push({id:crypto.randomUUID(),url:url.href,name:decodeURIComponent(url.pathname.split('/').pop() || url.hostname),status:'queued',progress:0});added++;}catch{toast('Có link không hợp lệ. Chỉ nhận link ảnh HTTP(S).');}}
  $('image-urls').value='';renderQueue();if(added)toast(`Đã thêm ${added} link ảnh.`);
});
function options(){return {tags:tagList($('upload-tags').value),rating:$('upload-rating').value,manual:!$('use-ai').checked,source_url:$('upload-source').value.trim() || null};}
function renderQueue(){
  const list=$('queue-list');list.replaceChildren();$('queue-count').textContent=number(state.queue.length);
  const done=state.queue.filter(x=>x.status==='done').length,errors=state.queue.filter(x=>x.status==='error').length,pending=state.queue.filter(x=>x.status==='queued').length,active=state.queue.filter(x=>x.status==='uploading').length;
  if(!state.queue.length)list.append(element('p','queue-empty','Ảnh bạn chọn sẽ xuất hiện ở đây.'));
  for(const item of state.queue){
    const row=element('div','queue-item '+item.status);row.dataset.id=item.id;
    if(item.preview){const img=element('img');img.src=item.preview;img.alt='';img.loading='lazy';row.append(img);}else{const mark=element('span','queue-file-icon');mark.append(icon('link'));row.append(mark);}
    const text=element('div');text.append(element('strong','',item.name));
    const description=item.status==='done'?(item.duplicate?'Đã có trong kho':'Đã lưu ảnh') :item.status==='error'?item.error:item.status==='uploading'?`Đang tải… ${item.progress}%`:(item.file?bytes(item.file.size)+' · Chờ tải':'Link ảnh · Chờ tải');
    text.append(element('small','',description));
    if(item.status==='uploading'){const progress=element('progress');progress.max=100;progress.value=item.progress;text.append(progress);}
    const remove=element('button','',item.status==='done'?'✓':'×');remove.type='button';remove.setAttribute('aria-label','Bỏ '+item.name+' khỏi hàng đợi');remove.disabled=item.status==='uploading';remove.addEventListener('click',()=>{state.queue=state.queue.filter(x=>x!==item);if(item.preview)URL.revokeObjectURL(item.preview);renderQueue();});row.append(text,remove);list.append(row);
  }
  $('queue-progress').hidden=!state.queue.length;$('queue-summary').textContent=`${done}/${state.queue.length} đã lưu${errors?' · '+errors+' lỗi':''}${active?' · '+active+' đang tải':''}`;$('batch-progress').max=Math.max(1,state.queue.length);$('batch-progress').value=done+errors;
  $('start-upload').disabled=!pending;$('start-upload').textContent=state.pumping&&!state.paused?'Đang tải lên…':state.paused && active?'Tiếp tục tải lên':`Tải ${pending || ''} ảnh lên`.replace('  ',' ');
  $('pause-upload').hidden=!state.pumping || state.paused;$('retry-errors').hidden=!errors;
  for(const id of ['use-ai','upload-rating','upload-tags','upload-source'])$(id).disabled=state.pumping&&!state.paused;
}
function fileUpload(item){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/v1/images');xhr.responseType='json';xhr.timeout=90000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable){item.progress=Math.round(event.loaded/event.total*95);const row=$('queue-list').querySelector(`[data-id="${item.id}"]`);if(row){row.querySelector('small').textContent=`Đang tải… ${item.progress}%`;row.querySelector('progress').value=item.progress;}}};
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else{const error=new Error(xhr.response?.error?.message || 'Tải ảnh chưa thành công.');error.status=xhr.status;error.retryAfter=Number(xhr.getResponseHeader('retry-after')) || 2;if(xhr.status===401)showLogin('Phiên đã hết hạn. Vui lòng đăng nhập lại.');reject(error);}};
    xhr.onerror=()=>reject(new Error('Mất kết nối. Bấm thử lại khi mạng ổn định.'));xhr.ontimeout=()=>reject(new Error('Quá thời gian chờ. Bạn có thể thử lại file này.'));
    const form=new FormData();form.set('file',item.file,item.file.name);form.set('tags',item.options.tags.join(','));form.set('rating',item.options.rating);form.set('manual',String(item.options.manual));if(item.options.source_url)form.set('source_url',item.options.source_url);xhr.send(form);
  });
}
async function worker(){
  while(!state.paused && state.authenticated){
    const item=state.queue.find(x=>x.status==='queued');if(!item)return;item.options??=structuredClone(state.activeOptions || options());item.status='uploading';item.progress=0;renderQueue();
    try{
      let data;for(let attempt=0;attempt<8;attempt++){
        try{data=item.file?await fileUpload(item):await api('/v1/images',{method:'POST',body:JSON.stringify({url:item.url,...item.options,source_url:item.options.source_url || item.url})});break;}
        catch(error){if(error.status!==429 || attempt===7)throw error;await sleep(Math.min(10000,Math.max(2,error.retryAfter || 2)*1000));}
      }
      item.status='done';item.progress=100;item.duplicate=data.duplicate;item.imageId=data.image.id;
    }catch(error){item.status='error';item.error=error.message || 'Tải ảnh chưa thành công.';}
    renderQueue();refreshStats(true);
  }
}
async function start(){
  if(!state.authenticated)return;
  const selected=options();if(selected.tags.length>30){toast('Tối đa 30 tag chung.');return;}
  if(!$('upload-source').checkValidity()){toast('Link nguồn chưa hợp lệ.');return;}state.activeOptions=selected;
  for(const item of state.queue)if(item.status==='queued')item.options=structuredClone(selected);
  state.paused=false;if(state.pumping){renderQueue();return;}state.pumping=true;renderQueue();
  try{await Promise.all([worker(),worker()]);}finally{state.pumping=false;renderQueue();refreshStats(true);}
}
$('start-upload').addEventListener('click',start);$('pause-upload').addEventListener('click',()=>{state.paused=true;renderQueue();toast('Đã tạm dừng. Các file đang tải sẽ hoàn tất trước.');});
$('retry-errors').addEventListener('click',()=>{for(const item of state.queue)if(item.status==='error'){item.status='queued';item.error='';item.progress=0;}start();});
$('clear-done').addEventListener('click',()=>{for(const item of state.queue)if(item.status==='done'&&item.preview)URL.revokeObjectURL(item.preview);state.queue=state.queue.filter(x=>x.status!=='done');renderQueue();});
window.addEventListener('beforeunload',event=>{if(state.pumping || state.queue.some(x=>x.status==='queued')){event.preventDefault();event.returnValue='';}});

async function openEditor(id){
  try{const {image}=await api('/v1/images/'+id);state.editing=image;state.images.set(id,image);$('edit-preview').src='/media/'+encodeURIComponent(id);$('edit-title').value=image.title;$('edit-tags').value=image.tags.join(', ');$('edit-rating').value=image.rating;$('edit-source').value=image.source_url || '';$('edit-meta').textContent=`${bytes(image.size)} · ${image.mime_type.replace('image/','').toUpperCase()} · ${new Date(image.created_at).toLocaleDateString('vi-VN')}`;$('editor').showModal();}
  catch(error){toast(error.message);}
}
$('editor-close').addEventListener('click',()=>$('editor').close());
$('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();const image=state.editing;if(!image)return;$('save-image').disabled=true;
  try{const {image:updated}=await api('/v1/images/'+image.id,{method:'PATCH',body:JSON.stringify({title:$('edit-title').value,tags:tagList($('edit-tags').value),rating:$('edit-rating').value,source_url:$('edit-source').value.trim() || null})});state.images.set(image.id,updated);state.editing=updated;renderGallery();$('editor').close();toast('Đã lưu thay đổi.');refreshStats();}
  catch(error){toast(error.message);}finally{$('save-image').disabled=false;}
});
$('analyze-image').addEventListener('click',async()=>{
  if(!state.editing)return;$('analyze-image').disabled=true;
  try{await api('/v1/images/'+state.editing.id+'/analyze',{method:'POST'});$('editor').close();toast('Ảnh đã vào hàng đợi AI.');refreshStats(true);}catch(error){toast(error.message);}finally{$('analyze-image').disabled=false;}
});
$('copy-link').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.origin+'/media/'+state.editing.id);toast('Đã sao chép link ảnh.');}catch{toast('Trình duyệt chưa cho phép sao chép. Bạn có thể mở ảnh để lấy link.');}});
$('delete-image').addEventListener('click',()=>$('confirm-delete').showModal());$('cancel-delete').addEventListener('click',()=>$('confirm-delete').close());
$('confirm-delete-button').addEventListener('click',async()=>{
  if(!state.editing)return;$('confirm-delete-button').disabled=true;
  try{await api('/v1/images/'+state.editing.id,{method:'DELETE'});state.images.delete(state.editing.id);$('confirm-delete').close();$('editor').close();state.editing=null;renderGallery();refreshStats();toast('Đã xóa ảnh.');}
  catch(error){toast(error.message);}finally{$('confirm-delete-button').disabled=false;}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden && state.authenticated)refreshStats(true);});
api('/v1/session').then(enter).catch(()=>showLogin());
