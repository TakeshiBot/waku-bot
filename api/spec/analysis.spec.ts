import {after,before,beforeEach,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare,convertV4MiniflareOptions,Response as MockResponse} from 'miniflare';
import {parseAnalysis,parseVisionResponse} from '../src/analysis.ts';

let mf:Miniflare, calls=0, fail=false, hold:Promise<void>|undefined, release:()=>void;
let burst=false,active=0,peak=0;
let blockedMode='';
let expectedKey='vision-key',expectedModel='vision-model',expectedPath='/v1/chat/completions';
const sent:Array<{method:string;body:any}>=[];
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const predicted={title:'Ảnh anime',tags:['anime','blue_hair','cat_ears'],rating:'safe',rating_confidence:0.99,image_type:'anime'};
let output:Record<string,unknown>=predicted;

test('vision distinguishes provider content filters from malformed data and accepts native Gemini envelopes',()=>{
  for(const response of [
    {response:{promptFeedback:{blockReason:'PROHIBITED_CONTENT',blockReasonMessage:'upstream text'}}},
    {choices:[{finish_reason:'content_filter',message:{content:''}}]},
    {choices:[{message:{refusal:'upstream text',content:null}}]},
    {response:{candidates:[{finishReason:'SAFETY'}]}},
  ])assert.throws(()=>parseVisionResponse(response),/provider_content_filtered/);
  const expected=parseAnalysis(JSON.stringify(predicted));
  assert.deepEqual(parseVisionResponse({response:{candidates:[{finishReason:'STOP',content:{parts:[{thought:true,text:'not output'},{text:JSON.stringify(predicted)}]}}]}}),expected);
  assert.deepEqual(parseVisionResponse({choices:[{finish_reason:'stop',message:{content:[{type:'text',text:JSON.stringify(predicted)}]}}]}),expected);
  assert.throws(()=>parseVisionResponse({choices:[{message:{content:'not JSON'}}]}),/invalid_response/);
});

before(async()=>{
  mf=new Miniflare(convertV4MiniflareOptions({
    modules:true,scriptPath:'.test/worker.mjs',compatibilityDate:'2026-07-01',
    d1Databases:['DB'],r2Buckets:['IMAGES'],bindings:{
      API_KEY:'key',TELEGRAM_WEBHOOK_SECRET:'secret',TELEGRAM_BOT_TOKEN:'123456789:test',OWNER_IDS:'101',
      PUBLIC_BASE_URL:'https://images.example.com',AI_BASE_URL:'https://vision.example.com/v1',AI_API_KEY:'vision-key',AI_MODEL:'vision-model',
    },
    outboundService:async(request:Request)=>{
      const url=new URL(request.url);
      if(url.hostname==='images.example.com' && url.pathname==='/internal/jobs') return mf.dispatchFetch(url.href,{method:'POST',headers:Object.fromEntries(request.headers),body:await request.text()});
      if(url.hostname==='vision.example.com') {
        calls++;
        active++;peak=Math.max(peak,active);
        try {
        assert.equal(request.headers.get('authorization'),'Bearer '+expectedKey);assert.equal(url.pathname,expectedPath);
        const body=await request.json() as any;
        assert.equal(body.model,expectedModel);
        assert.match(body.messages[1].content[1].image_url.url,/^data:image\/png;base64,/);
        if(hold) await hold;
        if(burst)await new Promise(resolve=>setTimeout(resolve,100));
        if(fail) return new MockResponse('{}',{status:401});
        if(blockedMode==='gemini')return MockResponse.json({response:{promptFeedback:{blockReason:'PROHIBITED_CONTENT'}}});
        if(blockedMode==='openai')return MockResponse.json({choices:[{finish_reason:'content_filter',message:{content:''}}]});
        assert.match(body.messages[0].content,/complete all four fields together/);
        return MockResponse.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify(output)}}]});
        } finally {active--;}
      }
      assert.equal(url.hostname,'api.telegram.org');
      if(url.pathname.includes('/file/bot')) return new MockResponse(png);
      const method=url.pathname.split('/').at(-1)!, body=await request.json();sent.push({method,body});
      return MockResponse.json({ok:true,result:method==='getFile'?{file_path:'photos/test.png'}:{message_id:200+sent.length}});
    },
  }));
  const db=await mf.getD1Database('DB');
  for(const name of ['0001_initial.sql','0002_ai_tags.sql','0003_worker_slots.sql','0004_analysis_batches.sql','0005_library_filters.sql','0006_ai_providers.sql','0007_parallel_ai.sql','0008_analysis_controls.sql','0009_catalog_keys.sql','0010_complete_analysis.sql']) {
    const sql=await readFile('migrations/'+name,'utf8');await db.batch(sql.split(';').map(s=>s.trim()).filter(Boolean).map(s=>db.prepare(s)));
  }
});
after(async()=>{release?.();await mf?.dispose();});
beforeEach(async()=>{output=predicted;await(await mf.getD1Database('DB')).prepare('UPDATE ai_providers SET cooldown_until=0,last_error=NULL').run();});
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
  const first=await upload(1,{title:'My title',tags:'custom',rating:'r18',image_type:'real'});
  const result=await finished(first.id);
  assert.equal(result.ai_status,'done');assert.equal(result.title,'My title');assert.equal(result.rating,'r18');
  assert.deepEqual(result.tags,['anime','blue_hair','cat_ears','custom']);
  assert.equal(result.image_type,'real','AI preserves a user-selected image type');
  const before=calls;await upload(1);assert.equal(calls,before);
  const manual=await upload(2,{manual:'true',tags:'only_manual',rating:'safe'});
  assert.equal(manual.ai_status,'skipped');assert.deepEqual(manual.tags,['only_manual']);assert.equal(calls,before);
});

test('provider errors do not lose the image; explicit model decisions are preserved and incomplete output is rejected',async()=>{
  fail=true;const file=await upload(3);const result=await finished(file.id);fail=false;
  assert.equal(result.ai_status,'failed');assert.equal(result.rating,'unknown');
  const media=await mf.dispatchFetch(result.url);assert.equal(media.status,200);await media.arrayBuffer();
  assert.equal(parseAnalysis(JSON.stringify({...predicted,rating_confidence:0.85})).rating,'safe');
  assert.equal(parseAnalysis(JSON.stringify({...predicted,rating:'r18',rating_confidence:0.65})).rating,'r18');
  const {rating_confidence,...complete}=predicted;assert.equal(parseAnalysis(JSON.stringify(complete)).rating,'safe');
  for(const field of ['title','tags','rating','image_type']){const incomplete={...complete};delete incomplete[field as keyof typeof complete];assert.throws(()=>parseAnalysis(JSON.stringify(incomplete)),/incomplete_response/);}
  assert.throws(()=>parseAnalysis(JSON.stringify({...complete,title:'  '})),/incomplete_response/);
  assert.throws(()=>parseAnalysis(JSON.stringify({...complete,rating:'unknown'})),/classification_unresolved/);
  assert.throws(()=>parseAnalysis(JSON.stringify({error:'classification_unresolved'})),/classification_unresolved/);
  assert.throws(()=>parseAnalysis('I cannot inspect this image.'));
  assert.throws(()=>parseAnalysis(JSON.stringify({...predicted,tags:['https://malicious.com']})));
});

test('unresolved or missing classification fails without saving partial tags, counts as failed and never retries automatically',async()=>{
  try{for(const [index,response]of [
    {...predicted,rating:'unknown'}, {...predicted,rating:null}, {error:'classification_unresolved'}, {...predicted,image_type:null},
  ].entries()){
    output=response;const before=calls,batch=await api('/v1/analysis-batches','POST',{});
    const file=await upload(220+index,{title:'Kept title',tags:'kept_tag',batch_id:batch.id}),result=await finished(file.id);
    assert.equal(result.ai_status,'failed');assert.equal(result.ai_error,index===0||index===2?'classification_unresolved':'incomplete_response');
    assert.equal(result.rating,'unknown');assert.equal(result.title,'Kept title');assert.deepEqual(result.tags,['kept_tag']);assert.equal(result.classified,false);
    const progress=await api('/v1/analysis-batches/'+batch.id);assert.equal(progress.failed,1);assert.equal(progress.done,0);
    await api('/v1/jobs/drain','POST',{});assert.equal(calls,before+1);
    if(index===0){
      output={...predicted,rating:'r18',rating_confidence:0.65};await api('/v1/images/'+file.id+'/analyze','POST',{});
      const retried=await finished(file.id);assert.equal(retried.ai_status,'done');assert.equal(retried.rating,'r18');assert.equal(retried.image_type,'anime');assert.equal(retried.classified,true);assert.ok(retried.tags.includes('kept_tag'));
    }
  }}finally{output=predicted;}
});

test('legacy incomplete successes become retryable failures without changing photos or metadata',async()=>{
  const pending=await upload(228,{manual:'true',title:'Old tagged image',tags:'old_tag',image_type:'anime'});
  const noTitle=await upload(229,{manual:'true',tags:'old_tag',rating:'safe',image_type:'anime'});
  const complete=await upload(230,{manual:'true',title:'Complete image',tags:'old_tag',rating:'r18',image_type:'real'});
  const manual=await upload(231,{manual:'true',tags:'manual_tag'}),db=await mf.getD1Database('DB'),batch=await api('/v1/analysis-batches','POST',{});
  for(const image of [pending,noTitle,complete]){
    await db.prepare("UPDATE image_analysis SET status='done' WHERE image_id=?").bind(image.id).run();
    await db.prepare("INSERT INTO analysis_batch_items(batch_id,image_id,status) VALUES (?,?,'done')").bind(batch.id,image.id).run();
  }
  const sql=await readFile('migrations/0010_complete_analysis.sql','utf8');await db.batch(sql.split(';').map(s=>s.trim()).filter(Boolean).map(s=>db.prepare(s)));
  const unresolved=(await api('/v1/images/'+pending.id)).image,missing=(await api('/v1/images/'+noTitle.id)).image;
  assert.equal(unresolved.ai_status,'failed');assert.equal(unresolved.ai_error,'classification_unresolved');
  assert.equal(unresolved.title,pending.title);assert.deepEqual(unresolved.tags,pending.tags);assert.equal(unresolved.rating,'unknown');assert.equal(unresolved.image_type,'anime');assert.equal(unresolved.sha256,pending.sha256);
  assert.equal(missing.ai_status,'failed');assert.equal(missing.ai_error,'incomplete_response');
  assert.equal((await api('/v1/images/'+complete.id)).image.ai_status,'done');assert.equal((await api('/v1/images/'+manual.id)).image.ai_status,'skipped');
  const progress=await api('/v1/analysis-batches/'+batch.id);assert.equal(progress.failed,2);assert.equal(progress.done,1);
});

test('blocked images expose a safe error code, retain the file and remain failed without retry',async()=>{
  try{for(const [index,mode]of ['gemini','openai'].entries()){
    blockedMode=mode;const before=calls,file=await upload(180+index),result=await finished(file.id);
    assert.equal(result.ai_status,'failed');assert.equal(result.ai_error,'provider_content_filtered');assert.equal(result.rating,'unknown');assert.deepEqual(result.tags,[]);
    await api('/v1/jobs/drain','POST',{});assert.equal(calls,before+1);
    const media=await mf.dispatchFetch(result.url);assert.equal(media.status,200);await media.arrayBuffer();
  }}finally{blockedMode='';}
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

test('bulk image analysis drains the queue with at most two concurrent provider requests',async()=>{
  peak=0;burst=true;const images=[];
  for(let i=50;i<56;i++)images.push(await upload(i));
  const results=await Promise.all(images.map(image=>finished(image.id)));
  burst=false;
  assert.ok(peak<=2);assert.equal(results.filter(image=>image.ai_status==='done').length,6);
});

test('analysis batch progress and pending view follow the actual queued/running/completed images',async()=>{
  const {id}=await api('/v1/analysis-batches','POST',{});
  hold=new Promise(resolve=>{release=resolve;});
  const images=[];for(let i=60;i<63;i++)images.push(await upload(i,{batch_id:id,rating:'safe',tags:'batch_progress'}));
  const progress=await api('/v1/analysis-batches/'+id);
  assert.equal(progress.total,3);assert.equal(progress.done,0);assert.equal(progress.pending+progress.running,3);
  const waiting=await api('/v1/images?view=pending&q=batch_progress');assert.equal(waiting.images.length,3);
  const ready=await api('/v1/images?view=classified&rating=safe&q=batch_progress');assert.equal(ready.images.length,0);
  release();hold=undefined;await Promise.all(images.map(image=>finished(image.id)));
  const done=await api('/v1/analysis-batches/'+id);assert.equal(done.total,3);assert.equal(done.done,3);assert.equal(done.pending+done.running+done.failed,0);
  assert.equal((await api('/v1/images?view=pending&q=batch_progress')).images.length,0);
  fail=true;
  const errorBatch=await api('/v1/analysis-batches','POST',{});const errorImage=await upload(63,{batch_id:errorBatch.id});await finished(errorImage.id);fail=false;
  const failed=await api('/v1/analysis-batches/'+errorBatch.id);assert.equal(failed.failed,1);assert.equal(failed.done,0);
});

test('bulk AI action creates jobs for manually uploaded images and reports progress',async()=>{
  const images=[];for(let i=70;i<73;i++)images.push(await upload(i,{manual:'true'}));
  const batch=await api('/v1/analysis-batches','POST',{});
  const accepted=await api('/v1/analysis-batches/'+batch.id,'POST',{ids:images.map(image=>image.id)});assert.equal(accepted.added,3);
  await Promise.all(images.map(image=>finished(image.id)));
  const result=await api('/v1/analysis-batches/'+batch.id);assert.equal(result.total,3);assert.equal(result.done,3);
});

test('saved API/model CRUD is encrypted, persists selection and drives actual inference without deploy',async()=>{
  const initial=await api('/v1/ai/settings');assert.equal(initial.providers.length,1);assert.equal(initial.enabled,true);
  const legacyId=initial.selected_provider,legacyModelId=initial.selected_model;
  await api('/v1/ai/selection','PUT',{provider_id:legacyId,enabled:false});
  assert.ok(!JSON.stringify(initial).includes('vision-key'));
  const create=await api('/v1/ai/providers','POST',{name:'Alternative API',base_url:'https://vision.example.com/v2',api_key:'alternative-key'});
  const provider=create.providers.find((p:any)=>p.name==='Alternative API');assert.ok(provider);
  const db=await mf.getD1Database('DB'),stored=await db.prepare('SELECT key_cipher FROM ai_providers WHERE id=?').bind(provider.id).first<{key_cipher:string}>();
  assert.ok(stored?.key_cipher);assert.ok(!stored.key_cipher.includes('alternative-key'));
  const first=await api('/v1/ai/providers/'+provider.id+'/models','POST',{model:'alternative-vision'});
  const model=first.providers.find((p:any)=>p.id===provider.id).models[0];
  const second=await api('/v1/ai/providers/'+provider.id+'/models','POST',{model:'second-vision'});
  const backup=second.providers.find((p:any)=>p.id===provider.id).models[1];
  expectedKey='alternative-key';expectedModel='alternative-vision';expectedPath='/v2/chat/completions';
  await api('/v1/ai/selection','PUT',{provider_id:provider.id,model_id:model.id,enabled:true});
  assert.equal((await finished((await upload(240)).id)).ai_status,'done');
  await api('/v1/ai/providers/'+provider.id,'PATCH',{name:'Renamed API',base_url:'https://vision.example.com/v3',api_key:'rotated-key'});
  await api('/v1/ai/providers/'+provider.id,'PATCH',{name:'Renamed again'});
  expectedKey='rotated-key';expectedModel='renamed-vision';expectedPath='/v3/chat/completions';
  await api('/v1/ai/models/'+model.id,'PATCH',{model:'renamed-vision'});
  assert.equal((await finished((await upload(241)).id)).ai_status,'done');
  const saved=await api('/v1/ai/settings');assert.equal(saved.selected_model,model.id);assert.equal(saved.providers.find((p:any)=>p.id===provider.id).name,'Renamed again');
  assert.ok(!JSON.stringify(saved).includes('rotated-key'));
  expectedModel='second-vision';const removed=await api('/v1/ai/models/'+model.id,'DELETE');assert.equal(removed.selected_model,backup.id);
  assert.equal((await finished((await upload(242)).id)).ai_status,'done');
  const deleted=await api('/v1/ai/providers/'+provider.id,'DELETE');assert.equal(deleted.enabled,false);assert.equal((await api('/v1/session')).ai_enabled,false,'deleting selected API must not silently fall back to environment');
  expectedKey='vision-key';expectedModel='vision-model';expectedPath='/v1/chat/completions';
  const restored=await api('/v1/ai/selection','PUT',{provider_id:legacyId,model_id:legacyModelId,enabled:true});assert.equal(restored.enabled,true);
});

test('bulk manual classification wins over AI inference already in flight',async()=>{
  hold=new Promise(resolve=>release=resolve);
  try{
    const image=await upload(250),updated=await api('/v1/images/bulk','PATCH',{ids:[image.id],rating:'r18',image_type:'real'});assert.equal(updated.updated,1);
    release();hold=undefined;const result=await finished(image.id);assert.equal(result.rating,'r18');assert.equal(result.image_type,'real');
  }finally{release();hold=undefined;}
});
