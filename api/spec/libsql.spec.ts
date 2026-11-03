import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createClient} from '@libsql/client';
import {LibsqlDatabase,withDatabase} from '../src/libsql.ts';
import type {Env} from '../src/types.ts';
import {migrationStatements} from '../src/sql.ts';

test('libSQL adapter preserves migrations, atomic batches, result metadata and cascades',async()=>{
  const client=createClient({url:'file::memory:'});
  const db=new LibsqlDatabase(client);
  try{
    await db.prepare('PRAGMA foreign_keys=ON').run();
    for(const name of (await readdir('migrations')).filter(x=>x.endsWith('.sql')).sort()){
      const sql=await readFile('migrations/'+name,'utf8');
      await db.batch(migrationStatements(sql).map(s=>db.prepare(s)));
    }
    await db.prepare('CREATE TABLE adapter_test(id INTEGER PRIMARY KEY,value TEXT)').run();
    await assert.rejects(db.batch([
      db.prepare('INSERT INTO adapter_test VALUES (?,?)').bind(1,'one'),
      db.prepare('INSERT INTO adapter_test VALUES (?,?)').bind(1,'duplicate'),
    ]));
    assert.equal((await db.prepare('SELECT COUNT(*) n FROM adapter_test').first<{n:number}>())?.n,0);
    const result=await db.prepare('INSERT INTO adapter_test VALUES (?,?)').bind(2,'two').run();
    assert.equal(result.meta.changes,1);
    assert.equal((await db.prepare('UPDATE adapter_test SET value=? WHERE id=? RETURNING id').bind('two',2).all<{id:number}>()).results[0].id,2);
    assert.equal(await db.prepare('SELECT value FROM adapter_test WHERE id=?').bind(2).first('value'),'two');
    const image='6e7e331d-552d-4294-bb09-8345b56519bc';
    await db.batch([
      db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at) VALUES (?,?,'a','a.png','image/png',1,'','unknown',0,0)").bind(image,'hash'),
      db.prepare("INSERT INTO image_tags(image_id,tag) VALUES (?,'anime')").bind(image),
      db.prepare("INSERT INTO image_analysis(image_id,status) VALUES (?,'pending')").bind(image),
    ]);
    assert.equal((await db.prepare("SELECT n FROM analysis_status_counts WHERE status='pending' AND control='active'").first<{n:number}>())?.n,1);
    await db.prepare('DELETE FROM images WHERE id=?').bind(image).run();
    assert.equal((await db.prepare('SELECT COUNT(*) n FROM image_tags WHERE image_id=?').bind(image).first<{n:number}>())?.n,0);
    assert.equal((await db.prepare("SELECT n FROM analysis_status_counts WHERE status='pending' AND control='active'").first<{n:number}>())?.n,0);
  }finally{client.close();}
});

test('database outages are reported separately from SQL errors',async()=>{
  const outage=Object.assign(new Error('Server returned HTTP status 530'),{code:'SERVER_ERROR'});
  const client={execute:async()=>{throw outage;},batch:async()=>{throw outage;}} as any;
  const db=new LibsqlDatabase(client);
  const unavailable=(error:unknown)=>{
    const e=error as {status:number;code:string};return e.status===503&&e.code==='database_unavailable';
  };
  await assert.rejects(db.prepare('SELECT 1').first(),unavailable);
  await assert.rejects(db.batch([db.prepare('UPDATE x SET a=1')]),unavailable);
  const sqlError=Object.assign(new Error('no such table'),{code:'SQLITE_ERROR'});
  const broken=new LibsqlDatabase({execute:async()=>{throw sqlError;}} as any);
  await assert.rejects(broken.prepare('SELECT 1').first(),e=>e===sqlError);
});

test('private libSQL uses the VPC binding with authentication and a deadline',async()=>{
  const requests:Request[]=[];
  const service={fetch:async function(input:RequestInfo|URL,init?:RequestInit){
    assert.equal(this,service);
    const request=new Request(input,init);requests.push(request);
    const payload=await request.json() as {requests:{type:string}[]};
    return Response.json({baton:null,base_url:null,results:payload.requests.map(r=>({type:'ok',response:r.type==='execute'?{
      type:'execute',result:{cols:[{name:'n',decltype:'INTEGER'}],rows:[[{type:'integer',value:'1'}]],affected_row_count:0,last_insert_rowid:null},
    }:{type:'close'}}))});
  }};
  const env=withDatabase({DATABASE_BACKEND:'libsql',LIBSQL_URL:'http://127.0.0.1:18783',LIBSQL_AUTH_TOKEN:'test-auth',LIBSQL_SERVICE:service} as unknown as Env);
  assert.equal(await env.DB.prepare('SELECT 1 n').first('n'),1);
  assert.equal(requests.length,1);
  assert.equal(requests[0].headers.get('authorization'),'Bearer test-auth');
  assert.equal(new URL(requests[0].url).host,'127.0.0.1:18783');
  assert.ok(requests[0].signal);
});
