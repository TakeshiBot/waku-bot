import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {inflateSync} from 'node:zlib';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';

const palette=new Map(['red:255,0,0','green:0,180,0','blue:0,0,255','yellow:255,255,0','purple:160,0,200','orange:255,140,0'].map(x=>{const [name,rgb]=x.split(':');return [rgb,name];}));
function inspectPng(uri:string){
  const bytes=Buffer.from(uri.split(',')[1],'base64'),data:Buffer[]=[];let width=0;
  assert.deepEqual([...bytes.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  for(let at=8;at<bytes.length;){const n=bytes.readUInt32BE(at),name=bytes.toString('ascii',at+4,at+8);if(name==='IHDR')width=bytes.readUInt32BE(at+8);if(name==='IDAT')data.push(bytes.subarray(at+8,at+8+n));at+=n+12;}
  assert.equal(width,160);const raw=inflateSync(Buffer.concat(data)),answer:Record<string,string>={};
  for(const [name,x,y]of [['top_left',40,40],['top_right',120,40],['bottom_left',40,120],['bottom_right',120,120]] as const){const at=y*(width*3+1)+1+x*3,color=palette.get([...raw.subarray(at,at+3)].join(','));assert.ok(color);answer[name]=color;}
  assert.equal(new Set(Object.values(answer)).size,4);return answer;
}
let mf:Miniflare,modelId:string,providerId:string,mode='ok',calls=0;
before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'qa-key',PUBLIC_BASE_URL:'https://probe.example.com',AI_BASE_URL:'https://default.example.com/v1',AI_API_KEY:'default-key',AI_MODEL:'default-model'},
    outboundService:async request=>{
      assert.equal(new URL(request.url).href,'https://target.example.com/v1/chat/completions');calls++;
      assert.equal(request.headers.get('authorization'),'Bearer private-target-key');const body=await request.json() as any;assert.equal(body.model,'target-vision');assert.equal(body.stream,false);
      const answer=inspectPng(body.messages[1].content[1].image_url.url);
      if(mode==='http')return new MockResponse('private-target-key upstream error',{status:401});
      if(mode==='refusal')return MockResponse.json({choices:[{message:{refusal:'blocked'}}]});
      if(mode==='stall')return new MockResponse(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{'));}}),{headers:{'content-type':'application/json'}});
      if(mode==='wrong')return MockResponse.json({choices:[{message:{content:JSON.stringify({error:'cannot_see_image'})}}]});
      if(mode==='native')return MockResponse.json({response:{candidates:[{content:{parts:[{thought:true,text:'hidden'},{text:JSON.stringify(answer)}]}}]}});
      return MockResponse.json({choices:[{message:{content:JSON.stringify(answer)}}]});
    }}));
  const db=await mf.getD1Database('DB');for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
  let data=(await api('/v1/ai/providers','POST',{name:'Disabled test API',base_url:'https://target.example.com/v1',api_key:'private-target-key'})).data;
  providerId=data.providers.find((p:any)=>p.name==='Disabled test API').id;
  data=(await api('/v1/ai/providers/'+providerId+'/models','POST',{model:'target-vision'})).data;modelId=data.providers.find((p:any)=>p.id===providerId).models[0].id;
});
after(async()=>{await mf?.dispose();});
async function api(path:string,method='GET',body?:unknown,authenticated=true){
  const r=await mf.dispatchFetch('https://probe.example.com'+path,{method,headers:{...(authenticated?{authorization:'Bearer qa-key'}:{}),...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});return {status:r.status,data:await r.json() as any};
}
test('vision test verifies random image pixels with the saved target key/model without enabling it or changing selections',async()=>{
  const before=(await api('/v1/ai/settings')).data;
  assert.equal(before.providers.find((p:any)=>p.id===providerId).models.find((m:any)=>m.id===modelId).test_elapsed_ms,null);
  for(const kind of ['ok','native']){
    mode=kind;const result=await api('/v1/ai/models/'+modelId+'/test','POST',{});assert.equal(result.status,200);assert.equal(result.data.ok,true);assert.equal(result.data.code,'vision_verified');assert.ok(result.data.elapsed_ms>=0);
    // A fresh request (as from another browser) reads the persisted result.
    const settings=(await api('/v1/ai/settings')).data;
    assert.equal(settings.providers.find((p:any)=>p.id===providerId).models.find((m:any)=>m.id===modelId).test_elapsed_ms,result.data.elapsed_ms);
  }
  const withoutTimings=(settings:any)=>({...settings,providers:settings.providers.map((p:any)=>({...p,models:p.models.map(({test_elapsed_ms,...m}:any)=>m)}))});
  assert.deepEqual(withoutTimings((await api('/v1/ai/settings')).data),withoutTimings(before));
  const db=await mf.getD1Database('DB');assert.equal((await db.prepare('SELECT COUNT(*) n FROM ai_provider_slots WHERE token IS NOT NULL').first())!.n,0);
  assert.equal((await db.prepare('SELECT COUNT(*) n FROM images').first())!.n,0);assert.equal((await(await mf.getR2Bucket('IMAGES')).list()).objects.length,0);
});
test('wrong answers, provider refusal and HTTP errors never report verified vision or expose upstream secrets',async()=>{
  const db=await mf.getD1Database('DB');
  for(const [kind,code]of [['wrong','image_mismatch'],['refusal','content_filtered'],['http','http_401']]){
    await db.prepare('UPDATE ai_model_tests SET elapsed_ms=123456 WHERE model_id=?').bind(modelId).run();
    mode=kind;const result=await api('/v1/ai/models/'+modelId+'/test','POST',{});assert.equal(result.status,200);assert.equal(result.data.ok,false);assert.equal(result.data.code,code);assert.ok(!JSON.stringify(result.data).includes('private-target-key'));
    assert.equal((await db.prepare('SELECT elapsed_ms FROM ai_model_tests WHERE model_id=?').bind(modelId).first())!.elapsed_ms,result.data.elapsed_ms);
  }
});
test('vision test respects provider slot limits and admin authentication',async()=>{
  const db=await mf.getD1Database('DB'),before=calls;
  const previous=await db.prepare('SELECT elapsed_ms FROM ai_model_tests WHERE model_id=?').bind(modelId).first();
  await db.prepare("UPDATE ai_provider_slots SET token='busy-'||slot,lease_until=? WHERE provider_id=?").bind(Date.now()+60000,providerId).run();
  try{assert.equal((await api('/v1/ai/models/'+modelId+'/test','POST',{})).status,429);assert.equal(calls,before);}finally{await db.prepare('UPDATE ai_provider_slots SET token=NULL,lease_until=0 WHERE provider_id=?').bind(providerId).run();}
  assert.equal((await api('/v1/ai/models/'+modelId+'/test','POST',{},false)).status,401);assert.equal(calls,before);
  assert.deepEqual(await db.prepare('SELECT elapsed_ms FROM ai_model_tests WHERE model_id=?').bind(modelId).first(),previous);
});
test('a stalled body stops at the ten-second deadline and releases the provider slot',async()=>{
  mode='stall';const start=Date.now(),result=await api('/v1/ai/models/'+modelId+'/test','POST',{});assert.equal(result.data.code,'timeout');assert.equal(result.data.ok,false);assert.ok(Date.now()-start<12500);
  assert.equal((await(await mf.getD1Database('DB')).prepare('SELECT COUNT(*) n FROM ai_provider_slots WHERE token IS NOT NULL').first())!.n,0);
});
