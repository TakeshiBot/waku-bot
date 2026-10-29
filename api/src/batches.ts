import {HttpError,type Env} from './types';
import {libraryChanged} from './store';

export async function getBatch(env:Env,id:string) {
  const batch=await env.DB.prepare('SELECT id,control FROM analysis_batches WHERE id=?').bind(id).first<{id:string;control:string}>();
  if (!batch) throw new HttpError(404,'batch_not_found','Không tìm thấy đợt xử lý AI.');
  return batch;
}
export async function batchProgress(env:Env,id:string) {
  const batch=await getBatch(env,id);
  const counts=await env.DB.prepare(`SELECT COUNT(*) total,
    COALESCE(SUM(b.status='pending'),0) pending,COALESCE(SUM(b.status='running'),0) running,
    COALESCE(SUM(b.status='done'),0) done,COALESCE(SUM(b.status='failed'),0) failed,
    COALESCE(SUM(b.status='skipped' AND COALESCE(a.last_error,'')!='cancelled'),0) skipped,
    COALESCE(SUM(b.status='skipped' AND a.last_error='cancelled'),0) cancelled,
    COALESCE(SUM(b.status='pending' AND a.control='paused'),0) paused
    FROM analysis_batch_items b LEFT JOIN image_analysis a ON a.image_id=b.image_id WHERE batch_id=?`)
    .bind(id).first<{total:number;pending:number;running:number;done:number;failed:number;skipped:number;cancelled:number;paused:number}>();
  return {id,control:batch.control,...counts!};
}
export async function createBatch(env:Env) {
  const id=crypto.randomUUID();
  await env.DB.prepare('INSERT INTO analysis_batches(id,created_at) VALUES (?,?)').bind(id,Date.now()).run();
  return id;
}
export async function attachBatch(env:Env,batchId:string,ids:string[],force=false) {
  await getBatch(env,batchId);
  if (!ids.length) return 0;
  const encoded=JSON.stringify([...new Set(ids)]);
  const rows=await env.DB.prepare('SELECT id FROM images WHERE id IN (SELECT value FROM json_each(?))').bind(encoded).all<{id:string}>();
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
  updates.push(env.DB.prepare(`INSERT INTO analysis_batch_items(batch_id,image_id,status)
    SELECT ?,image_id,status FROM image_analysis WHERE image_id IN (SELECT value FROM json_each(?))
    ON CONFLICT(batch_id,image_id) DO UPDATE SET status=excluded.status`).bind(batchId,encoded));
  await env.DB.batch(updates);
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
  const progress=batchId?await batchProgress(env,batchId):await queueProgress(env);
  if(action==='retry'&&(progress.pending||progress.running))throw new HttpError(409,'analysis_still_running','Chờ toàn bộ ảnh xử lý xong hoặc bấm Huỷ trước khi chạy lại lỗi.');
  const scope=batchId?'image_id IN (SELECT image_id FROM analysis_batch_items WHERE batch_id=?)':'1=1';
  const args=batchId?[batchId]:[];
  const control=action==='pause'?'paused':action==='cancel'?'cancelled':'active';
  const updates:D1PreparedStatement[]=[];
  if(batchId)updates.push(env.DB.prepare('UPDATE analysis_batches SET control=? WHERE id=?').bind(control,batchId));
  else{
    updates.push(env.DB.prepare('UPDATE analysis_queue_settings SET paused=? WHERE id=1').bind(action==='pause'?1:0));
    updates.push(env.DB.prepare(`UPDATE analysis_batches SET control=? WHERE id IN
      (SELECT b.batch_id FROM analysis_batch_items b JOIN image_analysis a ON a.image_id=b.image_id
       WHERE a.status IN ('pending','running') ${action==='retry'?"OR a.status='failed' OR a.control='cancelled'":''})`).bind(control));
  }
  if(action==='cancel'){
    updates.push(env.DB.prepare(`UPDATE analysis_batch_items SET status='skipped' WHERE status IN ('pending','running')
      AND image_id IN (SELECT image_id FROM image_analysis WHERE ${scope} AND status IN ('pending','running'))`).bind(...args));
    updates.push(env.DB.prepare(`UPDATE image_analysis SET status='skipped',control='cancelled',last_error='cancelled',lease_token=NULL,lease_until=0
      WHERE ${scope} AND status IN ('pending','running')`).bind(...args));
  }else if(action==='retry'){
    updates.push(env.DB.prepare(`UPDATE analysis_batch_items SET status='pending' WHERE status!='done' AND image_id IN
      (SELECT image_id FROM image_analysis WHERE ${scope} AND (status='failed' OR control='cancelled'))`).bind(...args));
    updates.push(env.DB.prepare(`UPDATE image_analysis SET status='pending',control='active',attempts=0,next_attempt=0,
      lease_until=0,lease_token=NULL,last_error=NULL WHERE ${scope} AND (status='failed' OR control='cancelled')`).bind(...args));
    updates.push(env.DB.prepare(`UPDATE ai_providers SET cooldown_until=0 WHERE id IN
      (SELECT provider_id FROM image_analysis WHERE ${scope} AND status='pending')`).bind(...args));
    updates.push(env.DB.prepare(`UPDATE image_analysis SET control='active' WHERE ${scope} AND control='paused' AND status IN ('pending','running')`).bind(...args));
  }else{
    updates.push(env.DB.prepare(`UPDATE image_analysis SET control=? WHERE ${scope} AND status IN ('pending','running')`)
      .bind(control,...args));
  }
  await env.DB.batch(updates);await libraryChanged(env);
  return batchId?batchProgress(env,batchId):queueProgress(env);
}
export async function stopPendingAnalysis(env:Env,id:string) {
  await env.DB.batch([
    env.DB.prepare("UPDATE image_analysis SET status='skipped',last_error='manual_edit' WHERE image_id=? AND status='pending'").bind(id),
    env.DB.prepare(`UPDATE analysis_batch_items SET status='skipped' WHERE image_id=? AND status IN ('pending','running')
      AND EXISTS (SELECT 1 FROM image_analysis WHERE image_id=? AND status='skipped')`).bind(id,id),
  ]);
  await libraryChanged(env);
}
