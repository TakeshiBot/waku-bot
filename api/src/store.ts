import { mediaType } from './input';
import {dimensionsForRows,imageDimensions,recordDimensions} from './dimensions';
import { HttpError, type Env, type ImageRow, type Metadata } from './types';
import {ensureStorage,reserveStorage} from './storage';
import {sourceIdentity} from './source-identity';
import {sourceProfiles} from './source-profiles';

// Classification follows saved metadata, including when AI is reanalysing it.
export const CLASSIFIED_IMAGE_SQL=`(length(trim(i.title))>0 AND i.rating IN ('safe','r18')
  AND COALESCE(i.image_type,'') IN ('anime','real')
  AND EXISTS(SELECT 1 FROM image_tags ct WHERE ct.image_id=i.id AND length(trim(ct.tag))>0))`;

export function maxBytes(env: Env): number {
  return Math.min(20, Math.max(1, Number(env.MAX_IMAGE_MB) || 10)) * 1024 * 1024;
}
export const MAX_VIDEO_BYTES=20*1024*1024;
export function maxMediaBytes(env:Env){return Math.max(maxBytes(env),MAX_VIDEO_BYTES);}
export const isVideo=(row:{mime_type:string})=>row.mime_type.startsWith('video/');

export async function libraryChanged(env:Env) {
  await env.DB.prepare('UPDATE library_revision SET version=version+1 WHERE id=1').run();
}

export async function image(env: Env, id: string): Promise<ImageRow> {
  const row = await env.DB.prepare('SELECT * FROM images WHERE id = ?').bind(id).first<ImageRow>();
  if (!row) throw new HttpError(404, 'not_found', 'Không tìm thấy ảnh.');
  return row;
}

export async function present(env: Env, row: ImageRow, base: string) {
  return (await presentMany(env,[row],base))[0];
}

export async function selectionItems(env:Env,rows:Pick<ImageRow,'id'|'mime_type'|'source_url'>[]){
  const profiles=await sourceProfiles(env,rows.flatMap(row=>row.source_url?[row.source_url]:[]));
  return rows.map(row=>{const key=row.source_url?.trim()?sourceIdentity(row.source_url).key:null;
    return {id:row.id,mime_type:row.mime_type,source_group:key?profiles.get(key)?.identity_key||key:null};});
}

export async function recordImportAliases(env:Env,id:string,urls:string[]) {
  const aliases=[...new Set(urls)];if(!aliases.length)return;
  await env.DB.prepare(`INSERT INTO image_import_urls(url,image_id) SELECT value,? FROM json_each(?) WHERE true
    ON CONFLICT(url) DO UPDATE SET image_id=excluded.image_id`).bind(id,JSON.stringify(aliases)).run();
}

export async function presentMany(env:Env, rows:ImageRow[], base:string) {
  if (!rows.length) return [];
  const [list,analysis,sizes,profiles]=await Promise.all([
    env.DB.prepare(`SELECT image_id,tag FROM image_tags WHERE image_id IN (${rows.map(()=>'?').join(',')}) ORDER BY tag`)
      .bind(...rows.map(row=>row.id)).all<{image_id:string;tag:string}>(),
    env.DB.prepare(`SELECT image_id,status,last_error FROM image_analysis WHERE image_id IN (${rows.map(()=>'?').join(',')})`)
      .bind(...rows.map(row=>row.id)).all<{image_id:string;status:string;last_error:string|null}>(),
    dimensionsForRows(env,rows),
    sourceProfiles(env,rows.flatMap(row=>row.source_url?[row.source_url]:[])),
  ]);
  const allTags=new Map<string,string[]>();
  for (const item of list.results) {const values=allTags.get(item.image_id) || [];values.push(item.tag);allTags.set(item.image_id,values);}
  const states=new Map(analysis.results.map(item=>[item.image_id,item]));
  const aliases=new Map<string,string[]>();for(const profile of profiles.values()){const keys=aliases.get(profile.identity_key)||[];keys.push(profile.source_key);aliases.set(profile.identity_key,keys);}
  return rows.map(row=>{
    const source=row.source_url?.trim()?sourceIdentity(row.source_url).key:null,sourceGroup=source?profiles.get(source)?.identity_key||source:null;
    const classified=Boolean(row.title.trim()&&['safe','r18'].includes(row.rating)&&['anime','real'].includes(row.image_type||'')
      &&(allTags.get(row.id)||[]).some(tag=>tag.trim()));
    return {
      id:row.id, url:`${base}/media/${row.id}`, filename:row.filename,
      telegram_file_url:env.TELEGRAM_BOT_USERNAME && /^[A-Za-z0-9_]{5,32}$/.test(env.TELEGRAM_BOT_USERNAME)
        ? `https://t.me/${env.TELEGRAM_BOT_USERNAME}?start=file_${row.id.replaceAll('-','')}` : null,
      mime_type:row.mime_type, media_type:isVideo(row)?'video':'image', size:row.size, sha256:row.sha256, title:row.title, metadata_version:row.metadata_version,
      width:sizes.get(row.id)?.width??null,height:sizes.get(row.id)?.height??null,
      source_url:row.source_url, source_group:sourceGroup,source_aliases:sourceGroup?[...new Set([source!,sourceGroup,...aliases.get(sourceGroup)||[]])]:[], rating:row.rating, image_type:row.image_type, original_name:row.original_name, tags:allTags.get(row.id) || [],
      created_at:new Date(row.created_at).toISOString(),
      ai_status:states.get(row.id)?.status || 'off',
      // A previous analysis failure does not invalidate complete saved metadata.
      ai_error:!classified&&states.get(row.id)?.status==='failed'?safeAnalysisError(states.get(row.id)?.last_error):null,
      classified,
    };
  });
}

function safeAnalysisError(code:unknown):string {
  return typeof code==='string'&&/^(provider_http_\d{3}|provider_content_filtered|provider_timeout|provider_unavailable|invalid_provider|invalid_response|incomplete_response|classification_unresolved|image_missing|worker_timeout|analysis_failed|key_decryption_failed|providers_exhausted)$/.test(code)?code:'analysis_failed';
}

async function importedSource(env:Env,row:ImageRow,source:string|null,importUrl:string|null) {
  if(importUrl&&source&&row.source_url!==source){
    // Refresh only the source on duplicate imports. A concurrent manual edit
    // wins, and the existing title, tags and classification stay intact.
    const changed=await env.DB.prepare('UPDATE images SET source_url=?,metadata_version=metadata_version+1 WHERE id=? AND metadata_version=? RETURNING *')
      .bind(source,row.id,row.metadata_version).first<ImageRow>();
    if(changed){await libraryChanged(env);return changed;}
    return image(env,row.id);
  }
  return row;
}

export async function save(env: Env, bytes: ArrayBuffer, info: Metadata, uploader: string | null, originalName:string|null=null, importUrl:string|null=null) {
  const kind = mediaType(bytes),video=kind.mime.startsWith('video/');
  if (bytes.byteLength > (video?MAX_VIDEO_BYTES:maxBytes(env))) throw new HttpError(413,'too_large',video?'Video vượt giới hạn 20 MB.':'Ảnh vượt giới hạn dung lượng.');
  const dimensions=video?null:imageDimensions(bytes);
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const existing = await env.DB.prepare('SELECT * FROM images WHERE sha256 = ?').bind(hash).first<ImageRow>();
  if (existing) {
    if(dimensions)await recordDimensions(env,[{id:existing.id,...dimensions}]);
    if(importUrl)await env.DB.prepare('INSERT INTO image_import_urls(url,image_id) VALUES (?,?) ON CONFLICT(url) DO UPDATE SET image_id=excluded.image_id').bind(importUrl,existing.id).run();
    return {row:await importedSource(env,existing,info.source_url,importUrl), duplicate:true};
  }
  const id = crypto.randomUUID();
  // Unique object keys prevent a pending delete from removing a re-upload of
  // the same hash. The UNIQUE hash in D1 remains the deduplication authority.
  const key = `images/${id}.${kind.ext}`;
  // Journal BEFORE writing R2. Keep the intent when a database response is
  // uncertain: deleting the object immediately could delete a committed image.
  await env.DB.prepare('INSERT INTO object_uploads(object_key,expires_at) VALUES (?,?)').bind(key,Date.now()+3600000).run();
  try{await reserveStorage(env,key,bytes.byteLength);}
  catch(error){
    // A lost DB acknowledgement may already own a reservation. Retain its
    // journal for cleanup rather than freeing uncertain capacity prematurely.
    if(error instanceof HttpError&&error.code==='storage_full')await env.DB.prepare('DELETE FROM object_uploads WHERE object_key=?').bind(key).run();
    throw error;
  }
  await env.IMAGES.put(key, bytes, {httpMetadata:{contentType:kind.mime}});
  await env.DB.batch([env.DB.prepare(`INSERT OR IGNORE INTO images
    (id,sha256,object_key,filename,mime_type,size,title,source_url,rating,uploader_id,random_key,created_at,image_type,original_name,import_url)
    SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM object_uploads WHERE object_key=?)`).bind(
      id,hash,key,`${id}.${kind.ext}`,kind.mime,bytes.byteLength,info.title,info.source_url,
      info.rating,uploader,crypto.getRandomValues(new Uint32Array(1))[0]/2**32,Date.now(),info.image_type || null,originalName?.slice(0,255) || null,importUrl,key,
    ),env.DB.prepare(`INSERT OR IGNORE INTO image_tags(image_id,tag)
      SELECT i.id,t.value FROM images i,json_each(?) t WHERE i.id=?`).bind(JSON.stringify(info.tags),id),
    ...(dimensions?[env.DB.prepare(`INSERT OR IGNORE INTO image_dimensions(image_id,width,height)
      SELECT id,?,? FROM images WHERE id=?`).bind(dimensions.width,dimensions.height,id)]:[]),
    // Duplicates written concurrently still need their unused object removed.
    env.DB.prepare(`INSERT OR IGNORE INTO object_deletions(object_key) SELECT ? WHERE NOT EXISTS(SELECT 1 FROM images WHERE object_key=?)`).bind(key,key),
    env.DB.prepare('DELETE FROM object_uploads WHERE object_key=?').bind(key)]);
  const row = await env.DB.prepare('SELECT * FROM images WHERE sha256 = ?').bind(hash).first<ImageRow>();
  if (!row) throw new Error('Metadata persistence failed');
  if (row.id!==id) {
    await env.DB.prepare('INSERT OR IGNORE INTO object_deletions(object_key) VALUES (?)').bind(key).run();
    await deleteObject(env,key);
  }
  if(row.id===id) await libraryChanged(env);
  if(importUrl)await env.DB.prepare('INSERT INTO image_import_urls(url,image_id) VALUES (?,?) ON CONFLICT(url) DO UPDATE SET image_id=excluded.image_id').bind(importUrl,row.id).run();
  return {row:await importedSource(env,row,info.source_url,importUrl), duplicate:row.id !== id};
}

export async function update(env: Env, id: string, info: Metadata, expectedVersion?:number) {
  const current=await image(env,id),version=expectedVersion??current.metadata_version;
  const result=await env.DB.batch([
    env.DB.prepare('DELETE FROM image_tags WHERE image_id=? AND EXISTS(SELECT 1 FROM images WHERE id=? AND metadata_version=?)').bind(id,id,version),
    env.DB.prepare(`INSERT INTO image_tags(image_id,tag) SELECT i.id,t.value FROM images i,json_each(?) t WHERE i.id=? AND i.metadata_version=?`).bind(JSON.stringify(info.tags),id,version),
    env.DB.prepare('UPDATE images SET title=?,source_url=?,rating=?,image_type=?,metadata_version=metadata_version+1 WHERE id=? AND metadata_version=?').bind(info.title,info.source_url,info.rating,info.image_type || null,id,version),
  ]);
  if(!result[2].meta.changes)throw new HttpError(409,'metadata_conflict','Ảnh đã được thay đổi ở nơi khác hoặc bởi AI. Nội dung bạn đang nhập vẫn được giữ. Đóng rồi mở lại ảnh để xem bản mới trước khi sửa tiếp.');
  await libraryChanged(env);return image(env,id);
}

export async function remove(env: Env, id: string) {
  await ensureStorage(env);
  const row = await image(env,id);
  // Drop access through the API before deleting the private object.
  await env.DB.batch([
    env.DB.prepare('INSERT OR IGNORE INTO object_deletions(object_key) VALUES (?)').bind(row.object_key),
    env.DB.prepare('DELETE FROM images WHERE id=?').bind(id),
  ]);
  await deleteObject(env,row.object_key);
  await libraryChanged(env);
}

async function deleteObject(env: Env, key: string) {
  try {
    await env.IMAGES.delete(key);
    // Only a confirmed physical delete returns capacity. Retrying this batch
    // after an uncertain acknowledgement cannot subtract the same bytes twice.
    await env.DB.batch([
      env.DB.prepare('DELETE FROM storage_objects WHERE object_key=?').bind(key),
      env.DB.prepare('DELETE FROM object_deletions WHERE object_key=?').bind(key),
    ]);
  } catch { console.warn('Object deletion queued for retry'); }
}

export async function cleanupObjects(env: Env) {
  await ensureStorage(env);
  // Claim expired intents atomically. A late metadata transaction must still
  // own its intent to commit, so cleanup cannot remove a referenced image.
  await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO object_deletions(object_key) SELECT object_key FROM object_uploads
      WHERE object_key IN (SELECT object_key FROM object_uploads WHERE expires_at<? ORDER BY expires_at LIMIT 5)
      AND NOT EXISTS(SELECT 1 FROM images WHERE images.object_key=object_uploads.object_key)`).bind(Date.now()),
    env.DB.prepare('DELETE FROM object_uploads WHERE object_key IN (SELECT object_key FROM object_deletions)'),
  ]);
  const rows = await env.DB.prepare('SELECT object_key FROM object_deletions LIMIT 5').all<{object_key:string}>();
  for (const row of rows.results) await deleteObject(env,row.object_key);
}

export async function choose(env: Env, rating: string, tags: string[], count: number, exclude: string[], imageType: string | null = null, subject = '', media = 'image') {
  if (!['image','video'].includes(media)) throw new HttpError(400,'invalid_media_type','media_type: image hoặc video.');
  if (media==='video') count=1;
  if (!['safe','r18','mixed'].includes(rating)) throw new HttpError(400,'invalid_rating','rating: safe, r18 hoặc mixed.');
  if (imageType !== null && !['anime','real'].includes(imageType)) throw new HttpError(400,'invalid_type','Kiểu ảnh không hợp lệ.');
  const ratings = rating === 'mixed' ? ['safe','r18'] : [rating];
  const terms=[...new Set(subject.normalize('NFKC').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[])];
  if(subject.length>80||terms.length>6)throw new HttpError(400,'invalid_query','Tên tìm kiếm tối đa 80 ký tự và 6 từ.');
  if(subject.trim()&&!terms.length)throw new HttpError(400,'invalid_query','Tên tìm kiếm phải có chữ hoặc số.');
  // Metadata token matches allow Kurumi to match tokisaki_kurumi without
  // equating it with unrelated prefixes such as kurumiko. Bound values only.
  const words=(column:string)=>`' '||${[...'_-()[]:,.!?/'].reduce((sql,char)=>`replace(${sql},'${char}',' ')`,`lower(${column})`)}||' '`;
  const filters = [
    CLASSIFIED_IMAGE_SQL,
    media==='video'?"i.mime_type LIKE 'video/%'":"i.mime_type NOT LIKE 'video/%'",
    `i.rating IN (${ratings.map(()=>'?').join(',')})`,
    ...(imageType ? ['i.image_type=?'] : []),
    ...tags.map(()=> 'EXISTS (SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND t.tag=?)'),
    ...terms.map(()=>`(instr(${words('i.title')},?)>0 OR EXISTS(SELECT 1 FROM image_tags nt WHERE nt.image_id=i.id AND instr(${words('nt.tag')},?)>0))`),
    ...(exclude.length ? [`i.id NOT IN (${exclude.map(()=>'?').join(',')})`] : []),
  ];
  const args = [...ratings,...(imageType ? [imageType] : []),...tags,...terms.flatMap(term=>[' '+term+' ',' '+term+' ']),...exclude];
  const pivot = crypto.getRandomValues(new Uint32Array(1))[0]/2**32;
  const first = await env.DB.prepare(`SELECT i.* FROM images i WHERE ${filters.join(' AND ')}
    AND i.random_key>=? ORDER BY i.random_key LIMIT ?`).bind(...args,pivot,count).all<ImageRow>();
  if (first.results.length === count) return first.results;
  const rest = await env.DB.prepare(`SELECT i.* FROM images i WHERE ${filters.join(' AND ')}
    AND i.random_key<? ORDER BY i.random_key LIMIT ?`).bind(...args,pivot,count-first.results.length).all<ImageRow>();
  return [...first.results,...rest.results];
}
