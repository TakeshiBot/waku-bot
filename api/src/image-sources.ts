// Only known image CDN layouts are rewritten. Unknown/signed links stay intact.
export interface ImageCandidate {url:string;original:boolean;}
export interface ImageSourcePlan {input:string;source:string|null;identity:string;preferred_url:string;candidates:ImageCandidate[];}
const raster='(?:jpe?g|png|gif|webp|avif)';
const signed=(url:URL,allow:string[]=[])=>[...url.searchParams.keys()].some(key=>!allow.includes(key)&&/^(?:s|sig|signature|token|access_token|auth|auth_key|expires|policy|key-pair-id|x-amz-.+|x-goog-.+)$/i.test(key));
function strip(url:URL,keys:string[]){for(const key of keys)url.searchParams.delete(key);}

export function imageSourcePlan(input:URL):ImageSourcePlan {
  const raw=input.href,url=new URL(raw),host=url.hostname.toLowerCase().replace(/\.$/,''),path=url.pathname;
  let source:string|null=null,identity='',alternatives:string[]=[];
  // Discord's ex/is/hm authenticate the attachment, not its resize parameters.
  // They are preserved, including when switching from media proxy to file CDN.
  const discord=['media.discordapp.net','cdn.discordapp.com'].includes(host)&&/^\/attachments\/\d+\/\d+\/[^/]+$/.test(path);
  if(signed(url,host==='preview.redd.it'?['s']:[]))return {input:raw,source:null,identity:raw,preferred_url:raw,candidates:[{url:raw,original:false}]};
  if(host==='pbs.twimg.com'){
    const match=path.match(/^\/media\/([\w-]+)(?:\.(jpg|jpeg|png|webp))?(?::(?:orig|large|medium|small|thumb|\d+x\d+))?$/i);
    if(match){
      source='X / Twitter';identity='waku-image:v1:twitter:'+match[1];url.pathname='/media/'+match[1];
      const format=(url.searchParams.get('format') || match[2] || 'jpg').toLowerCase();
      url.searchParams.set('format',format==='png'?'png':'jpg');url.searchParams.set('name','orig');
      const alt=new URL(url);alt.searchParams.set('format',format==='png'?'jpg':'png');alternatives=[alt.href];
    }else if(/^\/profile_images\/\d+\/[^/]+\.(?:jpe?g|png|gif|webp)$/i.test(path)){
      source='X / Twitter';url.pathname=path.replace(/_(?:normal|bigger|mini|\d+x\d+)(?=\.[^.]+$)/,'');identity=url.href;
    }
  }else if(discord){
    source='Discord';url.hostname='cdn.discordapp.com';
    strip(url,['width','height','format','quality','size','fit','crop']);
    // The attachment IDs stay stable when Discord refreshes the signed URL.
    // This identity is for deduplication only, never an address to fetch.
    identity='waku-image:v1:discord:'+path;
  }else if(host==='i.pinimg.com'&&/^\/(?:originals|\d+x(?:\d+)?(?:_[a-z]+)?)\/[a-f0-9]{2}\/[a-f0-9]{2}\/[a-f0-9]{2}\/[a-f0-9]+\.(?:jpg|png|webp|gif)$/i.test(path)){
    source='Pinterest';url.pathname=path.replace(/^\/[^/]+\//,'/originals/');
    identity='waku-image:v1:pinterest:'+url.pathname.replace(/\.[^.]+$/,'');
    alternatives=['jpg','png','webp'].map(ext=>{const alt=new URL(url);alt.pathname=alt.pathname.replace(/\.[^.]+$/,'.'+ext);return alt.href;});
  }else if(host==='i.pximg.net'){
    const match=path.match(/^\/(?:c\/[^/]+\/)?(?:img-master|custom-thumb|img-original)\/(img\/\d{4}\/\d{2}\/\d{2}\/\d{2}\/\d{2}\/\d{2}\/\d+_p\d+)(?:_(?:master|square|custom)\d+)?\.(jpg|png|gif|webp)$/i);
    if(match){source='Pixiv';identity='waku-image:v1:pixiv:'+match[1];url.pathname='/img-original/'+match[1]+'.'+match[2];
      alternatives=['jpg','png'].map(ext=>{const alt=new URL(url);alt.pathname='/img-original/'+match[1]+'.'+ext;return alt.href;});}
  }else if(host==='i.imgur.com'){
    const match=path.match(/^\/([a-zA-Z0-9]{5}|[a-zA-Z0-9]{7})[sbtmlh]\.(jpg|jpeg|png|gif|webp)$/);
    if(match){source='Imgur';url.pathname='/'+match[1]+'.'+match[2];identity=url.href;}
  }else if(host==='preview.redd.it'&&new RegExp('^/[^/]+\\.'+raster+'$','i').test(path)){
    // Reddit's s= is a rendition signature; the original uses i.redd.it.
    source='Reddit';url.hostname='i.redd.it';strip(url,['width','height','crop','auto','format','quality','s']);identity=url.href;
  }else if(host==='images.unsplash.com'&&/^\/photo-[\w-]+/.test(path)){
    source='Unsplash';strip(url,['w','h','fit','crop','fm','auto','q','dpr']);identity=url.href;
  }else if(host==='images.pexels.com'&&/^\/photos\/\d+\//.test(path)){
    source='Pexels';strip(url,['w','h','fit','crop','auto','q','dpr','cs']);identity=url.href;
  }else if(host==='cdn.shopify.com'||host.endsWith('.myshopify.com')&&path.startsWith('/cdn/shop/')){
    if(new RegExp('\\.'+raster+'$','i').test(path)){
      source='Shopify';strip(url,['width','height','crop','format','quality']);
      url.pathname=path.replace(/_(?:\d+x\d*|x\d+|small|medium|large|grande|compact|master)(?:_crop_[a-z]+)?(?:@\d+x)?(?=\.[^.]+$)/i,'');identity=url.href;
    }
  }else if(host==='upload.wikimedia.org'){
    const match=path.match(new RegExp('^(/[^/]+/[^/]+)/thumb/([a-f0-9]/[a-f0-9]{2}/[^/]+\\.'+raster+')/[^/]+$','i'));
    if(match){source='Wikimedia';url.pathname=match[1]+'/'+match[2];identity=url.href;}
  }else if(/^i[0-3]\.wp\.com$/.test(host)){
    source='WordPress';strip(url,['w','h','resize','fit','crop','zoom','quality','allow_lossy']);
    url.pathname=path.replace(/(-\d+x\d+)(?=\.(?:jpe?g|png|gif|webp|avif)$)/i,'');identity=url.href;
  }else if(/\/wp-content\/uploads\//.test(path)&&/-\d+x\d+\.(?:jpe?g|png|gif|webp|avif)$/i.test(path)){
    source='WordPress';url.pathname=path.replace(/-\d+x\d+(?=\.[^.]+$)/,'');identity=url.href;
  }
  if(!source)return {input:raw,source:null,identity:raw,preferred_url:raw,candidates:[{url:raw,original:false}]};
  if(identity.startsWith('http')){const stable=new URL(identity);stable.searchParams.sort();identity=stable.href;}
  const urls=[...new Set([url.href,...alternatives])].slice(0,3);
  const candidates:ImageCandidate[]=urls.map(url=>({url,original:true}));
  if(!urls.includes(raw))candidates.push({url:raw,original:false});
  return {input:raw,source,identity:identity || url.href,preferred_url:url.href,candidates};
}

export function imageHeaders(url:URL):Record<string,string> {
  return {'User-Agent':'WakuImageAPI/0.1',...(url.hostname.toLowerCase().replace(/\.$/,'')==='i.pximg.net'?{Referer:'https://www.pixiv.net/'}:{})};
}
