import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';
import {getBatch,batchProgress,queueProgress} from '../src/batches.ts';
import {isD1DailyLimit,recordD1Limit,assertD1Available,protectD1} from '../src/d1-quota.ts';
import {NEXT_ANALYSIS_SQL} from '../src/queue.ts';

test('large upload batches validate in one indexed lookup; queue indexes, counters and state cache remain correct',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'key',PUBLIC_BASE_URL:'https://read-qa.example.com'}}));
  try{
    const db=await mf.getD1Database('DB');
    for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')&&n<'0011').sort()){
      await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    }
    const ids=Array.from({length:2000},(_,n)=>'10000000-0000-4000-8000-'+String(n).padStart(12,'0')),batch='20000000-0000-4000-8000-000000000001';
    await db.batch([
      db.prepare(`INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at)
        SELECT value,value,value,'x.jpg','image/jpeg',100,'QA','unknown',.5,key FROM json_each(?)`).bind(JSON.stringify(ids)),
      db.prepare("INSERT INTO image_analysis(image_id,status) SELECT id,'pending' FROM images"),
      db.prepare('INSERT INTO analysis_batches(id,created_at) VALUES (?,?)').bind(batch,Date.now()),
      db.prepare("INSERT INTO analysis_batch_items(batch_id,image_id,status) SELECT ?,id,'pending' FROM images").bind(batch),
    ]);
    const next=NEXT_ANALYSIS_SQL;
    const oldPlan=(await db.prepare('EXPLAIN QUERY PLAN '+next.replace(' INDEXED BY analysis_ready','')).bind(Date.now()).all()).results.map(row=>String(row.detail)).join(' ');
    const call=(path:string,method='GET',body?:unknown)=>mf.dispatchFetch('https://read-qa.example.com'+path,{method,headers:{authorization:'Bearer key',...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
    // Runtime installation must work for a database that could not be migrated
    // until the free quota reset, and must not depend on running a local CLI.
    assert.equal((await call('/v1/session')).status,200);
    const plan=(await db.prepare('EXPLAIN QUERY PLAN '+next).bind(Date.now()).all()).results.map(row=>String(row.detail)).join(' ');
    assert.ok(oldPlan.includes('TEMP B-TREE'));assert.ok(plan.includes('analysis_ready'),plan);assert.ok(!plan.includes('TEMP B-TREE'),plan);
    const queries:string[]=[],env={DB:{prepare(sql:string){queries.push(sql);return db.prepare(sql);}}} as any;
    await getBatch(env,batch);assert.equal(queries.length,1);assert.ok(!queries[0].includes('COUNT'));
    const progress=await batchProgress({DB:db} as any,batch);assert.equal(progress.pending,2000);
    async function compare(){
      const actual=await queueProgress({DB:db} as any),expected=await db.prepare(`SELECT COUNT(*) total,
        COALESCE(SUM(status='pending'),0) pending,COALESCE(SUM(status='running'),0) running,
        COALESCE(SUM(status='done'),0) done,COALESCE(SUM(status='failed'),0) failed,
        COALESCE(SUM(status='skipped' AND COALESCE(last_error,'')!='cancelled'),0) skipped,
        COALESCE(SUM(control='cancelled'),0) cancelled,COALESCE(SUM(status='pending' AND control='paused'),0) paused
        FROM image_analysis`).first();
      for(const [name,value]of Object.entries(expected!))assert.equal(actual[name],value,name);
    }
    await compare();
    await db.batch([
      db.prepare("UPDATE image_analysis SET status='done' WHERE image_id IN (SELECT id FROM images WHERE created_at<500)"),
      db.prepare("UPDATE image_analysis SET status='skipped',control='cancelled',last_error='cancelled' WHERE image_id=?").bind(ids[500]),
      db.prepare("UPDATE image_analysis SET control='paused' WHERE image_id=?").bind(ids[501]),
      db.prepare("UPDATE image_analysis SET status='running' WHERE image_id=?").bind(ids[502]),
      db.prepare("UPDATE image_analysis SET status='failed',last_error='provider_timeout' WHERE image_id=?").bind(ids[503]),
    ]);await compare();
    await db.prepare("UPDATE image_analysis SET status='pending',control='active',last_error=NULL WHERE image_id=?").bind(ids[500]).run();
    await db.prepare('DELETE FROM images WHERE id=?').bind(ids[503]).run();await compare();
    const schema=await readFile('migrations/0011_read_efficiency.sql','utf8');await db.batch(migrationStatements(schema).map(sql=>db.prepare(sql)));await compare();
    const cold=await call('/v1/state');assert.equal(cold.status,200);assert.equal(cold.headers.get('x-waku-stats-cache'),'miss');
    const hot=await call('/v1/state');assert.equal(hot.headers.get('x-waku-stats-cache'),'hit');
    await call('/v1/images/'+ids[0],'PATCH',{title:'Classified',tags:['anime'],rating:'safe',image_type:'anime'});
    const changed=await call('/v1/state');assert.equal(changed.headers.get('x-waku-stats-cache'),'miss');assert.equal((await changed.json() as any).tabs.safe,1);
    assert.equal((await call('/v1/state')).headers.get('x-waku-stats-cache'),'hit');
  }finally{await mf.dispose();}
});

test('daily quota circuit persists across isolates, skips repeated writes and does not mistake ordinary SQL errors for quota',async()=>{
  const files=new Map<string,string>();let writes=0;
  const bucket={async get(key:string){return files.has(key)?{json:async()=>JSON.parse(files.get(key)!)}:null;},async put(key:string,value:string){writes++;files.set(key,value);}};
  const env={DB:{},IMAGES:bucket} as any,error=new Error('D1_ERROR: Your account has exceeded the daily limit for rows read');
  assert.ok(isD1DailyLimit(error));assert.ok(isD1DailyLimit(new Error('D1_ERROR: maximum number of rows read per day exceeded')));
  assert.ok(isD1DailyLimit(new Error("D1_ERROR: Your account has exceeded D1's free tier daily row read limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue.")));
  assert.equal(isD1DailyLimit(new Error('D1_ERROR: no such table: abc')),false);
  assert.equal(isD1DailyLimit(new Error('API rate limit exceeded')),false);
  assert.equal(await recordD1Limit(env,error),true);assert.equal(await recordD1Limit(env,error),true);assert.equal(writes,1);
  await assert.rejects(()=>assertD1Available({DB:{},IMAGES:bucket} as any),e=>e instanceof Error&&(e as any).code==='d1_daily_limit'&&(e as any).retryAfter>0);
  let called=false;await protectD1(env,async()=>{called=true;});assert.equal(called,false);
  files.clear();await assertD1Available({DB:{},IMAGES:bucket} as any);
});

test('a blocked API returns actionable 503 and Retry-After without database queries; health stays available',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'key'}}));
  try{
    await(await mf.getR2Bucket('IMAGES')).put('_system/d1-daily-limit.json',JSON.stringify({until:Date.now()+60000}));
    // DB deliberately has no schema: touching it would fail differently.
    const blocked=await mf.dispatchFetch('https://read-qa.example.com/v1/state',{headers:{authorization:'Bearer key'}});
    assert.equal(blocked.status,503);assert.ok(Number(blocked.headers.get('retry-after'))>0);assert.equal((await blocked.json() as any).error.code,'d1_daily_limit');
    assert.equal((await mf.dispatchFetch('https://read-qa.example.com/health')).status,200);
  }finally{await mf.dispose();}
});
