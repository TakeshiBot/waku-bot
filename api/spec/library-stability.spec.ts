import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createClient} from '@libsql/client';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {LibsqlDatabase} from '../src/libsql.ts';
import {migrationStatements} from '../src/sql.ts';
import {save,update,cleanupObjects} from '../src/store.ts';
import {ensureStorage} from '../src/storage.ts';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const info={title:'Original title',tags:['anime'],rating:'safe',image_type:'anime',source_url:null} as const;
const payload=(suffix:string)=>Uint8Array.from(Buffer.concat([png,Buffer.from(suffix)])).buffer;

for(const backend of ['d1','libsql'])test(`${backend}: upload intents recover failed writes and never delete acknowledged-or-uncertain commits; stale edits cannot change tags`,async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES']}));
  const client=backend==='libsql'?createClient({url:'file::memory:'}):null;
  try{
    const db:any=client?new LibsqlDatabase(client):await mf.getD1Database('DB');
    if(client)await client.execute('PRAGMA foreign_keys=ON');
    for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    const bucket=await mf.getR2Bucket('IMAGES'),env:any={DB:db,IMAGES:bucket};
    await ensureStorage(env);
    const metadata={...info,tags:[...info.tags]};
    const fault={...env,DB:{prepare:db.prepare.bind(db),batch:async()=>{throw Error('simulated write failure');}}};
    await assert.rejects(save(fault,payload('failed'),metadata,null),/simulated write failure/);
    assert.equal((await bucket.list()).objects.length,1);
    assert.equal((await db.prepare('SELECT COUNT(*) n FROM object_uploads').first()).n,1);
    const kept=await save(env,payload('failed'),metadata,null);
    await db.prepare('UPDATE object_uploads SET expires_at=0').run();await cleanupObjects(env);
    assert.equal((await bucket.list()).objects.length,1);assert.ok(await bucket.get(kept.row.object_key));
    assert.equal((await db.prepare('SELECT COUNT(*) n FROM object_uploads').first()).n,0);

    const uncertain={...env,DB:{prepare:db.prepare.bind(db),batch:async(statements:any[])=>{await db.batch(statements);throw Error('lost commit acknowledgement');}}};
    await assert.rejects(save(uncertain,payload('committed'),metadata,null),/lost commit acknowledgement/);
    await cleanupObjects(env);
    const retry=await save(env,payload('committed'),metadata,null);assert.equal(retry.duplicate,true);assert.ok(await bucket.get(retry.row.object_key));
    assert.equal((await bucket.list()).objects.length,2);

    const v=kept.row.metadata_version;
    await update(env,kept.row.id,{...metadata,title:'Device A',tags:['device_a']},v);
    await assert.rejects(update(env,kept.row.id,{...metadata,rating:'r18',tags:['stale_device_b']},v),(e:any)=>e.status===409&&e.code==='metadata_conflict');
    const current=await db.prepare('SELECT title,rating FROM images WHERE id=?').bind(kept.row.id).first();assert.equal(current.title,'Device A');assert.equal(current.rating,'safe');
    assert.deepEqual((await db.prepare('SELECT tag FROM image_tags WHERE image_id=?').bind(kept.row.id).all()).results.map((row:any)=>row.tag),['device_a']);
  }finally{client?.close();await mf.dispose();}
});

test('changes API is bounded, tracks removed/filter-changed images and respects the expanded gallery window',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'qa-key',PUBLIC_BASE_URL:'https://sync.example.com'}}));
  try{
    const db=await mf.getD1Database('DB');for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    const call=async(path:string,method='GET',body?:unknown)=>{
      const res=await mf.dispatchFetch('https://sync.example.com'+path,{method,headers:{authorization:'Bearer qa-key',...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
      return {status:res.status,data:await res.json() as any};
    };
    const ids=Array.from({length:220},()=>crypto.randomUUID());
    for(const [n,id]of ids.entries())await db.batch([
      db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) VALUES (?,?,?,'a.png','image/png',100,?,'safe',.5,?,'anime')").bind(id,id,id,'Image '+n,n+1),
      db.prepare("INSERT INTO image_tags VALUES (?,'anime')").bind(id),db.prepare('INSERT INTO image_dimensions VALUES (?,1,1)').bind(id),
    ]);
    const initial=await call('/v1/images?limit=100');assert.equal(initial.status,200);assert.ok(Number.isSafeInteger(initial.data.revision));
    const original=(await call('/v1/images/'+ids[219])).data.image;
    const changed=await call('/v1/images/'+ids[219],'PATCH',{title:'Changed',metadata_version:original.metadata_version});assert.equal(changed.status,200);
    assert.equal((await call('/v1/images/'+ids[219],'PATCH',{tags:['stale'],metadata_version:original.metadata_version})).status,409);
    const delta=await call('/v1/images/changes?since='+initial.data.revision);assert.equal(delta.status,200);assert.deepEqual(delta.data.ids,[ids[219]]);assert.equal(delta.data.images.length,1);
    const next=delta.data.revision;
    await call('/v1/images/'+ids[219],'PATCH',{rating:'r18'});
    const filtered=(await call('/v1/images/changes?rating=safe&view=classified&since='+next)).data;
    assert.deepEqual(filtered.ids,[ids[219]]);assert.equal(filtered.images.length,0);
    await call('/v1/images/'+ids[219],'DELETE');
    const deleted=(await call('/v1/images/changes?since='+filtered.revision)).data;assert.deepEqual(deleted.ids,[ids[219]]);assert.equal(deleted.images.length,0);
    const boundary=btoa(JSON.stringify({time:200,id:ids[199]}));
    await call('/v1/images/'+ids[0],'PATCH',{title:'Old changed'});
    assert.equal((await call('/v1/images/changes?since='+deleted.revision+'&window='+encodeURIComponent(boundary))).data.images.length,0);

    const before=(await call('/v1/state')).data.revision;
    for(const id of ids.slice(0,105))await db.prepare('UPDATE images SET title=title||? WHERE id=?').bind(' updated',id).run();
    const first=(await call('/v1/images/changes?since='+before)).data;assert.equal(first.ids.length,100);assert.equal(first.more,true);
    const second=(await call('/v1/images/changes?since='+first.revision)).data;assert.equal(second.ids.length,5);assert.equal(second.more,false);
    assert.equal(new Set([...first.ids,...second.ids]).size,105);
    const plan=(await db.prepare('EXPLAIN QUERY PLAN SELECT image_id,revision FROM library_changes WHERE revision>? AND revision<=? ORDER BY revision LIMIT 101').bind(0,999999).all()).results;
    assert.ok(plan.some(row=>String(row.detail).includes('library_changes_revision')));
    assert.equal((await call('/v1/images/changes?since=999999999')).status,409);
  }finally{await mf.dispose();}
});
