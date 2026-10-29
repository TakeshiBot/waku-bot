import {readFile} from 'node:fs/promises';
import {createClient} from '@libsql/client/web';

// Imports an already verified D1 export or SQLite iterdump into an EMPTY server.
// Keep URL and token in the environment, never in shell history or output.
const file=process.argv[2],url=process.env.LIBSQL_URL,authToken=process.env.LIBSQL_AUTH_TOKEN;
if(!file||!url||!authToken)throw new Error('Usage: set LIBSQL_URL and LIBSQL_AUTH_TOKEN, then run node scripts/import-libsql.mjs <dump.sql>');
const sql=(await readFile(file,'utf8'))
  .replace(/^\s*BEGIN(?:\s+TRANSACTION)?\s*;/i,'')
  .replace(/COMMIT\s*;\s*$/i,'');
const client=createClient({url,authToken});
try{
  const tables=await client.execute("SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'");
  if(Number(tables.rows[0].n)!==0)throw new Error('Refusing to import into a non-empty database.');
  await client.executeMultiple('BEGIN IMMEDIATE; PRAGMA defer_foreign_keys=ON;\n'+sql+'\nCOMMIT;');
  const invalid=await client.execute('PRAGMA foreign_key_check');
  if(invalid.rows.length)throw new Error('Import completed but foreign key validation failed. Do not switch traffic.');
  const count=await client.execute('SELECT COUNT(*) n FROM images');
  console.log('Import complete; images:',count.rows[0].n,'foreign key errors:',invalid.rows.length);
}finally{client.close();}
