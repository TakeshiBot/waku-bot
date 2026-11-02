import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';

let mf:Miniflare,gate:Promise<void>|undefined,release:()=>void=()=>{},dropDispatch=false,stallBody=false;
let peak=0,active=0;
const perApi=new Map<string,number>(),peaks=new Map<string,number>(),seen:Array<{host:string;image:string;model:string}>=[];
const imageIds=new Map<string,string>();
const configs=new Map([
  ['vision-a.example.com',{key:'key-a',model:'model-a',status:200}],
  ['vision-b.example.com',{key:'key-b',model:'model-b',status:200}],
  ['vision-c.example.com',{key:'key-c',model:'model-c',status:200}],
  ['vision-d.example.com',{key:'key-d',model:'model-d',status:401}],
]);
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
let original:any,b:any,c:any;
before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],
    bindings:{API_KEY:'key',OWNER_IDS:'101',PUBLIC_BASE_URL:'https://images.example.com',AI_BASE_URL:'https://vision-a.example.com/v1',AI_API_KEY:'key-a',AI_MODEL:'model-a'},
    outboundService:async(request:Request)=>{
      const url=new URL(request.url);
      if(url.hostname==='images.example.com'&&url.pathname==='/internal/jobs')return dropDispatch?new MockResponse(null,{status:503}):mf.dispatchFetch(url.href,{method:'POST',headers:Object.fromEntries(request.headers),body:await request.text()});
      const config=configs.get(url.hostname);assert.ok(config,'No other external network allowed');
      const body=await request.json() as any,uri=body.messages[1].content[1].image_url.url,image=imageIds.get(uri)!;
      assert.match(uri,/^data:image\/png;base64,/);assert.ok(image,'Vision receives the actual stored image, not a remote URL');
      assert.equal(request.headers.get('authorization'),'Bearer '+config.key);assert.equal(body.model,config.model);
      seen.push({host:url.hostname,image,model:body.model});active++;peak=Math.max(peak,active);perApi.set(url.hostname,(perApi.get(url.hostname)||0)+1);peaks.set(url.hostname,Math.max(peaks.get(url.hostname)||0,perApi.get(url.hostname)!));
      try{
        if(gate)await gate;await new Promise(resolve=>setTimeout(resolve,20));
        if(stallBody)return new MockResponse(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('{"choices":'));}}),{headers:{'content-type':'application/json'}});
        if(config.status!==200)return new MockResponse('{}',{status:config.status});
        return MockResponse.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify({title:'Tagged',tags:['anime','blue_hair'],rating:'safe',rating_confidence:1,image_type:'anime'})}}]});
      }finally{active--;perApi.set(url.hostname,perApi.get(url.hostname)!-1);}
    },
  }));
  const db=await mf.getD1Database('DB');for(const name of (await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort()){
    const sql=await readFile('migrations/'+name,'utf8');await db.batch(migrationStatements(sql).map(s=>db.prepare(s)));
  }
});
after(async()=>{release();await mf?.dispose();});
async function api(path:string,method='GET',body?:any){
  const request=new Request('https://images.example.com'+path,{method,headers:{authorization:'Bearer key',...(body&&!(body instanceof FormData)?{'content-type':'application/json'}:{})},...(body?{body:body instanceof FormData?body:JSON.stringify(body)}:{})});
  const response=await mf.dispatchFetch(request.url,{method,headers:Object.fromEntries(request.headers),...(body?{body:Buffer.from(await request.arrayBuffer())}:{})});assert.ok(response.ok,await response.clone().text());return response.json() as Promise<any>;
}
async function until(check:()=>Promise<boolean>){for(let i=0;i<200;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,20));}assert.fail('Timed out waiting for queue');}
async function images(start:number,count:number){const ids:string[]=[];for(let i=start;i<start+count;i++){const form=new FormData(),bytes=Buffer.concat([png,Buffer.from('parallel-'+i)]);form.set('file',new Blob([bytes]),'image.png');form.set('manual','true');const id=(await api('/v1/images','POST',form)).image.id;ids.push(id);imageIds.set('data:image/png;base64,'+bytes.toString('base64'),id);}return ids;}
async function enqueue(ids:string[]){const batch=await api('/v1/analysis-batches','POST',{});await api('/v1/analysis-batches/'+batch.id,'POST',{ids});return batch.id;}
async function complete(ids:string[]){const db=await mf.getD1Database('DB');await until(async()=>{const rows=await db.prepare('SELECT status FROM image_analysis WHERE image_id IN (SELECT value FROM json_each(?))').bind(JSON.stringify(ids)).all();return rows.results.length===ids.length&&rows.results.every(row=>row.status==='done');});await until(async()=>active===0&&(await db.prepare('SELECT COUNT(*) n FROM ai_provider_slots WHERE token IS NOT NULL').first())!.n===0);}
async function add(host:string){const config=configs.get(host)!;const result=await api('/v1/ai/providers','POST',{name:host,base_url:'https://'+host+'/v1',api_key:config.key});const provider=result.providers.find((p:any)=>p.base_url==='https://'+host+'/v1');const model=await api('/v1/ai/providers/'+provider.id+'/models','POST',{model:config.model});return model.providers.find((p:any)=>p.id===provider.id);}

test('migration preserves the selected API/model and encrypted keys without enabling other APIs',async()=>{
  const old=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB']}));
  try{
    const db=await old.getD1Database('DB');for(const name of (await readdir('migrations')).filter(n=>n.endsWith('.sql')&&n<'0007').sort()){
      const sql=await readFile('migrations/'+name,'utf8');await db.batch(sql.split(';').map(s=>s.trim()).filter(Boolean).map(s=>db.prepare(s)));
    }
    await db.batch([
      db.prepare("INSERT INTO ai_providers(id,name,base_url,key_cipher,preferred_model_id) VALUES ('a','A','https://a.site.com/v1','cipher-a','ma'),('b','B','https://b.site.com/v1','cipher-b','mb')"),
      db.prepare("INSERT INTO ai_models(id,provider_id,model) VALUES ('ma','a','model-a'),('mb','b','model-b')"),
      db.prepare("UPDATE ai_preferences SET initialized=1,provider_id='b',model_id='mb' WHERE id=1"),
    ]);
    const sql=await readFile('migrations/0007_parallel_ai.sql','utf8');await db.batch(sql.split(';').map(s=>s.trim()).filter(Boolean).map(s=>db.prepare(s)));
    const rows=(await db.prepare('SELECT id,enabled,preferred_model_id,key_cipher FROM ai_providers ORDER BY id').all()).results;
    assert.deepEqual(rows,[{id:'a',enabled:0,preferred_model_id:'ma',key_cipher:'cipher-a'},{id:'b',enabled:1,preferred_model_id:'mb',key_cipher:'cipher-b'}]);
    assert.equal((await db.prepare('SELECT COUNT(*) n FROM ai_provider_slots').first())!.n,4);
  }finally{await old.dispose();}
});

test('three enabled APIs reach six concurrent requests, use their own models and never process an image twice',async()=>{
  const initial=await api('/v1/ai/settings');original=initial.providers[0];b=await add('vision-b.example.com');c=await add('vision-c.example.com');
  await api('/v1/ai/selection','PUT',{provider_id:b.id,enabled:true});const settings=await api('/v1/ai/selection','PUT',{provider_id:c.id,enabled:true});
  assert.equal(settings.active_providers.length,3);assert.equal(settings.parallelism,6);
  const ids=await images(0,8);peak=0;gate=new Promise(resolve=>release=resolve);const before=seen.length,batch=await enqueue(ids);
  await until(async()=>active===6);assert.equal(peak,6);assert.equal(seen.length-before,6);
  const progress=await api('/v1/analysis-batches/'+batch);assert.equal(progress.running,6);assert.equal(progress.pending,2);
  await Promise.all(Array.from({length:3},()=>api('/v1/jobs/drain','POST',{})));assert.equal(seen.length-before,6);
  release();gate=undefined;await complete(ids);
  const calls=seen.slice(before);assert.equal(calls.length,8);assert.equal(new Set(calls.map(call=>call.image)).size,8);assert.ok([...peaks.values()].every(n=>n<=2));
  for(const host of ['vision-a.example.com','vision-b.example.com','vision-c.example.com'])assert.ok(calls.filter(call=>call.host===host).length>=2);
});

test('each API remembers its own model; disabling one leaves others running and stops new assignments',async()=>{
  const added=await api('/v1/ai/providers/'+b.id+'/models','POST',{model:'model-b-next'}),next=added.providers.find((p:any)=>p.id===b.id).models[1];
  configs.get('vision-b.example.com')!.model='model-b-next';
  const changed=await api('/v1/ai/selection','PUT',{provider_id:b.id,model_id:next.id});assert.equal(changed.active_providers.length,3);assert.ok(changed.active_providers.some((p:any)=>p.provider_id===original.id&&p.model==='model-a'));
  await api('/v1/ai/selection','PUT',{provider_id:c.id,enabled:false});
  const ids=await images(10,6),before=seen.length;gate=new Promise(resolve=>release=resolve);await enqueue(ids.slice(0,4));await until(async()=>active===4);
  const off=await api('/v1/ai/selection','PUT',{provider_id:original.id,enabled:false});assert.equal(off.active_providers.length,1);await enqueue(ids.slice(4));
  release();gate=undefined;await complete(ids);const calls=seen.slice(before);
  assert.equal(calls.filter(call=>call.host==='vision-a.example.com').length,2);assert.equal(calls.filter(call=>call.host==='vision-c.example.com').length,0);assert.equal(calls.filter(call=>call.host==='vision-b.example.com').length,4);
  const restored=await api('/v1/ai/selection','PUT',{provider_id:original.id,enabled:true});assert.equal(restored.providers.find((p:any)=>p.id===b.id).preferred_model_id,next.id);await api('/v1/ai/selection','PUT',{provider_id:original.id,enabled:false});
});
