import {setupProviders} from './providers.js?v=16';
import {setupKeys} from './keys.js?v=16';
const $=id=>document.getElementById(id);
const state={authenticated:false,maxMb:10,images:new Map(),cursor:null,query:'',rating:'',queue:[],pumping:false,paused:true,editing:null,listVersion:0,statsBusy:false,
  selected:new Set(),allSelected:false,selectionBusy:false,bulkBusy:false,deleteTargets:[],editTargets:[],loadedLimit:0,uploadIds:new Set(),starting:false,aiBatchId:null,aiProgress:null,aiPending:0,aiEnqueueing:false,
  revision:null,lastStats:0,galleryDirty:false,gallerySignature:'',filterTags:new Set(),filterTypes:new Set(),tagOptions:[],tagPage:0,tagsLoaded:0,checking:0,aiAvailable:false,aiQueue:null,aiControlBusy:false,aiRunActive:false,aiResult:null,aiBaseline:null};
const number=n=>new Intl.NumberFormat('vi-VN').format(n || 0);
const bytes=n=>n>=1073741824?(n/1073741824).toFixed(1)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';
const tagList=value=>value.trim().split(/[,\n]|\s+(?=#)/).map(x=>x.trim().replace(/^#/, '')).filter(Boolean);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let toastTimer,filterTimer,pollTimer;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4000);}
function element(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;}
function isClassified(image){return image.classified??Boolean(image.title?.trim()&&image.tags?.some(tag=>tag.trim())&&['safe','r18'].includes(image.rating)&&['anime','real'].includes(image.image_type)&&!['pending','running'].includes(image.ai_status));}
function analysisError(code){
  const messages={provider_content_filtered:'Model từ chối phân tích ảnh do bộ lọc nội dung của nhà cung cấp. Đây không phải lỗi file. Bạn có thể gắn tag thủ công hoặc chọn một model vision khác.',provider_timeout:'Model không trả xong kết quả trong 10 giây. Ảnh không tự chạy lại.',worker_timeout:'Lượt xử lý bị gián đoạn. Ảnh không tự chạy lại.',invalid_response:'API trả kết quả không đúng định dạng gắn tag.',incomplete_response:'Model trả kết quả chưa đầy đủ.',provider_unavailable:'Không kết nối được API vision.',image_missing:'Không đọc được ảnh đã lưu.',key_decryption_failed:'Cần nhập lại key của API vision.'};
  return messages[code]||(/^provider_http_\d{3}$/.test(code||'')?`API vision trả lỗi HTTP ${code.slice(-3)}. Kiểm tra API, key và model đang chọn.`:'Chưa phân tích được ảnh. Kiểm tra API/model rồi chủ động chạy lại.');
}
function showLogin(message=''){
  state.authenticated=false;state.paused=true;clearTimeout(pollTimer);$('app').hidden=true;$('login').hidden=false;$('login-error').textContent=message;
  $('editor').close();$('confirm-delete').close();$('bulk-edit').close();$('api-settings').close();
  $('service-keys').close();
}
async function api(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{...(options.body && !(options.body instanceof FormData)?{'content-type':'application/json'}:{}),...options.headers},signal:options.signal || AbortSignal.timeout(30000)});
  let data;try{data=await response.json();}catch{throw new Error('Máy chủ chưa phản hồi đúng. Hãy thử lại sau.');}
  if(!response.ok){if(response.status===401 && state.authenticated)showLogin('Phiên đăng nhập đã hết hạn. Vui lòng nhập lại key.');const error=new Error(data.error?.message || 'Chưa thực hiện được thao tác.');error.status=response.status;error.retryAfter=Number(response.headers.get('retry-after')) || 2;throw error;}
  return data;
}
async function enter(session){
  session ||= await api('/v1/session');state.authenticated=true;state.maxMb=session.max_image_mb || 10;
  state.lastStats=0;state.aiQueue=null;state.aiProgress=null;state.aiRunActive=false;state.aiResult=null;state.aiBaseline=null;
  $('ai-progress').hidden=true;$('ai-result').hidden=true;
  state.aiAvailable=Boolean(session.ai_enabled);$('use-ai').checked=state.aiAvailable;$('use-ai').disabled=!state.aiAvailable;
  try{state.aiBatchId=sessionStorage.getItem('waku-ai-batch');}catch{}
  $('api-key').value='';$('login').hidden=true;$('app').hidden=false;$('upload-limits').textContent=`JPG, PNG, WebP, GIF, AVIF · tối đa ${state.maxMb} MB/ảnh`;
  await Promise.all([refreshStats(),loadImages(true)]);
  schedulePoll();
}
function schedulePoll(){
  clearTimeout(pollTimer);if(!state.authenticated||document.hidden)return;
  const q=state.aiQueue,active=state.pumping||state.starting||state.aiEnqueueing||Boolean(q?.running)||Boolean(q?.control!=='paused'&&q?.pending>q?.paused);
  pollTimer=setTimeout(()=>refreshStats(true),active?8000:60000);
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

async function refreshStats(updateGallery=false,force=false){
  if(state.statsBusy || !state.authenticated)return;state.statsBusy=true;
  try{
    if(!force&&Date.now()-state.lastStats<2000)return;
    const batchId=state.aiBatchId,stats=await api('/v1/state'+(batchId?'?batch='+encodeURIComponent(batchId):''));state.lastStats=Date.now();
    $('stat-total').textContent=number(stats.images.total);$('nav-count').textContent=number(stats.images.total);$('stat-size').textContent=bytes(stats.images.bytes);
    for(const count of document.querySelectorAll('[data-tab-count]'))count.textContent=`(${number(stats.tabs?.[count.dataset.tabCount])})`;
    const pending=Number(stats.ai_pending)+Number(stats.uploads_pending);$('stat-pending').textContent=number(pending);$('stat-pending-note').textContent=pending?`${number(stats.ai_pending)} AI · ${number(stats.uploads_pending)} upload`:'Mọi thứ đã sẵn sàng';
    state.aiPending=Number(stats.ai_pending);state.aiQueue=stats.ai_queue;if(batchId&&batchId===state.aiBatchId){if(stats.batch)state.aiProgress=stats.batch;else setAiBatch(null);}renderAiProgress();
    const changed=state.revision!==null&&state.revision!==stats.revision;state.revision=stats.revision;state.galleryDirty ||= changed;
    if(updateGallery&&state.galleryDirty){await loadImages(false);state.galleryDirty=false;}
  }catch(error){if(state.authenticated)$('list-status').textContent=error.message;}
  finally{state.statsBusy=false;schedulePoll();}
}
async function loadImages(reset=false,more=false){
  const pageSize=5*(parseInt(getComputedStyle($('gallery')).getPropertyValue('--gallery-columns'),10)||6);
  const version=++state.listVersion,wanted=reset?pageSize:(state.loadedLimit||pageSize)+(more?pageSize:0),base=listParameters();
  if(reset)$('list-status').textContent='Đang mở thư viện…';
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
  if(state.query)params.set('q',state.query);
  if(state.filterTags.size)params.set('tags',[...state.filterTags].join(','));
  if(state.filterTypes.size)params.set('types',[...state.filterTypes].join(','));return params;
}
function changeFilter(rating,clearSearch=false){
  state.rating=rating;state.selected.clear();state.allSelected=false;
  if(clearSearch){state.query='';$('search').value='';clearTimeout(filterTimer);}
  document.querySelectorAll('[data-rating]').forEach(button=>{const active=button.dataset.rating===rating;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  setView('library');
  loadImages(true);renderSelection();renderAiProgress();
}
function renderGallery(){
  const signature=JSON.stringify([[...state.images.values()],[...state.selected],state.bulkBusy,state.selectionBusy,state.cursor,state.rating,state.query,[...state.uploadIds],state.queue.map(x=>[x.id,x.status]),[...state.filterTags],[...state.filterTypes]]);
  if(signature===state.gallerySignature){renderSelection();return;}state.gallerySignature=signature;
  const gallery=$('gallery'),previous=new Map([...gallery.querySelectorAll('.card[data-id]')].map(card=>[card.dataset.id,card]));gallery.replaceChildren();
  const incoming=state.rating==='unknown'&&!state.query&&!state.filterTags.size&&!state.filterTypes.size?state.queue.filter(item=>state.uploadIds.has(item.id) && ['queued','uploading'].includes(item.status)):[];
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
    const content=JSON.stringify([image,state.selected.has(image.id),state.bulkBusy,state.selectionBusy]),old=previous.get(image.id);
    if(old?.dataset.content===content){gallery.append(old);continue;}
    const card=element('article','card '+image.rating+(state.selected.has(image.id)?' selected':'')),thumb=element('button','thumb');card.dataset.id=image.id;thumb.type='button';thumb.setAttribute('aria-label','Xem chi tiết '+(image.title || 'ảnh'));
    card.dataset.content=content;
    const checkLabel=element('label','image-select'),check=element('input');check.type='checkbox';check.checked=state.selected.has(image.id);check.disabled=state.bulkBusy||state.selectionBusy;check.setAttribute('aria-label','Chọn '+(image.title || 'ảnh'));
    check.addEventListener('change',()=>{if(check.checked)state.selected.add(image.id);else state.selected.delete(image.id);state.allSelected=false;card.classList.toggle('selected',check.checked);renderSelection();});checkLabel.append(check);card.append(checkLabel);
    const img=old?.querySelector('.thumb img') || element('img');if(!img.src)img.src=new URL('/media/'+encodeURIComponent(image.id),location.origin).href;img.alt=image.title || 'Ảnh trong thư viện';img.loading='lazy';img.decoding='async';thumb.append(img);
    const classified=isClassified(image);thumb.append(element('span','rating-label '+(classified?image.rating:'unknown'),classified?(image.rating==='safe'?'An toàn':'R18'):'Chờ phân loại'));
    const copy=element('span','copy-mini');copy.append(icon('link'));copy.title='Mở chi tiết để sao chép link';thumb.append(copy);thumb.addEventListener('click',()=>openEditor(image.id));
    const title=element('button','card-title',image.title || (['pending','running'].includes(image.ai_status)?'AI đang đặt tên…':'Ảnh chưa có tên'));title.type='button';title.title=image.title || '';title.addEventListener('click',()=>openEditor(image.id));
    const tags=element('div','card-tags');for(const tag of image.tags.slice(0,2))tags.append(element('span','',tag));
    if(image.tags.length>2)tags.append(element('span','',`+${image.tags.length-2}`));
    if(['pending','running'].includes(image.ai_status))tags.append(element('span','ai-state busy','✦ AI'));
    else if(image.ai_status==='failed'){const error=element('span','ai-state',image.ai_error==='provider_content_filtered'?'AI từ chối':'AI lỗi');error.title=analysisError(image.ai_error);tags.append(error);}
    card.append(thumb,title,tags);gallery.append(card);
  }
  renderSelection();
}
$('search').addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{state.query=$('search').value.trim();state.selected.clear();state.allSelected=false;loadImages(true);},300);});
document.querySelectorAll('[data-rating]').forEach(button=>button.addEventListener('click',()=>changeFilter(button.dataset.rating)));
$('reveal-r18').addEventListener('click',()=>{const reveal=$('gallery').classList.toggle('reveal');$('reveal-r18').setAttribute('aria-pressed',String(reveal));$('reveal-r18').textContent=reveal?'Ẩn R18':'Hiện R18';});
$('load-more').addEventListener('click',()=>loadImages(false,true));$('refresh').addEventListener('click',()=>{loadImages(true);refreshStats(false,true);});
function applyFilters(){
  state.selected.clear();state.allSelected=false;$('filter-count').textContent=state.filterTags.size+state.filterTypes.size || '';loadImages(true);
}
async function loadTags(){
  const query=$('filter-tag-search').value.trim(),data=await api('/v1/filters?q='+encodeURIComponent(query));if(query!==$('filter-tag-search').value.trim())return;
  state.tagsLoaded=Date.now();state.tagOptions=data.tags;state.tagPage=0;renderTagOptions();
}
function renderTagOptions(){
  const pages=Math.max(1,Math.ceil(state.tagOptions.length/5));state.tagPage=Math.min(state.tagPage,pages-1);
  const list=$('filter-tags');list.replaceChildren();
  for(const item of state.tagOptions.slice(state.tagPage*5,state.tagPage*5+5)){const label=element('label'),check=element('input'),name=element('span','',item.tag);name.title=item.tag;check.type='checkbox';check.value=item.tag;check.checked=state.filterTags.has(item.tag);
    check.addEventListener('change',()=>{if(check.checked&&state.filterTags.size>=30){check.checked=false;toast('Chọn tối đa 30 tag.');return;}if(check.checked)state.filterTags.add(item.tag);else state.filterTags.delete(item.tag);applyFilters();});
    label.append(check,name,element('small','',number(item.count)));list.append(label);
  }
  if(!state.tagOptions.length)list.append(element('p','', 'Chưa có tag phù hợp.'));
  $('filter-tag-page').textContent=`${state.tagPage+1}/${pages}`;$('filter-tags-prev').disabled=state.tagPage===0;$('filter-tags-next').disabled=state.tagPage===pages-1;
}
$('filter-tags-prev').addEventListener('click',()=>{state.tagPage=Math.max(0,state.tagPage-1);renderTagOptions();});
$('filter-tags-next').addEventListener('click',()=>{state.tagPage++;renderTagOptions();});
let tagSearchTimer,filterCloseTimer,tagsBusy=false;
function openFilter(open){
  clearTimeout(filterCloseTimer);$('filter-panel').hidden=!open;$('filter-toggle').setAttribute('aria-expanded',String(open));
  if(open){const box=$('filter-toggle').closest('.bulk-tools').getBoundingClientRect(),height=$('filter-panel').getBoundingClientRect().height;$('filter-panel').classList.toggle('above',box.bottom+height>innerHeight-12&&box.top>height+12);}
  if(open&&!tagsBusy&&Date.now()-state.tagsLoaded>60000){tagsBusy=true;loadTags().catch(error=>toast(error.message)).finally(()=>tagsBusy=false);}
}
const filterAnchor=$('filter-toggle').closest('.filter-anchor');
filterAnchor.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse')openFilter(true);});
filterAnchor.addEventListener('pointerleave',event=>{if(event.pointerType==='mouse')filterCloseTimer=setTimeout(()=>openFilter(false),120);});
filterAnchor.addEventListener('focusout',event=>{if(!filterAnchor.contains(event.relatedTarget)&&!filterAnchor.matches(':hover'))openFilter(false);});
filterAnchor.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();openFilter(false);$('filter-toggle').focus();}});
$('filter-toggle').addEventListener('click',event=>openFilter(event.detail>0&&matchMedia('(hover:hover)').matches?true:$('filter-panel').hidden));
$('filter-tag-search').addEventListener('input',()=>{clearTimeout(tagSearchTimer);tagSearchTimer=setTimeout(()=>loadTags().catch(error=>toast(error.message)),400);});
document.querySelectorAll('[name="filter-type"]').forEach(input=>input.addEventListener('change',()=>{if(input.checked)state.filterTypes.add(input.value);else state.filterTypes.delete(input.value);applyFilters();}));
$('clear-filters').addEventListener('click',()=>{state.filterTags.clear();state.filterTypes.clear();document.querySelectorAll('#filter-panel input[type=checkbox]').forEach(input=>input.checked=false);applyFilters();});
document.addEventListener('click',event=>{if(!event.target.closest('.filter-anchor'))openFilter(false);});
function setView(view){
  document.body.dataset.view=view;
  $('nav-library').classList.toggle('selected',view==='library');$('nav-library').setAttribute('aria-label','Kho ảnh');
}
function focusUpload(){setView('upload');$('upload-panel').scrollTop=0;$('choose-files').focus({preventScroll:true});}
for(const id of ['show-upload','empty-upload'])$(id).addEventListener('click',focusUpload);
$('nav-library').addEventListener('click',()=>{setView('library');$('library-results').scrollTop=0;});

function renderSelection(){
  const count=state.selected.size,busy=state.bulkBusy||state.selectionBusy;
  $('selection-count').textContent=state.bulkBusy?state.bulkLabel || 'Đang xử lý…':`${number(count)} đã chọn`;
  $('select-all').checked=state.selectionBusy||state.allSelected&&count>0;$('select-all').indeterminate=!state.selectionBusy&&!state.allSelected&&count>0;
  $('select-all').disabled=busy;$('clear-selection').disabled=busy||!count;$('bulk-delete').disabled=busy||!count;$('bulk-edit-button').disabled=busy||!count;
  $('bulk-ai').hidden=state.rating!=='unknown';$('bulk-ai').disabled=busy||!count||!state.aiAvailable;
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
  state.aiBatchId=id;state.aiProgress=null;state.aiRunActive=Boolean(id);state.aiResult=null;state.aiBaseline=null;
  try{if(id)sessionStorage.setItem('waku-ai-batch',id);else sessionStorage.removeItem('waku-ai-batch');}catch{}
}
async function refreshAiProgress(){
  await refreshStats(true,true);
}
function renderAiProgress(){
  const uploading=Boolean(state.aiBatchId)&&(state.pumping||state.starting);
  const q=state.aiQueue;
  let p=state.aiBatchId?state.aiProgress:null;
  // A restored finished batch is history, not a task that just completed here.
  if(p&&!p.pending&&!p.running&&!state.aiRunActive&&!state.aiEnqueueing&&!uploading){
    state.aiBatchId=null;state.aiProgress=null;p=null;
    try{sessionStorage.removeItem('waku-ai-batch');}catch{}
  }
  if(!state.aiBatchId&&q){
    if((q.pending||q.running)&&!state.aiBaseline){
      state.aiBaseline={done:q.done,failed:q.failed,skipped:q.skipped,cancelled:q.cancelled||0};state.aiResult=null;
    }
    if(state.aiBaseline){
      p={...q};for(const key of ['done','failed','skipped','cancelled'])p[key]=Math.max(0,(q[key]||0)-state.aiBaseline[key]);
      p.total=p.pending+p.running+p.done+p.failed+p.skipped+p.cancelled;
    }
  }
  const active=Boolean(p?.pending||p?.running||state.aiEnqueueing||uploading);
  if(active){state.aiRunActive=true;state.aiResult=null;}
  else if(state.aiRunActive&&!state.aiControlBusy){
    if(p?.total){
      state.aiResult=p.failed||p.cancelled?{batchId:state.aiBatchId,progress:{...p}}:null;
      if(!state.aiResult)toast(`AI đã xử lý xong ${number(p.total)} ảnh.`);
    }
    state.aiRunActive=false;state.aiBatchId=null;state.aiProgress=null;state.aiBaseline=null;
    try{sessionStorage.removeItem('waku-ai-batch');}catch{}
  }
  const paused=p?.control==='paused'||!state.aiBatchId&&p?.paused>0||q?.control==='paused';
  $('ai-progress').hidden=!active;
  const result=state.aiResult?.progress;
  $('ai-result').hidden=active||!result;
  $('ai-result-summary').textContent=result?`Đợt AI đã kết thúc · ${number(result.done)} thành công${result.failed?' · '+number(result.failed)+' lỗi':''}${result.cancelled?' · '+number(result.cancelled)+' đã huỷ':''}`:'';
  $('dismiss-ai').disabled=state.aiControlBusy;
  $('pause-ai').textContent=paused?'Tiếp tục':'Tạm dừng';$('pause-ai').disabled=state.aiControlBusy||!p?.pending&&!p?.running;
  $('cancel-ai').disabled=state.aiControlBusy||!p?.pending&&!p?.running;
  $('retry-ai').hidden=active||!result;$('retry-ai').textContent='Chạy lại lỗi';$('retry-ai').disabled=state.aiControlBusy||!state.aiAvailable;
  $('retry-ai').title=state.aiResult?.batchId?'Chạy lại ảnh lỗi hoặc đã huỷ của đợt này':'Chạy lại toàn bộ ảnh lỗi hoặc đã huỷ trong hàng đợi';
  $('ai-control-note').textContent=state.aiControlBusy?'Đang cập nhật…':(state.aiBatchId?'Đợt đang xem.':'Hàng đợi đang xử lý.')+(paused?' Đã tạm dừng; ảnh đang chạy có thể hoàn tất.':' Mỗi ảnh tối đa 10 giây. Ảnh lỗi không tự thử lại.');
  if(!paused&&!state.aiControlBusy&&p?.pending&&!p?.running){
    const ready=Math.max(q?.next_attempt||0,q?.provider_ready_at||0);
    if(q?.provider_ready_at===null)$('ai-control-note').textContent+=' Cần bật API và chọn model vision để tiếp tục.';
    else if(ready>Date.now())$('ai-control-note').textContent+=` API tạm chờ khoảng ${Math.ceil((ready-Date.now())/1000)} giây trước khi nhận ảnh mới.`;
  }
  $('pause-ai').dataset.action=paused?'resume':'pause';
  const progress=$('ai-progress-bar');
  if(p?.total){
    const finished=p.done+p.failed+p.skipped+(p.cancelled||0);progress.max=p.total;progress.value=finished;
    $('ai-summary').textContent=`AI gắn tag${paused?' · tạm dừng':''} · đã xử lý ${finished}/${p.total}`;
    $('ai-detail').textContent=`${p.done} thành công · ${p.running} đang chạy · ${p.pending} chờ${p.failed?' · '+p.failed+' lỗi':''}${p.cancelled?' · '+p.cancelled+' đã huỷ':''}${p.skipped?' · '+p.skipped+' bỏ qua':''}`;
    $('ai-progress').classList.toggle('has-errors',p.failed>0);
  }else{
    progress.removeAttribute('value');progress.max=1;$('ai-progress').classList.remove('has-errors');
    $('ai-summary').textContent=state.aiEnqueueing?'Đang đưa ảnh vào hàng đợi AI…':'AI đang gắn tag…';
    $('ai-detail').textContent=state.aiEnqueueing?'Đang xếp ảnh đã chọn vào hàng đợi.':uploading?'Ảnh đã lưu sẽ được AI xử lý. Đang lấy tiến trình từ máy chủ.':'Đang lấy tiến trình xử lý…';
  }
}
$('dismiss-ai').addEventListener('click',()=>{state.aiResult=null;renderAiProgress();});
async function controlAi(action){
  if(state.aiControlBusy)return;
  const batchId=state.aiQueue?.control==='paused'&&action==='resume'?null:action==='retry'?state.aiResult?.batchId||null:state.aiBatchId;
  if(action==='cancel'&&!confirm('Huỷ các ảnh chưa xử lý xong '+(batchId?'trong đợt này':'trong hàng đợi')+'? Ảnh và tag đã lưu vẫn được giữ nguyên.'))return;
  state.aiControlBusy=true;renderAiProgress();
  try{
    const data=await api('/v1/analysis/control',{method:'POST',body:JSON.stringify({action,...(batchId?{batch_id:batchId}:{})})});
    if(action==='retry'){
      if(batchId)setAiBatch(batchId);
      else{const old=state.aiQueue;state.aiBaseline={done:old?.done||0,failed:0,skipped:old?.skipped||0,cancelled:0};state.aiResult=null;state.aiRunActive=true;}
    }
    if(batchId)state.aiProgress=data.progress;else state.aiQueue=data.progress;
    state.galleryDirty=true;await refreshAiProgress();
  }catch(error){toast(error.message);}finally{state.aiControlBusy=false;renderAiProgress();schedulePoll();}
}
$('pause-ai').addEventListener('click',()=>controlAi($('pause-ai').dataset.action));
$('cancel-ai').addEventListener('click',()=>controlAi('cancel'));
$('retry-ai').addEventListener('click',()=>controlAi('retry'));
async function queueAi(ids){
  if(!ids.length)return;state.bulkBusy=true;state.aiEnqueueing=true;state.bulkLabel='Đang xếp hàng AI…';renderSelection();
  renderAiProgress();
  try{
    const {id}=await api('/v1/analysis-batches',{method:'POST',body:'{}'});setAiBatch(id);setView('library');
    let added=0;for(let i=0;i<ids.length;i+=100){const data=await api('/v1/analysis-batches/'+id,{method:'POST',body:JSON.stringify({ids:ids.slice(i,i+100)})});added+=data.added;}
    for(const imageId of ids){const image=state.images.get(imageId);if(image)image.ai_status='pending';}state.galleryDirty=true;renderGallery();
    toast(`Đã xếp hàng AI cho ${added} ảnh.`);await refreshAiProgress();
  }catch(error){toast(error.message);}finally{state.bulkBusy=false;state.aiEnqueueing=false;state.bulkLabel='';renderSelection();renderAiProgress();}
}
$('bulk-ai').addEventListener('click',()=>queueAi([...state.selected]));
function confirmDelete(ids){
  if(!ids.length)return;state.deleteTargets=[...ids];$('delete-title').textContent=ids.length===1?'Xóa ảnh này?':`Xóa ${ids.length} ảnh đã chọn?`;
  $('delete-description').textContent=`${ids.length} ảnh cùng tag sẽ được xóa khỏi thư viện. Thao tác này không thể hoàn tác.`;$('confirm-delete').showModal();
}
$('bulk-delete').addEventListener('click',()=>confirmDelete([...state.selected]));

function bulkEditControls(){
  const busy=state.bulkBusy,changed=Boolean($('bulk-edit-rating').value||$('bulk-edit-type').value);
  $('bulk-edit-rating').disabled=busy;$('bulk-edit-type').disabled=busy;$('cancel-bulk-edit').disabled=busy;
  $('save-bulk-edit').disabled=busy||!changed||!state.editTargets.length;
}
$('bulk-edit-button').addEventListener('click',()=>{
  if(!state.selected.size||state.bulkBusy)return;state.editTargets=[...state.selected];
  $('bulk-edit-title').textContent=`Sửa ${number(state.editTargets.length)} ảnh đã chọn`;
  $('bulk-edit-rating').value='';$('bulk-edit-type').value='';$('bulk-edit-status').hidden=true;$('save-bulk-edit').textContent='Lưu thay đổi';
  bulkEditControls();$('bulk-edit').showModal();
});
for(const id of ['bulk-edit-rating','bulk-edit-type'])$(id).addEventListener('change',bulkEditControls);
$('cancel-bulk-edit').addEventListener('click',()=>$('bulk-edit').close());
$('bulk-edit').addEventListener('cancel',event=>{if(state.bulkBusy)event.preventDefault();});
$('bulk-edit-form').addEventListener('submit',async event=>{
  event.preventDefault();if(state.bulkBusy||!state.editTargets.length)return;
  const changes={},rating=$('bulk-edit-rating').value,type=$('bulk-edit-type').value;
  if(rating)changes.rating=rating;if(type)changes.image_type=type==='clear'?null:type;
  if(!Object.keys(changes).length)return;
  const ids=[...state.editTargets],remaining=new Set(ids);let done=0,missing=0;
  state.bulkBusy=true;bulkEditControls();renderSelection();$('bulk-edit-status').hidden=false;$('bulk-edit-status').textContent=`Đang lưu 0/${number(ids.length)} ảnh…`;
  try{
    for(let i=0;i<ids.length;i+=100){
      const result=await api('/v1/images/bulk',{method:'PATCH',body:JSON.stringify({ids:ids.slice(i,i+100),...changes})});
      done+=result.updated;missing+=result.missing.length;
      for(const id of [...result.ids,...result.missing]){remaining.delete(id);state.selected.delete(id);}
      for(const id of result.ids){const image=state.images.get(id);if(image)Object.assign(image,changes);}
      state.bulkLabel=`Đã sửa ${number(done)}/${number(ids.length)} ảnh`;$('bulk-edit-status').textContent=state.bulkLabel+(missing?` · ${number(missing)} ảnh không còn tồn tại`:'');$('save-bulk-edit').textContent=`Đang lưu ${number(done)}/${number(ids.length)}`;renderSelection();
    }
    $('bulk-edit').close();toast(`Đã sửa ${number(done)} ảnh${missing?` · ${number(missing)} ảnh không còn tồn tại`:'.'}`);
  }catch(error){$('bulk-edit-status').textContent=`Đã sửa ${number(done)}/${number(ids.length)} ảnh. ${error.message}`;}
  finally{
    state.editTargets=[...remaining];state.bulkBusy=false;state.bulkLabel='';state.allSelected=false;$('save-bulk-edit').textContent=remaining.size?'Thử lại':'Lưu thay đổi';if(remaining.size)$('bulk-edit-title').textContent=`Sửa ${number(remaining.size)} ảnh còn lại`;bulkEditControls();renderGallery();
    await loadImages(false);refreshStats(false,true);
  }
});

function addFiles(files){
  const added=[];for(const file of files){
    const fingerprint=`${file.name}:${file.size}:${file.lastModified}`;
    const valid=/\.(jpe?g|png|webp|gif|avif)$/i.test(file.name) || /^image\/(jpeg|png|webp|gif|avif)$/.test(file.type);
    const error=!valid?'Định dạng ảnh chưa được hỗ trợ.':file.size>state.maxMb*1048576?`Ảnh vượt ${state.maxMb} MB.`:'';
    const item={id:crypto.randomUUID(),fingerprint,file,name:file.name,preview:valid&&!error?URL.createObjectURL(file):null,status:error?'error':'checking',error,progress:0,needsCheck:!error};state.queue.push(item);if(!error)added.push(item);
  }
  renderQueue();if(added.length)checkUploads(added);
}
let checkChain=Promise.resolve();
function checkUploads(items){
  state.checking++;renderQueue();
  checkChain=checkChain.then(async()=>{
    try{
      // Hash at most two files at a time; the filename never decides equality.
      let next=0;const hash=async()=>{while(next<items.length){const item=items[next++];if(item.file&&!item.sha256){const buffer=await item.file.arrayBuffer();item.sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',buffer))].map(x=>x.toString(16).padStart(2,'0')).join('');}}};await Promise.all([hash(),hash()]);
      const seenHashes=new Set(),seenUrls=new Set();
      for(const item of state.queue){if(item.status==='duplicate')continue;const seen=item.sha256?seenHashes:item.url?seenUrls:null,key=item.sha256||item.url;if(!seen)continue;if(seen.has(key)&&items.includes(item)){item.status='duplicate';item.needsCheck=false;}else seen.add(key);}
      const candidates=items.filter(item=>item.status==='checking'&&state.queue.includes(item));
      for(let i=0;i<candidates.length;i+=100){const chunk=candidates.slice(i,i+100);const data=await api('/v1/images/duplicates',{method:'POST',body:JSON.stringify({hashes:chunk.filter(x=>x.sha256).map(x=>x.sha256),urls:chunk.filter(x=>x.url).map(x=>x.url)})});
        const hashes=new Set(data.duplicates.map(x=>x.sha256)),urls=new Set(data.duplicates.map(x=>x.import_url).filter(Boolean)),links=new Map((data.links || []).map(link=>[link.input,link]));
        for(const item of chunk){if(item.url)item.link=links.get(item.url);item.status=hashes.has(item.sha256)||urls.has(item.url)?'duplicate':'queued';item.needsCheck=false;}
        const identities=new Set();for(const item of state.queue){if(!item.url||item.status==='duplicate')continue;const identity=item.link?.identity || item.url;if(identities.has(identity)&&items.includes(item)&&item.status==='queued')item.status='duplicate';else identities.add(identity);}
      }
    }catch(error){for(const item of items)if(item.status==='checking'){item.status='error';item.error='Chưa kiểm tra được ảnh trùng. '+error.message;item.needsCheck=true;}}
    finally{state.checking--;renderQueue();}
  });return checkChain;
}
$('choose-files').addEventListener('click',()=>$('file-input').click());$('file-input').addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('dragging');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',event=>addFiles(event.dataTransfer.files));
$('add-urls').addEventListener('click',()=>{
  const urls=$('image-urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),added=[];
  for(const raw of urls){try{const url=new URL(raw);if(!['https:','http:'].includes(url.protocol) || url.username || url.password)throw new Error();url.hash='';const item={id:crypto.randomUUID(),url:url.href,name:decodeURIComponent(url.pathname.split('/').pop() || url.hostname),status:'checking',needsCheck:true,progress:0};state.queue.push(item);added.push(item);}catch{toast('Có link không hợp lệ. Chỉ nhận link ảnh HTTP(S).');}}
  $('image-urls').value='';renderQueue();if(added.length)checkUploads(added);
});
function options(){return {tags:tagList($('upload-tags').value),rating:$('upload-rating').value,image_type:$('upload-type').value || null,manual:!$('use-ai').checked,source_url:$('upload-source').value.trim() || null};}
function renderQueue(){
  const list=$('queue-list');list.replaceChildren();const visible=state.queue.filter(x=>x.status!=='duplicate');$('queue-count').textContent=number(visible.length);
  const done=state.queue.filter(x=>x.status==='done').length,errors=state.queue.filter(x=>x.status==='error').length,pending=state.queue.filter(x=>x.status==='queued').length,active=state.queue.filter(x=>x.status==='uploading').length;
  if(!visible.length)list.append(element('p','queue-empty','Ảnh bạn chọn sẽ xuất hiện ở đây.'));
  for(const item of visible){
    const row=element('div','queue-item '+item.status);row.dataset.id=item.id;
    if(item.preview){const img=element('img');img.src=item.preview;img.alt='';img.loading='lazy';row.append(img);}else{const mark=element('span','queue-file-icon');mark.append(icon('link'));row.append(mark);}
    const text=element('div');text.append(element('strong','',item.name));
    const savedText=item.importInfo?.fallback_reason?(item.importInfo.fallback_reason==='too_large'?'Đã lưu bản từ link · ảnh gốc vượt giới hạn':'Đã lưu bản từ link · không lấy được ảnh gốc'):item.importInfo?.used_original?'Đã lưu ảnh gốc':'Đã lưu ảnh';
    const description=item.status==='done'?savedText:item.status==='checking'?'Đang kiểm tra ảnh trùng…':item.status==='error'?item.error:item.status==='uploading'?(item.progress===100?'Đang lưu trên máy chủ…':`Đang tải… ${item.progress}%`):(item.file?bytes(item.file.size)+' · Chờ tải':item.link?.source?'Link '+item.link.source+' · Ưu tiên ảnh gốc':'Link ảnh · Chờ tải');
    text.append(element('small','',description));
    if(item.status==='uploading'){const progress=element('progress');progress.max=100;progress.value=item.progress;text.append(progress);}
    const remove=element('button','',item.status==='done'?'✓':'×');remove.type='button';remove.setAttribute('aria-label','Bỏ '+item.name+' khỏi hàng đợi');remove.disabled=item.status==='uploading';remove.addEventListener('click',()=>{state.queue=state.queue.filter(x=>x!==item);if(item.preview)URL.revokeObjectURL(item.preview);renderQueue();});row.append(text,remove);list.append(row);
  }
  const batch=state.uploadIds.size?state.queue.filter(item=>state.uploadIds.has(item.id)):state.queue;
  const saved=batch.filter(item=>item.status==='done').length,failed=batch.filter(item=>item.status==='error').length,total=batch.filter(item=>item.status!=='duplicate').length,duplicates=state.queue.filter(item=>item.status==='duplicate').length;
  $('queue-progress').hidden=!total||!state.pumping&&!state.starting;$('queue-summary').textContent=`${saved}/${total} đã lưu${failed?' · '+failed+' lỗi':''}${active?' · '+active+' đang tải':''}`;$('batch-progress').max=Math.max(1,total);$('batch-progress').value=saved;
  $('duplicate-summary').hidden=!duplicates&&!state.checking;$('duplicate-summary').textContent=`${duplicates?`${duplicates} ảnh trùng đã bỏ qua. `:''}${state.checking?'Đang kiểm tra ảnh…':''}`;
  $('start-upload').disabled=!pending||state.starting||state.checking>0;$('start-upload').textContent=state.checking?'Đang kiểm tra ảnh…':state.starting?'Đang chuẩn bị…':state.pumping&&!state.paused?'Đang tải lên…':state.paused && active?'Tiếp tục tải lên':`Tải ${pending || ''} ảnh lên`.replace('  ',' ');
  $('pause-upload').hidden=!state.pumping || state.paused;$('retry-errors').hidden=!errors;
  for(const id of ['use-ai','upload-rating','upload-type','upload-tags','upload-source'])$(id).disabled=state.pumping&&!state.paused||id==='use-ai'&&!state.aiAvailable;
}
function fileUpload(item){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/v1/images');xhr.responseType='json';xhr.timeout=90000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable){item.progress=Math.round(event.loaded/event.total*100);const row=$('queue-list').querySelector(`[data-id="${item.id}"]`);if(row){row.querySelector('small').textContent=item.progress===100?'Đang lưu trên máy chủ…':`Đang gửi… ${item.progress}%`;row.querySelector('progress').value=item.progress;}const preview=$('gallery').querySelector(`[data-upload-id="${item.id}"] .rating-label`);if(preview)preview.textContent=item.progress===100?'Đang lưu…':`Đang tải ${item.progress}%`;}};
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else{const error=new Error(xhr.response?.error?.message || 'Tải ảnh chưa thành công.');error.status=xhr.status;error.retryAfter=Number(xhr.getResponseHeader('retry-after')) || 2;if(xhr.status===401)showLogin('Phiên đã hết hạn. Vui lòng đăng nhập lại.');reject(error);}};
    xhr.onerror=()=>reject(new Error('Mất kết nối. Bấm thử lại khi mạng ổn định.'));xhr.ontimeout=()=>reject(new Error('Quá thời gian chờ. Bạn có thể thử lại file này.'));
    const form=new FormData();form.set('file',item.file,item.file.name);form.set('tags',item.options.tags.join(','));form.set('rating',item.options.rating);if(item.options.image_type)form.set('image_type',item.options.image_type);form.set('manual',String(item.options.manual));if(item.options.batch_id)form.set('batch_id',item.options.batch_id);if(item.options.source_url)form.set('source_url',item.options.source_url);xhr.send(form);
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
      item.status=data.duplicate?'duplicate':'done';item.progress=100;item.duplicate=data.duplicate;item.imageId=data.image.id;item.importInfo=data.import;
      if(state.rating==='' || state.rating==='unknown'&&!isClassified(data.image) || state.rating===data.image.rating&&isClassified(data.image))state.images.set(data.image.id,data.image);
    }catch(error){item.status='error';item.error=error.message || 'Tải ảnh chưa thành công.';}
    renderQueue();renderGallery();
  }
}
async function start(){
  if(!state.authenticated||state.starting||state.checking)return;
  const selected=options();if(selected.tags.length>30){toast('Tối đa 30 tag chung.');return;}
  if(!$('upload-source').checkValidity()){toast('Link nguồn chưa hợp lệ.');return;}
  const resume=state.queue.some(item=>state.uploadIds.has(item.id)&&['queued','uploading'].includes(item.status));
  if(!resume)state.uploadIds=new Set(state.queue.filter(item=>item.status==='queued').map(item=>item.id));
  state.starting=true;state.filterTags.clear();state.filterTypes.clear();document.querySelectorAll('#filter-panel input[type=checkbox]').forEach(input=>input.checked=false);$('filter-count').textContent='';changeFilter('unknown',true);setView('library');renderQueue();let ownsWorkers=false;
  try{
    if(!selected.manual){
      if(!resume || !state.aiBatchId){const {id}=await api('/v1/analysis-batches',{method:'POST',body:'{}'});setAiBatch(id);}
      selected.batch_id=state.aiBatchId;
    }else if(!resume)setAiBatch(null);
    state.activeOptions=selected;for(const item of state.queue)if(item.status==='queued')item.options=structuredClone(selected);
    state.paused=false;state.starting=false;if(state.pumping){renderQueue();return;}state.pumping=true;ownsWorkers=true;renderQueue();renderAiProgress();schedulePoll();
    await Promise.all([worker(),worker()]);
  }catch(error){toast(error.message);}finally{state.starting=false;if(ownsWorkers)state.pumping=false;renderQueue();renderGallery();refreshStats(true,true);}
}
$('start-upload').addEventListener('click',start);$('pause-upload').addEventListener('click',()=>{state.paused=true;renderQueue();toast('Đã tạm dừng. Các file đang tải sẽ hoàn tất trước.');});
$('retry-errors').addEventListener('click',async()=>{const checking=[];for(const item of state.queue)if(item.status==='error'){item.status=item.needsCheck?'checking':'queued';item.error='';item.progress=0;if(item.needsCheck)checking.push(item);}if(checking.length)await checkUploads(checking);start();});
$('clear-done').addEventListener('click',()=>{for(const item of state.queue)if(['done','duplicate'].includes(item.status)&&item.preview)URL.revokeObjectURL(item.preview);state.queue=state.queue.filter(x=>!['done','duplicate'].includes(x.status));renderQueue();});
window.addEventListener('beforeunload',event=>{if(state.pumping || state.queue.some(x=>x.status==='queued')){event.preventDefault();event.returnValue='';}});

async function openEditor(id){
  try{const image=state.images.get(id) || (await api('/v1/images/'+id)).image;state.editing=image;state.images.set(id,image);$('edit-preview').src='/media/'+encodeURIComponent(id);$('edit-title').value=image.title;$('edit-tags').value=image.tags.join(', ');$('edit-rating').value=image.rating;$('edit-type').value=image.image_type || '';$('edit-source').value=image.source_url || '';$('analyze-image').hidden=isClassified(image);$('analyze-image').disabled=['pending','running'].includes(image.ai_status);$('edit-meta').textContent=`${bytes(image.size)} · ${image.mime_type.replace('image/','').toUpperCase()} · ${new Date(image.created_at).toLocaleDateString('vi-VN')}${image.original_name?' · '+image.original_name:''}`;$('edit-ai-error').hidden=image.ai_status!=='failed';$('edit-ai-error').textContent=image.ai_status==='failed'?analysisError(image.ai_error):'';$('editor').showModal();}
  catch(error){toast(error.message);}
}
$('editor-close').addEventListener('click',()=>$('editor').close());
let outsideEditor=false;
const editorOutside=event=>{const box=$('editor').getBoundingClientRect();return event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom;};
$('editor').addEventListener('pointerdown',event=>outsideEditor=event.target===$('editor')&&editorOutside(event));
$('editor').addEventListener('click',event=>{if(outsideEditor&&event.target===$('editor')&&editorOutside(event))$('editor').close();outsideEditor=false;});
$('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();const image=state.editing;if(!image)return;$('save-image').disabled=true;
  try{const {image:updated}=await api('/v1/images/'+image.id,{method:'PATCH',body:JSON.stringify({title:$('edit-title').value,tags:tagList($('edit-tags').value),rating:$('edit-rating').value,image_type:$('edit-type').value || null,source_url:$('edit-source').value.trim() || null})});state.images.set(image.id,updated);state.editing=updated;state.selected.delete(image.id);renderGallery();$('editor').close();toast('Đã lưu thay đổi.');loadImages(false);refreshStats(false,true);}
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
document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden && state.authenticated){if(Date.now()-state.lastStats>15000)refreshStats(true);else schedulePoll();}});
setupProviders({api,toast,onChange:enabled=>{
  const before=state.aiAvailable;state.aiAvailable=enabled;
  if(!enabled)$('use-ai').checked=false;else if(!before)$('use-ai').checked=true;
  renderQueue();renderSelection();
}});
api('/v1/session').then(enter).catch(()=>showLogin());

setupKeys({api,toast});
