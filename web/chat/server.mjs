import http from 'node:http';
import https from 'node:https';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Transform } from 'node:stream';
import { codeLanguages } from './public/highlight-languages.js';

const publicDirectory = new URL('./public/', import.meta.url);
const files = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/chat.css', ['chat.css', 'text/css; charset=utf-8']],
  ['/chat.js', ['chat.js', 'text/javascript; charset=utf-8']],
  ['/chat-render.js', ['chat-render.js', 'text/javascript; charset=utf-8']],
  ['/theme.js', ['theme.js', 'text/javascript; charset=utf-8']],
  ['/highlighter.js', ['highlighter.js', 'text/javascript; charset=utf-8']],
  ['/highlight-languages.js', ['highlight-languages.js', 'text/javascript; charset=utf-8']],
]);
for (const path of ['core.min.js',...codeLanguages.map(name=>'languages/'+name+'.min.js')]) {
  files.set('/vendor/highlight/'+path,['vendor/highlight/'+path,'text/javascript; charset=utf-8']);
}
files.set('/vendor/highlight/LICENSE',['vendor/highlight/LICENSE','text/plain; charset=utf-8']);
const hopHeaders = new Set(['connection', 'keep-alive', 'proxy-authenticate',
  'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']);
const forwardedHeaders = ['authorization', 'content-type', 'content-length',
  'accept', 'range', 'if-range', 'if-none-match'];
const proxyMethods = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']);

function securityHeaders(response) {
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Referrer-Policy', 'no-referrer');
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
}

function problem(response, status, detail) {
  if (response.destroyed || response.writableEnded) return;
  if (response.headersSent) { response.destroy(); return; }
  for (const name of ['Content-Length', 'Content-Encoding', 'Content-Disposition', 'Location']) response.removeHeader(name);
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify({ detail }));
}

function backendOrigin(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('AGENTAI_URL must be an HTTP(S) origin.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('AGENTAI_URL must be an HTTP(S) origin without credentials or a path.');
  }
  return url;
}

/** Create an isolated server; importing this module never starts a listener. */
export function createChatServer({
  agentUrl = process.env.AGENTAI_URL || 'http://127.0.0.1:8000',
  maxBodyBytes = 28_000_000,
  proxyTimeoutMs = 600_000,
} = {}) {
  const backend = backendOrigin(agentUrl);
  if (!Number.isSafeInteger(maxBodyBytes) || maxBodyBytes < 1 ||
      !Number.isSafeInteger(proxyTimeoutMs) || proxyTimeoutMs < 1) {
    throw new Error('Request limits must be positive integers.');
  }
  const transport = backend.protocol === 'https:' ? https : http;
  const upstreams = new Set();
  // A fresh keep-alive agent belongs to this server, not to unrelated code.
  const agent = new transport.Agent({ keepAlive: true, maxSockets: 32, maxFreeSockets: 8 });

  function proxy(request, response) {
    const path = request.url.slice(4); // Strip /api, retain the raw path and query.
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
      problem(response, 400, 'Некорректный путь API.'); return;
    }
    if (!proxyMethods.has(request.method)) {
      response.setHeader('Allow', [...proxyMethods].join(', '));
      problem(response, 405, 'Метод не поддерживается.'); return;
    }
    if (Number(request.headers['content-length']) > maxBodyBytes) {
      response.setHeader('Connection', 'close');
      problem(response, 413, 'Размер запроса превышает 28 МБ.'); request.resume(); return;
    }
    const headers = {};
    for (const name of forwardedHeaders) {
      if (request.headers[name] !== undefined) headers[name] = request.headers[name];
    }
    let bytes = 0;
    const limiter = new Transform({
      transform(chunk, encoding, callback) {
        bytes += chunk.length;
        if (bytes > maxBodyBytes) callback(new Error('BODY_LIMIT'));
        else callback(null, chunk);
      },
    });
    const upstream = transport.request(backend, { method: request.method, path, headers, agent });
    upstreams.add(upstream);
    let finished = false;
    const deadline = setTimeout(() => fail(504, 'AgentAI не ответил вовремя. Повторите запрос.'), proxyTimeoutMs);
    deadline.unref();

    function release() { clearTimeout(deadline); upstreams.delete(upstream); }
    function fail(status, message) {
      if (finished) return;
      finished = true; release();
      request.unpipe(limiter); limiter.unpipe(upstream);
      upstream.destroy(); limiter.destroy(); request.resume();
      problem(response, status, message);
    }
    limiter.on('error', () => {
      if (finished) return;
      if (!response.headersSent) response.setHeader('Connection', 'close');
      fail(413, 'Размер запроса превышает 28 МБ.');
    });
    upstream.on('error', () => fail(502, 'Нет соединения с AgentAI. Проверьте сервер и повторите запрос.'));
    upstream.on('response', incoming => {
      if (finished) { incoming.destroy(); return; }
      const excluded = new Set(hopHeaders);
      for (const name of String(incoming.headers.connection || '').split(',')) excluded.add(name.trim().toLowerCase());
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (!excluded.has(name) && value !== undefined) response.setHeader(name, value);
      }
      // JSON, authorization failures and PDF bytes retain the backend's status.
      response.statusCode = incoming.statusCode || 502;
      response.setHeader('Cache-Control', 'no-store');
      incoming.on('error', () => fail(502, 'Ответ AgentAI прерван. Повторите запрос.'));
      incoming.on('end', () => { finished = true; release(); });
      incoming.pipe(response);
    });
    request.on('aborted', () => { finished = true; release(); limiter.destroy(); upstream.destroy(); });
    request.on('error', () => fail(400, 'Передача запроса прервана.'));
    response.on('close', () => {
      if (!finished) { finished = true; release(); request.unpipe(limiter); limiter.destroy(); upstream.destroy(); }
    });
    request.pipe(limiter).pipe(upstream);
  }

  const server = http.createServer({ maxHeaderSize: 16_384, requestTimeout: 650_000 }, (request, response) => {
    securityHeaders(response);
    if (!request.url?.startsWith('/') || request.url.startsWith('//')) {
      problem(response, 400, 'Некорректный адрес.'); return;
    }
    if (request.url.startsWith('/api/')) { proxy(request, response); return; }
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.setHeader('Allow', 'GET, HEAD'); problem(response, 405, 'Метод не поддерживается.'); return;
    }
    let pathname;
    try { pathname = new URL(request.url, 'http://localhost').pathname; }
    catch { problem(response, 400, 'Некорректный адрес.'); return; }
    if (pathname === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(request.method === 'HEAD' ? undefined : JSON.stringify({ status: 'alive', service: 'agentai-chat' })); return;
    }
    if (pathname === '/error') { response.writeHead(303, { Location: '/' }); response.end(); return; }
    if (pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
    const asset = files.get(pathname);
    if (!asset) { problem(response, 404, 'Страница не найдена.'); return; }
    readFile(fileURLToPath(new URL(asset[0], publicDirectory))).then(data => {
      if (response.destroyed) return;
      response.writeHead(200, { 'Content-Type': asset[1], 'Content-Length': data.length,
        'Cache-Control': pathname === '/' || pathname === '/index.html' ? 'no-store' : 'no-cache' });
      response.end(request.method === 'HEAD' ? undefined : data);
    }).catch(() => problem(response, 500, 'Не удалось загрузить интерфейс. Перезапустите чат.'));
  });
  server.headersTimeout = 20_000;
  server.on('close', () => { for (const upstream of upstreams) upstream.destroy(); agent.destroy(); });
  return server;
}
