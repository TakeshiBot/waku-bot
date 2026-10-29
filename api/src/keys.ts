import {HttpError,type Env} from './types';

export async function catalogKeyHash(value:string) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');
}
export async function authorizeCatalogKey(request:Request,env:Env):Promise<boolean> {
  const token=request.headers.get('authorization')?.replace(/^Bearer /,'')||'';
  if(!/^wku_[a-f0-9]{64}$/.test(token))return false;
  const row=await env.DB.prepare('SELECT id,last_used_at FROM catalog_keys WHERE key_hash=? AND enabled=1').bind(await catalogKeyHash(token)).first<{id:string;last_used_at:number|null}>();
  if(!row)return false;
  if(request.method!=='GET'||new URL(request.url).pathname!=='/v1/images/random')throw new HttpError(403,'catalog_key_scope','Key dịch vụ chỉ được gọi API lấy ảnh, không có quyền quản trị.');
  if(!row.last_used_at||row.last_used_at<Date.now()-300000)await env.DB.prepare('UPDATE catalog_keys SET last_used_at=? WHERE id=?').bind(Date.now(),row.id).run();
  return true;
}
export async function manageCatalogKeys(request:Request,env:Env,body:Record<string,unknown>) {
  const path=new URL(request.url).pathname;
  if(path==='/v1/keys'&&request.method==='GET')return {keys:(await env.DB.prepare('SELECT id,name,prefix,enabled,created_at,last_used_at FROM catalog_keys ORDER BY created_at DESC,id').all()).results};
  const name=()=>{if(typeof body.name!=='string'||!body.name.trim()||body.name.trim().length>80)throw new HttpError(400,'invalid_name','Tên key dài 1–80 ký tự.');return body.name.trim();};
  if(path==='/v1/keys'&&request.method==='POST'){
    if((await env.DB.prepare('SELECT COUNT(*) n FROM catalog_keys').first<{n:number}>())!.n>=50)throw new HttpError(400,'key_limit','Tối đa 50 key dịch vụ.');
    const label=name(),id=crypto.randomUUID(),key='wku_'+[...crypto.getRandomValues(new Uint8Array(32))].map(b=>b.toString(16).padStart(2,'0')).join(''),prefix=key.slice(0,12),created_at=Date.now();
    await env.DB.prepare('INSERT INTO catalog_keys(id,name,key_hash,prefix,created_at) VALUES (?,?,?,?,?)').bind(id,label,await catalogKeyHash(key),prefix,created_at).run();
    return {key,record:{id,name:label,prefix,enabled:1,created_at,last_used_at:null}};
  }
  const match=path.match(/^\/v1\/keys\/([a-f0-9-]{36})$/);
  if(match&&['PATCH','DELETE'].includes(request.method)){
    const row=await env.DB.prepare('SELECT id,name,enabled FROM catalog_keys WHERE id=?').bind(match[1]).first<{id:string;name:string;enabled:number}>();
    if(!row)throw new HttpError(404,'key_not_found','Không tìm thấy key dịch vụ.');
    if(request.method==='DELETE'){await env.DB.prepare('DELETE FROM catalog_keys WHERE id=?').bind(row.id).run();return {deleted:true};}
    if(body.enabled!==undefined&&typeof body.enabled!=='boolean')throw new HttpError(400,'invalid_key_state','Trạng thái key không hợp lệ.');
    await env.DB.prepare('UPDATE catalog_keys SET name=?,enabled=? WHERE id=?').bind(body.name===undefined?row.name:name(),body.enabled===undefined?row.enabled:body.enabled?1:0,row.id).run();return {updated:true};
  }
  throw new HttpError(404,'not_found','Không tìm thấy đường dẫn.');
}
