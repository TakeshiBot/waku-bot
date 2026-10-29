import {HttpError,type Env} from './types';

const MARKER='_system/d1-daily-limit.json',states=new WeakMap<D1Database,{until:number;checked:number}>();
export function isD1DailyLimit(error:unknown){
  let current=error,text='';
  for(let n=0;n<4&&current;n++){text+=' '+String((current as Error).message||current);current=(current as Error).cause;}
  return /\bD1\b|D1_ERROR/i.test(text)&&/(?:daily|(?:per|single|one|each|a)[ -]day|24[ -]hours|free.?tier.{0,50}quota|maximum number of rows)/i.test(text)&&/(?:exceed|limit|quota)/i.test(text);
}
function resetAt(){return (Math.floor(Date.now()/86400000)+1)*86400000;}
export function quotaError(){
  return new HttpError(503,'d1_daily_limit','D1 đã hết hạn mức trong ngày. Tạm dừng truy cập dữ liệu đến kỳ reset lúc 07:00 (giờ Việt Nam). Ảnh đã lưu vẫn được giữ nguyên.',Math.max(1,Math.ceil((resetAt()-Date.now())/1000)));
}
export async function assertD1Available(env:Env){
  let state=states.get(env.DB);
  if(!state||Date.now()-state.checked>60000){
    try{
      const saved=await env.IMAGES?.get(MARKER),record=saved?await saved.json<{until:number}>():null;
      state={until:Number(record?.until)||0,checked:Date.now()};states.set(env.DB,state);
    }catch{state={until:state?.until||0,checked:Date.now()};states.set(env.DB,state);}
  }
  if(state.until>Date.now())throw quotaError();
}
export async function recordD1Limit(env:Env,error:unknown){
  if(!isD1DailyLimit(error))return false;
  const state=states.get(env.DB);if(state&&state.until>Date.now())return true;
  // Brief server-side probes allow recovery after a plan change too. Browsers
  // stop automatic polling/upload until the daily reset, rather than retrying.
  const until=Math.min(resetAt(),Date.now()+15*60000);states.set(env.DB,{until,checked:Date.now()});
  try{await env.IMAGES?.put(MARKER,JSON.stringify({until}),{httpMetadata:{contentType:'application/json'}});}catch{}
  console.warn('D1 daily quota reached; database work suspended');return true;
}
export async function protectD1(env:Env,work:()=>Promise<unknown>){
  try{await assertD1Available(env);await work();}
  catch(error){if(!(error instanceof HttpError&&error.code==='d1_daily_limit')&&!await recordD1Limit(env,error))console.warn('Background work interrupted; durable jobs retained');}
}
