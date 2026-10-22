import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createSessions, cookieHeader, loadSessionKey, sessionSeconds } from '../session.mjs';
import { historyGroups, readView, writeView } from '../public/chat-state.js';

test('encrypted sessions expire and reject changes or another server key', () => {
  let now=1000; const sessions=createSessions(randomBytes(32),()=>now);
  const value=sessions.issue('synthetic-key'); const cookie=cookieHeader(value);
  assert(!cookie.includes('synthetic-key')); assert.match(cookie,/HttpOnly; SameSite=Strict/);
  assert.equal(sessions.read(cookie),'synthetic-key');
  assert.equal(sessions.read('agentai_session=broken'),null);
  assert.equal(createSessions().read(cookie),null);
  now+=sessionSeconds*1000; assert.equal(sessions.read(cookie),null);
  assert.match(cookieHeader(''),/Max-Age=0/);
});
test('the key survives restart without being replaced',async()=>{
  const root=await mkdtemp(join(tmpdir(),'agentai-session-'));
  try { assert.deepEqual(await loadSessionKey(join(root,'key')),await loadSessionKey(join(root,'key'))); }
  finally { await rm(root,{recursive:true,force:true}); }
});
test('conversation and draft state is isolated by user and survives reload',()=>{
  const data=new Map(),storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)};
  writeView('alice',{identity:'chat-1',drafts:{'chat-1':'Вопрос'},scroll:120},storage);
  assert.deepEqual(readView('alice',storage),{identity:'chat-1',drafts:{'chat-1':'Вопрос'},scroll:120});
  assert.deepEqual(readView('bob',storage),{identity:null,drafts:{},scroll:0});
  storage.setItem('agentai.view.alice','broken'); assert.equal(readView('alice',storage).identity,null);
  assert.doesNotThrow(()=>writeView('alice',{}, {setItem(){throw Error('quota');}}));
});
test('history groups dates and keeps unnamed chats findable',()=>{
  const now=new Date('2026-10-22T12:00:00');
  const rows=[{id:'1',title:'Отчёты',updated_at:now.toISOString()},
    {id:'2',title:'Палаты',updated_at:'2026-10-21T12:00:00'},
    {id:'3',title:'Новый диалог',updated_at:now.toISOString()}];
  const grouped=historyGroups(rows,'',now);
  assert.deepEqual([...grouped.groups.keys()],['Сегодня','Вчера']);
  assert.equal(grouped.unnamed.length,1);
  assert.equal(historyGroups(rows,'палаты',now).groups.get('Вчера')[0].id,'2');
});
