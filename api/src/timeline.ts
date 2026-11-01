import {usesLibsql,type Env} from './types';

type Day={day:string;count:number};
// Timeline contains dates/counts only. Coalesce concurrent reads and reuse them
// for one minute, including during uploads; do not scan the catalog on each poll.
const cache=new Map<string,{until:number;value:Promise<Day[]>}>();
export async function imageTimeline(env:Env,filters:string[],params:(string|number)[]){
  const key=JSON.stringify([usesLibsql(env)?env.LIBSQL_URL:env.PUBLIC_BASE_URL,filters,params]);
  const hit=cache.get(key);if(hit&&hit.until>Date.now())return hit.value;
  const entry={until:Date.now()+60000,value:env.DB.prepare(`SELECT
    strftime('%Y-%m-%d',i.created_at/1000.0,'unixepoch','+7 hours') day,COUNT(*) count
    FROM images i ${filters.length?'WHERE '+filters.join(' AND '):''}
    GROUP BY day ORDER BY day DESC`).bind(...params).all<Day>().then(data=>data.results)};
  cache.delete(key);cache.set(key,entry);
  while(cache.size>16)cache.delete(cache.keys().next().value!);
  entry.value.catch(()=>{if(cache.get(key)===entry)cache.delete(key);});
  return entry.value;
}
