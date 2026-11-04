import {mediaType} from './input';
import {imageDimensions} from './dimensions';
import {reserveStorage} from './storage';
import {HttpError,type Env,type ImageRow} from './types';

export const MAX_FRAME_BYTES=512*1024;
export async function videoFrame(env:Env,id:string){
  return env.DB.prepare('SELECT object_key FROM video_frames WHERE image_id=?').bind(id).first<{object_key:string}>();
}

// Immutable, small JPEG sampled by the browser. No video decoding on the VPS.
export async function saveVideoFrame(env:Env,row:ImageRow,bytes:ArrayBuffer){
  if(!row.mime_type.startsWith('video/'))throw new HttpError(400,'not_video','File này không phải video.');
  const dimensions=imageDimensions(bytes);
  if(bytes.byteLength>MAX_FRAME_BYTES||mediaType(bytes).mime!=='image/jpeg'||!dimensions||Math.max(dimensions.width,dimensions.height)>1280)
    throw new HttpError(400,'invalid_frame','Khung hình phải là JPEG, tối đa 512 KB và 1280 px.');
  if(await videoFrame(env,row.id))return;
  const key=`video-frames/${row.id}/${crypto.randomUUID()}.jpg`;
  await env.DB.prepare('INSERT INTO object_uploads(object_key,expires_at) VALUES (?,?)').bind(key,Date.now()+3600000).run();
  try{await reserveStorage(env,key,bytes.byteLength);}catch(error){
    if(error instanceof HttpError&&error.code==='storage_full')await env.DB.prepare('DELETE FROM object_uploads WHERE object_key=?').bind(key).run();
    throw error;
  }
  await env.IMAGES.put(key,bytes,{httpMetadata:{contentType:'image/jpeg'}});
  // The journal fences late commits; concurrent requests keep just one frame.
  await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO video_frames(image_id,object_key) SELECT id,? FROM images WHERE id=?
      AND EXISTS(SELECT 1 FROM object_uploads WHERE object_key=?)`).bind(key,row.id,key),
    env.DB.prepare(`INSERT OR IGNORE INTO object_deletions(object_key) SELECT ?
      WHERE NOT EXISTS(SELECT 1 FROM video_frames WHERE object_key=?)`).bind(key,key),
    env.DB.prepare('DELETE FROM object_uploads WHERE object_key=?').bind(key),
    env.DB.prepare('UPDATE library_revision SET version=version+1 WHERE id=1'),
  ]);
  if(!await videoFrame(env,row.id))throw new HttpError(409,'frame_not_saved','Chưa lưu được khung hình; hãy thử lại.');
}
