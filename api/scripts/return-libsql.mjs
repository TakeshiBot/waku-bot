import {createClient} from '@libsql/client/web';
import {copyFile,mkdir,open,readFile,unlink,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {credentials,cf,wrangler,localConfig,root,secrets} from './common.mjs';
import {exportDatabase,verifyDatabase,buildIncrementalDump} from './db-transfer.mjs';

const wait=ms=>new Promise(r=>setTimeout(r,ms));
const scratch=path.join(root,'wrangler.db-move.json'),lockPath=path.join(root,'.db-move.lock');
let lock,target,creds,original,directory,workerTouched=false,activationAttempted=false;
async function checkpoint(phase){await writeFile(path.join(directory,'state.json'),JSON.stringify({phase,activationAttempted,updated_at:new Date().toISOString()},null,2)+'\n',{mode:0o600});}
async function deploy(config){await writeFile(scratch,JSON.stringify(config,null,2)+'\n',{mode:0o600});await wrangler(creds,['deploy','--config',scratch]);}
async function probe(base,key,backend,maintenance,images){
  for(let attempt=0;attempt<12;attempt++){
    const headers={authorization:'Bearer '+key,...(maintenance?{'x-waku-maintenance-probe':key}:{})};
    try{const session=await fetch(base+'/v1/session',{headers,signal:AbortSignal.timeout(20000)}),data=await session.json();
      if(session.ok&&data.database===backend){const response=await fetch(base+'/v1/state',{headers,signal:AbortSignal.timeout(20000)}),state=await response.json();if(response.ok&&(images===undefined||state.images.total===images))return;}}
    catch{}await wait(2500);
  }throw new Error('Worker database probe failed');
}
try{
  if(!process.argv.includes('--apply'))throw new Error('Use --apply to perform the transfer');
  lock=await open(lockPath,'wx',0o600);creds=await credentials();original=JSON.parse(await readFile(localConfig,'utf8'));const values=await secrets();
  if(original.vars?.DATABASE_BACKEND!=='d1')throw new Error('Source is not D1');
  if(!values.LIBSQL_URL||!values.LIBSQL_AUTH_TOKEN)throw new Error('Missing libSQL connection');
  const d1=original.d1_databases?.find(x=>x.binding==='DB');if(!d1?.database_id)throw new Error('Missing D1 source');
  const executeD1=async statement=>{const result=await cf(creds,'/d1/database/'+d1.database_id+'/query','POST',typeof statement==='string'?{sql:statement}:{sql:statement.sql,params:statement.args||[]});if(result.some(x=>!x.success))throw new Error('D1 source query failed');return {rows:result[0]?.results||[]};};
  // The live Worker is put into maintenance before this read-only adapter is
  // used, so every D1 query sees a stable source without pretending to lock it.
  const source={transaction:async()=>({execute:executeD1,commit:async()=>{},rollback:async()=>{},close:()=>{}})};
  target=createClient({url:values.LIBSQL_URL,authToken:values.LIBSQL_AUTH_TOKEN,intMode:'bigint',fetch:(u,i)=>fetch(u,{...i,signal:AbortSignal.timeout(60000)})});
  const base=original.vars.PUBLIC_BASE_URL.replace(/\/$/,'');
  await probe(base,values.API_KEY,'d1',false);
  await target.execute('SELECT version FROM api_schema WHERE id=1');
  directory=path.join(root,'.backups','return-libsql-'+new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(directory,{recursive:true,mode:0o700});
  await copyFile(localConfig,path.join(directory,'source-config.json'));
  await writeFile(path.join(directory,'secrets.json'),JSON.stringify(values,null,2)+'\n',{mode:0o600,flag:'wx'});
  console.log('Backing up the existing VPS database...');await exportDatabase(target,path.join(directory,'previous-vps'));
  await checkpoint('target-backed-up');
  const held=structuredClone(original);held.vars.MIGRATION_MAINTENANCE='1';workerTouched=true;await deploy(held);await checkpoint('source-maintenance');
  console.log('Waiting for active uploads and AI tasks to finish...');await wait(35000);const deadline=Date.now()+180000;
  while(true){const rows=await executeD1({sql:'SELECT (SELECT COUNT(*) FROM worker_slots WHERE token IS NOT NULL AND lease_until>?) + (SELECT COUNT(*) FROM ai_provider_slots WHERE token IS NOT NULL AND lease_until>?) AS n',args:[Date.now(),Date.now()]});if(Number(rows.rows[0].n)===0)break;if(Date.now()>deadline)throw new Error('Active tasks did not drain');await wait(3000);}
  console.log('Saving a D1 snapshot and computing the VPS delta...');const snapshot=await exportDatabase(source,path.join(directory,'source-d1'));await checkpoint('snapshot-ready');
  const delta=await buildIncrementalDump(source,stmt=>target.execute(stmt),snapshot.manifest,path.join(directory,'source-d1'));console.log('Changed rows:',JSON.stringify(delta.changes));
  const sql=await readFile(delta.dumpPath,'utf8');await target.executeMultiple('BEGIN IMMEDIATE;\n'+sql+'\nCOMMIT;');
  const verified=await verifyDatabase(stmt=>target.execute(stmt),snapshot.manifest);await checkpoint('data-verified');console.log('Verified tables:',verified.tables,'Images:',verified.images);
  const staged=structuredClone(held);staged.vars.DATABASE_BACKEND='libsql';await deploy(staged);await checkpoint('target-maintenance');await probe(base,values.API_KEY,'libsql',true,verified.images);
  const live=structuredClone(staged);delete live.vars.MIGRATION_MAINTENANCE;activationAttempted=true;await checkpoint('activating');
  await writeFile(localConfig,JSON.stringify(live,null,2)+'\n',{mode:0o600});await deploy(live);await probe(base,values.API_KEY,'libsql',false);await checkpoint('complete');
  console.log('libSQL is active; D1 and R2 remain intact. Private transfer record:',directory);
}catch(error){
  if(workerTouched&&!activationAttempted&&original&&creds){try{console.log('Restoring the D1 backend...');await deploy(original);await checkpoint('restored-source');}catch{console.error('Unable to confirm D1 recovery; inspect the transfer record.');}}
  if(activationAttempted)console.error('Activation acknowledgement incomplete; do not revert to the older D1 snapshot.');
  console.error('Transfer did not complete. Source D1 and VPS files were not deleted.');if(directory)console.error('Private transfer record:',directory);console.error('Failure type:',error?.code||error?.name||'unknown');if(error?.message?.startsWith('Cloudflare API')||error?.message?.startsWith('Wrangler '))console.error(error.message);process.exitCode=1;
}finally{target?.close();if(lock){await lock.close();await unlink(lockPath).catch(()=>{});await unlink(scratch).catch(()=>{});}}
