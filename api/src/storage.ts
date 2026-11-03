import {HttpError,type Env} from './types';

// Decimal GB, deliberately no larger than R2's free storage allowance.
export const STORAGE_LIMIT=10_000_000_000;
type Usage={bytes:number;ready:number;init_token:string|null;init_until:number};
const initializing=()=>new HttpError(503,'storage_initializing','Đang kiểm tra dung lượng R2. Chưa nhận thêm ảnh; hãy thử lại sau.',10);

export async function storageUsage(env:Env){
  const row=await env.DB.prepare('SELECT * FROM storage_usage WHERE id=1').first<Usage>();
  if(!row)throw initializing();
  return {bytes:Number(row.bytes),limit_bytes:STORAGE_LIMIT,available_bytes:Math.max(0,STORAGE_LIMIT-Number(row.bytes)),full:Number(row.bytes)>=STORAGE_LIMIT,ready:row.ready===1};
}

// Bootstrap once from ALL objects, including orphaned/queued deletions. Uploads
// and cleanup fail closed until the snapshot commits. Token fencing prevents a
// timed-out initializer from overwriting a newer snapshot or live reservations.
export async function ensureStorage(env:Env){
  const state=await storageUsage(env);if(state.ready)return;
  const token=crypto.randomUUID(),now=Date.now();
  const lock=await env.DB.prepare(`UPDATE storage_usage SET init_token=?,init_until=?
    WHERE id=1 AND ready=0 AND init_until<? RETURNING id`).bind(token,now+300000,now).first();
  if(!lock)throw initializing();
  try{
    const objects:{key:string;size:number}[]=[];let cursor:string|undefined;
    do{
      const page=await env.IMAGES.list({limit:1000,...(cursor?{cursor}:{})});
      objects.push(...page.objects.map(object=>({key:object.key,size:object.size})));
      cursor=page.truncated?page.cursor:undefined;
    }while(cursor);
    const owns='EXISTS(SELECT 1 FROM storage_usage WHERE id=1 AND ready=0 AND init_token=?)';
    const statements=[env.DB.prepare(`DELETE FROM storage_objects WHERE ${owns}`).bind(token)];
    for(let start=0;start<objects.length;start+=500)statements.push(env.DB.prepare(`INSERT INTO storage_objects(object_key,size)
      SELECT json_extract(value,'$.key'),json_extract(value,'$.size') FROM json_each(?) WHERE ${owns}`)
      .bind(JSON.stringify(objects.slice(start,start+500)),token));
    statements.push(env.DB.prepare('UPDATE storage_usage SET ready=1,init_token=NULL,init_until=0 WHERE id=1 AND ready=0 AND init_token=?').bind(token));
    const result=await env.DB.batch(statements);
    if(!result.at(-1)?.meta.changes)throw initializing();
  }catch(error){
    await env.DB.prepare('UPDATE storage_usage SET init_until=0 WHERE id=1 AND ready=0 AND init_token=?').bind(token).run().catch(()=>{});
    throw error;
  }
}

export async function reserveStorage(env:Env,key:string,size:number){
  await ensureStorage(env);
  // One SQLite write statement plus triggers: simultaneous web/Telegram uploads
  // cannot both spend the same remaining bytes. Reserve BEFORE R2.put.
  const row=await env.DB.prepare(`INSERT INTO storage_objects(object_key,size)
    SELECT ?,? FROM storage_usage WHERE id=1 AND ready=1 AND bytes+?<=? RETURNING object_key`)
    .bind(key,size,size,STORAGE_LIMIT).first();
  if(!row)throw new HttpError(507,'storage_full','Ảnh này sẽ làm kho vượt 10 GB. Đã dừng tải lên để tránh vượt dung lượng miễn phí; hãy xoá bớt ảnh trước khi tiếp tục.');
}
