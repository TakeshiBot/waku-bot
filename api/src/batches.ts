import {HttpError,type Env} from './types';

export async function batchProgress(env:Env,id:string) {
  const batch=await env.DB.prepare('SELECT id FROM analysis_batches WHERE id=?').bind(id).first();
  if (!batch) throw new HttpError(404,'batch_not_found','Không tìm thấy đợt xử lý AI.');
  const counts=await env.DB.prepare(`SELECT COUNT(*) total,
    COALESCE(SUM(status='pending'),0) pending,COALESCE(SUM(status='running'),0) running,
    COALESCE(SUM(status='done'),0) done,COALESCE(SUM(status='failed'),0) failed,
    COALESCE(SUM(status='skipped'),0) skipped FROM analysis_batch_items WHERE batch_id=?`)
    .bind(id).first<{total:number;pending:number;running:number;done:number;failed:number;skipped:number}>();
  return {id,...counts!};
}
export async function createBatch(env:Env) {
  const id=crypto.randomUUID();
  await env.DB.prepare('INSERT INTO analysis_batches(id,created_at) VALUES (?,?)').bind(id,Date.now()).run();
  return id;
}
export async function attachBatch(env:Env,batchId:string,ids:string[],force=false) {
  await batchProgress(env,batchId);
  if (!ids.length) return 0;
  const encoded=JSON.stringify([...new Set(ids)]);
  const rows=await env.DB.prepare('SELECT id FROM images WHERE id IN (SELECT value FROM json_each(?))').bind(encoded).all<{id:string}>();
  if (force) await env.DB.prepare(`INSERT INTO image_analysis(image_id,status) SELECT id,'pending' FROM images
    WHERE id IN (SELECT value FROM json_each(?))
    ON CONFLICT(image_id) DO UPDATE SET status='pending',attempts=0,lease_until=0,next_attempt=0,last_error=NULL
    WHERE image_analysis.status!='running'`).bind(encoded).run();
  await env.DB.prepare(`INSERT INTO analysis_batch_items(batch_id,image_id,status)
    SELECT ?,image_id,status FROM image_analysis WHERE image_id IN (SELECT value FROM json_each(?))
    ON CONFLICT(batch_id,image_id) DO UPDATE SET status=excluded.status`).bind(batchId,encoded).run();
  return rows.results.length;
}
export async function stopPendingAnalysis(env:Env,id:string) {
  await env.DB.batch([
    env.DB.prepare("UPDATE image_analysis SET status='skipped',last_error='manual_edit' WHERE image_id=? AND status='pending'").bind(id),
    env.DB.prepare(`UPDATE analysis_batch_items SET status='skipped' WHERE image_id=? AND status IN ('pending','running')
      AND EXISTS (SELECT 1 FROM image_analysis WHERE image_id=? AND status='skipped')`).bind(id,id),
  ]);
}
