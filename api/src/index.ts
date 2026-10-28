import { boundedBody, equalSecret, fetchImage, metadata, tags } from './input';
import { choose, image, maxBytes, present, presentMany, remove, save, update } from './store';
import { processJobs, refreshAnalysisReceipt, webhook } from './telegram';
import {aiEnabled,enqueueAnalysis,processAnalysis} from './analysis';
import { HttpError, type Env, type ImageRow } from './types';
import {authorize,sessionCookie,clearSession} from './auth';
import {acquireSlot,releaseSlot,continueWork} from './queue';
import {attachBatch,batchProgress,createBatch,stopPendingAnalysis} from './batches';

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
  if (request.method==='GET' && (path==='/' || /^\/assets\/[\w.-]+$/.test(path)) && env.ASSETS) {
    const result=await env.ASSETS.fetch(request);
    const headers=new Headers(result.headers);
    headers.set('content-security-policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
    headers.set('x-content-type-options','nosniff');headers.set('referrer-policy','no-referrer');
    headers.set('cache-control',path==='/'?'no-store':'public, max-age=300');
    return new Response(result.body,{status:result.status,headers});
  }
  if (path==='/internal/jobs' && request.method==='POST') {
    if (!equalSecret(request.headers.get('authorization') || '',`Bearer ${env.API_KEY}`) || !env.API_KEY) throw new HttpError(401,'unauthorized','Không có quyền.');
    const task=await json(request),hop=Number(task.hop);
    if (!['ai','upload'].includes(String(task.kind)) || !Number.isInteger(hop) || hop<1 || hop>8) throw new HttpError(400,'invalid_job','Tác vụ không hợp lệ.');
    ctx.waitUntil(task.kind==='ai'?processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job),hop):processJobs(env,1,undefined,true,hop));
    return response({accepted:true});
  }
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
  await authorize(request,env);
  if (path==='/v1/session') {
    if (request.method==='POST') return Response.json({ok:true},{headers:{'set-cookie':await sessionCookie(env),'cache-control':'no-store'}});
    if (request.method==='GET') return response({ok:true,max_image_mb:maxBytes(env)/1024/1024,concurrency:2,ai_enabled:aiEnabled(env)});
    if (request.method==='DELETE') return Response.json({ok:true},{headers:{'set-cookie':clearSession(),'cache-control':'no-store'}});
  }
  if (path==='/v1/analysis-batches' && request.method==='POST') {
    if (!aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
    return response({id:await createBatch(env)},201);
  }
  const batchMatch=path.match(/^\/v1\/analysis-batches\/([a-f0-9-]{36})$/);
  if (batchMatch) {
    const id=batchMatch[1];
    if (request.method==='GET') return response(await batchProgress(env,id));
    if (request.method==='POST') {
      if (!aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
      const body=await json(request);
      if (!Array.isArray(body.ids) || !body.ids.length || body.ids.length>100 || body.ids.some(id=>typeof id!=='string' || !uuid.test(id)))
        throw new HttpError(400,'invalid_ids','Mỗi lượt nhận 1–100 ID ảnh.');
      const added=await attachBatch(env,id,body.ids as string[],true);
      ctx.waitUntil(Promise.all([processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job)),processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job))]));
      return response({added});
    }
  }
  if (path==='/v1/jobs/drain' && request.method==='POST') {
    ctx.waitUntil(Promise.all([continueWork(env,'upload',0),continueWork(env,'ai',0)]));
    return response({accepted:true});
  }
  if (path==='/v1/stats' && request.method==='GET') {
    const images=await env.DB.prepare(`SELECT COUNT(*) total,COALESCE(SUM(size),0) bytes,
      COALESCE(SUM(rating='safe'),0) safe,COALESCE(SUM(rating='r18'),0) r18,COALESCE(SUM(rating='unknown'),0) unknown FROM images`).first();
    const ai=await env.DB.prepare("SELECT COUNT(*) pending FROM image_analysis WHERE status IN ('pending','running')").first();
    const uploads=await env.DB.prepare("SELECT COUNT(*) pending FROM telegram_jobs WHERE status IN ('pending','running')").first();
    return response({images,ai_pending:ai?.pending || 0,uploads_pending:uploads?.pending || 0});
  }
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
    const filters:string[]=[],params:(string|number)[]=[];
    const view=url.searchParams.get('view');
    if (view && !['pending','classified'].includes(view)) throw new HttpError(400,'invalid_view','Mục ảnh không hợp lệ.');
    if (view==='pending') filters.push("(i.rating='unknown' OR EXISTS (SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running')))");
    if (view==='classified') filters.push("NOT EXISTS (SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))");
    const rating=url.searchParams.get('rating');
    if (rating) {
      if (!['safe','r18','unknown'].includes(rating)) throw new HttpError(400,'invalid_rating','Phân loại không hợp lệ.');
      filters.push('i.rating=?');params.push(rating);
    }
    const query=(url.searchParams.get('q') || '').trim();
    if (query.length>100) throw new HttpError(400,'invalid_query','Từ khóa tối đa 100 ký tự.');
    if (query) {
      const escaped='%'+query.toLowerCase().replace(/[\\%_]/g,'\\$&')+'%';
      filters.push("(LOWER(i.title) LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND t.tag LIKE ? ESCAPE '\\'))");params.push(escaped,escaped);
    }
    if (cursor) {filters.push('(i.created_at<? OR (i.created_at=? AND i.id<?))');params.push(cursor.time,cursor.time,cursor.id);}
    const rows=await env.DB.prepare(`SELECT i.* FROM images i ${filters.length?'WHERE '+filters.join(' AND '):''} ORDER BY i.created_at DESC,i.id DESC LIMIT ?`)
      .bind(...params,limit+1).all<ImageRow>();
    const page=rows.results.slice(0,limit), last=page.at(-1);
    return response({images:await presentMany(env,page,base),next:rows.results.length>limit && last?btoa(JSON.stringify({time:last.created_at,id:last.id})):null});
  }
  if (path==='/v1/images' && request.method==='POST') {
    const slot=await acquireSlot(env,'upload');
    if (!slot) return Response.json({error:{code:'busy',message:'Đang xử lý 2 ảnh. File này sẽ được thử lại sau.'}},{status:429,headers:{'retry-after':'2','cache-control':'no-store'}});
    try {
    let bytes:ArrayBuffer, info, manual=false,batchId:unknown;
    if (request.headers.get('content-type')?.startsWith('multipart/form-data')) {
      const body=await boundedBody(request,maxBytes(env)+65536);
      let form:FormData;
      try {form=await new Response(body,{headers:{'content-type':request.headers.get('content-type')!}}).formData();}
      catch {throw new HttpError(400,'invalid_form','Form không hợp lệ.');}
      const file=form.get('file');
      if (!file || typeof file==='string') throw new HttpError(400,'missing_file','Thiếu file ảnh.');
      bytes=await file.arrayBuffer();
      manual=form.get('manual')==='true';
      batchId=form.get('batch_id');
      info=metadata(Object.fromEntries(['title','tags','rating','source_url'].filter(key=>form.has(key)).map(key=>[key,form.get(key)])));
    } else {
      const body=await json(request);
      if (typeof body.url!=='string') throw new HttpError(400,'missing_url','Thiếu URL ảnh.');
      info=metadata(body);
      manual=body.manual===true;
      batchId=body.batch_id;
      bytes=await fetchImage(body.url,maxBytes(env));
    }
    if (batchId) {
      if (typeof batchId!=='string' || !uuid.test(batchId)) throw new HttpError(400,'invalid_batch','Đợt AI không hợp lệ.');
      await batchProgress(env,batchId);
    }
    const result=await save(env,bytes,info,null);
    await enqueueAnalysis(env,result.row.id,manual);
    if (batchId && typeof batchId==='string') await attachBatch(env,batchId,[result.row.id]);
    ctx.waitUntil(processAnalysis(env,result.row.id,job=>refreshAnalysisReceipt(env,job)));
    return response({image:await present(env,result.row,base),duplicate:result.duplicate},result.duplicate?200:201);
    } finally {await releaseSlot(env,slot);ctx.waitUntil(continueWork(env,'upload'));}
  }
  const match=path.match(/^\/v1\/images\/([^/]+)$/);
  if (match && uuid.test(match[1])) {
    const id=match[1], row=await image(env,id);
    if (request.method==='GET') return response({image:await present(env,row,base)});
    if (request.method==='PATCH') {
      const current=await present(env,row,base), body=await json(request);
      const info=metadata({...current,...body});
      const changed=await update(env,id,info);
      if (Object.hasOwn(body,'tags')) await stopPendingAnalysis(env,id);
      return response({image:await present(env,changed,base)});
    }
    if (request.method==='DELETE') {await remove(env,id); return response({deleted:true,id});}
  }
  const analyze=path.match(/^\/v1\/images\/([a-f0-9-]{36})\/analyze$/);
  if (analyze && request.method==='POST') {
    await image(env,analyze[1]);
    if (!aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
    const batchId=await createBatch(env);await attachBatch(env,batchId,[analyze[1]],true);
    ctx.waitUntil(processAnalysis(env,analyze[1],job=>refreshAnalysisReceipt(env,job)));
    return response({queued:true,batch_id:batchId});
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
