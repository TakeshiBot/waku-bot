import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {captionMetadata,publicUrl} from '../src/input.ts';
import {acquireSlot,releaseSlot} from '../src/queue.ts';

// Real D1/R2 bindings and the bundled Worker; no production credentials/network.
let mf:Miniflare;
const key='test-api-key', secret='test-webhook-secret';
const sent:Array<{method:string;body:Record<string,any>}>=[];
const sourceRequests:string[]=[];
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');

before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({
    modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',
    d1Databases:['DB'],r2Buckets:['IMAGES'],
    bindings:{API_KEY:key,TELEGRAM_WEBHOOK_SECRET:secret,TELEGRAM_BOT_TOKEN:'123456789:test',OWNER_IDS:'101',PUBLIC_BASE_URL:'https://images.example.com',MAX_IMAGE_MB:'1'},
    outboundService:async(request:Request)=>{
      const url=new URL(request.url);
      if (url.hostname==='images.example.com' && url.pathname==='/internal/jobs') return mf.dispatchFetch(url.href,{method:'POST',headers:Object.fromEntries(request.headers),body:await request.text()});
      if (url.hostname==='cdn.example.com') {
        if (url.pathname==='/redirect') return new MockResponse(null,{status:302,headers:{location:'http://127.0.0.1/private'}});
        return new MockResponse(png,{headers:{'content-type':'image/png'}});
      }
      if(url.hostname==='pbs.twimg.com'){
        sourceRequests.push(url.href);
        if(url.pathname==='/media/qa-fallback'&&url.searchParams.get('name')==='orig')return new MockResponse(null,{status:403});
        return new MockResponse(Buffer.concat([png,Buffer.from(url.pathname+(url.searchParams.get('name')==='orig'?'original':'thumbnail'))]),{headers:{'content-type':'image/png'}});
      }
      assert.equal(url.hostname,'api.telegram.org','No external requests allowed in tests');
      if (url.pathname.includes('/file/bot')) return new MockResponse(url.pathname.includes('/bulk-')?Buffer.concat([png,Buffer.from(url.pathname)]):png);
      const method=url.pathname.split('/').at(-1)!;
      const body=await request.json();sent.push({method,body});
      return MockResponse.json({ok:true,result:method==='getFile'?{file_path:`photos/${String(body.file_id).startsWith('bulk-')?body.file_id:'test'}.png`}:{message_id:100}});
    },
  }));
  const db=await mf.getD1Database('DB');
  for (const name of ['0001_initial.sql','0002_ai_tags.sql','0003_worker_slots.sql','0004_analysis_batches.sql','0005_library_filters.sql','0006_ai_providers.sql']) {
    const migration=await readFile(`migrations/${name}`,'utf8');
    await db.batch(migration.split(';').map(s=>s.trim()).filter(Boolean).map(sql=>db.prepare(sql)));
  }
});
after(async()=>{await mf?.dispose();});

async function call(path:string,method='GET',body?:unknown) {
  const native=new Request(`https://images.example.com${path}`,{
    method,headers:{authorization:`Bearer ${key}`,...(body && !(body instanceof FormData)?{'content-type':'application/json'}:{})},
    ...(body===undefined?{}:{body:body instanceof FormData?body:JSON.stringify(body)}),
  });
  const response=await mf.dispatchFetch(`https://images.example.com${path}`,{
    method,headers:Object.fromEntries(native.headers),
    ...(body===undefined?{}:{body:Buffer.from(await native.arrayBuffer())}),
  });
  return response;
}
async function upload(seed:number,rating='safe',tags='cat,cute') {
  const form=new FormData();
  // Distinct content hashes; image sniffing does not decode raster payloads.
  form.set('file',new Blob([png,new Uint8Array([seed])],{type:'image/png'}),'test.png');
  form.set('rating',rating);form.set('tags',tags);form.set('title',`Image ${seed}`);
  const result=await call('/v1/images','POST',form);
  assert.ok([200,201].includes(result.status),await result.clone().text());
  return await result.json() as any;
}
async function webhook(update:unknown,token=secret) {
  return mf.dispatchFetch('https://images.example.com/telegram/webhook',{
    method:'POST',headers:{'x-telegram-bot-api-secret-token':token,'content-type':'application/json'},body:JSON.stringify(update),
  });
}
async function finished(id:string) {
  const db=await mf.getD1Database('DB');
  for (let i=0;i<100;i++) {
    const job=await db.prepare('SELECT * FROM telegram_jobs WHERE id=?').bind(id).first();
    if (job && ['done','failed','uncertain'].includes(String(job.status))) return job;
    await new Promise(r=>setTimeout(r,20));
  }
  assert.fail('Webhook job did not finish');
}

test('private API, webhook secret, owner and DM boundaries',async()=>{
  assert.equal((await mf.dispatchFetch('https://images.example.com/v1/images')).status,401);
  assert.equal((await webhook({update_id:1},'wrong')).status,403);
  assert.equal((await webhook({update_id:2,message:{message_id:1,from:{id:2},chat:{id:2,type:'private'},text:'/stats'}})).status,200);
  await webhook({update_id:3,message:{message_id:1,from:{id:101},chat:{id:-123,type:'supergroup'},text:'/stats'}});
  const db=await mf.getD1Database('DB');
  assert.equal((await db.prepare('SELECT COUNT(*) n FROM telegram_jobs').first())?.n,0);
});

test('browser sessions use HttpOnly cookies, reject cross-origin writes and expire on logout',async()=>{
  const login=await call('/v1/session','POST');
  assert.equal(login.status,200);
  const cookie=login.headers.get('set-cookie')!;
  assert.match(cookie,/HttpOnly/);assert.match(cookie,/Secure/);assert.match(cookie,/SameSite=Strict/);
  const session=cookie.split(';')[0];
  const read=await mf.dispatchFetch('https://images.example.com/v1/stats',{headers:{cookie:session}});assert.equal(read.status,200);
  const denied=await mf.dispatchFetch('https://images.example.com/v1/session',{method:'DELETE',headers:{cookie:session,origin:'https://evil.example.com'}});assert.equal(denied.status,403);
  const logout=await mf.dispatchFetch('https://images.example.com/v1/session',{method:'DELETE',headers:{cookie:session,origin:'https://images.example.com'}});assert.equal(logout.status,200);assert.match(logout.headers.get('set-cookie')!,/Max-Age=0/);
  const tampered=await mf.dispatchFetch('https://images.example.com/v1/stats',{headers:{cookie:session+'x'}});assert.equal(tampered.status,401);
  assert.equal((await mf.dispatchFetch('https://images.example.com/internal/jobs',{method:'POST',body:'{}'})).status,401);
});

test('global upload semaphore admits two jobs, rejects excess and can recover an expired lease',async()=>{
  const db=await mf.getD1Database('DB'),env={DB:db} as any;
  const claims=await Promise.all([acquireSlot(env,'upload'),acquireSlot(env,'upload'),acquireSlot(env,'upload')]);
  assert.equal(claims.filter(Boolean).length,2);
  const blocked=await call('/v1/images','POST',{url:'https://cdn.example.com/a.png'});assert.equal(blocked.status,429);assert.equal(blocked.headers.get('retry-after'),'2');
  for(const token of claims.filter(Boolean))await releaseSlot(env,token!);
  await db.prepare("UPDATE worker_slots SET lease_until=?,token='dead' WHERE kind='upload' AND slot=1").bind(Date.now()-1).run();
  const recovered=await acquireSlot(env,'upload');assert.ok(recovered);await releaseSlot(env,recovered!);
});

test('upload, duplicate metadata, MIME, conditional GET, PATCH and delete',async()=>{
  const first=await upload(1), again=await upload(1,'r18','wrong');
  assert.equal(first.duplicate,false);assert.equal(again.duplicate,true);
  assert.equal(first.image.id,again.image.id);assert.equal(again.image.rating,'safe');
  assert.deepEqual(again.image.tags,['cat','cute']);
  const media=await mf.dispatchFetch(first.image.url);
  assert.equal(media.status,200);assert.equal(media.headers.get('content-type'),'image/png');
  assert.equal((await media.arrayBuffer()).byteLength,png.length+1);
  const unchanged=await mf.dispatchFetch(first.image.url,{headers:{'if-none-match':media.headers.get('etag')!}});
  assert.equal(unchanged.status,304);
  const patch=await call(`/v1/images/${first.image.id}`,'PATCH',{rating:'r18'});
  const updated=await patch.json() as any;
  assert.equal(updated.image.rating,'r18');assert.deepEqual(updated.image.tags,['cat','cute']);
  assert.equal((await call(`/v1/images/${first.image.id}`,'DELETE')).status,200);
  assert.equal((await mf.dispatchFetch(first.image.url)).status,404);
  const bucket=await mf.getR2Bucket('IMAGES');
  assert.equal((await bucket.list()).objects.length,0);
});

test('random albums: count 10, AND tag filtering, ratings, exclusions and pagination',async()=>{
  const ids=[];
  for (let i=10;i<22;i++) ids.push((await upload(i)).image.id);
  await upload(30,'r18');await upload(31,'unknown');await upload(32,'safe','cat');
  const random=await (await call('/v1/images/random?count=10&tags=cat,cute')).json() as any;
  assert.equal(random.images.length,10);assert.equal(new Set(random.images.map((x:any)=>x.id)).size,10);
  assert.ok(random.images.every((x:any)=>x.rating==='safe' && x.tags.includes('cute')));
  const remaining=await (await call(`/v1/images/random?count=10&tags=cat,cute&exclude=${ids.join(',')}`)).json() as any;
  assert.equal(remaining.count,0);
  const r18=await (await call('/v1/images/random?rating=r18&count=10')).json() as any;
  assert.equal(r18.count,1);
  const mixed=await (await call('/v1/images/random?rating=mixed&count=10')).json() as any;
  assert.ok(mixed.images.every((x:any)=>x.rating!=='unknown'));
  assert.equal((await call('/v1/images/random?count=11')).status,400);
  const page=await (await call('/v1/images?limit=10')).json() as any;
  const next=await (await call(`/v1/images?limit=10&before=${encodeURIComponent(page.next)}`)).json() as any;
  assert.equal(page.images.length,10);assert.equal(next.images.length,5);
  assert.equal(new Set([...page.images,...next.images].map((x:any)=>x.id)).size,15);
  const filtered=await (await call('/v1/images?q=cute&rating=r18')).json() as any;
  assert.equal(filtered.images.length,1);assert.equal(filtered.images[0].rating,'r18');
  const stats=await (await call('/v1/stats')).json() as any;assert.equal(stats.images.total,15);assert.equal(stats.images.unknown,1);
});

test('URL imports, unsupported files, unsafe redirects, size and malformed metadata',async()=>{
  const imported=await call('/v1/images','POST',{url:'https://cdn.example.com/photo.webp',rating:'safe'});
  assert.equal(imported.status,201);
  assert.equal((await call('/v1/images','POST',{url:'https://cdn.example.com/redirect'})).status,400);
  for (const bad of ['http://localhost/a','http://127.0.0.1/a','http://0x7f000001/a','http://[::1]/a','http://user:pass@host.com/a','http://site.local/a']) assert.throws(()=>publicUrl(bad));
  const html=new FormData();html.set('file',new Blob(['<html>not an image</html>']),'image.png');
  assert.equal((await call('/v1/images','POST',html)).status,415);
  const large=new FormData();large.set('file',new Blob([new Uint8Array(1024*1024+1)]),'big.png');
  assert.equal((await call('/v1/images','POST',large)).status,413);
  assert.deepEqual(captionMetadata('Mèo #cat #safe https://www.pixiv.net/a'),{title:'Mèo',tags:['cat'],rating:'safe',source_url:'https://www.pixiv.net/a',image_type:null});
});

test('image types, checkbox filters, content dedup preflight and lightweight state revisions',async()=>{
  const initial=await (await call('/v1/state')).json() as any;
  const form=new FormData();form.set('file',new Blob([png,Buffer.from('typed-image')]),'original-name.png');form.set('image_type','real');form.set('rating','safe');form.set('manual','true');form.set('tags','qa_tag,cosplay');
  const created=await (await call('/v1/images','POST',form)).json() as any;
  assert.equal(created.image.image_type,'real');assert.equal(created.image.original_name,'original-name.png');
  const dedup=await (await call('/v1/images/duplicates','POST',{hashes:[created.image.sha256]})).json() as any;
  assert.equal(dedup.duplicates[0].id,created.image.id);
  const imported=await (await call('/v1/images','POST',{url:'https://cdn.example.com/second-photo.png',manual:true})).json() as any;
  const sameUrl=await (await call('/v1/images/duplicates','POST',{urls:['https://cdn.example.com/second-photo.png']})).json() as any;assert.equal(sameUrl.duplicates[0].id,imported.image.id);
  // An existing content hash remains canonical even when imported under another URL.
  const typed=await (await call('/v1/images?types=real&tags=qa_tag&rating=safe')).json() as any;assert.deepEqual(typed.images.map((x:any)=>x.id),[created.image.id]);
  const other=await (await call('/v1/images?types=anime&tags=qa_tag')).json() as any;assert.equal(other.images.length,0);
  assert.equal((await call('/v1/images?types=invalid')).status,400);
  assert.equal((await call('/v1/images/duplicates','POST',{hashes:['invalid']})).status,400);
  const available=await (await call('/v1/filters?q=qa_')).json() as any;assert.ok(available.tags.some((t:any)=>t.tag==='qa_tag'));
  const after=await (await call('/v1/state')).json() as any;assert.ok(after.revision>initial.revision);
  const unchanged=await (await call('/v1/state')).json() as any;assert.equal(unchanged.revision,after.revision);
  await call('/v1/images/'+created.image.id,'DELETE');
});

test('original URL imports persist full content and dedup renditions without treating failed fallback as full',async()=>{
  const small='https://pbs.twimg.com/media/qa-original?format=jpg&name=360x360';
  const before=sourceRequests.length;
  const initial=await (await call('/v1/images/duplicates','POST',{urls:[small]})).json() as any;
  assert.equal(initial.duplicates.length,0);assert.equal(initial.links[0].preferred_url,small.replace('360x360','orig'));
  assert.equal(sourceRequests.length,before,'preflight normalizes locally without probing remote CDNs');
  const result=await (await call('/v1/images','POST',{url:small,manual:true})).json() as any;
  assert.equal(result.import.used_original,true);assert.equal(result.import.fallback_reason,null);assert.equal(sourceRequests.length,before+1);
  assert.equal(result.image.source_url,result.import.fetched_url);
  assert.equal(result.image.source_url,small.replace('360x360','orig'));
  const stored=await mf.dispatchFetch(result.image.url);assert.ok(Buffer.from(await stored.arrayBuffer()).includes(Buffer.from('original')));
  const other='https://pbs.twimg.com/media/qa-original?format=png&name=large';
  const duplicate=await (await call('/v1/images/duplicates','POST',{urls:[other]})).json() as any;
  assert.equal(duplicate.duplicates[0].id,result.image.id);assert.equal(duplicate.duplicates[0].import_url,other);
  assert.equal(sourceRequests.length,before+1,'different sizes do not cause a second source download');
  const fallbackUrl='https://pbs.twimg.com/media/qa-fallback?format=jpg&name=360x360';
  const fallback=await (await call('/v1/images','POST',{url:fallbackUrl,manual:true})).json() as any;
  assert.equal(fallback.import.used_original,false);assert.equal(fallback.import.fallback_reason,'download_failed');
  assert.equal(fallback.image.source_url,fallbackUrl,'fallback source reflects the file actually downloaded');
  const retry=await (await call('/v1/images/duplicates','POST',{urls:[fallbackUrl.replace('360x360','orig')]})).json() as any;
  assert.equal(retry.duplicates.length,0,'a thumbnail fallback must not mark the original as already stored');
  const db=await mf.getD1Database('DB');
  await db.prepare('UPDATE images SET source_url=? WHERE id=?').bind(small,result.image.id).run();
  const reimport=await (await call('/v1/images','POST',{url:small,source_url:'https://artist.site.com/post/1',manual:true,title:'Should not replace title'})).json() as any;
  assert.equal(reimport.duplicate,true);assert.equal(reimport.image.source_url,reimport.import.fetched_url);assert.equal(reimport.image.title,result.image.title);
  await db.prepare('DELETE FROM image_import_urls WHERE image_id=?').bind(result.image.id).run();
  await db.prepare('INSERT INTO image_import_urls(url,image_id) VALUES (?,?)').bind(small,result.image.id).run();
  const legacy=await (await call('/v1/images/duplicates','POST',{urls:[small]})).json() as any;assert.equal(legacy.duplicates.length,0,'old raw thumbnail aliases never prevent upgrading');
  await call('/v1/images/'+result.image.id,'DELETE');await call('/v1/images/'+fallback.image.id,'DELETE');
});

test('Telegram photo ingestion, update dedup and classification callback change real API state',async()=>{
  const msg={message_id:1,from:{id:101},chat:{id:101,type:'private'},photo:[{file_id:'photo',file_size:png.length}],caption:'Telegram cat #cat'};
  await webhook({update_id:100,message:msg});
  const job=await finished('tg:100');assert.equal(job.status,'done');assert.ok(job.image_id);
  const before=sent.filter(x=>x.method==='sendPhoto').length;
  await webhook({update_id:100,message:msg});
  assert.equal(sent.filter(x=>x.method==='sendPhoto').length,before);
  // The URL import above is the same hash: dedup keeps its original safe metadata.
  const id=String(job.image_id);
  const cb={id:'callback',from:{id:101},data:`rating:r18:${id}`,message:msg};
  await webhook({update_id:101,callback_query:cb});
  assert.equal((await finished('tg:101')).status,'done');
  const current=await (await call(`/v1/images/${id}`)).json() as any;
  assert.equal(current.image.rating,'r18');
  assert.ok(sent.some(x=>x.method==='editMessageCaption' && x.body.caption.includes('R18')));
  await webhook({update_id:102,callback_query:{...cb,id:'delete',data:`delete:${id}`}});
  assert.equal((await finished('tg:102')).status,'done');
  assert.equal((await call(`/v1/images/${id}`)).status,404);
});

test('Telegram album burst is persisted and all five files drain through the bounded queue',async()=>{
  await Promise.all(Array.from({length:5},(_,i)=>webhook({update_id:1000+i,message:{
    message_id:100+i,media_group_id:'album-1',from:{id:101},chat:{id:101,type:'private'},photo:[{file_id:`bulk-${i}`}],caption:'#safe',
  }})));
  // This fixture uses the same configured owner as the main worker setup.
  const db=await mf.getD1Database('DB');
  let jobs:any[]=[];
  for(let i=0;i<200;i++){
    jobs=(await db.prepare("SELECT * FROM telegram_jobs WHERE id IN ('tg:1000','tg:1001','tg:1002','tg:1003','tg:1004')").all()).results;
    if(jobs.length===5 && jobs.every(job=>job.status==='done'))break;
    await new Promise(resolve=>setTimeout(resolve,75));
  }
  assert.equal(jobs.length,5);assert.ok(jobs.every(job=>job.status==='done'));assert.equal(new Set(jobs.map(job=>job.image_id)).size,5);
});
