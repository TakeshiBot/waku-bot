// Group only identities visible in the URL. A CDN file/post without an author
// stays its own source page; never guess that different authors are the same.
export function sourceIdentity(raw:string){
  const url=new URL(raw);url.hash='';
  let host=url.hostname.toLowerCase().replace(/^www\./,''),parts=url.pathname.split('/').filter(Boolean);
  if(['facebook.com','web.facebook.com','m.facebook.com','mbasic.facebook.com'].includes(host)){
    const id=parts[0]==='profile.php'?url.searchParams.get('id'):parts[0]==='people'?parts[2]:/^\d+$/.test(parts[0]||'')?parts[0]:null;
    if(id&&/^\d+$/.test(id))return {key:'https://www.facebook.com/profile.php?id='+id,label:'Facebook · '+id};
    if(parts[0]&&!['photo','photo.php','photos','watch','reel','share','groups','story.php','permalink.php','login','profile.php'].includes(parts[0].toLowerCase()))return {key:'https://www.facebook.com/'+parts[0].toLowerCase(),label:'@'+parts[0]+' · Facebook'};
  }
  if(['x.com','twitter.com','mobile.twitter.com','mobile.x.com'].includes(host)&&parts[0]==='i'&&parts[1]==='user'&&/^\d+$/.test(parts[2]||''))return {key:'https://x.com/i/user/'+parts[2],label:'X · '+parts[2]};
  if(['x.com','twitter.com','mobile.twitter.com','mobile.x.com'].includes(host)&&parts[0]&&!['i','intent','search','hashtag','home','explore','share'].includes(parts[0].toLowerCase())){
    const account=parts[0].toLowerCase();return {key:'https://x.com/'+account,label:'@'+account+' · X'};
  }
  if(host==='tiktok.com'&&parts[0]?.startsWith('@'))return {key:'https://www.tiktok.com/'+parts[0].toLowerCase(),label:parts[0]+' · TikTok'};
  if(host==='bsky.app'&&parts[0]==='profile'&&parts[1])return {key:'https://bsky.app/profile/'+parts[1].toLowerCase(),label:parts[1]+' · Bluesky'};
  if(host==='instagram.com'&&parts[0]&&!['p','reel','reels','stories','explore'].includes(parts[0]))return {key:'https://www.instagram.com/'+parts[0].toLowerCase(),label:'@'+parts[0]+' · Instagram'};
  if(host==='pixiv.net'){
    if(parts[0]&&/^[a-z]{2}$/i.test(parts[0]))parts=parts.slice(1);
    if(parts[0]==='users'&&/^\d+$/.test(parts[1]||''))return {key:'https://www.pixiv.net/users/'+parts[1],label:'Pixiv · '+parts[1]};
  }
  url.hostname=host;url.pathname=url.pathname.replace(/\/+$/,'')||'/';
  for(const key of [...url.searchParams.keys()])if(/^utm_/i.test(key)||['fbclid','gclid','ref_src','ref_url'].includes(key))url.searchParams.delete(key);
  url.searchParams.sort();
  return {key:url.href,label:host+(url.pathname==='/'?'':url.pathname)+url.search};
}
