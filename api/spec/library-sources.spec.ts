import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {migrationStatements} from '../src/sql.ts';
import {sourceIdentity} from '../src/source-identity.ts';
import {parseSourceProfile} from '../src/source-profiles.ts';

test('source identities follow an account/page, never a whole website or an invented CDN author',()=>{
  const key='https://x.com/artist';
  for(const url of ['https://x.com/Artist/media?filter=photo','https://twitter.com/artist/status/123/photo/1','http://mobile.twitter.com/Artist/'])assert.equal(sourceIdentity(url).key,key);
  assert.notEqual(sourceIdentity('https://x.com/artist2').key,key);
  assert.equal(sourceIdentity('https://www.tiktok.com/@artist/video/123').key,'https://www.tiktok.com/@artist');
  assert.equal(sourceIdentity('https://www.pixiv.net/en/users/123/artworks').key,'https://www.pixiv.net/users/123');
  assert.notEqual(sourceIdentity('https://site.example/page?id=1').key,sourceIdentity('https://site.example/page?id=2').key);
  assert.equal(sourceIdentity('https://site.example/page/?utm_source=x#top').key,'https://site.example/page');
  assert.notEqual(sourceIdentity('https://pbs.twimg.com/media/one.jpg').key,sourceIdentity('https://pbs.twimg.com/media/two.jpg').key);
  for(const url of ['https://web.facebook.com/profile.php?id=123&sk=photos','https://m.facebook.com/people/Example/123/','https://www.facebook.com/123/photos'])assert.equal(sourceIdentity(url).key,'https://www.facebook.com/profile.php?id=123');
  assert.equal(sourceIdentity('https://twitter.com/i/user/123').key,'https://x.com/i/user/123');
});

test('profile resolution verifies the account; CDN URLs and unrelated identities are never guessed',()=>{
  const result={data:{user_result_by_screen_name:{result:{rest_id:'123',core:{screen_name:'Artist'}}}}};
  const html='value:'+JSON.stringify(JSON.stringify(result));
  assert.equal(parseSourceProfile('https://x.com/artist',html)?.key,'https://x.com/i/user/123');
  assert.equal(parseSourceProfile('https://x.com/i/user/123',html)?.label,'@Artist · X');
  assert.equal(parseSourceProfile('https://x.com/other',html),null);
  assert.equal(parseSourceProfile('https://x.com/i/user/456',html),null);
  assert.equal(parseSourceProfile('https://pbs.twimg.com/media/file',html),null);
  const facebook='<title>Account name</title>{"userID":"123","userVanity":"artist"}';
  assert.equal(parseSourceProfile('https://www.facebook.com/artist',facebook)?.key,'https://www.facebook.com/profile.php?id=123');
  assert.equal(parseSourceProfile('https://www.facebook.com/profile.php?id=123',facebook)?.label,'@artist · Facebook');
  assert.equal(parseSourceProfile('https://www.facebook.com/profile.php?id=456',facebook),null);
  assert.equal(parseSourceProfile('https://www.pixiv.net/users/123',JSON.stringify({error:false,body:{userId:'123',name:'Artist'}}))?.label,'Artist · Pixiv');
});

test('source overview paginates groups and loose files; source contents retain filters, changes and manual moves',async()=>{
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{API_KEY:'sources-qa',PUBLIC_BASE_URL:'https://sources.example.com'},outboundService:()=>new Response('',{status:404})}));
  try{
    const db=await mf.getD1Database('DB');for(const name of(await readdir('migrations')).filter(n=>n.endsWith('.sql')).sort())await db.batch(migrationStatements(await readFile('migrations/'+name,'utf8')).map(sql=>db.prepare(sql)));
    const insert=async(source:string|null,time:number,video=false,tag='cat')=>{
      const id=crypto.randomUUID();await db.prepare("INSERT INTO images(id,sha256,object_key,filename,mime_type,size,title,source_url,rating,random_key,created_at,image_type) VALUES (?,?,?,?,?,10,'Example',?,'safe',0.5,?,'anime')").bind(id,id,id,video?'clip.mp4':'photo.png',video?'video/mp4':'image/png',source,time).run();
      await db.prepare('INSERT INTO image_tags VALUES (?,?)').bind(id,tag).run();return id;
    };
    const artist=[];for(let n=0;n<35;n++)artist.push(await insert(n%2?'https://x.com/Artist/media?filter=photo':'https://twitter.com/artist/status/'+n,1000+n,n===34));
    const other=await insert('https://x.com/Other/status/1',1040),loose=await insert(null,1040),empty=await insert('',500);
    const call=async(path:string,options:any={})=>{const response=await mf.dispatchFetch('https://sources.example.com'+path,{...options,headers:{authorization:'Bearer sources-qa','content-type':'application/json',...options.headers}});assert.equal(response.status,200);return response.json() as Promise<any>;};
    const entries=[];let cursor=null;
    do{const page=await call('/v1/images?group_sources=1&limit=1'+(cursor?'&before='+encodeURIComponent(cursor):'&refresh_sources=1'));entries.push(...page.images.map((row:any)=>row.id),...page.groups.map((group:any)=>group.key));cursor=page.next;}while(cursor);
    assert.equal(entries.length,4);assert.equal(new Set(entries).size,4);assert.ok(entries.includes(loose)&&entries.includes(empty));
    const root=await call('/v1/images?group_sources=1&refresh_sources=1');const group=root.groups.find((row:any)=>row.key==='https://x.com/artist');
    assert.equal(group.count,35);assert.equal(group.image_count,34);assert.equal(group.video_count,1);assert.equal(group.cover.id,artist.at(-2));assert.equal(root.images.length,3);
    assert.deepEqual(root.library,{sources:2,images:37,videos:1});assert.equal(group.url,'https://x.com/artist');
    assert.equal(root.groups.length,1);assert.ok(root.images.some((row:any)=>row.id===other),'a source containing one file stays an ordinary file');
    assert.equal(root.images.find((row:any)=>row.id===other).source_label,'@other · X');assert.equal(root.images.find((row:any)=>row.id===loose).source_label,null);
    const second=await insert('https://x.com/Other/status/2',1050,true);
    const expanded=await call('/v1/images?group_sources=1&refresh_sources=1');assert.equal(expanded.groups.find((row:any)=>row.key==='https://x.com/other').count,2);
    await call('/v1/images/'+second,{method:'DELETE'});
    const collapsed=await call('/v1/images?group_sources=1&refresh_sources=1');assert.equal(collapsed.groups.length,1);assert.ok(collapsed.images.some((row:any)=>row.id===other),'a group collapses to a single file after removal');
    const source='&source='+encodeURIComponent(group.key);
    const clips=await call('/v1/images?media_types=video'+source);assert.deepEqual(clips.images.map((row:any)=>row.id),[artist.at(-1)]);assert.equal(clips.images[0].source_group,group.key);
    const selected=await call('/v1/images?selection=1&limit=1000'+source);assert.equal(selected.images.length,35);assert.ok(selected.images.every((row:any)=>row.source_group===group.key));assert.ok(!('tags' in selected.images[0]),'selection does not read full image metadata or previews');
    assert.equal((await call('/v1/images?selection=1&group_sources=1&limit=1000')).images.length,38,'select all includes groups and single files');
    const oneVideo=await call('/v1/images?group_sources=1&media_types=video&refresh_sources=1');assert.equal(oneVideo.groups.length,0);assert.equal(oneVideo.images[0].id,artist.at(-1));
    assert.equal((await call('/v1/images?rating=safe&view=classified&tags=missing'+source)).images.length,0);
    const all=await call('/v1/images?limit=100');assert.equal(all.images.length,38,'ordinary tabs/API retain individual files');
    const saved=await call('/v1/images/'+loose,{method:'PATCH',body:JSON.stringify({source_url:'https://x.com/artist/status/new'})});assert.equal(saved.image.source_group,group.key);
    const after=await call('/v1/images?group_sources=1&refresh_sources=1');assert.equal(after.groups.find((row:any)=>row.key===group.key).count,36);assert.deepEqual(after.images.map((row:any)=>row.id),[other,empty]);
    await call('/v1/images/'+other,{method:'DELETE'});
    const deleted=await call('/v1/images?group_sources=1&refresh_sources=1');assert.equal(deleted.groups.length,1,'empty groups disappear');
    const scoped=await call('/v1/images?limit=100&refresh_sources=1'+source);assert.equal(scoped.images.length,36);
    await call('/v1/images/bulk',{method:'PATCH',body:JSON.stringify({ids:[empty],source_url:'https://web.facebook.com/profile.php?id=123&sk=photos'})});
    let moved=await call('/v1/images/'+empty);assert.equal(moved.image.source_group,'https://www.facebook.com/profile.php?id=123');assert.equal(moved.image.rating,'safe');
    await call('/v1/images/bulk',{method:'PATCH',body:JSON.stringify({ids:[empty],source_url:null})});
    moved=await call('/v1/images/'+empty);assert.equal(moved.image.source_url,null);
    for(const key of ['https://x.com/artist','https://x.com/i/user/123'])await db.prepare('INSERT OR REPLACE INTO source_profiles VALUES(?,?,?,?,?)').bind(key,'https://x.com/i/user/123','@Artist · X','https://x.com/artist',Date.now()+86400000).run();
    await insert('https://x.com/i/user/123',2000);
    const merged=await call('/v1/images?group_sources=1&refresh_sources=1');assert.equal(merged.groups.length,1);assert.equal(merged.groups[0].count,37);assert.equal(merged.groups[0].key,'https://x.com/i/user/123');
    assert.equal(merged.groups[0].url,'https://x.com/artist');assert.deepEqual(merged.library,{sources:1,images:37,videos:1});
    await insert(null,4000,true);
    const totals=await call('/v1/images?group_sources=1&refresh_sources=1&limit=1');
    const filtered=await call('/v1/images?group_sources=1&media_types=video&refresh_sources=1');
    assert.deepEqual(totals.library,{sources:1,images:37,videos:2},'totals include unlinked files and ignore pagination');
    assert.deepEqual(filtered.library,totals.library,'filters do not change whole-library totals');
    const aliases=await call('/v1/images?limit=100&refresh_sources=1'+source);assert.equal(aliases.images.length,37);assert.ok(aliases.images.every((row:any)=>row.source_aliases.includes('https://x.com/artist')));
  }finally{await mf.dispose();}
});
