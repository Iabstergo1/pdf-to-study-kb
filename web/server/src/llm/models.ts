import path from 'node:path';
import fs from 'node:fs';
import { randomUUID, createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { AsyncLocalStorage } from 'node:async_hooks';
import { z } from 'zod';
import { ModelRuntime, readStoredCredential } from '@earendil-works/pi-coding-agent';
import { getSupportedThinkingLevels, type ModelThinkingLevel } from '@earendil-works/pi-ai';
import { readJson, saveJson, redact, type AppConfig } from '../config.ts';

const modelSchema = z.object({ id: z.string().min(1).max(200), name: z.string().optional(), vision: z.boolean(), reasoning: z.boolean().default(false), contextWindow: z.number().int().positive().default(128000), maxTokens: z.number().int().positive().default(8192) });
export const providerSchema = z.object({
  id: z.string().regex(/^[a-z0-9][a-z0-9-]{0,63}$/), name: z.string().min(1).max(100),
  kind: z.enum(['subscription', 'apikey', 'custom']), provider: z.string().min(1),
  apiKey: z.string().optional(), baseUrl: z.url().optional(),
  api: z.enum(['openai-completions', 'openai-responses', 'anthropic-messages', 'google-generative-ai']).optional(),
  models: z.array(modelSchema).default([]),
  compat: z.object({ supportsStore: z.boolean().optional(), supportsDeveloperRole: z.boolean().optional(), supportsReasoningEffort: z.boolean().optional(), maxTokensField: z.enum(['max_tokens', 'max_completion_tokens']).optional() }).optional(),
}).refine(p => p.kind !== 'subscription' || p.provider === 'openai', 'Only ChatGPT subscriptions are supported')
  .refine(p => p.kind !== 'custom' || p.baseUrl && p.api && p.models.length, 'Custom providers need URL, protocol and models');
export type Provider = Omit<z.infer<typeof providerSchema>, 'apiKey'>;
export const selectionSchema = z.object({ provider: z.string(), model: z.string(), thinkingLevel: z.enum(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']).optional() });
const transientNetworkCodes = new Set(['ECONNRESET', 'ECONNREFUSED', 'ECONNABORTED', 'EPIPE', 'ETIMEDOUT', 'EHOSTUNREACH', 'ENETUNREACH', 'EAI_AGAIN', 'ENOTFOUND', 'UND_ERR_SOCKET', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT']);
const diagnosticCodes = new Set([...transientNetworkCodes, 'UND_ERR_BODY_TIMEOUT', 'ABORT_ERR', 'CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'SELF_SIGNED_CERT_IN_CHAIN', 'ERR_TLS_CERT_ALTNAME_INVALID']);
function networkCodes(error: unknown) {
  const codes = new Set<string>(); const pending: any[] = [error]; const seen = new Set();
  while (pending.length && seen.size < 16) {
    const item = pending.shift(); if (!item || typeof item !== 'object' || seen.has(item)) continue;
    seen.add(item); if (diagnosticCodes.has(item.code)) codes.add(item.code);
    if (item.name === 'TimeoutError') codes.add('ETIMEDOUT');
    if (item.cause) pending.push(item.cause);
    if (Array.isArray(item.errors)) pending.push(...item.errors.slice(0, 8));
  }
  return codes;
}
export function recoverableTransportError(message: string) {
  if (/\b(?:400|401|403|429)\b|(?:HTTP|API error|status(?: code)?)[\s:(]*[345]\d\d\b|quota|usage.limit|rate.limit|invalid.grant|invalid.refresh|refresh.token|invalid.client|unauthorized|forbidden|context.{0,30}(?:length|window|limit|overflow|exceed)|too.many.tokens|maximum.{0,20}tokens|certificate|\bTLS\b/i.test(message)) return false;
  return /\bterminated\b|\bECONN(?:RESET|REFUSED|ABORTED)\b|\bEPIPE\b|\bETIMEDOUT\b|\bUND_ERR_(?:SOCKET|CONNECT_TIMEOUT|HEADERS_TIMEOUT|BODY_TIMEOUT)\b|socket|fetch failed|connection (?:error|closed|reset)|network (?:error|interruption|connection)|request timed?\s*out/i.test(message);
}
type NetworkScope = { log: (text: string) => void; signal?: AbortSignal; onStop?: () => void };
const accountNetwork = new AsyncLocalStorage<NetworkScope>();
let accountFetchUsers = 0; let restoreAccountFetch: (() => void) | undefined;
async function withAccountNetwork<T>(scope: NetworkScope, work: () => Promise<T>): Promise<T> {
  if (accountFetchUsers++ === 0) {
    const original = globalThis.fetch;
    const wrapped: typeof fetch = (input, init) => {
      const context = accountNetwork.getStore();
      const url = input instanceof Request ? input.url : String(input);
      if (!context || !['https://auth.openai.com/api/accounts/oauth/token', 'https://api.openai.com/v1/models'].includes(url)) return original(input, init);
      return networkFetch(context.log, original, 15000, { ...context, sdkTimeout: true, finishRefresh: url.startsWith('https://auth.openai.com/') })(input, init);
    };
    globalThis.fetch = wrapped;
    restoreAccountFetch = () => { if (globalThis.fetch === wrapped) globalThis.fetch = original; };
  }
  try { return await accountNetwork.run(scope, work); }
  catch (error) {
    // Pi includes the token endpoint's response body in its error. Never expose that body.
    const status = String(error).match(/OpenAI OAuth token request failed \((\d{3})\)/)?.[1];
    if (status) throw new Error(`OAuth refresh failed for openai: HTTP ${status}`);
    throw error;
  }
  finally { if (--accountFetchUsers === 0) { restoreAccountFetch?.(); restoreAccountFetch = undefined; } }
}
export function networkFetch(log: (text: string) => void, request: typeof fetch = globalThis.fetch, timeoutMs?: number, options: { signal?: AbortSignal; sdkTimeout?: boolean; finishRefresh?: boolean; onStop?: () => void } = {}): typeof fetch {
  return async (input, init) => {
    const incoming = init?.signal || (input instanceof Request ? input.signal : undefined);
    const cancel = new AbortController();
    // Only the two controlled account endpoints use Pi's internal 15s signal.
    // Replace its timeout with per-attempt deadlines, but preserve actual cancellation.
    const forward = () => { if (incoming?.aborted && (!options.sdkTimeout || incoming.reason?.name !== 'TimeoutError')) cancel.abort(incoming.reason); };
    incoming?.addEventListener('abort', forward, { once: true }); forward();
    const signal = options.signal ? AbortSignal.any([options.signal, cancel.signal]) : cancel.signal;
    const cleanup = () => incoming?.removeEventListener('abort', forward);
    const host = new URL(input instanceof Request ? input.url : String(input)).hostname;
    const replayable = !(input instanceof Request && input.body) && (init?.body == null || typeof init.body === 'string' || init.body instanceof URLSearchParams);
    if (init?.body instanceof URLSearchParams) init = { ...init, body: new URLSearchParams(init.body) };
    let streaming = false;
    try {
      for (let attempt = 0; ; attempt++) {
        signal?.throwIfAborted();
        const started = Date.now();
        const deadline = timeoutMs === undefined ? undefined : new AbortController();
        const timer = deadline ? setTimeout(() => deadline.abort(new DOMException('Request timed out.', 'TimeoutError')), timeoutMs) : undefined;
        const finish = () => { clearTimeout(timer); cleanup(); };
        let headersReceived = false;
        try {
          let response: Response;
          // Pi races caller cancellation outside its credential lock. Let an already sent
          // refresh finish and save rotated tokens; cancellation still forbids another attempt.
          const requestSignal = options.finishRefresh ? cancel.signal : signal;
          try { response = await request(input, { ...init, signal: deadline ? AbortSignal.any([requestSignal, deadline.signal]) : requestSignal }); headersReceived = true; }
          finally { if (!options.sdkTimeout || !headersReceived) clearTimeout(timer); }
          if (!response.ok) options.onStop?.();
          if (!response.body) { finish(); return response; }
          const reader = response.body.getReader();
          const body = new ReadableStream<Uint8Array>({
            async pull(controller) {
              try {
                const chunk = await reader.read();
                if (chunk.done) { finish(); reader.releaseLock(); controller.close(); }
                else controller.enqueue(chunk.value);
              } catch (error) {
                finish(); reader.releaseLock();
                // Account requests only retry before headers; a partial token response
                // must not be retried indirectly by the model's stream recovery.
                if (options.sdkTimeout) options.onStop?.();
                if (!signal.aborted) log(`[network] host=${host} stream_code=${[...networkCodes(error)].join(',') || 'UNKNOWN'} elapsed_ms=${Date.now() - started}\n`);
                controller.error(error);
              }
            },
            async cancel(reason) { finish(); try { await reader.cancel(reason); } finally { reader.releaseLock(); } },
          });
          const wrapped = new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
          for (const key of ['url', 'redirected', 'type'] as const) Object.defineProperty(wrapped, key, { value: response[key] });
          streaming = true; return wrapped;
        }
        catch (error) {
          clearTimeout(timer);
          if (signal?.aborted) throw error;
          const codes = networkCodes(error);
          if (deadline?.signal.aborted) { codes.delete('ABORT_ERR'); codes.add('ETIMEDOUT'); }
          const retry = replayable && codes.size > 0 && [...codes].every(code => transientNetworkCodes.has(code)) && attempt < 2;
          log(`[network] host=${host} code=${[...codes].join(',') || 'UNKNOWN'} elapsed_ms=${Date.now() - started} attempt=${attempt + 1}/3 ${retry ? `retry=${attempt + 1}/2 wait_ms=${1000 * (attempt + 1)}` : 'stopped'}\n`);
          if (!retry) { options.onStop?.(); throw error; }
          await delay(1000 * (attempt + 1), undefined, { signal: signal || undefined });
        }
      }
    } finally { if (!streaming) cleanup(); }
  };
}
export class Models {
  providers: Provider[];
  private keys: Record<string, string>;
  private runtimes = new Map<string, Promise<ModelRuntime>>();
  private verified: Record<string, { at: string; pass: boolean }>;
  private accountModels = new Map<string, { key: string; at: number; models: { slug: string; display_name?: string; visibility: string }[] }>();
  loginState: { state: string; url?: string; code?: string; message?: string; prompt?: string } = { state: 'idle' };
  private answer?: (text: string) => void;
  private c: AppConfig;
  constructor(c: AppConfig) {
    this.c = c;
    this.providers = readJson(path.join(c.config, 'custom-providers.json'), [{ id: 'chatgpt', name: 'ChatGPT', kind: 'subscription', provider: 'openai', models: [] }]);
    this.keys = readJson(path.join(c.config, 'auth-api.json'), {});
    this.verified = readJson(path.join(c.config, 'vision.json'), {});
  }
  clean = (s: string) => redact(s, Object.values(this.keys));
  handleNetwork(rt: ModelRuntime, log: (text: string) => void) {
    const original = rt.streamSimple;
    const originalAuth = rt.getAuth;
    let requestStopped = false;
    const onStop = () => { requestStopped = true; };
    const getAuth = originalAuth.bind(rt);
    rt.getAuth = (provider, overrides = {}) => withAccountNetwork(accountNetwork.getStore() || { log, signal: overrides.signal, onStop }, () => typeof provider === 'string' ? getAuth(provider, overrides) : getAuth(provider, overrides));
    rt.streamSimple = (model, context, options) => {
      requestStopped = false;
      if (model.api !== 'openai-responses') return original.call(rt, model, context, options);
      const timeoutMs = options?.timeoutMs ?? 600_000;
      // Each fetch attempt owns its deadline; the SDK must allow all three attempts and backoff.
      return original.call(rt, model, context, { ...options, maxRetries: 0,
        timeoutMs: Math.min(timeoutMs * 3 + 4000, 2147483647), fetch: networkFetch(log, options?.fetch, timeoutMs, { onStop }) });
    };
    return Object.assign(() => { rt.streamSimple = original; rt.getAuth = originalAuth; }, { canRecover: () => !requestStopped });
  }
  cleanMessage(message: any) {
    const cleanValue = (v: any): any => typeof v === 'string' ? this.clean(v) : Array.isArray(v) ? v.map(cleanValue) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, value]) => [k, cleanValue(value)])) : v;
    const cleaned = cleanValue(message);
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      message.content.forEach((block: any, i: number) => {
        // Provider replay signatures are opaque: redacting a coincidental sk- substring corrupts them.
        const key = block.type === 'thinking' ? 'thinkingSignature' : block.type === 'text' ? 'textSignature' : block.type === 'toolCall' ? 'thoughtSignature' : '';
        if (key && typeof block[key] === 'string') cleaned.content[i][key] = block[key];
      });
    }
    return cleaned;
  }
  public() { return this.providers.map(p => ({ ...p, configured: p.kind === 'subscription' ? !!readStoredCredential(p.provider, this.authFile(p)) : !!this.keys[p.id] })); }
  find(id: string) { const p = this.providers.find(p => p.id === id); if (!p) throw new Error('Unknown provider'); return p; }
  private authFile(p: Provider) { return path.join(this.c.config, p.id === 'chatgpt' ? 'auth-chatgpt.json' : `auth-${p.id}.json`); }
  save(value: unknown) {
    const { apiKey, ...p } = providerSchema.parse(value);
    if (p.baseUrl && !/^https?:\/\//.test(p.baseUrl)) throw new Error('Use an HTTP(S) endpoint');
    if (apiKey) this.keys[p.id] = apiKey;
    this.providers = [...this.providers.filter(old => old.id !== p.id), p];
    saveJson(path.join(this.c.config, 'auth-api.json'), this.keys);
    saveJson(path.join(this.c.config, 'custom-providers.json'), this.providers);
    this.runtimes.delete(p.id);
    this.accountModels.delete(p.id);
    for (const key of Object.keys(this.verified)) if (key.startsWith(p.id + '/')) delete this.verified[key];
    saveJson(path.join(this.c.config, 'vision.json'), this.verified);
    return this.public();
  }
  runtime(id: string) {
    if (!this.runtimes.has(id)) this.runtimes.set(id, this.create(this.find(id)));
    return this.runtimes.get(id)!;
  }
  private async create(p: Provider) {
    fs.mkdirSync(this.c.config, { recursive: true });
    const rt = await ModelRuntime.create({ authPath: this.authFile(p), modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
    if (p.kind === 'custom') rt.registerProvider(p.provider, {
      name: p.name, baseUrl: p.baseUrl, api: p.api as never, apiKey: this.keys[p.id],
      models: p.models.map(m => ({ ...m, name: m.name || m.id, api: p.api as never, input: m.vision ? ['text', 'image'] : ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, compat: p.compat })),
    });
    if (p.kind !== 'subscription' && this.keys[p.id]) await rt.setRuntimeApiKey(p.provider, this.keys[p.id]);
    return rt;
  }
  async catalogue() {
    const rt = await this.runtime('chatgpt');
    return rt.getProviders().map(p => ({ id: p.id, name: p.name }));
  }
  async list(id: string, refresh = false, network: NetworkScope = { log: text => console.warn(text.trimEnd()) }) {
    const p = this.find(id); const rt = await this.runtime(id);
    const known = rt.getModels(p.provider);
    const describe = (m: typeof known[number]) => ({ id: m.id, name: m.name, vision: m.input.includes('image'), contextWindow: m.contextWindow, verified: this.isVerified(id, m.id), supported: true, thinkingLevels: getSupportedThinkingLevels(m), thinkingLevel: this.savedThinking(id, m.id, getSupportedThinkingLevels(m)), catalogue: p.kind === 'subscription' ? 'account' : 'runtime' });
    if (p.kind !== 'subscription') return known.map(describe);
    if (!readStoredCredential(p.provider, this.authFile(p))) return [];
    const key = this.verificationKey(id, 'catalogue');
    let cached = this.accountModels.get(id);
    if (refresh || !cached || cached.key !== key || Date.now() - cached.at > 300000) {
      const data = await withAccountNetwork(network, async () => {
        const credential = await rt.getAuth(p.provider, { signal: network.signal });
        if (!credential?.auth.apiKey) throw new Error('Sign in to ChatGPT before loading account models');
        const response = await fetch('https://api.openai.com/v1/models', { headers: { Authorization: `Bearer ${credential.auth.apiKey}` }, signal: AbortSignal.timeout(15000), redirect: 'error' });
        if (!response.ok) throw new Error(`Account model listing: HTTP ${response.status}`);
        return z.object({ models: z.array(z.object({ slug: z.string(), display_name: z.string().optional(), visibility: z.string() })) }).parse(await response.json());
      });
      cached = { key, at: Date.now(), models: data.models.filter(m => m.visibility === 'list') };
      this.accountModels.set(id, cached);
    }
    return cached.models.map(entry => {
      const m = known.find(m => m.id === entry.slug);
      return m ? { ...describe(m), name: entry.display_name || m.name } : { id: entry.slug, name: entry.display_name || entry.slug, supported: false, vision: false, verified: false, contextWindow: null, thinkingLevels: [], thinkingLevel: 'off', catalogue: 'account' };
    });
  }
  private savedThinking(id: string, model: string, levels: ModelThinkingLevel[]) {
    const settings = readJson<any>(path.join(this.c.config, 'settings.json'), {});
    const saved = settings.thinking?.[id]?.[model];
    return levels.includes(saved) ? saved : levels.includes('low') ? 'low' : levels[0];
  }
  async thinking(id: string, model: string, requested?: ModelThinkingLevel) {
    const rt = await this.runtime(id); const m = rt.getModel(this.find(id).provider, model);
    if (!m) throw new Error('Unknown model');
    const levels = getSupportedThinkingLevels(m);
    if (requested !== undefined && !levels.includes(requested)) throw Object.assign(new Error(`Unsupported thinking level: ${requested}. Supported: ${levels.join(', ')}`), { statusCode: 400 });
    return requested ?? this.savedThinking(id, model, levels);
  }
  async select(value: z.infer<typeof selectionSchema>) {
    const thinkingLevel = await this.thinking(value.provider, value.model, value.thinkingLevel);
    const file = path.join(this.c.config, 'settings.json'); const settings = readJson<any>(file, {});
    settings.thinking = { ...settings.thinking, [value.provider]: { ...settings.thinking?.[value.provider], [value.model]: thinkingLevel } };
    saveJson(file, settings); saveJson(path.join(this.c.config, 'selection.json'), { ...value, thinkingLevel });
  }
  async model(id: string, model: string, requireVision = true, network?: NetworkScope) {
    const rt = await this.runtime(id); const m = rt.getModel(this.find(id).provider, model);
    if (!m) throw new Error('Unknown model');
    if (requireVision && !m.input.includes('image')) throw new Error('Choose an image-capable model');
    if (this.find(id).kind === 'subscription' && !(await this.list(id, false, network)).some(m => m.id === model && m.supported)) throw new Error('This model is not listed for the signed-in account. Refresh Models & settings.');
    return { rt, model: m };
  }
  private verificationKey(id: string, model: string) {
    const p = this.find(id);
    const credential = p.kind === 'subscription' ? readStoredCredential(p.provider, this.authFile(p)) : undefined;
    // ChatGPT issues a clientId per login; refresh keeps it while rotating tokens.
    const identity = credential?.type === 'oauth' && typeof credential.clientId === 'string'
      ? `oauth-client:${credential.clientId}` : credential?.type === 'oauth' && typeof credential.accountId === 'string'
        ? `oauth:${credential.accountId}` : p.kind === 'subscription' ? JSON.stringify(credential) : this.keys[id] || '';
    return `${id}/${model}/${createHash('sha256').update(JSON.stringify(p) + identity).digest('hex')}`;
  }
  isVerified(id: string, model: string) { return this.verified[this.verificationKey(id, model)]?.pass === true; }
  mark(id: string, model: string, pass: boolean) {
    this.verified[this.verificationKey(id, model)] = { at: new Date().toISOString(), pass };
    saveJson(path.join(this.c.config, 'vision.json'), this.verified);
  }
  async fetchModels(id: string) {
    const p = this.find(id);
    if (!p.baseUrl) throw new Error('This provider uses the built-in catalogue');
    const res = await fetch(p.baseUrl.replace(/\/$/, '') + '/models', { headers: { Authorization: `Bearer ${this.keys[id] || ''}`, 'x-api-key': this.keys[id] || '' }, signal: AbortSignal.timeout(15000), redirect: 'error' });
    if (!res.ok) throw new Error(`Model listing: HTTP ${res.status}`);
    const data = await res.json() as { data?: { id: string }[] };
    return (data.data || []).map(m => ({ id: m.id }));
  }
  async login(id: string) {
    if (this.loginState.state === 'pending') throw new Error('Login already running');
    if (this.find(id).kind !== 'subscription') throw new Error('Not a subscription provider');
    this.loginState = { state: 'pending' };
    const rt = await this.runtime(id);
    const deviceFile = path.join(this.c.config, 'device-id');
    if (!fs.existsSync(deviceFile)) fs.writeFileSync(deviceFile, randomUUID(), { mode: 0o600 });
    try {
      await rt.login('openai', 'oauth', {
        notify: (ev: any) => {
          if (ev.type === 'auth_url') this.loginState.url = ev.url;
          else if (ev.type === 'device_code') { this.loginState.url = ev.verificationUri; this.loginState.code = ev.userCode; }
        },
        prompt: p => new Promise<string>((resolve, reject) => {
          this.loginState.prompt = p.message; this.answer = resolve;
          p.signal?.addEventListener('abort', () => { this.answer = undefined; reject(new Error('Login prompt closed')); }, { once: true });
        }),
      }, { getDeviceId: () => fs.readFileSync(deviceFile, 'utf8').trim() });
      for (const key of Object.keys(this.verified)) if (key.startsWith(id + '/')) delete this.verified[key];
      saveJson(path.join(this.c.config, 'vision.json'), this.verified);
      this.accountModels.delete(id);
      this.loginState = { state: 'succeeded' };
    } catch (e) { this.loginState = { state: 'failed', message: this.clean(String(e)) }; }
    finally { this.answer = undefined; }
  }
  loginAnswer(text: string) { if (!this.answer) throw new Error('No login prompt'); this.answer(text); this.answer = undefined; }
}
