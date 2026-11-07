import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import { afterEach, test, expect, vi } from 'vitest';
import { createApp } from '../src/app.ts';
import { repo, redact, type AppConfig } from '../src/config.ts';
import { buildArgs } from '../src/engine/commands.ts';
import { checkArgs, checkPath } from '../src/ingest/guard.ts';
import { Jobs } from '../src/engine/jobs.ts';
import { commands } from '../src/engine/commands.ts';
import { pipeline } from '../src/engine/process.ts';
import { z } from 'zod';
import ts from 'typescript';
import { messages } from '../../client/src/i18n.tsx';
import { Models, networkFetch, recoverableTransportError } from '../src/llm/models.ts';

const close: (() => Promise<unknown>)[] = [];
afterEach(async () => { for (const fn of close.splice(0)) await fn(); });
function config(): AppConfig {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-kb-web-test-'));
  return { repo, root, config: path.join(root, 'private'), python: process.env.STUDY_KB_PYTHON || 'python', port: 8765, token: 'test-token' };
}

test.each([false, true])('environment check detects a missing FSRS dependency: %s', async missing => {
  const c = config();
  if (missing) fs.writeFileSync(path.join(c.root, 'fsrs.py'), "raise ModuleNotFoundError(\"No module named 'fsrs'\")\n");
  vi.stubEnv('PYTHONPATH', c.root);
  try {
    const { app } = await createApp(c); close.push(() => app.close());
    const response = await app.inject({ url: '/api/doctor', headers: { host: '127.0.0.1:8765', 'x-console-token': c.token } });
    expect(response.statusCode).toBe(200);
    const python = response.json().find((check: any) => check.id === 'python');
    expect(python.ok).toBe(!missing);
    if (missing) {
      expect(python.detail).toContain("No module named 'fsrs'");
      expect(python.fix).toContain('pip install -r requirements.txt');
    }
  } finally { vi.unstubAllEnvs(); }
});
test('session redaction preserves provider replay signatures without exempting text or tool arguments', () => {
  const models = new Models(config());
  const signature = 'gAAAA_fixture_sk-0123456789abcdef==';
  const secret = 'sk-secret123456789';
  const message = { role: 'assistant', content: [
    { type: 'thinking', thinking: secret, thinkingSignature: signature },
    { type: 'text', text: secret, textSignature: signature },
    { type: 'toolCall', name: 'write', thoughtSignature: signature, arguments: { content: secret, type: 'thinking', thinkingSignature: secret } },
  ] };
  const cleaned = models.cleanMessage(message);
  expect(cleaned.content[0]).toEqual({ type: 'thinking', thinking: '[redacted]', thinkingSignature: signature });
  expect(cleaned.content[1]).toEqual({ type: 'text', text: '[redacted]', textSignature: signature });
  expect(cleaned.content[2].thoughtSignature).toBe(signature);
  expect(cleaned.content[2].arguments).toEqual({ content: '[redacted]', type: 'thinking', thinkingSignature: '[redacted]' });
  expect(message.content[0].thinking).toBe(secret);
  expect(models.cleanMessage({ role: 'user', content: message.content }).content[0].thinkingSignature).not.toContain('sk-');
});

test('network retries stop at three attempts and log only host, codes and timings', async () => {
  let calls = 0; const logs: string[] = [];
  const error = new TypeError('fetch failed secret-in-message', { cause: Object.assign(new Error('Bearer private-token'), { code: 'ECONNRESET' }) });
  const request = networkFetch(s => logs.push(s), async () => { calls++; throw error; });
  await expect(request('https://user:password@example.com/private-token?key=secret', { method: 'POST', body: 'private-source', headers: { Authorization: 'Bearer private-token' } })).rejects.toBe(error);
  expect(calls).toBe(3); expect(logs).toHaveLength(3);
  expect(logs.join('')).toContain('code=ECONNRESET'); expect(logs[2]).toContain('stopped');
  expect(logs.join('')).not.toMatch(/password|private|secret|Bearer/);
});

test.each([400, 401, 403, 429, 500])('HTTP %s responses are not retried by the network handler', async status => {
  let calls = 0; let stopped = false;
  const request = networkFetch(() => { throw new Error('No network failure to log'); }, async () => { calls++; return new Response('', { status }); }, undefined, { onStop: () => { stopped = true; } });
  expect((await request('https://example.com')).status).toBe(status); expect(calls).toBe(1);
  expect(stopped).toBe(true);
});

test('TLS errors and unknown failures are diagnosed without retry', async () => {
  for (const code of ['CERT_HAS_EXPIRED', 'UND_ERR_INVALID_ARG']) {
    let calls = 0; const logs: string[] = [];
    const error = Object.assign(new Error('private details'), { code });
    const request = networkFetch(s => logs.push(s), async () => { calls++; throw error; });
    await expect(request('https://example.com')).rejects.toBe(error);
    expect(calls).toBe(1); expect(logs[0]).toContain(code === 'CERT_HAS_EXPIRED' ? code : 'UNKNOWN');
  }
});

test('cancel interrupts the network retry wait without another request', async () => {
  const controller = new AbortController(); let calls = 0;
  const request = networkFetch(() => controller.abort(), async () => { calls++; throw Object.assign(new Error('reset'), { code: 'ECONNRESET' }); });
  await expect(request('https://example.com', { signal: controller.signal })).rejects.toThrow();
  expect(calls).toBe(1);
});

test('a response stream failure is not replayed after response headers arrive', async () => {
  let calls = 0; const logs: string[] = [];
  const error = new TypeError('terminated secret', { cause: Object.assign(new Error('private socket details'), { code: 'UND_ERR_SOCKET' }) });
  const request = networkFetch(s => logs.push(s), async () => { calls++; return new Response(new ReadableStream({ start(c) { c.error(error); } })); });
  const response = await request('https://example.com');
  await expect(response.text()).rejects.toBe(error); expect(calls).toBe(1);
  expect(logs).toHaveLength(1); expect(logs[0]).toMatch(/host=example.com stream_code=UND_ERR_SOCKET elapsed_ms=\d+/);
  expect(logs[0]).not.toMatch(/secret|private/);
});

test('stream diagnostics preserve response metadata and bytes and forward consumer cancellation', async () => {
  let cancelled = false;
  const original = new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array([0, 255, 10])); }, cancel() { cancelled = true; } }), { status: 201, statusText: 'Created', headers: { 'x-test': 'kept' } });
  Object.defineProperty(original, 'url', { value: 'https://example.com/response' });
  const response = await networkFetch(() => { throw new Error('Not a failure'); }, async () => original)('https://example.com');
  expect(response.status).toBe(201); expect(response.statusText).toBe('Created'); expect(response.url).toBe(original.url); expect(response.headers.get('x-test')).toBe('kept');
  const reader = response.body!.getReader(); expect(Array.from((await reader.read()).value!)).toEqual([0, 255, 10]);
  await reader.cancel(); expect(cancelled).toBe(true);
});

test.each(['HTTP 500: socket unavailable', '401 connection error', '403 fetch failed', '429 quota exceeded socket', 'invalid_grant network error', 'context_length_exceeded socket', 'maximum context length exceeded: terminated', 'TLS certificate expired: socket'])('terminal error does not enter transport recovery: %s', message => {
  expect(recoverableTransportError(message)).toBe(false);
});

test.each(['Request timed out.', 'terminated', 'ECONNRESET', 'socket hang up', 'OAuth refresh failed for openai: fetch failed'])('transport error is classified as network: %s', async message => {
  const jobs = new Jobs(config(), s => s);
  const job = jobs.start('ingest', async () => { throw new Error(message); });
  await new Promise<void>(resolve => jobs.on(job.id, value => { if (value === null) resolve(); }));
  expect(job.status).toBe('failed'); expect(job.error_kind).toBe('network');
});

test('URLSearchParams retries replay the same form without logging its credentials', async () => {
  const bodies: string[] = []; const logs: string[] = [];
  const request = networkFetch(s => logs.push(s), async (_input, init) => {
    bodies.push(String(init?.body));
    if (bodies.length < 3) throw new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } });
    return new Response('ok');
  });
  const form = new URLSearchParams({ refresh_token: 'secret-refresh', client_id: 'secret-client' });
  expect(await (await request('https://auth.openai.com/api/accounts/oauth/token', { method: 'POST', body: form })).text()).toBe('ok');
  expect(bodies).toEqual([form.toString(), form.toString(), form.toString()]);
  expect(logs.join('')).not.toMatch(/secret|refresh_token|client_id|oauth\/token/);
});

test.each(['recover', 'exhaust', 'cancel'])('internal TimeoutError gets independent attempt deadlines: %s', async scenario => {
  const controller = new AbortController(); const logs: string[] = []; const signals: AbortSignal[] = [];
  const request = networkFetch(s => { logs.push(s); if (scenario === 'cancel') controller.abort(); }, async (_input, init) => {
    const signal = init!.signal!; signals.push(signal); expect(signal.aborted).toBe(false);
    if (scenario === 'recover' && signals.length === 3) return new Response('ok');
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  }, 15, { sdkTimeout: true, signal: controller.signal });
  const result = request('https://auth.openai.com/api/accounts/oauth/token', { body: new URLSearchParams({ refresh_token: 'secret' }), method: 'POST', signal: AbortSignal.timeout(5) });
  if (scenario === 'recover') expect(await (await result).text()).toBe('ok');
  else await expect(result).rejects.toThrow();
  expect(signals).toHaveLength(scenario === 'cancel' ? 1 : 3);
  expect(new Set(signals).size).toBe(signals.length);
  expect(logs.every(line => line.includes('code=ETIMEDOUT'))).toBe(true);
});

test.each(['recover', 'handler', 'exhaust', 'cancel', 400, 401, 403] as const)('real Pi OAuth refresh and account model listing: %s', async scenario => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  const file = path.join(c.config, 'auth-chatgpt.json');
  const originalCredential = JSON.stringify({ openai: { type: 'oauth', access: 'expired-secret', refresh: 'refresh-secret', expires: 1, clientId: 'client-secret', scopes: ['api.model.request'] } });
  fs.writeFileSync(file, originalCredential);
  const logs: string[] = []; const bodies: string[] = []; let listings = 0;
  const controller = new AbortController(); const originalFetch = globalThis.fetch;
  const stub: typeof fetch = async (input, init) => {
    const url = String(input);
    if (url === 'https://auth.openai.com/api/accounts/oauth/token') {
      bodies.push(String(init?.body));
      if (typeof scenario === 'number') return Response.json({ error: 'invalid_grant', detail: 'refresh-secret client-secret private-body' }, { status: scenario });
      if (bodies.length < 3 || !['recover', 'handler'].includes(String(scenario))) throw new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } });
      return Response.json({ access_token: 'renewed-access', refresh_token: 'renewed-refresh', expires_in: 3600, scope: 'chatgpt.tokens.use.direct' });
    }
    expect(url).toBe('https://api.openai.com/v1/models'); listings++;
    expect((init?.headers as any).Authorization).toBe('Bearer renewed-access');
    if (listings < 3) throw new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } });
    return Response.json({ models: [{ slug: 'gpt-6-luna', visibility: 'list' }] });
  };
  globalThis.fetch = stub;
  try {
    const models = new Models(c);
    if (scenario === 'handler') {
      const rt = await models.runtime('chatgpt'); const restore = models.handleNetwork(rt, s => logs.push(s));
      try { expect((await rt.getAuth('openai'))?.auth.apiKey).toBe('renewed-access'); }
      finally { restore(); }
    }
    const result = models.list('chatgpt', true, { signal: controller.signal, log: s => { logs.push(s); if (scenario === 'cancel') controller.abort(); } });
    if (scenario === 'recover' || scenario === 'handler') {
      expect((await result).map(m => m.id)).toEqual(['gpt-6-luna']); expect(listings).toBe(3);
      const saved = JSON.parse(fs.readFileSync(file, 'utf8')).openai;
      expect(saved.access).toBe('renewed-access'); expect(saved.refresh).toBe('renewed-refresh'); expect(saved.clientId).toBe('client-secret');
    } else {
      await expect(result).rejects.toThrow(typeof scenario === 'number' ? `HTTP ${scenario}` : undefined);
      expect(fs.readFileSync(file, 'utf8')).toBe(originalCredential); expect(listings).toBe(0);
    }
    expect(bodies).toHaveLength(typeof scenario === 'number' || scenario === 'cancel' ? 1 : 3);
    expect(new Set(bodies).size).toBe(1);
    expect(logs.join('')).not.toMatch(/secret|private-body|refresh_token|clientId|\/api\//);
    // Cancellation may return before the SDK releases its credential lock.
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(globalThis.fetch).toBe(stub);
  } finally { globalThis.fetch = originalFetch; }
}, 15000);

test('cancelling an in-flight OAuth refresh stops the caller but saves an already rotated credential', async () => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  const file = path.join(c.config, 'auth-chatgpt.json');
  fs.writeFileSync(file, JSON.stringify({ openai: { type: 'oauth', access: 'expired', refresh: 'old-refresh', expires: 1, clientId: 'client-secret', scopes: ['chatgpt.tokens.use.direct'] } }));
  const original = globalThis.fetch; const controller = new AbortController(); let calls = 0;
  let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void; const started = new Promise<void>(resolve => { entered = resolve; });
  globalThis.fetch = async (_input, init) => {
    calls++; entered(); await pending;
    expect(init?.signal?.aborted).toBe(false);
    return Response.json({ access_token: 'rotated-access', refresh_token: 'rotated-refresh', expires_in: 3600, scope: 'chatgpt.tokens.use.direct' });
  };
  const models = new Models(c); const rt = await models.runtime('chatgpt'); const restore = models.handleNetwork(rt, () => {});
  try {
    const auth = rt.getAuth('openai', { signal: controller.signal });
    const rejected = expect(auth).rejects.toThrow();
    await started; controller.abort(); await rejected;
    release();
    // The second read waits on the SDK credential lock, then sees the saved rotation.
    expect((await rt.getAuth('openai'))?.auth.apiKey).toBe('rotated-access');
    expect(JSON.parse(fs.readFileSync(file, 'utf8')).openai.refresh).toBe('rotated-refresh');
    expect(calls).toBe(1);
  } finally { release(); restore(); globalThis.fetch = original; }
});

test('a partial OAuth token response stops without retrying or replacing credentials', async () => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  const file = path.join(c.config, 'auth-chatgpt.json');
  const credential = JSON.stringify({ openai: { type: 'oauth', access: 'expired', refresh: 'unchanged', expires: 1, clientId: 'client-secret', scopes: ['chatgpt.tokens.use.direct'] } });
  fs.writeFileSync(file, credential);
  const original = globalThis.fetch; let calls = 0; const logs: string[] = [];
  globalThis.fetch = async () => {
    calls++;
    return new Response(new ReadableStream({ start(c) { c.error(new TypeError('terminated', { cause: { code: 'UND_ERR_SOCKET' } })); } }));
  };
  const models = new Models(c); const rt = await models.runtime('chatgpt'); const restore = models.handleNetwork(rt, s => logs.push(s));
  try {
    await expect(rt.getAuth('openai')).rejects.toThrow('terminated');
    expect(calls).toBe(1); expect(restore.canRecover()).toBe(false);
    expect(fs.readFileSync(file, 'utf8')).toBe(credential);
    expect(logs.join('')).toContain('host=auth.openai.com stream_code=UND_ERR_SOCKET');
  } finally { restore(); globalThis.fetch = original; }
});

test('concurrent account calls isolate retry logs and leave unrelated fetches untouched', async () => {
  const original = globalThis.fetch; const logs = [[], []] as string[][]; const counts = [0, 0];
  let outsideCalls = 0;
  const stub: typeof fetch = async (input, init) => {
    if (String(input) === 'https://example.com/unrelated') { outsideCalls++; return new Response('outside'); }
    expect(String(input)).toBe('https://api.openai.com/v1/models');
    const index = (init?.headers as any).Authorization.endsWith('access-0') ? 0 : 1;
    if (++counts[index] < 3) throw new TypeError('fetch failed', { cause: { code: index === 0 ? 'ECONNRESET' : 'ETIMEDOUT' } });
    return Response.json({ models: [] });
  };
  globalThis.fetch = stub;
  try {
    const requests = logs.map(async (log, index) => {
      const c = config(); fs.mkdirSync(c.config, { recursive: true });
      fs.writeFileSync(path.join(c.config, 'auth-chatgpt.json'), JSON.stringify({ openai: { type: 'oauth', access: 'access-' + index, refresh: 'refresh', expires: Date.now() + 3600000, clientId: 'test-' + index, scopes: ['chatgpt.tokens.use.direct'] } }));
      return new Models(c).list('chatgpt', true, { log: text => log.push(text) });
    });
    await new Promise(resolve => setTimeout(resolve, 100));
    expect(await (await fetch('https://example.com/unrelated')).text()).toBe('outside');
    await Promise.all(requests);
    expect(counts).toEqual([3, 3]); expect(outsideCalls).toBe(1);
    expect(logs[0].every(text => text.includes('code=ECONNRESET'))).toBe(true); expect(logs[0]).toHaveLength(2);
    expect(logs[1].every(text => text.includes('code=ETIMEDOUT'))).toBe(true); expect(logs[1]).toHaveLength(2);
    expect(globalThis.fetch).toBe(stub);
  } finally { globalThis.fetch = original; }
});

test('subscription image verification survives OAuth token refresh and service restart', () => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  const auth = path.join(c.config, 'auth-chatgpt.json');
  // pi 1.0.3 openai-chatgpt credentials have clientId/scopes, not accountId.
  const credential = { type: 'oauth', access: 'old-access', refresh: 'old-refresh', expires: 1000, clientId: 'issued-client', scopes: ['api.model.request'] };
  fs.writeFileSync(auth, JSON.stringify({ openai: credential }));
  const models = new Models(c);
  models.mark('chatgpt', 'gpt-6-luna', true);
  expect(models.isVerified('chatgpt', 'gpt-6-luna')).toBe(true);
  fs.writeFileSync(auth, JSON.stringify({ openai: { ...credential, access: 'new-access', refresh: 'new-refresh', expires: 2000 } }));
  expect(models.isVerified('chatgpt', 'gpt-6-luna')).toBe(true);
  expect(new Models(c).isVerified('chatgpt', 'gpt-6-luna')).toBe(true);
});
test('subscription image verification rejects other accounts/models and explicit relogin', async () => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  const auth = path.join(c.config, 'auth-chatgpt.json');
  const credential = { type: 'oauth', access: 'fake-access', refresh: 'fake-refresh', expires: 1000, clientId: 'first-login', scopes: ['api.model.request'] };
  fs.writeFileSync(auth, JSON.stringify({ openai: credential }));
  const models = new Models(c);
  models.mark('chatgpt', 'gpt-6-luna', true);
  expect(models.isVerified('chatgpt', 'other-model')).toBe(false);
  fs.writeFileSync(auth, JSON.stringify({ openai: { ...credential, clientId: 'other-login' } }));
  expect(models.isVerified('chatgpt', 'gpt-6-luna')).toBe(false);
  models.mark('chatgpt', 'gpt-6-luna', true);
  fs.unlinkSync(auth);
  expect(models.isVerified('chatgpt', 'gpt-6-luna')).toBe(false);
  fs.writeFileSync(auth, JSON.stringify({ openai: { ...credential, clientId: 'other-login' } }));
  expect(models.isVerified('chatgpt', 'gpt-6-luna')).toBe(true);
  models.runtime = async () => ({ login: async () => {} }) as any;
  await models.login('chatgpt');
  expect(new Models(c).isVerified('chatgpt', 'gpt-6-luna')).toBe(false);
});
test('opening a launch link in an existing tab replaces a missing or stale token and reconnects', async () => {
  const c = { ...config(), token: 'a'.repeat(64) };
  const { app } = await createApp(c); close.push(() => app.close());
  const code = ts.transpileModule(fs.readFileSync(path.join(repo, 'web/client/src/api.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  for (const previous of ['', 'b'.repeat(64)]) {
    const values = new Map([['study-kb-token', previous]]);
    const window = new EventTarget(); let reloads = 0;
    const location = { hash: '#doctor', reload: () => { reloads++; } };
    const client: any = {};
    vm.runInNewContext(code, { exports: client, window, location, FormData,
      sessionStorage: { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value) },
      history: { replaceState: (_state: unknown, _title: string, url: string) => { location.hash = new URL(url, 'http://127.0.0.1:8765').hash; } },
      fetch: async (url: string, options: any) => {
        const response = await app.inject({ method: options.method, url, headers: { host: '127.0.0.1:8765', ...options.headers }, payload: options.body });
        return { ok: response.statusCode === 200, json: () => response.json() };
      },
    });
    await expect(client.api('/session', {})).rejects.toThrow('Open the launch link to connect');
    location.hash = '#token=' + c.token; window.dispatchEvent(new Event('hashchange'));
    expect(values.get('study-kb-token')).toBe(c.token);
    expect(location.hash).toBe('#dashboard'); expect(reloads).toBe(1);
    await expect(client.api('/session', {})).resolves.toEqual({ ok: true });
    location.hash = '#library'; window.dispatchEvent(new Event('hashchange'));
    expect(reloads).toBe(1);
  }
});
test('local auth rejects rebinding, foreign Origin, missing token before routes', async () => {
  const { app } = await createApp(config()); close.push(() => app.close());
  for (const headers of [ {}, { host: 'evil.test:8765', 'x-console-token': 'test-token' }, { host: '127.0.0.1:8765', origin: 'https://evil.test', 'x-console-token': 'test-token' } ]) {
    expect((await app.inject({ url: '/api/jobs', headers })).statusCode).toBe(403);
  }
  const headers = { host: '127.0.0.1:8765', 'x-console-token': 'test-token' };
  expect((await app.inject({ url: '/api/jobs', headers })).json()).toEqual([]);
  const session = await app.inject({ method: 'POST', url: '/api/session', headers });
  expect(session.headers['set-cookie']).toContain('HttpOnly; SameSite=Strict');
  expect((await app.inject({ url: '/api/jobs', headers: { host: headers.host, cookie: String(session.headers['set-cookie']).split(';')[0] } })).statusCode).toBe(200);
});
test('CLI rejects extra flags and unconfirmed destructive actions', async () => {
  expect(() => buildArgs('retract-source', { source: '--force' })).toThrow();
  expect(() => buildArgs('status', { force: true })).toThrow();
  expect(() => buildArgs('unlock', {}, true)).toThrow();
  const { app } = await createApp(config()); close.push(() => app.close());
  const response = await app.inject({ method: 'POST', url: '/api/jobs', headers: { host: '127.0.0.1:8765', 'x-console-token': 'test-token' }, payload: { command: 'retract-source', params: { source: 'demo' }, apply: true, confirm: 'demo' } });
  expect(response.json().error).toContain('preview');
});
test('agent paths and arguments stay within one source', () => {
  const c = config();
  expect(() => checkPath('read', path.join(c.config, 'auth.json'), c, 'a')).toThrow('Private');
  expect(() => checkPath('read', path.resolve(c.repo, '../study-kb-test-outside.txt'), c, 'a')).toThrow();
  expect(() => checkPath('write', 'scripts/pipeline.py', c, 'a')).toThrow();
  expect(() => checkPath('write', 'pipeline-workspace/staging/b/digest.md', c, 'a')).toThrow();
  expect(checkPath('write', 'pipeline-workspace/staging/a/digest.md', c, 'a')).toContain('digest.md');
  for (const args of [['retract-source', '--source', 'a'], ['windows', '--source', 'b'], ['windows', '--source', 'a', '--dev-bypass'], ['lint', '--source', 'a', '--session', 'other'], ['window-done', '--source', 'a', '--writes-file', 'auth.json']]) expect(() => checkArgs(args, 'a', c)).toThrow();
  checkArgs(['window-done', '--source', 'a', '--window', 'w0000', '--writes', '[]'], 'a', c);
  expect(() => checkArgs(undefined, 'a', c)).toThrow('separate read tool');
  expect(() => checkArgs(['read', 'source.md'], 'a', c)).toThrow('separate tools');
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-outside-'));
  fs.symlinkSync(outside, path.join(c.root, 'wiki'), 'junction');
  expect(() => checkPath('write', 'wiki/escape.md', c, 'a')).toThrow('outside workspace');
});
test('single job, cancellation, persisted restart state and redacted logs', async () => {
  const c = config(); const secret = 'test-secret-123'; const jobs = new Jobs(c, s => redact(s, [secret]));
  const job = jobs.start('test', async (_j, signal, log) => { log(secret); await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve())); });
  expect(() => jobs.start('second', async () => {})).toThrow('Another job');
  expect(new Jobs(c, s => s).history[0].status).toBe('interrupted');
  expect(jobs.log(job.id)).not.toContain(secret);
  const finished = new Promise(resolve => jobs.on(job.id, value => { if (value === null) resolve(null); }));
  jobs.cancel(); await finished;
  expect(job.status).toBe('cancelled'); expect(jobs.active).toBeUndefined();
});
test('model key is never returned in settings or provider responses', async () => {
  const { app, models } = await createApp(config()); close.push(() => app.close());
  await models.runtime('chatgpt');
  expect(models.public().find(p => p.id === 'chatgpt')?.configured).toBe(false);
  const headers = { host: '127.0.0.1:8765', 'x-console-token': 'test-token' };
  const saved = await app.inject({ method: 'POST', url: '/api/providers', headers, payload: { id: 'test', name: 'test', kind: 'apikey', provider: 'openai', apiKey: 'private-key-value' } });
  expect(saved.statusCode).toBe(200); expect(saved.body).not.toContain('private-key-value');
  expect((await app.inject({ url: '/api/settings', headers })).body).not.toContain('private-key-value');
});

test('web command flags and enum choices exist in the real CLI', async () => {
  const c = config();
  for (const [name, spec] of Object.entries(commands)) {
    const help = await pipeline(c, [name, '--help']);
    const schema = z.toJSONSchema(z.object(spec.params));
    for (const [key, field] of Object.entries<any>(schema.properties || {})) {
      const flag = '--' + (key === 'ids' ? 'id' : key.replaceAll('_', '-'));
      expect(help, name + ': ' + flag).toContain(flag);
      if (field.enum) {
        const choices = help.match(new RegExp(flag + '\\s+\\{([^}]+)\\}'))?.[1].split(',');
        if (choices) for (const value of field.enum) expect(choices).toContain(value);
      }
    }
    for (const flag of spec.fixed || []) expect(help).toContain(flag);
    if (spec.kind === 'apply') expect(help).toContain('--apply');
  }
}, 60000);

test('bilingual help and UI text remain paired and help links resolve', () => {
  const zh = path.join(repo, 'docs/guide/zh'), en = path.join(repo, 'docs/guide/en');
  expect(fs.readdirSync(zh).sort()).toEqual(fs.readdirSync(en).sort());
  for (const file of fs.readdirSync(zh)) expect(fs.readFileSync(path.join(zh, file), 'utf8').match(/^#+ /gm)?.length).toBe(fs.readFileSync(path.join(en, file), 'utf8').match(/^#+ /gm)?.length);
  for (const pair of Object.values(messages)) { expect(pair).toHaveLength(2); expect(pair.every(v => v.trim())).toBe(true); }
  const allowed = new Set(['Study KB', 'k', 'HTML ↓', 'TSV ↓', '中 / EN', '/wiki']);
  const client = path.join(repo, 'web/client/src');
  for (const file of fs.readdirSync(client).filter(f => f.endsWith('.tsx') && f !== 'i18n.tsx')) {
    const text = fs.readFileSync(path.join(client, file), 'utf8');
    for (const match of text.matchAll(/#help\/([a-z-]+)/g)) expect(fs.existsSync(path.join(zh, match[1] + '.md')), file).toBe(true);
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = (node: ts.Node) => {
      if (ts.isJsxText(node)) { const value = node.text.trim(); if (/[a-z\p{Script=Han}]/iu.test(value)) expect(allowed.has(value), file + ': ' + value).toBe(true); }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
});
