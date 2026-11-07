import { timingSafeEqual } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
import type { AppConfig } from './config.ts';

export function localSecurity(app: FastifyInstance, c: AppConfig) {
  const hosts = new Set([`127.0.0.1:${c.port}`, `localhost:${c.port}`]);
  const cookie = `study_kb_${c.port}`;
  app.addHook('onRequest', async (request, reply) => {
    reply.header('Referrer-Policy', 'no-referrer').header('X-Content-Type-Options', 'nosniff').header('Cache-Control', 'no-store');
    reply.header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'self'; base-uri 'none'; object-src 'none'");
    const host = request.headers.host || '';
    const origin = request.headers.origin;
    if (!hosts.has(host) || origin && !hosts.has(origin.replace(/^http:\/\//, '')) || request.headers['sec-fetch-site'] === 'cross-site') return reply.code(403).send({ error: 'Local requests only' });
    if (!request.url.startsWith('/api/')) return;
    const value = request.headers['x-console-token'] || request.headers.cookie?.split(';').map(s => s.trim()).find(s => s.startsWith(cookie + '='))?.slice(cookie.length + 1) || '';
    const supplied = Buffer.from(String(value)); const expected = Buffer.from(c.token);
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return reply.code(403).send({ error: 'Open the launch link to connect' });
  });
  app.post('/api/session', async (_request, reply) => {
    reply.header('Set-Cookie', `${cookie}=${c.token}; HttpOnly; SameSite=Strict; Path=/api/`);
    return { ok: true };
  });
}
