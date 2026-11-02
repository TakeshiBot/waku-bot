import {setupProviders} from './providers.js?v=16';
import {setupKeys} from './keys.js?v=16';
import {GalleryThumbnails} from './gallery-cache.js?v=17';
const $=id=>document.getElementById(id);
const state={authenticated:false,maxMb:10,images:new Map(),cursor:null,query:'',rating:'',queue:[],pumping:false,paused:true,editing:null,listVersion:0,statsBusy:false,
  selected:new Set(),allSelected:false,selectionBusy:false,bulkBusy:false,deleteTargets:[],editTargets:[],loadedLimit:0,uploadIds:new Set(),uploadResults:new Map(),starting:false,aiBatchId:null,aiProgress:null,aiPending:0,aiEnqueueing:false,
  revision:null,lastStats:0,galleryDirty:false,gallerySignature:'',galleryScope:null,galleryViews:new Map(),cards:new Map(),dayGroups:new Map(),listController:null,quotaUntil:0,filterTags:new Set(),filterTypes:new Set(),tagOptions:[],tagsLoaded:0,checking:0,aiAvailable:false,aiQueue:null,aiControlBusy:false,aiResult:null,
  editorIds:[],editorVersion:0,editorNavigationBusy:false,editorNavigationRun:0};
const thumbnails=new GalleryThumbnails($('library-results'));
const number=n=>new Intl.NumberFormat('vi-VN').format(n || 0);
const bytes=n=>n>=1073741824?(n/1073741824).toFixed(1)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(0)+' KB';
const tagList=value=>value.trim().split(/[,\n]|\s+(?=#)/).map(x=>x.trim().replace(/^#/, '')).filter(Boolean);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let toastTimer,filterTimer,pollTimer,duplicateTimer;
function positionUploadPanel(){
  const panel=$('upload-panel');if(panel.hidden)return;
  const anchor=$('show-upload').getBoundingClientRect(),gap=8,top=anchor.bottom+gap;
  panel.style.top=top+'px';panel.style.right=Math.max(10,innerWidth-anchor.right)+'px';
  panel.style.height=Math.max(0,Math.min(740,innerHeight-top-12))+'px';
  positionUploadSettings();
}
function positionUploadSettings(){
  const panel=$('upload-settings');if(!panel.matches(':popover-open'))return;
  const anchor=$('upload-options-toggle').closest('.upload-options').getBoundingClientRect();
  if(!anchor.width||!anchor.height){panel.hidePopover();return;}
  panel.style.width=Math.min(anchor.width,innerWidth-24)+'px';panel.style.maxHeight=Math.max(0,innerHeight-24)+'px';
  const height=panel.getBoundingClientRect().height,below=innerHeight-anchor.bottom-18,above=anchor.top-18;
  const upward=below<height&&above>below,available=Math.max(0,upward?above:below);
  panel.style.maxHeight=available+'px';
  panel.style.left=Math.max(12,Math.min(anchor.left,innerWidth-panel.getBoundingClientRect().width-12))+'px';
  panel.style.top=(upward?Math.max(12,anchor.top-Math.min(height,available)-6):anchor.bottom+6)+'px';
}
$('upload-settings').addEventListener('toggle',event=>{
  $('upload-options-toggle').setAttribute('aria-expanded',String(event.newState==='open'));
  if(event.newState==='open')positionUploadSettings();
});
window.addEventListener('resize',()=>{positionUploadPanel();queueGalleryLayout();});
document.addEventListener('scroll',event=>{if(event.target!==$('upload-settings'))positionUploadSettings();},true);
try{state.quotaUntil=Number(localStorage.getItem('waku-d1-pause-until'))||0;}catch{}
function pauseForQuota(error){
  if(error.code!=='d1_daily_limit')return;
  state.quotaUntil=Date.now()+Math.max(60,error.retryAfter||60)*1000;state.paused=true;
  try{localStorage.setItem('waku-d1-pause-until',String(state.quotaUntil));}catch{}
  clearTimeout(pollTimer);$('list-status').textContent=error.message;toast(error.message);renderQueue();schedulePoll();
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4000);}
function element(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;}
function isClassified(image){return image.classified??Boolean(image.title?.trim()&&image.tags?.some(tag=>tag.trim())&&['safe','r18'].includes(image.rating)&&['anime','real'].includes(image.image_type));}
function analysisError(code){
  const messages={provider_content_filtered:'Model từ chối phân tích ảnh do bộ lọc nội dung của nhà cung cấp. Đây không phải lỗi file. Bạn có thể gắn tag thủ công hoặc chọn một model vision khác.',provider_timeout:'Model không trả xong kết quả trong 10 giây. Ảnh không tự chạy lại.',worker_timeout:'Lượt xử lý bị gián đoạn. Ảnh không tự chạy lại.',invalid_response:'API trả kết quả không đúng định dạng gắn tag.',incomplete_response:'Model trả thiếu tên, tag, phân loại Safe/R18 hoặc kiểu ảnh. Lượt này chưa hoàn tất; bạn có thể chạy lại hoặc sửa thủ công.',classification_unresolved:'AI chưa xác định được Safe/R18 từ ảnh. Lượt này chưa hoàn tất; bạn có thể chạy lại hoặc chọn phân loại thủ công. Hệ thống không tự đoán.',provider_unavailable:'Không kết nối được API vision.',image_missing:'Không đọc được ảnh đã lưu.',key_decryption_failed:'Cần nhập lại key của API vision.'};
  return messages[code]||(/^provider_http_\d{3}$/.test(code||'')?`API vision trả lỗi HTTP ${code.slice(-3)}. Kiểm tra API, key và model đang chọn.`:'Chưa phân tích được ảnh. Kiểm tra API/model rồi chủ động chạy lại.');
}
function showLogin(message=''){
  clearTimeout(aiRenderTimer);state.aiProgress=null;state.aiBatchId=null;
  state.listVersion++;state.listController?.abort();state.galleryViews.clear();state.galleryScope=null;
  state.dayGroups.clear();$('scroll-date').hidden=true;
  for(const card of state.cards.values()){const img=card.querySelector('.thumb img');if(img)thumbnails.release(img);}state.cards.clear();thumbnails.clear();$('gallery').replaceChildren();state.gallerySignature='';
  state.authenticated=false;state.paused=true;clearTimeout(pollTimer);$('app').hidden=true;$('login').hidden=false;$('login-error').textContent=message;
  $('editor').close();$('confirm-delete').close();$('bulk-edit').close();$('api-settings').close();
  $('service-keys').close();
  toggleUpload(false);
  if($('upload-settings').matches(':popover-open'))$('upload-settings').hidePopover();
}
async function api(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{...(options.body && !(options.body instanceof FormData)?{'content-type':'application/json'}:{}),...options.headers},signal:options.signal || AbortSignal.timeout(30000)});
  let data;try{data=await response.json();}catch{data={};}
  if(!response.ok){if(response.status===401 && state.authenticated)showLogin('Phiên đăng nhập đã hết hạn. Vui lòng nhập lại key.');const error=new Error(data.error?.message || 'Chưa thực hiện được thao tác.');error.status=response.status;error.code=data.error?.code;error.retryAfter=Number(response.headers.get('retry-after')) || 2;pauseForQuota(error);throw error;}
  return data;
}
async function enter(session){
  session ||= await api('/v1/session');state.authenticated=true;state.maxMb=session.max_image_mb || 10;
  if(session.database==='libsql'){state.quotaUntil=0;try{localStorage.removeItem('waku-d1-pause-until');}catch{}}
  state.lastStats=0;state.aiQueue=null;state.aiProgress=null;state.aiResult=null;
  $('ai-progress').hidden=true;$('ai-result').hidden=true;
  state.aiAvailable=Boolean(session.ai_enabled);$('use-ai').checked=state.aiAvailable;$('use-ai').disabled=!state.aiAvailable;
  state.aiBatchId=null;state.aiDismissedSession=null;
  $('api-key').value='';$('login').hidden=true;$('app').hidden=false;$('upload-limits').textContent=`JPG, PNG, WebP, GIF, AVIF · tối đa ${state.maxMb} MB/ảnh`;
  await Promise.all([refreshStats(),loadImages(true)]);
  schedulePoll();
}
function schedulePoll(){
  clearTimeout(pollTimer);if(!state.authenticated||document.hidden)return;
  if(state.quotaUntil>Date.now()){pollTimer=setTimeout(()=>{state.quotaUntil=0;refreshStats(true,true);},state.quotaUntil-Date.now()+1000);return;}
  const q=state.aiQueue,active=['active','retry_wait','paused'].includes(state.aiProgress?.phase)||Boolean(state.aiProgress?.next_retry_at)||state.pumping||state.starting||state.aiEnqueueing||Boolean(q?.running)||Boolean(q?.control!=='paused'&&q?.pending>q?.paused);
  const waiting=state.aiProgress?.phase==='done'&&state.aiProgress.next_retry_at>Date.now()&&!state.pumping&&!state.starting&&!q?.running&&!q?.pending;
  pollTimer=setTimeout(()=>refreshStats(true),waiting?30000:active?8000:60000);
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
  showLogin();state.images.clear();state.selected.clear();setAiBatch(null);state.queue.forEach(item=>{if(item.preview)URL.revokeObjectURL(item.preview);});state.queue=[];state.uploadIds.clear();state.uploadResults.clear();renderQueue();
});

async function refreshStats(updateGallery=false,force=false){
  if(state.statsBusy || !state.authenticated || state.quotaUntil>Date.now())return;state.statsBusy=true;
  try{
    if(!force&&Date.now()-state.lastStats<2000)return;
    const stats=await api('/v1/state');state.lastStats=Date.now();
    $('nav-count').textContent=number(stats.images.total);$('nav-library').setAttribute('aria-label',`Kho ảnh · ${number(stats.images.total)} ảnh`);$('stat-size').textContent=bytes(stats.images.bytes);
    for(const count of document.querySelectorAll('[data-tab-count]'))count.textContent=`(${number(stats.tabs?.[count.dataset.tabCount])})`;
    const pending=Number(stats.ai_pending)+Number(stats.uploads_pending);$('stat-pending').textContent=number(pending);$('nav-processing').title=pending?`${number(stats.ai_pending)} AI · ${number(stats.uploads_pending)} upload`:'Mọi thứ đã sẵn sàng';
    state.aiPending=Number(stats.ai_pending);state.aiQueue=stats.ai_queue;state.aiProgress=stats.ai_session||null;state.aiBatchId=stats.ai_session?.id||null;renderAiProgress();
    const changed=state.revision!==null&&state.revision!==stats.revision;state.revision=stats.revision;state.galleryDirty ||= changed;if(changed)state.galleryViews.clear();
    if(updateGallery&&state.galleryDirty){await loadImages(false);state.galleryDirty=false;}
  }catch(error){if(state.authenticated)$('list-status').textContent=error.message;}
  finally{state.statsBusy=false;schedulePoll();}
}
function rememberGalleryView(){
  if(state.galleryScope===null)return;
  const previous=state.galleryViews.get(state.galleryScope);
  state.galleryViews.delete(state.galleryScope);
  state.galleryViews.set(state.galleryScope,{images:new Map(state.images),cursor:state.cursor,limit:state.loadedLimit,scroll:$('library-results').scrollTop,at:previous?.at || Date.now()});
  while(state.galleryViews.size>8)state.galleryViews.delete(state.galleryViews.keys().next().value);
}
async function loadImages(reset=false,more=false,useCache=false){
  const pageSize=5*(parseInt(getComputedStyle($('gallery')).getPropertyValue('--gallery-columns'),10)||6);
  rememberGalleryView();
  const base=listParameters(),scope=base.toString(),sameScope=scope===state.galleryScope,version=++state.listVersion;
  state.listController?.abort();state.listController=null;
  const cached=useCache&&!state.galleryDirty?state.galleryViews.get(scope):null;
  if(cached&&Date.now()-cached.at<60000){
    state.images=new Map(cached.images);state.cursor=cached.cursor;state.loadedLimit=cached.limit;state.galleryScope=scope;
    $('list-status').textContent='';renderGallery();$('library-results').scrollTop=cached.scroll;return;
  }
  if(!useCache&&!more)state.galleryViews.clear();
  const wanted=reset||!sameScope?pageSize:(state.loadedLimit||pageSize)+(more?pageSize:0),controller=new AbortController();state.listController=controller;
  const scroll=sameScope?$('library-results').scrollTop:0;
  if(!sameScope){state.images=new Map();state.cursor=null;state.galleryScope=null;renderGallery();}
  if(reset)$('list-status').textContent='Đang mở thư viện…';
  try{
    const images=more&&sameScope?new Map(state.images):new Map();let cursor=more&&sameScope?state.cursor:null;
    do{
      const params=new URLSearchParams(base);params.set('limit',String(Math.min(100,wanted-images.size)));if(cursor)params.set('before',cursor);
      const data=await api('/v1/images?'+params,{signal:AbortSignal.any([controller.signal,AbortSignal.timeout(30000)])});if(version!==state.listVersion)return;
      for(const image of data.images)images.set(image.id,image);cursor=data.next;
    }while(cursor && images.size<wanted);
    state.images=images;state.cursor=cursor;state.loadedLimit=wanted;state.galleryScope=scope;$('list-status').textContent='';renderGallery();$('library-results').scrollTop=scroll;
    state.galleryViews.delete(scope);rememberGalleryView();
  }catch(error){if(version===state.listVersion&&!controller.signal.aborted)$('list-status').textContent=error.message;}
  finally{if(state.listController===controller)state.listController=null;}
}
function listParameters(){
  const params=new URLSearchParams();
  if(state.rating==='unknown')params.set('view','pending');
  else if(state.rating){params.set('rating',state.rating);params.set('view','classified');}
  if(state.query)params.set('q',state.query);
  if(state.filterTags.size)params.set('tags',[...state.filterTags].sort().join(','));
  if(state.filterTypes.size)params.set('types',[...state.filterTypes].sort().join(','));return params;
}
function changeFilter(rating,clearSearch=false){
  state.rating=rating;state.selected.clear();state.allSelected=false;
  if(clearSearch){state.query='';$('search').value='';clearTimeout(filterTimer);}
  document.querySelectorAll('[data-rating]').forEach(button=>{const active=button.dataset.rating===rating;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  setView('library');
  loadImages(true,false,true);renderSelection();renderAiProgress();
}
function renderGallery(){
  const signature=JSON.stringify([[...state.images.values()],[...state.selected],state.bulkBusy,state.selectionBusy,state.cursor,state.rating,state.query,[...state.filterTags],[...state.filterTypes]]);
  if(signature===state.gallerySignature){renderSelection();return;}state.gallerySignature=signature;
  const gallery=$('gallery'),desired=[];
  const count=state.images.size;$('empty').hidden=count>0;
  if(!count){$('empty').querySelector('h3').textContent=state.query || state.rating?'Chưa có ảnh phù hợp.':'Một khởi đầu mới.';$('empty').querySelector('p').textContent=state.query || state.rating?'Thử từ khóa hoặc bộ lọc khác nhé.':'Thêm những tấm ảnh đầu tiên. Phần sắp xếp, cứ để Waku lo.';}
  $('visible-count').textContent=count?number(count):'';$('load-more').hidden=!state.cursor;
  for(const image of [...state.images.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))){
    const content=JSON.stringify(image),old=state.cards.get(image.id);
    if(old?.dataset.content===content){state.cards.delete(image.id);state.cards.set(image.id,old);desired.push(old);continue;}
    const card=element('article','card '+image.rating+(state.selected.has(image.id)?' selected':'')),thumb=element('button','thumb');card.dataset.id=image.id;thumb.type='button';thumb.setAttribute('aria-label','Xem chi tiết '+(image.title || 'ảnh'));
    card.dataset.content=content;
    const checkLabel=element('label','image-select'),check=element('input');check.type='checkbox';check.checked=state.selected.has(image.id);check.disabled=state.bulkBusy||state.selectionBusy;check.setAttribute('aria-label','Chọn '+(image.title || 'ảnh'));
    check.addEventListener('change',()=>{if(check.checked)state.selected.add(image.id);else state.selected.delete(image.id);state.allSelected=false;card.classList.toggle('selected',check.checked);renderSelection();});checkLabel.append(check);card.append(checkLabel);
    const img=old?.querySelector('.thumb img') || element('img');img.alt=image.title || 'Ảnh trong thư viện';img.decoding='async';img.width=384;img.height=512;thumb.append(img);thumbnails.observe(img,image);
    const hasDimensions=Number(image.width)>0&&Number(image.height)>0;
    if(hasDimensions)card.style.setProperty('--image-ratio',String(image.width/image.height));
    const sizeCard=()=>{if(!hasDimensions&&img.naturalWidth&&img.naturalHeight){card.style.setProperty('--image-ratio',String(img.naturalWidth/img.naturalHeight));queueGalleryLayout();}};
    img.onload=sizeCard;if(img.complete)sizeCard();
    const classified=isClassified(image);thumb.append(element('span','rating-label '+(classified?image.rating:'unknown'),classified?(image.rating==='safe'?'An toàn':'R18'):'Chờ phân loại'));
    const copy=element('span','copy-mini');copy.append(icon('link'));copy.title='Mở chi tiết để sao chép link';thumb.append(copy);thumb.addEventListener('click',event=>selectOrOpen(image.id,event));
    const title=element('button','card-title',image.title || (['pending','running'].includes(image.ai_status)?'AI đang đặt tên…':'Ảnh chưa có tên'));title.type='button';title.title=image.title || '';title.addEventListener('click',event=>selectOrOpen(image.id,event));
    const tags=element('div','card-tags');for(const tag of image.tags.slice(0,2))tags.append(element('span','',tag));
    if(image.tags.length>2)tags.append(element('span','',`+${image.tags.length-2}`));
    if(['pending','running'].includes(image.ai_status))tags.append(element('span','ai-state busy','✦ AI'));
    else if(!classified&&image.ai_status==='failed'){const error=element('span','ai-state',image.ai_error==='provider_content_filtered'?'AI từ chối':image.ai_error==='classification_unresolved'?'Thiếu phân loại':'AI lỗi');error.title=analysisError(image.ai_error);tags.append(error);}
    card.append(thumb,title,tags);state.cards.delete(image.id);state.cards.set(image.id,card);desired.push(card);
  }
  // Keep unchanged cards connected: selection, progress and pagination no longer
  // detach every image and force the browser to decode/paint the grid again.
  renderDayGroups(desired);
  for(const card of desired){const check=card.querySelector('.image-select input');if(check){check.checked=state.selected.has(card.dataset.id);check.disabled=state.bulkBusy||state.selectionBusy;card.classList.toggle('selected',check.checked);}}
  for(const [id,card]of state.cards){if(state.cards.size<=120)break;if(!card.isConnected){const img=card.querySelector('.thumb img');if(img)thumbnails.release(img);state.cards.delete(id);}}
  renderSelection();
  updateScrollDate();
  if($('editor').open)updateEditorNavigation();
}
const uploadDay=iso=>new Date(Date.parse(iso)+7*3600000).toISOString().slice(0,10);
const dayLabel=day=>new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',weekday:'long',day:'numeric',month:'long'}).format(new Date(day+'T12:00:00+07:00'));
function reconcileChildren(parent,children){
  const keep=new Set(children);for(const child of [...parent.children])if(!keep.has(child))child.remove();
  let position=parent.firstChild;for(const child of children){if(child===position)position=position.nextSibling;else parent.insertBefore(child,position);}
}
function renderDayGroups(cards){
  const groups=new Map();for(const card of cards){const day=uploadDay(state.images.get(card.dataset.id).created_at);if(!groups.has(day))groups.set(day,[]);groups.get(day).push(card);}
  const sections=[];let previousMonth='';
  for(const [day,items]of groups){
    let section=state.dayGroups.get(day);
    if(!section){section=element('section','gallery-day');section.dataset.day=day;
      const month=element('h3','gallery-month'),heading=element('h4','gallery-date'),photos=element('div','day-images');section.append(month,heading,photos);state.dayGroups.set(day,section);}
    const month=day.slice(0,7),monthHeading=section.querySelector('.gallery-month');monthHeading.hidden=month===previousMonth;monthHeading.textContent=`Tháng ${Number(day.slice(5,7))} · ${day.slice(0,4)}`;previousMonth=month;
    section.querySelector('.gallery-date').textContent=dayLabel(day);section.photoCards=items;sections.push(section);
  }
  reconcileChildren($('gallery'),sections);for(const day of state.dayGroups.keys())if(!groups.has(day))state.dayGroups.delete(day);layoutPhotoRows();
}
let galleryLayoutFrame=0;
function queueGalleryLayout(){
  if(galleryLayoutFrame)return;
  galleryLayoutFrame=requestAnimationFrame(()=>{galleryLayoutFrame=0;layoutPhotoRows();updateScrollDate();});
}
function layoutPhotoRows(){
  const mobile=matchMedia('(max-width:760px)').matches,gap=mobile?5:7;
  const height=parseFloat(getComputedStyle($('gallery')).getPropertyValue('--photo-height'))||190;
  for(const section of $('gallery').children){
    const container=section.querySelector('.day-images'),available=container.clientWidth;if(!available)continue;
    const groups=[];let row=[],used=0;
    for(const card of section.photoCards||[]){
      const ratio=Number(card.style.getPropertyValue('--image-ratio'))||.75;
      const width=mobile?(available-gap*2)/3:Math.min(320,Math.max(108,height*ratio));
      if(row.length&&used+gap+width>available+.1){groups.push(row);row=[];used=0;}
      used+=(row.length?gap:0)+width;card.style.width=width+'px';row.push(card);
    }
    if(row.length)groups.push(row);
    const rows=groups.map((cards,index)=>{const el=container.children[index]||element('div','photo-row');reconcileChildren(el,cards);return el;});
    reconcileChildren(container,rows);
  }
}
function updateScrollDate(){
  const root=$('library-results'),label=$('scroll-date'),top=root.getBoundingClientRect().top;
  const section=[...$('gallery').children].find(el=>el.getBoundingClientRect().bottom>top+12);
  const overflow=root.scrollHeight-root.clientHeight;
  label.hidden=!section||overflow<=1;if(label.hidden)return;
  const day=section.dataset.day;label.textContent=day.slice(8)+'/'+day.slice(5,7)+'/'+day.slice(0,4);
  const thumb=Math.max(28,root.clientHeight*root.clientHeight/root.scrollHeight);
  label.style.top=Math.max(12,Math.min(root.clientHeight-12,root.scrollTop/overflow*(root.clientHeight-thumb)+thumb/2))+'px';
}
let scrollDateTimer;
let galleryScrollFrame=0;
$('library-results').addEventListener('scroll',()=>{$('scroll-date').classList.add('scrolling');clearTimeout(scrollDateTimer);scrollDateTimer=setTimeout(()=>$('scroll-date').classList.remove('scrolling'),700);if(!galleryScrollFrame)galleryScrollFrame=requestAnimationFrame(()=>{galleryScrollFrame=0;updateScrollDate();});},{passive:true});
$('search').addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{state.query=$('search').value.trim();state.selected.clear();state.allSelected=false;loadImages(true,false,true);},300);});
document.querySelectorAll('[data-rating]').forEach(button=>button.addEventListener('click',()=>changeFilter(button.dataset.rating)));
$('reveal-r18').addEventListener('click',()=>{const reveal=$('gallery').classList.toggle('reveal');$('reveal-r18').setAttribute('aria-pressed',String(reveal));});
$('load-more').addEventListener('click',()=>loadImages(false,true));$('refresh').addEventListener('click',()=>{loadImages(true);refreshStats(false,true);});
function applyFilters(){
  state.selected.clear();state.allSelected=false;$('filter-count').textContent=state.filterTags.size+state.filterTypes.size || '';loadImages(true,false,true);
}
async function loadTags(){
  const query=$('filter-tag-search').value.trim(),data=await api('/v1/filters?q='+encodeURIComponent(query));if(query!==$('filter-tag-search').value.trim())return;
  state.tagsLoaded=Date.now();state.tagOptions=data.tags;renderTagOptions();
}
function renderTagOptions(){
  const list=$('filter-tags');list.replaceChildren();
  for(const item of state.tagOptions){const label=element('label'),check=element('input'),name=element('span','',item.tag);name.title=item.tag;check.type='checkbox';check.value=item.tag;check.checked=state.filterTags.has(item.tag);
    check.addEventListener('change',()=>{if(check.checked&&state.filterTags.size>=30){check.checked=false;toast('Chọn tối đa 30 tag.');return;}if(check.checked)state.filterTags.add(item.tag);else state.filterTags.delete(item.tag);applyFilters();});
    label.append(check,name,element('small','',number(item.count)));list.append(label);
  }
  if(!state.tagOptions.length)list.append(element('p','', 'Chưa có tag phù hợp.'));
}
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
  document.body.dataset.view='library';
  $('nav-library').classList.add('selected');
}
function toggleUpload(open){
  if(!open&&$('upload-settings').matches(':popover-open'))$('upload-settings').hidePopover();
  $('upload-panel').hidden=!open;$('show-upload').setAttribute('aria-expanded',String(open));
  if(open)positionUploadPanel();
}
function focusUpload(){toggleUpload(true);$('choose-files').focus({preventScroll:true});}
$('show-upload').addEventListener('click',()=>{if($('upload-panel').hidden)focusUpload();else toggleUpload(false);});
$('empty-upload').addEventListener('click',focusUpload);
$('close-upload').addEventListener('click',()=>{toggleUpload(false);$('show-upload').focus({preventScroll:true});});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.defaultPrevented&&!document.querySelector('dialog[open],:popover-open')&&!$('upload-panel').hidden){toggleUpload(false);$('show-upload').focus({preventScroll:true});}});

function expandSearch(open){$('library-search').classList.toggle('expanded',open);$('toggle-search').setAttribute('aria-expanded',String(open));}
$('toggle-search').addEventListener('click',()=>{const open=!$('library-search').classList.contains('expanded');expandSearch(open);if(open)$('search').focus();});
$('search').addEventListener('blur',()=>{if(!$('search').value)expandSearch(false);});
$('search').addEventListener('keydown',event=>{if(event.key==='Escape'){$('search').blur();expandSearch(false);$('toggle-search').focus();}});

let swipeStart=null,suppressClickUntil=0;
$('library-results').addEventListener('touchstart',event=>{
  swipeStart=matchMedia('(max-width:760px)').matches&&event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY,at:Date.now()}:null;
},{passive:true});
$('library-results').addEventListener('touchcancel',()=>swipeStart=null,{passive:true});
$('library-results').addEventListener('touchend',event=>{
  const start=swipeStart;swipeStart=null;if(!start||event.touches.length||!event.changedTouches.length||state.bulkBusy||state.selectionBusy)return;
  const dx=event.changedTouches[0].clientX-start.x,dy=event.changedTouches[0].clientY-start.y;
  if(Date.now()-start.at>700||Math.abs(dx)<60||Math.abs(dx)<Math.abs(dy)*1.6)return;
  suppressClickUntil=Date.now()+450;
  const tabs=['','safe','r18','unknown'],next=tabs.indexOf(state.rating)+(dx<0?1:-1);if(next>=0&&next<tabs.length)changeFilter(tabs[next]);
},{passive:true});
$('library-results').addEventListener('click',event=>{if(Date.now()<suppressClickUntil){event.preventDefault();event.stopImmediatePropagation();}},true);

function selectOrOpen(id,event){
  if(state.bulkBusy||state.selectionBusy)return;
  if(!state.selected.size||event?.ctrlKey){openEditor(id);return;}
  if(state.selected.has(id))state.selected.delete(id);else state.selected.add(id);
  state.allSelected=false;renderGallery();
}

function renderSelection(){
  const count=state.selected.size,busy=state.bulkBusy||state.selectionBusy;
  $('selection-count').textContent=`(${number(count)})`;$('selection-count').title=state.bulkBusy?state.bulkLabel || 'Đang xử lý…':'';
  $('select-all').checked=state.selectionBusy||state.allSelected&&count>0;$('select-all').indeterminate=!state.selectionBusy&&!state.allSelected&&count>0;
  $('select-all').disabled=busy;$('bulk-delete').disabled=busy||!count;$('bulk-edit-button').disabled=busy||!count;
  $('bulk-ai').hidden=state.rating!=='unknown';$('bulk-ai').disabled=busy||!count||!state.aiAvailable;
  document.querySelectorAll('.image-select input').forEach(input=>input.disabled=busy);
  $('gallery').classList.toggle('selecting',count>0);
  for(const thumb of document.querySelectorAll('#gallery .thumb'))thumb.setAttribute('aria-label',(count?'Chọn ':'Xem chi tiết ')+(state.images.get(thumb.closest('.card').dataset.id)?.title || 'ảnh'));
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
function setAiBatch(id){state.aiBatchId=id;state.aiResult=null;state.aiDismissedSession=null;}
async function refreshAiProgress(){await refreshStats(true,true);}
let aiRenderTimer;
function renderAiProgress(){
  clearTimeout(aiRenderTimer);
  const p=state.aiProgress,waiting=Boolean(p?.next_retry_at),paused=p?.phase==='paused';
  const active=Boolean(state.aiEnqueueing||p&&p.presentation!=='inline'&&(['active','retry_wait','paused'].includes(p.phase)||waiting)&&(p.total||p.enrolling_until>Date.now()));
  const result=!active&&p&&p.presentation!=='inline'&&(p.failed||p.cancelled)&&state.aiDismissedSession!==p.session_id?p:null;
  state.aiResult=result?{batchId:p.id,progress:p}:null;
  $('ai-progress').hidden=!active;$('ai-result').hidden=!result;
  const reason=p?.stop_reason==='providers_exhausted'?' · Các API đã nghỉ hết trong phiên này':p?.stop_reason==='retry_limit'?' · Đã đạt giới hạn thử lại an toàn':'';
  $('ai-result-summary').textContent=result?`Phiên AI đã kết thúc · ${number(result.done)} thành công${result.failed?' · '+number(result.failed)+' lỗi':''}${result.cancelled?' · '+number(result.cancelled)+' đã huỷ':''}${reason}`:'';
  $('dismiss-ai').disabled=state.aiControlBusy;
  $('pause-ai').textContent=paused?'Tiếp tục':'Tạm dừng';$('pause-ai').dataset.action=paused?'resume':'pause';
  $('pause-ai').disabled=state.aiControlBusy||!active;$('cancel-ai').disabled=state.aiControlBusy||!active;
  $('retry-ai').hidden=!result;$('retry-ai').disabled=state.aiControlBusy||!state.aiAvailable;$('retry-ai').textContent='Chạy lại lỗi';
  $('retry-ai').title='Tạo phiên mới chỉ gồm ảnh lỗi hoặc đã huỷ còn cần phân loại';
  const progress=$('ai-progress-bar');
  if(p?.total){
    const finished=p.done+p.failed+p.skipped+(p.cancelled||0);progress.max=p.total;progress.value=finished;
    $('ai-summary').textContent=`AI gắn tag${p.phase==='done'?' · hoàn thành':paused?' · tạm dừng':''} · đã xử lý ${finished}/${p.total}`;
    $('ai-detail').textContent=`${p.done} thành công · ${p.running} đang chạy · ${p.pending} chờ${p.failed?' · '+p.failed+' lỗi':''}${p.cancelled?' · '+p.cancelled+' đã huỷ':''}${p.skipped?' · '+p.skipped+' bỏ qua':''}`;
    $('ai-progress').classList.toggle('has-errors',p.failed>0);
  }else{
    progress.removeAttribute('value');progress.max=1;$('ai-progress').classList.remove('has-errors');
    $('ai-summary').textContent='Đang chuẩn bị phiên AI…';$('ai-detail').textContent='Ảnh đã lưu sẽ được xử lý trên máy chủ.';
  }
  const providers=(p?.providers||[]).filter(provider=>provider.enabled&&!provider.disabled);
  const ready=providers.length?Math.min(...providers.map(provider=>Math.max(provider.cooldown_until||0,provider.global_cooldown_until||0))):0;
  $('ai-control-note').textContent=state.aiControlBusy?'Đang cập nhật…':paused?'Đã tạm dừng; ảnh đang chạy có thể hoàn tất.':waiting?p.next_retry_at>Date.now()?`Phiên mới cho ảnh lỗi sau ${Math.ceil((p.next_retry_at-Date.now())/1000)} giây.`:'Đã hết thời gian nghỉ; chờ máy chủ tiếp tục.':ready>Date.now()&&!p?.running?`API đang nghỉ khoảng ${Math.ceil((ready-Date.now())/1000)} giây.`:'Mỗi ảnh tối đa 10 giây · phiên chạy trên máy chủ.';
  if(active&&!document.hidden&&(waiting||ready>Date.now()))aiRenderTimer=setTimeout(renderAiProgress,1000);
}
$('dismiss-ai').addEventListener('click',()=>{state.aiDismissedSession=state.aiProgress?.session_id;renderAiProgress();});
async function controlAi(action){
  if(state.aiControlBusy)return;
  const batchId=state.aiProgress?.id||state.aiResult?.batchId;
  if(action==='cancel'&&!confirm('Huỷ các ảnh chưa xử lý xong của phiên AI đang chạy? Ảnh và tag đã lưu vẫn được giữ nguyên.'))return;
  state.aiControlBusy=true;renderAiProgress();
  try{
    const data=await api('/v1/analysis/control',{method:'POST',body:JSON.stringify({action,...(batchId?{batch_id:batchId}:{})})});
    state.aiProgress=data.progress;state.aiBatchId=data.progress?.id||null;state.aiDismissedSession=null;
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
  clearTimeout(duplicateTimer);
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
    finally{
      for(const item of items)if(item.status==='duplicate'){
        if(item.preview)URL.revokeObjectURL(item.preview);item.preview=null;item.file=null;
      }
      state.checking--;renderQueue();
      // A selection containing only existing images has no upload to finish.
      // Show its duplicate count briefly, then clear the completed selection.
      if(!state.checking)duplicateTimer=setTimeout(clearFinishedUploadDuplicates,5000);
    }
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
const queueRows=new Map();
function clearFinishedUploadDuplicates(){
  if(state.pumping||state.starting||state.checking||state.queue.some(item=>item.status!=='duplicate'))return;
  state.queue=[];for(const [id,status]of state.uploadResults)if(status==='duplicate')state.uploadResults.delete(id);
  renderQueue();
}
function renderQueue(){
  const list=$('queue-list');const visible=state.queue.filter(x=>x.status!=='duplicate');$('queue-count').textContent=number(visible.length);
  $('upload-badge').hidden=!visible.length;$('upload-badge').textContent=number(visible.length);$('show-upload').classList.toggle('uploading',state.pumping&&!state.paused);
  const errors=state.queue.filter(x=>x.status==='error').length,pending=state.queue.filter(x=>x.status==='queued').length,active=state.queue.filter(x=>x.status==='uploading').length;
  const ids=new Set(visible.map(item=>item.id));
  for(const [id,row] of queueRows)if(!ids.has(id)){row.remove();queueRows.delete(id);}
  const empty=list.querySelector('.queue-empty');
  if(!visible.length){if(!empty)list.append(element('p','queue-empty','Ảnh bạn chọn sẽ xuất hiện ở đây.'));}
  else empty?.remove();
  for(const item of visible){
    let row=queueRows.get(item.id);
    if(!row){
      row=element('div');row.dataset.id=item.id;
      if(item.preview){const img=element('img');img.src=item.preview;img.alt='';img.loading='lazy';row.append(img);}else{const mark=element('span','queue-file-icon');mark.append(icon('link'));row.append(mark);}
      const text=element('div');text.append(element('strong','',item.name),element('small'));const progress=element('progress');progress.max=100;text.append(progress);
      const remove=element('button','','×');remove.type='button';remove.setAttribute('aria-label','Bỏ '+item.name+' khỏi hàng đợi');remove.addEventListener('click',()=>{state.queue=state.queue.filter(x=>x!==item);if(item.preview)URL.revokeObjectURL(item.preview);renderQueue();});row.append(text,remove);queueRows.set(item.id,row);list.append(row);
    }
    const className='queue-item '+item.status;if(row.className!==className)row.className=className;
    const description=item.status==='checking'?'Đang kiểm tra ảnh trùng…':item.status==='error'?item.error:item.status==='uploading'?(item.waiting?'Đang chờ lượt tải…':item.progress===100?'Đang lưu trên máy chủ…':`Đang tải… ${item.progress}%`):(item.file?bytes(item.file.size)+' · Chờ tải':item.link?.source?'Link '+item.link.source+' · Ưu tiên ảnh gốc':'Link ảnh · Chờ tải');
    const label=row.querySelector('small');if(label.textContent!==description)label.textContent=description;const progress=row.querySelector('progress');progress.hidden=item.status!=='uploading'||Boolean(item.waiting);progress.value=item.progress;row.querySelector('button').disabled=item.status==='uploading';
  }
  const queued=new Map(state.queue.map(item=>[item.id,item.status]));
  const batch=state.uploadIds.size?[...state.uploadIds].map(id=>state.uploadResults.get(id)||queued.get(id)).filter(Boolean):[...queued.values()];
  const saved=batch.filter(status=>status==='done').length,failed=batch.filter(status=>status==='error').length,total=batch.filter(status=>status!=='duplicate').length,duplicates=state.queue.filter(item=>item.status==='duplicate').length+[...state.uploadResults.values()].filter(status=>status==='duplicate').length;
  $('queue-progress').hidden=!total||!state.pumping&&!state.starting;$('queue-summary').textContent=`${saved}/${total} đã lưu${failed?' · '+failed+' lỗi':''}${active?' · '+active+' đang tải':''}${state.uploadRecovery?.failures?' · Đang chờ dịch vụ phục hồi':''}`;$('batch-progress').max=Math.max(1,total);$('batch-progress').value=saved;
  $('duplicate-summary').hidden=!duplicates&&!state.checking;$('duplicate-summary').textContent=`${duplicates?`${duplicates} ảnh trùng đã bỏ qua. `:''}${state.checking?'Đang kiểm tra ảnh…':''}`;
  $('start-upload').disabled=!pending||state.starting||state.checking>0;$('start-upload').textContent=state.checking?'Đang kiểm tra ảnh…':state.starting?'Đang chuẩn bị…':state.pumping&&!state.paused?'Đang tải lên…':state.paused && active?'Tiếp tục tải lên':`Tải ${pending || ''} ảnh lên`.replace('  ',' ');
  $('pause-upload').hidden=!state.pumping || state.paused;$('retry-errors').hidden=!errors||state.pumping;
  for(const id of ['use-ai','upload-rating','upload-type','upload-tags','upload-source'])$(id).disabled=state.pumping&&!state.paused||id==='use-ai'&&!state.aiAvailable;
}
function fileUpload(item){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/v1/images');xhr.responseType='json';xhr.timeout=90000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable){item.progress=Math.round(event.loaded/event.total*100);const row=$('queue-list').querySelector(`[data-id="${item.id}"]`);if(row){row.querySelector('small').textContent=item.progress===100?'Đang lưu trên máy chủ…':`Đang gửi… ${item.progress}%`;row.querySelector('progress').value=item.progress;}}};
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else{const error=new Error(xhr.response?.error?.message || (xhr.status?`Tải ảnh chưa thành công (HTTP ${xhr.status}).`:'Không nhận được phản hồi từ máy chủ.'));error.status=xhr.status;error.code=xhr.response?.error?.code;error.retryAfter=Number(xhr.getResponseHeader('retry-after')) || 2;if(xhr.status===401)showLogin('Phiên đã hết hạn. Vui lòng đăng nhập lại.');pauseForQuota(error);reject(error);}};
    xhr.onerror=()=>reject(new Error('Mất kết nối. Bấm thử lại khi mạng ổn định.'));xhr.ontimeout=()=>reject(new Error('Quá thời gian chờ. Bạn có thể thử lại file này.'));
    const metadata={...item.options,tags:item.options.tags.join(','),filename:item.file.name};
    xhr.setRequestHeader('content-type','application/octet-stream');xhr.setRequestHeader('x-waku-upload-metadata',encodeURIComponent(JSON.stringify(metadata)));xhr.send(item.file);
  });
}
async function worker(){
  // A service outage affects the whole queue, not each individual image.
  // Share one recovery gate between both workers and allow only one probe.
  const recovery=state.uploadRecovery ||= {failures:0,nextAt:0,probing:false};
  while(!state.paused && state.authenticated){
    const item=state.queue.find(x=>x.status==='queued');if(!item)return;item.options??=structuredClone(state.activeOptions || options());state.uploadIds.add(item.id);item.status='uploading';item.progress=0;renderQueue();renderGallery();
    try{
      if(item.retryAfter){await sleep(Math.min(30000,Math.max(2,item.retryAfter)*1000));item.retryAfter=0;if(state.paused||!state.authenticated){item.status='queued';renderQueue();return;}}
      let data,busySince=0,busyAttempts=0;
      while(true){
        let probe=false;
        while(recovery.failures&&!state.paused&&state.authenticated){
          item.waiting=true;renderQueue();
          const remaining=recovery.nextAt-Date.now();
          if(remaining>0){await sleep(Math.min(1000,remaining));continue;}
          if(recovery.probing){await sleep(500);continue;}
          recovery.probing=true;probe=true;break;
        }
        if(state.paused||!state.authenticated){item.status='queued';item.waiting=false;renderQueue();return;}
        item.waiting=false;
        try{
          data=item.file?await fileUpload(item):await api('/v1/images',{method:'POST',body:JSON.stringify({url:item.url,...item.options,source_url:item.options.source_url || item.url})});
          if(probe){recovery.failures=0;recovery.nextAt=0;recovery.probing=false;}
          break;
        }
        catch(error){
          if(probe)recovery.probing=false;
          const transient=error.code!=='d1_daily_limit'&&(error.status===429&&error.code!=='busy'||[500,502,503,504,520,521,522,523,524].includes(error.status));
          if(transient&&(state.paused||!state.authenticated)){item.status='queued';item.waiting=false;renderQueue();return;}
          if(transient&&!state.paused&&state.authenticated){
            recovery.failures++;
            if(recovery.failures>=6){
              state.paused=true;item.status='queued';item.waiting=false;
              toast('Dịch vụ vẫn chưa phục hồi. Đã tạm dừng và giữ ảnh chờ; bấm tiếp tục để thử lại.');renderQueue();return;
            }
            const delay=Math.max((error.retryAfter||0)*1000,Math.min(120000,5000*2**(recovery.failures-1)))+Math.random()*1000;
            recovery.nextAt=Math.max(recovery.nextAt,Date.now()+delay);item.waiting=true;renderQueue();continue;
          }
          // The API has not accepted the file when its upload slots are busy.
          // Waiting for a slot is not a failed upload attempt.
          if(error.status!==429||error.code!=='busy'||state.paused||!state.authenticated)throw error;
          busySince ||= Date.now();if(Date.now()-busySince>=150000)throw error;
          const delay=Math.min(10000,Math.max(2,error.retryAfter||2)*1000*2**Math.min(busyAttempts++,2))+Math.random()*500;
          item.waiting=true;renderQueue();
          for(let waited=0;waited<delay&&!state.paused&&state.authenticated;waited+=1000)await sleep(Math.min(1000,delay-waited));
          item.waiting=false;if(state.paused||!state.authenticated){item.status='queued';renderQueue();return;}renderQueue();
        }
      }
      state.uploadResults.set(item.id,data.duplicate?'duplicate':'done');
      state.queue=state.queue.filter(queued=>queued!==item);if(item.preview)URL.revokeObjectURL(item.preview);
      if(state.rating==='' || state.rating==='unknown'&&!isClassified(data.image) || state.rating===data.image.rating&&isClassified(data.image))state.images.set(data.image.id,data.image);
    }catch(error){item.status='error';item.error=error.message || 'Tải ảnh chưa thành công.';item.retryAfter=error.status===429?error.retryAfter:0;}
    renderQueue();renderGallery();
    if(!state.paused&&state.authenticated)await sleep(400);
  }
}
async function start(){
  if(!state.authenticated||state.starting||state.checking)return;
  const selected=options();if(selected.tags.length>30){toast('Tối đa 30 tag chung.');return;}
  if(!$('upload-source').checkValidity()){toast('Link nguồn chưa hợp lệ.');return;}
  const resume=state.queue.some(item=>state.uploadIds.has(item.id)&&['queued','uploading'].includes(item.status));
  if(!resume){state.uploadIds=new Set(state.queue.filter(item=>item.status==='queued').map(item=>item.id));state.uploadResults.clear();}
  state.starting=true;state.filterTags.clear();state.filterTypes.clear();document.querySelectorAll('#filter-panel input[type=checkbox]').forEach(input=>input.checked=false);$('filter-count').textContent='';changeFilter('unknown',true);setView('library');renderQueue();let ownsWorkers=false;
  try{
    if(!selected.manual){
      if(!resume || !state.aiBatchId){const {id}=await api('/v1/analysis-batches',{method:'POST',body:'{}'});setAiBatch(id);}
      selected.batch_id=state.aiBatchId;
    }else if(!resume)setAiBatch(null);
    state.activeOptions=selected;for(const item of state.queue)if(item.status==='queued')item.options=structuredClone(selected);
    if(state.uploadRecovery?.failures>=6)state.uploadRecovery={failures:0,nextAt:0,probing:false};
    state.paused=false;state.starting=false;if(state.pumping){renderQueue();return;}state.pumping=true;ownsWorkers=true;renderQueue();renderAiProgress();schedulePoll();
    await Promise.all([worker(),worker()]);
    // Retry only failures from this batch, once, after every original upload
    // has finished. Validation errors and an explicitly paused queue stay put.
    if(!state.paused&&state.authenticated){
      const retry=state.queue.filter(item=>state.uploadIds.has(item.id)&&item.status==='error'&&!item.autoRetried&&!item.needsCheck);
      for(const item of retry){item.autoRetried=true;item.status='queued';item.error='';item.progress=0;}
      if(retry.length){renderQueue();await Promise.all([worker(),worker()]);}
    }
  }catch(error){
    // Batch creation can fail before the workers start. Keep each file and show
    // the actual failure so Retry can restart it once the service is available.
    for(const item of state.queue)if(state.uploadIds.has(item.id)&&item.status==='queued'){
      item.status='error';item.error=error.message || 'Chưa thể chuẩn bị tải ảnh.';
    }
    state.paused=true;toast(error.message);
  }finally{
    state.starting=false;if(ownsWorkers)state.pumping=false;
    if(ownsWorkers)clearFinishedUploadDuplicates();
    renderQueue();renderGallery();refreshStats(true,true);
  }
}
$('start-upload').addEventListener('click',start);$('pause-upload').addEventListener('click',()=>{state.paused=true;renderQueue();toast('Đã tạm dừng. Các file đang tải sẽ hoàn tất trước.');});
$('retry-errors').addEventListener('click',async()=>{const checking=[];for(const item of state.queue)if(item.status==='error'){item.status=item.needsCheck?'checking':'queued';item.error='';item.progress=0;item.autoRetried=false;item.retryAfter=0;if(item.needsCheck)checking.push(item);}if(checking.length)await checkUploads(checking);start();});
window.addEventListener('beforeunload',event=>{if(state.pumping || state.queue.some(x=>x.status==='queued')){event.preventDefault();event.returnValue='';}});

const galleryImageIds=()=>[...state.images.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at)||b.id.localeCompare(a.id)).map(image=>image.id);
function toggleEditorDetails(open){
  $('editor').classList.toggle('details-open',open);$('toggle-editor-details').setAttribute('aria-expanded',String(open));
}
let editorAiTimer,editorAiController,editorAiSubmitting=null,editorFields={};
const editorFieldValues=image=>({'edit-title':image.title,'edit-tags':image.tags.join(', '),'edit-rating':image.rating,'edit-type':image.image_type||'','edit-source':image.source_url||''});
function fillEditorFields(image,preserveEdits=false){
  for(const [id,value]of Object.entries(editorFieldValues(image))){if(!preserveEdits||$(id).value===editorFields[id])$(id).value=value;editorFields[id]=value;}
}
function stopEditorAiPolling(){clearTimeout(editorAiTimer);editorAiTimer=null;editorAiController?.abort();editorAiController=null;}
function scheduleEditorAiPolling(){
  if(editorAiTimer||editorAiController||!$('editor').open||!state.editing)return;
  const id=state.editing.id,version=state.editorVersion;
  editorAiTimer=setTimeout(async()=>{
    editorAiTimer=null;const controller=new AbortController();editorAiController=controller;
    try{
      const {image}=await api('/v1/images/'+id,{signal:AbortSignal.any([controller.signal,AbortSignal.timeout(15000)])});
      if(!$('editor').open||state.editorVersion!==version||state.editing?.id!==id)return;
      state.editing=image;state.images.set(id,image);fillEditorFields(image,true);
      if(!['pending','running'].includes(image.ai_status)){
        const failed=!isClassified(image)&&image.ai_status==='failed';$('edit-ai-error').hidden=!failed;$('edit-ai-error').textContent=failed?analysisError(image.ai_error):'';
        state.galleryDirty=true;renderGallery();refreshStats(false,true);
      }
    }catch(error){if(!controller.signal.aborted&&state.editorVersion===version)$('editor-ai-status').textContent='Đang chờ cập nhật từ máy chủ…';}
    finally{
      if(editorAiController===controller)editorAiController=null;
      if($('editor').open&&state.editorVersion===version&&state.editing?.id===id)updateViewerAi();
    }
  },2000);
}
function updateViewerAi(){
  const image=state.editing&&(state.images.get(state.editing.id)||state.editing);if(!image)return;
  const busy=editorAiSubmitting===image.id||['pending','running'].includes(image.ai_status);
  $('viewer-ai').hidden=isClassified(image);$('viewer-ai').disabled=!state.aiAvailable||busy;
  $('analyze-image').hidden=isClassified(image);$('analyze-image').disabled=!state.aiAvailable||busy;
  $('analyze-image').textContent=busy?'✦ Đang tạo thông tin…':'✦ AI gắn tag';
  $('editor').classList.toggle('ai-working',busy);$('editor-ai-status').hidden=!busy;
  if(busy){$('editor-ai-status').textContent='AI đang tạo thông tin…';scheduleEditorAiPolling();}
}
function updateEditorNavigation(){
  updateViewerAi();
  const ordered=galleryImageIds(),last=ordered.indexOf(state.editorIds.at(-1));
  if(last>=0){const known=new Set(state.editorIds);state.editorIds.push(...ordered.slice(last+1).filter(id=>!known.has(id)));}
  const index=state.editorIds.indexOf(state.editing?.id),busy=state.editorNavigationBusy||$('save-image').disabled;
  $('editor-prev').disabled=busy||index<=0;
  $('editor-next').disabled=busy||index<0||index===state.editorIds.length-1&&!state.cursor;
  $('editor').setAttribute('aria-busy',String(state.editorNavigationBusy));
}
async function openEditor(id,navigating=false){
  stopEditorAiPolling();
  const version=++state.editorVersion;
  try{
    const image=state.images.get(id)||(await api('/v1/images/'+id)).image;if(version!==state.editorVersion)return;
    if(!navigating||!$('editor').open){state.editorIds=galleryImageIds();if(!state.editorIds.includes(id))state.editorIds.push(id);toggleEditorDetails(false);}
    state.editing=image;state.images.set(id,image);
    $('edit-preview').src='/media/'+encodeURIComponent(id);$('edit-preview').alt=image.title||'Ảnh đang xem';
    fillEditorFields(image);
    $('analyze-image').hidden=isClassified(image);$('analyze-image').disabled=['pending','running'].includes(image.ai_status);
    $('edit-meta').textContent=`${bytes(image.size)} · ${image.mime_type.replace('image/','').toUpperCase()} · ${new Date(image.created_at).toLocaleDateString('vi-VN')}${image.original_name?' · '+image.original_name:''}`;
    const failed=!isClassified(image)&&image.ai_status==='failed';$('edit-ai-error').hidden=!failed;$('edit-ai-error').textContent=failed?analysisError(image.ai_error):'';
    $('edit-form').scrollTop=0;
    if(!$('editor').open){$('editor').showModal();$('editor-close').focus({preventScroll:true});}
    updateEditorNavigation();
  }catch(error){if(version===state.editorVersion)toast(error.message);}
}
async function navigateEditor(direction){
  if(!$('editor').open||!state.editing||state.editorNavigationBusy||$('save-image').disabled)return;
  const current=state.editing.id,scope=listParameters().toString(),version=state.editorVersion;let index=state.editorIds.indexOf(current);
  if(index<0||direction<0&&index===0)return;
  const run=++state.editorNavigationRun;state.editorNavigationBusy=true;updateEditorNavigation();
  try{
    if(direction>0&&index===state.editorIds.length-1&&state.cursor){
      const before=state.cursor;await loadImages(false,true);
      if(!$('editor').open||state.editing?.id!==current||version!==state.editorVersion||scope!==listParameters().toString())return;
      const known=new Set(state.editorIds);state.editorIds.push(...galleryImageIds().filter(id=>!known.has(id)));
      if(state.editorIds.length===index+1&&state.cursor===before){toast($('list-status').textContent||'Chưa tải được ảnh tiếp theo.');return;}
    }
    const next=state.editorIds[index+direction];if(next&&$('editor').open&&state.editing?.id===current&&version===state.editorVersion)await openEditor(next,true);
  }finally{if(run===state.editorNavigationRun){state.editorNavigationBusy=false;updateEditorNavigation();}}
}
$('editor-prev').addEventListener('click',()=>navigateEditor(-1));$('editor-next').addEventListener('click',()=>navigateEditor(1));
$('toggle-editor-details').addEventListener('click',()=>toggleEditorDetails(!$('editor').classList.contains('details-open')));
$('close-editor-details').addEventListener('click',()=>{toggleEditorDetails(false);$('toggle-editor-details').focus({preventScroll:true});});
document.addEventListener('keydown',event=>{
  if(!$('editor').open||document.querySelectorAll('dialog[open]').length>1||event.defaultPrevented||event.isComposing||event.altKey||event.ctrlKey||event.metaKey)return;
  if(event.target.closest('input,textarea,select,[contenteditable=true]'))return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();navigateEditor(event.key==='ArrowLeft'?-1:1);}
});
let editorSwipe=null;
$('editor-image').addEventListener('touchstart',event=>{editorSwipe=matchMedia('(max-width:760px)').matches&&event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY,at:Date.now()}:null;},{passive:true});
$('editor-image').addEventListener('touchcancel',()=>editorSwipe=null,{passive:true});
$('editor-image').addEventListener('touchend',event=>{
  const start=editorSwipe;editorSwipe=null;if(!start||event.touches.length||!event.changedTouches.length)return;
  const dx=event.changedTouches[0].clientX-start.x,dy=event.changedTouches[0].clientY-start.y;
  if(Date.now()-start.at<=700&&Math.abs(dx)>=60&&Math.abs(dx)>Math.abs(dy)*1.6)navigateEditor(dx<0?1:-1);
},{passive:true});
$('editor').addEventListener('close',()=>{if($('editor').open)return;stopEditorAiPolling();state.editorVersion++;state.editorNavigationRun++;state.editing=null;state.editorIds=[];state.editorNavigationBusy=false;$('editor').classList.remove('ai-working');toggleEditorDetails(false);});
$('editor').addEventListener('cancel',event=>{if(matchMedia('(max-width:760px)').matches&&$('editor').classList.contains('details-open')){event.preventDefault();toggleEditorDetails(false);$('toggle-editor-details').focus({preventScroll:true});}});
$('editor-close').addEventListener('click',()=>$('editor').close());
let outsideEditor=false;
const editorOutside=event=>{const box=$('editor-surface').getBoundingClientRect();return event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom;};
$('editor').addEventListener('pointerdown',event=>outsideEditor=event.target===$('editor')&&editorOutside(event));
$('editor').addEventListener('click',event=>{if(outsideEditor&&event.target===$('editor')&&editorOutside(event))$('editor').close();outsideEditor=false;});
$('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();const image=state.editing,version=state.editorVersion;if(!image)return;$('save-image').disabled=true;updateEditorNavigation();
  try{const {image:updated}=await api('/v1/images/'+image.id,{method:'PATCH',body:JSON.stringify({title:$('edit-title').value,tags:tagList($('edit-tags').value),rating:$('edit-rating').value,image_type:$('edit-type').value || null,source_url:$('edit-source').value.trim() || null})});state.images.set(image.id,updated);state.selected.delete(image.id);renderGallery();if(version===state.editorVersion&&state.editing?.id===image.id)$('editor').close();toast('Đã lưu thay đổi.');loadImages(false);refreshStats(false,true);}
  catch(error){toast(error.message);}finally{$('save-image').disabled=false;updateEditorNavigation();}
});
const analyzeEditingImage=async()=>{
  if(!state.editing||editorAiSubmitting)return;
  const image=state.editing,id=image.id,version=state.editorVersion,previous=image.ai_status;
  if(['pending','running'].includes(previous))return;
  editorAiSubmitting=id;image.ai_status='pending';$('edit-ai-error').hidden=true;updateViewerAi();
  if(matchMedia('(max-width:760px)').matches)toggleEditorDetails(true);
  try{
    await api('/v1/images/'+id+'/analyze',{method:'POST',body:JSON.stringify({inline:true})});state.galleryDirty=true;
  }catch(error){image.ai_status=previous;if(state.editorVersion===version){$('edit-ai-error').hidden=false;$('edit-ai-error').textContent=error.message;}}
  finally{editorAiSubmitting=null;if(state.editorVersion===version&&state.editing?.id===id)updateViewerAi();}
};
$('analyze-image').addEventListener('click',analyzeEditingImage);$('viewer-ai').addEventListener('click',analyzeEditingImage);
for(const id of ['copy-link','viewer-copy'])$(id).addEventListener('click',async()=>{if(!state.editing)return;try{await navigator.clipboard.writeText(location.origin+'/media/'+state.editing.id);toast('Đã sao chép link ảnh.');}catch{toast('Trình duyệt chưa cho phép sao chép. Bạn có thể mở ảnh để lấy link.');}});
for(const id of ['delete-image','viewer-delete'])$(id).addEventListener('click',()=>{if(state.editing)confirmDelete([state.editing.id]);});$('cancel-delete').addEventListener('click',()=>$('confirm-delete').close());
$('confirm-delete-button').addEventListener('click',async()=>{
  const ids=[...state.deleteTargets];if(!ids.length||state.bulkBusy)return;
  state.bulkBusy=true;$('confirm-delete-button').disabled=true;let next=0,done=0,failed=0;
  const remove=async()=>{while(next<ids.length){const id=ids[next++];try{
    try{await api('/v1/images/'+id,{method:'DELETE'});}catch(error){if(error.status!==404)throw error;}
    const image=state.images.get(id);if(image)thumbnails.forget(image);
    const card=state.cards.get(id);if(card){const img=card.querySelector('.thumb img');if(img)thumbnails.release(img);card.remove();state.cards.delete(id);}
    state.images.delete(id);state.selected.delete(id);done++;
  }catch{failed++;}state.bulkLabel=`Đã xóa ${done}/${ids.length}${failed?' · '+failed+' lỗi':''}`;$('confirm-delete-button').textContent=`Đang xóa ${done}/${ids.length}`;renderSelection();}};
  try{await Promise.all([remove(),remove()]);$('confirm-delete').close();if(state.editing&&ids.includes(state.editing.id)){$('editor').close();state.editing=null;}state.allSelected=false;await loadImages(false);refreshStats();toast(`Đã xóa ${done} ảnh${failed?'; '+failed+' ảnh lỗi, hãy thử lại.':'.'}`);}
  finally{state.deleteTargets=[];state.bulkBusy=false;state.bulkLabel='';$('confirm-delete-button').disabled=false;$('confirm-delete-button').textContent='Xóa ảnh';renderGallery();}
});
document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden && state.authenticated){if(Date.now()-state.lastStats>15000)refreshStats(true);else schedulePoll();}});
setupProviders({api,toast,onChange:enabled=>{
  const before=state.aiAvailable;state.aiAvailable=enabled;
  if(!enabled)$('use-ai').checked=false;else if(!before)$('use-ai').checked=true;
  renderQueue();renderSelection();updateViewerAi();
}});
api('/v1/session').then(enter).catch(error=>showLogin(['d1_daily_limit','database_unavailable','maintenance'].includes(error.code)?error.message:''));

setupKeys({api,toast});
