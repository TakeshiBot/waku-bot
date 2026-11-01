import {imageSize} from 'image-size';
import type {Env,ImageRow} from './types';

export interface Dimensions {width:number;height:number;}
export function imageDimensions(bytes:ArrayBuffer):Dimensions|null{
  try{
    let {width,height,orientation}=imageSize(new Uint8Array(bytes));
    if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<=0||height<=0)return null;
    if(orientation&&orientation>=5&&orientation<=8)[width,height]=[height,width];
    return {width,height};
  }catch{return null;}
}

export async function recordDimensions(env:Env,values:Array<Dimensions&{id:string}>){
  if(!values.length)return;
  // Ignore images deleted while their header was being read. Layout metadata
  // never changes tags, AI state, classification, timestamps or counters.
  await env.DB.prepare(`INSERT OR IGNORE INTO image_dimensions(image_id,width,height)
    SELECT i.id,json_extract(j.value,'$.width'),json_extract(j.value,'$.height')
    FROM json_each(?) j JOIN images i ON i.id=json_extract(j.value,'$.id')`)
    .bind(JSON.stringify(values)).run();
}

export async function dimensionsForRows(env:Env,rows:ImageRow[]){
  const known=await env.DB.prepare(`SELECT image_id,width,height FROM image_dimensions
    WHERE image_id IN (${rows.map(()=>'?').join(',')})`).bind(...rows.map(row=>row.id))
    .all<Dimensions&{image_id:string}>();
  const sizes=new Map<string,Dimensions>(known.results.map(size=>[size.image_id,size]));
  const missing=rows.filter(row=>!sizes.has(row.id)),found:Array<Dimensions&{id:string}>=[];
  let next=0;
  const read=async()=>{
    while(next<missing.length){
      const row=missing[next++];
      try{
        // At most two bounded header readers. No pixel decoding or full-file
        // download on the VPS/Worker, even when opening a page of old images.
        for(const limit of [65536,1048576]){
          const object=await env.IMAGES.get(row.object_key,{range:{offset:0,length:Math.min(row.size,limit)}});
          if(!object)break;
          const size=imageDimensions(await object.arrayBuffer());
          if(size){sizes.set(row.id,size);found.push({id:row.id,...size});break;}
          if(row.size<=limit)break;
        }
      }catch{ /* An unavailable original must not take the library page down. */ }
    }
  };
  if(missing.length){await Promise.all([read(),read()]);await recordDimensions(env,found);}
  return sizes;
}
