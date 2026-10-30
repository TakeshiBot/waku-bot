import {createClient} from '@libsql/client/web';
import {copyFile,mkdir,open,readFile,unlink,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {credentials,cf,wrangler,localConfig,root,secrets} from './common.mjs';
import {exportDatabase,verifyDatabase,buildIncrementalDump} from './db-transfer.mjs';

const apply=process.argv.includes('--apply');
const reuse=process.argv.includes('--reuse-existing');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const scratch=path.join(root,'wrangler.db-move.json');
const lockPath=path.join(root,'.db-move.lock');
let lock,client,creds,original,directory,target,activationAttempted=false,workerTouched=false;
async function checkpoint(phase){
  await writeFile(path.join(directory,'state.json'),JSON.stringify({phase,target,activationAttempted,updated_at:new Date().toISOString()},null,2)+'\n',{mode:0o600});
}
async function deploy(config){
  await writeFile(scratch,JSON.stringify(config,null,2)+'\n',{mode:0o600});
  await wrangler(creds,['deploy','--config',scratch]);
}
async function d1Execute(statement){
  const result=await cf(creds,'/d1/database/'+target.uuid+'/query','POST',typeof statement==='string'?{sql:statement}:{sql:statement.sql,params:statement.args||[]});
  if(result.some(x=>!x.success))throw new Error('Destination query failed');
  return {rows:result[0]?.results||[]};
}
async function probe(base,key,backend,maintenance,expectedImages){
  for(let attempt=0;attempt<12;attempt++){
    const headers={authorization:'Bearer '+key,...(maintenance?{'x-waku-maintenance-probe':key}:{})};
    try{
      const session=await fetch(base+'/v1/session',{headers,signal:AbortSignal.timeout(20000)});
      const data=await session.json();
      if(session.ok&&data.database===backend){
        const response=await fetch(base+'/v1/state',{headers,signal:AbortSignal.timeout(20000)});
        const state=await response.json();
        if(response.ok&&(expectedImages===undefined||state.images.total===expectedImages))return;
      }
    }catch{}
    await wait(2500);
  }
  throw new Error('Worker database probe did not pass');
}
try{
  lock=await open(lockPath,'wx',0o600);
  creds=await credentials();
  original=JSON.parse(await readFile(localConfig,'utf8'));
  const values=await secrets();
  if(original.vars?.DATABASE_BACKEND==='d1')throw new Error('Worker configuration already selects D1');
  if(!values.LIBSQL_URL||!values.LIBSQL_AUTH_TOKEN)throw new Error('Missing libSQL connection');
  client=createClient({url:values.LIBSQL_URL,authToken:values.LIBSQL_AUTH_TOKEN,intMode:'bigint',
    fetch:(u,i)=>fetch(u,{...i,signal:AbortSignal.timeout(30000)})});
  const schema=await client.execute('SELECT version FROM api_schema WHERE id=1');
  if(Number(schema.rows[0]?.version)<12)throw new Error('Deploy the current database migrations first');
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  directory=path.join(root,'.backups','return-d1-'+stamp);
  await mkdir(directory,{recursive:true,mode:0o700});
  await copyFile(localConfig,path.join(directory,'source-config.json'));
  // Preserve the key that encrypts provider credentials as well as both DB connections.
  await writeFile(path.join(directory,'secrets.json'),JSON.stringify(values,null,2)+'\n',{mode:0o600,flag:'wx'});
  const base=original.vars.PUBLIC_BASE_URL.replace(/\/$/,'');
  if(!apply){
    const snapshot=await exportDatabase(client,directory);
    console.log('Snapshot ready:',directory);
    console.log('Tables:',snapshot.manifest.tables.length,'Images:',snapshot.manifest.tables.find(t=>t.name==='images')?.rows||0);
    console.log('Plan only. VPS remains active; no D1 resource or Worker was changed.');
    console.log('To migrate with a fresh snapshot and a short maintenance window, add --apply.');
  }else{
    await probe(base,values.API_KEY,'libsql',false);
    const oldD1=original.d1_databases?.find(db=>db.binding==='DB');
    if(oldD1?.database_id)await cf(creds,'/d1/database/'+oldD1.database_id+'/query','POST',{sql:'SELECT name FROM sqlite_master LIMIT 1'});
    if(reuse){
      if(!oldD1?.database_id||!oldD1.database_name)throw new Error('Missing existing D1 destination');
      target={uuid:oldD1.database_id,name:oldD1.database_name};
      console.log('Backing up the inactive D1 database...');
      await wrangler(creds,['d1','export','DB','--remote','--output',path.join(directory,'previous-d1.sql'),'--config',localConfig],undefined,{quiet:true});
    }else target=await cf(creds,'/d1/database','POST',{name:'waku-api-restore-'+stamp.slice(0,19).toLowerCase()});
    // Test both read and write availability BEFORE putting the live API in maintenance.
    await d1Execute('CREATE TABLE _waku_transfer_probe(id INTEGER PRIMARY KEY)');
    await d1Execute('INSERT INTO _waku_transfer_probe VALUES (1)');
    await d1Execute('SELECT id FROM _waku_transfer_probe');
    await d1Execute('DROP TABLE _waku_transfer_probe');
    await checkpoint('target-ready');
    const held=structuredClone(original);held.vars.DATABASE_BACKEND='libsql';held.vars.MIGRATION_MAINTENANCE='1';
    workerTouched=true;await deploy(held);await checkpoint('source-maintenance');
    // Requests started before maintenance may still be completing in waitUntil.
    console.log('Waiting for active upload/AI leases to drain...');
    const deadline=Date.now()+180000;
    await wait(35000);
    while(true){
      const active=await client.execute({sql:`SELECT
        (SELECT COUNT(*) FROM worker_slots WHERE token IS NOT NULL AND lease_until>?) +
        (SELECT COUNT(*) FROM ai_provider_slots WHERE token IS NOT NULL AND lease_until>?) AS n`,args:[Date.now(),Date.now()]});
      if(Number(active.rows[0].n)===0)break;
      if(Date.now()>deadline)throw new Error('Active tasks did not drain; migration stopped');
      await wait(3000);
    }
    const snapshot=await exportDatabase(client,directory);await checkpoint('snapshot-ready');
    const staged=structuredClone(held);
    staged.d1_databases=[{binding:'DB',database_name:target.name,database_id:target.uuid,migrations_dir:'migrations'}];
    await writeFile(scratch,JSON.stringify(staged,null,2)+'\n',{mode:0o600});
    let dumpPath=snapshot.dumpPath;
    if(reuse){
      console.log('Comparing the source with the inactive D1 snapshot...');
      const delta=await buildIncrementalDump(client,d1Execute,snapshot.manifest,directory);
      dumpPath=delta.dumpPath;
      console.log('Changed rows:',JSON.stringify(delta.changes));
    }
    console.log('Importing into D1; source data is retained...');
    await wrangler(creds,['d1','execute','DB','--remote','--file',dumpPath,'--config',scratch],undefined,{quiet:true});
    await d1Execute('CREATE TABLE _waku_transfer_probe(id INTEGER PRIMARY KEY)');
    await d1Execute('INSERT INTO _waku_transfer_probe VALUES (1)');
    await d1Execute('DROP TABLE _waku_transfer_probe');
    const verified=await verifyDatabase(d1Execute,snapshot.manifest);await checkpoint('data-verified');
    console.log('Verified tables:',verified.tables,'Images:',verified.images);
    staged.vars.DATABASE_BACKEND='d1';staged.vars.D1_QUOTA_SCOPE=target.uuid;
    await deploy(staged);await checkpoint('target-maintenance');
    await probe(base,values.API_KEY,'d1',true,verified.images);
    const live=structuredClone(staged);delete live.vars.MIGRATION_MAINTENANCE;
    // From this point, never reopen the old source automatically: the final
    // deploy may have accepted new writes even if its CLI acknowledgement fails.
    activationAttempted=true;await checkpoint('activating');
    await writeFile(localConfig,JSON.stringify(live,null,2)+'\n',{mode:0o600});
    await deploy(live);
    await probe(base,values.API_KEY,'d1',false);
    await checkpoint('complete');
    console.log('D1 is active. The VPS database, R2 images and service keys are retained.');
    console.log('Backup and transfer record:',directory);
  }
}catch(error){
  if(workerTouched&&!activationAttempted&&original&&creds){
    try{console.log('Transfer stopped before activation; restoring the VPS backend...');await deploy(original);await checkpoint('restored-source');}
    catch{console.error('Could not confirm source recovery. Keep writes paused and inspect the private transfer record.');}
  }
  if(activationAttempted)console.error('Activation acknowledgement is incomplete. Local config selects the verified D1 target. Do not switch back to the old VPS snapshot; retry deployment of the current config.');
  console.error('Database transfer did not complete. No source database or R2 objects were deleted.');
  if(directory)console.error('Private transfer record:',directory);
  // Never print raw SQL/driver errors: they may contain private metadata or keys.
  console.error('Failure type:',error?.code||error?.name||'unknown');
  if(error?.message?.startsWith('Cloudflare API')||error?.message?.startsWith('Wrangler '))console.error(error.message);
  process.exitCode=1;
}finally{
  client?.close();
  if(lock){await lock.close();await unlink(lockPath).catch(()=>{});await unlink(scratch).catch(()=>{});}
}
