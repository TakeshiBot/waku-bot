import {HttpError,type Env} from './types';
import {libraryChanged} from './store';

export const PROVIDER_REST_MS=30000,ROUND_REST_MS=300000;
const OPEN="('active','retry_wait','paused')";
interface Session {id:string;current_batch_id:string;created_at:number;phase:string;round:number;max_rounds:number;next_retry_at:number;enrolling_until:number;parent_id:string|null;stop_reason:string|null;presentation:'batch'|'inline';}
export async function openSession(env:Env){return env.DB.prepare(`SELECT * FROM analysis_sessions WHERE phase IN ${OPEN} LIMIT 1`).first<Session>();}
export async function seedSessionProviders(env:Env,id:string){
  await env.DB.prepare(`INSERT INTO analysis_session_providers(session_id,provider_id,config_version)
    SELECT ?,p.id,p.config_version FROM ai_providers p JOIN ai_models m ON m.id=p.preferred_model_id AND m.provider_id=p.id WHERE p.enabled=1
    ON CONFLICT(session_id,provider_id) DO UPDATE SET config_version=excluded.config_version,failures=0,strikes=0,cooldown_until=0,disabled=0,last_error=NULL
    WHERE analysis_session_providers.config_version!=excluded.config_version`).bind(id).run();
}
export async function ensureSession(env:Env,parentId:string|null=null,automatic=false,presentation:'batch'|'inline'='batch'){
  const existing=await openSession(env);if(existing){
    if(parentId&&existing.parent_id!==parentId)throw new HttpError(409,'analysis_still_running','Đang có phiên AI khác chạy. Chờ hoàn tất hoặc huỷ trước khi chạy lại.');
    if(presentation==='batch'&&existing.presentation==='inline'){
      await env.DB.prepare("UPDATE analysis_sessions SET presentation='batch' WHERE id=?").bind(existing.id).run();existing.presentation='batch';
    }
    return existing;
  }
  const id=crypto.randomUUID(),batch=crypto.randomUUID(),now=Date.now();
  await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO analysis_sessions(id,current_batch_id,created_at,phase,enrolling_until,parent_id,presentation)
      SELECT ?,?,?,CASE WHEN (SELECT paused FROM analysis_queue_settings WHERE id=1)=1 THEN 'paused' ELSE 'active' END,
      ?,?,?
      WHERE ?=0 OR EXISTS(SELECT 1 FROM analysis_sessions WHERE id=? AND phase='done' AND next_retry_at>0 AND next_retry_at<=?)`)
      .bind(id,batch,now,now+60000,parentId,presentation,automatic?1:0,parentId,now),
    env.DB.prepare(`INSERT INTO analysis_batches(id,created_at,control) SELECT current_batch_id,created_at,CASE WHEN phase='paused' THEN 'paused' ELSE 'active' END
      FROM analysis_sessions WHERE id=?`).bind(id),
    env.DB.prepare('INSERT INTO analysis_session_batches SELECT current_batch_id,id,1 FROM analysis_sessions WHERE id=?').bind(id),
    env.DB.prepare('UPDATE analysis_sessions SET next_retry_at=0 WHERE id=? AND EXISTS(SELECT 1 FROM analysis_sessions WHERE id=?)').bind(parentId,id),
  ]);
  const session=await openSession(env);if(!session)throw new HttpError(409,'analysis_session_changed','Phiên AI đã thay đổi.');
  if(parentId&&session.parent_id!==parentId)throw new HttpError(409,'analysis_still_running','Đang có phiên AI khác chạy.');
  await seedSessionProviders(env,session.id);return session;
}
export async function sessionForBatch(env:Env,batchId:string){
  return env.DB.prepare(`SELECT s.* FROM analysis_sessions s JOIN analysis_session_batches b ON b.session_id=s.id WHERE b.batch_id=?`).bind(batchId).first<Session>();
}
export async function attachSessionImages(env:Env,session:Session,ids:string[]){
  if(!ids.length)return;
  const encoded=JSON.stringify([...new Set(ids)]);
  for(let attempt=0;attempt<3;attempt++){
    // Completion, cancellation and round changes can race a new upload. Resolve
    // the current batch INSIDE the transaction, never attach to a finished round.
    const attached=await env.DB.batch([
      env.DB.prepare(`INSERT INTO analysis_job_sessions(image_id,session_id) SELECT a.image_id,s.id FROM image_analysis a CROSS JOIN analysis_sessions s
        WHERE a.image_id IN (SELECT value FROM json_each(?)) AND s.id=? AND s.phase IN ('active','retry_wait','paused','cancelled')
        ON CONFLICT(image_id) DO UPDATE SET session_id=excluded.session_id`).bind(encoded,session.id),
      env.DB.prepare(`UPDATE image_analysis SET control=CASE s.phase WHEN 'paused' THEN 'paused' WHEN 'cancelled' THEN 'cancelled' ELSE 'active' END,
        status=CASE WHEN s.phase='cancelled' THEN 'skipped' ELSE status END,
        last_error=CASE WHEN s.phase='cancelled' THEN 'cancelled' ELSE last_error END,
        lease_token=CASE WHEN s.phase='cancelled' THEN NULL ELSE lease_token END
        FROM analysis_sessions s WHERE s.id=? AND s.phase IN ('active','retry_wait','paused','cancelled')
        AND image_id IN (SELECT value FROM json_each(?)) AND status IN ('pending','running')`).bind(session.id,encoded),
      env.DB.prepare(`INSERT INTO analysis_batch_items(batch_id,image_id,status) SELECT s.current_batch_id,a.image_id,a.status FROM image_analysis a CROSS JOIN analysis_sessions s
        WHERE a.image_id IN (SELECT value FROM json_each(?)) AND s.id=? AND s.phase IN ('active','retry_wait','paused','cancelled')
        ON CONFLICT(batch_id,image_id) DO UPDATE SET status=excluded.status`).bind(encoded,session.id),
      env.DB.prepare(`UPDATE analysis_sessions SET enrolling_until=0,phase=CASE WHEN phase='retry_wait' THEN 'active' ELSE phase END,next_retry_at=0 WHERE id=? AND phase IN ${OPEN}`).bind(session.id),
      env.DB.prepare("SELECT id FROM analysis_sessions WHERE id=? AND phase IN ('active','retry_wait','paused','cancelled')").bind(session.id),
    ]);
    if(attached[4].results.length)return;
    session=await ensureSession(env,null,false,session.presentation);
  }
  throw new HttpError(409,'analysis_session_changed','Phiên AI vừa thay đổi. Hãy thử lại.');
}
export async function sharedSessionProgress(env:Env){
  const session=await openSession(env)||await env.DB.prepare("SELECT * FROM analysis_sessions WHERE phase='done' AND next_retry_at>0 ORDER BY next_retry_at,created_at LIMIT 1").first<Session>()||await env.DB.prepare('SELECT * FROM analysis_sessions ORDER BY created_at DESC,id DESC LIMIT 1').first<Session>();
  if(!session)return null;
  const [counts,providers]=await Promise.all([
    env.DB.prepare('SELECT total,pending,running,done,failed,skipped,cancelled,paused FROM analysis_batch_counts WHERE batch_id=?').bind(session.current_batch_id).first<Record<string,number>>(),
    env.DB.prepare(`SELECT h.provider_id,p.name,h.failures,h.strikes,h.cooldown_until,h.disabled,h.last_error,p.enabled,p.cooldown_until global_cooldown_until
      FROM analysis_session_providers h JOIN ai_providers p ON p.id=h.provider_id WHERE h.session_id=? ORDER BY p.rowid`).bind(session.id).all(),
  ]);
  return {...session,...counts,id:session.current_batch_id,session_id:session.id,control:session.phase==='paused'?'paused':session.phase==='cancelled'?'cancelled':'active',providers:providers.results};
}

const retryable=`a.status='failed' AND COALESCE(a.last_error,'') NOT IN ('image_missing')
  AND NOT EXISTS(SELECT 1 FROM image_catalog_state c WHERE c.image_id=a.image_id AND c.classified=1)`;
export async function retrySession(env:Env,batchId?:string,automatic=false){
  const source=batchId?await sessionForBatch(env,batchId):await env.DB.prepare('SELECT * FROM analysis_sessions ORDER BY created_at DESC,id DESC LIMIT 1').first<Session>();
  if(!source)return null;
  if(automatic&&(source.phase!=='done'||!source.next_retry_at||source.next_retry_at>Date.now()))return sharedSessionProgress(env);
  if(['active','retry_wait','paused'].includes(source.phase))throw new HttpError(409,'analysis_still_running','Chờ phiên hoàn tất hoặc bấm Huỷ trước khi chạy lại lỗi.');
  const ids=await env.DB.prepare(`SELECT a.image_id FROM image_analysis a JOIN analysis_batch_items b ON b.image_id=a.image_id
    JOIN analysis_job_sessions j ON j.image_id=a.image_id AND j.session_id=?
    WHERE b.batch_id=? AND (a.status='failed' OR a.control='cancelled') AND (?=0 OR COALESCE(a.last_error,'')!='image_missing')
    AND NOT EXISTS(SELECT 1 FROM image_catalog_state c WHERE c.image_id=a.image_id AND c.classified=1)`).bind(source.id,source.current_batch_id,automatic?1:0).all<{image_id:string}>();
  if(!ids.results.length){await env.DB.prepare('UPDATE analysis_sessions SET next_retry_at=0 WHERE id=?').bind(source.id).run();return sharedSessionProgress(env);}
  let session:Session;
  try{session=await ensureSession(env,source.id,automatic,source.presentation);}catch(error){if(automatic&&error instanceof HttpError&&error.status===409)return sharedSessionProgress(env);throw error;}
  const encoded=JSON.stringify(ids.results.map(row=>row.image_id));
  await env.DB.prepare(`UPDATE image_analysis SET status='pending',control='active',attempts=0,next_attempt=0,lease_until=0,lease_token=NULL,last_error=NULL
    WHERE image_id IN (SELECT value FROM json_each(?)) AND (status='failed' OR control='cancelled')`).bind(encoded).run();
  await attachSessionImages(env,session,ids.results.map(row=>row.image_id));await libraryChanged(env);return sharedSessionProgress(env);
}

export async function recordProviderOutcome(env:Env,imageId:string,token:string,providerId:string,version:number,code:string|null){
  const now=Date.now(),failed=Boolean(code);
  await env.DB.batch([
    env.DB.prepare(`UPDATE analysis_session_providers SET
      disabled=CASE WHEN ?=0 THEN 0 WHEN failures>=2 AND strikes>=2 THEN 1 ELSE disabled END,
      cooldown_until=CASE WHEN ?=0 OR (failures>=2 AND strikes>=2) THEN 0
        WHEN failures>=2 THEN ?+CASE WHEN strikes>=1 THEN 60000 ELSE 30000 END ELSE cooldown_until END,
      strikes=CASE WHEN ?=0 THEN 0 ELSE strikes+CASE WHEN failures>=2 THEN 1 ELSE 0 END END,
      failures=CASE WHEN ?=0 OR failures>=2 THEN 0 ELSE failures+1 END,last_error=?
      WHERE provider_id=? AND config_version=? AND (disabled=0 OR ?=0) AND (cooldown_until<=? OR ?=0)
      AND session_id=(SELECT session_id FROM analysis_job_sessions WHERE image_id=?)
      AND EXISTS(SELECT 1 FROM image_analysis WHERE image_id=? AND lease_token=?)
      AND NOT EXISTS(SELECT 1 FROM analysis_provider_events WHERE token=?)`)
      .bind(failed?1:0,failed?1:0,now,failed?1:0,failed?1:0,code,providerId,version,failed?1:0,now,failed?1:0,imageId,imageId,token,token),
    env.DB.prepare(`INSERT OR IGNORE INTO analysis_provider_events(token,session_id,provider_id,created_at)
      SELECT ?,j.session_id,?,? FROM analysis_job_sessions j JOIN image_analysis a ON a.image_id=j.image_id WHERE j.image_id=? AND a.lease_token=?`)
      .bind(token,providerId,now,imageId,token),
  ]);
}

export async function adoptUnownedAnalysis(env:Env){
  // Adopt only unfinished legacy work; historical errors never start themselves.
  const orphan=await env.DB.prepare(`SELECT a.image_id FROM image_analysis a INDEXED BY analysis_runnable
    WHERE a.control IN ('active','paused') AND a.status IN ('pending','running')
    AND NOT EXISTS(SELECT 1 FROM analysis_job_sessions j WHERE j.image_id=a.image_id) LIMIT 100`).all<{image_id:string}>();
  if(orphan.results.length)await attachSessionImages(env,await ensureSession(env),orphan.results.map(row=>row.image_id));
}
export async function advanceSessions(env:Env,adopt=true){
  if(adopt)await adoptUnownedAnalysis(env);
  const session=await openSession(env);
  if(!session){
    const due=await env.DB.prepare("SELECT current_batch_id FROM analysis_sessions WHERE phase='done' AND next_retry_at>0 AND next_retry_at<=? ORDER BY next_retry_at,created_at LIMIT 1").bind(Date.now()).first<{current_batch_id:string}>();
    if(due){await retrySession(env,due.current_batch_id,true);return;}
    const latest=await env.DB.prepare('SELECT * FROM analysis_sessions ORDER BY created_at DESC,id DESC LIMIT 1').first<Session>();
    if(latest?.phase==='stopped'&&['providers_exhausted','retry_limit'].includes(latest.stop_reason||'')){
      await env.DB.prepare(`UPDATE analysis_sessions SET phase='done',next_retry_at=CASE WHEN EXISTS(
        SELECT 1 FROM image_analysis a JOIN analysis_batch_items b ON b.image_id=a.image_id WHERE b.batch_id=current_batch_id AND ${retryable}) THEN ? ELSE 0 END
        WHERE id=? AND phase='stopped'`).bind(Date.now()+ROUND_REST_MS,latest.id).run();return;
    }
    return;
  }
  if(session.phase==='paused')return;
  await seedSessionProviders(env,session.id);
  const counts=await env.DB.prepare('SELECT * FROM analysis_batch_counts WHERE batch_id=?').bind(session.current_batch_id).first<Record<string,number>>();
  if(!counts)return;
  const available=await env.DB.prepare(`SELECT COUNT(*) n FROM analysis_session_providers h JOIN ai_providers p ON p.id=h.provider_id
    JOIN ai_models m ON m.id=p.preferred_model_id AND m.provider_id=p.id WHERE h.session_id=? AND h.disabled=0 AND p.enabled=1`).bind(session.id).first<{n:number}>();
  if(!available?.n&&!counts.running){
    await env.DB.batch([
      env.DB.prepare(`UPDATE analysis_batch_items SET status='failed' WHERE batch_id=? AND status='pending'`).bind(session.current_batch_id),
      env.DB.prepare(`UPDATE image_analysis SET status='failed',last_error='providers_exhausted',lease_token=NULL,lease_until=0 WHERE status='pending'
        AND image_id IN (SELECT image_id FROM analysis_job_sessions WHERE session_id=?)`).bind(session.id),
      env.DB.prepare(`UPDATE analysis_sessions SET phase='done',next_retry_at=CASE WHEN EXISTS(
        SELECT 1 FROM image_analysis a JOIN analysis_batch_items b ON b.image_id=a.image_id WHERE b.batch_id=current_batch_id AND ${retryable}) THEN ? ELSE 0 END,
        stop_reason='providers_exhausted' WHERE id=? AND phase IN ('active','retry_wait')`).bind(Date.now()+ROUND_REST_MS,session.id),
    ]);await libraryChanged(env);return;
  }
  if(counts.pending||counts.running||session.enrolling_until>Date.now())return;
  const eligible=await env.DB.prepare(`SELECT COUNT(*) n FROM image_analysis a JOIN analysis_batch_items b ON b.image_id=a.image_id WHERE b.batch_id=? AND ${retryable}`).bind(session.current_batch_id).first<{n:number}>();
  await env.DB.prepare(`UPDATE analysis_sessions SET phase='done',next_retry_at=?,stop_reason=NULL WHERE id=? AND current_batch_id=? AND phase IN ('active','retry_wait')
      AND NOT EXISTS(SELECT 1 FROM analysis_batch_items WHERE batch_id=current_batch_id AND status IN ('pending','running'))`)
      .bind(eligible?.n?Date.now()+ROUND_REST_MS:0,session.id,session.current_batch_id).run();
}
