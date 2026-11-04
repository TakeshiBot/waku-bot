import {boundedBody} from './input';
import {sourceIdentity} from './source-identity';
import {usesLibsql,type Env} from './types';

type Profile={source_key:string;identity_key:string;label:string;profile_url:string;next_check:number};
export async function sourceProfiles(env:Env,urls:string[]){
  const keys=[...new Set(urls.map(url=>sourceIdentity(url).key))];
  if(!keys.length)return new Map<string,Profile>();
  const rows=await env.DB.prepare(`SELECT * FROM source_profiles WHERE source_key IN (SELECT value FROM json_each(?))
    OR identity_key IN (SELECT identity_key FROM source_profiles WHERE source_key IN (SELECT value FROM json_each(?)))`)
    .bind(JSON.stringify(keys),JSON.stringify(keys)).all<Profile>();
  return new Map(rows.results.map(row=>[row.source_key,row]));
}
const decode=(value:string)=>value.replace(/&#(x[0-9a-f]+|\d+);/gi,(_,n)=>{const code=n[0].toLowerCase()==='x'?parseInt(n.slice(1),16):Number(n);return code>0&&code<=0x10ffff?String.fromCodePoint(code):'';}).replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>');
export function parseSourceProfile(key:string,text:string):{key:string;label:string;url:string;aliases:string[]}|null{
  const source=new URL(key),parts=source.pathname.split('/').filter(Boolean);
  if(source.hostname==='www.pixiv.net'){
    try{const data=JSON.parse(text),user=data.body;if(!data.error&&String(user?.userId)===parts[1]&&typeof user.name==='string'&&user.name.trim())return {key,label:user.name.trim().slice(0,120)+' · Pixiv',url:key,aliases:[key]};}catch{}
  }
  if(source.hostname==='x.com'){
    // Parse the profile query itself. Never pick a mentioned/related user or a
    // tweet's author from arbitrary HTML matches.
    for(const match of text.matchAll(/\bvalue:("(?:\\.|[^"\\])*")/g))try{
      const data=JSON.parse(JSON.parse(match[1])),user=data?.data?.user_result_by_screen_name?.result;
      const name=user?.core?.screen_name||user?.legacy?.screen_name,id=user?.rest_id;
      if(!/^[A-Za-z0-9_]{1,15}$/.test(name||'')||!/^\d+$/.test(id||''))continue;
      if(parts[0]==='i'?parts[2]!==id:parts[0]?.toLowerCase()!==name.toLowerCase())continue;
      const identity='https://x.com/i/user/'+id,url='https://x.com/'+name.toLowerCase();
      return {key:identity,label:'@'+name+' · X',url,aliases:[key,identity,url]};
    }catch{}
  }
  if(source.hostname==='www.facebook.com'){
    const match=text.match(/"userID":"(\d+)","userVanity":"([A-Za-z0-9.]*)"/);
    const id=match?.[1],name=match?.[2],requested=source.searchParams.get('id');
    if(!id||(requested?requested!==id:parts[0]?.toLowerCase()!==name?.toLowerCase()))return null;
    const title=decode(text.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1]||'').trim();
    const identity='https://www.facebook.com/profile.php?id='+id,url=name?'https://www.facebook.com/'+name.toLowerCase():identity;
    return {key:identity,label:(name?'@'+name:title&&!/log in|login|facebook/i.test(title)?title.slice(0,120):id)+' · Facebook',url,aliases:[key,identity,url]};
  }
  return null;
}
function supported(key:string){const url=new URL(key);return url.hostname==='x.com'||url.hostname==='www.facebook.com'||url.hostname==='www.pixiv.net'&&/^\/users\/\d+$/.test(url.pathname);}
async function lookup(key:string){
  const source=new URL(key),hosts=source.hostname==='x.com'?['x.com','www.x.com','twitter.com']:source.hostname==='www.facebook.com'?['www.facebook.com','web.facebook.com','m.facebook.com']:['www.pixiv.net'];
  let url=source.hostname==='www.pixiv.net'?'https://www.pixiv.net/ajax/user/'+source.pathname.split('/')[2]:key;
  const signal=AbortSignal.timeout(6000);
  for(let n=0;n<4;n++){
    const result=await fetch(url,{redirect:'manual',signal,headers:{'user-agent':'Mozilla/5.0','accept':'text/html,application/json'}});
    if(result.status>=300&&result.status<400){const next=new URL(result.headers.get('location')||'',url);await result.body?.cancel();if(next.protocol!=='https:'||!hosts.includes(next.hostname)||next.username||next.password||next.port)throw new Error('unsafe profile redirect');url=next.href;continue;}
    if(!result.ok){await result.body?.cancel();throw new Error('profile unavailable');}
    return parseSourceProfile(key,new TextDecoder().decode(await boundedBody(result,1500000)));
  }
  return null;
}
const running=new Set<string>();
export async function refreshSourceProfiles(env:Env,keys:string[]){
  const scope=(usesLibsql(env)?env.LIBSQL_URL:env.PUBLIC_BASE_URL)||'default';
  if(running.has(scope))return;running.add(scope);
  try{
    const profiles=await sourceProfiles(env,keys),now=Date.now();let changed=false;
    const pending=[...new Set(keys)].filter(key=>supported(key)&&!profiles.has(key)).map(key=>{const source=sourceIdentity(key);return {key,label:source.label};});
    if(pending.length)await env.DB.prepare(`INSERT OR IGNORE INTO source_profiles(source_key,identity_key,label,profile_url,next_check)
      SELECT json_extract(value,'$.key'),json_extract(value,'$.key'),json_extract(value,'$.label'),json_extract(value,'$.key'),0 FROM json_each(?)`).bind(JSON.stringify(pending)).run();
    const due=[...new Set(keys)].filter(key=>supported(key)&&(!profiles.has(key)||profiles.get(key)!.next_check<=now)).slice(0,2);
    for(const key of due){
      let found:null|Awaited<ReturnType<typeof lookup>>=null;try{found=await lookup(key);}catch{/* Public profiles can be unavailable; retain the last verified identity. */}
      const previous=profiles.get(key),identity=sourceIdentity(key),next=now+(found?7:1)*86400000;
      if(found){
        const aliases=[...new Set(found.aliases)];
        await env.DB.batch(aliases.map(alias=>env.DB.prepare(`INSERT INTO source_profiles VALUES(?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
          identity_key=excluded.identity_key,label=excluded.label,profile_url=excluded.profile_url,next_check=excluded.next_check`).bind(alias,found.key,found.label,found.url,next)));
        changed ||= previous?.identity_key!==found.key||previous?.label!==found.label;
      }else await env.DB.prepare(`INSERT INTO source_profiles VALUES(?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET next_check=excluded.next_check`)
        .bind(key,previous?.identity_key||key,previous?.label||identity.label,previous?.profile_url||key,next).run();
    }
    if(changed)await env.DB.prepare('UPDATE library_revision SET version=version+1 WHERE id=1').run();
    return changed;
  }finally{running.delete(scope);}
}

export async function refreshDueSourceProfiles(env:Env){
  const due=await env.DB.prepare('SELECT source_key FROM source_profiles WHERE next_check<=? ORDER BY next_check,source_key LIMIT 2').bind(Date.now()).all<{source_key:string}>();
  return refreshSourceProfiles(env,due.results.map(row=>row.source_key));
}
