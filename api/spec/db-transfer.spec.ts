import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createClient} from '@libsql/client';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';
import {exportDatabase,verifyDatabase} from '../scripts/db-transfer.mjs';

test('libSQL snapshot restores into D1 with exact metadata, keys, counters and active triggers',async()=>{
  const source=createClient({url:'file::memory:',intMode:'bigint'});
  const dir=await mkdtemp(path.join(tmpdir(),'waku-transfer-'));
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB']}));
  try{
    await source.execute('PRAGMA foreign_keys=ON');
    for(const file of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())
      await source.batch(migrationStatements(await readFile('migrations/'+file,'utf8')).map(sql=>({sql})));
    const title="A quote ' and ; -- newline\n Tiếng Việt\0end";
    await source.execute({sql:"INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) VALUES ('img','hash','object','photo.jpg','image/jpeg',42,?,'safe',0.25,123,'anime')",args:[title]});
    await source.execute("INSERT INTO image_tags VALUES ('img','anime')");
    await source.execute("INSERT INTO image_analysis(image_id,status) VALUES ('img','done')");
    await source.execute("INSERT INTO catalog_keys(id,name,key_hash,prefix,created_at) VALUES ('key','Test','hash','wku_',123)");
    const statements:string[]=[];
    const {manifest}=await exportDatabase(source,dir,{onStatement:(sql:string)=>statements.push(sql)});
    const db=await mf.getD1Database('DB');
    await db.batch(statements.map(sql=>db.prepare(sql)));
    const execute=async(stmt:any)=>{const r=await db.prepare(typeof stmt==='string'?stmt:stmt.sql).bind(...(typeof stmt==='string'?[]:stmt.args||[])).all();return {rows:r.results};};
    const checked=await verifyDatabase(execute,manifest);assert.equal(checked.images,1);
    assert.equal((await db.prepare("SELECT title FROM images WHERE id='img'").first())?.title,title);
    await db.prepare("UPDATE images SET rating='r18' WHERE id='img'").run();
    assert.equal((await db.prepare('SELECT classified_r18 FROM library_counts WHERE id=1').first())?.classified_r18,1);
    await assert.rejects(verifyDatabase(execute,manifest),/verification failed/);
    assert.equal((await source.execute("SELECT rating FROM images WHERE id='img'")).rows[0].rating,'safe');
  }finally{source.close();await mf.dispose();await rm(dir,{recursive:true,force:true});}
});
