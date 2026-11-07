import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createServer } from 'node:http';
import { test, expect, vi } from 'vitest';
import { createApp } from '../src/app.ts';
import { repo, type AppConfig } from '../src/config.ts';
import { pipeline } from '../src/engine/process.ts';
import { Models } from '../src/llm/models.ts';

vi.mock('node:crypto', async importOriginal => ({ ...await importOriginal<typeof import('node:crypto')>(), randomInt: () => 4821 }));
const headers = { host: '127.0.0.1:8765', 'x-console-token': 'test-token' };
type Reply = { text: string } | { tool: string; args: any };
async function done(jobs: any, id: string) {
  const job = jobs.history.find((j: any) => j.id === id);
  if (job.status === 'running') await new Promise<void>(resolve => { const listen = (v: any) => { if (v === null) { jobs.off(id, listen); resolve(); } }; jobs.on(id, listen); });
  return job;
}
test.each(['recover', 'exhaust', 'cancel', 'stream'])('Responses header deadline: %s', async scenario => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-timeout-test-'));
  const c: AppConfig = { root, repo, config: path.join(root, 'private'), python: 'unused', port: 8765, token: 'test-token' };
  const bodies: string[] = []; const logs: string[] = []; const controller = new AbortController();
  const server = createServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk; bodies.push(body);
    if (scenario === 'cancel') { controller.abort(); return; }
    if (scenario !== 'stream' && (scenario !== 'recover' || bodies.length < 3)) return; // Leave the first requests waiting for headers.
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (scenario === 'stream') { response.flushHeaders(); await new Promise(resolve => setTimeout(resolve, 300)); }
    response.end('data: ' + JSON.stringify({ type: 'response.completed', response: { id: 'resp_timeout', status: 'completed', output: [], usage: { input_tokens: 1, output_tokens: 0, total_tokens: 1 } } }) + '\n\n');
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const models = new Models(c);
  models.save({ id: 'test', name: 'Test', kind: 'custom', provider: 'test', api: 'openai-responses', baseUrl: `http://127.0.0.1:${(server.address() as any).port}`, apiKey: 'fake-timeout-key', models: [{ id: 'writer', vision: true }] });
  const { rt, model } = await models.model('test', 'writer'); const restore = models.handleNetwork(rt, s => logs.push(s));
  try {
    const result = await rt.completeSimple(model, { messages: [{ role: 'user', content: 'test', timestamp: Date.now() }] }, { timeoutMs: 100, signal: controller.signal });
    expect(result.stopReason, result.errorMessage).toBe(['recover', 'stream'].includes(scenario) ? 'stop' : scenario === 'cancel' ? 'aborted' : 'error');
    expect(bodies).toHaveLength(['cancel', 'stream'].includes(scenario) ? 1 : 3);
    expect(new Set(bodies).size).toBe(1);
    expect(restore.canRecover()).toBe(scenario !== 'exhaust');
    if (['cancel', 'stream'].includes(scenario)) expect(logs).toHaveLength(0);
    else {
      expect(logs.join('')).toContain('code=ETIMEDOUT');
      expect(logs.join('')).toContain('retry=2/2');
      if (scenario === 'exhaust') { expect(result.errorMessage).toContain('timed out'); expect(logs.at(-1)).toContain('attempt=3/3 stopped'); }
    }
  } finally { restore(); server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
}, 15000);
for (const protocol of ['openai-completions', 'anthropic-messages', 'openai-responses']) test(`${protocol}: custom settings, discovery, images and real CLI publication`, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-protocol-test-'));
  for (const dir of ['templates', 'docs']) fs.symlinkSync(path.join(repo, dir), path.join(root, dir), 'junction');
  const c: AppConfig = { root, repo, config: path.join(root, 'private'), python: process.env.STUDY_KB_PYTHON || 'python', port: 8765, token: 'test-token' };
  let replies: Reply[] = []; const requests: any[] = [];
  const encrypted = 'gAAAA_fixture_sk-0123456789abcdef==';
  const interruptedBodies: string[] = []; let disconnect = false; let permanentDisconnect = false; let failedRequests = 0; let streamFailures = 0;
  const server = createServer(async (request, response) => {
    if (request.url?.endsWith('/models')) { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ data: [{ id: 'writer' }] })); return; }
    let bytes = ''; for await (const chunk of request) bytes += chunk;
    requests.push(JSON.parse(bytes));
    if (permanentDisconnect) { failedRequests++; request.socket.destroy(); return; }
    if (disconnect && requests.at(-1).input?.some((i: any) => i.type === 'function_call_output' && String(i.output).includes('[OK] ingesting'))) {
      if (interruptedBodies.length < 3) interruptedBodies.push(bytes);
      if (interruptedBodies.length < 3) { request.socket.destroy(); return; }
      if (streamFailures < 2) {
        streamFailures++;
        response.writeHead(200, { 'Content-Type': 'text/event-stream' });
        const event = (type: string, payload: any) => response.write(`data: ${JSON.stringify({ type, ...payload })}\n\n`);
        event('response.created', { response: { id: 'resp_broken' } });
        for (let i = 0; i < 5; i++) {
          const item = { id: 'rs_broken_' + i, type: 'reasoning', summary: [{ type: 'summary_text', text: 'unfinished-fixture' }], encrypted_content: 'broken-signature' };
          event('response.output_item.added', { output_index: i, item: { ...item, encrypted_content: null } });
          if (i < 4) event('response.output_item.done', { output_index: i, item });
          else event('response.reasoning_summary_text.delta', { output_index: i, summary_index: 0, delta: 'unfinished-fixture' });
        }
        // Even a fully assembled tool call in an errored response must not execute.
        const tool = { id: 'fc_broken', type: 'function_call', call_id: 'call_broken', name: 'write', arguments: JSON.stringify({ path: 'pipeline-workspace/staging/demo/partial-must-not-exist.md', content: 'must not execute' }), status: 'completed' };
        event('response.output_item.added', { output_index: 5, item: tool });
        event('response.output_item.done', { output_index: 5, item: tool });
        setTimeout(() => response.destroy(), 30); return;
      }
    }
    const item = replies.shift() || { text: 'No scripted response remains' };
    if (protocol === 'openai-responses' && requests.at(-1).input.some((i: any) => i.type === 'reasoning' && i.encrypted_content !== encrypted)) {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Encrypted content could not be decrypted or parsed.', type: 'invalid_request_error', code: 'invalid_encrypted_content' } })); return;
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (protocol === 'openai-completions') {
      const delta = 'tool' in item ? { role: 'assistant', tool_calls: [{ index: 0, id: 'call-' + requests.length, type: 'function', function: { name: item.tool, arguments: JSON.stringify(item.args) } }] } : { role: 'assistant', content: item.text };
      response.write(`data: ${JSON.stringify({ id: 'chat-' + requests.length, object: 'chat.completion.chunk', model: 'writer', choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`);
      response.write(`data: ${JSON.stringify({ id: 'chat-' + requests.length, object: 'chat.completion.chunk', model: 'writer', choices: [{ index: 0, delta: {}, finish_reason: 'tool' in item ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 } })}\n\ndata: [DONE]\n\n`);
    } else if (protocol === 'openai-responses') {
      const event = (type: string, payload: any) => response.write(`data: ${JSON.stringify({ type, ...payload })}\n\n`);
      const reasoning = { id: 'rs_' + requests.length, type: 'reasoning', summary: [], encrypted_content: encrypted };
      const output = 'tool' in item
        ? { id: 'fc_' + requests.length, type: 'function_call', call_id: 'call_' + requests.length, name: item.tool, arguments: JSON.stringify(item.args), status: 'completed' }
        : { id: 'msg_' + requests.length, type: 'message', role: 'assistant', content: [{ type: 'output_text', text: item.text, annotations: [] }], status: 'completed' };
      event('response.created', { response: { id: 'resp_' + requests.length } });
      event('response.output_item.added', { output_index: 0, item: { ...reasoning, encrypted_content: null } });
      event('response.output_item.done', { output_index: 0, item: reasoning });
      event('response.output_item.added', { output_index: 1, item: output });
      event('response.output_item.done', { output_index: 1, item: output });
      event('response.completed', { response: { id: 'resp_' + requests.length, status: 'completed', output: [reasoning, output], usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } });
    } else {
      const event = (type: string, payload: any) => response.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...payload })}\n\n`);
      event('message_start', { message: { id: 'msg-' + requests.length, type: 'message', role: 'assistant', model: 'writer', content: [], stop_reason: null, usage: { input_tokens: 10, output_tokens: 0 } } });
      event('content_block_start', { index: 0, content_block: 'tool' in item ? { type: 'tool_use', id: 'tool-' + requests.length, name: item.tool, input: {} } : { type: 'text', text: '' } });
      event('content_block_delta', { index: 0, delta: 'tool' in item ? { type: 'input_json_delta', partial_json: JSON.stringify(item.args) } : { type: 'text_delta', text: item.text } });
      event('content_block_stop', { index: 0 });
      event('message_delta', { delta: { stop_reason: 'tool' in item ? 'tool_use' : 'end_turn', stop_sequence: null }, usage: { output_tokens: 10 } });
      event('message_stop', {});
    }
    response.end();
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as any).port;
  const { app, jobs, models } = await createApp(c);
  const post = (url: string, payload: any) => app.inject({ method: 'POST', url, headers, payload });
  try {
    const saved = await post('/api/providers', { id: 'custom', name: protocol, kind: 'custom', provider: 'custom', api: protocol, baseUrl: `http://127.0.0.1:${port}`, apiKey: 'custom-key-never-in-logs', models: [{ id: 'writer', vision: true }] });
    expect(saved.statusCode, saved.body).toBe(200);
    expect((await post('/api/providers/custom/fetch-models', {})).json()).toEqual([{ id: 'writer' }]);
    replies = Array.from({ length: 3 }, () => [{ tool: 'read', args: { path: 'check.png' } }, { text: '4821' }] as Reply[]).flat();
    const v = await post('/api/vision', { provider: 'custom', model: 'writer' });
    const vj = await done(jobs, v.json().id); expect(vj.status, jobs.log(vj.id)).toBe('succeeded');
    expect(requests.filter(r => /image_url|"type":"image"/.test(JSON.stringify(r.messages || r.input))).length).toBe(3);
    fs.mkdirSync(path.join(root, 'books/demo/input'), { recursive: true });
    fs.writeFileSync(path.join(root, 'books/demo/input/book.md'), '两个人的选择互相影响，各自偏好的结果可能不同。', 'utf8');
    await pipeline(c, ['init-vault']);
    const originalPurpose = '# 我的学习目标\n\n这些已有内容必须原样保留。\n';
    fs.writeFileSync(path.join(root, 'wiki/_meta/purpose.md'), originalPurpose);
    const prepared = await post('/api/library/prepare', { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo/input/book.md', purpose: '保留原目标，并补充理解选择互动。' });
    const pj = await done(jobs, prepared.json().id); expect(pj.status, jobs.log(pj.id)).toBe('succeeded');
    const purpose = fs.readFileSync(path.join(root, 'wiki/_meta/purpose.md'), 'utf8');
    expect(purpose).toBe(originalPurpose);
    expect(fs.readFileSync(path.join(root, 'pipeline-workspace/staging/demo/learning-goal.md'), 'utf8')).toBe('保留原目标，并补充理解选择互动。\n');
    expect(vj.progress.tokens.total).toBeGreaterThan(0);
    const w = JSON.parse(fs.readFileSync(path.join(root, 'pipeline-workspace/staging/demo/windows.jsonl'), 'utf8').trim());
    const run = (...args: string[]): Reply => ({ tool: 'pipeline', args: { args } });
    replies = [run('ingest-start', '--source', 'demo'), run('show-window', '--source', 'demo', '--window', w.window_id), run('window-start', '--source', 'demo', '--window', w.window_id, '--hash', `${w.char_start}:${w.char_end}`),
      { tool: 'write', args: { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择互动\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两个人的选择互相影响，各自偏好的结果可能不同。' } },
      { tool: 'write', args: { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [全文]\n---\n[[sources/demo|选择互动]] 是本库当前的来源。' } },
      run('window-done', '--source', 'demo', '--window', w.window_id, '--writes', '["sources/demo.md","overview.md"]'),
      { tool: 'write', args: { path: 'pipeline-workspace/staging/demo/digest.md', content: '## DONE\n全部窗口完成，待发布检查。' } },
      run('ingest-done', '--source', 'demo'), run('lint', '--source', 'demo'), { text: 'published, pending content acceptance' }];
    disconnect = protocol === 'openai-responses';
    const started = await post('/api/ingest', { source: 'demo', provider: 'custom', model: 'writer', mode: 'start' });
    const ij = await done(jobs, started.json().id); expect(ij.status, jobs.log(ij.id)).toBe('succeeded');
    const state = (await app.inject({ url: '/api/status', headers })).json(); expect(state.sources[0].current_status).toBe('published');
    if (protocol === 'openai-responses') {
      expect(interruptedBodies).toHaveLength(3);
      expect(new Set(interruptedBodies).size).toBe(1);
      expect(jobs.log(ij.id).match(/\[ingest-start\]/g)).toHaveLength(1);
      expect(jobs.log(ij.id)).toContain('[network]');
      expect(streamFailures).toBe(2);
      expect(jobs.log(ij.id).match(/\[network-recovery\]/g)).toHaveLength(2);
      expect(jobs.log(ij.id)).toContain('stream_code=UND_ERR_SOCKET');
      expect(fs.existsSync(path.join(root, 'pipeline-workspace/staging/demo/partial-must-not-exist.md'))).toBe(false);
      expect(requests.every(r => !JSON.stringify(r.input).includes('broken-signature') && !JSON.stringify(r.input).includes('call_broken'))).toBe(true);
      const saved = fs.readdirSync(path.join(root, 'pipeline-workspace/web-sessions/demo')).flatMap(file => fs.readFileSync(path.join(root, 'pipeline-workspace/web-sessions/demo', file), 'utf8').trim().split('\n').map(line => JSON.parse(line)));
      const broken = saved.filter(entry => entry.message?.stopReason === 'error' && entry.message.responseId === 'resp_broken');
      expect(broken).toHaveLength(2);
      expect(broken[0].message.content.filter((b: any) => b.type === 'thinking' && b.thinkingSignature)).toHaveLength(4);
      expect(broken[0].message.content.filter((b: any) => b.type === 'thinking' && !b.thinkingSignature)).toHaveLength(1);
      // Reopen a persisted skill session as well as continuing tool calls in the ingest session.
      replies = [{ tool: 'read', args: { path: 'wiki/sources/demo.md' } }, { text: '已读取来源。custom-key-never-in-logs' }];
      const skillInput = { skill: 'wiki-lint-semantic', request: '检查来源', source: 'demo', provider: 'custom', model: 'writer' };
      const first = await post('/api/skills/run', skillInput); const firstJob = await done(jobs, first.json().id);
      expect(firstJob.status, jobs.log(firstJob.id)).toBe('succeeded');
      replies = [{ text: '继续核对。' }];
      const resumed = await post('/api/skills/run', { ...skillInput, previous: firstJob.id }); const resumedJob = await done(jobs, resumed.json().id);
      expect(resumedJob.status, jobs.log(resumedJob.id)).toBe('succeeded');
      const replayed = requests.at(-1).input;
      expect(replayed.filter((i: any) => i.type === 'reasoning').length).toBeGreaterThan(0);
      expect(JSON.stringify(replayed)).not.toContain('custom-key-never-in-logs');
      expect(JSON.stringify(replayed)).toContain('[redacted]');
      permanentDisconnect = true;
      const failed = await post('/api/skills/run', { ...skillInput, previous: resumedJob.id }); const failedJob = await done(jobs, failed.json().id);
      expect(failedJob.status, jobs.log(failedJob.id)).toBe('failed');
      expect(failedRequests).toBe(3);
      expect(jobs.log(failedJob.id)).toContain('retry=2/2');
      expect(jobs.log(failedJob.id)).toContain('attempt=3/3 stopped');
      expect(jobs.log(failedJob.id)).toContain('Connection error');
      permanentDisconnect = false;
    }
    const files = fs.readdirSync(path.join(root, 'pipeline-workspace'), { recursive: true, withFileTypes: true }).filter(e => e.isFile() && /\.(log|jsonl)$/.test(e.name));
    for (const f of files) expect(fs.readFileSync(path.join(f.parentPath, f.name), 'utf8')).not.toContain('custom-key-never-in-logs');
    replies = [{ tool: 'read', args: { path: 'check.png' } }, { text: '0000' }];
    const wrong = await post('/api/vision', { provider: 'custom', model: 'writer' });
    expect((await done(jobs, wrong.json().id)).status).toBe('failed'); expect(models.isVerified('custom', 'writer')).toBe(false);
  } finally { await app.close(); await new Promise<void>(resolve => server.close(() => resolve())); }
}, 120000);
