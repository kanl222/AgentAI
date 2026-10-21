import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createChatServer } from '../server.mjs';

async function listen(server) {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  return 'http://127.0.0.1:' + server.address().port;
}
async function close(server) {
  if (!server.listening) return;
  const stopped = new Promise(resolve => server.close(resolve));
  server.closeAllConnections(); await stopped;
}
async function fixture(t, handler, options = {}) {
  const backend = http.createServer(handler);
  const origin = await listen(backend);
  const chat = createChatServer({ agentUrl: origin, ...options });
  const url = await listen(chat);
  t.after(async () => { await close(chat); await close(backend); });
  return { url, origin, backend, chat };
}
async function body(request) {
  const chunks=[]; for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks);
}

test('serves the real interface, module MIME types, HEAD and security headers', async t => {
  const { url } = await fixture(t, (_, res) => res.end());
  const response = await fetch(url);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /AgentAI — чат/);
  assert.match(response.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  const script=await fetch(url+'/chat.js');
  assert.match(script.headers.get('content-type'), /javascript/);
  for (const path of ['/theme.js','/highlighter.js','/highlight-languages.js','/vendor/highlight/core.min.js','/vendor/highlight/languages/sql.min.js']) {
    const asset=await fetch(url+path);assert.equal(asset.status,200,path);assert.match(asset.headers.get('content-type'),/javascript/);
  }
  assert.equal((await fetch(url+'/vendor/highlight/package.json')).status,404);
  const head=await fetch(url+'/chat.css',{method:'HEAD'});
  assert.equal(head.status,200); assert.equal(await head.text(),'');
  assert(Number(head.headers.get('content-length'))>100);
});

test('health checks only the Node service; an old /error address redirects home', async t => {
  const {url}=await fixture(t,(_,res)=>res.end());
  assert.deepEqual(await (await fetch(url+'/health')).json(),{status:'alive',service:'agentai-chat'});
  const response=await fetch(url+'/error',{redirect:'manual'});
  assert.equal(response.status,303); assert.equal(response.headers.get('location'),'/');
});

test('never exposes server files, configuration or traversal paths', async t => {
  const {url}=await fixture(t,(_,res)=>res.end());
  for(const path of ['/server.mjs','/start.mjs','/.env','/package.json','/test/server.test.mjs','/%2e%2e/.env','/not-found']) {
    assert.equal((await fetch(url+path)).status,404,path);
  }
  const response=await fetch(url,{method:'POST',body:'no'});
  assert.equal(response.status,405); assert.equal(response.headers.get('allow'),'GET, HEAD');
});

test('forwards the personal key and raw query to the fixed backend, without cookies', async t => {
  let observed;
  const {url}=await fixture(t,(req,res)=>{observed={url:req.url,authorization:req.headers.authorization,cookie:req.headers.cookie};res.setHeader('Content-Type','application/json');res.end('{"name":"reader"}');});
  const response=await fetch(url+'/api/session?label=%D1%82%D0%B5%D1%81%D1%82',{headers:{Authorization:'Bearer test-key',Cookie:'unrelated=private'}});
  assert.equal(response.status,200);
  assert.deepEqual(observed,{url:'/session?label=%D1%82%D0%B5%D1%81%D1%82',authorization:'Bearer test-key',cookie:undefined});
  assert.deepEqual(await response.json(),{name:'reader'});
});

test('streams POST JSON and returns the queued job unchanged', async t => {
  let observed;
  const {url}=await fixture(t,async(req,res)=>{observed={method:req.method,path:req.url,body:JSON.parse((await body(req)).toString())};res.writeHead(202,{'Content-Type':'application/json'});res.end('{"id":"job","state":"queued"}');});
  const input={question:'Какие значения принимает flag_order?',conversation_id:'test-conversation'};
  const response=await fetch(url+'/api/jobs/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});
  assert.equal(response.status,202); assert.deepEqual(await response.json(),{id:'job',state:'queued'});
  assert.deepEqual(observed,{method:'POST',path:'/jobs/chat',body:input});
});

test('preserves authorization, queue and service failures from AgentAI', async t => {
  const {url}=await fixture(t,(req,res)=>{res.writeHead(Number(req.url.slice(1)),{'Content-Type':'application/json'});res.end('{"detail":"backend-message"}');});
  for(const status of [401,403,404,409,429,503]) {
    const response=await fetch(url+'/api/'+status);
    assert.equal(response.status,status);assert.deepEqual(await response.json(),{detail:'backend-message'});
  }
});

test('preserves exact PDF bytes and download headers; strips hop-by-hop headers', async t => {
  const data=Buffer.from([0x25,0x50,0x44,0x46,0x00,0xff,0x80,0x0a]);
  const {url}=await fixture(t,(_,res)=>{res.writeHead(200,{'Content-Type':'application/pdf','Content-Disposition':'attachment; filename="source.pdf"','Content-Length':data.length,'Connection':'x-hop','x-hop':'private'});res.end(data);});
  const response=await fetch(url+'/api/documents/version/original');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()),data);
  assert.equal(response.headers.get('content-disposition'),'attachment; filename="source.pdf"');
  assert.equal(response.headers.get('x-hop'),null);
  assert.equal(response.headers.get('cache-control'),'no-store');
});

test('rejects oversized Content-Length before contacting the backend', async t => {
  let called=false;
  const {url}=await fixture(t,(_,res)=>{called=true;res.end();},{maxBodyBytes:16});
  const response=await fetch(url+'/api/upload',{method:'POST',body:'x'.repeat(32)});
  assert.equal(response.status,413);await response.text();assert.equal(called,false);
});

test('enforces the body limit for chunked uploads without Content-Length', async t => {
  const {url}=await fixture(t,(req,res)=>{req.resume();req.on('end',()=>res.end());},{maxBodyBytes:16});
  const status=await new Promise((resolve,reject)=>{
    const request=http.request(url+'/api/upload',{method:'POST',headers:{'Transfer-Encoding':'chunked'}},response=>{response.resume();response.on('end',()=>resolve(response.statusCode));});
    request.on('error',reject);request.write('12345678');request.end('x'.repeat(24));
  });
  assert.equal(status,413);
  assert.equal((await fetch(url+'/health')).status,200);
});

test('returns a bounded 504 for a stalled backend and remains available', async t => {
  const {url}=await fixture(t,()=>{}, {proxyTimeoutMs:35});
  const response=await fetch(url+'/api/jobs/slow');
  assert.equal(response.status,504);assert.match((await response.json()).detail,/вовремя/);
  assert.equal((await fetch(url+'/health')).status,200);
});

test('returns a safe 502 when the backend is offline', async t => {
  const {url,backend}=await fixture(t,(_,res)=>res.end());await close(backend);
  const response=await fetch(url+'/api/session',{headers:{Authorization:'Bearer never-echo-this'}});
  assert.equal(response.status,502);const text=await response.text();assert(!text.includes('never-echo-this'));assert(!text.includes('ECONNREFUSED'));
});

test('independent proxy requests run concurrently', async t => {
  const waiting=[];
  const {url}=await fixture(t,(_,res)=>{waiting.push(res);if(waiting.length===2)for(const response of waiting)response.end('ready');},{proxyTimeoutMs:2000});
  const responses=await Promise.all([fetch(url+'/api/one'),fetch(url+'/api/two')]);
  assert.deepEqual(await Promise.all(responses.map(r=>r.text())),['ready','ready']);
});

test('client cancellation closes the corresponding upstream response', async t => {
  let responseClosed;
  const closed=new Promise(resolve=>responseClosed=resolve);
  const {url}=await fixture(t,(_,res)=>{res.on('close',responseClosed);res.write('first chunk');});
  const controller=new AbortController();const response=await fetch(url+'/api/slow',{signal:controller.signal});
  assert.equal(response.status,200);controller.abort();
  let deadline;
  try {await Promise.race([closed,new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('upstream not cancelled')),2000);})]);}
  finally {clearTimeout(deadline);}
});

test('refuses invalid upstreams and API paths that could become an open proxy', async t => {
  for(const agentUrl of ['file:///tmp','http://name:password@localhost/','http://localhost/v1'])assert.throws(()=>createChatServer({agentUrl}));
  let called=false;const {url}=await fixture(t,(_,res)=>{called=true;res.end();});
  const response=await fetch(url+'/api//example.com/secret');
  assert.equal(response.status,400);assert.equal(called,false);
});
