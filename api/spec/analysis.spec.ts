import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {parseAnalysis} from '../src/analysis.ts';

let mf:Miniflare, calls=0, fail=false, hold:Promise<void>|undefined, release:()=>void;
const sent:Array<{method:string;body:any}>=[];
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const predicted={title:'Ảnh anime',tags:['anime','blue_hair','cat_ears'],rating:'safe',rating_confidence:0.99};

before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({
    modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',
    d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{
      API_KEY:'key',TELEGRAM_WEBHOOK_SECRET:'secret',TELEGRAM_BOT_TOKEN:'123456789:test',OWNER_IDS:'101',
      PUBLIC_BASE_URL:'https://images.example.com',AI_BASE_URL:'https://vision.example.com/v1',AI_API_KEY:'vision-key',AI_MODEL:'vision-model',
    },
    outboundService:async(request:Request)=>{
      const url=new URL(request.url);
      if(url.hostname==='vision.example.com') {
        calls++;
        assert.equal(request.headers.get('authorization'),'Bearer vision-key');
        const body=await request.json() as any;
        assert.equal(body.model,'vision-model');
        assert.match(body.messages[1].content[1].image_url.url,/^https:\/\/images\.example\.com\/media\//);
        if(hold) await hold;
        if(fail) return new MockResponse('{}',{status:401});
        return MockResponse.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify(predicted)}}]});
      }
      assert.equal(url.hostname,'api.telegram.org');
      if(url.pathname.includes('/file/bot')) return new MockResponse(png);
      const method=url.pathname.split('/').at(-1)!, body=await request.json();sent.push({method,body});
      return MockResponse.json({ok:true,result:method==='getFile'?{file_path:'photos/test.png'}:{message_id:200+sent.length}});
    },
  }));
  const db=await mf.getD1Database('DB');
  for(const name of ['0001_initial.sql','0002_ai_tags.sql']) {
    const sql=await readFile('migrations/'+name,'utf8');await db.batch(sql.split(';').map(s=>s.trim()).filter(Boolean).map(s=>db.prepare(s)));
  }
});
after(async()=>{release?.();await mf?.dispose();});
async function api(path:string,method='GET',body?:any) {
  const req=new Request('https://images.example.com'+path,{method,headers:{authorization:'Bearer key',...(body && !(body instanceof FormData)?{'content-type':'application/json'}:{})},...(body?{body:body instanceof FormData?body:JSON.stringify(body)}:{})});
  const res=await mf.dispatchFetch(req.url,{method,headers:Object.fromEntries(req.headers),...(body?{body:Buffer.from(await req.arrayBuffer())}:{})});
  assert.ok(res.ok,await res.clone().text());return await res.json() as any;
}
async function upload(seed:number,fields:Record<string,string>={}) {
  const form=new FormData();form.set('file',new Blob([png,new Uint8Array([seed])]),'test.png');
  for(const [key,value] of Object.entries(fields)) form.set(key,value);
  return (await api('/v1/images','POST',form)).image;
}
async function finished(id:string) {
  for(let i=0;i<100;i++) {
    const data=(await api(`/v1/images/${id}`)).image;
    if(!['pending','running'].includes(data.ai_status)) return data;
    await new Promise(r=>setTimeout(r,20));
  }
  assert.fail('Analysis did not finish');
}
async function webhook(update:any) {await mf.dispatchFetch('https://images.example.com/telegram/webhook',{method:'POST',headers:{'x-telegram-bot-api-secret-token':'secret','content-type':'application/json'},body:JSON.stringify(update)});}
async function jobDone(id:string) {
  const db=await mf.getD1Database('DB');
  for(let i=0;i<100;i++) {
    const row=await db.prepare('SELECT * FROM telegram_jobs WHERE id=?').bind('tg:'+id).first();
    if(row?.status==='done') return row;
    await new Promise(r=>setTimeout(r,20));
  }
  assert.fail('Telegram job did not finish');
}

test('AI adds tags, preserves manual title/rating, duplicate is not analyzed again, manual bypass works',async()=>{
  const first=await upload(1,{title:'My title',tags:'custom',rating:'r18'});
  const result=await finished(first.id);
  assert.equal(result.ai_status,'done');assert.equal(result.title,'My title');assert.equal(result.rating,'r18');
  assert.deepEqual(result.tags,['anime','blue_hair','cat_ears','custom']);
  const before=calls;await upload(1);assert.equal(calls,before);
  const manual=await upload(2,{manual:'true',tags:'only_manual',rating:'safe'});
  assert.equal(manual.ai_status,'skipped');assert.deepEqual(manual.tags,['only_manual']);assert.equal(calls,before);
});

test('provider errors do not lose the image; conservative rating and invalid output validation',async()=>{
  fail=true;const file=await upload(3);const result=await finished(file.id);fail=false;
  assert.equal(result.ai_status,'failed');assert.equal(result.rating,'unknown');
  const media=await mf.dispatchFetch(result.url);assert.equal(media.status,200);await media.arrayBuffer();
  assert.equal(parseAnalysis(JSON.stringify({...predicted,rating_confidence:0.5})).rating,'unknown');
  assert.throws(()=>parseAnalysis('I cannot inspect this image.'));
  assert.throws(()=>parseAnalysis(JSON.stringify({...predicted,tags:['https://malicious.com']})));
});

test('manual edit during inference wins over late AI results',async()=>{
  hold=new Promise<void>(resolve=>{release=resolve;});
  const first=await upload(4);const before=calls;
  for(let i=0;i<50 && calls===before;i++) await new Promise(r=>setTimeout(r,10));
  await api(`/v1/images/${first.id}`,'PATCH',{tags:['hand_picked'],rating:'r18'});
  release();hold=undefined;
  const result=await finished(first.id);
  assert.equal(result.ai_status,'skipped');assert.deepEqual(result.tags,['hand_picked']);assert.equal(result.rating,'r18');
});

test('Telegram automatic analysis edits the original receipt; manual tag editing remains available',async()=>{
  const message={message_id:1,chat:{id:101,type:'private'},from:{id:101},photo:[{file_id:'photo'}]};
  await webhook({update_id:400,message});const job=await jobDone('400');
  const result=await finished(String(job.image_id));
  assert.equal(result.ai_status,'done');assert.ok(result.tags.includes('cat_ears'));
  const photo=sent.find(x=>x.method==='sendPhoto')!;assert.ok(photo);
  assert.ok(sent.some(x=>x.method==='editMessageCaption' && x.body.caption.includes('#cat_ears')));
  assert.equal(sent.filter(x=>x.method==='sendPhoto').length,1);
  const receipt={...message,message_id:201,caption:photo.body.caption.replace(/<[^>]*>/g,'')};
  await webhook({update_id:401,message:{message_id:2,chat:message.chat,from:message.from,text:'/tags #manual_cat',reply_to_message:receipt}});
  await jobDone('401');
  const changed=(await api(`/v1/images/${result.id}`)).image;
  assert.deepEqual(changed.tags,['manual_cat']);
});
