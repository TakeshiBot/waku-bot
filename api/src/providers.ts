import {publicUrl} from './input';
import {HttpError,type Env} from './types';

export interface AiConnection {base_url:string;api_key:string;model:string;}
interface Provider {id:string;name:string;base_url:string;key_cipher:string;preferred_model_id:string|null;}
interface Model {id:string;provider_id:string;model:string;}
interface Preference {initialized:number;provider_id:string|null;model_id:string|null;}
const legacyProvider='00000000-0000-4000-8000-000000000001',legacyModel='00000000-0000-4000-8000-000000000002';

function legacy(env:Env):AiConnection|null {
  return env.AI_BASE_URL&&env.AI_API_KEY&&env.AI_MODEL?{base_url:env.AI_BASE_URL,api_key:env.AI_API_KEY,model:env.AI_MODEL}:null;
}
async function key(env:Env) {
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode('waku/provider-keys/v1:'+env.API_KEY));
  return crypto.subtle.importKey('raw',digest,'AES-GCM',false,['encrypt','decrypt']);
}
async function encrypt(env:Env,id:string,value:string) {
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const data=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(id)},await key(env),new TextEncoder().encode(value)));
  return btoa(String.fromCharCode(...iv,...data));
}
async function decrypt(env:Env,id:string,value:string) {
  const bytes=Uint8Array.from(atob(value),c=>c.charCodeAt(0));
  return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.slice(0,12),additionalData:new TextEncoder().encode(id)},await key(env),bytes.slice(12)));
}
export async function aiConnection(env:Env):Promise<AiConnection|null> {
  const row=await env.DB.prepare(`SELECT s.initialized,p.id,p.base_url,p.key_cipher,m.model FROM ai_preferences s
    LEFT JOIN ai_providers p ON p.id=s.provider_id LEFT JOIN ai_models m ON m.id=s.model_id AND m.provider_id=p.id WHERE s.id=1`)
    .first<{initialized:number;id:string|null;base_url:string|null;key_cipher:string|null;model:string|null}>();
  if(!row?.initialized)return legacy(env);
  if(!row.id||!row.base_url||!row.key_cipher||!row.model)return null;
  try{return {base_url:row.base_url,api_key:await decrypt(env,row.id,row.key_cipher),model:row.model};}
  catch{console.warn('Saved AI key could not be decrypted; update it in API settings');return null;}
}
async function initialize(env:Env) {
  const preference=await env.DB.prepare('SELECT * FROM ai_preferences WHERE id=1').first<Preference>();
  if(preference?.initialized)return;
  const existing=legacy(env);
  if(existing)await env.DB.batch([
    env.DB.prepare('INSERT OR IGNORE INTO ai_providers(id,name,base_url,key_cipher,preferred_model_id) VALUES (?,?,?,?,?)')
      .bind(legacyProvider,'API Waku',existing.base_url,await encrypt(env,legacyProvider,existing.api_key),legacyModel),
    env.DB.prepare('INSERT OR IGNORE INTO ai_models(id,provider_id,model) VALUES (?,?,?)').bind(legacyModel,legacyProvider,existing.model),
    env.DB.prepare('UPDATE ai_preferences SET initialized=1,provider_id=?,model_id=? WHERE id=1 AND initialized=0').bind(legacyProvider,legacyModel),
  ]);
  else await env.DB.prepare('UPDATE ai_preferences SET initialized=1 WHERE id=1 AND initialized=0').run();
}
export async function providerSettings(env:Env) {
  await initialize(env);
  const [providers,models,selection]=await Promise.all([
    env.DB.prepare('SELECT id,name,base_url,preferred_model_id FROM ai_providers ORDER BY rowid').all<Omit<Provider,'key_cipher'>>(),
    env.DB.prepare('SELECT * FROM ai_models ORDER BY rowid').all<Model>(),
    env.DB.prepare('SELECT provider_id,model_id FROM ai_preferences WHERE id=1').first<Preference>(),
  ]);
  return {providers:providers.results.map(provider=>({...provider,has_key:true,models:models.results.filter(model=>model.provider_id===provider.id)})),
    selected_provider:selection?.provider_id || null,selected_model:selection?.model_id || null,
    enabled:Boolean(selection?.provider_id&&selection?.model_id)};
}
function text(value:unknown,label:string,max:number) {
  if(typeof value!=='string'||!value.trim()||value.length>max||/[\u0000-\u001f\u007f]/.test(value))throw new HttpError(400,'invalid_setting',`${label} không hợp lệ (tối đa ${max} ký tự).`);
  return value.trim();
}
async function provider(env:Env,id:string) {
  const row=await env.DB.prepare('SELECT * FROM ai_providers WHERE id=?').bind(id).first<Provider>();
  if(!row)throw new HttpError(404,'provider_not_found','Không tìm thấy API.');return row;
}
async function select(env:Env,providerId:string|null,modelId?:string|null) {
  if(!providerId){await env.DB.prepare('UPDATE ai_preferences SET provider_id=NULL,model_id=NULL WHERE id=1').run();return;}
  const p=await provider(env,providerId);
  let model:Model|null=null;
  if(modelId){model=await env.DB.prepare('SELECT * FROM ai_models WHERE id=? AND provider_id=?').bind(modelId,providerId).first<Model>();if(!model)throw new HttpError(400,'invalid_model','Model không thuộc API đã chọn.');}
  else model=await env.DB.prepare('SELECT * FROM ai_models WHERE provider_id=? ORDER BY (id=?) DESC,rowid LIMIT 1').bind(providerId,p.preferred_model_id || '').first<Model>();
  await env.DB.batch([
    env.DB.prepare('UPDATE ai_preferences SET provider_id=?,model_id=? WHERE id=1').bind(providerId,model?.id || null),
    env.DB.prepare('UPDATE ai_providers SET preferred_model_id=? WHERE id=?').bind(model?.id || null,providerId),
  ]);
}
export async function manageProviders(request:Request,env:Env,body:Record<string,unknown>) {
  await initialize(env);const path=new URL(request.url).pathname,method=request.method;
  if(path==='/v1/ai/settings'&&method==='GET')return providerSettings(env);
  if(path==='/v1/ai/selection'&&method==='PUT'){
    if(body.provider_id!==null&&typeof body.provider_id!=='string'||body.model_id!=null&&typeof body.model_id!=='string')throw new HttpError(400,'invalid_selection','Lựa chọn API/model không hợp lệ.');
    await select(env,body.provider_id as string|null,body.model_id as string|null|undefined);
  }else if(path==='/v1/ai/providers'&&method==='POST'){
    const count=await env.DB.prepare('SELECT COUNT(*) n FROM ai_providers').first<{n:number}>();if(count!.n>=30)throw new HttpError(400,'provider_limit','Tối đa 30 API.');
    const id=crypto.randomUUID(),name=text(body.name,'Tên API',80),url=publicUrl(body.base_url);
    if(url.protocol!=='https:'||url.search||url.hash)throw new HttpError(400,'invalid_provider','Dùng URL HTTPS của API, ví dụ https://api.example.com/v1.');
    await env.DB.prepare('INSERT INTO ai_providers(id,name,base_url,key_cipher) VALUES (?,?,?,?)').bind(id,name,url.href.replace(/\/$/,''),await encrypt(env,id,text(body.api_key,'API key',4096))).run();
  }else{
    const p=path.match(/^\/v1\/ai\/providers\/([a-f0-9-]{36})(\/models)?$/),m=path.match(/^\/v1\/ai\/models\/([a-f0-9-]{36})$/);
    if(p){const row=await provider(env,p[1]);
      if(!p[2]&&method==='PATCH'){
        const name=text(body.name ?? row.name,'Tên API',80),url=publicUrl(body.base_url ?? row.base_url);
        if(url.protocol!=='https:'||url.search||url.hash)throw new HttpError(400,'invalid_provider','URL API phải là HTTPS và không có query.');
        const cipher=body.api_key?await encrypt(env,row.id,text(body.api_key,'API key',4096)):row.key_cipher;
        await env.DB.prepare('UPDATE ai_providers SET name=?,base_url=?,key_cipher=? WHERE id=?').bind(name,url.href.replace(/\/$/,''),cipher,row.id).run();
      }else if(!p[2]&&method==='DELETE')await env.DB.prepare('DELETE FROM ai_providers WHERE id=?').bind(row.id).run();
      else if(p[2]&&method==='POST'){
        const model=text(body.model,'Model ID',200),existing=await env.DB.prepare('SELECT id FROM ai_models WHERE provider_id=? AND model=?').bind(row.id,model).first();
        if(existing)throw new HttpError(409,'duplicate_model','API này đã có model đó.');
        const count=await env.DB.prepare('SELECT COUNT(*) n FROM ai_models WHERE provider_id=?').bind(row.id).first<{n:number}>();if(count!.n>=100)throw new HttpError(400,'model_limit','Tối đa 100 model/API.');
        const id=crypto.randomUUID();await env.DB.prepare('INSERT INTO ai_models(id,provider_id,model) VALUES (?,?,?)').bind(id,row.id,model).run();
        const selected=await env.DB.prepare('SELECT * FROM ai_preferences WHERE id=1').first<Preference>();
        if(selected?.provider_id===row.id&&!selected.model_id)await select(env,row.id,id);
      }else throw new HttpError(405,'method_not_allowed','Thao tác không hỗ trợ.');
    }else if(m){
      const model=await env.DB.prepare('SELECT * FROM ai_models WHERE id=?').bind(m[1]).first<Model>();if(!model)throw new HttpError(404,'model_not_found','Không tìm thấy model.');
      if(method==='PATCH'){
        const name=text(body.model,'Model ID',200),duplicate=await env.DB.prepare('SELECT id FROM ai_models WHERE provider_id=? AND model=? AND id!=?').bind(model.provider_id,name,model.id).first();
        if(duplicate)throw new HttpError(409,'duplicate_model','API này đã có model đó.');
        await env.DB.prepare('UPDATE ai_models SET model=? WHERE id=?').bind(name,model.id).run();
      }else if(method==='DELETE'){
        const selected=await env.DB.prepare('SELECT * FROM ai_preferences WHERE id=1').first<Preference>();
        await env.DB.prepare('DELETE FROM ai_models WHERE id=?').bind(model.id).run();if(selected?.model_id===model.id)await select(env,model.provider_id);
      }else throw new HttpError(405,'method_not_allowed','Thao tác không hỗ trợ.');
    }else throw new HttpError(404,'not_found','Không tìm thấy đường dẫn.');
  }
  return providerSettings(env);
}
