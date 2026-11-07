import { spawn, type ChildProcess } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import type { AppConfig } from '../config.ts';
import path from 'node:path';

export function killTree(child: ChildProcess) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    killer.on('error', () => child.kill());
  } else { try { process.kill(-child.pid, 'SIGKILL'); } catch { child.kill(); } }
}
export async function runProcess(exe: string, args: string[], options: { cwd: string; env?: NodeJS.ProcessEnv; signal?: AbortSignal; onOutput?: (s: string) => void; timeout?: number; stdoutOnly?: boolean; input?: string; maxOutput?: number }) {
  options.signal?.throwIfAborted();
  return new Promise<string>((resolve, reject) => {
    const child = spawn(exe, args, { cwd: options.cwd, env: options.env || process.env, windowsHide: true, detached: process.platform !== 'win32', stdio: [options.input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
    if (options.input !== undefined) { child.stdin!.on('error', () => {}); child.stdin!.end(options.input, 'utf8'); }
    let output = ''; let stdout = ''; let timedOut = false;
    const append = (value: string, isStdout: boolean) => { output = (output + value).slice(-(options.maxOutput || 2_000_000)); if (isStdout) stdout = (stdout + value).slice(-(options.maxOutput || 2_000_000)); options.onOutput?.(value); };
    for (const stream of [child.stdout, child.stderr]) {
      const decoder = new StringDecoder('utf8');
      stream!.on('data', chunk => append(decoder.write(chunk), stream === child.stdout));
      stream!.on('end', () => append(decoder.end(), stream === child.stdout));
    }
    const abort = () => killTree(child);
    const timer = setTimeout(() => { timedOut = true; abort(); }, options.timeout || 30 * 60_000);
    options.signal?.addEventListener('abort', abort, { once: true });
    const cleanup = () => { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); };
    child.on('error', error => { cleanup(); reject(error); });
    child.on('close', code => {
      cleanup();
      if (options.signal?.aborted) reject(new Error('Cancelled'));
      else if (timedOut) reject(new Error('Process timed out'));
      else if (code !== 0) reject(new Error(`exit ${code}: ${output}`));
      else resolve(options.stdoutOnly ? stdout : output);
    });
  });
}
export function pipeline(c: AppConfig, args: string[], signal?: AbortSignal, onOutput?: (s: string) => void, input?: unknown) {
  const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', STUDY_KB_ROOT: c.root };
  return runProcess(c.python, [path.join(c.repo, 'scripts/pipeline.py'), ...args], { cwd: c.root, env, signal, onOutput, input: input === undefined ? undefined : JSON.stringify(input), maxOutput: input === undefined ? undefined : 64 * 1024 * 1024 });
}
