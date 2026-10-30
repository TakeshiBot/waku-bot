import {mkdir,open,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

export const quote=name=>'"'+name.replaceAll('"','""')+'"';
const argsValue=v=>{
  if(typeof v==='bigint'){const n=Number(v);if(!Number.isSafeInteger(n))throw new Error('Primary key exceeds safe D1 integer range');return n;}
  return v;
};
async function page(execute,table,last){
  const keys=table.keys,where=last?' WHERE ('+keys.map(quote).join(',')+')>('+keys.map(()=>'?').join(',')+')':'';
  // Serialize cells in SQLite so binary data, embedded NUL and 64-bit integers
  // survive drivers whose JS value conversion would otherwise lose precision.
  const values=table.columns.map((name,n)=>{const c=quote(name);return `CASE WHEN typeof(${c})='text' AND instr(${c},char(0))>0 THEN 'CAST(X'''||hex(${c})||''' AS TEXT)' ELSE quote(${c}) END AS __value${n}`;});
  const sql='SELECT '+[...values,...keys.map((k,n)=>quote(k)+' AS __key'+n)].join(',')+' FROM '+quote(table.name)+where+' ORDER BY '+keys.map(quote).join(',')+' LIMIT 500';
  return (await execute({sql,args:last?last.map(argsValue):[]})).rows;
}
function rowText(table,row){return table.columns.map((c,n)=>row['__value'+n]).join(',');}
export async function exportDatabase(client,directory,{onStatement}={}){
  await mkdir(directory,{recursive:true,mode:0o700});
  const dumpPath=path.join(directory,'database.sql'),handle=await open(dumpPath,'wx',0o600);
  let tx,complete=false;
  const emit=async sql=>{
    const statement=sql.replace(/;\s*$/,'');
    if(Buffer.byteLength(statement)>100000)throw new Error('A database row exceeds the D1 SQL statement limit');
    await handle.writeFile(statement+';\n');if(onStatement)onStatement(statement);
  };
  try{
    tx=await client.transaction('read');
    const execute=stmt=>tx.execute(stmt);
    if((await execute('PRAGMA foreign_key_check')).rows.length)throw new Error('Source database has foreign key errors');
    const schema=(await execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','index','trigger') ORDER BY type,name")).rows;
    const definitions=schema.filter(r=>r.type==='table'&&!/^(sqlite_|_cf_|libsql_|__)/i.test(r.name));
    const names=new Set(definitions.map(r=>r.name)),tables=[];
    for(const r of definitions){
      const info=(await execute('PRAGMA table_info('+quote(r.name)+')')).rows;
      const keys=info.filter(c=>Number(c.pk)>0).sort((a,b)=>Number(a.pk)-Number(b.pk)).map(c=>c.name);
      if(!keys.length)throw new Error('Database table without a primary key cannot be transferred safely');
      tables.push({name:r.name,columns:info.map(c=>c.name),keys,rows:0,sha256:'',sql:r.sql,
        dependencies:(await execute('PRAGMA foreign_key_list('+quote(r.name)+')')).rows.map(f=>f.table).filter(n=>names.has(n))});
    }
    if(!names.has('images')||!names.has('api_schema'))throw new Error('Source is not an initialized Waku database');
    const ordered=[],remaining=[...tables];
    while(remaining.length){
      const i=remaining.findIndex(t=>t.dependencies.every(d=>ordered.some(x=>x.name===d)||d===t.name));
      if(i<0)throw new Error('Cyclic database dependencies require a manual transfer');
      ordered.push(remaining.splice(i,1)[0]);
    }
    await emit('PRAGMA defer_foreign_keys=ON');
    for(const t of ordered)await emit(t.sql);
    for(const t of ordered){
      const hash=createHash('sha256');let last;
      while(true){
        const rows=await page(execute,t,last);if(!rows.length)break;
        for(const row of rows){const values=rowText(t,row);hash.update(values+'\n');await emit('INSERT INTO '+quote(t.name)+'('+t.columns.map(quote).join(',')+') VALUES ('+values+')');t.rows++;}
        last=t.keys.map((k,n)=>rows.at(-1)['__key'+n]);
      }
      t.sha256=hash.digest('hex');
    }
    // Install indexes and triggers after the data, avoiding duplicate counters
    // and unnecessary trigger writes while importing a snapshot.
    for(const r of schema.filter(r=>r.type!=='table'&&names.has(r.tbl_name)))await emit(r.sql);
    await tx.commit();complete=true;
    const manifest={format:1,created_at:new Date().toISOString(),tables:ordered.map(({dependencies,sql,...t})=>t)};
    await writeFile(path.join(directory,'manifest.json'),JSON.stringify(manifest,null,2)+'\n',{mode:0o600,flag:'wx'});
    return {dumpPath,manifest};
  }finally{if(tx&&!complete)try{await tx.rollback();}catch{}tx?.close();await handle.close();}
}
export async function verifyDatabase(execute,manifest){
  if(manifest.format!==1)throw new Error('Unknown database snapshot format');
  const mismatches=[];
  for(const table of manifest.tables){
    const hash=createHash('sha256');let last,count=0;
    while(true){
      const rows=await page(execute,table,last);if(!rows.length)break;
      for(const row of rows){hash.update(rowText(table,row)+'\n');count++;}
      last=table.keys.map((k,n)=>rows.at(-1)['__key'+n]);
    }
    if(count!==table.rows||hash.digest('hex')!==table.sha256)mismatches.push(table.name);
  }
  if(mismatches.length)throw new Error('Database verification failed for '+mismatches.length+' tables');
  if((await execute('PRAGMA foreign_key_check')).rows.length)throw new Error('Destination has foreign key errors');
  return {tables:manifest.tables.length,images:manifest.tables.find(t=>t.name==='images')?.rows||0};
}
