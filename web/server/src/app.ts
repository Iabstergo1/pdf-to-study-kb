import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { Transform } from 'node:stream';
import { pipeline as streamPipeline } from 'node:stream/promises';
import Fastify from 'fastify';
import multipart from '@fastify/multipart';
import serveStatic from '@fastify/static';
import { z } from 'zod';
import { localSecurity } from './security.ts';
import { Models, selectionSchema } from './llm/models.ts';
import { vision } from './llm/vision.ts';
import { ingest } from './ingest/session.ts';
import { inside } from './ingest/guard.ts';
import { Jobs } from './engine/jobs.ts';
import { commands, buildArgs, target, sourceId, domainId, loadCommands } from './engine/commands.ts';
import { pipeline, runProcess } from './engine/process.ts';
import { readJson, saveJson, type AppConfig } from './config.ts';
import { skills, skillInput, runSkill } from './skills/session.ts';
import { prepareEvolution, collectEvolution, evolutionPreview, adoptEvolution } from './skills/evolution.ts';
import { readerInfo, registerStudy } from './study.ts';

const selection = selectionSchema;
const taskInput = z.object({ command: z.string(), params: z.record(z.string(), z.unknown()).default({}), apply: z.boolean().default(false), confirm: z.string().optional() });
export async function createApp(c: AppConfig) {
  const app = Fastify({ logger: false, bodyLimit: 2 * 1024 * 1024 });
  const models = new Models(c);
  const jobs = new Jobs(c, models.clean);
  let catalogue: Promise<void> | undefined;
  const ensureCatalogue = () => catalogue ||= pipeline(c, ['--describe-commands']).then(text => { loadCommands(JSON.parse(text)); }).catch(error => { catalogue = undefined; throw error; });
  const serverFiles = path.join(c.repo, 'web/server/src');
  const revision = () => fs.existsSync(serverFiles) ? createHash('sha256').update(fs.readdirSync(serverFiles, { recursive: true, withFileTypes: true }).filter(f => f.isFile() && f.name.endsWith('.ts')).map(f => path.join(f.parentPath, f.name)).sort().map(file => file + ':' + fs.readFileSync(file, 'utf8')).join('\n')).digest('hex') : '';
  const loadedRevision = revision();
  localSecurity(app, c);
  registerStudy(app, c);
  app.setErrorHandler((error: any, _request, reply) => reply.code(error.statusCode || (error instanceof z.ZodError ? 400 : 500)).send({ error: models.clean(error.message), code: error.code }));
  await app.register(multipart, { limits: { files: 1, fileSize: 512 * 1024 * 1024 } });
  const status = async () => JSON.parse(await pipeline(c, ['status', '--json']));
  const assertUnlocked = async (resumeSource?: string) => {
    const st = await status();
    const ownInterrupted = resumeSource && st.lock?.holder === resumeSource && !jobs.active && jobs.history.some(j => j.command === 'ingest' && j.source === resumeSource && ['cancelled', 'interrupted', 'paused', 'failed'].includes(j.status));
    if (st.lock && !st.lock.stale && !ownInterrupted) throw Object.assign(new Error('The vault is being edited by another ingest session'), { statusCode: 409 });
  };
  app.get('/api/status', async () => ({ ...await status(), active_job: jobs.active, workspace: c.root, restart_required: revision() !== loadedRevision }));
  app.get('/api/next', async () => JSON.parse(await pipeline(c, ['next', '--json'])));
  app.get('/api/commands', async () => { await ensureCatalogue(); return Object.entries(commands).map(([name, spec]) => ({ name, description: spec.description, kind: spec.kind, target: spec.target, writes: !!spec.writes, params: z.toJSONSchema(z.object(spec.params)) })); });
  app.get('/api/jobs', async () => jobs.history);
  app.get('/api/jobs/:id/log', async request => ({ output: jobs.log((request.params as any).id) }));
  app.get('/api/jobs/:id/events', async (request, reply) => {
    const id = (request.params as any).id; const initial = jobs.log(id);
    reply.hijack(); reply.raw.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
    reply.raw.write(`data: ${JSON.stringify(initial)}\n\n`);
    if (jobs.active?.id !== id) { reply.raw.write('event: done\ndata: {}\n\n'); reply.raw.end(); return; }
    const send = (value: string | null) => {
      if (value === null) { reply.raw.write('event: done\ndata: {}\n\n'); reply.raw.end(); }
      else reply.raw.write(`data: ${JSON.stringify(value)}\n\n`);
    };
    jobs.on(id, send); reply.raw.on('close', () => jobs.off(id, send));
  });
  app.post('/api/jobs/cancel', async () => { jobs.cancel(); return { ok: true }; });
  app.post('/api/jobs/:id/answer', async request => {
    const value = z.object({ request: z.uuid(), text: z.string().min(1).max(20000), confirm: z.string().optional() }).parse(request.body);
    jobs.reply((request.params as any).id, value.request, value.text, value.confirm); return { ok: true };
  });
  app.get('/api/skills', async () => Object.entries(skills).map(([id, spec]) => ({ id, ...spec })));
  app.get('/api/skills/jobs/:id', async request => {
    const id = z.uuid().parse((request.params as any).id);
    if (!jobs.history.some(j => j.id === id && j.command in skills)) throw new Error('Unknown skill job');
    const value = readJson<any>(path.join(c.root, 'pipeline-workspace/web-sessions/skills', id + '.json'), null);
    if (!value) throw new Error('No session was created for this job');
    return { input: value.input, runId: value.runId };
  });
  app.get('/api/query-sessions', async () => {
    const dir = path.join(c.root, 'pipeline-workspace/query-sessions');
    if (!fs.existsSync(dir)) return [];
    return fs.readdirSync(dir, { withFileTypes: true }).filter(e => e.isDirectory() && sourceId.safeParse(e.name).success && inside(dir, path.join(dir, e.name))).map(e => ({ id: e.name, question: fs.existsSync(path.join(dir, e.name, 'question.md')) ? models.clean(fs.readFileSync(path.join(dir, e.name, 'question.md'), 'utf8')).slice(0, 500) : '', saved: fs.existsSync(path.join(dir, e.name, 'decision.md')) }));
  });
  app.post('/api/skills/run', async request => {
    await ensureCatalogue(); const input = skillInput.parse(request.body);
    if (input.skill !== 'pipeline-doctor') await assertUnlocked();
    if (skills[input.skill].vision && !models.isVerified(input.provider, input.model)) throw Object.assign(new Error('Pass the image test first'), { statusCode: 409 });
    return jobs.start(input.skill, async (job, signal, log) => {
      jobs.dryRuns.clear();
      let execution = c, context = {};
      if (input.skill === 'skill-evolve') {
        if (!input.targetSkill) throw new Error('Choose the project skill to improve');
        if (input.previous) throw new Error('Review the existing candidate and its logs before starting a new evolution; automatic replay cannot safely recreate its Git branch');
        execution = await prepareEvolution(c, job.id, input.targetSkill, signal, log);
        context = { candidate: job.id, worktree: execution.root, homeRoot: c.root };
      }
      await runSkill(execution, models, input, job, jobs, signal, log, context);
      if (input.skill === 'skill-evolve') {
        const outputs: string[] = [];
        for (const rel of job.progress?.outputs || []) {
          const file = path.resolve(c.root, rel), reports = path.join(execution.root, 'pipeline-workspace/reports');
          if (inside(reports, file) && fs.existsSync(file)) {
            const dest = path.join(c.root, 'pipeline-workspace/reports/skill-evolve', job.id, path.relative(reports, file));
            fs.mkdirSync(path.dirname(dest), { recursive: true }); fs.copyFileSync(file, dest); outputs.push(path.relative(c.root, dest).replaceAll('\\', '/'));
          }
        }
        jobs.update(job, { candidate: collectEvolution(c, job.id), outputs });
      }
    }, input.source);
  });
  app.get('/api/skills/candidates/:id', async request => evolutionPreview(c, z.uuid().parse((request.params as any).id)));
  app.post('/api/skills/candidates/:id/adopt', async request => {
    const id = z.uuid().parse((request.params as any).id);
    const body = z.object({ revision: z.string(), confirm: z.string() }).parse(request.body);
    if (body.confirm !== id) throw new Error('Type the exact candidate ID to confirm');
    const preview = await evolutionPreview(c, id);
    if (preview.revision !== body.revision) throw new Error('Preview changed; review it again');
    await assertUnlocked();
    return jobs.start('skill-adopt', async (_job, signal, log) => { jobs.dryRuns.clear(); await adoptEvolution(c, id, body.revision, signal, log); const original = jobs.history.find(j => j.id === id); if (original) jobs.update(original, { candidate: { ...original.progress?.candidate, state: 'adopted' } }); });
  });
  app.post('/api/jobs', async request => {
    await ensureCatalogue();
    const body = taskInput.parse(request.body); const args = buildArgs(body.command, body.params, body.apply); const spec = commands[body.command];
    if (spec.kind === 'read') throw new Error('Use the read endpoint');
    if (body.command === 'add-source' && !inside(path.join(c.root, 'books'), path.resolve(c.root, String(body.params.path)))) throw new Error('Use an uploaded source path');
    const key = JSON.stringify(buildArgs(body.command, body.params));
    if ((body.apply || spec.kind === 'maintenance') && body.confirm !== target(body.command, body.params)) throw new Error('Type the exact target name to confirm');
    if (body.apply && !jobs.dryRuns.has(key)) throw new Error('Run a successful preview with the same arguments first');
    const writes = spec.writes && (spec.kind !== 'apply' || body.apply);
    if (writes) await assertUnlocked();
    return jobs.start(body.command, async (_job, signal, log) => {
      if (writes) jobs.dryRuns.clear();
      await pipeline(c, args, signal, log);
      if (spec.kind === 'apply' && !body.apply) jobs.dryRuns.add(key);
    }, typeof body.params.source === 'string' ? body.params.source : undefined);
  });
  app.post('/api/read', async request => {
    await ensureCatalogue();
    const body = taskInput.parse(request.body);
    if (commands[body.command]?.kind !== 'read') throw new Error('Read command required');
    const output = await pipeline(c, buildArgs(body.command, body.params));
    try { return JSON.parse(output); } catch { return { output }; }
  });
  app.get('/api/settings', async () => {
    const selected = readJson<{ provider: string; model: string } | null>(path.join(c.config, 'selection.json'), null);
    return { python: c.python, providers: models.public(), selected, verified: selected ? models.isVerified(selected.provider, selected.model) : false };
  });
  app.post('/api/settings', async request => {
    if (jobs.active) throw Object.assign(new Error('Stop the running job before changing settings'), { statusCode: 409 });
    const body = z.object({ python: z.string().min(1).optional(), selected: selection.optional() }).strict().parse(request.body);
    if (body.python) { c.python = body.python; const file = path.join(c.config, 'settings.json'); saveJson(file, { ...readJson(file, {}), python: body.python }); }
    if (body.selected) await models.select(body.selected);
    return { ok: true };
  });
  app.get('/api/providers/catalogue', async () => models.catalogue());
  app.post('/api/providers', async request => { if (jobs.active) throw Object.assign(new Error('A job is running'), { statusCode: 409 }); return models.save(request.body); });
  app.get('/api/providers/:id/models', async request => models.list((request.params as any).id, (request.query as any).refresh === 'true'));
  app.post('/api/providers/:id/fetch-models', async request => models.fetchModels((request.params as any).id));
  app.post('/api/providers/:id/login', async request => {
    if (jobs.active) throw Object.assign(new Error('A job is running'), { statusCode: 409 });
    const id = (request.params as any).id; models.find(id);
    if (models.loginState.state === 'pending') throw Object.assign(new Error('Login already running'), { statusCode: 409 });
    void models.login(id).catch(e => { models.loginState = { state: 'failed', message: models.clean(String(e)) }; });
    return { ok: true };
  });
  app.get('/api/login', async () => models.loginState);
  app.post('/api/login/answer', async request => { models.loginAnswer(z.object({ text: z.string() }).parse(request.body).text); return { ok: true }; });
  app.post('/api/vision', async request => {
    const body = selection.parse(request.body);
    return jobs.start('vision', (job, signal, log) => vision(c, models, body.provider, body.model, signal, log, value => jobs.update(job, value), body.thinkingLevel));
  });
  app.get('/api/doctor', async () => {
    const checks: { id: string; ok: boolean; detail: string; fix: string }[] = [];
    const check = async (id: string, work: () => Promise<string>, fix = '') => {
      try { checks.push({ id, ok: true, detail: models.clean(await work()), fix }); }
      catch (e) { checks.push({ id, ok: false, detail: models.clean(String(e)), fix }); }
    };
    const [major, minor] = process.versions.node.split('.').map(Number);
    checks.push({ id: 'node', ok: major > 22 || major === 22 && minor >= 19, detail: process.version, fix: 'Node.js >= 22.19' });
    await check('python', () => runProcess(c.python, ['-c', 'import sys,fitz,yaml,markdown_it; print(sys.executable); print(sys.version)'], { cwd: c.repo, timeout: 20000 }), 'conda create -n study-kb python=3.12\nconda run -n study-kb python -m pip install -r requirements.txt');
    await check('pipeline', () => pipeline(c, ['status', '--json']));
    await check('mineru', () => runProcess(c.python, ['-c', 'import mineru; print("MinerU installed")'], { cwd: c.repo, timeout: 20000 }), 'conda run -n study-kb python scripts/install_mineru.py');
    await check('gpu', () => runProcess('nvidia-smi', ['--query-gpu=name,memory.total', '--format=csv,noheader'], { cwd: c.repo, timeout: 10000 }));
    checks.push({ id: 'vault', ok: fs.existsSync(path.join(c.root, 'wiki/overview.md')), detail: path.join(c.root, 'wiki'), fix: 'init-vault' });
    const disk = fs.statfsSync(c.root); checks.push({ id: 'disk', ok: disk.bavail * disk.bsize > 1024 ** 3, detail: `${(disk.bavail * disk.bsize / 1024 ** 3).toFixed(1)} GB`, fix: '' });
    const obsidian = process.platform === 'win32' ? path.join(process.env.LOCALAPPDATA || '', 'Obsidian/Obsidian.exe') : '/Applications/Obsidian.app';
    checks.push({ id: 'obsidian', ok: fs.existsSync(obsidian), detail: 'Optional', fix: 'https://obsidian.md/download' });
    return checks;
  });
  app.post('/api/library/upload', async request => {
    if (jobs.active) throw Object.assign(new Error('A job is running'), { statusCode: 409 });
    const domain = domainId.parse((request.query as any).domain);
    const data = await request.file(); if (!data) throw new Error('Choose a source file');
    const ext = path.extname(data.filename).toLowerCase();
    if (!['.pdf', '.md', '.docx', '.pptx'].includes(ext)) throw new Error('Supported formats: PDF, DOCX, PPTX, Markdown');
    const dir = path.join(c.root, 'books', domain, 'input');
    if (!inside(c.root, dir)) throw new Error('Upload directory escapes workspace');
    fs.mkdirSync(dir, { recursive: true });
    const temp = path.join(dir, randomUUID() + '.upload'); const hash = createHash('sha256');
    try {
      await streamPipeline(data.file, new Transform({ transform(chunk, _encoding, cb) { hash.update(chunk); cb(null, chunk); } }), fs.createWriteStream(temp, { flags: 'wx' }));
      if (data.file.truncated) throw new Error('File exceeds 512 MB');
      const digest = hash.digest('hex');
      const name = data.filename.replace(/[^\p{L}\p{N}_.-]/gu, '_').slice(-150);
      const file = path.join(dir, digest.slice(0, 12) + '-' + name); const duplicate = fs.existsSync(file);
      if (duplicate) fs.unlinkSync(temp); else fs.renameSync(temp, file);
      return { path: path.relative(c.root, file).replaceAll('\\', '/'), source: name.slice(0, -ext.length).replace(/^[^\p{L}\p{N}_]+/u, '').slice(0, 128) || digest.slice(0, 12), domain, fmt: ext.slice(1), sha256: digest, duplicate };
    } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  });
  const prepareInput = z.object({ source: sourceId, domain: domainId, path: z.string(), fmt: z.enum(['pdf', 'md', 'docx', 'pptx']), purpose: z.string().max(20000).optional() });
  const prepare = async (body: z.infer<typeof prepareInput>, job: import('./engine/jobs.ts').Job, signal: AbortSignal, log: (s: string) => void) => {
    if (!inside(path.join(c.root, 'books'), path.resolve(c.root, body.path))) throw new Error('Source must be uploaded first');
      jobs.dryRuns.clear();
      const run = (args: string[]) => { jobs.update(job, { phase: 'prepare', command: args[0] }); return pipeline(c, args, signal, log); };
      if (!fs.existsSync(path.join(c.root, 'wiki/overview.md'))) await run(['init-vault']);
      let row = (await status()).sources.find((s: any) => s.source_id === body.source);
      if (row) {
        const known = JSON.parse(await pipeline(c, ['ingest-stats', '--source', body.source, '--json'])).source;
        if (known.domain !== body.domain || known.format !== body.fmt || !known.path || path.resolve(c.root, known.path) !== path.resolve(c.root, body.path)) throw new Error('This source ID belongs to another upload; choose a new ID');
      }
      if (!row) { await run([...buildArgs('add-source', { source: body.source, domain: body.domain, path: body.path, fmt: body.fmt }), ...(body.purpose ? ['--purpose', body.purpose] : [])]); row = { current_stage: 'registered' }; }
      if (row.current_stage === 'registered') { await run(['profile', '--source', body.source]); row.current_stage = 'profiled'; }
      if (row.current_stage === 'profiled') { await run(['source-convert', '--source', body.source]); row.current_stage = 'converted'; }
      if (row.current_stage === 'converted') {
        await run(['source-audit', '--source', body.source, '--strict']);
        const arbitration = JSON.parse(await run(['arbitration-status', '--source', body.source, '--json']));
        if (arbitration.pending || arbitration.needs_human.length) { jobs.update(job, { phase: 'prepare', command: 'arbitration', waiting: arbitration.needs_human.length ? 'human' : 'arbitration', arbitration }); log('预处理等待分歧核对。请在本资料卡中选择“开始 AI 入库”；需要人工判断的页面会在详情中列出。 / Preparation awaits evidence review.\n'); return arbitration; }
        if (arbitration.candidates) await run(['arbitration-apply', '--source', body.source]);
        await run(['windows', '--source', body.source]); row.current_stage = 'windowed';
      }
      if (row.current_stage === 'windowed') await run(['workorder', '--source', body.source]);
      await run(['preflight-eval', '--source', body.source, '--strict']);
      jobs.update(job, { phase: 'ready', waiting: null });
      return null;
  };
  app.post('/api/library/prepare', async request => {
    const body = prepareInput.parse(request.body);
    await assertUnlocked();
    return jobs.start('prepare', async (job, signal, log) => { await prepare(body, job, signal, log); }, body.source);
  });
  app.get('/api/library/:source', async request => {
    const source = sourceId.parse((request.params as any).source);
    const staging = path.join(c.root, 'pipeline-workspace/staging', source);
    const arbitration = fs.existsSync(path.join(staging, 'evidence.json')) ? JSON.parse(await pipeline(c, ['arbitration-status', '--source', source, '--json'])) : null;
    const stats = JSON.parse(await pipeline(c, ['ingest-stats', '--source', source, '--json']));
    return { arbitration, stats, review: await readerInfo(c, 'review', { source }), prepare: stats.source.path ? { source, domain: stats.source.domain, fmt: stats.source.format, path: stats.source.path } : null, profile: readJson(path.join(staging, 'parse_report.json'), null) };
  });
  app.get('/api/library/:source/image/:page', async (request, reply) => {
    const { source, page } = request.params as any; sourceId.parse(source);
    const staging = path.join(c.root, 'pipeline-workspace/staging', source);
    const queue = readJson<{ packets: any[] }>(path.join(staging, 'arbitration/queue.json'), { packets: [] });
    const image = queue.packets.find(p => p.page === Number(page))?.page_image;
    if (!image) return reply.code(404).send({ error: 'Image not found' });
    const file = path.resolve(staging, image); if (!inside(staging, file)) throw new Error('Invalid image path');
    return reply.type('image/png').send(fs.createReadStream(file));
  });
  app.post('/api/ingest', async request => {
    const body = selection.extend({ source: sourceId, mode: z.enum(['start', 'resume', 'repair', 'arbitrate', 'rework']), instructions: z.string().max(20000).optional(), confirm: z.string().optional() }).parse(request.body);
    if (body.mode === 'rework' && (body.confirm !== body.source || !body.instructions?.trim())) throw new Error('Describe the content findings and type the exact source name before reopening');
    if (!models.isVerified(body.provider, body.model)) throw Object.assign(new Error('Pass the image test first'), { code: 'VISION_REQUIRED', statusCode: 409 });
    await assertUnlocked(['resume', 'repair'].includes(body.mode) ? body.source : undefined);
    return jobs.start('ingest', async (job, signal, log) => {
      job.mode = body.mode; jobs.dryRuns.clear();
      const update = (value: object) => jobs.update(job, value);
      const readStats = async () => JSON.parse(await pipeline(c, ['ingest-stats', '--source', body.source, '--json'], signal));
      const initial = (await readStats()).source;
      if (body.mode === 'rework') {
        if (initial.current_status !== 'published') throw new Error('Content rework requires a published source; use resume or repair for unfinished ingest');
        await pipeline(c, ['reopen', '--source', body.source], signal, log);
        fs.writeFileSync(path.join(c.root, 'pipeline-workspace/staging', body.source, 'rework-request.md'), body.instructions!, 'utf8');
      }
      if (body.mode !== 'arbitrate' && ['registered', 'profiled', 'converted', 'windowed'].includes(initial.current_stage)) {
        const prep = { source: body.source, domain: initial.domain, fmt: initial.format, path: initial.path };
        let arbitration = await prepare(prep, job, signal, log);
        if (arbitration?.needs_human.length) { update({ waiting: 'human' }); return; }
        if (arbitration?.pending) {
          await ingest(c, models, { ...body, mode: 'arbitrate' }, job, signal, log, update);
          arbitration = await prepare(prep, job, signal, log);
          if (arbitration) { update({ waiting: arbitration.needs_human.length ? 'human' : 'arbitration' }); return; }
        }
      }
      const state = (await readStats()).source;
      const mode = body.mode === 'arbitrate' || body.mode === 'repair' ? body.mode : ['ingesting', 'ingested', 'lint'].includes(state.current_stage) ? 'resume' : 'start';
      await ingest(c, models, { ...body, mode }, job, signal, log, update);
    }, body.source);
  });
  const dataDir = path.join(c.root, 'pipeline-workspace/exports/site-data');
  const manifest = () => { if (!fs.existsSync(path.join(dataDir, 'site.json'))) throw Object.assign(new Error('Export reader data first'), { statusCode: 404 }); return readJson<any>(path.join(dataDir, 'site.json'), {}); };
  app.get('/api/reader', async () => manifest());
  app.get('/api/reader/page', async request => {
    const page = manifest().pages.find((p: any) => p.path === (request.query as any).path);
    if (!page || !inside(dataDir, path.join(dataDir, page.file))) throw Object.assign(new Error('Unknown page'), { statusCode: 404 });
    return readJson(path.join(dataDir, page.file), {});
  });
  app.get('/api/reader/:view', async (request, reply) => {
    const view = (request.params as any).view;
    if (!['graph', 'quiz', 'propositions', 'graph-view'].includes(view)) return reply.code(404).send();
    if (view === 'graph-view') return reply.header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'").type('text/html; charset=utf-8').send(fs.readFileSync(path.join(dataDir, 'graph-view.html'), 'utf8'));
    return readJson(path.join(dataDir, view + '.json'), []);
  });
  app.get('/api/reader/media', async (request, reply) => {
    const rel = (request.query as any).path;
    const allowed = manifest().pages.flatMap((p: any) => (p.source_pages || []).flatMap((m: any) => [m.original, m.thumbnail]));
    if (!allowed.includes(rel) || !inside(dataDir, path.join(dataDir, rel))) return reply.code(404).send();
    return reply.type('image/png').send(fs.createReadStream(path.join(dataDir, rel)));
  });
  app.get('/api/download/:kind', async (request, reply) => {
    const kind = (request.params as any).kind;
    const file = kind === 'site' ? path.join(c.root, 'pipeline-workspace/exports/site/study-kb.html') : kind === 'anki' ? path.join(c.root, 'wiki/anki-export.generated.tsv') : '';
    if (!file || !fs.existsSync(file)) return reply.code(404).send({ error: 'Run export first' });
    return reply.header('Content-Disposition', `attachment; filename="${path.basename(file)}"`).type('application/octet-stream').send(fs.createReadStream(file));
  });
  const reportRoots = ['wiki/Review-Queue', 'pipeline-workspace/reports', 'pipeline-workspace/query-sessions', 'pipeline-workspace/skill-evolution/candidates'];
  app.get('/api/reports', async () => reportRoots.flatMap(rel => {
    const dir = path.join(c.root, rel); if (!fs.existsSync(dir)) return [];
    return fs.readdirSync(dir, { recursive: true, withFileTypes: true }).filter(e => e.isFile() && /\.(md|json|txt)$/.test(e.name)).map(e => path.relative(c.root, path.join(e.parentPath, e.name)).replaceAll('\\', '/'));
  }));
  app.get('/api/report', async request => {
    const file = path.resolve(c.root, String((request.query as any).path));
    if (!reportRoots.some(rel => inside(path.join(c.root, rel), file))) throw new Error('Report not accessible');
    const rendered = await readerInfo(c, 'report', { path: path.relative(c.root, file).replaceAll('\\', '/') });
    return { text: models.clean(rendered.text), html: models.clean(rendered.html) };
  });
  app.get('/api/help/:lang/:name', async request => {
    const { lang, name } = request.params as any;
    if (!['zh', 'en'].includes(lang) || !/^[a-z-]+$/.test(name)) throw new Error('Unknown guide');
    return { text: fs.readFileSync(path.join(c.repo, 'docs/guide', lang, name + '.md'), 'utf8') };
  });
  const client = path.join(c.repo, 'web/client/dist');
  if (fs.existsSync(client)) await app.register(serveStatic, { root: client, wildcard: false });
  app.setNotFoundHandler((_request, reply) => reply.code(404).send({ error: 'Not found' }));
  app.addHook('onClose', async () => { await jobs.stop(); });
  return { app, jobs, models };
}
