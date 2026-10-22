import { createChatServer } from './server.mjs';
import { loadSessionKey } from './session.mjs';
import { fileURLToPath } from 'node:url';

const port = Number(process.env.PORT || 3000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535.');
const sessionKey = await loadSessionKey(process.env.CHAT_SESSION_KEY_FILE || fileURLToPath(new URL('../../data/chat-session.key', import.meta.url)));
const server = createChatServer({ sessionKey });
server.on('error', error => {
  // Never log URLs, headers, keys, request bodies or provider error payloads.
  console.error(JSON.stringify({ event: 'chat_server_error', code: error.code || 'SERVER_ERROR' }));
  process.exitCode = 1;
});
server.listen(port, process.env.HOST || '127.0.0.1', () => {
  console.log(JSON.stringify({ event: 'chat_listening', port, runtime: process.version }));
});
let stopping = false;
function shutdown() {
  if (stopping) return;
  stopping = true;
  const deadline = setTimeout(() => server.closeAllConnections(), 10_000);
  deadline.unref();
  server.close(() => { clearTimeout(deadline); });
  server.closeIdleConnections();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
