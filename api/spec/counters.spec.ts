import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createClient} from '@libsql/client';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {LibsqlDatabase} from '../src/libsql.ts';
import {migrationStatements} from '../src/sql.ts';
import {CLASSIFIED_IMAGE_SQL} from '../src/store.ts';

async function migrations(db:any){
  for(const name of(await readdir('migrations')).filter(x=>x.endsWith('.sql')).sort())
    await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
}
async function verify(db:any){
  const actual=await db.prepare('SELECT total,bytes,safe,r18,unknown,classified_safe,classified_r18,pending FROM library_counts WHERE id=1').first();
  const expected=await db.prepare(`WITH catalog AS (SELECT i.size,i.rating,${CLASSIFIED_IMAGE_SQL} classified FROM images i)
    SELECT COUNT(*) total,COALESCE(SUM(size),0) bytes,COALESCE(SUM(rating='safe'),0) safe,
    COALESCE(SUM(rating='r18'),0) r18,COALESCE(SUM(rating='unknown'),0) unknown,
    COALESCE(SUM(rating='safe' AND classified),0) classified_safe,
    COALESCE(SUM(rating='r18' AND classified),0) classified_r18,COALESCE(SUM(NOT classified),0) pending FROM catalog`).first();
  assert.deepEqual({...actual},{...expected});
  const tags=(await db.prepare('SELECT tag,n FROM tag_totals WHERE n>0 ORDER BY tag').all()).results;
  const rawTags=(await db.prepare('SELECT tag,COUNT(*) n FROM image_tags GROUP BY tag ORDER BY tag').all()).results;
  assert.deepEqual(tags.map((r:any)=>({...r})),rawTags.map((r:any)=>({...r})));
  const counts=(await db.prepare('SELECT * FROM analysis_batch_counts ORDER BY batch_id').all()).results;
  const raw=(await db.prepare(`SELECT g.id batch_id,COUNT(b.image_id) total,
    COALESCE(SUM(b.status='pending'),0) pending,COALESCE(SUM(b.status='running'),0) running,
    COALESCE(SUM(b.status='done'),0) done,COALESCE(SUM(b.status='failed'),0) failed,
    COALESCE(SUM(b.status='skipped' AND COALESCE(a.last_error,'')!='cancelled'),0) skipped,
    COALESCE(SUM(b.status='skipped' AND a.last_error='cancelled'),0) cancelled,
    COALESCE(SUM(b.status='pending' AND a.control='paused'),0) paused
    FROM analysis_batches g LEFT JOIN analysis_batch_items b ON b.batch_id=g.id
    LEFT JOIN image_analysis a ON a.image_id=b.image_id GROUP BY g.id ORDER BY g.id`).all()).results;
  assert.deepEqual(counts.map((r:any)=>({...r})),raw.map((r:any)=>({...r})));
}

for(const backend of ['d1','libsql']){
  test(backend+': counters survive AI, manual edits, cancel/retry, overlapping batches and cascades',async()=>{
    const mf=backend==='d1'?new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB']})):null;
    const client=backend==='libsql'?createClient({url:'file::memory:'}):null;
    const db:any=mf?await mf.getD1Database('DB'):new LibsqlDatabase(client!);
    try{
      await db.prepare('PRAGMA foreign_keys=ON').run();await migrations(db);
      const run=async(sql:string,...args:any[])=>{await db.prepare(sql).bind(...args).run();await verify(db);};
      for(let n=0;n<3;n++)await run("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) VALUES (?,?,?,'x.jpg','image/jpeg',10,'Title','safe',.5,0,'anime')",'i'+n,'h'+n,'o'+n);
      await run("INSERT INTO image_tags VALUES ('i0','anime'),('i0','cute'),('i1','anime'),('i2','   ')");
      await run("INSERT INTO analysis_batches(id,created_at) VALUES ('b1',0),('b2',0)");
      await run("INSERT INTO image_analysis(image_id,status) VALUES ('i0','pending'),('i1','done')");
      await run("INSERT INTO analysis_batch_items VALUES ('b1','i0','pending'),('b1','i1','done'),('b2','i0','pending')");
      await run("UPDATE image_analysis SET control='paused' WHERE image_id='i0'");
      await run("UPDATE image_analysis SET control='active',status='running' WHERE image_id='i0'");
      await run("UPDATE analysis_batch_items SET status='running' WHERE image_id='i0'");
      await run("UPDATE analysis_batch_items SET status='skipped' WHERE image_id='i0'");
      await run("UPDATE image_analysis SET status='skipped',control='cancelled',last_error='cancelled' WHERE image_id='i0'");
      await run("UPDATE analysis_batch_items SET status='pending' WHERE image_id='i0'");
      await run("UPDATE image_analysis SET status='pending',control='active',last_error=NULL WHERE image_id='i0'");
      await run("UPDATE image_analysis SET status='done' WHERE image_id='i0'");
      await run("UPDATE analysis_batch_items SET status='done' WHERE image_id='i0'");
      await run("UPDATE images SET title='   ' WHERE id='i0'");
      await run("UPDATE images SET title='Fixed',rating='r18',size=22 WHERE id='i0'");
      await run("DELETE FROM image_tags WHERE image_id='i0' AND tag='anime'");
      await run("DELETE FROM image_tags WHERE image_id='i0'");
      await run("UPDATE image_tags SET tag='cute' WHERE image_id='i1'");
      await run("UPDATE image_tags SET image_id='i0' WHERE image_id='i1'");
      await run("UPDATE image_analysis SET status='skipped',control='cancelled',last_error='cancelled' WHERE image_id='i0'");
      await run("UPDATE analysis_batch_items SET status='skipped' WHERE image_id='i0'");
      await run("DELETE FROM image_analysis WHERE image_id='i0'");
      await run("INSERT INTO image_analysis(image_id,status,control,last_error) VALUES ('i0','skipped','cancelled','cancelled')");
      await run("DELETE FROM images WHERE id='i0'");
      await run("DELETE FROM analysis_batches WHERE id='b1'");
      await db.batch(migrationStatements(await readFile('migrations/0012_incremental_counts.sql','utf8')).map(sql=>db.prepare(sql)));await verify(db);
    }finally{await mf?.dispose();client?.close();}
  });
}

test('D1 reads for library and batch counters stay constant as the library grows',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB']}));
  try{
    const db=await mf.getD1Database('DB');await migrations(db);
    const ids=Array.from({length:2000},(_,n)=>'i'+n);
    await db.batch([
      db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) SELECT value,value,value,'x.jpg','image/jpeg',10,'Title','safe',.5,0,'anime' FROM json_each(?)").bind(JSON.stringify(ids)),
      db.prepare("INSERT INTO image_tags SELECT id,'anime' FROM images"),
      db.prepare("INSERT INTO image_analysis(image_id,status) SELECT id,'done' FROM images"),
      db.prepare("INSERT INTO analysis_batches(id,created_at) VALUES ('large',0)"),
      db.prepare("INSERT INTO analysis_batch_items SELECT 'large',id,'done' FROM images"),
    ]);
    await verify(db);
    const state=await db.prepare('SELECT c.total,r.version FROM library_counts c CROSS JOIN library_revision r WHERE c.id=1 AND r.id=1').all();
    const batch=await db.prepare("SELECT total,done FROM analysis_batch_counts WHERE batch_id='large'").all();
    const tags=await db.prepare("SELECT tag,n FROM tag_totals WHERE n>0 ORDER BY tag").all();
    assert.equal(state.results[0].total,2000);assert.ok(state.meta.rows_read<=2);
    assert.equal(batch.results[0].done,2000);assert.ok(batch.meta.rows_read<=1);
    assert.equal(tags.results[0].n,2000);assert.ok(tags.meta.rows_read<=1);
    console.log('D1 counter rows read:',{library:state.meta.rows_read,batch:batch.meta.rows_read,tags:tags.meta.rows_read});
  }finally{await mf.dispose();}
});
