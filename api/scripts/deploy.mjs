import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {cf,configureWebhook,credentials,failed,root,secrets,writeSecrets,wrangler,writeConfig,localConfig} from './common.mjs';

try {
  const creds=await credentials();
  const values=await secrets();
  const libsql=Boolean(values.LIBSQL_URL && values.LIBSQL_AUTH_TOKEN);
  if(Boolean(values.LIBSQL_URL)!==Boolean(values.LIBSQL_AUTH_TOKEN)) throw new Error('Thiếu URL hoặc token libSQL.');
  console.log('Kiểm tra Cloudflare và tạo tài nguyên nếu chưa có...');
  let db;
  if(!libsql){
    const databases=await cf(creds,'/d1/database?per_page=100');
    db=databases.find(db=>db.name==='waku-api');
    if (!db) db=await cf(creds,'/d1/database','POST',{name:'waku-api'});
  }
  const result=await cf(creds,'/r2/buckets?per_page=1000');
  if (!result.buckets.some(bucket=>bucket.name==='waku-api-images')) await cf(creds,'/r2/buckets','POST',{name:'waku-api-images',storageClass:'Standard'});
  const {subdomain}=await cf(creds,'/workers/subdomain');
  if (!subdomain) throw new Error('Không có workers.dev subdomain. Mở Workers & Pages trong Cloudflare để thiết lập tên subdomain trước.');
  const config=JSON.parse(await readFile(path.join(root,'wrangler.jsonc'),'utf8'));
  config.account_id=creds.account;
  if(db)config.d1_databases[0].database_id=db.uuid;
  else {
    // Keep the existing D1 binding available for rollback without querying it.
    try {
      const previous=JSON.parse(await readFile(localConfig,'utf8'));
      config.d1_databases=previous.d1_databases;
    } catch(error) {
      if(error.code!=='ENOENT')throw error;
      config.d1_databases=[];
    }
  }
  config.vars.OWNER_IDS=creds.owners;
  config.vars.PUBLIC_BASE_URL=`https://${config.name}.${subdomain}.workers.dev`;
  await writeConfig(config);
  if (creds.aiUrl) {
    Object.assign(values,{AI_BASE_URL:creds.aiUrl,AI_API_KEY:creds.aiKey,AI_MODEL:creds.aiModel});
    await writeSecrets(values);
  }
  if(!libsql){
    console.log('Áp dụng schema D1...');
    await wrangler(creds,['d1','migrations','apply','DB','--remote','--config',localConfig]);
  } else console.log('Sử dụng libSQL; không áp dụng migration lên D1.');
  console.log('Deploy Worker...');
  await wrangler(creds,['deploy','--config',localConfig]);
  console.log('Cài secrets qua Cloudflare, cấu hình Telegram webhook...');
  await wrangler(creds,['secret','bulk','--config',localConfig],JSON.stringify({...values,TELEGRAM_BOT_TOKEN:creds.bot}));
  const health=await fetch(`${config.vars.PUBLIC_BASE_URL}/health`,{signal:AbortSignal.timeout(30000)});
  if (!health.ok) throw new Error('Không truy cập được Worker sau deploy.');
  await configureWebhook(creds,config,values);
} catch(error) {failed(error);}
