import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { readJson, saveJson, type AppConfig } from '../config.ts';

export type Job = { id: string; command: string; source?: string; mode?: string; status: string; started_at: string; finished_at?: string; message?: string; progress?: any; error_kind?: string };
export class Jobs extends EventEmitter {
  active?: Job;
  controller?: AbortController;
  history: Job[];
  dryRuns = new Set<string>();
  private answer?: { job: string; request: string; target?: string; resolve: (text: string) => void; reject: (error: Error) => void };
  private dir: string;
  private clean: (s: string) => string;
  constructor(c: AppConfig, clean: (s: string) => string) {
    super();
    this.clean = clean;
    this.dir = path.join(c.root, 'pipeline-workspace/web-jobs-node');
    this.history = readJson<Job[]>(path.join(this.dir, 'jobs.json'), []).map(j => j.status === 'running' ? { ...j, status: 'interrupted', progress: { ...j.progress, question: null, waiting: null } } : j);
  }
  private persist() { saveJson(path.join(this.dir, 'jobs.json'), this.history); }
  update(job: Job, progress: object) { job.progress = { ...job.progress, ...progress }; this.persist(); }
  ask(job: Job, signal: AbortSignal, question: { title: string; detail: string; target?: string }) {
    signal.throwIfAborted();
    if (this.answer) throw new Error('A decision is already pending');
    const id = randomUUID();
    this.update(job, { question: { id, ...question }, waiting: 'human' });
    return new Promise<string>((resolve, reject) => {
      const cleanup = () => { this.answer = undefined; signal.removeEventListener('abort', abort); this.update(job, { question: null, waiting: null }); };
      const abort = () => { cleanup(); reject(new Error('Cancelled')); };
      this.answer = { job: job.id, request: id, target: question.target, resolve: text => { cleanup(); resolve(text); }, reject: error => { cleanup(); reject(error); } };
      signal.addEventListener('abort', abort, { once: true });
    });
  }
  reply(job: string, request: string, text: string, confirm?: string) {
    const pending = this.answer;
    if (!pending || pending.job !== job || pending.request !== request) throw new Error('This question is no longer pending');
    if (pending.target && text === 'approve' && confirm !== pending.target) throw new Error('Type the exact target name to confirm');
    pending.resolve(text);
  }
  start(command: string, work: (job: Job, signal: AbortSignal, log: (s: string) => void) => Promise<void>, source?: string) {
    if (this.active) throw Object.assign(new Error('Another job is running'), { statusCode: 409 });
    const job: Job = { id: randomUUID(), command, source, status: 'running', started_at: new Date().toISOString() };
    this.active = job; this.controller = new AbortController(); this.history.unshift(job); this.persist();
    const signal = this.controller.signal;
    const log = (s: string) => { const safe = this.clean(s); fs.appendFileSync(path.join(this.dir, job.id + '.log'), safe); this.emit(job.id, safe); };
    void work(job, signal, log).then(() => { job.status = signal.aborted ? 'cancelled' : 'succeeded'; })
      .catch(error => {
        const message = this.clean(String(error));
        job.message = message.slice(-8000);
        job.error_kind = /429|quota|usage.limit|rate.limit/i.test(message) ? 'quota'
          : /401|403|unauthorized|forbidden|invalid.grant|invalid.client|refresh.token.(?:expired|invalidated|reused)/i.test(message) ? 'auth'
            : /network|fetch|connect|timed?\s*out|terminated|econnreset|socket/i.test(message) ? 'network'
              : /auth/i.test(message) ? 'auth' : /image|vision/i.test(message) ? 'vision' : 'task';
        job.status = signal.aborted ? 'cancelled' : job.error_kind === 'quota' ? 'paused' : 'failed'; log(message + '\n');
      }).finally(() => {
        job.finished_at = new Date().toISOString(); this.active = undefined; this.controller = undefined;
        this.persist(); this.emit(job.id, null);
      });
    return job;
  }
  cancel() { this.controller?.abort(); }
  async stop() {
    if (!this.active) return;
    const id = this.active.id;
    const finished = new Promise<void>(resolve => {
      const listener = (value: string | null) => { if (value === null) { this.off(id, listener); resolve(); } };
      this.on(id, listener);
    });
    this.cancel(); await finished;
  }
  log(id: string) {
    if (!this.history.some(j => j.id === id)) throw Object.assign(new Error('Unknown job'), { statusCode: 404 });
    const file = path.join(this.dir, id + '.log');
    return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').slice(-200_000) : '';
  }
}
