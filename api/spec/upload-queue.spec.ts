import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Exercise the real browser queue with a deterministic transport; no network or
// DOM is needed to test sequencing and backpressure.
async function runQueue(behavior:(id:string,attempt:number)=>string){
  const script=await readFile('public/assets/app.js','utf8');
  const code=script.slice(script.indexOf('async function worker(){'),script.indexOf("$('start-upload').addEventListener('click',start)"));
  assert.ok(code.includes('Promise.all([worker(),worker()])'));
  const run=new Function('behavior',`return (async()=>{
    const calls=[],attempts={};let now=0;
    const Date={now:()=>now};
    const state={authenticated:true,starting:false,checking:0,paused:true,pumping:false,
      queue:['a','b','c'].map(id=>({id,name:id,file:{name:id},status:'queued'})),
      uploadIds:new Set(),uploadResults:new Map(),images:new Map(),rating:'',filterTags:new Set(),filterTypes:new Set()};
    const el={value:'',checked:false,textContent:'',checkValidity:()=>true},$=()=>el,document={querySelectorAll:()=>[]};
    const options=()=>({tags:[],manual:true}),renderQueue=()=>{},renderGallery=()=>{},renderAiProgress=()=>{},schedulePoll=()=>{},refreshStats=()=>{},changeFilter=()=>{},setView=()=>{},setAiBatch=()=>{},toast=()=>{},isClassified=()=>false,sleep=async(ms)=>{now+=ms;};
    async function fileUpload(item){
      calls.push(item.id);attempts[item.id]=(attempts[item.id]||0)+1;
      const outcome=behavior(item.id,attempts[item.id]);
      if(outcome==='busy'){const e=Error('busy');e.status=429;e.code='busy';e.retryAfter=2;throw e;}
      if(outcome==='outage'){const e=Error('unavailable');e.status=503;e.retryAfter=30;throw e;}
      if(outcome==='fail')throw Error('upload failed');
      return {duplicate:false,image:{id:item.id,rating:'safe'}};
    }
    ${code}
    await start();return {calls,queue:state.queue,results:state.uploadResults,paused:state.paused,now};
  })();`);
  return run(behavior) as Promise<{calls:string[];queue:{id:string;status:string}[];results:Map<string,string>;paused:boolean;now:number}>;
}

test('busy slots wait without marking an image failed or consuming its retry',async()=>{
  const result=await runQueue((id,n)=>id==='a'&&n<=3?'busy':id==='b'&&n===1?'fail':'ok');
  assert.equal(result.calls.filter(id=>id==='a').length,4);
  assert.equal(result.calls.filter(id=>id==='b').length,2);
  assert.equal(result.calls.filter(id=>id==='c').length,1);
  assert.ok(result.calls.lastIndexOf('b')>result.calls.indexOf('c'));
  assert.equal(result.queue.length,0);
});

test('a real upload error is retried once only after the remaining images',async()=>{
  const result=await runQueue(id=>id==='b'?'fail':'ok');
  assert.equal(result.calls.filter(id=>id==='b').length,2);
  assert.ok(result.calls.lastIndexOf('b')>result.calls.indexOf('c'));
  assert.deepEqual(result.queue.map(item=>[item.id,item.status]),[['b','error']]);
});

test('a temporary outage waits and retries without failing the batch',async()=>{
  let failures=0;
  const result=await runQueue(()=>failures++<3?'outage':'ok');
  assert.equal(result.queue.length,0);
  assert.equal(result.results.size,3);
  assert.ok(result.now>=30000);
});

test('a persistent outage pauses and keeps files instead of marking them failed',async()=>{
  const result=await runQueue(()=>'outage');
  assert.equal(result.paused,true);
  assert.ok(result.queue.length>0);
  assert.ok(result.queue.every(item=>item.status==='queued'));
});
