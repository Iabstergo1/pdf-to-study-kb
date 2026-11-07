import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn, type ChildProcess } from 'node:child_process';
import { test, expect, vi } from 'vitest';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai';
import { createApp } from '../src/app.ts';
import { repo, type AppConfig } from '../src/config.ts';
import { pipeline } from '../src/engine/process.ts';
import * as processes from '../src/engine/process.ts';

vi.mock('node:crypto', async importOriginal => ({ ...await importOriginal<typeof import('node:crypto')>(), randomInt: () => 4821 }));
function setup(): AppConfig {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-ingest-web-test-'));
  for (const dir of ['templates', 'docs']) fs.symlinkSync(path.join(repo, dir), path.join(root, dir), 'junction');
  return { root, repo, config: path.join(root, 'private'), python: process.env.STUDY_KB_PYTHON || 'python', port: 8765, token: 'test-token' };
}
const headers = { host: '127.0.0.1:8765', 'x-console-token': 'test-token' };
const call = (name: string, args: any) => fauxAssistantMessage(fauxToolCall(name, args), { stopReason: 'toolUse' });
const cli = (args: string[]) => call('pipeline', { args });
async function completed(jobs: any, id: string) {
  const j = jobs.history.find((j: any) => j.id === id);
  if (j.status !== 'running') return j;
  await new Promise<void>(resolve => { const listener = (value: any) => { if (value === null) { jobs.off(id, listener); resolve(); } }; jobs.on(id, listener); });
  return j;
}

test.each(['normal', 'digest-first', 'blocked-next'] as const)('window handoff waits for U7 and the whole tool batch: %s', async ordering => {
  const c = setup(); let service = await createApp(c);
  const staging = path.join(c.root, 'pipeline-workspace/staging/demo');
  const digest = 'pipeline-workspace/staging/demo/digest.md';
  const sessions = () => fs.readdirSync(path.join(c.root, 'pipeline-workspace/web-sessions/demo')).filter(f => f.endsWith('.jsonl'));
  const imageCount = (context: any) => context.messages.flatMap((m: any) => Array.isArray(m.content) ? m.content : []).filter((b: any) => b.type === 'image').length;
  const register = async () => {
    const rt = await service.models.runtime('fake');
    const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] });
    rt.registerNativeProvider(faux.provider); return faux;
  };
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 前言\n选择问题。\n\n# 正文\n两个人的选择互相影响。');
    const prepared = await service.app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    expect((await completed(service.jobs, prepared.json().id)).status).toBe('succeeded');
    fs.writeFileSync(path.join(staging, 'proof.png'), Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAIAAAAC64paAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAAlUlEQVR4nGP4TwFgGAaaX716tWXLln///pGjubKyko2N7cCBAyRrBlqooqLS1dWVmppKsuYjR454enp++PBBQkLi169fpGnOyMhYvnw5kOHj47Np0yYSNAOtEhcXNzU1NTMzU1dXDw8PJ0Hz5s2b/fz8IGygywUFBb98+UKsZqBVq1atgosCDVq6dCmxmskDo5qHjGYAVq17t9UGIqUAAAAASUVORK5CYII=', 'base64'));
    service.models.save({ id: 'fake', name: 'Handoff', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); service.models.mark('fake', 'writer', true);
    let faux = await register();
    const interrupted = () => fauxAssistantMessage('', { stopReason: 'error', errorMessage: 'terminated' });
    faux.setResponses([
      cli(['ingest-start', '--source', 'demo']),
      ...(ordering === 'normal' ? [interrupted, interrupted] : []),
      call('read', { path: 'pipeline-workspace/staging/demo/windows.jsonl' }),
      context => {
        expect(JSON.stringify(context.messages.at(-1))).toContain('不要全量读取 windows.jsonl');
        return call('read', { path: 'pipeline-workspace/staging/demo/windows.jsonl', limit: 1 });
      },
      call('write', { path: digest, content: '## RESUME\n下一窗 w0000\n' }),
      cli(['show-window', '--source', 'demo', '--window', 'w0000']),
      cli(['window-start', '--source', 'demo', '--window', 'w0000', '--hash', 'fixture']),
      call('read', { path: 'pipeline-workspace/staging/demo/proof.png' }),
      ...(ordering === 'digest-first' ? [] : [cli(['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '[]'])]),
      context => {
        expect(sessions()).toHaveLength(1); expect(imageCount(context)).toBe(1);
        return cli(['next', '--source', 'demo', '--json']);
      },
      context => {
        const result = JSON.stringify(context.messages.at(-1));
        expect(result).toContain(ordering === 'digest-first' ? 'w0000' : 'w0001'); expect(result).toContain('sha256:');
        return fauxAssistantMessage([
          fauxToolCall('write', { path: process.platform === 'win32' ? path.join(c.root, digest).toLowerCase() : digest, content: '## RESUME\n下一窗 w0001\n已读前言：选择问题。交接保留事实。' }),
          ...(ordering === 'digest-first' ? [fauxToolCall('pipeline', { args: ['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '[]'] })] : []),
          fauxToolCall('pipeline', { args: ['next', '--source', 'demo', '--resume-packet'] }),
          ...(ordering === 'blocked-next' ? [fauxToolCall('pipeline', { args: ['window-start', '--source', 'demo', '--window', 'w0001', '--hash', 'fixture'] })] : []),
          fauxToolCall('read', { path: 'pipeline-workspace/staging/demo/proof.png' }),
        ], { stopReason: 'toolUse' });
      },
      context => {
        expect(sessions()).toHaveLength(2); expect(imageCount(context)).toBe(0);
        expect(JSON.stringify(context.messages)).toContain('交接保留事实');
        const old = sessions().map(f => fs.readFileSync(path.join(c.root, 'pipeline-workspace/web-sessions/demo', f), 'utf8')).find(t => t.includes('"type":"image"'))!;
        expect(old.match(/"type":"image"/g)).toHaveLength(2);
        return cli(['show-window', '--source', 'demo', '--window', 'w0001']);
      },
      cli(['window-start', '--source', 'demo', '--window', 'w0001', '--hash', 'fixture']),
      ...(ordering === 'normal' ? [interrupted, interrupted] : []),
      call('read', { path: 'pipeline-workspace/staging/demo/proof.png' }),
      call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [正文]\n---\n阅读 [[sources/demo|选择]]。' }),
      cli(['window-done', '--source', 'demo', '--window', 'w0001', '--writes', '["overview.md"]']),
      context => {
        expect(sessions()).toHaveLength(2); expect(imageCount(context)).toBe(1);
        return call('write', { path: digest, content: '## DONE\n两窗已读；综合层尚未完成。' });
      },
      context => {
        expect(sessions()).toHaveLength(3); expect(imageCount(context)).toBe(0);
        expect(JSON.stringify(context.messages)).toContain('phase=finish');
        expect(JSON.stringify(context.messages)).toContain('last_finished_writes=');
        return fauxAssistantMessage('', { stopReason: 'error', errorMessage: 'Network interruption before synthesis' });
      },
      ...Array.from({ length: 2 }, () => fauxAssistantMessage('', { stopReason: 'error', errorMessage: 'Network interruption before synthesis' })),
    ]);
    const started = await service.app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' } });
    const stopped = await completed(service.jobs, started.json().id);
    expect(stopped.message, service.jobs.log(stopped.id)).toContain('Network interruption before synthesis');
    expect(stopped.progress.windows.finished).toBe(2);
    expect(service.jobs.log(stopped.id).match(/\[handoff\]/g)).toHaveLength(2);
    expect(service.jobs.log(stopped.id).match(/\[network-recovery\]/g)).toHaveLength(ordering === 'normal' ? 6 : 2);
    const messages = sessions().flatMap(f => fs.readFileSync(path.join(c.root, 'pipeline-workspace/web-sessions/demo', f), 'utf8').trim().split('\n').map(line => JSON.parse(line)));
    for (const key of ['input', 'output', 'cacheRead', 'cacheWrite']) expect(stopped.progress.tokens[key]).toBe(messages.reduce((sum, entry) => sum + (entry.message?.usage?.[key] || 0), 0));
    await service.app.close(); service = await createApp(c); faux = await register();
    faux.setResponses([
      context => {
        const prompt = JSON.stringify(context.messages); expect(prompt).toContain('phase=finish');
        expect(prompt).toContain('last_finished_writes=[\\"overview.md\\"]');
        return call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两个人的选择互相影响。' });
      },
      call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [正文]\n---\n阅读 [[sources/demo|选择]]。' }),
      cli(['window-done', '--source', 'demo', '--window', 'w0001', '--writes', '["sources/demo.md","overview.md"]']),
      cli(['ingest-done', '--source', 'demo']), cli(['lint', '--source', 'demo']), fauxAssistantMessage('published'),
    ]);
    const resumed = await service.app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'resume' } });
    const result = await completed(service.jobs, resumed.json().id);
    expect(result.status, service.jobs.log(result.id)).toBe('succeeded');
    expect(result.progress.windows.finished).toBe(2); expect(sessions()).toHaveLength(4);
    expect(service.jobs.log(result.id)).not.toContain('[handoff]');
  } finally { await service.app.close(); }
}, 120000);

test.each(['missing-u7', 'unchanged-u7', 'invalid-packet', 'cancel', 'cancel-at-handoff'] as const)('handoff stops safely on %s', async scenario => {
  const c = setup(); const { app, jobs, models } = await createApp(c);
  const staging = path.join(c.root, 'pipeline-workspace/staging/demo');
  const digest = 'pipeline-workspace/staging/demo/digest.md';
  let reached!: () => void; const waiting = new Promise<void>(resolve => { reached = resolve; });
  let nextRequests = 0;
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 前言\n选择问题。\n\n# 正文\n两个人的选择互相影响。');
    const prepared = await app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    expect((await completed(jobs, prepared.json().id)).status).toBe('succeeded');
    models.save({ id: 'fake', name: 'Handoff refusal', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake'); const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    faux.setResponses([
      cli(['ingest-start', '--source', 'demo']),
      // Mentioning the next ID in advance must not count as performing U7 after window-done.
      call('write', { path: digest, content: '## RESUME\n当前 w0000，下一窗 w0001。' }),
      cli(['show-window', '--source', 'demo', '--window', 'w0000']),
      cli(['window-start', '--source', 'demo', '--window', 'w0000', '--hash', 'fixture']),
      cli(['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '[]']),
      async (_context, options) => {
        if (scenario === 'cancel') {
          reached(); await new Promise<void>(resolve => options?.signal?.addEventListener('abort', () => resolve(), { once: true }));
          return fauxAssistantMessage('Stopped');
        }
        if (scenario === 'invalid-packet') {
          fs.renameSync(path.join(staging, 'workorder.yaml'), path.join(staging, 'missing-workorder.yaml'));
          return call('write', { path: digest, content: '## RESUME\n下一窗 w0001，前言已读。' });
        }
        if (scenario === 'cancel-at-handoff') return call('write', { path: digest, content: '## RESUME\n下一窗 w0001，前言已读。' });
        if (scenario === 'unchanged-u7') return call('write', { path: digest, content: '## RESUME\n当前 w0000，下一窗 w0001。' });
        return cli(['next', '--source', 'demo', '--resume-packet']);
      },
      () => { nextRequests++; return cli(['window-start', '--source', 'demo', '--window', 'w0001', '--hash', 'fixture']); },
      context => { expect(JSON.stringify(context.messages.at(-1))).toContain('DIGEST_REPAIR_REQUIRED'); return fauxAssistantMessage('暂未更新摘要。'); },
      fauxAssistantMessage('暂未更新摘要。'),
    ]);
    const started = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' } });
    if (scenario === 'cancel-at-handoff') jobs.on(started.json().id, text => { if (text?.includes('[handoff]')) jobs.cancel(); });
    if (scenario === 'cancel') { await waiting; await app.inject({ method: 'POST', url: '/api/jobs/cancel', headers }); }
    const result = await completed(jobs, started.json().id);
    expect(result.status, jobs.log(result.id)).toBe(scenario.startsWith('cancel') ? 'cancelled' : 'failed');
    if (!scenario.startsWith('cancel')) expect(result.message).toContain(scenario === 'invalid-packet' ? 'workorder' : '摘要仍未完成核对');
    if (scenario === 'cancel-at-handoff') expect(nextRequests).toBe(0);
    else {
      expect(fs.readdirSync(path.join(c.root, 'pipeline-workspace/web-sessions/demo')).filter(f => f.endsWith('.jsonl'))).toHaveLength(1);
      expect(jobs.log(result.id)).not.toContain('[handoff]');
    }
    const next = JSON.parse(await pipeline(c, ['next', '--source', 'demo', '--json']));
    expect(next.finished).toBe(1); expect(next.next_window.window_id).toBe('w0001');
  } finally { await app.close(); }
}, 120000);

test.each(['same-session', 'restart', 'no-repair', 'broken-workorder'] as const)('stale digest recovery: %s', async scenario => {
  const c = setup(); let service = await createApp(c);
  const staging = path.join(c.root, 'pipeline-workspace/staging/demo');
  const digest = 'pipeline-workspace/staging/demo/digest.md';
  const digestText = (next: string) => `## RESUME\n下一窗 ${next}。\n\n## 阅读记录\n封面与目录不单独写知识页；保留正文中的选择问题。`;
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 封面\n选择问题。\n\n# 目录\n前言、正文。\n\n# 正文\n两个人的选择互相影响。');
    const prepared = await service.app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    expect((await completed(service.jobs, prepared.json().id)).status).toBe('succeeded');
    const wins = fs.readFileSync(path.join(staging, 'windows.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    expect(wins).toHaveLength(3);
    const interrupted = service.jobs.start('ingest', async () => { await pipeline(c, ['ingest-start', '--source', 'demo']); throw new Error('Fixture interruption'); }, 'demo');
    await completed(service.jobs, interrupted.id);
    fs.writeFileSync(path.join(c.root, digest), digestText('w0000'));
    if (scenario !== 'same-session') {
      for (const w of wins.slice(0, 2)) {
        await pipeline(c, ['show-window', '--source', 'demo', '--window', w.window_id]);
        await pipeline(c, ['window-start', '--source', 'demo', '--window', w.window_id, '--hash', 'fixture']);
        await pipeline(c, ['window-done', '--source', 'demo', '--window', w.window_id, '--writes', '[]']);
      }
      await expect(pipeline(c, ['next', '--source', 'demo', '--resume-packet'])).rejects.toThrow('下一窗 w0002');
      if (scenario === 'broken-workorder') fs.renameSync(path.join(staging, 'workorder.yaml'), path.join(staging, 'missing-workorder.yaml'));
      await service.app.close(); service = await createApp(c);
    }
    service.models.save({ id: 'fake', name: 'Digest recovery', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); service.models.mark('fake', 'writer', true);
    const rt = await service.models.runtime('fake'); const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    let replies = 0;
    const nextId = scenario === 'same-session' ? 'w0001' : 'w0002';
    const blocked = (context: any) => {
      expect(JSON.stringify(context.messages.at(-1))).toContain('DIGEST_REPAIR_REQUIRED');
      expect(fs.existsSync(path.join(c.root, 'wiki/sources/demo.md'))).toBe(false);
      return fauxAssistantMessage('需要人工更新摘要。');
    };
    faux.setResponses(scenario === 'no-repair' || scenario === 'broken-workorder'
      ? Array.from({ length: 4 }, () => () => { replies++; return fauxAssistantMessage('需要人工更新摘要。'); })
      : [
        ...(scenario === 'same-session' ? [
          cli(['show-window', '--source', 'demo', '--window', 'w0000']),
          cli(['window-start', '--source', 'demo', '--window', 'w0000', '--hash', 'fixture']),
          cli(['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '[]']),
        ] : []),
        // A stale digest must not permit new knowledge writes, even if the model ignores the warning.
        call('write', { path: 'wiki/sources/demo.md', content: 'This must never be written.' }),
        context => { expect(JSON.stringify(context.messages.at(-1))).toContain('DIGEST_REPAIR_REQUIRED'); return cli(['window-start', '--source', 'demo', '--window', nextId, '--hash', 'fixture']); },
        context => { expect(JSON.stringify(context.messages.at(-1))).toContain('DIGEST_REPAIR_REQUIRED'); return cli(['ingest-done', '--source', 'demo']); }, blocked,
        context => { expect(context.messages.at(-1)?.role).toBe('user'); expect(JSON.stringify(context.messages.at(-1))).toContain('DIGEST_REPAIR_REQUIRED'); return call('read', { path: digest }); },
        cli(['show-window', '--source', 'demo', '--window', 'w0000']),
        ...(scenario === 'restart' ? [cli(['show-window', '--source', 'demo', '--window', 'w0001'])] : []),
        call('write', { path: digest, content: digestText(nextId) }),
        cli(['next', '--source', 'demo', '--resume-packet']),
        ...(scenario === 'same-session' ? [
          cli(['show-window', '--source', 'demo', '--window', 'w0001']),
          cli(['window-start', '--source', 'demo', '--window', 'w0001', '--hash', 'fixture']),
          cli(['window-done', '--source', 'demo', '--window', 'w0001', '--writes', '[]']),
          call('write', { path: digest, content: digestText('w0002') }),
          cli(['next', '--source', 'demo', '--resume-packet']),
        ] : []),
        cli(['show-window', '--source', 'demo', '--window', 'w0002']),
        cli(['window-start', '--source', 'demo', '--window', 'w0002', '--hash', 'fixture']),
        call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两个人的选择互相影响。' }),
        call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [正文]\n---\n阅读 [[sources/demo|选择]]。' }),
        cli(['window-done', '--source', 'demo', '--window', 'w0002', '--writes', '["sources/demo.md","overview.md"]']),
        call('write', { path: digest, content: '## DONE\n保留正文中的选择问题；待发布检查。' }),
        cli(['ingest-done', '--source', 'demo']), cli(['lint', '--source', 'demo']), fauxAssistantMessage('published'),
      ]);
    const started = await service.app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'resume' } });
    expect(started.statusCode, started.body).toBe(200);
    const result = await completed(service.jobs, started.json().id);
    if (scenario === 'no-repair' || scenario === 'broken-workorder') {
      expect(result.status).toBe('failed'); expect(replies).toBe(scenario === 'no-repair' ? 2 : 0);
      expect(result.message).toContain(scenario === 'no-repair' ? '摘要仍未完成核对' : 'workorder');
      expect(fs.readFileSync(path.join(c.root, digest), 'utf8')).toBe(digestText('w0000'));
    } else {
      expect(result.status, service.jobs.log(result.id)).toBe('succeeded');
      expect(result.progress.windows.finished).toBe(3);
      expect(service.jobs.log(result.id)).toContain('[digest-repair]');
      if (scenario === 'restart') expect(service.jobs.log(result.id)).not.toMatch(/\[window-start\].*--window w000[01]/);
      expect(fs.readFileSync(path.join(c.root, digest), 'utf8')).toContain('保留正文中的选择问题');
    }
  } finally { await service.app.close(); }
}, 120000);

test.each(['ignore', 'needs_human', 'finish_interruption'] as const)('one confirmation chains arbitration and publication, or pauses for %s', async scenario => {
  const decision = scenario === 'needs_human' ? 'needs_human' : 'ignore';
  const c = setup(); const { app, jobs, models } = await createApp(c);
  const realPipeline = processes.pipeline;
  const staging = path.join(c.root, 'pipeline-workspace/staging/demo');
  // Supply a deterministic disagreement instead of downloading/running MinerU in this test.
  const audit = vi.spyOn(processes, 'pipeline').mockImplementation(async (config, args, signal, log) => {
    const output = await realPipeline(config, args, signal, log);
    if (args[0] === 'source-audit') {
      fs.mkdirSync(path.join(staging, 'arbitration'), { recursive: true });
      fs.writeFileSync(path.join(staging, 'evidence.json'), JSON.stringify({ candidates: [1], pages: { '1': { page: 1, risk_flags: [] } } }));
      fs.writeFileSync(path.join(staging, 'arbitration/queue.json'), JSON.stringify({ packets: [{ page: 1, page_image: 'arbitration/p0001.png', disagreement_kinds: ['figure'] }] }));
      fs.writeFileSync(path.join(staging, 'arbitration/p0001.png'), Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFckAAAAASUVORK5CYII=', 'base64'));
    }
    return output;
  });
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 选择\n两个人的选择互相影响。');
    const prepared = await app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    const pj = await completed(jobs, prepared.json().id); expect(pj.status, jobs.log(pj.id)).toBe('succeeded'); expect(pj.progress.waiting).toBe('arbitration');
    models.save({ id: 'fake', name: 'Workflow test', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake'); const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    let startedWriting = false;
    faux.setResponses([
      call('read', { path: 'pipeline-workspace/staging/demo/arbitration/p0001.png' }),
      call('write', { path: 'pipeline-workspace/staging/demo/arbitration/decisions.json', content: JSON.stringify({ decisions: [{ page: 1, decision, reason: 'Fixture evidence review' }] }) }),
      cli(['arbitration-apply', '--source', 'demo']), fauxAssistantMessage('分歧核对完成'),
      () => { startedWriting = true; return cli(['ingest-start', '--source', 'demo']); },
      cli(['show-window', '--source', 'demo', '--window', 'w0000']),
      () => { const w = JSON.parse(fs.readFileSync(path.join(staging, 'windows.jsonl'), 'utf8').trim()); return cli(['window-start', '--source', 'demo', '--window', w.window_id, '--hash', `${w.char_start}:${w.char_end}`]); },
      call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两个人的选择互相影响。' }),
      call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [全文]\n---\n阅读 [[sources/demo|选择]]。' }),
      cli(['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '["sources/demo.md","overview.md"]']),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '## DONE\n全部窗口完成，待发布检查。' }),
      cli(['ingest-done', '--source', 'demo']), ...(scenario === 'finish_interruption' ? Array.from({ length: 3 }, () => fauxAssistantMessage('', { stopReason: 'error', errorMessage: 'Network connection closed before lint' })) : [cli(['lint', '--source', 'demo'])]), fauxAssistantMessage('published'),
    ]);
    const started = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' } });
    let result = await completed(jobs, started.json().id);
    if (scenario === 'finish_interruption') {
      expect(result.status).toBe('failed');
      faux.setResponses([context => { expect(JSON.stringify(context.messages)).toContain('phase=finish'); return cli(['lint', '--source', 'demo']); }, fauxAssistantMessage('published')]);
      const resumed = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'resume' } });
      result = await completed(jobs, resumed.json().id);
    }
    expect(result.status, jobs.log(result.id)).toBe('succeeded');
    expect(startedWriting).toBe(decision === 'ignore');
    if (decision === 'needs_human') { expect(result.progress.waiting).toBe('human'); expect(fs.existsSync(path.join(staging, 'workorder.yaml'))).toBe(false); }
    else { expect(result.progress.phase).toBe('published'); expect(JSON.parse(await realPipeline(c, ['status', '--json'])).sources[0].current_status).toBe('published'); }
  } finally { audit.mockRestore(); await app.close(); }
}, 120000);

test('upload -> prepare -> read-tool image verification -> restricted agent -> published reader', async () => {
  const c = setup(); const { app, jobs, models } = await createApp(c);
  try {
    const material = '两人分别选择合作或背叛。双方合作各判一年；一人背叛另一人合作时，背叛者零年，合作者十年；双方背叛各判五年。无论对方怎样选择，自己背叛的刑期更少，因此个体选择造成比共同合作更差的结果。';
    const boundary = 'test-source-upload';
    const body = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.md"\r\nContent-Type: text/markdown\r\n\r\n${material}\r\n--${boundary}--\r\n`;
    const upload = await app.inject({ method: 'POST', url: '/api/library/upload?domain=demo', headers: { ...headers, 'content-type': `multipart/form-data; boundary=${boundary}` }, payload: body });
    expect(upload.statusCode, upload.body).toBe(200);
    const prepared = await app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: upload.json() });
    expect(prepared.statusCode, prepared.body).toBe(200);
    const pj = await completed(jobs, prepared.json().id);
    expect(pj.status, jobs.log(pj.id)).toBe('succeeded');
    const windows = fs.readFileSync(path.join(c.root, 'pipeline-workspace/staging/demo/windows.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    expect(windows).toHaveLength(1);
    const detail = (await app.inject({ url: '/api/library/demo', headers })).json();
    expect(detail.stats.windows.planned).toBe(1);
    expect(detail.stats.windows.finished).toBe(0);
    models.save({ id: 'fake', name: 'Fake model', kind: 'apikey', provider: 'faux', apiKey: 'fake-secret-do-not-log' });
    const rt = await models.runtime('fake');
    const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'], reasoning: true }] });
    rt.registerNativeProvider(faux.provider);
    faux.setResponses(Array.from({ length: 3 }, () => [call('read', { path: 'check.png' }), fauxAssistantMessage('4821')]).flat());
    const verification = await app.inject({ method: 'POST', url: '/api/vision', headers, payload: { provider: 'fake', model: 'writer' } });
    const vj = await completed(jobs, verification.json().id);
    expect(vj.status, jobs.log(vj.id)).toBe('succeeded');
    expect(models.isVerified('fake', 'writer')).toBe(true);
    // Replay the real failure: first start asks for recovery before creating a digest.
    faux.setResponses([
      cli(['ingest-start', '--source', 'demo']),
      cli(['next', '--source', 'demo', '--resume-packet']),
      context => {
        expect(JSON.stringify(context.messages)).toContain('phase=initialize');
        return fauxAssistantMessage('', { stopReason: 'error', errorMessage: 'Simulated startup interruption' });
      },
    ]);
    const interrupted = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { provider: 'fake', model: 'writer', source: 'demo', mode: 'start' } });
    const interruptedJob = await completed(jobs, interrupted.json().id);
    expect(interruptedJob.status, jobs.log(interruptedJob.id)).toBe('failed');
    expect(fs.existsSync(path.join(c.root, 'pipeline-workspace/staging/demo/digest.md'))).toBe(false);
    const w = windows[0];
    const concept = 'domains/demo/concepts/囚徒困境.md';
    const conceptBody = '==囚徒困境==描述个人选择与共同利益之间的冲突。' + material + '\n\n固定对方的选择才能比较自己的行动。如果对方合作，背叛带来零年而合作为一年；如果对方背叛，背叛带来五年而合作为十年。这两组比较说明背叛占优。双方都据此选择后各判五年，又比共同合作时各判一年更差。较短刑期才是偏好的结果，不能把刑期数字当作越大越好的收益。\n\n> [!question] 如果对方合作，自己如何选择刑期更少？\n> > [!success]- 答案\n> > 背叛得到零年，小于合作的一年。\n';
    const overview = '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [全文]\n---\n从 [[domains/demo/concepts/囚徒困境|囚徒困境]] 理解个人选择与共同利益的差异。原始材料见 [[sources/demo|来源]]。';
    faux.setResponses([
      context => {
        expect(JSON.stringify(context.messages)).toContain('phase=initialize');
        return cli(['ingest-start', '--source', 'demo']);
      },
      call('read', { path: path.join(repo, '.agents/skills/ingest/SKILL.md') }),
      call('read', { path: 'pipeline-workspace/staging/demo/chapters.json' }),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: `## RESUME\n- done: 无\n- next: ${w.window_id}\n\n## 路由表\n全文：理论型，以合作选择为概念。` }),
      fauxAssistantMessage('摘要初始化完成，下一步从第一个窗口开始。'),
      context => {
        expect(context.messages.at(-1)?.role).toBe('user');
        expect(JSON.stringify(context.messages.at(-1))).toContain('继续执行');
        return call('read', { path: 'pipeline-workspace/staging/demo/digest.md' });
      },
      cli(['show-window', '--source', 'demo', '--window', w.window_id]),
      cli(['window-start', '--source', 'demo', '--window', w.window_id, '--hash', `${w.char_start}:${w.char_end}`]),
      cli(['resolve-concept', '--mention', '囚徒困境', '--domain', 'demo', '--ref-source', 'demo', '--ref-sections', '全文']),
      () => {
        const seed = fs.readFileSync(path.join(c.root, 'wiki', concept), 'utf8');
        const front = seed.slice(0, seed.indexOf('\n---', 3) + 4);
        return call('write', { path: 'wiki/' + concept, content: front + '\n' + conceptBody });
      },
      call('write', { path: 'wiki/overview.md', content: overview }),
      call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 合作选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n材料给出合作与背叛的刑期，见 [[domains/demo/concepts/囚徒困境|囚徒困境]]。' }),
      cli(['window-done', '--source', 'demo', '--window', w.window_id, '--writes', JSON.stringify([concept, 'overview.md', 'sources/demo.md'])]),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '# 摘要\n\n## DONE\n全部窗口已阅读，阶段 E 已完成。' }),
      fauxAssistantMessage('窗口写作与综合层已完成，下一步收尾。'),
      cli(['ingest-done', '--source', 'demo']),
      fauxAssistantMessage('已收工，下一步发布检查。'),
      cli(['lint', '--source', 'demo']),
      fauxAssistantMessage('published, pending content acceptance'),
    ]);
    const started = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { provider: 'fake', model: 'writer', source: 'demo', mode: 'resume' } });
    expect(started.statusCode, started.body).toBe(200);
    const concurrent = await app.inject({ method: 'POST', url: '/api/vision', headers, payload: { provider: 'fake', model: 'writer' } });
    expect(concurrent.statusCode).toBe(409);
    const ij = await completed(jobs, started.json().id);
    expect(ij.status, jobs.log(ij.id)).toBe('succeeded');
    expect(jobs.log(ij.id).match(/\[continue\]/g)).toHaveLength(3);
    const state = JSON.parse(await pipeline(c, ['status', '--json']));
    expect(state.sources[0].current_status).toBe('published'); expect(state.lock).toBeNull();
    const reader = await app.inject({ url: '/api/reader', headers });
    expect(reader.json().pages.map((p: any) => p.path).sort()).toEqual([concept, 'overview.md', 'sources/demo.md'].sort());
    const original = fs.readFileSync(path.join(c.root, 'wiki', concept), 'utf8');
    const changed = original.replace('status: published', 'status: proposed') + '\n补充说明：这组选择的结论依赖给定刑期，不能直接套用于所有合作情境。\n';
    faux.setResponses([
      context => { expect(JSON.stringify(context.messages)).toContain('增量返工'); expect(JSON.stringify(context.messages)).not.toContain('这是首次入库'); return cli(['ingest-start', '--source', 'demo']); },
      cli(['show-window', '--source', 'demo', '--window', w.window_id]),
      cli(['window-start', '--source', 'demo', '--window', w.window_id, '--hash', `${w.char_start}:${w.char_end}`]),
      call('write', { path: 'wiki/' + concept, content: changed }),
      cli(['window-done', '--source', 'demo', '--window', w.window_id, '--writes', JSON.stringify([concept])]),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '## DONE\n增量返工完成，待发布检查。' }),
      cli(['ingest-done', '--source', 'demo']), cli(['lint', '--source', 'demo']), fauxAssistantMessage('指定的内容返工已发布。'),
    ]);
    const rework = { provider: 'fake', model: 'writer', source: 'demo', mode: 'rework', instructions: '补充说明这组选择依赖给定刑期。' };
    const denied = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: rework }); expect(denied.json().error).toContain('exact source');
    const repair = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { ...rework, confirm: 'demo' } });
    const repaired = await completed(jobs, repair.json().id); expect(repaired.status, jobs.log(repaired.id)).toBe('succeeded');
    expect(fs.readFileSync(path.join(c.root, 'wiki', concept), 'utf8')).toContain('不能直接套用于所有合作情境');
    expect(fs.existsSync(path.join(c.root, 'pipeline-workspace/staging/demo/rework-request.md'))).toBe(false);
    for (const dir of ['web-jobs-node', 'web-sessions']) for (const entry of fs.readdirSync(path.join(c.root, 'pipeline-workspace', dir), { recursive: true, withFileTypes: true }).filter(e => e.isFile())) {
      expect(fs.readFileSync(path.join(entry.parentPath, entry.name), 'utf8')).not.toContain('fake-secret-do-not-log');
    }
    faux.setResponses([call('read', { path: 'check.png' }), fauxAssistantMessage('0000')]);
    const wrong = await app.inject({ method: 'POST', url: '/api/vision', headers, payload: { provider: 'fake', model: 'writer' } });
    expect((await completed(jobs, wrong.json().id)).status).toBe('failed');
    expect(models.isVerified('fake', 'writer')).toBe(false);
  } finally { await app.close(); }
}, 120000);

test('ordinary tool errors can be corrected; files enumerates only allowed evidence and pages', async () => {
  const c = setup(); const { app, jobs, models } = await createApp(c);
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 选择\n两个人的选择互相影响。');
    const prepared = await app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    expect((await completed(jobs, prepared.json().id)).status).toBe('succeeded');
    models.save({ id: 'fake', name: 'Correction', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake'); const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    faux.setResponses([
      cli(['ingest-start', '--source', 'demo']),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '## RESUME\n下一窗 w0000' }),
      call('edit', { path: 'pipeline-workspace/staging/demo/digest.md', oldText: 'not present', newText: 'replacement' }),
      fauxAssistantMessage('编辑文本没有匹配，我会重新读取并修正。'),
      context => { expect(JSON.stringify(context.messages.at(-1))).toContain('not find'); return call('files', { path: 'wiki' }); },
      context => { expect(JSON.stringify(context.messages.at(-1))).toContain('overview.md'); return call('files', { path: c.config }); },
      context => { expect(JSON.stringify(context.messages.at(-1))).toMatch(/not allowed|outside/i); return call('files', { path: 'pipeline-workspace/staging/demo' }); },
      context => { expect(JSON.stringify(context.messages.at(-1))).toContain('workorder.yaml'); return cli(['show-window', '--source', 'demo', '--window', 'w0000']); },
      cli(['window-start', '--source', 'demo', '--window', 'w0000', '--hash', 'fixture']),
      call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两个人的选择互相影响。' }),
      call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [全文]\n---\n阅读 [[sources/demo|选择]]。' }),
      cli(['sync-overview-sources', '--apply']),
      cli(['window-done', '--source', 'demo', '--window', 'w0000', '--writes', '["overview.md","sources/demo.md"]']),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '## DONE\n窗口和综合页已完成，待发布' }),
      cli(['ingest-done', '--source', 'demo']), cli(['lint', '--source', 'demo']), fauxAssistantMessage('published'),
    ]);
    const started = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' } });
    const result = await completed(jobs, started.json().id);
    expect(result.status, jobs.log(result.id)).toBe('succeeded');
    expect(result.progress.windows.finished).toBe(1);
  } finally { await app.close(); }
}, 120000);

test.each(['no-progress', 'tool-error', 'vision', 'network', 'network-cancel', 'auth', 'forbidden', 'context', 'quota', 'lint'] as const)('continuation stops on %s without unbounded model calls', async kind => {
  const c = setup(); const { app, jobs, models } = await createApp(c);
  try {
    fs.mkdirSync(path.join(c.root, 'books'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo.md'), '# 选择\n两个人的选择互相影响，各自偏好的结果可能不同。');
    const prepared = await app.inject({ method: 'POST', url: '/api/library/prepare', headers, payload: { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo.md' } });
    const pj = await completed(jobs, prepared.json().id);
    expect(pj.status, jobs.log(pj.id)).toBe('succeeded');
    models.save({ id: 'fake', name: 'Stop test', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' });
    models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake');
    const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'], reasoning: true }] });
    rt.registerNativeProvider(faux.provider);
    let replies = 0;
    const response = () => {
      replies++;
      return kind === 'vision' ? fauxAssistantMessage('我无法读取图片，不能继续写作。') : ['no-progress', 'tool-error'].includes(kind) ? fauxAssistantMessage('下一步继续。')
        : fauxAssistantMessage('', { stopReason: 'error', errorMessage: kind === 'quota' ? '429 quota exceeded' : kind === 'auth' ? '401 Unauthorized socket' : kind === 'forbidden' ? '403 Forbidden network error' : kind === 'context' ? 'context_length_exceeded socket' : 'Network connection closed' });
    };
    faux.setResponses(kind === 'lint'
      ? [cli(['ingest-start', '--source', 'demo']), cli(['lint', '--source', 'demo']), response]
      : kind === 'tool-error' ? [cli(['ingest-start', '--source', 'demo']), ...Array.from({ length: 4 }, () => [call('read', { path: 'missing-file.md' }), response]).flat()]
        : [cli(['ingest-start', '--source', 'demo']), response, response, response, response]);
    const started = await app.inject({ method: 'POST', url: '/api/ingest', headers, payload: { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' } });
    if (kind === 'network-cancel') jobs.on(started.json().id, value => { if (typeof value === 'string' && value.includes('[network-recovery]')) jobs.cancel(); });
    const result = await completed(jobs, started.json().id);
    expect(result.status, jobs.log(result.id)).toBe(kind === 'quota' ? 'paused' : kind === 'network-cancel' ? 'cancelled' : 'failed');
    // Pi may compact/retry a context overflow itself; our transport recovery must not add retries.
    expect(replies).toBe(kind === 'lint' ? 0 : ['no-progress', 'tool-error', 'network', 'context'].includes(kind) ? 3 : 1);
    if (['auth', 'forbidden', 'context', 'quota', 'vision', 'lint'].includes(kind)) expect(jobs.log(result.id)).not.toContain('[network-recovery]');
    if (['no-progress', 'tool-error'].includes(kind)) expect(result.message).toContain('连续 3 轮');
    if (kind === 'vision') expect(result.message).toContain('无法读取图片');
    if (kind === 'lint') expect(result.message).toContain('发布检查失败');
    expect(jobs.active).toBeUndefined();
  } finally { await app.close(); }
}, 120000);

test('quota pauses, stop completes, restart resumes the next unfinished window and redacts session text', async () => {
  const c = setup(); let service = await createApp(c);
  let child: ChildProcess | undefined;
  const post = (url: string, payload: any) => service.app.inject({ method: 'POST', url, headers, payload });
  const secret = 'fake-private-key-for-recovery';
  const register = async () => {
    const rt = await service.models.runtime('fake');
    const faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'], reasoning: true }] });
    rt.registerNativeProvider(faux.provider); return faux;
  };
  try {
    fs.mkdirSync(path.join(c.root, 'books/demo/input'), { recursive: true });
    fs.writeFileSync(path.join(c.root, 'books/demo/input/book.md'), '# 前言\n这本材料讨论选择。\n\n# 正文\n两人的选择互相影响，偏好可能不同。');
    const prep = await post('/api/library/prepare', { source: 'demo', domain: 'demo', fmt: 'md', path: 'books/demo/input/book.md' });
    expect((await completed(service.jobs, prep.json().id)).status).toBe('succeeded');
    const detail = (await service.app.inject({ url: '/api/library/demo', headers })).json();
    expect(detail.prepare.path).toContain('book.md');
    const windows = fs.readFileSync(path.join(c.root, 'pipeline-workspace/staging/demo/windows.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    expect(windows).toHaveLength(2);
    const [first, next] = windows;
    service.models.save({ id: 'fake', name: 'Recovery', kind: 'apikey', provider: 'faux', apiKey: secret });
    service.models.mark('fake', 'writer', true);
    let faux = await register();
    faux.setResponses([
      cli(['ingest-start', '--source', 'demo']), cli(['show-window', '--source', 'demo', '--window', first.window_id]),
      cli(['window-start', '--source', 'demo', '--window', first.window_id, '--hash', `${first.char_start}:${first.char_end}`]),
      cli(['window-done', '--source', 'demo', '--window', first.window_id, '--writes', '[]']),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: `## RESUME\n- done: ${first.window_id}\n- next: ${next.window_id}\n` }),
      fauxAssistantMessage('', { stopReason: 'error', errorMessage: '429 quota exceeded ' + secret }),
    ]);
    const start = await post('/api/ingest', { source: 'demo', provider: 'fake', model: 'writer', mode: 'start' });
    const paused = await completed(service.jobs, start.json().id);
    expect(paused.status, service.jobs.log(paused.id)).toBe('paused');
    expect(paused.error_kind).toBe('quota');
    expect(paused.message).not.toContain(secret);
    await service.app.close(); service = await createApp(c); faux = await register();
    let reached!: () => void;
    const waiting = new Promise<void>(resolve => { reached = resolve; });
    faux.setResponses([async (context, options) => {
      expect(JSON.stringify(context.messages)).toContain('next_window=' + next.window_id);
      reached();
      await new Promise<void>(resolve => { if (options?.signal?.aborted) resolve(); else options?.signal?.addEventListener('abort', () => resolve(), { once: true }); });
      return fauxAssistantMessage('Stopped');
    }]);
    const resumed = await post('/api/ingest', { source: 'demo', provider: 'fake', model: 'writer', mode: 'resume' });
    expect(resumed.statusCode, resumed.body).toBe(200); await waiting;
    const other = await post('/api/ingest', { source: 'another', provider: 'fake', model: 'writer', mode: 'start' });
    expect(other.statusCode).toBe(409);
    await post('/api/jobs/cancel', {});
    expect((await completed(service.jobs, resumed.json().id)).status).toBe('cancelled');
    await service.app.close();
    const childCode = `
      import { createApp } from './server/src/app.ts';
      import { fauxProvider } from '@earendil-works/pi-ai';
      const c = ${JSON.stringify(c)};
      const {app,models} = await createApp(c);
      const rt = await models.runtime('fake');
      const faux = fauxProvider({provider:'faux',models:[{id:'writer',input:['text','image'],reasoning:true}]});
      rt.registerNativeProvider(faux.provider);
      faux.setResponses([async () => { console.log('BLOCKED_IN_MODEL'); return new Promise(()=>{}); }]);
      await app.listen({host:'127.0.0.1',port:0});
      const response = await app.inject({method:'POST',url:'/api/ingest',headers:${JSON.stringify(headers)},payload:{source:'demo',provider:'fake',model:'writer',mode:'resume'}});
      if(response.statusCode!==200) throw new Error(response.body);
    `;
    child = spawn(process.execPath, ['--input-type=module', '-'], { cwd: path.join(repo, 'web'), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let childOutput = '';
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Child did not reach model: ' + childOutput)), 30000);
      child!.stdout!.on('data', chunk => { childOutput += chunk; if (childOutput.includes('BLOCKED_IN_MODEL')) { clearTimeout(timer); resolve(); } });
      child!.stderr!.on('data', chunk => { childOutput += chunk; });
      child!.once('exit', code => { clearTimeout(timer); reject(new Error('Child exited before kill: ' + code + ' ' + childOutput)); });
      child!.stdin!.end(childCode);
    });
    const killed = new Promise<void>(resolve => child!.once('exit', () => resolve()));
    child.kill('SIGKILL'); await killed; child = undefined;
    service = await createApp(c); faux = await register();
    expect(service.jobs.history[0].status).toBe('interrupted');
    faux.setResponses([
      context => { expect(JSON.stringify(context.messages)).toContain('next_window=' + next.window_id); return cli(['ingest-start', '--source', 'demo']); },
      cli(['show-window', '--source', 'demo', '--window', next.window_id]),
      cli(['window-start', '--source', 'demo', '--window', next.window_id, '--hash', `${next.char_start}:${next.char_end}`]),
      call('write', { path: 'wiki/sources/demo.md', content: '---\ntype: source\nsource_id: demo\ntitle: 选择\ndomain: demo\nformat: md\nstatus: proposed\nmanaged_by: pipeline\n---\n两人的选择互相影响。' }),
      call('write', { path: 'wiki/overview.md', content: '---\ntype: overview\ntitle: 总览\nstatus: proposed\nmanaged_by: pipeline\nsource_refs:\n- source: demo\n  sections: [正文]\n---\n从 [[sources/demo|选择]] 开始阅读。' }),
      cli(['window-done', '--source', 'demo', '--window', next.window_id, '--writes', '["overview.md","sources/demo.md"]']),
      call('write', { path: 'pipeline-workspace/staging/demo/digest.md', content: '## DONE\n全部窗口完成，待发布检查。' }),
      cli(['ingest-done', '--source', 'demo']), cli(['lint', '--source', 'demo']),
      fauxAssistantMessage('published ' + secret),
    ]);
    const finish = await post('/api/ingest', { source: 'demo', provider: 'fake', model: 'writer', mode: 'resume' });
    const finished = await completed(service.jobs, finish.json().id);
    expect(finished.status, service.jobs.log(finished.id)).toBe('succeeded');
    const events = await service.app.inject({ url: `/api/jobs/${finished.id}/events`, headers });
    expect(events.body).toContain('[redacted]'); expect(events.body).not.toContain(secret);
    for (const dir of ['web-jobs-node', 'web-sessions']) for (const f of fs.readdirSync(path.join(c.root, 'pipeline-workspace', dir), { recursive: true, withFileTypes: true }).filter(e => e.isFile())) expect(fs.readFileSync(path.join(f.parentPath, f.name), 'utf8')).not.toContain(secret);
  } finally { child?.kill('SIGKILL'); await service.app.close(); }
}, 120000);
