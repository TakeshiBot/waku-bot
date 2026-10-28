import { HttpError, type Metadata, type Rating } from './types';

export function equalSecret(actual: string, expected: string | undefined): boolean {
  if (!expected) return false;
  let diff = actual.length ^ expected.length;
  for (let i = 0; i < Math.max(actual.length, expected.length); i++) {
    diff |= (actual.charCodeAt(i) || 0) ^ (expected.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export function owner(id: unknown, ids: string): boolean {
  return (typeof id === 'number' && Number.isSafeInteger(id) || typeof id === 'string' && /^\d+$/.test(id))
    && ids.split(/[\s,]+/).filter(Boolean).includes(String(id));
}

export function publicUrl(raw: unknown): URL {
  if (typeof raw !== 'string' || raw.length > 4096) throw new HttpError(400, 'invalid_url', 'URL không hợp lệ.');
  let url: URL;
  try { url = new URL(raw); } catch { throw new HttpError(400, 'invalid_url', 'URL không hợp lệ.'); }
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  // This importer deliberately does not accept IP literals, local names or
  // alternate ports. Worker fetches use Cloudflare's public network resolver.
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password
      || host.includes(':') || /^\d+(\.\d+)*$/.test(host) || !host.includes('.')
      || /(?:^|\.)(localhost|local|internal|test|invalid|example|home|lan)$/.test(host)
      || host.endsWith('.arpa') || url.port && !['80','443'].includes(url.port)) {
    throw new HttpError(400, 'unsafe_url', 'Chỉ nhận URL HTTP(S) công khai, không chứa thông tin đăng nhập.');
  }
  url.hash = '';
  return url;
}

export function tags(value: unknown): string[] {
  const values = typeof value === 'string' ? value.split(',') : value;
  if (values === undefined || values === null) return [];
  if (!Array.isArray(values) || values.length > 30) throw new HttpError(400, 'invalid_tags', 'Tối đa 30 tag.');
  return [...new Set(values.map(x => {
    if (typeof x !== 'string') throw new HttpError(400, 'invalid_tags', 'Tag phải là chữ.');
    const tag = x.normalize('NFKC').toLowerCase().replace(/^#/, '').trim();
    if (!tag || tag.length > 64 || /[\u0000-\u001f\u007f]/.test(tag)) throw new HttpError(400, 'invalid_tags', 'Tag dài 1–64 ký tự.');
    return tag;
  }))];
}

export function metadata(input: Record<string, unknown>): Metadata {
  const rating = input.rating ?? 'unknown';
  if (!['safe','r18','unknown'].includes(String(rating))) throw new HttpError(400, 'invalid_rating', 'rating: safe, r18 hoặc unknown.');
  if (input.title !== undefined && (typeof input.title !== 'string' || input.title.length > 200)) throw new HttpError(400, 'invalid_title', 'Tên ảnh tối đa 200 ký tự.');
  const source = input.source_url ? publicUrl(input.source_url).href : null;
  return { title: String(input.title ?? '').trim(), tags: tags(input.tags), rating: rating as Rating, source_url: source };
}

export function captionMetadata(caption: string): Metadata {
  const matches = [...caption.matchAll(/#([\p{L}\p{N}_-]{1,64})/gu)].map(m => m[1]);
  const rating: Rating = matches.some(x => /^(r18|nsfw)$/i.test(x)) ? 'r18'
    : matches.some(x => /^(safe|sfw)$/i.test(x)) ? 'safe' : 'unknown';
  const link = caption.match(/https?:\/\/[^\s<>]+/i)?.[0];
  return metadata({
    title: caption.replace(/#[\p{L}\p{N}_-]+/gu, '').replace(/https?:\/\/[^\s<>]+/g, '').trim().slice(0,200),
    tags: matches.filter(x => !/^(r18|nsfw|safe|sfw|manual)$/i.test(x)), rating, source_url: link,
  });
}

export function imageType(data: ArrayBuffer): {mime: string; ext: string} {
  const b = new Uint8Array(data);
  const text = (a: number, z: number) => String.fromCharCode(...b.subarray(a,z));
  if (b.length >= 12 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return {mime:'image/jpeg', ext:'jpg'};
  if (b.length >= 24 && [137,80,78,71,13,10,26,10].every((x,i) => b[i] === x)) return {mime:'image/png', ext:'png'};
  if (b.length >= 13 && /^GIF8[79]a$/.test(text(0,6))) return {mime:'image/gif', ext:'gif'};
  if (b.length >= 16 && text(0,4) === 'RIFF' && text(8,12) === 'WEBP') return {mime:'image/webp', ext:'webp'};
  if (b.length >= 16 && text(4,8) === 'ftyp' && ['avif','avis'].includes(text(8,12))) return {mime:'image/avif', ext:'avif'};
  throw new HttpError(415, 'not_image', 'Chỉ nhận ảnh JPEG, PNG, WebP, GIF hoặc AVIF; không nhận trang HTML/SVG.');
}

export async function boundedBody(response: Response | Request, limit: number): Promise<ArrayBuffer> {
  const length = response.headers.get('content-length');
  if (length && (!/^\d+$/.test(length) || Number(length) > limit)) throw new HttpError(413, 'too_large', 'Ảnh vượt giới hạn dung lượng.');
  if (!response.body) throw new HttpError(400, 'empty_body', 'Nội dung trống.');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new HttpError(413, 'too_large', 'Ảnh vượt giới hạn dung lượng.'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  if (!size) throw new HttpError(400, 'empty_body', 'Ảnh trống.');
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; }
  return joined.buffer;
}

export async function fetchImage(raw: string, limit: number): Promise<ArrayBuffer> {
  let url = publicUrl(raw);
  for (let i=0; i<5; i++) {
    const response = await fetch(url, {redirect:'manual', signal:AbortSignal.timeout(20000), headers:{'User-Agent':'WakuImageAPI/0.1'}});
    if ([301,302,303,307,308].includes(response.status)) {
      await response.body?.cancel();
      const location = response.headers.get('location');
      if (!location) throw new HttpError(400, 'bad_redirect', 'Link chuyển hướng không hợp lệ.');
      url = publicUrl(new URL(location, url).href);
      continue;
    }
    if (!response.ok) { await response.body?.cancel(); throw new HttpError(422, 'download_failed', `Nguồn ảnh trả HTTP ${response.status}.`); }
    return boundedBody(response, limit);
  }
  throw new HttpError(400, 'redirect_limit', 'Link chuyển hướng quá nhiều lần.');
}
