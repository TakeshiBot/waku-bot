import {createClient, type Client, type InValue, type ResultSet} from '@libsql/client/web';
import {HttpError, type Env} from './types';

function databaseError(error:unknown):never {
  const e=error as {code?:string;name?:string;message?:string};
  if(e?.code==='FETCH_ERROR'||e?.name==='AbortError'||e?.name==='TimeoutError'||
    (e?.code==='SERVER_ERROR' && /HTTP status (?:429|5\d\d)\b/.test(e.message||'')) ||
    (e?.name==='TypeError' && /fetch|network/i.test(e.message||''))) {
    throw new HttpError(503,'database_unavailable','Không kết nối được cơ sở dữ liệu kho ảnh. Các file chưa tải vẫn được giữ; hãy thử lại khi kết nối phục hồi.',30);
  }
  throw error;
}

// Preserve D1's atomic batch semantics; queue leases and image updates depend on
// the entire batch committing or rolling back together.
class Statement {
  constructor(readonly client:Client, readonly sql:string, readonly args:InValue[]=[]){ }
  bind(...args:unknown[]){return new Statement(this.client,this.sql,args as InValue[]);}
  private async execute(){try{return await this.client.execute({sql:this.sql,args:this.args});}catch(error){return databaseError(error);}}
  async first<T>(column?:string):Promise<T|null>{
    const row=(await this.execute()).rows[0];
    if(!row)return null;
    return (column?row[column]:row) as T;
  }
  async all<T>(){return result<T>(await this.execute());}
  async run<T>(){return result<T>(await this.execute());}
}
function result<T>(value:ResultSet){
  return {success:true,results:value.rows as T[],meta:{changes:value.rowsAffected,last_row_id:Number(value.lastInsertRowid||0)}};
}
export class LibsqlDatabase {
  constructor(readonly client:Client){}
  prepare(sql:string){return new Statement(this.client,sql);}
  async batch<T>(statements:Statement[]){
    if(!statements.length)return [];
    if(statements.some(s=>s.client!==this.client))throw new Error('Database batch contains foreign statement');
    try{return (await this.client.batch(statements.map(s=>({sql:s.sql,args:s.args})), 'write')).map(r=>result<T>(r));}
    catch(error){return databaseError(error);}
  }
}
export function withDatabase(env:Env):Env{
  if(!env.LIBSQL_URL)return env;
  if(!env.LIBSQL_AUTH_TOKEN)throw new Error('LibSQL authentication is not configured');
  const client=createClient({url:env.LIBSQL_URL,authToken:env.LIBSQL_AUTH_TOKEN,
    fetch:(input:RequestInfo|URL,init?:RequestInit)=>fetch(input,{...init,signal:init?.signal?AbortSignal.any([init.signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000)})});
  return {...env,DB:new LibsqlDatabase(client) as unknown as D1Database};
}
