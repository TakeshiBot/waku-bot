import {boundedBody} from './input';
import {HttpError,type Env} from './types';
import type {AiConnection} from './providers';
import {releaseAiSlot} from './queue';

const palette=[['red',255,0,0],['green',0,180,0],['blue',0,0,255],['yellow',255,255,0],['purple',160,0,200],['orange',255,140,0]] as const;
const positions=['top_left','top_right','bottom_left','bottom_right'] as const;
class ProbeError extends Error{constructor(public code:string){super(code);}}
function chunk(name:string,data:Uint8Array){
  const bytes=new Uint8Array(data.length+12),view=new DataView(bytes.buffer);view.setUint32(0,data.length);
  bytes.set(new TextEncoder().encode(name),4);bytes.set(data,8);let crc=0xffffffff;
  for(const byte of bytes.subarray(4,8+data.length)){crc^=byte;for(let i=0;i<8;i++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
  view.setUint32(8+data.length,(crc^0xffffffff)>>>0);return bytes;
}
// Random colors exist only in the image, never in the question. A text-only
// response or an API that silently drops image_url cannot pass by echoing text.
export async function visionChallenge(){
  const colors=[...palette];for(let i=colors.length-1;i>0;i--){const j=crypto.getRandomValues(new Uint32Array(1))[0]%(i+1);[colors[i],colors[j]]=[colors[j],colors[i]];}
  const width=160,stride=width*3+1,raw=new Uint8Array(stride*width);raw.fill(255);
  for(let y=0;y<width;y++){
    raw[y*stride]=0;
    for(let x=0;x<width;x++)if(x%80>=8&&x%80<72&&y%80>=8&&y%80<72){
      const color=colors[(y>=80?2:0)+(x>=80?1:0)],at=y*stride+1+x*3;raw.set(color.slice(1) as number[],at);
    }
  }
  const header=new Uint8Array(13),view=new DataView(header.buffer);view.setUint32(0,width);view.setUint32(4,width);header[8]=8;header[9]=2;
  const compressed=new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('deflate'))).arrayBuffer());
  const chunks=[new Uint8Array([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',compressed),chunk('IEND',new Uint8Array())];
  const bytes=new Uint8Array(chunks.reduce((n,c)=>n+c.length,0));let offset=0;for(const part of chunks){bytes.set(part,offset);offset+=part.length;}
  let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));
  return {url:'data:image/png;base64,'+btoa(binary),expected:Object.fromEntries(positions.map((name,i)=>[name,colors[i][0]]))};
}
export function verifyVisionTest(value:any,expected:Record<string,string>){
  const data=value?.response||value,choice=data?.choices?.[0],candidate=data?.candidates?.[0];
  if(choice?.message?.refusal||['content_filter','safety'].includes(String(choice?.finish_reason).toLowerCase())||data?.promptFeedback?.blockReason||['SAFETY','PROHIBITED_CONTENT'].includes(candidate?.finishReason))throw new ProbeError('content_filtered');
  let content=choice?.message?.content;
  if(Array.isArray(content))content=content.filter(p=>p?.type==='text').map(p=>p.text).join('');
  if(content===undefined&&Array.isArray(candidate?.content?.parts))content=candidate.content.parts.filter((p:any)=>!p.thought&&typeof p.text==='string').map((p:any)=>p.text).join('');
  let answer;try{answer=JSON.parse(content.trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,''));}catch{throw new ProbeError('invalid_response');}
  return positions.every(name=>typeof answer?.[name]==='string'&&answer[name].trim().toLowerCase()===expected[name]);
}
export async function testVision(env:Env,config:AiConnection){
  const now=Date.now(),token=crypto.randomUUID().replaceAll('-','');
  // Share the real provider's two slots; testing does not overbook an API that
  // is already tagging images. Disabled/unselected models can still be tested.
  const slot=await env.DB.prepare(`UPDATE ai_provider_slots SET token=?,image_id=NULL,lease_until=?,last_started=?
    WHERE provider_id=? AND slot=(SELECT slot FROM ai_provider_slots WHERE provider_id=? AND lease_until<? ORDER BY slot LIMIT 1) RETURNING token`)
    .bind(token,now+30000,now,config.provider_id,config.provider_id,now).first();
  if(!slot)throw new HttpError(429,'vision_test_busy','API đang dùng hết lượt xử lý. Chờ ảnh đang chạy hoàn tất rồi thử Test lại.',3);
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  const run=async()=>{
    const challenge=await visionChallenge(),endpoint=new URL(config.base_url.replace(/\/$/,'')+'/chat/completions');
    if(endpoint.protocol!=='https:'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash)throw new ProbeError('invalid_provider');
    const response=await fetch(endpoint,{method:'POST',redirect:'manual',signal:controller.signal,
      headers:{authorization:'Bearer '+config.api_key,'content-type':'application/json'},
      body:JSON.stringify({model:config.model,stream:false,max_tokens:160,messages:[
        {role:'system',content:'Inspect the supplied image. Return only a JSON object with top_left, top_right, bottom_left, bottom_right. Each value is the color of that square: red, green, blue, yellow, purple or orange. Ignore the white background. If you cannot see the image, return {"error":"cannot_see_image"}; do not guess.'},
        {role:'user',content:[{type:'text',text:'What color is each of the four squares in this image?'},{type:'image_url',image_url:{url:challenge.url,detail:'auto'}}]},
      ]})});
    if(!response.ok){await response.body?.cancel();throw new ProbeError('http_'+response.status);}
    const body=await boundedBody(response,65536);let value;try{value=JSON.parse(new TextDecoder().decode(body));}catch{throw new ProbeError('invalid_response');}
    return verifyVisionTest(value,challenge.expected);
  };
  try{
    const ok=await Promise.race([run(),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new ProbeError('timeout'));},10000);})]);
    return {ok,code:ok?'vision_verified':'image_mismatch',message:ok?'Đã đọc đúng ảnh mẫu. Vision hoạt động trong bài thử.':'Model trả lời sai hoặc không đọc được ảnh mẫu. Chưa xác nhận được vision.',elapsed_ms:Date.now()-now};
  }catch(error){
    const code=error instanceof ProbeError?error.code:controller.signal.aborted?'timeout':'provider_unavailable';
    const message=code==='timeout'?'API không trả xong trong 10 giây. Chưa xác nhận được vision.':/^http_\d{3}$/.test(code)?`API trả HTTP ${code.slice(-3)}. Kiểm tra key, model hoặc giới hạn dịch vụ.`:code==='content_filtered'?'API từ chối ảnh kiểm thử. Chưa xác nhận được vision.':code==='invalid_response'?'API trả kết quả không đúng định dạng kiểm thử.':'Không kết nối hoặc đọc được kết quả API. Chưa xác nhận được vision.';
    return {ok:false,code,message,elapsed_ms:Date.now()-now};
  }finally{
    if(timer)clearTimeout(timer);controller.abort();
    try{await releaseAiSlot(env,token);}catch{console.warn('Vision test slot release deferred to lease expiry');}
  }
}
