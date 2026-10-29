import {CLASSIFIED_IMAGE_SQL} from './store';
import type {Env} from './types';

interface Counts{total:number;bytes:number;safe:number;r18:number;unknown:number;classified_safe:number;classified_r18:number;pending:number;}
export async function libraryState(env:Env){
  const row=await env.DB.prepare('SELECT version FROM library_revision WHERE id=1').first<{version:number}>(),revision=row?.version||0;
  const fingerprint=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(env.API_KEY)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const key=new Request(new URL('/__private_state_cache/v1/'+fingerprint,env.PUBLIC_BASE_URL||'https://waku.invalid').href);
  const cache=typeof caches==='undefined'?null:caches.default;
  try{
    const hit=await cache?.match(key),data=hit?await hit.json<{revision:number;counts:Counts}>():null;
    if(data?.revision===revision)return {...data,cacheHit:true};
  }catch{}
  const counts=await env.DB.prepare(`WITH catalog AS (SELECT i.rating,i.size,${CLASSIFIED_IMAGE_SQL} classified FROM images i)
    SELECT COUNT(*) total,COALESCE(SUM(size),0) bytes,
    COALESCE(SUM(rating='safe'),0) safe,COALESCE(SUM(rating='r18'),0) r18,COALESCE(SUM(rating='unknown'),0) unknown,
    COALESCE(SUM(rating='safe' AND classified),0) classified_safe,
    COALESCE(SUM(rating='r18' AND classified),0) classified_r18,
    COALESCE(SUM(NOT classified),0) pending FROM catalog`).first<Counts>();
  const data={revision,counts:counts!};
  try{await cache?.put(key,Response.json(data,{headers:{'cache-control':'public, max-age=3600'}}));}catch{}
  return {...data,cacheHit:false};
}
