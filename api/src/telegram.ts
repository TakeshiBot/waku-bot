import { boundedBody, captionMetadata, equalSecret, downloadImage, owner,type ImageDownload } from './input';
import { cleanupObjects, image, maxBytes, present, remove, save, update as updateMetadata,recordImportAliases } from './store';
import {aiEnabled,enqueueAnalysis,processAnalysis,type AnalysisJob} from './analysis';
import { HttpError, type Env, type TelegramJob } from './types';
import {acquireSlot,releaseSlot,continueWork} from './queue';
import {stopPendingAnalysis} from './batches';
import {protectD1} from './d1-quota';
import {ensureReadEfficiency} from './read-efficiency';

interface TgFile {file_id:string; file_size?:number; file_name?:string;}
interface TgMessage {message_id:number; chat:{id:number;type:string}; from?:{id:number}; text?:string; caption?:string; photo?:TgFile[]; document?:TgFile & {mime_type?:string};reply_to_message?:TgMessage;}
interface Update {update_id:number; message?:TgMessage; callback_query?:{id:string;from:{id:number};data?:string;message?:TgMessage};}
class TelegramError extends Error {
  constructor(public status:number, public retryAfter=0) {super(`Telegram HTTP ${status}`);}
}

async function tg<T>(env:Env, method:string, payload:unknown):Promise<T> {
  if (['sendPhoto','sendMessage'].includes(method)) {
    const chat=(payload as {chat_id?:number}).chat_id;
    if (chat) {
      const now=Date.now();
      const reserved=await env.DB.prepare(`INSERT INTO telegram_send_times(chat_id,next_send) VALUES (?,?)
        ON CONFLICT(chat_id) DO UPDATE SET next_send=MAX(next_send,?)+1100 RETURNING next_send`)
        .bind(chat,now+1100,now).first<{next_send:number}>();
      const delay=Math.max(0,(reserved?.next_send || now)-1100-now);
      if (delay>10000) throw new TelegramError(429,Math.ceil(delay/1000));
      if (delay) await new Promise(resolve=>setTimeout(resolve,delay));
    }
  }
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload), signal:AbortSignal.timeout(15000),
  });
  const result = await response.json<{ok:boolean;result:T;error_code?:number;parameters?:{retry_after?:number}}>();
  if (!response.ok || !result.ok) throw new TelegramError(result.error_code ?? response.status,result.parameters?.retry_after ?? 0);
  return result.result;
}

function escape(value:string) {return value.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function baseUrl(env:Env, fallback:string) {return (env.PUBLIC_BASE_URL || fallback).replace(/\/$/,'');}
const HELP = 'Gửi ảnh hoặc file ảnh gốc, hoặc dán link ảnh trực tiếp.\n'
  + 'Caption: tên ảnh #tag1 #tag2 #safe (hoặc #r18), có thể kèm link nguồn.\n'
  + 'Khi có API AI, bot tự xem ảnh để bổ sung tag và phân loại. Tag, tên và phân loại bạn nhập được ưu tiên.\n'
  + 'Thêm #manual để chỉ nhập tay. Bấm Sửa tag, hoặc reply ảnh đã lưu bằng /tags #tag1 #tag2 để thay tag.\n'
  + 'Ảnh chưa phân loại chưa xuất hiện trong API random; bạn vẫn có thể bấm An toàn/R18.\n'
  + 'Mỗi ảnh tối đa 10 MB mặc định. Gửi dạng file để giữ chất lượng gốc.\n'
  + '/stats — thống kê kho; /help — hướng dẫn. Chỉ tài khoản được cấp quyền dùng được bot.';

export async function webhook(request:Request, env:Env, ctx:ExecutionContext) {
  if (!equalSecret(request.headers.get('x-telegram-bot-api-secret-token') || '',env.TELEGRAM_WEBHOOK_SECRET)) return new Response('Forbidden',{status:403});
  const body = await boundedBody(request,1024*1024);
  let update:Update;
  try {update = JSON.parse(new TextDecoder().decode(body));} catch {throw new HttpError(400,'invalid_json','JSON không hợp lệ.');}
  if (!Number.isSafeInteger(update.update_id)) throw new HttpError(400,'invalid_update','Update không hợp lệ.');
  const actor = update.callback_query?.from ?? update.message?.from;
  const message = update.callback_query?.message ?? update.message;
  if (!actor || !owner(actor.id,env.OWNER_IDS) || !message || message.chat.type !== 'private' || message.chat.id !== actor.id) return Response.json({ok:true});
  const id = `tg:${update.update_id}`;
  await env.DB.prepare('INSERT OR IGNORE INTO telegram_jobs(id,payload,created_at) VALUES (?,?,?)')
    .bind(id,JSON.stringify({update,base:baseUrl(env,new URL(request.url).origin)}),Date.now()).run();
  // D1 is the durable outbox. Cron retries jobs if this background execution
  // is interrupted; never ACK a webhook before its job has been persisted.
  ctx.waitUntil(protectD1(env,async()=>{await ensureReadEfficiency(env);await processJobs(env,1,id);}));
  return Response.json({ok:true});
}

function controls(id:string, url:string, rating='unknown') {
  return {inline_keyboard:[
    [{text:'Xem ảnh',url}],
    [{text:`${rating==='safe'?'✅ ':''}An toàn`,callback_data:`rating:safe:${id}`},{text:`${rating==='r18'?'✅ ':''}R18`,callback_data:`rating:r18:${id}`}],
    [{text:'✨ AI gắn tag',callback_data:`analyze:${id}`},{text:'✏️ Sửa tag',callback_data:`tags:${id}`}],
    [{text:'Xóa ảnh',callback_data:`delete:${id}`}],
  ]};
}

async function receiptText(env:Env,id:string,base:string,duplicate=false) {
  const row = await image(env,id);
  const data = await present(env,row,base);
  const rating = row.rating === 'unknown' ? 'Chưa phân loại — chọn nút bên dưới.' : row.rating === 'safe' ? 'An toàn' : 'R18';
  const ai=data.ai_status==='pending' || data.ai_status==='running'?'AI đang xem ảnh…'
    :data.ai_status==='done'?'AI đã gắn tag và phân loại':data.ai_status==='failed'
      ?(data.ai_error==='classification_unresolved'?'AI chưa xác định được Safe/R18; bạn có thể thử lại hoặc chọn thủ công.':'AI chưa phân tích đủ thông tin; bạn có thể thử lại hoặc nhập tay.') :'';
  const text = `${duplicate ? 'Ảnh đã có trong kho' : 'Đã lưu ảnh'}\n${escape(row.title || row.filename)}\n`
    + `${rating}\n${data.tags.length ? escape(data.tags.map(t=>'#'+t).join(' ').slice(0,160))+'\n' : ''}`
    + (ai?`${ai}\n`:'')
    + `<code>${escape(data.url)}</code>`;
  return {text,row,data};
}

export async function refreshAnalysisReceipt(env:Env,job:AnalysisJob) {
  if (!job.chat_id || !job.message_id) return;
  const {text,row,data}=await receiptText(env,job.image_id,env.PUBLIC_BASE_URL);
  await tg(env,job.photo?'editMessageCaption':'editMessageText',{
    chat_id:job.chat_id,message_id:job.message_id,...(job.photo?{caption:text}:{text}),
    parse_mode:'HTML',reply_markup:controls(row.id,data.url,row.rating),
  });
}

async function receipt(env:Env, job:TelegramJob, message:TgMessage, id:string, base:string, duplicate:boolean) {
  const {text,row,data}=await receiptText(env,id,base,duplicate);
  // Store the notification boundary before sending: a lost Telegram ACK must
  // not lead to repeatedly posting receipts. The saved image remains available.
  await env.DB.prepare('UPDATE telegram_jobs SET notified=1 WHERE id=?').bind(job.id).run();
  // Reuse the real Telegram photo/file ID when possible; URL-imports use a text
  // receipt so a format unsupported by sendPhoto cannot hide a successful upload.
  const file = message.photo?.at(-1)?.file_id;
  let result:{message_id:number}|undefined,photo=false;
  if (file) {
    try {result=await tg(env,'sendPhoto',{chat_id:message.chat.id,photo:file,caption:text,parse_mode:'HTML',reply_markup:controls(id,data.url,row.rating)});photo=true;}
    catch(error) {if (!(error instanceof TelegramError && error.status===400)) throw error;}
  }
  const sent=result ?? await tg<{message_id:number}>(env,'sendMessage',{chat_id:message.chat.id,text,parse_mode:'HTML',link_preview_options:{is_disabled:true},reply_markup:controls(id,data.url,row.rating)});
  const attached=await env.DB.prepare('UPDATE image_analysis SET chat_id=?,message_id=?,photo=? WHERE image_id=? AND message_id IS NULL RETURNING *')
    .bind(message.chat.id,sent.message_id,photo?1:0,id).first<AnalysisJob & {status:string}>();
  if(attached&&['pending','running'].includes(data.ai_status)&&!['pending','running'].includes(attached.status)){
    try{await refreshAnalysisReceipt(env,attached);}catch{console.warn('Telegram receipt refresh deferred');}
  }
}

async function handle(env:Env, job:TelegramJob,analyze:boolean,hop=0) {
  const {update,base} = JSON.parse(job.payload) as {update:Update;base:string};
  const callback = update.callback_query;
  const message = callback?.message ?? update.message!;
  if (callback) {
    const match = callback.data?.match(/^(rating:(safe|r18)|delete|tags|analyze):([a-f0-9-]{36})$/);
    if (!match) return;
    const id = match[3];
    if (match[1] === 'delete') {
      try {await remove(env,id);} catch (e) {if (!(e instanceof HttpError && e.status===404)) throw e;}
      await tg(env,'answerCallbackQuery',{callback_query_id:callback.id,text:'Đã xóa ảnh.'});
      await tg(env,'editMessageReplyMarkup',{chat_id:message.chat.id,message_id:message.message_id,reply_markup:{inline_keyboard:[]}});
    } else if (match[1]==='tags') {
      await image(env,id);
      await tg(env,'answerCallbackQuery',{callback_query_id:callback.id});
      await tg(env,'sendMessage',{chat_id:message.chat.id,text:`Sửa tag cho ảnh: ${base}/media/${id}\nReply tin này với #tag1 #tag2, hoặc dấu - để xóa toàn bộ tag.`,reply_markup:{force_reply:true,selective:true}});
    } else if (match[1]==='analyze') {
      if (!await aiEnabled(env)) {await tg(env,'answerCallbackQuery',{callback_query_id:callback.id,text:'Chưa cấu hình API AI.',show_alert:true});return;}
      await image(env,id);
      await enqueueAnalysis(env,id,false,true);
      await env.DB.prepare('UPDATE image_analysis SET chat_id=?,message_id=?,photo=? WHERE image_id=?')
        .bind(message.chat.id,message.message_id,message.photo?1:0,id).run();
      await tg(env,'answerCallbackQuery',{callback_query_id:callback.id,text:'Đã gửi ảnh cho AI xem.'});
      await processAnalysis(env,id,pending=>refreshAnalysisReceipt(env,pending),hop);
    } else {
      await image(env,id);
      await env.DB.prepare('UPDATE images SET rating=?,metadata_version=metadata_version+1 WHERE id=?').bind(match[2],id).run();
      await tg(env,'answerCallbackQuery',{callback_query_id:callback.id,text:match[2]==='safe'?'Đã chọn An toàn.':'Đã chọn R18.'});
      // Replace the old receipt as well as its buttons so the displayed rating
      // agrees with the classification actually used by the API.
      const {text}=await receiptText(env,id,base);
      await tg(env,message.photo?'editMessageCaption':'editMessageText',{
        chat_id:message.chat.id,message_id:message.message_id,
        ...(message.photo?{caption:text}:{text}),parse_mode:'HTML',
        reply_markup:controls(id,`${base}/media/${id}`,match[2]),
      });
    }
    return;
  }
  const reply=message.reply_to_message;
  const target=(reply?.text || reply?.caption || '').match(/\/media\/([a-f0-9-]{36})(?:\s|$)/)?.[1];
  if (target && message.text && (/^\/tags(?:\s|$)/i.test(message.text) || /^Sửa tag cho ảnh:/.test(reply?.text || '') || message.text.startsWith('#') || message.text==='-')) {
    const current=await present(env,await image(env,target),base);
    const raw=message.text.replace(/^\/tags\s*/i,'').trim();
    if (!raw) throw new HttpError(400,'missing_tags','Gửi /tags #tag1 #tag2, hoặc /tags - để xóa hết tag.');
    const chosen=raw==='-'?[]:captionMetadata(raw).tags;
    if (raw!=='-' && !chosen.length) throw new HttpError(400,'invalid_tags','Gửi tag dạng #tag1 #tag2.');
    await updateMetadata(env,target,{title:current.title,source_url:current.source_url,rating:current.rating,tags:chosen});
    await stopPendingAnalysis(env,target);
    const stored=await env.DB.prepare('SELECT * FROM image_analysis WHERE image_id=?').bind(target).first<AnalysisJob>();
    if (stored) await refreshAnalysisReceipt(env,stored);
    await tg(env,'sendMessage',{chat_id:message.chat.id,text:'Đã cập nhật tag.'});return;
  }
  if (/^\/(start|help)(?:\s|$)/i.test(message.text || '')) {
    await tg(env,'sendMessage',{chat_id:message.chat.id,text:HELP}); return;
  }
  if (/^\/stats(?:\s|$)/i.test(message.text || '')) {
    const stats = await env.DB.prepare('SELECT COUNT(*) total,COALESCE(SUM(size),0) bytes,SUM(rating=\'unknown\') pending FROM images').first<{total:number;bytes:number;pending:number}>();
    await tg(env,'sendMessage',{chat_id:message.chat.id,text:`${stats?.total ?? 0} ảnh · ${((stats?.bytes ?? 0)/1024/1024).toFixed(1)} MB\nChờ phân loại: ${stats?.pending ?? 0}`}); return;
  }
  if (job.image_id) {
    if (!job.notified) await receipt(env,job,message,job.image_id,base,false);
    if (analyze) await processAnalysis(env,job.image_id,pending=>refreshAnalysisReceipt(env,pending),hop);
    return;
  }
  const file = message.photo?.at(-1) ?? message.document;
  const info = captionMetadata(message.caption || (file ? '' : message.text || ''));
  let bytes:ArrayBuffer,download:ImageDownload|undefined;
  if (file) {
    if (file.file_size && file.file_size > maxBytes(env)) throw new HttpError(413,'too_large','Ảnh vượt giới hạn dung lượng.');
    const remote = await tg<{file_path?:string}>(env,'getFile',{file_id:file.file_id});
    if (!remote.file_path || remote.file_path.includes('..') || remote.file_path.startsWith('/')) throw new Error('Invalid Telegram file path');
    const response = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${remote.file_path}`,{signal:AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error('Telegram file download failed');
    bytes = await boundedBody(response,maxBytes(env));
  } else {
    const url = (message.text || '').match(/https?:\/\/[^\s<>]+/i)?.[0];
    if (!url) {
      await tg(env,'sendMessage',{chat_id:message.chat.id,text:HELP}); return;
    }
    download=await downloadImage(url,maxBytes(env));bytes=download.bytes;info.source_url=download.url;
  }
  const saved = await save(env,bytes,info,String(message.from!.id),file?.file_name || null,file?null:(message.text || '').match(/https?:\/\/[^\s<>]+/i)?.[0] || null);
  if(download)await recordImportAliases(env,saved.row.id,[download.input,download.url,...(download.used_original?[download.identity]:[])]);
  await enqueueAnalysis(env,saved.row.id,/#manual(?:\s|$)/i.test(message.caption || message.text || ''));
  await env.DB.prepare('UPDATE telegram_jobs SET image_id=? WHERE id=?').bind(saved.row.id,job.id).run();
  await receipt(env,job,message,saved.row.id,base,saved.duplicate);
  if (analyze) await processAnalysis(env,saved.row.id,pending=>refreshAnalysisReceipt(env,pending),hop);
}

// Recover one upload per cron invocation to stay below the Free plan's query
// budget even when an image has the maximum number of tags. Normal webhooks
// each process their own job immediately and do not wait for this recovery.
export async function processJobs(env:Env, limit=1, only?:string,analyze=true,hop=0) {
  if(!only&&!await env.DB.prepare(`SELECT 1 FROM telegram_jobs WHERE
    (status='pending' AND next_attempt<=?) OR (status='running' AND lease_until<?) LIMIT 1`).bind(Date.now(),Date.now()).first()){
    await cleanupHistory(env);return;
  }
  const slot=await acquireSlot(env,'upload');
  if (!slot) return;
  try {await runJobs(env,limit,only,analyze,hop);}
  finally {await releaseSlot(env,slot);await continueWork(env,'upload',hop);}
}

async function runJobs(env:Env, limit=1, only?:string,analyze=true,hop=0) {
  const now=Date.now();
  const rows = await env.DB.prepare(`SELECT id FROM telegram_jobs WHERE
    ((status='pending' AND next_attempt<=?) OR (status='running' AND lease_until<?))
    ${only?'AND id=?':''} ORDER BY created_at LIMIT ?`).bind(now,now,...(only?[only]:[]),limit).all<{id:string}>();
  for (const entry of rows.results) {
    const job = await env.DB.prepare(`UPDATE telegram_jobs SET status='running',attempts=attempts+1,lease_until=?
      WHERE id=? AND ((status='pending' AND next_attempt<=?) OR (status='running' AND lease_until<?)) RETURNING *`)
      .bind(now+120000,entry.id,now,now).first<TelegramJob>();
    if (!job) continue;
    try {
      await handle(env,job,analyze,hop);
      await env.DB.prepare('UPDATE telegram_jobs SET status=\'done\',lease_until=0 WHERE id=?').bind(job.id).run();
    } catch (error) {
      // Never log exception text: fetch errors may contain the bot token URL.
      const fresh = await env.DB.prepare('SELECT notified FROM telegram_jobs WHERE id=?').bind(job.id).first<{notified:number}>();
      const known = error instanceof TelegramError;
      const sentUnknown = fresh?.notified === 1 && !known;
      const terminal = sentUnknown || error instanceof HttpError || job.attempts>=3 || known && error.status!==429 && error.status<500;
      await env.DB.prepare('UPDATE telegram_jobs SET status=?,next_attempt=?,last_error=?,lease_until=0 WHERE id=?')
        .bind(sentUnknown?'uncertain':terminal?'failed':'pending',Date.now()+(known?Math.max(error.retryAfter,30)*1000:60000),
          error instanceof HttpError?error.code:known?`telegram_${error.status}`:'temporary_error',job.id).run();
      if (known && error.status===429 && job.attempts<3) {
        await env.DB.prepare('UPDATE telegram_jobs SET notified=0 WHERE id=?').bind(job.id).run();
      } else if (terminal && !fresh?.notified) {
        const {update} = JSON.parse(job.payload) as {update:Update};
        const chat=update.message?.chat ?? update.callback_query?.message?.chat;
        if (chat) try {await tg(env,'sendMessage',{chat_id:chat.id,text:error instanceof HttpError?error.message:'Chưa hoàn tất upload. Hãy thử lại sau hoặc kiểm tra /stats.'});} catch { /* Receipt cannot be delivered. */ }
      }
      console.warn('Upload job status',{job_id:job.id,status:sentUnknown?'uncertain':terminal?'failed':'pending'});
    }
  }
  if (!only) {
    await cleanupHistory(env);
  }
}
async function cleanupHistory(env:Env){
  await cleanupObjects(env);
  await env.DB.prepare("DELETE FROM telegram_jobs WHERE status IN ('done','failed','uncertain') AND created_at<?").bind(Date.now()-7*86400000).run();
  await env.DB.prepare('DELETE FROM analysis_batches WHERE created_at<?').bind(Date.now()-7*86400000).run();
}
