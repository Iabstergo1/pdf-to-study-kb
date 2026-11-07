import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import { test, expect, vi } from 'vitest';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai';
import { createApp } from '../src/app.ts';
import { Models } from '../src/llm/models.ts';
import { repo, type AppConfig } from '../src/config.ts';
import { pipeline } from '../src/engine/process.ts';
import { commands, loadCommands } from '../src/engine/commands.ts';
import { Jobs } from '../src/engine/jobs.ts';
import * as processes from '../src/engine/process.ts';
import { prepareEvolution, collectEvolution, evolutionPreview, adoptEvolution } from '../src/skills/evolution.ts';

const config = (): AppConfig => { const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-kb-workflows-')); return { repo, root, config: path.join(root, 'private'), python: process.env.STUDY_KB_PYTHON || 'python', port: 8765, token: 'test-token' }; };
const headers = { host: '127.0.0.1:8765', 'x-console-token': 'test-token' };
const call = (name: string, args: any) => fauxAssistantMessage(fauxToolCall(name, args), { stopReason: 'toolUse' });
async function finished(jobs: Jobs, id: string) { const j = jobs.history.find(j => j.id === id)!; if (j.status === 'running') await new Promise<void>(resolve => { const done = (v: unknown) => { if (v === null) { jobs.off(id, done); resolve(); } }; jobs.on(id, done); }); return j; }

test('account models use account response ordering, hide non-list entries, and preserve unknown models as unavailable', async () => {
  const c = config(); fs.mkdirSync(c.config, { recursive: true });
  fs.writeFileSync(path.join(c.config, 'auth-chatgpt.json'), JSON.stringify({ openai: { type: 'oauth', clientId: 'fixture', access: 'fixture-access', refresh: 'fixture-refresh', expires: Date.now() + 100000 } }));
  const models = new Models(c); const rt = await models.runtime('chatgpt');
  const known = rt.getModels('openai').filter(m => m.input.includes('image')).slice(0, 2);
  const auth = vi.spyOn(rt, 'getAuth').mockResolvedValue({ auth: { apiKey: 'fixture-token' } });
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ models: [{ slug: known[1].id, display_name: 'Account first', visibility: 'list' }, { slug: known[0].id, visibility: 'hide' }, { slug: 'future-model', visibility: 'list' }] })));
  try {
    const result = await models.list('chatgpt');
    expect(result.map(m => m.id)).toEqual([known[1].id, 'future-model']);
    expect(result[0].name).toBe('Account first'); expect(result[1].supported).toBe(false);
    expect(fetch.mock.calls[0][0]).toBe('https://api.openai.com/v1/models');
    expect(fetch.mock.calls[0][1]?.headers).toEqual({ Authorization: 'Bearer fixture-token' });
    expect(JSON.stringify(result)).not.toContain('fixture-token');
    await expect(models.model('chatgpt', known[0].id)).rejects.toThrow('not listed');
    await models.list('chatgpt'); expect(fetch).toHaveBeenCalledTimes(1);
    fetch.mockResolvedValueOnce(new Response('failure', { status: 503 }));
    await expect(models.list('chatgpt', true)).rejects.toThrow('HTTP 503');
  } finally { auth.mockRestore(); fetch.mockRestore(); }
});

test('thinking settings are model-specific, survive restart, and reject unsupported levels', async () => {
  const c = config(); const models = new Models(c);
  models.save({ id: 'test', name: 'test', kind: 'custom', provider: 'fixture', apiKey: 'fake', api: 'openai-responses', baseUrl: 'https://invalid.example/v1', models: [{ id: 'reasoner', vision: true, reasoning: true }, { id: 'plain', vision: true, reasoning: false }] });
  await models.select({ provider: 'test', model: 'reasoner', thinkingLevel: 'high' });
  await models.select({ provider: 'test', model: 'plain', thinkingLevel: 'off' });
  const restarted = new Models(c);
  expect(await restarted.thinking('test', 'reasoner')).toBe('high');
  expect(await restarted.thinking('test', 'plain')).toBe('off');
  await expect(restarted.thinking('test', 'plain', 'high')).rejects.toThrow('Unsupported');
});

test('all CLI commands are classified and unsafe calls require confirmation or preview', async () => {
  const c = config(); const service = await createApp(c);
  try {
    const catalogue = JSON.parse(await pipeline(c, ['--describe-commands'])); loadCommands(catalogue);
    expect(Object.keys(commands).sort()).toEqual(catalogue.map((c: any) => c.name).sort());
    expect(() => loadCommands([{ name: 'new-unreviewed-command', description: '', fields: [] }])).toThrow('Unclassified');
    for (const name of ['reopen', 'skill-adopt', 'promote-concept', 'window-done']) {
      const params = name === 'skill-adopt' ? { candidate: 'test' } : name === 'promote-concept' ? { id: 'concept.demo.test' } : name === 'window-done' ? { source: 'demo', window: 'w0000' } : { source: 'demo' };
      const response = await service.app.inject({ method: 'POST', url: '/api/jobs', headers, payload: { command: name, params } });
      expect(response.json().error).toContain('exact target');
    }
    const response = await service.app.inject({ method: 'POST', url: '/api/jobs', headers, payload: { command: 'adopt-vault', params: { source: 'legacy', title: 'Legacy', domain: 'demo', baseline_archive: 'archive.zip', baseline_sha256: '0'.repeat(64) }, apply: true, confirm: 'legacy' } });
    expect(response.json().error).toContain('preview'); expect(service.jobs.history).toHaveLength(0);
  } finally { await service.app.close(); }
});

test('human questions bind to their job and exact request, and cancellation releases the waiter', async () => {
  const c = config(), jobs = new Jobs(c, s => s); let answered = '';
  const job = jobs.start('test', async (job, signal) => { answered = await jobs.ask(job, signal, { title: 'Apply?', detail: 'reviewed changes', target: 'demo' }); });
  const request = job.progress.question.id;
  expect(() => jobs.reply(job.id, request, 'approve', 'other')).toThrow('exact target');
  expect(() => jobs.reply('other', request, 'approve', 'demo')).toThrow('no longer pending');
  jobs.reply(job.id, request, 'decline'); await finished(jobs, job.id); expect(answered).toBe('decline');
  const cancelled = jobs.start('test', async (j, signal) => { await jobs.ask(j, signal, { title: 'Why?', detail: '' }); });
  await jobs.stop(); expect(cancelled.status).toBe('cancelled'); expect(cancelled.progress.question).toBeNull();
});

test('server code changes are surfaced without restarting the process', async () => {
  const c = config(); c.repo = path.join(c.root, 'repo'); const dir = path.join(c.repo, 'web/server/src'); fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'app.ts'); fs.writeFileSync(file, 'version one');
  const stub = vi.spyOn(processes, 'pipeline').mockResolvedValue('{"sources":[]}');
  const { app } = await createApp(c);
  try {
    expect((await app.inject({ url: '/api/status', headers })).json().restart_required).toBe(false);
    fs.writeFileSync(file, 'version two');
    expect((await app.inject({ url: '/api/status', headers })).json().restart_required).toBe(true);
  } finally { await app.close(); stub.mockRestore(); }
});

test.each(['kb-qa', 'wiki-lint-semantic'] as const)('%s can report findings but cannot edit knowledge or run publication', async skill => {
  const c = config(), { app, models, jobs } = await createApp(c);
  try {
    models.save({ id: 'fake', name: 'fake', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake'), faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    faux.setResponses([
      call('write', { path: 'wiki/topics/forbidden.md', content: 'must not write' }),
      ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('cannot write'); return call('pipeline', { command: 'lint', params: { source: 'demo' } }); },
      ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('not allowed'); return call('write', { path: `pipeline-workspace/reports/${skill}/fixture.md`, content: '仅抽查一项；不能代表全文通过。' }); },
      fauxAssistantMessage('已保存抽查报告。'),
    ]);
    const response = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill, request: '检查证据', provider: 'fake', model: 'writer' } });
    const job = await finished(jobs, response.json().id);
    expect(job.status, jobs.log(job.id)).toBe('succeeded'); expect(fs.existsSync(path.join(c.root, 'wiki/topics/forbidden.md'))).toBe(false);
    expect(fs.existsSync(path.join(c.root, `pipeline-workspace/reports/${skill}/fixture.md`))).toBe(true);
  } finally { await app.close(); }
}, 60000);

test('source QA registers exact read versions automatically, preserves findings, and requires a human acceptance', async () => {
  const c = config(), { app, models, jobs } = await createApp(c);
  const rel = 'domains/demo/concepts/review.md', page = path.join(c.root, 'wiki', rel);
  await processes.runProcess(c.python, ['-c', `import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import state_store, mdpage; r=Path(sys.argv[2]); db=r/'pipeline-workspace/state/study-kb.sqlite'; state_store.init_db(db); state_store.register_source(db,'demo',domain='demo',fmt='md'); c=state_store.connect(db); c.execute("UPDATE sources SET current_stage='lint',current_status='published'"); c.commit(); c.close(); mdpage.write_page(r/'wiki/domains/demo/concepts/review.md',{'type':'concept','status':'published','source_refs':['demo']},'Original claim'); p=r/'pipeline-workspace/staging/demo/source.md'; p.parent.mkdir(parents=True); p.write_text('Original evidence',encoding='utf-8')`, path.join(repo, 'scripts'), c.root], { cwd: c.root });
  const original = fs.readFileSync(page, 'utf8');
  try {
    models.save({ id: 'fake', name: 'fake', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' }); models.mark('fake', 'writer', true);
    const rt = await models.runtime('fake'), faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text', 'image'] }] }); rt.registerNativeProvider(faux.provider);
    let runId = '';
    const reviewResponses = (findings: boolean) => [
      call('review', { action: 'begin', value: {} }),
      (ctx: any) => { const result = JSON.parse(ctx.messages.at(-1).content[0].text); runId = result.run_id; expect(result.pages).toEqual([rel]); return call('review', { action: 'read', value: { path: rel } }); },
      (ctx: any) => { const receipt = JSON.parse(ctx.messages.at(-1).content[0].text); expect(receipt.content).toContain('Original claim'); return call('review', { action: 'register', value: { format: 1, summary: 'Fixture source review', pages: [{ path: rel, read_id: receipt.read_id, outcome: findings ? 'findings' : 'no-findings', checks: [{ claim: 'The page claim', evidence: [{ path: 'pipeline-workspace/staging/demo/source.md', locator: 'paragraph 1' }], judgment: 'Compared with source' }], findings: findings ? [{ id: 'F1', reason: 'Check this claim' }] : [] }] } }); },
      (ctx: any) => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('exports_pending'); return call('pipeline', { command: 'content-review', params: { action: 'accept' } }); },
      (ctx: any) => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('not allowed'); return fauxAssistantMessage('Registered; awaiting human disposition and acceptance.'); },
    ];
    faux.setResponses(reviewResponses(true));
    const response = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-qa', source: 'demo', reviewMethod: 'full-page', request: 'Review this source', provider: 'fake', model: 'writer' } });
    const job = await finished(jobs, response.json().id);
    expect(job.status, jobs.log(job.id)).toBe('succeeded'); expect(job.progress.reviewRegistered).toBe(true);
    const firstRun = runId;
    const queue = (await app.inject({ url: '/api/reader/info/proposals?source=demo', headers })).json(); expect(queue).toHaveLength(1);
    const batch = (await app.inject({ url: '/api/content-review/status?source=demo', headers })).json()[0]; expect(batch.can_accept).toBe(false);
    const denied = await app.inject({ method: 'POST', url: '/api/content-review/accept', headers, payload: { run_id: firstRun, token: batch.token, confirmed: true } }); expect(denied.statusCode).toBe(400);
    const close = await app.inject({ method: 'POST', url: '/api/study/proposal-decision', headers, payload: { id: queue[0].id, expected_status: 'open', reason: 'Human checked the finding', decision: 'resolved', verified: true } }); expect(close.statusCode).toBe(200);
    faux.setResponses(reviewResponses(false));
    const second = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-qa', source: 'demo', reviewOf: firstRun, reviewMethod: 'sampled-claims', request: 'Recheck affected pages', provider: 'fake', model: 'writer' } });
    expect((await finished(jobs, second.json().id)).status).toBe('succeeded');
    const checked = (await app.inject({ url: '/api/content-review/status?source=demo', headers })).json()[0];
    expect(checked.can_accept).toBe(true); expect(checked.method).toBe('full-page'); expect(checked.runs).toHaveLength(2);
    const invalid = await app.inject({ method: 'POST', url: '/api/content-review/accept', headers, payload: { run_id: firstRun, token: checked.token, confirmed: false } }); expect(invalid.statusCode).toBe(400);
    const accepted = await app.inject({ method: 'POST', url: '/api/content-review/accept', headers, payload: { run_id: firstRun, token: checked.token, confirmed: true } }); expect(accepted.statusCode).toBe(200);
    const report = 'pipeline-workspace/reports/kb-qa/' + firstRun + '.md'; fs.unlinkSync(path.join(c.root, report));
    const stored = await app.inject({ url: '/api/report?path=' + encodeURIComponent(report), headers }); expect(stored.statusCode).toBe(200); expect(stored.json().text).toContain('Check this claim');
    expect(fs.readFileSync(page, 'utf8')).toBe(original);
    fs.appendFileSync(page, '\nChanged shared page');
    const changed = (await app.inject({ url: '/api/reader/info/review?source=demo', headers })).json();
    expect(changed.batches[0].accepted).toBe(false); expect(changed.batches[0].acceptances).toHaveLength(1); expect(changed.changed).toBe(1);
    faux.setResponses([fauxAssistantMessage('I am done, without registering anything.')]);
    const incomplete = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-qa', source: 'demo', reviewMethod: 'full-page', request: 'New review', provider: 'fake', model: 'writer' } });
    expect((await finished(jobs, incomplete.json().id)).status).toBe('failed');
    // A paused recheck keeps its scope even when another page changes meanwhile.
    const rel2 = 'domains/demo/concepts/second.md', page2 = path.join(c.root, 'wiki', rel2);
    fs.writeFileSync(page2, original);
    const parentId = randomUUID();
    const review = async (action: string, value: any) => JSON.parse(await pipeline(c, ['content-review', '--action', action, '--stdin'], undefined, undefined, value));
    await review('begin', { run_id: parentId, source: 'demo', method: 'full-page' });
    const receipts = await Promise.all([rel, rel2].map(path => review('read', { run_id: parentId, path })));
    await review('register', { format: 1, run_id: parentId, summary: 'Two-page fixture', pages: receipts.map(r => ({ path: r.path, read_id: r.read_id, outcome: 'no-findings', findings: [], checks: [{ claim: 'Original claim', evidence: [{ path: 'pipeline-workspace/staging/demo/source.md', locator: 'paragraph 1' }], judgment: 'Compared' }] })) });
    fs.appendFileSync(page, '\nFirst affected page');
    faux.setResponses([call('review', { action: 'begin' }), fauxAssistantMessage('Paused before registration')]);
    const paused = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-qa', source: 'demo', reviewOf: parentId, request: 'Recheck', provider: 'fake', model: 'writer' } });
    expect((await finished(jobs, paused.json().id)).status).toBe('failed');
    fs.appendFileSync(page2, '\nSecond affected page');
    faux.setResponses(reviewResponses(false));
    const resumed = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-qa', source: 'demo', previous: paused.json().id, request: 'Continue the paused recheck', provider: 'fake', model: 'writer' } });
    const resumedJob = await finished(jobs, resumed.json().id);
    expect(resumedJob.status, jobs.log(resumedJob.id)).toBe('succeeded');
    const remaining = (await review('status', { source: 'demo' })).find((b: any) => b.id === parentId);
    expect(remaining.affected).toEqual([rel2]); expect(remaining.can_accept).toBe(false);
  } finally { await app.close(); }
}, 120000);

test('skill candidate remains isolated until reviewed adoption; changed proposals are rejected', async () => {
  const c = config(); c.repo = c.root;
  const git = (args: string[]) => processes.runProcess('git', args, { cwd: c.repo });
  fs.cpSync(path.join(repo, 'scripts'), path.join(c.repo, 'scripts'), { recursive: true, filter: file => !file.includes('__pycache__') });
  fs.cpSync(path.join(repo, 'templates'), path.join(c.repo, 'templates'), { recursive: true });
  fs.writeFileSync(path.join(c.repo, '.gitignore'), 'tmp/\npipeline-workspace/\nwiki/\nprivate/\n__pycache__/\n.pytest_cache/\n');
  for (const tree of ['.agents', '.claude']) { const dir = path.join(c.repo, tree, 'skills/demo'); fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, 'SKILL.md'), '# Demo\nOld rule.\n'); }
  fs.mkdirSync(path.join(c.repo, 'tests')); fs.writeFileSync(path.join(c.repo, 'tests/test_rule.py'), "import os\ndef test_isolated():\n    assert 'STUDY_KB_ROOT' not in os.environ\n");
  await git(['init', '-q']); await git(['config', 'user.email', 'fixture@example.invalid']); await git(['config', 'user.name', 'Fixture']); await git(['add', '.']); await git(['commit', '-qm', 'Fixture base']);
  await pipeline(c, ['init-vault']);
  const id = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', signal = new AbortController().signal;
  const work = await prepareEvolution(c, id, 'demo', signal, () => {});
  for (const tree of ['.agents', '.claude']) fs.writeFileSync(path.join(work.repo, tree, 'skills/demo/SKILL.md'), '# Demo\nCheck evidence before writing.\n');
  await pipeline(work, ['skill-gate', '--candidate', id]); await pipeline(work, ['skill-stage', '--candidate', id]);
  expect(collectEvolution(c, id).state).toBe('staged');
  const preview = await evolutionPreview(c, id); expect(preview.diff).toContain('Check evidence');
  expect(fs.readFileSync(path.join(c.repo, '.agents/skills/demo/SKILL.md'), 'utf8')).toContain('Old rule');
  await expect(adoptEvolution(c, id, 'wrong-revision', signal, () => {})).rejects.toThrow('preview again');
  await adoptEvolution(c, id, preview.revision, signal, () => {});
  expect(fs.readFileSync(path.join(c.repo, '.agents/skills/demo/SKILL.md'), 'utf8')).toContain('Check evidence');
  expect((await git(['status', '--porcelain'])).trim()).toBe('');
}, 120000);

test('query skill loads its document, writes a checked session, blocks vault and credentials, and resumes conversation', async () => {
  const c = config(), { app, models, jobs } = await createApp(c);
  try {
    models.save({ id: 'fake', name: 'fake', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' });
    const rt = await models.runtime('fake'), faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text'] }] }); rt.registerNativeProvider(faux.provider);
    const queryDir = 'pipeline-workspace/query-sessions/query-fixture';
    faux.setResponses([
      ctx => { expect(JSON.stringify(ctx.messages)).toContain('kb-query'); return call('read', { path: path.join(c.repo, '.agents/skills/kb-query/SKILL.md') }); },
      call('write', { path: 'wiki/illegal.md', content: 'must be denied' }),
      ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('cannot write'); return call('read', { path: path.join(c.config, 'auth-api.json') }); },
      ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('Private'); return call('write', { path: queryDir + '/question.md', content: '用户的问题' }); },
      call('write', { path: queryDir + '/answer.md', content: '根据知识库的回答' }),
      fauxAssistantMessage('已经回答并保存查询记录。'),
    ]);
    const response = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-query', request: '查询测试', provider: 'fake', model: 'writer', querySession: 'query-fixture' } });
    expect(response.statusCode, response.body).toBe(200); const job = await finished(jobs, response.json().id);
    expect(job.status, jobs.log(job.id)).toBe('succeeded'); expect(fs.existsSync(path.join(c.root, 'wiki/illegal.md'))).toBe(false);
    expect(job.progress.thinkingLevel).toBe('off'); expect(job.progress.outputs).toContain(queryDir + '/answer.md');
    faux.setResponses([ctx => { expect(JSON.stringify(ctx.messages)).toContain('根据知识库的回答'); return fauxAssistantMessage('继续解释这个回答。'); }]);
    const follow = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-query', request: '解释', provider: 'fake', model: 'writer', querySession: 'query-fixture', previous: job.id } });
    expect((await finished(jobs, follow.json().id)).status).toBe('succeeded');
  } finally { await app.close(); }
}, 60000);

test.each(['decline', 'approve'])('save creates only authorized new candidates and respects human %s', async decision => {
  const c = config(), { app, models, jobs } = await createApp(c);
  try {
    await pipeline(c, ['init-vault']);
    fs.writeFileSync(path.join(c.root, 'evidence.md'), '# 比较依据\n概念的适用条件不同，需要结合问题进行选择。');
    await pipeline(c, ['add-source', '--source', 'demo', '--domain', 'demo', '--path', path.join(c.root, 'evidence.md'), '--fmt', 'md']);
    const dir = path.join(c.root, 'pipeline-workspace/query-sessions/saving'); fs.mkdirSync(dir, { recursive: true });
    for (const [file, value] of Object.entries({ 'question.md': '比较两个概念', 'answer.md': '基于已有证据的比较', 'decision.md': '比较具有增值', 'related_pages.json': '[]', 'candidate_write_set.json': '["topics/比较.md"]', 'evidence_refs.json': '[{"source":"demo","sections":["全文"]}]' })) fs.writeFileSync(path.join(dir, file), value);
    models.save({ id: 'fake', name: 'fake', kind: 'apikey', provider: 'faux', apiKey: 'fake-key' });
    const rt = await models.runtime('fake'), faux = fauxProvider({ provider: 'faux', models: [{ id: 'writer', input: ['text'] }] }); rt.registerNativeProvider(faux.provider);
    const content = '---\ntype: topic\ntitle: 比较\nstatus: proposed\nmanaged_by: pipeline\nsave_session: saving\nsource_refs:\n- source: demo\n  sections: [全文]\n---\n待用户审阅的候选正文。' + '这是一项基于已有证据的比较，说明概念间的区别、适用条件以及理解结论需要的前置知识。'.repeat(8);
    faux.setResponses([
      call('write', { path: 'pipeline-workspace/query-sessions/saving/write_authorizations.json', content: '[]' }),
      ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('cannot write'); return call('write', { path: 'wiki/topics/比较.md', content }); },
      call('pipeline', { command: 'check-session', params: { id: 'saving', saved: true } }),
      call('pipeline', { command: 'lint', params: { source: 'kb-save', session: 'saving' } }),
      ...(decision === 'decline' ? [(ctx: any) => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('declined'); return fauxAssistantMessage('用户拒绝发布，保留候选供审阅。'); }] : [
        call('write', { path: 'wiki/topics/比较.md', content: '不应改写发布后的内容' }),
        (ctx: any) => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('already published'); return fauxAssistantMessage('发布后修改已拒绝。'); },
      ]),
    ]);
    const response = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { skill: 'kb-save', request: '保存这个比较', provider: 'fake', model: 'writer', querySession: 'saving' } });
    const job = jobs.history.find(j => j.id === response.json().id)!;
    await vi.waitFor(() => expect(job.progress?.question).toBeTruthy(), { timeout: 15000 });
    expect(job.progress.question.detail).toContain('待用户审阅的候选正文');
    const wrong = await app.inject({ method: 'POST', url: `/api/jobs/${job.id}/answer`, headers, payload: { request: job.progress.question.id, text: 'approve', confirm: 'wrong' } }); expect(wrong.json().error).toContain('exact target');
    await app.inject({ method: 'POST', url: `/api/jobs/${job.id}/answer`, headers, payload: { request: job.progress.question.id, text: decision, confirm: 'kb-save' } });
    expect((await finished(jobs, job.id)).status, jobs.log(job.id)).toBe('succeeded');
    const saved = fs.readFileSync(path.join(c.root, 'wiki/topics/比较.md'), 'utf8');
    expect(saved).toContain('待用户审阅的候选正文'); expect(saved).toContain(decision === 'approve' ? 'status: published' : 'status: proposed');
    if (decision === 'approve') expect((await app.inject({ url: '/api/reader', headers })).json().pages.some((p: any) => p.path === 'topics/比较.md')).toBe(true);
    expect(JSON.parse(fs.readFileSync(path.join(dir, 'write_authorizations.json'), 'utf8'))).toEqual([{ path: 'topics/比较.md', mode: 'new' }]);
    if (decision === 'decline') {
      const payload = { skill: 'kb-save', request: '继续完善未发布候选', provider: 'fake', model: 'writer', querySession: 'saving', previous: job.id };
      faux.setResponses([call('write', { path: 'wiki/topics/比较.md', content: content + '\n补充候选说明。' }), fauxAssistantMessage('候选已补充，尚未发布。')]);
      const continued = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload });
      expect((await finished(jobs, continued.json().id)).status).toBe('succeeded');
      expect(fs.readFileSync(path.join(c.root, 'wiki/topics/比较.md'), 'utf8')).toContain('补充候选说明');
      fs.appendFileSync(path.join(c.root, 'wiki/topics/比较.md'), '\n用户外部修改。');
      const external = fs.readFileSync(path.join(c.root, 'wiki/topics/比较.md'), 'utf8');
      faux.setResponses([call('write', { path: 'wiki/topics/比较.md', content: '不能覆盖外部修改' }), ctx => { expect(JSON.stringify(ctx.messages.at(-1))).toContain('changed outside'); return fauxAssistantMessage('检测到外部修改，需要重新审阅。'); }]);
      const stale = await app.inject({ method: 'POST', url: '/api/skills/run', headers, payload: { ...payload, previous: continued.json().id } });
      expect((await finished(jobs, stale.json().id)).status).toBe('succeeded');
      expect(fs.readFileSync(path.join(c.root, 'wiki/topics/比较.md'), 'utf8')).toBe(external);
    }
  } finally { await app.close(); }
}, 60000);
