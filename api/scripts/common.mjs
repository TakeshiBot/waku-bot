import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';

export const root=fileURLToPath(new URL('..',import.meta.url));
export const localConfig=path.join(root,'wrangler.local.json');
const secretPath=path.join(root,'.secrets.json');
export function argument(name) {const i=process.argv.indexOf(name);return i>=0?process.argv[i+1]:undefined;}

export async function credentials() {
  const filename=argument('--credentials');
  const text=filename?await readFile(path.resolve(filename),'utf8'):'';
  const field=(pattern)=>text.match(pattern)?.[1]?.trim().replace(/^['"]|['"]$/g,'');
  const result={
    token:process.env.CLOUDFLARE_API_TOKEN || field(/^(?:cloudflare_token|token\s+cloudflare)(?:\s*[:=]\s*|\s+)([^\s]+)\s*$/im),
    account:process.env.CLOUDFLARE_ACCOUNT_ID || field(/^(?:account_id|account\s+id)(?:\s*[:=]\s*|\s+)([a-f0-9]{32})\s*$/im),
    bot:process.env.TELEGRAM_BOT_TOKEN || text.match(/\b\d{8,12}:[A-Za-z0-9_-]{30,}\b/)?.[0],
    owners:process.env.OWNER_IDS || field(/^(?:admin\s+id|owners?|owner_ids)(?:\s*[:=]\s*|\s+)([\d,\s]+)$/im)?.replace(/\s+/g,','),
    aiUrl:process.env.AI_BASE_URL || field(/^ai_base_url(?:\s*[:=]\s*|\s+)([^\s]+)\s*$/im),
    aiKey:process.env.AI_API_KEY || field(/^ai_api_key(?:\s*[:=]\s*|\s+)([^\s]+)\s*$/im),
    aiModel:process.env.AI_MODEL || field(/^ai_model(?:\s*[:=]\s*|\s+)([^\s]+)\s*$/im),
  };
  if (!result.token || !result.account || !result.bot || !result.owners) throw new Error('Thiếu Cloudflare token/Account ID hoặc Telegram token/Owner ID. Dùng --credentials đường-dẫn-api.txt hoặc biến môi trường.');
  if (!/^[a-f0-9]{32}$/i.test(result.account) || !/^\d+(?:,\d+)*$/.test(result.owners)) throw new Error('Account ID hoặc Owner ID không hợp lệ.');
  if ([result.aiUrl,result.aiKey,result.aiModel].some(Boolean) && ![result.aiUrl,result.aiKey,result.aiModel].every(Boolean)) throw new Error('Thiếu AI_BASE_URL, AI_API_KEY hoặc AI_MODEL. Cần điền đủ cả ba để bật AI.');
  return result;
}

export async function cf(creds,endpoint,method='GET',body) {
  let response;
  try {response=await fetch(`https://api.cloudflare.com/client/v4/accounts/${creds.account}${endpoint}`,{
    method,headers:{authorization:`Bearer ${creds.token}`,'content-type':'application/json'},
    ...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(30000),
  });} catch {throw new Error('Không kết nối được Cloudflare API.');}
  const data=await response.json();
  if (!response.ok || !data.success) {
    const codes=(data.errors || []).map(x=>x.code).join(',');
    throw new Error(`Cloudflare API ${method} ${endpoint.split('?')[0]}: HTTP ${response.status}, mã ${codes || 'unknown'}. Kiểm tra quyền API token và trạng thái R2 trong dashboard.`);
  }
  return data.result;
}

export async function telegram(creds,method,payload={}) {
  let response;
  try {response=await fetch(`https://api.telegram.org/bot${creds.bot}/${method}`,{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(30000),
  });} catch {throw new Error(`Không kết nối được Telegram (${method}).`);}
  const data=await response.json();
  if (!response.ok || !data.ok) throw new Error(`Telegram ${method}: HTTP ${data.error_code || response.status}.`);
  return data.result;
}

export async function secrets() {
  try {return JSON.parse(await readFile(secretPath,'utf8'));}
  catch(error) {if (error.code!=='ENOENT') throw error;}
  const values={API_KEY:randomBytes(32).toString('hex'),TELEGRAM_WEBHOOK_SECRET:randomBytes(32).toString('hex')};
  await writeFile(secretPath,JSON.stringify(values,null,2)+'\n',{mode:0o600,flag:'wx'});
  return values;
}
export async function writeSecrets(values) {await writeFile(secretPath,JSON.stringify(values,null,2)+'\n',{mode:0o600});}

export async function wrangler(creds,args,input) {
  // Secrets are passed through stdin/env, never command arguments or log text.
  const child=spawn(process.execPath,[path.join(root,'node_modules/wrangler/bin/wrangler.js'),...args],{
    cwd:root,env:{...process.env,CLOUDFLARE_API_TOKEN:creds.token,CLOUDFLARE_ACCOUNT_ID:creds.account,CI:'true',WRANGLER_SEND_METRICS:'false'},
    stdio:['pipe','pipe','pipe'],windowsHide:true,
  });
  const redact=(data)=>{
    let message=data.toString();
    const inputValues=input?Object.values(JSON.parse(input)).filter(x=>typeof x==='string'):[];
    for (const value of [creds.token,creds.bot,creds.aiKey,input,...inputValues]) if (value) message=message.split(value).join('[REDACTED]');
    return message.replace(/\b\d{8,12}:[A-Za-z0-9_-]{30,}/g,'[BOT_TOKEN]');
  };
  child.stdout.on('data',data=>process.stdout.write(redact(data)));
  child.stderr.on('data',data=>process.stderr.write(redact(data)));
  child.stdin.end(input);
  await new Promise((resolve,reject)=>{child.on('error',()=>reject(new Error('Không khởi chạy được Wrangler.')));child.on('exit',code=>code===0?resolve():reject(new Error(`Wrangler thất bại (exit ${code}).`)));});
}

export async function configureWebhook(creds,config,values) {
  const bot=await telegram(creds,'getMe');
  const target=`${config.vars.PUBLIC_BASE_URL}/telegram/webhook`;
  await telegram(creds,'setWebhook',{
    url:target,secret_token:values.TELEGRAM_WEBHOOK_SECRET,
    allowed_updates:['message','callback_query'],max_connections:5,
  });
  const state=await telegram(creds,'getWebhookInfo');
  if (state.url!==target) throw new Error('URL webhook sau khi cấu hình không khớp.');
  await telegram(creds,'setMyCommands',{commands:[
    {command:'start',description:'Hướng dẫn upload ảnh'},
    {command:'help',description:'Cách thêm ảnh, tag và phân loại'},
    {command:'stats',description:'Thống kê kho ảnh'},
  ]});
  console.log(`Bot upload: @${bot.username}\nAPI: ${config.vars.PUBLIC_BASE_URL}\nAPI key được lưu tại ${secretPath} (không gửi key lên chat).`);
}

export async function readConfig() {return JSON.parse(await readFile(localConfig,'utf8'));}
export async function writeConfig(config) {await writeFile(localConfig,JSON.stringify(config,null,2)+'\n');}

export function failed(error) {
  // Avoid printing raw exceptions/stacks that may include credential URLs.
  console.error(error?.message?.startsWith('Cloudflare API') || error?.message?.startsWith('Telegram ')
    || /^(Thiếu |Không |Account ID|URL webhook|Wrangler )/.test(error?.message || '') ? error.message : 'Triển khai chưa hoàn tất. Kiểm tra cấu hình và kết nối rồi chạy lại; không cần xóa tài nguyên.');
  process.exitCode=1;
}
