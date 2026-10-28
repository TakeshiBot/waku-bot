const $=id=>document.getElementById(id);
const state={authenticated:false,maxMb:10,images:new Map(),cursor:null,query:'',rating:'',queue:[],pumping:false,paused:true,editing:null,listVersion:0,statsBusy:false,
  selected:new Set(),allSelected:false,selectionBusy:false,bulkBusy:false,deleteTargets:[],loadedLimit:48,uploadIds:new Set(),starting:false,aiBatchId:null,aiProgress:null,aiPending:0,aiEnqueueing:false};
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
  $('use-ai').checked=Boolean(session.ai_enabled);
  try{state.aiBatchId=sessionStorage.getItem('waku-ai-batch');}catch{}
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
  showLogin();state.images.clear();state.selected.clear();setAiBatch(null);state.queue.forEach(item=>{if(item.preview)URL.revokeObjectURL(item.preview);});state.queue=[];renderQueue();
});

async function refreshStats(updateGallery=false){
  if(state.statsBusy || !state.authenticated)return;state.statsBusy=true;
  try{
    const stats=await api('/v1/stats');$('stat-total').textContent=number(stats.images.total);$('nav-count').textContent=number(stats.images.total);$('stat-size').textContent=bytes(stats.images.bytes);
    const pending=Number(stats.ai_pending)+Number(stats.uploads_pending);$('stat-pending').textContent=number(pending);$('stat-pending-note').textContent=pending?`${number(stats.ai_pending)} AI · ${number(stats.uploads_pending)} upload`:'Mọi thứ đã sẵn sàng';
    state.aiPending=Number(stats.ai_pending);await refreshAiProgress();
    if(pending)api('/v1/jobs/drain',{method:'POST'}).catch(()=>{});
    if(updateGallery)await loadImages(false);
  }catch(error){if(state.authenticated)$('list-status').textContent=error.message;}
  finally{state.statsBusy=false;}
}
async function loadImages(reset=false,more=false){
  const version=++state.listVersion,wanted=reset?48:state.loadedLimit+(more?48:0),base=listParameters();
  if(reset){state.images.clear();$('list-status').textContent='Đang mở thư viện…';renderGallery();}
  try{
    const images=new Map();let cursor=null;
    do{
      const params=new URLSearchParams(base);params.set('limit',String(Math.min(100,wanted-images.size)));if(cursor)params.set('before',cursor);
      const data=await api('/v1/images?'+params);if(version!==state.listVersion)return;
      for(const image of data.images)images.set(image.id,image);cursor=data.next;
    }while(cursor && images.size<wanted);
    state.images=images;state.cursor=cursor;state.loadedLimit=wanted;$('list-status').textContent='';renderGallery();
  }catch(error){if(version===state.listVersion)$('list-status').textContent=error.message;}
}
function listParameters(){
  const params=new URLSearchParams();
  if(state.rating==='unknown')params.set('view','pending');
  else if(state.rating){params.set('rating',state.rating);params.set('view','classified');}
  if(state.query)params.set('q',state.query);return params;
}
function changeFilter(rating,clearSearch=false){
  state.rating=rating;state.selected.clear();state.allSelected=false;
  if(clearSearch){state.query='';$('search').value='';clearTimeout(filterTimer);}
  document.querySelectorAll('[data-rating]').forEach(button=>button.classList.toggle('active',button.dataset.rating===rating));
  loadImages(true);renderSelection();renderAiProgress();
}
function renderGallery(){
  const gallery=$('gallery');gallery.replaceChildren();
  const incoming=state.rating==='unknown'&&!state.query?state.queue.filter(item=>state.uploadIds.has(item.id) && ['queued','uploading'].includes(item.status)):[];
  const count=state.images.size+incoming.length;$('empty').hidden=count>0;
  if(!count){$('empty').querySelector('h3').textContent=state.query || state.rating?'Chưa có ảnh phù hợp.':'Một khởi đầu mới.';$('empty').querySelector('p').textContent=state.query || state.rating?'Thử từ khóa hoặc bộ lọc khác nhé.':'Thêm những tấm ảnh đầu tiên. Phần sắp xếp, cứ để Waku lo.';}
  $('visible-count').textContent=count?number(count):'';$('load-more').hidden=!state.cursor;
  for(const item of incoming){
    const card=element('article','card pending-upload');card.dataset.uploadId=item.id;const thumb=element('div','thumb');
    if(item.preview){const img=element('img');img.src=item.preview;img.alt=item.name;img.loading='lazy';thumb.append(img);}
    const label=element('span','rating-label',item.status==='uploading'?(item.progress===100?'Đang lưu…':`Đang tải ${item.progress}%`):'Chờ tải');thumb.append(label);
    card.append(thumb,element('div','card-title',item.name),element('div','card-tags','Chưa lưu lên máy chủ'));gallery.append(card);
  }
  for(const image of [...state.images.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))){
    const card=element('article','card '+image.rating+(state.selected.has(image.id)?' selected':'')),thumb=element('button','thumb');card.dataset.id=image.id;thumb.type='button';thumb.setAttribute('aria-label','Xem chi tiết '+(image.title || 'ảnh'));
    const checkLabel=element('label','image-select'),check=element('input');check.type='checkbox';check.checked=state.selected.has(image.id);check.disabled=state.bulkBusy||state.selectionBusy;check.setAttribute('aria-label','Chọn '+(image.title || 'ảnh'));
    check.addEventListener('change',()=>{if(check.checked)state.selected.add(image.id);else state.selected.delete(image.id);state.allSelected=false;card.classList.toggle('selected',check.checked);renderSelection();});checkLabel.append(check);card.append(checkLabel);
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
  renderSelection();
}
$('search').addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{state.query=$('search').value.trim();state.selected.clear();state.allSelected=false;loadImages(true);},300);});
document.querySelectorAll('[data-rating]').forEach(button=>button.addEventListener('click',()=>changeFilter(button.dataset.rating)));
$('reveal-r18').addEventListener('change',()=>{$('gallery').classList.toggle('reveal',$('reveal-r18').checked);});
$('load-more').addEventListener('click',()=>loadImages(false,true));$('refresh').addEventListener('click',()=>{loadImages(true);refreshStats();});
function setView(view){
  document.body.dataset.view=view;
  $('nav-library').classList.toggle('selected',view==='library');$('nav-upload').classList.toggle('selected',view==='upload');
  $('nav-library').setAttribute('aria-label','Kho ảnh');$('nav-upload').setAttribute('aria-label','Tải ảnh lên');
}
function focusUpload(){setView('upload');$('upload-panel').scrollTop=0;$('choose-files').focus({preventScroll:true});}
for(const id of ['nav-upload','empty-upload'])$(id).addEventListener('click',focusUpload);
$('nav-library').addEventListener('click',()=>{setView('library');$('library-results').scrollTop=0;});

function renderSelection(){
  const count=state.selected.size,busy=state.bulkBusy||state.selectionBusy;
  $('selection-count').textContent=state.bulkBusy?state.bulkLabel || 'Đang xử lý…':`${number(count)} đã chọn`;
  $('select-all').checked=state.selectionBusy||state.allSelected&&count>0;$('select-all').indeterminate=!state.selectionBusy&&!state.allSelected&&count>0;
  $('select-all').disabled=busy;$('clear-selection').disabled=busy||!count;$('bulk-delete').disabled=busy||!count;
  $('bulk-ai').hidden=state.rating!=='unknown';$('bulk-ai').disabled=busy||!count;
  document.querySelectorAll('.image-select input').forEach(input=>input.disabled=busy);
}
$('select-all').addEventListener('change',async()=>{
  if(!$('select-all').checked){state.selected.clear();state.allSelected=false;renderGallery();return;}
  const base=listParameters(),scope=base.toString();state.selectionBusy=true;renderSelection();
  try{
    const ids=new Set();let cursor=null;
    do{const params=new URLSearchParams(base);params.set('limit','100');if(cursor)params.set('before',cursor);const data=await api('/v1/images?'+params);for(const image of data.images)ids.add(image.id);cursor=data.next;}while(cursor);
    if(scope===listParameters().toString()){state.selected=ids;state.allSelected=true;}
  }catch(error){toast(error.message);}finally{state.selectionBusy=false;renderGallery();}
});
$('clear-selection').addEventListener('click',()=>{state.selected.clear();state.allSelected=false;renderGallery();});
function setAiBatch(id){
  state.aiBatchId=id;state.aiProgress=null;
  try{if(id)sessionStorage.setItem('waku-ai-batch',id);else sessionStorage.removeItem('waku-ai-batch');}catch{}
}
async function refreshAiProgress(){
  const id=state.aiBatchId;
  if(id)try{const progress=await api('/v1/analysis-batches/'+id);if(state.aiBatchId===id)state.aiProgress=progress;}
  catch(error){if(error.status===404)setAiBatch(null);}
  renderAiProgress();
}
function renderAiProgress(){
  const p=state.aiProgress,active=state.aiPending>0||state.aiEnqueueing||state.pumping&&Boolean(state.aiBatchId);
  $('ai-progress').hidden=state.rating!=='unknown'||!active&&!p?.total;
  $('dismiss-ai').disabled=active||Boolean(p?.pending||p?.running);
  const progress=$('ai-progress-bar');
  if(p?.total){
    const finished=p.done+p.failed+p.skipped;progress.max=p.total;progress.value=finished;
    $('ai-summary').textContent=`AI gắn tag · đã xử lý ${finished}/${p.total}`;
    $('ai-detail').textContent=`${p.done} thành công · ${p.running} đang chạy · ${p.pending} chờ${p.failed?' · '+p.failed+' lỗi':''}${p.skipped?' · '+p.skipped+' bỏ qua':''}`;
    $('ai-progress').classList.toggle('has-errors',p.failed>0);
  }else{
    progress.removeAttribute('value');progress.max=1;$('ai-progress').classList.remove('has-errors');
    $('ai-summary').textContent=state.aiEnqueueing?'Đang đưa ảnh vào hàng đợi AI…':'AI đang gắn tag…';
    $('ai-detail').textContent=state.aiPending?`${state.aiPending} ảnh đang chờ hoặc xử lý`:'Đợi ảnh tải lên để bắt đầu.';
  }
}
$('dismiss-ai').addEventListener('click',()=>{setAiBatch(null);renderAiProgress();});
async function queueAi(ids){
  if(!ids.length)return;state.bulkBusy=true;state.aiEnqueueing=true;state.bulkLabel='Đang xếp hàng AI…';renderSelection();
  try{
    const {id}=await api('/v1/analysis-batches',{method:'POST',body:'{}'});setAiBatch(id);changeFilter('unknown',true);setView('library');
    let added=0;for(let i=0;i<ids.length;i+=100){const data=await api('/v1/analysis-batches/'+id,{method:'POST',body:JSON.stringify({ids:ids.slice(i,i+100)})});added+=data.added;}
    toast(`Đã xếp hàng AI cho ${added} ảnh.`);await refreshAiProgress();await loadImages(false);refreshStats();
  }catch(error){toast(error.message);}finally{state.bulkBusy=false;state.aiEnqueueing=false;state.bulkLabel='';renderSelection();renderAiProgress();}
}
$('bulk-ai').addEventListener('click',()=>queueAi([...state.selected]));
function confirmDelete(ids){
  if(!ids.length)return;state.deleteTargets=[...ids];$('delete-title').textContent=ids.length===1?'Xóa ảnh này?':`Xóa ${ids.length} ảnh đã chọn?`;
  $('delete-description').textContent=`${ids.length} ảnh cùng tag sẽ được xóa khỏi thư viện. Thao tác này không thể hoàn tác.`;$('confirm-delete').showModal();
}
$('bulk-delete').addEventListener('click',()=>confirmDelete([...state.selected]));

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
    const description=item.status==='done'?(item.duplicate?'Đã có trong kho':'Đã lưu ảnh') :item.status==='error'?item.error:item.status==='uploading'?(item.progress===100?'Đang lưu trên máy chủ…':`Đang tải… ${item.progress}%`):(item.file?bytes(item.file.size)+' · Chờ tải':'Link ảnh · Chờ tải');
    text.append(element('small','',description));
    if(item.status==='uploading'){const progress=element('progress');progress.max=100;progress.value=item.progress;text.append(progress);}
    const remove=element('button','',item.status==='done'?'✓':'×');remove.type='button';remove.setAttribute('aria-label','Bỏ '+item.name+' khỏi hàng đợi');remove.disabled=item.status==='uploading';remove.addEventListener('click',()=>{state.queue=state.queue.filter(x=>x!==item);if(item.preview)URL.revokeObjectURL(item.preview);renderQueue();});row.append(text,remove);list.append(row);
  }
  const batch=state.uploadIds.size?state.queue.filter(item=>state.uploadIds.has(item.id)):state.queue;
  const saved=batch.filter(item=>item.status==='done').length,failed=batch.filter(item=>item.status==='error').length;
  $('queue-progress').hidden=!batch.length;$('queue-summary').textContent=`${saved}/${batch.length} đã lưu${failed?' · '+failed+' lỗi':''}${active?' · '+active+' đang tải':''}`;$('batch-progress').max=Math.max(1,batch.length);$('batch-progress').value=saved;
  $('start-upload').disabled=!pending||state.starting;$('start-upload').textContent=state.starting?'Đang chuẩn bị…':state.pumping&&!state.paused?'Đang tải lên…':state.paused && active?'Tiếp tục tải lên':`Tải ${pending || ''} ảnh lên`.replace('  ',' ');
  $('pause-upload').hidden=!state.pumping || state.paused;$('retry-errors').hidden=!errors;
  for(const id of ['use-ai','upload-rating','upload-tags','upload-source'])$(id).disabled=state.pumping&&!state.paused;
}
function fileUpload(item){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/v1/images');xhr.responseType='json';xhr.timeout=90000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable){item.progress=Math.round(event.loaded/event.total*100);const row=$('queue-list').querySelector(`[data-id="${item.id}"]`);if(row){row.querySelector('small').textContent=item.progress===100?'Đang lưu trên máy chủ…':`Đang gửi… ${item.progress}%`;row.querySelector('progress').value=item.progress;}const preview=$('gallery').querySelector(`[data-upload-id="${item.id}"] .rating-label`);if(preview)preview.textContent=item.progress===100?'Đang lưu…':`Đang tải ${item.progress}%`;}};
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else{const error=new Error(xhr.response?.error?.message || 'Tải ảnh chưa thành công.');error.status=xhr.status;error.retryAfter=Number(xhr.getResponseHeader('retry-after')) || 2;if(xhr.status===401)showLogin('Phiên đã hết hạn. Vui lòng đăng nhập lại.');reject(error);}};
    xhr.onerror=()=>reject(new Error('Mất kết nối. Bấm thử lại khi mạng ổn định.'));xhr.ontimeout=()=>reject(new Error('Quá thời gian chờ. Bạn có thể thử lại file này.'));
    const form=new FormData();form.set('file',item.file,item.file.name);form.set('tags',item.options.tags.join(','));form.set('rating',item.options.rating);form.set('manual',String(item.options.manual));if(item.options.batch_id)form.set('batch_id',item.options.batch_id);if(item.options.source_url)form.set('source_url',item.options.source_url);xhr.send(form);
  });
}
async function worker(){
  while(!state.paused && state.authenticated){
    const item=state.queue.find(x=>x.status==='queued');if(!item)return;item.options??=structuredClone(state.activeOptions || options());state.uploadIds.add(item.id);item.status='uploading';item.progress=0;renderQueue();renderGallery();
    try{
      let data;for(let attempt=0;attempt<8;attempt++){
        try{data=item.file?await fileUpload(item):await api('/v1/images',{method:'POST',body:JSON.stringify({url:item.url,...item.options,source_url:item.options.source_url || item.url})});break;}
        catch(error){if(error.status!==429 || attempt===7)throw error;await sleep(Math.min(10000,Math.max(2,error.retryAfter || 2)*1000));}
      }
      item.status='done';item.progress=100;item.duplicate=data.duplicate;item.imageId=data.image.id;
      if(state.rating==='' || state.rating==='unknown'&&(data.image.rating==='unknown'||['pending','running'].includes(data.image.ai_status)) || state.rating===data.image.rating)state.images.set(data.image.id,data.image);
    }catch(error){item.status='error';item.error=error.message || 'Tải ảnh chưa thành công.';}
    renderQueue();renderGallery();refreshStats(true);
  }
}
async function start(){
  if(!state.authenticated||state.starting)return;
  const selected=options();if(selected.tags.length>30){toast('Tối đa 30 tag chung.');return;}
  if(!$('upload-source').checkValidity()){toast('Link nguồn chưa hợp lệ.');return;}
  const resume=state.queue.some(item=>state.uploadIds.has(item.id)&&['queued','uploading'].includes(item.status));
  if(!resume)state.uploadIds=new Set(state.queue.filter(item=>item.status==='queued').map(item=>item.id));
  state.starting=true;changeFilter('unknown',true);setView('library');renderQueue();let ownsWorkers=false;
  try{
    if(!selected.manual){
      if(!resume || !state.aiBatchId){const {id}=await api('/v1/analysis-batches',{method:'POST',body:'{}'});setAiBatch(id);}
      selected.batch_id=state.aiBatchId;
    }else if(!resume)setAiBatch(null);
    state.activeOptions=selected;for(const item of state.queue)if(item.status==='queued')item.options=structuredClone(selected);
    state.paused=false;state.starting=false;if(state.pumping){renderQueue();return;}state.pumping=true;ownsWorkers=true;renderQueue();renderAiProgress();
    await Promise.all([worker(),worker()]);
  }catch(error){toast(error.message);}finally{state.starting=false;if(ownsWorkers)state.pumping=false;renderQueue();renderGallery();refreshStats(true);}
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
  try{const {image:updated}=await api('/v1/images/'+image.id,{method:'PATCH',body:JSON.stringify({title:$('edit-title').value,tags:tagList($('edit-tags').value),rating:$('edit-rating').value,source_url:$('edit-source').value.trim() || null})});state.images.set(image.id,updated);state.editing=updated;state.selected.delete(image.id);renderGallery();$('editor').close();toast('Đã lưu thay đổi.');loadImages(false);refreshStats();}
  catch(error){toast(error.message);}finally{$('save-image').disabled=false;}
});
$('analyze-image').addEventListener('click',async()=>{
  if(!state.editing)return;const id=state.editing.id;$('editor').close();await queueAi([id]);
});
$('copy-link').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.origin+'/media/'+state.editing.id);toast('Đã sao chép link ảnh.');}catch{toast('Trình duyệt chưa cho phép sao chép. Bạn có thể mở ảnh để lấy link.');}});
$('delete-image').addEventListener('click',()=>{if(state.editing)confirmDelete([state.editing.id]);});$('cancel-delete').addEventListener('click',()=>$('confirm-delete').close());
$('confirm-delete-button').addEventListener('click',async()=>{
  const ids=[...state.deleteTargets];if(!ids.length||state.bulkBusy)return;
  state.bulkBusy=true;$('confirm-delete-button').disabled=true;let next=0,done=0,failed=0;
  const remove=async()=>{while(next<ids.length){const id=ids[next++];try{
    try{await api('/v1/images/'+id,{method:'DELETE'});}catch(error){if(error.status!==404)throw error;}
    state.images.delete(id);state.selected.delete(id);done++;
  }catch{failed++;}state.bulkLabel=`Đã xóa ${done}/${ids.length}${failed?' · '+failed+' lỗi':''}`;$('confirm-delete-button').textContent=`Đang xóa ${done}/${ids.length}`;renderSelection();}};
  try{await Promise.all([remove(),remove()]);$('confirm-delete').close();if(state.editing&&ids.includes(state.editing.id)){$('editor').close();state.editing=null;}state.allSelected=false;await loadImages(false);refreshStats();toast(`Đã xóa ${done} ảnh${failed?'; '+failed+' ảnh lỗi, hãy thử lại.':'.'}`);}
  finally{state.deleteTargets=[];state.bulkBusy=false;state.bulkLabel='';$('confirm-delete-button').disabled=false;$('confirm-delete-button').textContent='Xóa ảnh';renderGallery();}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden && state.authenticated)refreshStats(true);});
api('/v1/session').then(enter).catch(()=>showLogin());
