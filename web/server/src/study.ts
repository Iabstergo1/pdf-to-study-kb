import type { FastifyInstance } from 'fastify';
import type { AppConfig } from './config.ts';
import { pipeline } from './engine/process.ts';
import { z } from 'zod';

async function command(c: AppConfig, name: string, action: string, value: unknown) {
  try { return JSON.parse(await pipeline(c, [name, '--action', action, '--stdin'], undefined, undefined, value)); }
  catch (error) {
    const message = String(error).trim().split('\n').at(-1) || '';
    if (message.startsWith('ValueError:')) throw Object.assign(new Error(message.slice(11).trim()), { statusCode: 400 });
    throw error;
  }
}
export async function readerInfo(c: AppConfig, action: string, value: unknown = {}) {
  return command(c, 'reader-info', action, value);
}

export function registerStudy(app: FastifyInstance, c: AppConfig) {
  app.get('/api/content-review/:action', async request => {
    const action = z.enum(['status', 'checks']).parse((request.params as any).action);
    return command(c, 'content-review', action, request.query);
  });
  app.post('/api/content-review/:action', async request => {
    const action = z.enum(['accept', 'close']).parse((request.params as any).action);
    const value = z.record(z.string(), z.unknown()).parse(request.body);
    return command(c, 'content-review', action, value);
  });
  app.post('/api/reader/render', async request => readerInfo(c, 'render', z.object({ text: z.string().max(2_000_000) }).parse(request.body)));
  app.get('/api/study/:action', async (request, reply) => {
    const action = z.enum(['summary', 'cards', 'state', 'annotations', 'history', 'export']).parse((request.params as any).action);
    const value = await command(c, 'study-read', action, request.query);
    if (action === 'export') reply.header('Content-Disposition', 'attachment; filename="study-records.json"');
    return value;
  });
  // Personal records do not acquire the vault lock or occupy the model job slot.
  app.post('/api/study/:action', { bodyLimit: 64 * 1024 * 1024 }, async request => {
    const action = z.enum(['review', 'undo', 'preferences', 'progress', 'session', 'annotation', 'flag', 'delete-annotation', 'import', 'proposal-decision']).parse((request.params as any).action);
    const body = z.record(z.string(), z.unknown()).parse(request.body);
    return command(c, 'study-write', action, body);
  });
  app.get('/api/reader/info/:action', async request => {
    const action = z.enum(['reports', 'review', 'evidence', 'source-page', 'proposals', 'freshness', 'preferences']).parse((request.params as any).action);
    return readerInfo(c, action, request.query);
  });
  app.get('/api/reader/source-image', async (request, reply) => {
    const value = z.object({ source: z.string().min(1).max(128), page: z.coerce.number().int().positive() }).parse(request.query);
    const image = await readerInfo(c, 'source-image', value);
    if (!image.available) return reply.code(404).send({ error: 'No source image is available' });
    return reply.type('image/png').send(Buffer.from(image.data, 'base64'));
  });
}
