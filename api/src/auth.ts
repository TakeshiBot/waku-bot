import {equalSecret} from './input';
import {HttpError,type Env} from './types';
import {authorizeCatalogKey} from './keys';

const COOKIE='__Host-waku_session';
const TTL=8*60*60;
function encode(bytes:Uint8Array) {return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
async function signature(key:string,text:string) {
  const imported=await crypto.subtle.importKey('raw',new TextEncoder().encode(key),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  return encode(new Uint8Array(await crypto.subtle.sign('HMAC',imported,new TextEncoder().encode(text))));
}
export async function authorize(request:Request,env:Env) {
  if (!env.API_KEY) throw new HttpError(503,'not_configured','API chưa được cấu hình.');
  if (equalSecret(request.headers.get('authorization') || '',`Bearer ${env.API_KEY}`)) return;
  const cookie=(request.headers.get('cookie') || '').split(';').map(x=>x.trim()).find(x=>x.startsWith(COOKIE+'='))?.slice(COOKIE.length+1);
  if (cookie && cookie.length<300) {
    const [expires,nonce,mac,...extra]=cookie.split('.');
    if (!extra.length && /^\d+$/.test(expires) && Number(expires)>Date.now()/1000
      && Number(expires)<=Date.now()/1000+TTL+60 && /^[a-f0-9-]{36}$/.test(nonce)
      && equalSecret(mac || '',await signature(env.API_KEY,`${expires}.${nonce}`))) {
      if (!['GET','HEAD','OPTIONS'].includes(request.method) && request.headers.get('origin')!==new URL(request.url).origin)
        throw new HttpError(403,'invalid_origin','Vui lòng thao tác từ trang quản lý.');
      return;
    }
  }
  if(await authorizeCatalogKey(request,env))return;
  throw new HttpError(401,'unauthorized','API key không hợp lệ hoặc phiên đăng nhập đã hết hạn.');
}
export async function sessionCookie(env:Env) {
  const body=`${Math.floor(Date.now()/1000)+TTL}.${crypto.randomUUID()}`;
  return `${COOKIE}=${body}.${await signature(env.API_KEY,body)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${TTL}`;
}
export function clearSession() {return `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;}
