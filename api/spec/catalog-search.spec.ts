import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';

test('catalog name search matches title or compound tags while retaining all explicit filters and key scope',async()=>{
 const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'qa-key',PUBLIC_BASE_URL:'https://images.example.com'}}));
 try{
  const db=await mf.getD1Database('DB');for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
  const entries=[
   ['Portrait',['tokisaki_kurumi','cat_ears'],'safe','anime'],
   ['Kurumi: cosplay',['cosplay'],'safe','real'],
   ['Portrait',['tokisaki_kurumi','cat_ears'],'r18','anime'],
   ['Rem portrait',['rem','cat_ears'],'safe','anime'],
   ['Kurumiko portrait',['kurumiko','cat_ears'],'safe','anime'],
  ] as const;
  const ids:string[]=[];
  for(let n=0;n<entries.length;n++){
   const [title,tags,rating,type]=entries[n],id=crypto.randomUUID();ids.push(id);
   await db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,rating,random_key,created_at,image_type) VALUES (?,?,?,'x.jpg','image/jpeg',10,?,?,?,0,?)").bind(id,'hash'+n,'object'+n,title,rating,n/10,type).run();
   for(const tag of tags)await db.prepare('INSERT INTO image_tags VALUES (?,?)').bind(id,tag).run();
  }
  const call=async(path:string,key='qa-key',method='GET')=>mf.dispatchFetch('https://images.example.com'+path,{method,headers:{authorization:'Bearer '+key,...(method==='POST'?{'content-type':'application/json'}:{})},...(method==='POST'?{body:JSON.stringify({name:'catalog reader'})}:{})});
  const keyResponse=await call('/v1/keys','qa-key','POST'),catalogKey=(await keyResponse.json() as any).key;assert.ok(catalogKey);
  const search=async(query:string)=>{const response=await call('/v1/images/random?count=10&'+query,catalogKey);assert.equal(response.status,200);return(await response.json() as any).images.map((image:any)=>image.id).sort();};
  assert.deepEqual(await search('rating=mixed&q=Kurumi'),ids.slice(0,3).sort());
  assert.deepEqual(await search('rating=safe&q=Tokisaki_Kurumi'),[ids[0]]);
  assert.deepEqual(await search('rating=r18&q=kurumi&tags=cat_ears&image_type=anime'),[ids[2]]);
  assert.deepEqual(await search('rating=safe&q=kurumi&tags=cat_ears&image_type=real'),[]);
  assert.deepEqual(await search('rating=mixed&q=missing_character'),[]);
  assert.deepEqual(await search('rating=mixed'),[...ids].sort(),'unspecified requests still include the whole eligible catalog');
  assert.equal((await call('/v1/images',catalogKey)).status,403,'name search does not grant library/admin access');
  assert.equal((await call('/v1/images/random?q='+encodeURIComponent('a'.repeat(81)),catalogKey)).status,400);
 }finally{await mf.dispose();}
});
