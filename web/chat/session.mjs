import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

export const sessionCookie = 'agentai_session';
export const sessionSeconds = 30 * 24 * 60 * 60;

export async function loadSessionKey(path) {
  await mkdir(dirname(path), { recursive: true });
  try { await writeFile(path, randomBytes(32), { flag: 'wx', mode: 0o600 }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  const key = await readFile(path);
  if (key.length !== 32) throw Error('Invalid chat session key file');
  return key;
}

export function createSessions(key = randomBytes(32), now = Date.now) {
  const aad = Buffer.from('agentai-chat-session-v1');
  return {
    issue(token) {
      const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', key, iv);
      cipher.setAAD(aad);
      const data = Buffer.from(JSON.stringify({ token, expires: now() + sessionSeconds * 1000 }));
      return Buffer.concat([iv, cipher.update(data), cipher.final(), cipher.getAuthTag()]).toString('base64url');
    },
    read(cookies = '') {
      try {
        const value = cookies.split(';').map(item => item.trim()).find(item => item.startsWith(sessionCookie + '='))?.slice(sessionCookie.length + 1);
        if (!value || value.length > 4000) return null;
        const data = Buffer.from(value, 'base64url');
        if (data.length < 29) return null;
        const decipher = createDecipheriv('aes-256-gcm', key, data.subarray(0, 12), { authTagLength: 16 });
        decipher.setAAD(aad); decipher.setAuthTag(data.subarray(-16));
        const decoded = JSON.parse(Buffer.concat([decipher.update(data.subarray(12, -16)), decipher.final()]).toString());
        return typeof decoded.token === 'string' && decoded.expires > now() ? decoded.token : null;
      } catch { return null; }
    },
  };
}

export function cookieHeader(value, secure = false) {
  return `${sessionCookie}=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${value ? sessionSeconds : 0}${secure ? '; Secure' : ''}`;
}
export function sessionScope(token) { return createHash('sha256').update(token).digest('hex').slice(0, 24); }
