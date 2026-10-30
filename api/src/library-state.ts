import type {Env} from './types';

interface Counts{total:number;bytes:number;safe:number;r18:number;unknown:number;classified_safe:number;classified_r18:number;pending:number;}
export async function libraryState(env:Env){
  const row=await env.DB.prepare(`SELECT r.version revision,c.total,c.bytes,c.safe,c.r18,c.unknown,
    c.classified_safe,c.classified_r18,c.pending FROM library_counts c CROSS JOIN library_revision r
    WHERE c.id=1 AND r.id=1`).first<Counts&{revision:number}>();
  if(!row)throw new Error('Library counters are not initialized');
  const {revision,...counts}=row;
  return {revision,counts};
}
