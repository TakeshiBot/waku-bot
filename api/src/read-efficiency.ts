import schema from '../migrations/0011_read_efficiency.sql';
import counters from '../migrations/0012_incremental_counts.sql';
import {migrationStatements} from './sql';
import type {Env} from './types';

const ready=new WeakMap<D1Database,Promise<void>>();
export async function ensureReadEfficiency(env:Env){
  let promise=ready.get(env.DB);
  if(!promise){
    promise=(async()=>{
      try{
        const row=await env.DB.prepare('SELECT version FROM api_schema WHERE id=1').first<{version:number}>();
        if(row&&row.version>=12)return;
      }
      catch(error){
        const message=String((error as Error)?.message)+' '+String((error as Error)?.cause);
        if(!message.includes('no such table: api_schema'))throw error;
      }
      // Install once atomically, including on a D1 deployment that was held by
      // quota. Production migrations are pre-applied before deploying the Worker.
      await env.DB.batch([...migrationStatements(schema),...migrationStatements(counters)].map(sql=>env.DB.prepare(sql)));
    })().catch(error=>{ready.delete(env.DB);throw error;});
    ready.set(env.DB,promise);
  }
  await promise;
}
