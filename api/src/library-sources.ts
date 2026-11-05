import {HttpError,usesLibsql,type Env,type ImageRow} from './types';
import {presentMany} from './store';
import {libraryState} from './library-state';

import {sourceIdentity} from './source-identity';
import {sourceProfiles} from './source-profiles';
type Group={key:string;label:string;url:string;count:number;image_count:number;video_count:number;time:number;cover_id:string;urls:string[]};
type RawGroup={source_url:string|null;count:number;videos:number;latest:string;latest_image:string|null};
const cache=new Map<string,{until:number;revision:number;value:Promise<{groups:Group[];summary:{sources:number;images:number;videos:number}}>}>();
export function invalidateSourceGroups(){cache.clear();}
const cacheKey=(env:Env,filters:string[],params:(string|number)[])=>JSON.stringify([usesLibsql(env)?env.LIBSQL_URL:env.PUBLIC_BASE_URL,filters,params]);
async function sourceSnapshot(env:Env,filters:string[]=[],params:(string|number)[]=[],fresh=false,revision?:number){
  revision??=(await libraryState(env)).revision;
  const key=cacheKey(env,filters,params);
  const hit=cache.get(key);if(!fresh&&hit&&(hit.revision===revision||hit.until>Date.now()))return hit.value;
  const value=(async()=>{
    // One compact, covering-index aggregation per minute, not a download of all
    // image metadata on every poll/page. Media bytes are never read here.
    const rows=await env.DB.prepare(`SELECT i.source_url,COUNT(*) count,
      SUM(CASE WHEN i.mime_type LIKE 'video/%' THEN 1 ELSE 0 END) videos,
      MAX(printf('%016d',i.created_at)||i.id) latest,
      MAX(CASE WHEN i.mime_type NOT LIKE 'video/%' THEN printf('%016d',i.created_at)||i.id END) latest_image FROM images i
      ${filters.length?'WHERE '+filters.join(' AND '):''}
      GROUP BY i.source_url`).bind(...params).all<RawGroup>();
    const groups=new Map<string,Group>(),profiles=await sourceProfiles(env,rows.results.flatMap(row=>row.source_url?.trim()?[row.source_url]:[])),coverTimes=new Map<string,string>();
    let total=0,videos=0;
    for(const row of rows.results){
      total+=Number(row.count);videos+=Number(row.videos);
      if(!row.source_url?.trim())continue;
      const raw=sourceIdentity(row.source_url),profile=profiles.get(raw.key),identity=profile?{key:profile.identity_key,label:profile.label}:raw,
        time=Number(row.latest.slice(0,16)),cover=(row.latest_image||row.latest).slice(16),coverTime=(row.latest_image?'1':'0')+(row.latest_image||row.latest);
      let group=groups.get(identity.key);
      if(!group){group={...identity,url:profile?.profile_url||raw.key,count:0,image_count:0,video_count:0,time,cover_id:cover,urls:[]};groups.set(group.key,group);}
      group.count+=Number(row.count);group.video_count+=Number(row.videos);group.image_count+=Number(row.count)-Number(row.videos);group.urls.push(row.source_url);
      group.time=Math.max(group.time,time);
      if(coverTime>(coverTimes.get(group.key)||'')){group.cover_id=cover;coverTimes.set(group.key,coverTime);}
    }
    return {groups:[...groups.values()],summary:{sources:groups.size,images:total-videos,videos}};
  })();
  const entry={until:Date.now()+60000,revision,value};cache.delete(key);cache.set(key,entry);
  while(cache.size>16)cache.delete(cache.keys().next().value!);
  value.catch(()=>{if(cache.get(key)===entry)cache.delete(key);});return value;
}
export async function librarySources(env:Env,filters:string[]=[],params:(string|number)[]=[],fresh=false,revision?:number){
  return (await sourceSnapshot(env,filters,params,fresh,revision)).groups;
}
async function librarySummary(env:Env,revision:number){
  return (await sourceSnapshot(env,[],[],false,revision)).summary;
}
export async function sourceUrls(env:Env,key:string,fresh=false){
  if(key.length>4096)throw new HttpError(400,'invalid_source','Nguồn không hợp lệ.');
  const revision=(await libraryState(env)).revision,groups=await librarySources(env,[],[],fresh,revision),snapshot=cache.get(cacheKey(env,[],[]))||{revision:-1,until:Date.now()};
  const profiles=await sourceProfiles(env,[key]),canonical=profiles.get(key)?.identity_key||key;
  return {urls:groups.find(group=>group.key===canonical||group.urls.some(url=>sourceIdentity(url).key===key))?.urls||[],refreshAt:snapshot.revision===revision?null:snapshot.until};
}
export async function sourceLibrary(env:Env,filters:string[],params:(string|number)[],limit:number,cursor:{time:number;id:string}|undefined,base:string,revision:number,fresh=false){
  const groups=await librarySources(env,filters,params,fresh,revision),snapshot=cache.get(cacheKey(env,filters,params))||{revision:-1,until:Date.now()};
  const singles=new Map(groups.filter(group=>group.count===1).map(group=>[group.cover_id,group]));
  const conditions=[...filters,"(i.source_url IS NULL OR length(trim(i.source_url))=0 OR i.id IN (SELECT value FROM json_each(?)))"],values=[...params,JSON.stringify([...singles.keys()])];
  if(cursor){conditions.push('(i.created_at<? OR (i.created_at=? AND i.id<?))');values.push(cursor.time,cursor.time,cursor.id);}
  const files=await env.DB.prepare(`SELECT i.* FROM images i WHERE ${conditions.join(' AND ')} ORDER BY i.created_at DESC,i.id DESC LIMIT ?`).bind(...values,limit+1).all<ImageRow>();
  const entries=[...groups.filter(group=>group.count>1).map(group=>({id:'source:'+group.key,time:group.time,group})),...files.results.map(row=>({id:row.id,time:row.created_at,row}))]
    .filter(entry=>!cursor||entry.time<cursor.time||entry.time===cursor.time&&entry.id<cursor.id)
    .sort((a,b)=>b.time-a.time||(a.id<b.id?1:a.id>b.id?-1:0));
  const page=entries.slice(0,limit),groupPage=page.flatMap(entry=>'group'in entry?[entry.group]:[]),ids=groupPage.map(group=>group.cover_id);
  const covers=ids.length?await env.DB.prepare('SELECT * FROM images WHERE id IN (SELECT value FROM json_each(?))').bind(JSON.stringify(ids)).all<ImageRow>():{results:[]};
  const images=await presentMany(env,[...page.flatMap(entry=>'row'in entry?[entry.row]:[]),...covers.results],base),byId=new Map(images.map(image=>[image.id,image]));
  const last=page.at(-1);
  return {library:await librarySummary(env,revision),images:page.flatMap(entry=>'row'in entry?[{...byId.get(entry.row.id)!,source_label:singles.get(entry.row.id)?.label||null,source_profile_url:singles.get(entry.row.id)?.url||null}]:[]),groups:groupPage.map(({urls,cover_id,time,...group})=>({...group,created_at:new Date(time).toISOString(),cover:byId.get(cover_id)||null})),
    next:entries.length>limit&&last?btoa(JSON.stringify({time:last.time,id:last.id})):null,revision,sources_refresh_at:snapshot.revision===revision?null:snapshot.until};
}
