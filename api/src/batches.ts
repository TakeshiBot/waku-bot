import {HttpError,type Env} from './types';
import {libraryChanged} from './store';
import {ensureSession,attachSessionImages,sessionForBatch,sharedSessionProgress,retrySession,openSession} from './sessions';

export async function getBatch(env:Env,id:string) {
  const batch=await env.DB.prepare('SELECT id,control FROM analysis_batches WHERE id=?').bind(id).first<{id:string;control:string}>();
  if (!batch) throw new HttpError(404,'batch_not_found','Không tìm thấy đợt xử lý AI.');
  return batch;
}
export async function batchProgress(env:Env,id:string) {
  const batch=await getBatch(env,id);
  const counts=await env.DB.prepare(`SELECT total,pending,running,done,failed,skipped,cancelled,paused
    FROM analysis_batch_counts WHERE batch_id=?`)
    .bind(id).first<{total:number;pending:number;running:number;done:number;failed:number;skipped:number;cancelled:number;paused:number}>();
  return {id,control:batch.control,...counts!};
}
export async function createBatch(env:Env,inline=false) {
  return (await ensureSession(env,null,false,inline?'inline':'batch')).current_batch_id;
}
export async function attachBatch(env:Env,batchId:string,ids:string[],force=false,inline=false) {
  await getBatch(env,batchId);
  if (!ids.length) return 0;
  const rows=await env.DB.prepare("SELECT id FROM images WHERE id IN (SELECT value FROM json_each(?)) AND mime_type NOT LIKE 'video/%'").bind(JSON.stringify([...new Set(ids)])).all<{id:string}>();
  ids=rows.results.map(row=>row.id);if(!ids.length)return 0;
  const linked=await sessionForBatch(env,batchId),session=linked?.phase==='cancelled'?linked:await ensureSession(env,null,false,inline?'inline':'batch');
  batchId=session.current_batch_id;
  const encoded=JSON.stringify(ids);
  const updates:D1PreparedStatement[]=[];
  if (force) updates.push(env.DB.prepare(`INSERT INTO image_analysis(image_id,status,control,last_error)
    SELECT i.id,CASE WHEN b.control='cancelled' THEN 'skipped' ELSE 'pending' END,b.control,
      CASE WHEN b.control='cancelled' THEN 'cancelled' ELSE NULL END FROM images i CROSS JOIN analysis_batches b
    WHERE b.id=? AND i.id IN (SELECT value FROM json_each(?))
    ON CONFLICT(image_id) DO UPDATE SET status=excluded.status,control=excluded.control,attempts=0,lease_until=0,lease_token=NULL,next_attempt=0,last_error=excluded.last_error
    WHERE image_analysis.status!='running'`).bind(batchId,encoded));
  updates.push(env.DB.prepare(`UPDATE image_analysis SET control=b.control,
    status=CASE WHEN b.control='cancelled' THEN 'skipped' ELSE status END,
    last_error=CASE WHEN b.control='cancelled' THEN 'cancelled' ELSE last_error END,
    lease_token=CASE WHEN b.control='cancelled' THEN NULL ELSE lease_token END
    FROM analysis_batches b WHERE b.id=? AND b.control!='active'
    AND image_id IN (SELECT value FROM json_each(?)) AND status IN ('pending','running')`).bind(batchId,encoded));
  if(updates.length)await env.DB.batch(updates);
  await attachSessionImages(env,session,ids);
  await libraryChanged(env);return rows.results.length;
}

export async function queueProgress(env:Env) {
  const counts=await env.DB.prepare(`SELECT COALESCE(SUM(n),0) total,
    COALESCE(SUM(CASE WHEN status='pending' THEN n ELSE 0 END),0) pending,
    COALESCE(SUM(CASE WHEN status='running' THEN n ELSE 0 END),0) running,
    COALESCE(SUM(CASE WHEN status='done' THEN n ELSE 0 END),0) done,
    COALESCE(SUM(CASE WHEN status='failed' THEN n ELSE 0 END),0) failed,
    COALESCE(SUM(CASE WHEN status='skipped' AND cancelled=0 THEN n ELSE 0 END),0) skipped,
    COALESCE(SUM(CASE WHEN control='cancelled' THEN n ELSE 0 END),0) cancelled,
    COALESCE(SUM(CASE WHEN status='pending' AND control='paused' THEN n ELSE 0 END),0) paused,
    (SELECT MIN(next_attempt) FROM image_analysis WHERE status='pending' AND control='active') next_attempt
    FROM analysis_status_counts`)
    .first<{total:number;pending:number;running:number;done:number;failed:number;skipped:number;cancelled:number;paused:number;next_attempt:number|null}>();
  const settings=await env.DB.prepare('SELECT paused FROM analysis_queue_settings WHERE id=1').first<{paused:number}>();
  const provider=await env.DB.prepare(`SELECT MIN(p.cooldown_until) ready_at FROM ai_providers p JOIN ai_models m
    ON m.id=p.preferred_model_id AND m.provider_id=p.id WHERE p.enabled=1`).first<{ready_at:number|null}>();
  return {...counts!,control:settings?.paused?'paused':'active',provider_ready_at:provider?.ready_at??null};
}

// Only unfinished images are paused/cancelled. Finished metadata is retained.
// Invalidating the lease on cancel/retry prevents late results from being saved.
export async function controlAnalysis(env:Env,action:string,batchId?:string) {
  if(!['pause','resume','cancel','retry'].includes(action))throw new HttpError(400,'invalid_action','Thao tác AI không hợp lệ.');
  if(action==='retry')return retrySession(env,batchId);
  const session=batchId?await sessionForBatch(env,batchId):await openSession(env)||await env.DB.prepare("SELECT * FROM analysis_sessions WHERE phase='done' AND next_retry_at>0 ORDER BY created_at DESC,id DESC LIMIT 1").first<Awaited<ReturnType<typeof openSession>>>();
  if(!session){
    if(!batchId)await env.DB.prepare('UPDATE analysis_queue_settings SET paused=? WHERE id=1').bind(action==='pause'?1:0).run();
    return sharedSessionProgress(env);
  }
  if(!['active','retry_wait','paused'].includes(session.phase)&&!(session.phase==='done'&&session.next_retry_at>0))return sharedSessionProgress(env);
  const control=action==='pause'?'paused':action==='cancel'?'cancelled':'active';
  const phase=action==='pause'?'paused':action==='cancel'?'cancelled':session.next_retry_at?'done':'active';
  const updates=[
    env.DB.prepare('UPDATE analysis_queue_settings SET paused=? WHERE id=1').bind(action==='pause'?1:0),
    env.DB.prepare('UPDATE analysis_batches SET control=? WHERE id=?').bind(control,session.current_batch_id),
    env.DB.prepare("UPDATE analysis_sessions SET phase=?,next_retry_at=CASE WHEN ?='cancelled' THEN 0 ELSE next_retry_at END WHERE id=?").bind(phase,phase,session.id),
  ];
  if(action==='cancel'){
    updates.push(env.DB.prepare("UPDATE analysis_batch_items SET status='skipped' WHERE batch_id=? AND status IN ('pending','running')").bind(session.current_batch_id));
    updates.push(env.DB.prepare(`UPDATE image_analysis SET status='skipped',control='cancelled',last_error='cancelled',lease_token=NULL,lease_until=0
      WHERE status IN ('pending','running') AND image_id IN (SELECT image_id FROM analysis_job_sessions WHERE session_id=?)`).bind(session.id));
  }else updates.push(env.DB.prepare(`UPDATE image_analysis SET control=? WHERE status IN ('pending','running')
    AND image_id IN (SELECT image_id FROM analysis_job_sessions WHERE session_id=?)`).bind(control,session.id));
  await env.DB.batch(updates);await libraryChanged(env);return sharedSessionProgress(env);
}

export async function stopPendingAnalysis(env:Env,id:string) {
  await env.DB.batch([
    env.DB.prepare("UPDATE image_analysis SET status='skipped',last_error='manual_edit' WHERE image_id=? AND status='pending'").bind(id),
    env.DB.prepare(`UPDATE analysis_batch_items SET status='skipped' WHERE image_id=? AND status IN ('pending','running')
      AND EXISTS (SELECT 1 FROM image_analysis WHERE image_id=? AND status='skipped')`).bind(id,id),
  ]);
  await libraryChanged(env);
}
