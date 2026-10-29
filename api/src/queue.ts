import type {Env} from './types';
import {libraryChanged} from './store';

export const NEXT_ANALYSIS_SQL=`SELECT image_id FROM image_analysis INDEXED BY analysis_ready
  WHERE control='active' AND attempts=0 AND status='pending' AND next_attempt<=?
  ORDER BY next_attempt,image_id LIMIT 1`;

// Lost invocations fail instead of starting another automatic inference.
export async function recoverAnalysis(env:Env) {
  const now=Date.now();
  if(!await env.DB.prepare(`SELECT 1 WHERE EXISTS(SELECT 1 FROM image_analysis INDEXED BY analysis_expired WHERE status='running' AND lease_until<?)
    OR EXISTS(SELECT 1 FROM image_analysis INDEXED BY analysis_started_pending WHERE status='pending' AND attempts>=1)
    OR EXISTS(SELECT 1 FROM ai_provider_slots WHERE token IS NOT NULL AND lease_until<?)`).bind(now,now).first())return;
  const results=await env.DB.batch([
    env.DB.prepare(`UPDATE analysis_batch_items SET status='failed' WHERE status IN ('pending','running')
      AND image_id IN (SELECT image_id FROM image_analysis WHERE (status='running' AND lease_until<?) OR (status='pending' AND attempts>=1))`).bind(now),
    env.DB.prepare(`UPDATE image_analysis SET status='failed',last_error='worker_timeout',lease_token=NULL,lease_until=0
      WHERE (status='running' AND lease_until<?) OR (status='pending' AND attempts>=1)`).bind(now),
    env.DB.prepare('UPDATE ai_provider_slots SET token=NULL,image_id=NULL,lease_until=0 WHERE token IS NOT NULL AND lease_until<?').bind(now),
  ]);
  if(results.some(result=>result.meta.changes))await libraryChanged(env);
}

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

export async function releaseAiSlot(env:Env,token:string) {
  await env.DB.prepare('UPDATE ai_provider_slots SET token=NULL,image_id=NULL,lease_until=0 WHERE token=?').bind(token).run();
}

async function reserveAnalysis(env:Env) {
  const now=Date.now(),token=crypto.randomUUID().replace(/-/g,'');
  // Reserving a provider slot and its image is one D1 transaction. Competing
  // schedulers cannot assign the same image to two APIs or exceed API limits.
  const results=await env.DB.batch([
    env.DB.prepare(`UPDATE ai_provider_slots SET token=?,lease_until=?,last_started=?,image_id=(
      ${NEXT_ANALYSIS_SQL})
      WHERE rowid=(SELECT s.rowid FROM ai_provider_slots s JOIN ai_providers p ON p.id=s.provider_id
      JOIN ai_models m ON m.id=p.preferred_model_id AND m.provider_id=p.id
      WHERE p.enabled=1 AND s.lease_until<? AND p.cooldown_until<=? AND (SELECT paused FROM analysis_queue_settings WHERE id=1)=0
      ORDER BY s.last_started,s.slot,s.provider_id LIMIT 1)
      AND EXISTS(SELECT 1 FROM image_analysis INDEXED BY analysis_ready WHERE control='active' AND attempts=0 AND status='pending' AND next_attempt<=?)
      RETURNING token`).bind(token,now+20000,now,now,now,now,now),
    env.DB.prepare(`UPDATE image_analysis SET status='running',attempts=attempts+1,lease_until=?,lease_token=?,
      provider_id=(SELECT provider_id FROM ai_provider_slots WHERE token=?)
      WHERE image_id=(SELECT image_id FROM ai_provider_slots WHERE token=?)
      AND control='active' AND attempts=0 AND status='pending' AND next_attempt<=? RETURNING image_id`)
      .bind(now+20000,token,token,token,now),
    env.DB.prepare(`UPDATE analysis_batch_items SET status='running' WHERE status='pending'
      AND image_id=(SELECT image_id FROM ai_provider_slots WHERE token=?)`).bind(token),
  ]);
  if(results[1].results.length)return token;
  if(results[0].results.length)await releaseAiSlot(env,token);
  return null;
}

async function invoke(env:Env,body:Record<string,unknown>) {
  try{
    const result=await fetch(`${env.PUBLIC_BASE_URL}/internal/jobs`,{method:'POST',redirect:'manual',
      headers:{authorization:`Bearer ${env.API_KEY}`,'content-type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(5000)});
    await result.body?.cancel();if(!result.ok)console.warn('Queue continuation deferred to cron',{kind:body.kind});
  }catch{console.warn('Queue continuation deferred to cron',{kind:body.kind});}
  // An uncertain acknowledgement must not release the reservation: the
  // recipient may already be using the API. Expired leases recover via cron.
}

export async function dispatchAnalysis(env:Env,hop=0,limit=8):Promise<number> {
  if(!env.PUBLIC_BASE_URL||!env.API_KEY||hop>=8)return 0;
  await recoverAnalysis(env);
  const due=await env.DB.prepare(`SELECT 1 FROM image_analysis INDEXED BY analysis_ready WHERE
    control='active' AND attempts=0 AND status='pending' AND next_attempt<=?
    AND (SELECT paused FROM analysis_queue_settings WHERE id=1)=0 LIMIT 1`).bind(Date.now()).first();
  if(!due)return 0;
  const tokens:string[]=[];
  for(let i=0;i<limit;i++){
    try{const token=await reserveAnalysis(env);if(!token)break;tokens.push(token);}
    catch{console.warn('AI dispatch deferred to cron');break;}
  }
  await Promise.all(tokens.map(token=>invoke(env,{kind:'ai',hop:hop+1,token})));
  // Dedicated fan-out invocations keep a large API pool below the per-request
  // subrequest/query limits. Normal completion only refills available slots.
  if(tokens.length===limit)await invoke(env,{kind:'ai',hop:hop+1});
  return tokens.length;
}

// A short continuation chain drains bursts promptly without keeping one Worker
// alive indefinitely. Cron restarts chains after interruptions and large bursts.
// Eight hops leave room below Cloudflare's 16-hop Worker invocation limit.
export async function continueWork(env:Env,kind:WorkKind,hop=0,force=false) {
  if(kind==='ai'){await dispatchAnalysis(env,hop,4);return;}
  if (!env.PUBLIC_BASE_URL || !env.API_KEY || hop>=8) return;
  if (!force) {
    const table='telegram_jobs';
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
