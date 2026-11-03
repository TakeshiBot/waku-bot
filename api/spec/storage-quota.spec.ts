import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createClient} from '@libsql/client';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {LibsqlDatabase} from '../src/libsql.ts';
import {migrationStatements} from '../src/sql.ts';
import {save,remove,cleanupObjects} from '../src/store.ts';
import {ensureStorage,storageUsage,STORAGE_LIMIT} from '../src/storage.ts';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const info={title:'Quota test',tags:['test'],rating:'safe',source_url:null} as const;
for(const backend of ['d1','libsql'])test(`${backend}: real R2 inventory, parallel uploads, full duplicates and confirmed deletion accounting`,async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES']}));
  const client=backend==='libsql'?createClient({url:'file::memory:'}):null;
  try{
    const db:any=client?new LibsqlDatabase(client):await mf.getD1Database('DB');
    for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    const bucket=await mf.getR2Bucket('IMAGES'),env:any={DB:db,IMAGES:bucket};
    await bucket.put('orphan',new Uint8Array(123));
    await ensureStorage(env);assert.equal((await storageUsage(env)).bytes,123);
    // Simulate a nearly full bucket without storing 10 GB in the test runner.
    const size=png.length+1;
    await db.prepare('INSERT INTO storage_objects VALUES (?,?)').bind('existing-data',STORAGE_LIMIT-123-size).run();
    const bodies=['a','b'].map(s=>Uint8Array.from(Buffer.concat([png,Buffer.from(s)])).buffer);
    const results=await Promise.allSettled(bodies.map(bytes=>save(env,bytes,{...info,tags:[...info.tags]},null)));
    const index=results.findIndex(result=>result.status==='fulfilled'),winner=results[index] as PromiseFulfilledResult<any>;
    assert.equal(results.filter(result=>result.status==='fulfilled').length,1);
    const rejected=results.find(result=>result.status==='rejected') as PromiseRejectedResult;
    assert.equal(rejected.reason.status,507);assert.equal(rejected.reason.code,'storage_full');
    assert.equal((await storageUsage(env)).bytes,STORAGE_LIMIT);assert.equal((await storageUsage(env)).full,true);
    assert.equal((await bucket.list()).objects.length,2);
    assert.equal((await save(env,bodies[index],{...info,tags:[...info.tags]},null)).duplicate,true);
    await assert.rejects(db.prepare("INSERT INTO storage_objects VALUES ('bypass',1)").run(),/storage_full/);

    // A failed physical delete must not make capacity available. Cleanup later
    // returns it once, even when the delete operation itself is retried.
    await remove({...env,IMAGES:{delete:async()=>{throw Error('R2 unavailable');}}},winner.value.row.id);
    assert.equal((await storageUsage(env)).bytes,STORAGE_LIMIT);
    await cleanupObjects(env);await cleanupObjects(env);
    assert.equal((await storageUsage(env)).bytes,STORAGE_LIMIT-size);
    assert.equal((await bucket.list()).objects.length,1);
    await save(env,bodies[1-index],{...info,tags:[...info.tags]},null);
    assert.equal((await storageUsage(env)).bytes,STORAGE_LIMIT);
  }finally{client?.close();await mf.dispose();}
});

test('a failed R2 inventory blocks writes instead of assuming an empty bucket',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES']}));
  try{
    const db=await mf.getD1Database('DB');
    for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    let writes=0;
    const env:any={DB:db,IMAGES:{list:async()=>{throw Error('inventory unavailable');},put:async()=>{writes++;}}};
    await assert.rejects(save(env,Uint8Array.from(png).buffer,{...info,tags:[...info.tags]},null),/inventory unavailable/);
    assert.equal(writes,0);assert.equal((await storageUsage(env)).ready,false);
  }finally{await mf.dispose();}
});
