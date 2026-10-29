// These are our own migration files, with no SQL supplied by clients. Preserve
// trigger bodies when separating statements for D1's transactional batch API.
export function migrationStatements(sql:string):string[]{
  const result:string[]=[];let trigger='';
  for(const part of sql.replace(/--[^\n]*/g,'').split(';')){
    const text=part.trim();if(!text)continue;
    if(trigger||/^CREATE TRIGGER\b/i.test(text)){
      trigger+=(trigger?'\n':'')+text+';';
      if(/^END$/i.test(text)){result.push(trigger);trigger='';}
    }else result.push(text);
  }
  if(trigger)throw new Error('Incomplete migration trigger');return result;
}
