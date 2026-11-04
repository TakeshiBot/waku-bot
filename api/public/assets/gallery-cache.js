// Small previews are generated once per browser. Originals remain untouched
// and are used by the editor/download API. No extra Worker resizing service.
const CACHE='waku-gallery-thumbnails-v1',MAX_DISK=256;

async function preview(blob){
  if(typeof createImageBitmap!=='function')throw new Error('bitmap unavailable');
  const bitmap=await createImageBitmap(blob,{resizeWidth:384,resizeQuality:'medium'});
  try{
    const width=Math.min(384,bitmap.width),height=Math.max(1,Math.round(bitmap.height*width/bitmap.width));
    // Limit unusually tall images too; object-fit on the card still crops them.
    const scale=Math.min(1,768/height),canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
    return await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('preview failed')),'image/webp',.8));
  }finally{bitmap.close();}
}

export function videoPreview(src,analysis=false){
  return new Promise((resolve,reject)=>{
    const video=document.createElement('video');video.muted=true;video.playsInline=true;video.preload='metadata';
    let settled=false,capturing=false;
    const done=(error,blob)=>{if(settled)return;settled=true;clearTimeout(timer);video.onloadedmetadata=video.onloadeddata=video.onseeked=video.onerror=null;video.pause();video.removeAttribute('src');video.load();error?reject(error):resolve(blob);};
    const timer=setTimeout(()=>done(new Error('video preview timeout')),12000);
    const capture=()=>{if(settled||capturing)return;capturing=true;try{
      const scale=Math.min(1,(analysis?768:384)/video.videoWidth,768/video.videoHeight),width=Math.round(video.videoWidth*scale),height=Math.round(video.videoHeight*scale);
      if(!width||!height)throw new Error('video unavailable');
      const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;canvas.getContext('2d').drawImage(video,0,0,width,height);
      canvas.toBlob(blob=>blob?done(null,blob):done(new Error('video preview unavailable')),analysis?'image/jpeg':'image/webp',.8);
    }catch(error){done(error);}};
    video.onloadedmetadata=()=>{if(Number.isFinite(video.duration)&&video.duration>.2)video.currentTime=video.duration*.25;};
    video.onseeked=capture;video.onloadeddata=()=>{if(!video.seeking)capture();};
    video.onerror=()=>done(new Error('video unavailable'));video.src=src;
  });
}

export class GalleryThumbnails{
  constructor(root){
    this.items=new WeakMap();this.queue=[];this.running=0;this.pending=new Map();this.epoch=0;
    this.cache=globalThis.caches?.open(CACHE).catch(()=>null) || Promise.resolve(null);
    this.observer=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
      for(const entry of entries)if(entry.isIntersecting){this.observer.unobserve(entry.target);this.enqueue(entry.target);}
    },{root,rootMargin:'350px'}):null;
  }
  observe(img,image){
    if(this.items.has(img))return;
    this.items.set(img,{key:new URL('/__gallery_preview/'+encodeURIComponent(image.id)+'/'+encodeURIComponent(image.sha256 || 'original'),location.origin).href,src:'/media/'+encodeURIComponent(image.id),video:image.media_type==='video'||image.mime_type?.startsWith('video/'),epoch:this.epoch});
    if(this.observer)this.observer.observe(img);else this.enqueue(img);
  }
  enqueue(img){
    this.queue.push(img);this.pump();
  }
  pump(){
    while(this.running<2&&this.queue.length){
      const img=this.queue.shift(),item=this.items.get(img);
      if(!item)continue;
      if(!img.isConnected){this.observer?.observe(img);continue;}
      this.running++;
      this.load(item).then(blob=>{
        if(this.items.get(img)!==item||item.epoch!==this.epoch)return;
        img.src=URL.createObjectURL(blob);
      }).catch(()=>{
        // Native decoding remains a fallback for unsupported image formats.
        if(this.items.get(img)===item&&item.epoch===this.epoch&&!item.video)img.src=item.src;
      }).finally(()=>{this.running--;this.pump();});
    }
  }
  load(item){
    if(this.pending.has(item.key))return this.pending.get(item.key);
    const job=(async()=>{
      const cache=await this.cache;
      try{const hit=await cache?.match(item.key);if(hit)return await hit.blob();}catch{}
      let blob;
      if(item.video)blob=await videoPreview(item.src);
      else{
        const result=await fetch(item.src,{cache:'force-cache',signal:AbortSignal.timeout(20000)});
        if(!result.ok)throw new Error('image unavailable');
        blob=await preview(await result.blob());
      }
      if(item.epoch===this.epoch&&cache)try{
        await cache.put(item.key,new Response(blob,{headers:{'content-type':blob.type}}));
        const keys=await cache.keys();if(keys.length>MAX_DISK+16)await Promise.all(keys.slice(0,keys.length-MAX_DISK).map(key=>cache.delete(key)));
      }catch{} // Storage may be unavailable/full; the in-memory preview still works.
      return blob;
    })().finally(()=>this.pending.delete(item.key));
    this.pending.set(item.key,job);return job;
  }
  release(img){
    this.observer?.unobserve(img);this.items.delete(img);
    if(img.src.startsWith('blob:'))URL.revokeObjectURL(img.src);
    img.removeAttribute('src');
  }
  forget(image){
    const key=new URL('/__gallery_preview/'+encodeURIComponent(image.id)+'/'+encodeURIComponent(image.sha256 || 'original'),location.origin).href;
    this.cache.then(cache=>cache?.delete(key)).catch(()=>{});
  }
  clear(){
    this.epoch++;this.queue=[];this.observer?.disconnect();
    // Logout discards this library's persisted previews as well.
    this.cache.then(cache=>cache?.keys().then(keys=>Promise.all(keys.map(key=>cache.delete(key))))).catch(()=>{});
  }
}
