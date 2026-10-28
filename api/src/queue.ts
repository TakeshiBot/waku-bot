import type {Env} from './types';

export type WorkKind='upload'|'ai';
export async function acquireSlot(env:Env,kind:WorkKind):Promise<string|null> {
  const now=Date.now(), token=crypto.randomUUID();
  const claimed=await env.DB.prepare(`UPDATE worker_slots SET token=?,lease_until=?
    WHERE kind=? AND slot=(SELECT slot FROM worker_slots WHERE kind=? AND lease_until<? ORDER BY slot LIMIT 1)
    RETURNING token`).bind(token,now+120000,kind,kind,now).first<{token:string}>();
  return claimed?.token || null;
}
export async function releaseSlot(env:Env,token:string) {
  await env.DB.prepare('UPDATE worker_slots SET token=NULL,lease_until=0 WHERE token=?').bind(token).run();
}

// A short continuation chain drains bursts promptly without keeping one Worker
// alive indefinitely. Cron restarts chains after interruptions and large bursts.
// Eight hops leave room below Cloudflare's 16-hop Worker invocation limit.
export async function continueWork(env:Env,kind:WorkKind,hop=0,force=false) {
  if (!env.PUBLIC_BASE_URL || !env.API_KEY || hop>=8) return;
  if (!force) {
    const table=kind==='ai'?'image_analysis':'telegram_jobs';
    const due=await env.DB.prepare(`SELECT 1 FROM ${table} WHERE
      (status='pending' AND next_attempt<=?) OR (status='running' AND lease_until<?) LIMIT 1`)
      .bind(Date.now(),Date.now()).first();
    if (!due) return;
  }
  try {
    const result=await fetch(`${env.PUBLIC_BASE_URL}/internal/jobs`,{
      method:'POST',redirect:'manual',headers:{authorization:`Bearer ${env.API_KEY}`,'content-type':'application/json'},
      body:JSON.stringify({kind,hop:hop+1}),signal:AbortSignal.timeout(5000),
    });
    await result.body?.cancel();
    if (!result.ok) console.warn('Queue continuation deferred to cron',{kind});
  } catch {console.warn('Queue continuation deferred to cron',{kind});}
}
