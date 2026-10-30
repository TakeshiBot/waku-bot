import { boundedBody, equalSecret, downloadImage, metadata, tags,publicUrl,type ImageDownload } from './input';
import { choose, image, maxBytes, present, presentMany, remove, save, update,recordImportAliases,libraryChanged,CLASSIFIED_IMAGE_SQL } from './store';
import { processJobs, refreshAnalysisReceipt, webhook } from './telegram';
import {aiEnabled,enqueueAnalysis,processAnalysis} from './analysis';
import { HttpError, usesLibsql, type Env, type ImageRow } from './types';
import {authorize,sessionCookie,clearSession} from './auth';
import {acquireSlot,releaseSlot,continueWork} from './queue';
import {attachBatch,batchProgress,getBatch,createBatch,stopPendingAnalysis,controlAnalysis,queueProgress} from './batches';
import {manageProviders} from './providers';
import {imageSourcePlan} from './image-sources';
import {manageCatalogKeys} from './keys';
import {libraryState} from './library-state';
import {assertD1Available,protectD1,recordD1Limit,quotaError} from './d1-quota';
import {ensureReadEfficiency} from './read-efficiency';
import {withDatabase} from './libsql';

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
function background(ctx:ExecutionContext,env:Env,work:()=>Promise<unknown>){
  if(env.MIGRATION_MAINTENANCE==='1')return;
  ctx.waitUntil(protectD1(env,async()=>{await ensureReadEfficiency(env);await work();}));
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
    if(task.token!==undefined&&(task.kind!=='ai'||typeof task.token!=='string'||!/^[a-f0-9]{32}$/.test(task.token)))throw new HttpError(400,'invalid_job','Tác vụ không hợp lệ.');
    background(ctx,env,()=>task.kind==='ai'?processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job),hop,task.token as string|undefined):processJobs(env,1,undefined,true,hop));
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
      // IDs never reuse image bytes, so previews/editor can reuse the original
      // browser response while metadata continues to refresh independently.
      'etag':object.httpEtag,'cache-control':'public, max-age=86400',
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
  await ensureReadEfficiency(env);
  if(path==='/v1/keys'||path.startsWith('/v1/keys/'))return response(await manageCatalogKeys(request,env,['POST','PATCH'].includes(request.method)?await json(request):{}),request.method==='POST'?201:200);
  if(path.startsWith('/v1/ai/')){
    const settings=await manageProviders(request,env,['POST','PATCH','PUT'].includes(request.method)?await json(request):{});
    if(request.method!=='GET')background(ctx,env,()=>processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job)));
    return response(settings);
  }
  if (path==='/v1/session') {
    if (request.method==='POST') return Response.json({ok:true},{headers:{'set-cookie':await sessionCookie(env),'cache-control':'no-store'}});
    if (request.method==='GET') return response({ok:true,max_image_mb:maxBytes(env)/1024/1024,concurrency:2,ai_enabled:await aiEnabled(env),database:usesLibsql(env)?'libsql':'d1'});
    if (request.method==='DELETE') return Response.json({ok:true},{headers:{'set-cookie':clearSession(),'cache-control':'no-store'}});
  }
  if (path==='/v1/analysis-batches' && request.method==='POST') {
    if (!await aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
    return response({id:await createBatch(env)},201);
  }
  if(path==='/v1/analysis/control' && request.method==='POST'){
    const body=await json(request);
    if(body.batch_id!==undefined&&(typeof body.batch_id!=='string'||!uuid.test(body.batch_id)))throw new HttpError(400,'invalid_batch','Đợt AI không hợp lệ.');
    if(!['pause','resume','cancel','retry'].includes(String(body.action)))throw new HttpError(400,'invalid_action','Thao tác AI không hợp lệ.');
    if(['resume','retry'].includes(String(body.action))&&!await aiEnabled(env))throw new HttpError(503,'ai_not_configured','Bật ít nhất một API và chọn model vision trước khi chạy.');
    const progress=await controlAnalysis(env,String(body.action),body.batch_id as string|undefined);
    if(['resume','retry'].includes(String(body.action)))background(ctx,env,()=>processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job)));
    return response({progress});
  }
  const batchMatch=path.match(/^\/v1\/analysis-batches\/([a-f0-9-]{36})$/);
  if (batchMatch) {
    const id=batchMatch[1];
    if (request.method==='GET') return response(await batchProgress(env,id));
    if (request.method==='POST') {
      if (!await aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
      const body=await json(request);
      if (!Array.isArray(body.ids) || !body.ids.length || body.ids.length>100 || body.ids.some(id=>typeof id!=='string' || !uuid.test(id)))
        throw new HttpError(400,'invalid_ids','Mỗi lượt nhận 1–100 ID ảnh.');
      const added=await attachBatch(env,id,body.ids as string[],true);
      background(ctx,env,()=>processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job)));
      return response({added});
    }
  }
  if (path==='/v1/jobs/drain' && request.method==='POST') {
    background(ctx,env,()=>Promise.all([continueWork(env,'upload',0),continueWork(env,'ai',0)]));
    return response({accepted:true});
  }
  if ((path==='/v1/stats' || path==='/v1/state') && request.method==='GET') {
    const {counts,revision}=await libraryState(env);
    const {classified_safe,classified_r18,pending:pendingCount,...images}=counts!;
    const tabs={all:images.total,safe:classified_safe,r18:classified_r18,pending:pendingCount};
    const ai=await queueProgress(env);
    const uploads=await env.DB.prepare("SELECT COUNT(*) pending FROM telegram_jobs WHERE status IN ('pending','running')").first();
    let batch=null;const batchId=url.searchParams.get('batch');
    if(batchId && uuid.test(batchId))try{batch=await batchProgress(env,batchId);}catch(error){if(!(error instanceof HttpError && error.status===404))throw error;}
    const result=response({images,tabs,ai_pending:Number(ai.pending)+Number(ai.running),uploads_pending:uploads?.pending || 0,revision,batch,ai_queue:ai});
    result.headers.set('x-waku-stats-source','counters');return result;
  }
  if(path==='/v1/filters' && request.method==='GET') {
    const query=(url.searchParams.get('q') || '').slice(0,64).toLowerCase();
    const rows=await env.DB.prepare("SELECT tag,n count FROM tag_totals WHERE n>0 AND instr(tag,?)>0 ORDER BY tag LIMIT 1000").bind(query).all();
    return response({tags:rows.results});
  }
  if(path==='/v1/images/duplicates' && request.method==='POST') {
    const body=await json(request),hashes=body.hashes ?? [],urls=body.urls ?? [];
    if(!Array.isArray(hashes)||!Array.isArray(urls)||hashes.length+urls.length>100
      ||hashes.some(x=>typeof x!=='string'||!/^[a-f0-9]{64}$/.test(x))||urls.some(x=>typeof x!=='string'||x.length>4096))
      throw new HttpError(400,'invalid_fingerprints','Tối đa 100 mã ảnh hoặc link mỗi lượt.');
    const links=(urls as string[]).map(raw=>imageSourcePlan(publicUrl(raw)));
    const lookups=links.map(link=>({input:link.input,lookup:link.identity}));
    const rows=await env.DB.prepare(`SELECT id,sha256,NULL import_url FROM images WHERE sha256 IN (SELECT value FROM json_each(?))
      UNION ALL SELECT i.id,i.sha256,json_extract(j.value,'$.input') import_url FROM json_each(?) j
      JOIN image_import_urls u ON u.url=json_extract(j.value,'$.lookup') JOIN images i ON i.id=u.image_id`)
      .bind(JSON.stringify(hashes),JSON.stringify(lookups)).all();
    return response({duplicates:rows.results,links:links.map(({input,identity,preferred_url,source})=>({input,identity,preferred_url,source}))});
  }
  if (path==='/v1/images/random' && request.method==='GET') {
    const exclude=(url.searchParams.get('exclude') || '').split(',').filter(Boolean);
    if (exclude.length>100 || exclude.some(x=>!uuid.test(x))) throw new HttpError(400,'invalid_exclude','exclude: tối đa 100 ID ảnh.');
    const filterTags=tags(url.searchParams.get('tags'));
    if (exclude.length+filterTags.length>96) throw new HttpError(400,'too_many_filters','Tổng tag và ID loại trừ tối đa 96.');
    const rows=await choose(env,url.searchParams.get('rating') || 'safe',filterTags,integer(url.searchParams.get('count'),1,10),exclude);
    return response({images:await presentMany(env,rows,base),count:rows.length});
  }
  if(path==='/v1/images/bulk'&&request.method==='PATCH'){
    const body=await json(request),hasRating=Object.hasOwn(body,'rating'),hasType=Object.hasOwn(body,'image_type');
    if(!Array.isArray(body.ids)||!body.ids.length||body.ids.length>100||body.ids.some(id=>typeof id!=='string'||!uuid.test(id)))
      throw new HttpError(400,'invalid_ids','Chọn 1–100 ID ảnh mỗi lượt.');
    if(!hasRating&&!hasType)throw new HttpError(400,'missing_changes','Chọn Phân loại hoặc Kiểu ảnh cần sửa.');
    if(hasRating&&!['safe','r18','unknown'].includes(String(body.rating)))throw new HttpError(400,'invalid_rating','Phân loại không hợp lệ.');
    if(hasType&&body.image_type!==null&&!['anime','real'].includes(String(body.image_type)))throw new HttpError(400,'invalid_type','Kiểu ảnh không hợp lệ.');
    const ids=[...new Set(body.ids as string[])],sets=['metadata_version=metadata_version+1'],values:(string|null)[]=[];
    if(hasRating){sets.push('rating=?');values.push(body.rating as string);}
    if(hasType){sets.push('image_type=?');values.push(body.image_type as string|null);}
    const rows=await env.DB.prepare(`UPDATE images SET ${sets.join(',')} WHERE id IN (SELECT value FROM json_each(?)) RETURNING id`)
      .bind(...values,JSON.stringify(ids)).all<{id:string}>();
    const updated=rows.results.map(row=>row.id),found=new Set(updated);
    if(updated.length)await libraryChanged(env);
    return response({updated:updated.length,ids:updated,missing:ids.filter(id=>!found.has(id))});
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
    if (view==='pending') filters.push('i.id IN (SELECT image_id FROM image_catalog_state WHERE classified=0)');
    if (view==='classified') filters.push(CLASSIFIED_IMAGE_SQL);
    const rating=url.searchParams.get('rating');
    if (rating) {
      if (!['safe','r18','unknown'].includes(rating)) throw new HttpError(400,'invalid_rating','Phân loại không hợp lệ.');
      filters.push('i.rating=?');params.push(rating);
    }
    const filterTags=tags(url.searchParams.get('tags'));
    if(filterTags.length){filters.push('EXISTS (SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND t.tag IN (SELECT value FROM json_each(?)))');params.push(JSON.stringify(filterTags));}
    const types=(url.searchParams.get('types') || '').split(',').filter(Boolean);
    if(types.some(type=>!['anime','real'].includes(type)))throw new HttpError(400,'invalid_type','Kiểu ảnh không hợp lệ.');
    if(types.length){filters.push('i.image_type IN (SELECT value FROM json_each(?))');params.push(JSON.stringify(types));}
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
    // Keep the operation and its slot cleanup alive if the browser disconnects
    // mid-upload. Duplicate hashes make a later retry safe.
    const upload=(async()=>{
    let slot:string|null=null;
    try {
    let bytes:ArrayBuffer, info, manual=false,batchId:unknown,originalName:string|null=null,importUrl:string|null=null,download:ImageDownload|undefined;
    if (request.headers.get('content-type')?.startsWith('application/octet-stream')) {
      // The web client sends image bytes directly to avoid parsing a second
      // multipart copy on CPU-limited Workers. Legacy multipart remains valid.
      const raw=request.headers.get('x-waku-upload-metadata') || '';
      if(raw.length>16384)throw new HttpError(400,'invalid_metadata','Thông tin ảnh quá dài.');
      let fields:Record<string,unknown>;
      try{const data=JSON.parse(decodeURIComponent(raw));if(!data||typeof data!=='object'||Array.isArray(data))throw new Error();fields=data;}
      catch{throw new HttpError(400,'invalid_metadata','Thông tin ảnh không hợp lệ.');}
      bytes=await boundedBody(request,maxBytes(env));
      originalName=typeof fields.filename==='string'?fields.filename:null;
      manual=fields.manual===true;batchId=fields.batch_id;
      info=metadata(fields);
    } else if (request.headers.get('content-type')?.startsWith('multipart/form-data')) {
      const body=await boundedBody(request,maxBytes(env)+65536);
      let form:FormData;
      try {form=await new Response(body,{headers:{'content-type':request.headers.get('content-type')!}}).formData();}
      catch {throw new HttpError(400,'invalid_form','Form không hợp lệ.');}
      const file=form.get('file');
      if (!file || typeof file==='string') throw new HttpError(400,'missing_file','Thiếu file ảnh.');
      bytes=await file.arrayBuffer();
      originalName=file.name;
      manual=form.get('manual')==='true';
      batchId=form.get('batch_id');
      info=metadata(Object.fromEntries(['title','tags','rating','source_url','image_type'].filter(key=>form.has(key)).map(key=>[key,form.get(key)])));
    } else {
      const body=await json(request);
      if (typeof body.url!=='string') throw new HttpError(400,'missing_url','Thiếu URL ảnh.');
      info=metadata(body);
      importUrl=publicUrl(body.url).href;
      manual=body.manual===true;
      batchId=body.batch_id;
      download=await downloadImage(importUrl,maxBytes(env));bytes=download.bytes;info.source_url=download.url;
    }
    if (batchId) {
      if (typeof batchId!=='string' || !uuid.test(batchId)) throw new HttpError(400,'invalid_batch','Đợt AI không hợp lệ.');
      await getBatch(env,batchId);
    }
    // Receiving and decoding a multipart image may exceed the Worker's CPU
    // budget. Do that before reserving a scarce database write slot so an
    // interrupted request cannot leave other uploads waiting on its lease.
    slot=await acquireSlot(env,'upload',45000);
    if (!slot) return Response.json({error:{code:'busy',message:'Đang xử lý 2 ảnh. File này sẽ được thử lại sau.'}},{status:429,headers:{'retry-after':'2','cache-control':'no-store'}});
    const result=await save(env,bytes,info,null,originalName,importUrl);
    if(download)await recordImportAliases(env,result.row.id,[download.input,download.url,...(download.used_original?[download.identity]:[])]);
    await enqueueAnalysis(env,result.row.id,manual);
    if (batchId && typeof batchId==='string') await attachBatch(env,batchId,[result.row.id]);
    background(ctx,env,()=>processAnalysis(env,result.row.id,job=>refreshAnalysisReceipt(env,job)));
    return response({image:await present(env,result.row,base),duplicate:result.duplicate,...(download?{import:{source:download.source,preferred_url:download.preferred_url,fetched_url:download.url,used_original:download.used_original,fallback_reason:download.fallback_reason}}:{})},result.duplicate?200:201);
    } finally {if(slot){try{await releaseSlot(env,slot);}catch{console.warn('Upload slot release failed; lease will expire');}background(ctx,env,()=>continueWork(env,'upload'));}}
    })();
    ctx.waitUntil(upload.then(()=>undefined,()=>undefined));
    return await upload;
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
    if (!await aiEnabled(env)) throw new HttpError(503,'ai_not_configured','Chưa cấu hình API AI.');
    const batchId=await createBatch(env);await attachBatch(env,batchId,[analyze[1]],true);
    background(ctx,env,()=>processAnalysis(env,analyze[1],job=>refreshAnalysisReceipt(env,job)));
    return response({queued:true,batch_id:batchId});
  }
  throw new HttpError(404,'not_found','Không tìm thấy đường dẫn.');
}

export default {
  async fetch(request:Request,env:Env,ctx:ExecutionContext) {
    try {
      if(env.MIGRATION_MAINTENANCE==='1'){
        const path=new URL(request.url).pathname;
        if(path==='/health')return response({ok:true,service:'waku-api',maintenance:true});
        const probe=['GET','HEAD'].includes(request.method)&&Boolean(env.API_KEY)&&equalSecret(request.headers.get('x-waku-maintenance-probe')||'',env.API_KEY);
        if(!probe&&(path.startsWith('/v1/')||path.startsWith('/media/')||path==='/internal/jobs'||path==='/telegram/webhook')){
          return Response.json({error:{code:'maintenance',message:'Kho ảnh đang chuyển dữ liệu, vui lòng thử lại sau.'}},{status:503,headers:{'retry-after':'60','cache-control':'no-store'}});
        }
      }
      env=withDatabase(env);
      const path=new URL(request.url).pathname;
      if(path.startsWith('/v1/')||path.startsWith('/media/')||path==='/internal/jobs'||path==='/telegram/webhook')await assertD1Available(env);
      return await route(request,env,ctx);
    }
    catch(error) {
      if(await recordD1Limit(env,error))error=quotaError();
      if (error instanceof HttpError){const result=response({error:{code:error.code,message:error.message}},error.status);if(error.retryAfter)result.headers.set('retry-after',String(error.retryAfter));return result;}
      // Never log exception text: upstream URLs can contain credentials.
      console.error('API request failed',{path:new URL(request.url).pathname,method:request.method});
      return response({error:{code:'internal_error',message:'Chưa hoàn tất xử lý. Hãy thử lại sau.'}},500);
    }
  },
  scheduled(_event:ScheduledController,env:Env,ctx:ExecutionContext) {
    if(env.MIGRATION_MAINTENANCE==='1')return;
    env=withDatabase(env);
    background(ctx,env,async()=>{
      await processJobs(env,1,undefined,false);
      await processAnalysis(env,undefined,job=>refreshAnalysisReceipt(env,job));
    });
  },
} satisfies ExportedHandler<Env>;
