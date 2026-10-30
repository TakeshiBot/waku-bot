import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';

async function setup(bindings:Record<string,string>){
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],
    bindings:{API_KEY:'admin-key',OWNER_IDS:'101',PUBLIC_BASE_URL:'https://images.example.com',...bindings},
    outboundService:()=>new MockResponse('unavailable',{status:530})}));
  const db=await mf.getD1Database('DB');
  for(const file of(await readdir('migrations')).filter(x=>x.endsWith('.sql')).sort())
    await db.batch(migrationStatements(await readFile('migrations/'+file,'utf8')).map(sql=>db.prepare(sql)));
  return mf;
}
test('explicit D1 selection ignores retained libSQL credentials and old quota markers',async()=>{
  const mf=await setup({DATABASE_BACKEND:'d1',LIBSQL_URL:'https://offline.example.com',LIBSQL_AUTH_TOKEN:'retained-token',D1_QUOTA_SCOPE:'new-database'});
  try{
    await(await mf.getR2Bucket('IMAGES')).put('_system/d1-daily-limit.json',JSON.stringify({until:Date.now()+60000}));
    const r=await mf.dispatchFetch('https://images.example.com/v1/session',{headers:{authorization:'Bearer admin-key'}});
    assert.equal(r.status,200);assert.equal((await r.json() as any).database,'d1');
  }finally{await mf.dispose();}
});
test('migration allows only authenticated read probes and blocks ordinary requests and writes',async()=>{
  const mf=await setup({DATABASE_BACKEND:'d1',MIGRATION_MAINTENANCE:'1'});
  try{
    const base='https://images.example.com/v1/session',headers={authorization:'Bearer admin-key','x-waku-maintenance-probe':'admin-key'};
    assert.equal((await mf.dispatchFetch(base,{headers:{authorization:'Bearer admin-key'}})).status,503);
    assert.equal((await mf.dispatchFetch(base,{headers})).status,200);
    assert.equal((await mf.dispatchFetch(base,{method:'POST',headers})).status,503);
  }finally{await mf.dispose();}
});
test('a libSQL outage never silently falls back to an older D1 snapshot',async()=>{
  const mf=await setup({DATABASE_BACKEND:'libsql',LIBSQL_URL:'https://offline.example.com',LIBSQL_AUTH_TOKEN:'token'});
  try{
    const r=await mf.dispatchFetch('https://images.example.com/v1/session',{headers:{authorization:'Bearer admin-key'}});
    assert.equal(r.status,503);assert.equal((await r.json() as any).error.code,'database_unavailable');
  }finally{await mf.dispose();}
});
