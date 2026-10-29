import schema from '../migrations/0011_read_efficiency.sql';
import {migrationStatements} from './sql';
import type {Env} from './types';

const ready=new WeakMap<D1Database,Promise<void>>();
export async function ensureReadEfficiency(env:Env){
  let promise=ready.get(env.DB);
  if(!promise){
    promise=(async()=>{
      try{await env.DB.prepare('SELECT n FROM analysis_status_counts LIMIT 1').first();}
      catch(error){
        const message=String((error as Error)?.message)+' '+String((error as Error)?.cause);
        if(!message.includes('no such table: analysis_status_counts'))throw error;
        // Runs once after the daily limit resets if today's quota prevents a
        // CLI migration. One transaction installs indexes, counters and triggers.
        await env.DB.batch(migrationStatements(schema).map(sql=>env.DB.prepare(sql)));
      }
    })().catch(error=>{ready.delete(env.DB);throw error;});
    ready.set(env.DB,promise);
  }
  await promise;
}
