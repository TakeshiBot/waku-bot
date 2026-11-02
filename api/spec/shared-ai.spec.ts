import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {readFile,readdir} from 'node:fs/promises';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {migrationStatements} from '../src/sql.ts';
import {createClient} from '@libsql/client';
import {LibsqlDatabase} from '../src/libsql.ts';
import {ensureSession,attachSessionImages,sharedSessionProgress,recordProviderOutcome} from '../src/sessions.ts';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64'),pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(test,note){for(let n=0;n<200;n++){if(await test())return;await pause(30);}throw Error('Timed out: '+note);}
async function harness(port){
 const origin='http://127.0.0.1:'+port,calls=[],config={fail:false,gate:null,stall:false},pending=[];let mf;
 mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',host:'127.0.0.1',port,
  assets:{directory:'public',binding:'ASSETS',run_worker_first:true,routerConfig:{has_user_worker:true}},d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'qa-key',OWNER_IDS:'101',PUBLIC_BASE_URL:origin,AI_BASE_URL:'https://vision-a.example.com/v1',AI_API_KEY:'qa-vision',AI_MODEL:'model'},
  outboundService:async request=>{
   const url=new URL(request.url);if(url.origin===origin&&url.pathname==='/internal/jobs')return mf.dispatchFetch(url.href,{method:'POST',headers:Object.fromEntries(request.headers),body:await request.text()});
   assert.ok(url.hostname.startsWith('vision-'));const body=await request.json();calls.push({host:url.hostname,image:body.messages[1].content[1].image_url.url});
   if(config.gate)await config.gate;await pause(8);
   if(config.stall)return new MockResponse(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{'))}}),{headers:{'content-type':'application/json'}});
   if(config.fail&&url.hostname==='vision-a.example.com')return new MockResponse('{}',{status:502});
   return MockResponse.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify({title:'Ảnh đã phân loại',tags:['anime','blue_hair'],rating:'safe',image_type:'anime'})}}]});
  },
 }));
 const db=await mf.getD1Database('DB');for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
 await mf.ready;
 const api=async(path,method='GET',body)=>{const r=await mf.dispatchFetch(origin+path,{method,headers:{authorization:'Bearer qa-key',...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});const value=await r.json();assert.ok(r.ok,JSON.stringify({path,status:r.status,error:value.error}));return value;};
 await api('/v1/ai/settings');
 const upload=async(count,prefix)=>{const ids=[];for(let i=0;i<count;i++){const r=await mf.dispatchFetch(origin+'/v1/images',{method:'POST',headers:{authorization:'Bearer qa-key','content-type':'application/octet-stream','x-waku-upload-metadata':encodeURIComponent(JSON.stringify({manual:true}))},body:Buffer.concat([png,Buffer.from(prefix+'-'+i)])});const data=await r.json();assert.ok(r.ok,JSON.stringify(data.error));ids.push(data.image.id);}return ids;};
 const enqueue=async ids=>{const batch=await api('/v1/analysis-batches','POST',{});await api('/v1/analysis-batches/'+batch.id,'POST',{ids});return batch.id;};
 const state=async()=>(await api('/v1/state')).ai_session;
 const cron=()=>{const p=mf.getWorker().then(w=>w.scheduled({cron:'* * * * *'}));pending.push(p);return p;};
 const close=async()=>{config.gate=null;await Promise.allSettled(pending);await mf.dispose();};
 return {mf,db,api,origin,config,calls,upload,enqueue,state,cron,close};
}

test('completed sessions retry only five failures in a fresh session after five minutes',async()=>{
 const h=await harness(8991);let release;
 try{
  const ids=await h.upload(15,'round'),batch=(await h.api('/v1/analysis-batches','POST',{})).id;
  await h.api('/v1/analysis/control','POST',{action:'pause',batch_id:batch});await h.api('/v1/analysis-batches/'+batch,'POST',{ids});
  const good=JSON.stringify(ids.slice(0,10)),bad=JSON.stringify(ids.slice(10));
  await h.db.batch([
   h.db.prepare("UPDATE image_analysis SET status=CASE WHEN image_id IN (SELECT value FROM json_each(?)) THEN 'done' ELSE 'failed' END,control='active',last_error=CASE WHEN image_id IN (SELECT value FROM json_each(?)) THEN NULL ELSE 'provider_timeout' END WHERE image_id IN (SELECT value FROM json_each(?))").bind(good,good,JSON.stringify(ids)),
   h.db.prepare("UPDATE images SET title='Done',rating='safe',image_type='anime' WHERE id IN (SELECT value FROM json_each(?))").bind(good),
   h.db.prepare("INSERT INTO image_tags SELECT value,'anime' FROM json_each(?)").bind(good),
   h.db.prepare('UPDATE analysis_batch_items SET status=(SELECT status FROM image_analysis a WHERE a.image_id=analysis_batch_items.image_id) WHERE batch_id=?').bind(batch),
  ]);
  await h.api('/v1/analysis/control','POST',{action:'resume',batch_id:batch});await until(async()=>(await h.state()).next_retry_at>0,'five-minute retry wait');
  const first=await h.state();assert.equal(first.total,15);assert.equal(first.done,10);assert.equal(first.failed,5);assert.ok(first.next_retry_at>Date.now()+290000);
  h.config.gate=new Promise(r=>release=r);await h.db.prepare('UPDATE analysis_sessions SET next_retry_at=? WHERE id=?').bind(Date.now()-1,first.session_id).run();h.cron();h.cron();
  await until(async()=>{const s=await h.state();return s.session_id!==first.session_id&&s.running===2;},'new five-image round');
  const a=await h.state(),b=await h.state();assert.equal(a.id,b.id);assert.notEqual(a.session_id,first.session_id);assert.notEqual(a.id,first.id);assert.equal(a.total,5);assert.equal(a.done,0);assert.equal(a.pending+a.running,5);
  release();h.config.gate=null;await until(async()=>(await h.state()).phase==='done','automatic round completes');const done=await h.state();assert.equal(done.total,5);assert.equal(done.done,5);assert.equal(h.calls.length,5);assert.equal((await h.db.prepare('SELECT done,failed FROM analysis_batch_counts WHERE batch_id=?').bind(batch).first()).done,10);
  console.log('PASS completed session, five-minute wait, fresh session 0/5, completed images excluded');
 }finally{release?.();await h.close();}
});

test('provider rests 30s then 60s, third strike completes the session, automatic retry resets health',async()=>{
 const h=await harness(8992);let release;
 try{
  h.config.fail=true;const ids=await h.upload(14,'breaker');await h.enqueue(ids);
  await until(async()=>{const s=await h.state();return s.providers[0]?.strikes===1&&s.running===0;},'provider first rest');
  const rested=await h.state(),n=h.calls.length;assert.equal(rested.providers[0].disabled,0);assert.ok(rested.providers[0].cooldown_until>Date.now()+20000);assert.ok(rested.pending>0);await h.cron();assert.equal(h.calls.length,n);
  await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=0 WHERE session_id=?').bind(rested.session_id).run();h.cron();
  await until(async()=>{const s=await h.state();return s.providers[0].strikes===2&&s.running===0;},'second 60s rest');const second=await h.state();assert.equal(second.providers[0].disabled,0);assert.ok(second.providers[0].cooldown_until>Date.now()+50000);await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=0 WHERE session_id=?').bind(second.session_id).run();h.cron();
  await until(async()=>(await h.state()).providers[0].disabled===1&&(await h.state()).phase==='done','provider disabled');const stopped=await h.state();assert.equal(stopped.providers[0].disabled,1);assert.equal(stopped.providers[0].strikes,3);assert.equal(stopped.stop_reason,'providers_exhausted');assert.equal(stopped.pending+stopped.running,0);
  const before=h.calls.length;await h.cron();await h.cron();assert.equal(h.calls.length,before);assert.equal((await h.db.prepare('SELECT enabled FROM ai_providers').first()).enabled,1);
  h.config.fail=false;h.config.gate=new Promise(r=>release=r);await h.db.prepare('UPDATE analysis_sessions SET next_retry_at=? WHERE id=?').bind(Date.now()-1,stopped.session_id).run();h.cron();await until(async()=>(await h.state()).session_id!==stopped.session_id&&(await h.state()).running===2,'fresh automatic session');const retried=await h.state();
  assert.notEqual(retried.session_id,stopped.session_id);assert.equal(retried.total,14);assert.equal(retried.done,0);assert.equal(retried.providers[0].disabled,0);assert.equal(retried.providers[0].strikes,0);
  release();h.config.gate=null;await until(async()=>(await h.state()).phase==='done','manual new session completes');
  console.log('PASS per-provider 30s/60s rest, third strike, automatic fresh session resets all health');
 }finally{release?.();await h.close();}
});

test('an unhealthy API never pauses healthy providers and retries keep health state',async()=>{
 const h=await harness(8993);
 try{
  const settings=await h.api('/v1/ai/providers','POST',{name:'Healthy B',base_url:'https://vision-b.example.com/v1',api_key:'qa-b'}),p=settings.providers.find(p=>p.name==='Healthy B');
  const models=await h.api('/v1/ai/providers/'+p.id+'/models','POST',{model:'model-b'}),model=models.providers.find(q=>q.id===p.id).models[0];await h.api('/v1/ai/selection','PUT',{provider_id:p.id,model_id:model.id,enabled:true});
  h.config.fail=true;await h.enqueue(await h.upload(24,'two-api'));await until(async()=>{const s=await h.state();if(s.phase==='active'&&s.pending&&!s.running)await h.cron();return s.phase==='done'&&s.next_retry_at>0;},'other provider drains work');const partial=await h.state();assert.ok(partial.done>=20);assert.ok(partial.failed>0);assert.equal(partial.providers.find(x=>x.name==='Healthy B').disabled,0);
  await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=? WHERE session_id=? AND provider_id!=?').bind(Date.now()+30000,partial.session_id,p.id).run();await h.db.prepare('UPDATE analysis_sessions SET next_retry_at=? WHERE id=?').bind(Date.now()-1,partial.session_id).run();await h.api('/v1/ai/selection','PUT',{provider_id:partial.providers.find(x=>x.name!=='Healthy B').provider_id,enabled:false});const n=h.calls.length;h.cron();await until(async()=>{const s=await h.state();return s.session_id!==partial.session_id&&s.phase==='done';},'healthy API retries failures');assert.ok(h.calls.slice(n).every(call=>call.host==='vision-b.example.com'));
  console.log('PASS independent provider rest and healthy-provider recovery');
 }finally{await h.close();}
});

test('pause holds late additions, cancel rejects late results, manual retry starts at 0/4',async()=>{
 const h=await harness(8995);let release;
 try{
  const ids=await h.upload(6,'controls');h.config.gate=new Promise(r=>release=r);const batch=await h.enqueue(ids.slice(0,4));
  await until(async()=>(await h.state()).running===2,'held requests');
  await h.api('/v1/analysis/control','POST',{action:'pause',batch_id:batch});await h.api('/v1/analysis-batches/'+batch,'POST',{ids:ids.slice(4)});
  release();h.config.gate=null;await until(async()=>{const s=await h.state();return s.done===2&&!s.running;},'paused active requests finish');
  const paused=await h.state();assert.equal(paused.phase,'paused');assert.equal(paused.pending,4);await h.cron();assert.equal(h.calls.length,2);
  h.config.gate=new Promise(r=>release=r);await h.api('/v1/analysis/control','POST',{action:'resume',batch_id:batch});await until(async()=>(await h.state()).running===2,'resumed requests');
  const cancelled=(await h.api('/v1/analysis/control','POST',{action:'cancel',batch_id:batch})).progress;assert.equal(cancelled.cancelled,4);assert.equal(cancelled.done,2);
  release();h.config.gate=null;await until(async()=>(await h.db.prepare('SELECT COUNT(*) n FROM ai_provider_slots WHERE token IS NOT NULL').first()).n===0,'late leases released');
  const rows=await h.db.prepare("SELECT COUNT(*) n FROM images WHERE id IN (SELECT value FROM json_each(?)) AND title!=''").bind(JSON.stringify(ids)).first();assert.equal(rows.n,2,'cancelled inference cannot write metadata');
  h.config.gate=new Promise(r=>release=r);const retried=(await h.api('/v1/analysis/control','POST',{action:'retry',batch_id:batch})).progress;
  assert.notEqual(retried.id,batch);assert.equal(retried.total,4);assert.equal(retried.done,0);
  release();h.config.gate=null;await until(async()=>(await h.state()).phase==='done','only cancelled images complete');assert.equal((await h.state()).done,4);
 }finally{release?.();await h.close();}
});

test('stalled vision response times out once per round and cancels scheduled retries',async()=>{
 const h=await harness(8996);
 try{
  h.config.stall=true;const ids=await h.upload(1,'timeout'),started=Date.now(),batch=await h.enqueue(ids);
  for(let n=0;n<60;n++){if((await h.state()).next_retry_at>0)break;await pause(200);}
  const state=await h.state();assert.equal(state.phase,'done');assert.ok(state.next_retry_at>Date.now()+290000);assert.equal(state.failed,1);assert.equal(h.calls.length,1);assert.ok(Date.now()-started<14000);
  const job=await h.db.prepare('SELECT last_error,attempts FROM image_analysis WHERE image_id=?').bind(ids[0]).first();assert.equal(job.last_error,'provider_timeout');assert.equal(job.attempts,1);
  await h.api('/v1/analysis/control','POST',{action:'cancel',batch_id:batch});await h.db.prepare('UPDATE analysis_sessions SET next_retry_at=0').run();await h.cron();assert.equal(h.calls.length,1);assert.equal((await h.state()).phase,'cancelled');
 }finally{await h.close();}
});

test('libSQL attaches delayed uploads to the current round and honours a concurrent cancellation',async()=>{
 const client=createClient({url:'file::memory:'}),db=new LibsqlDatabase(client),env={DB:db} as any;
 try{
  await db.prepare('PRAGMA foreign_keys=ON').run();
  for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
  for(let n=0;n<3;n++){
   await db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,random_key,created_at) VALUES (?,?,?,'x.jpg','image/jpeg',10,.5,0)").bind('i'+n,'h'+n,'o'+n).run();
   await db.prepare("INSERT INTO image_analysis(image_id,status) VALUES (?,'pending')").bind('i'+n).run();
  }
  const stale=await ensureSession(env);await db.prepare("UPDATE analysis_sessions SET phase='done' WHERE id=?").bind(stale.id).run();
  await attachSessionImages(env,stale,['i0']);const current=await ensureSession(env);assert.notEqual(current.id,stale.id);
  assert.equal((await sharedSessionProgress(env)).total,1);assert.equal((await db.prepare('SELECT total FROM analysis_batch_counts WHERE batch_id=?').bind(stale.current_batch_id).first()).total,0);
  const next=crypto.randomUUID();await db.batch([
   db.prepare('INSERT INTO analysis_batches(id,created_at) VALUES (?,?)').bind(next,Date.now()),
   db.prepare('INSERT INTO analysis_session_batches VALUES (?,?,2)').bind(next,current.id),
   db.prepare('UPDATE analysis_sessions SET current_batch_id=?,round=2 WHERE id=?').bind(next,current.id),
  ]);
  await attachSessionImages(env,current,['i1']);assert.equal((await sharedSessionProgress(env)).id,next);assert.equal((await sharedSessionProgress(env)).total,1);
  await db.prepare("UPDATE analysis_sessions SET phase='cancelled' WHERE id=?").bind(current.id).run();
  await attachSessionImages(env,current,['i2']);const cancelled=await db.prepare("SELECT status,control FROM image_analysis WHERE image_id='i2'").first();
  assert.equal(cancelled.status,'skipped');assert.equal(cancelled.control,'cancelled');assert.equal((await sharedSessionProgress(env)).phase,'cancelled');
 }finally{client.close();}
});

test('any successful image resets both error streak and backoff, including an in-flight success during rest',async()=>{
 const h=await harness(8997),env={DB:h.db} as any;
 try{
  const [id]=await h.upload(1,'reset'),session=await ensureSession(env);await attachSessionImages(env,session,[id]);
  const p=await h.db.prepare('SELECT id,config_version FROM ai_providers').first();
  const outcome=async(code)=>{const token=crypto.randomUUID();await h.db.prepare('UPDATE image_analysis SET lease_token=? WHERE image_id=?').bind(token,id).run();await recordProviderOutcome(env,id,token,p.id,p.config_version,code);return(await sharedSessionProgress(env)).providers[0];};
  const failThree=async()=>{await outcome('provider_timeout');await outcome('provider_timeout');return outcome('provider_timeout');};
  let health=await failThree();assert.equal(health.strikes,1);assert.ok(health.cooldown_until>Date.now()+29000);
  await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=0').run();health=await failThree();assert.equal(health.strikes,2);assert.ok(health.cooldown_until>Date.now()+59000);
  health=await outcome(null);assert.equal(health.failures,0);assert.equal(health.strikes,0);assert.equal(health.cooldown_until,0);
  health=await failThree();assert.equal(health.strikes,1);assert.ok(health.cooldown_until>Date.now()+29000);assert.ok(health.cooldown_until<Date.now()+31000);
  await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=0').run();await failThree();
  await h.db.prepare('UPDATE analysis_session_providers SET cooldown_until=0').run();health=await failThree();assert.equal(health.disabled,1);assert.equal(health.strikes,3);assert.equal(health.cooldown_until,0);
  health=await outcome(null);assert.equal(health.disabled,0);assert.equal(health.strikes,0);assert.equal(health.failures,0);
 }finally{await h.close();}
});

