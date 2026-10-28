import { imageType } from './input';
import { HttpError, type Env, type ImageRow, type Metadata } from './types';

export function maxBytes(env: Env): number {
  return Math.min(20, Math.max(1, Number(env.MAX_IMAGE_MB) || 10)) * 1024 * 1024;
}

export async function image(env: Env, id: string): Promise<ImageRow> {
  const row = await env.DB.prepare('SELECT * FROM images WHERE id = ?').bind(id).first<ImageRow>();
  if (!row) throw new HttpError(404, 'not_found', 'Không tìm thấy ảnh.');
  return row;
}

export async function present(env: Env, row: ImageRow, base: string) {
  return (await presentMany(env,[row],base))[0];
}

export async function presentMany(env:Env, rows:ImageRow[], base:string) {
  if (!rows.length) return [];
  const list=await env.DB.prepare(`SELECT image_id,tag FROM image_tags WHERE image_id IN (${rows.map(()=>'?').join(',')}) ORDER BY tag`)
    .bind(...rows.map(row=>row.id)).all<{image_id:string;tag:string}>();
  const allTags=new Map<string,string[]>();
  for (const item of list.results) {const values=allTags.get(item.image_id) || [];values.push(item.tag);allTags.set(item.image_id,values);}
  return rows.map(row=>({
    id:row.id, url:`${base}/media/${row.id}`, filename:row.filename,
    mime_type:row.mime_type, size:row.size, sha256:row.sha256, title:row.title,
    source_url:row.source_url, rating:row.rating, tags:allTags.get(row.id) || [],
    created_at:new Date(row.created_at).toISOString(),
  }));
}

export async function save(env: Env, bytes: ArrayBuffer, info: Metadata, uploader: string | null) {
  if (bytes.byteLength > maxBytes(env)) throw new HttpError(413,'too_large','Ảnh vượt giới hạn dung lượng.');
  const kind = imageType(bytes);
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const existing = await env.DB.prepare('SELECT * FROM images WHERE sha256 = ?').bind(hash).first<ImageRow>();
  if (existing) return {row:existing, duplicate:true};
  const id = crypto.randomUUID();
  // Unique object keys prevent a pending delete from removing a re-upload of
  // the same hash. The UNIQUE hash in D1 remains the deduplication authority.
  const key = `images/${id}.${kind.ext}`;
  await env.IMAGES.put(key, bytes, {httpMetadata:{contentType:kind.mime}});
  await env.DB.batch([env.DB.prepare(`INSERT OR IGNORE INTO images
    (id,sha256,object_key,filename,mime_type,size,title,source_url,rating,uploader_id,random_key,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id,hash,key,`${id}.${kind.ext}`,kind.mime,bytes.byteLength,info.title,info.source_url,
      info.rating,uploader,crypto.getRandomValues(new Uint32Array(1))[0]/2**32,Date.now(),
    ),...info.tags.map(tag=>env.DB.prepare(
      'INSERT OR IGNORE INTO image_tags(image_id,tag) SELECT id,? FROM images WHERE id=?').bind(tag,id))]);
  const row = await env.DB.prepare('SELECT * FROM images WHERE sha256 = ?').bind(hash).first<ImageRow>();
  if (!row) throw new Error('Metadata persistence failed');
  if (row.id!==id) {
    await env.DB.prepare('INSERT OR IGNORE INTO object_deletions(object_key) VALUES (?)').bind(key).run();
    await deleteObject(env,key);
  }
  return {row, duplicate:row.id !== id};
}

export async function update(env: Env, id: string, info: Metadata) {
  await image(env,id);
  await env.DB.batch([
    env.DB.prepare('UPDATE images SET title=?,source_url=?,rating=? WHERE id=?').bind(info.title,info.source_url,info.rating,id),
    env.DB.prepare('DELETE FROM image_tags WHERE image_id=?').bind(id),
    ...info.tags.map(tag=>env.DB.prepare('INSERT INTO image_tags(image_id,tag) VALUES (?,?)').bind(id,tag)),
  ]);
  return image(env,id);
}

export async function remove(env: Env, id: string) {
  const row = await image(env,id);
  // Drop access through the API before deleting the private object.
  await env.DB.batch([
    env.DB.prepare('INSERT OR IGNORE INTO object_deletions(object_key) VALUES (?)').bind(row.object_key),
    env.DB.prepare('DELETE FROM images WHERE id=?').bind(id),
  ]);
  await deleteObject(env,row.object_key);
}

async function deleteObject(env: Env, key: string) {
  try {
    await env.IMAGES.delete(key);
    await env.DB.prepare('DELETE FROM object_deletions WHERE object_key=?').bind(key).run();
  } catch { console.warn('Object deletion queued for retry'); }
}

export async function cleanupObjects(env: Env) {
  const rows = await env.DB.prepare('SELECT object_key FROM object_deletions LIMIT 5').all<{object_key:string}>();
  for (const row of rows.results) await deleteObject(env,row.object_key);
}

export async function choose(env: Env, rating: string, tags: string[], count: number, exclude: string[]) {
  if (!['safe','r18','mixed'].includes(rating)) throw new HttpError(400,'invalid_rating','rating: safe, r18 hoặc mixed.');
  const ratings = rating === 'mixed' ? ['safe','r18'] : [rating];
  const filters = [
    `i.rating IN (${ratings.map(()=>'?').join(',')})`,
    ...tags.map(()=> 'EXISTS (SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND t.tag=?)'),
    ...(exclude.length ? [`i.id NOT IN (${exclude.map(()=>'?').join(',')})`] : []),
  ];
  const args = [...ratings,...tags,...exclude];
  const pivot = crypto.getRandomValues(new Uint32Array(1))[0]/2**32;
  const first = await env.DB.prepare(`SELECT i.* FROM images i WHERE ${filters.join(' AND ')}
    AND i.random_key>=? ORDER BY i.random_key LIMIT ?`).bind(...args,pivot,count).all<ImageRow>();
  if (first.results.length === count) return first.results;
  const rest = await env.DB.prepare(`SELECT i.* FROM images i WHERE ${filters.join(' AND ')}
    AND i.random_key<? ORDER BY i.random_key LIMIT ?`).bind(...args,pivot,count-first.results.length).all<ImageRow>();
  return [...first.results,...rest.results];
}
