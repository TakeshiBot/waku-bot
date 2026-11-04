import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';
import {mediaType} from '../src/input.ts';
import {save,MAX_VIDEO_BYTES} from '../src/store.ts';
import {attachBatch,createBatch} from '../src/batches.ts';

const mp4=Buffer.concat([Buffer.from([0,0,0,24]),Buffer.from('ftypisom'),Buffer.from([0,0,2,0]),Buffer.from('isomiso2'),Buffer.from([0,0,0,8]),Buffer.from('mdat')]);
const payload=(data:Buffer)=>Uint8Array.from(data).buffer;
test('video MIME detection distinguishes AVIF/images and rejects unsupported content',()=>{
 assert.equal(mediaType(payload(mp4)).mime,'video/mp4');
 assert.equal(mediaType(payload(Buffer.from([0x1a,0x45,0xdf,0xa3,0x42,0x82,0x84,...Buffer.from('webm'),0,0,0,0,0,0]))).mime,'video/webm');
 assert.throws(()=>mediaType(payload(Buffer.from('<html>fake.mp4</html>'))));
});

test('videos upload manually, filter separately, remain outside AI and support byte ranges',async()=>{
 const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'video-qa',PUBLIC_BASE_URL:'https://media.example.com'},outboundService:async(request:Request)=>{
  if(new URL(request.url).hostname==='cdn.example.com')return new MockResponse(mp4,{headers:{'content-type':'video/mp4'}});
  throw Error('Unexpected upstream request');
 }}));
 try{
  const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('IMAGES');
  for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
  const call=(path:string,options:any={})=>mf.dispatchFetch('https://media.example.com'+path,{...options,headers:{authorization:'Bearer video-qa',...options.headers}});
  const upload=await call('/v1/images',{method:'POST',headers:{'content-type':'application/octet-stream','x-waku-upload-metadata':encodeURIComponent(JSON.stringify({filename:'clip.mp4',title:'Cat clip',tags:['cat_ears'],rating:'safe',image_type:'anime',manual:false}))},body:mp4});
  assert.equal(upload.status,201);const row=(await upload.json() as any).image;assert.equal(row.media_type,'video');assert.equal(row.ai_status,'off');assert.equal(row.classified,true);
  const imageId=crypto.randomUUID();await db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) VALUES (?,?,?,'x.jpg','image/jpeg',10,'Cat image','safe',0.5,0,'anime')").bind(imageId,'qa-image','qa-image').run();await db.prepare("INSERT INTO image_tags VALUES (?,'cat_ears')").bind(imageId).run();
  const videos=await(await call('/v1/images/random?media_type=video&count=10&tags=cat_ears')).json() as any;assert.deepEqual(videos.images.map((x:any)=>x.id),[row.id]);
  assert.deepEqual(((await(await call('/v1/images/random?count=10')).json()) as any).images.map((x:any)=>x.id),[imageId]);
  assert.deepEqual(((await(await call('/v1/images/random?media_type=video&tags=missing')).json()) as any).images,[]);
  for(const [media,expected]of [['video',row.id],['image',imageId]]){const result=await(await call('/v1/images?media_types='+media)).json() as any;assert.deepEqual(result.images.map((x:any)=>x.id),[expected]);}
  assert.equal((await call('/v1/images?media_types=pdf')).status,400);
  assert.equal((await call('/v1/images/'+row.id+'/analyze',{method:'POST'})).status,400);
  const env:any={DB:db,IMAGES:bucket};const batch=await createBatch(env);assert.equal(await attachBatch(env,batch,[row.id],true),0);
  assert.equal((await db.prepare('SELECT COUNT(*) n FROM image_analysis').first() as any).n,0);
  const full=await call('/media/'+row.id);assert.deepEqual(Buffer.from(await full.arrayBuffer()),mp4);
  const range=await call('/media/'+row.id,{headers:{range:'bytes=4-11'}});assert.equal(range.status,206);assert.equal(range.headers.get('content-range'),`bytes 4-11/${mp4.length}`);assert.deepEqual(Buffer.from(await range.arrayBuffer()),mp4.subarray(4,12));
  const suffix=await call('/media/'+row.id,{headers:{range:'bytes=-4'}});assert.equal(suffix.status,206);assert.deepEqual(Buffer.from(await suffix.arrayBuffer()),mp4.subarray(-4));
  assert.equal((await call('/media/'+row.id,{headers:{range:'bytes=99999-'}})).status,416);
  assert.equal((await call('/media/'+row.id,{headers:{'if-none-match':full.headers.get('etag')}})).status,304);
  assert.equal((await call('/media/'+row.id,{method:'HEAD'})).headers.get('content-length'),String(mp4.length));
  const imported=await call('/v1/images',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url:'https://cdn.example.com/video.mp4'})});assert.equal(imported.status,200,'URL import detects the same video and deduplicates it');
  const oversized=new Uint8Array(MAX_VIDEO_BYTES+1);oversized.set(mp4);
  await assert.rejects(save(env,oversized.buffer,{title:'Large',tags:[],rating:'unknown',source_url:null},null),(error:any)=>error.status===413);
  assert.equal((await bucket.list()).objects.length,1,'oversized upload reserves no storage');
 }finally{await mf.dispose();}
});
