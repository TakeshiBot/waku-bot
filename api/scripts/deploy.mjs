import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {cf,configureWebhook,credentials,failed,root,secrets,wrangler,writeConfig,localConfig} from './common.mjs';

try {
  const creds=await credentials();
  console.log('Kiểm tra Cloudflare và tạo tài nguyên nếu chưa có...');
  const databases=await cf(creds,'/d1/database?per_page=100');
  let db=databases.find(db=>db.name==='waku-api');
  if (!db) db=await cf(creds,'/d1/database','POST',{name:'waku-api'});
  const result=await cf(creds,'/r2/buckets?per_page=1000');
  if (!result.buckets.some(bucket=>bucket.name==='waku-api-images')) await cf(creds,'/r2/buckets','POST',{name:'waku-api-images',storageClass:'Standard'});
  const {subdomain}=await cf(creds,'/workers/subdomain');
  if (!subdomain) throw new Error('Không có workers.dev subdomain. Mở Workers & Pages trong Cloudflare để thiết lập tên subdomain trước.');
  const config=JSON.parse(await readFile(path.join(root,'wrangler.jsonc'),'utf8'));
  config.account_id=creds.account;
  config.d1_databases[0].database_id=db.uuid;
  config.vars.OWNER_IDS=creds.owners;
  config.vars.PUBLIC_BASE_URL=`https://${config.name}.${subdomain}.workers.dev`;
  await writeConfig(config);
  const values=await secrets();
  console.log('Áp dụng schema D1...');
  await wrangler(creds,['d1','migrations','apply','DB','--remote','--config',localConfig]);
  console.log('Deploy Worker...');
  await wrangler(creds,['deploy','--config',localConfig]);
  console.log('Cài secrets qua Cloudflare, cấu hình Telegram webhook...');
  await wrangler(creds,['secret','bulk','--config',localConfig],JSON.stringify({...values,TELEGRAM_BOT_TOKEN:creds.bot}));
  const health=await fetch(`${config.vars.PUBLIC_BASE_URL}/health`,{signal:AbortSignal.timeout(30000)});
  if (!health.ok) throw new Error('Không truy cập được Worker sau deploy.');
  await configureWebhook(creds,config,values);
} catch(error) {failed(error);}
