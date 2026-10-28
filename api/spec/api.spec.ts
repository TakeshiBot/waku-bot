import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {captionMetadata,publicUrl} from '../src/input.ts';

// Real D1/R2 bindings and the bundled Worker; no production credentials/network.
let mf:Miniflare;
const key='test-api-key', secret='test-webhook-secret';
const sent:Array<{method:string;body:Record<string,any>}>=[];
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');

before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({
    modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',
    d1Databases:['DB'],r2Buckets:['IMAGES'],
    bindings:{API_KEY:key,TELEGRAM_WEBHOOK_SECRET:secret,TELEGRAM_BOT_TOKEN:'123456789:test',OWNER_IDS:'101',PUBLIC_BASE_URL:'https://images.example.com',MAX_IMAGE_MB:'1'},
    outboundService:async(request:Request)=>{
      const url=new URL(request.url);
      if (url.hostname==='cdn.example.com') {
        if (url.pathname==='/redirect') return new MockResponse(null,{status:302,headers:{location:'http://127.0.0.1/private'}});
        return new MockResponse(png,{headers:{'content-type':'image/png'}});
      }
      assert.equal(url.hostname,'api.telegram.org','No external requests allowed in tests');
      if (url.pathname.includes('/file/bot')) return new MockResponse(png);
      const method=url.pathname.split('/').at(-1)!;
      const body=await request.json();sent.push({method,body});
      return MockResponse.json({ok:true,result:method==='getFile'?{file_path:'photos/test.png'}:{message_id:100}});
    },
  }));
  const db=await mf.getD1Database('DB');
  for (const name of ['0001_initial.sql','0002_ai_tags.sql']) {
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
  assert.deepEqual(captionMetadata('Mèo #cat #safe https://www.pixiv.net/a'),{title:'Mèo',tags:['cat'],rating:'safe',source_url:'https://www.pixiv.net/a'});
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
