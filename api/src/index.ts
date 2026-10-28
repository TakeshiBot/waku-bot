import { boundedBody, equalSecret, fetchImage, metadata, tags } from './input';
import { choose, image, maxBytes, present, presentMany, remove, save, update } from './store';
import { processJobs, refreshAnalysisReceipt, webhook } from './telegram';
import {enqueueAnalysis,processAnalysis} from './analysis';
import { HttpError, type Env, type ImageRow } from './types';

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
function integer(value:string|null, fallback:number, max:number) {
  if (value===null) return fallback;
  if (!/^\d+$/.test(value) || Number(value)<1 || Number(value)>max) throw new HttpError(400,'invalid_limit',`Giới hạn phải từ 1 đến ${max}.`);
  return Number(value);
}
async function json(request:Request) {
  const body=await boundedBody(request,65536);
  try {
    const result=JSON.parse(new TextDecoder().decode(body));
    if (!result || typeof result!=='object' || Array.isArray(result)) throw new Error();
    return result as Record<string,unknown>;
  } catch {throw new HttpError(400,'invalid_json','JSON không hợp lệ.');}
}
function response(data:unknown, status=200) {
  return Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
}
async function route(request:Request,env:Env,ctx:ExecutionContext):Promise<Response> {
  const url=new URL(request.url), path=url.pathname;
  const base=(env.PUBLIC_BASE_URL || url.origin).replace(/\/$/,'');
  if (path==='/health' && request.method==='GET') return response({ok:true,service:'waku-api'});
  if (path==='/telegram/webhook' && request.method==='POST') return webhook(request,env,ctx);
  if (path.startsWith('/media/') && ['GET','HEAD'].includes(request.method)) {
    const id=path.slice(7);
    if (!uuid.test(id)) throw new HttpError(404,'not_found','Không tìm thấy ảnh.');
    const row=await image(env,id);
    const object=await env.IMAGES.get(row.object_key);
    if (!object) throw new HttpError(404,'not_found','Không tìm thấy ảnh.');
    const headers=new Headers({
      'content-type':row.mime_type,'content-length':String(row.size),
      'content-disposition':`inline; filename="${row.filename}"`,
      'etag':object.httpEtag,'cache-control':'public, max-age=3600',
      'x-content-type-options':'nosniff',
    });
    if (request.headers.get('if-none-match')===object.httpEtag) {
      await object.body.cancel(); headers.delete('content-length');
      return new Response(null,{status:304,headers});
    }
    if (request.method==='HEAD') {await object.body.cancel(); return new Response(null,{headers});}
    return new Response(object.body,{headers});
  }
  if (!path.startsWith('/v1/')) throw new HttpError(404,'not_found','Không tìm thấy đường dẫn.');
  if (!env.API_KEY) throw new HttpError(503,'not_configured','API chưa được cấu hình.');
  if (!equalSecret(request.headers.get('authorization') || '',`Bearer ${env.API_KEY}`)) throw new HttpError(401,'unauthorized','API key không hợp lệ.');
  if (path==='/v1/images/random' && request.method==='GET') {
    const exclude=(url.searchParams.get('exclude') || '').split(',').filter(Boolean);
    if (exclude.length>100 || exclude.some(x=>!uuid.test(x))) throw new HttpError(400,'invalid_exclude','exclude: tối đa 100 ID ảnh.');
    const filterTags=tags(url.searchParams.get('tags'));
    if (exclude.length+filterTags.length>96) throw new HttpError(400,'too_many_filters','Tổng tag và ID loại trừ tối đa 96.');
    const rows=await choose(env,url.searchParams.get('rating') || 'safe',filterTags,integer(url.searchParams.get('count'),1,10),exclude);
    return response({images:await presentMany(env,rows,base),count:rows.length});
  }
  if (path==='/v1/images' && request.method==='GET') {
    const limit=integer(url.searchParams.get('limit'),20,100);
    const before=url.searchParams.get('before');
    let cursor:{time:number;id:string}|undefined;
    if (before) {
      try {cursor=JSON.parse(atob(before));} catch {throw new HttpError(400,'invalid_cursor','Cursor không hợp lệ.');}
      if (!cursor || !Number.isSafeInteger(cursor.time) || !uuid.test(cursor.id)) throw new HttpError(400,'invalid_cursor','Cursor không hợp lệ.');
    }
    const rows=await env.DB.prepare(`SELECT * FROM images ${cursor?'WHERE created_at<? OR (created_at=? AND id<?)':''} ORDER BY created_at DESC,id DESC LIMIT ?`)
      .bind(...(cursor?[cursor.time,cursor.time,cursor.id]:[]),limit+1).all<ImageRow>();
    const page=rows.results.slice(0,limit), last=page.at(-1);
    return response({images:await presentMany(env,page,base),next:rows.results.length>limit && last?btoa(JSON.stringify({time:last.created_at,id:last.id})):null});
  }
  if (path==='/v1/images' && request.method==='POST') {
    let bytes:ArrayBuffer, info, manual=false;
    if (request.headers.get('content-type')?.startsWith('multipart/form-data')) {
      const body=await boundedBody(request,maxBytes(env)+65536);
      let form:FormData;
      try {form=await new Response(body,{headers:{'content-type':request.headers.get('content-type')!}}).formData();}
      catch {throw new HttpError(400,'invalid_form','Form không hợp lệ.');}
      const file=form.get('file');
      if (!file || typeof file==='string') throw new HttpError(400,'missing_file','Thiếu file ảnh.');
      bytes=await file.arrayBuffer();
      manual=form.get('manual')==='true';
      info=metadata(Object.fromEntries(['title','tags','rating','source_url'].filter(key=>form.has(key)).map(key=>[key,form.get(key)])));
    } else {
      const body=await json(request);
      if (typeof body.url!=='string') throw new HttpError(400,'missing_url','Thiếu URL ảnh.');
      info=metadata(body);
      manual=body.manual===true;
      bytes=await fetchImage(body.url,maxBytes(env));
    }
    const result=await save(env,bytes,info,null);
    await enqueueAnalysis(env,result.row.id,manual);
    ctx.waitUntil(processAnalysis(env,result.row.id,job=>refreshAnalysisReceipt(env,job)));
    return response({image:await present(env,result.row,base),duplicate:result.duplicate},result.duplicate?200:201);
  }
  const match=path.match(/^\/v1\/images\/([^/]+)$/);
  if (match && uuid.test(match[1])) {
    const id=match[1], row=await image(env,id);
    if (request.method==='GET') return response({image:await present(env,row,base)});
    if (request.method==='PATCH') {
      const current=await present(env,row,base), body=await json(request);
      const info=metadata({...current,...body});
      const changed=await update(env,id,info);
      if (Object.hasOwn(body,'tags')) await env.DB.prepare("UPDATE image_analysis SET status='skipped',last_error='manual_edit' WHERE image_id=? AND status='pending'").bind(id).run();
      return response({image:await present(env,changed,base)});
    }
    if (request.method==='DELETE') {await remove(env,id); return response({deleted:true,id});}
  }
  throw new HttpError(404,'not_found','Không tìm thấy đường dẫn.');
}

export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext) {
    try {return await route(request,env,ctx);}
    catch(error) {
      if (error instanceof HttpError) return response({error:{code:error.code,message:error.message}},error.status);
      // Never log exception text: upstream URLs can contain credentials.
      console.error('API request failed',{path:new URL(request.url).pathname,method:request.method});
      return response({error:{code:'internal_error',message:'Chưa hoàn tất xử lý. Hãy thử lại sau.'}},500);
    }
  },
  scheduled(_event:ScheduledController,env:Env,ctx:ExecutionContext) {
    ctx.waitUntil((async()=>{
      await processJobs(env,1,undefined,false);
      await processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job));
    })());
  },
} satisfies ExportedHandler<Env>;
