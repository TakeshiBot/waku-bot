import {test} from 'node:test';
import assert from 'node:assert/strict';
import {imageSourcePlan,imageHeaders} from '../src/image-sources.ts';
import {downloadImage,publicUrl} from '../src/input.ts';

const plan=(url:string)=>imageSourcePlan(publicUrl(url));
test('popular direct-image CDN URLs prefer originals and preserve required query parameters',()=>{
  const cases:[string,string][]=[
    ['https://pbs.twimg.com/media/HVIGlbxboAAX0al?format=jpg&name=360x360','https://pbs.twimg.com/media/HVIGlbxboAAX0al?format=jpg&name=orig'],
    ['https://pbs.twimg.com/media/abc123.jpg:small','https://pbs.twimg.com/media/abc123?format=jpg&name=orig'],
    ['https://pbs.twimg.com/profile_images/123/avatar_400x400.jpg','https://pbs.twimg.com/profile_images/123/avatar.jpg'],
    ['https://media.discordapp.net/attachments/123/456/image.png?ex=abc&is=def&hm=secret&width=360&height=360&format=webp','https://cdn.discordapp.com/attachments/123/456/image.png?ex=abc&is=def&hm=secret'],
    ['https://i.pinimg.com/736x/ab/cd/ef/abcdef123456.jpg','https://i.pinimg.com/originals/ab/cd/ef/abcdef123456.jpg'],
    ['https://i.pximg.net/c/250x250_80_a2/img-master/img/2026/10/28/00/00/00/123456789_p0_square1200.jpg','https://i.pximg.net/img-original/img/2026/10/28/00/00/00/123456789_p0.jpg'],
    ['https://i.imgur.com/AbC1234m.jpg','https://i.imgur.com/AbC1234.jpg'],
    ['https://preview.redd.it/abc123.jpeg?width=360&crop=smart&auto=webp&s=signature','https://i.redd.it/abc123.jpeg'],
    ['https://images.unsplash.com/photo-123-abc?ixid=keep&w=360&h=360&fit=crop&auto=format&q=80','https://images.unsplash.com/photo-123-abc?ixid=keep'],
    ['https://images.pexels.com/photos/123/pexels-photo-123.jpeg?auto=compress&cs=tinysrgb&h=360&w=360','https://images.pexels.com/photos/123/pexels-photo-123.jpeg'],
    ['https://cdn.shopify.com/s/files/1/0001/products/test_360x360_crop_center.jpg?v=123&width=360','https://cdn.shopify.com/s/files/1/0001/products/test.jpg?v=123'],
    ['https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Test.jpg/360px-Test.jpg','https://upload.wikimedia.org/wikipedia/commons/a/ab/Test.jpg'],
    ['https://i0.wp.com/site.com/wp-content/uploads/2026/10/test-360x360.jpg?resize=360%2C360&ssl=1','https://i0.wp.com/site.com/wp-content/uploads/2026/10/test.jpg?ssl=1'],
    ['https://site.com/wp-content/uploads/2026/10/test-360x360.jpg','https://site.com/wp-content/uploads/2026/10/test.jpg'],
  ];
  for(const [input,expected]of cases){const result=plan(input);assert.equal(result.preferred_url,expected,input);assert.ok(result.source);assert.ok(result.candidates.length<=4);}
  assert.equal(plan('https://pbs.twimg.com/media/test?format=jpg&name=small').identity,plan('https://pbs.twimg.com/media/test?format=png&name=large').identity);
  assert.equal(plan('https://media.discordapp.net/attachments/123/456/image.png?ex=abc&is=def&hm=old&width=360').identity,plan('https://cdn.discordapp.com/attachments/123/456/image.png?ex=xyz&is=uv&hm=new').identity);
});

test('unknown/signed URLs, original Imgur IDs, external proxies and non-raster originals stay intact',()=>{
  for(const url of [
    'https://othercdn.com/image.jpg?width=360&token=secret',
    'https://images.unsplash.com/photo-123?width=360&s=signed',
    'https://media.discordapp.net/attachments/123/456/image.png?token=secret&width=360',
    'https://cdn.shopify.com/image.jpg?width=360&X-Amz-Signature=signed',
    'https://i.imgur.com/AbC123h.jpg',
    'https://pbs.twimg.com.evil.org/media/test?format=jpg&name=360x360',
    'https://external-preview.redd.it/test.jpg?width=360&s=signed',
    'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Test.svg/360px-Test.svg.png',
  ]){const result=plan(url);assert.equal(result.preferred_url,url);assert.equal(result.candidates.length,1);}
  assert.equal(imageHeaders(new URL('https://i.pximg.net/a.jpg')).Referer,'https://www.pixiv.net/');
  assert.equal(imageHeaders(new URL('https://i.pximg.net.evil.org/a.jpg')).Referer,undefined);
});

test('downloads use the real original, bounded extension alternatives, truthful fallback and validated redirects',async()=>{
  const originalFetch=globalThis.fetch,requests:string[]=[];
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
  const image=()=>new Response(png,{headers:{'content-type':'image/png'}});
  const input='https://pbs.twimg.com/media/test?format=jpg&name=360x360';
  try{
    globalThis.fetch=async url=>{requests.push(String(url));return image();};
    const full=await downloadImage(input,1024);assert.equal(requests.length,1);assert.match(requests[0],/name=orig/);assert.equal(full.used_original,true);assert.equal(full.fallback_reason,null);
    requests.length=0;globalThis.fetch=async url=>{const current=new URL(String(url));requests.push(current.href);return current.searchParams.get('format')==='jpg'?new Response(null,{status:404}):image();};
    const alternate=await downloadImage(input,1024);assert.equal(requests.length,2);assert.equal(alternate.used_original,true);assert.match(alternate.url,/format=png/);
    requests.length=0;globalThis.fetch=async url=>{requests.push(String(url));return String(url).includes('name=orig')?new Response(null,{status:403}):image();};
    const fallback=await downloadImage(input,1024);assert.equal(requests.length,2);assert.equal(fallback.used_original,false);assert.equal(fallback.fallback_reason,'download_failed');assert.equal(fallback.url,input);
    requests.length=0;globalThis.fetch=async url=>{requests.push(String(url));return String(url).includes('name=orig')?new Response(png,{headers:{'content-length':'999999'}}):image();};
    const capped=await downloadImage(input,1024);assert.equal(requests.length,2);assert.equal(capped.fallback_reason,'too_large');
    globalThis.fetch=async()=>new Response('<html>Not an image</html>');await assert.rejects(downloadImage('https://unknowncdn.com/file.jpg',1024),(error:any)=>error.code==='not_image');
    requests.length=0;globalThis.fetch=async url=>{requests.push(String(url));return new Response(null,{status:302,headers:{location:'http://127.0.0.1/private'}});};
    await assert.rejects(downloadImage(input,1024),(error:any)=>error.code==='unsafe_url');assert.equal(requests.length,1,'a private redirect is never fetched');
    requests.length=0;globalThis.fetch=async(url,options)=>{requests.push(String(url));assert.equal(new Headers(options?.headers).get('referer'),requests.length===1?'https://www.pixiv.net/':null);return requests.length===1?new Response(null,{status:302,headers:{location:'https://othercdn.com/actual.png'}}):image();};
    await downloadImage('https://i.pximg.net/img-original/img/2026/10/28/00/00/00/123456789_p0.png',1024);assert.equal(requests.length,2);
  }finally{globalThis.fetch=originalFetch;}
});
