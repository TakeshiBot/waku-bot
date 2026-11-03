import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cloudflareCredentials} from '../scripts/common.mjs';

test('deployment selects the token belonging to the configured account',()=>{
  const old='a'.repeat(32),current='b'.repeat(32);
  const text=`token cloudflare old-token\nAccount ID ${old}\nNew account\ntoken cloudflare new-token\nAccount ID ${current}\n`;
  assert.deepEqual(cloudflareCredentials(text,current),{token:'new-token',account:current});
  assert.deepEqual(cloudflareCredentials(text,old),{token:'old-token',account:old});
  assert.throws(()=>cloudflareCredentials(text),/chọn rõ account/);
  assert.throws(()=>cloudflareCredentials(text,'c'.repeat(32)),/không khớp/);
  assert.throws(()=>cloudflareCredentials(`token cloudflare x\nAccount ID ${old}\nAccount ID ${current}`,old),/đủ cặp/);
  assert.deepEqual(cloudflareCredentials('',current,'env-token'),{token:'env-token',account:current});
  assert.deepEqual(cloudflareCredentials(`cloudflare_token = single\naccount_id = ${old}`),{token:'single',account:old});
});
