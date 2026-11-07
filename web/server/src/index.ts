import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createApp } from './app.ts';
import { configDir, repo, readJson } from './config.ts';
import { runProcess } from './engine/process.ts';
import fs from 'node:fs';

const config = configDir();
const saved = readJson<{ python?: string }>(path.join(config, 'settings.json'), {});
let python = process.env.STUDY_KB_PYTHON || saved.python;
if (!python) {
  try {
    const envs = JSON.parse(await runProcess(process.env.CONDA_EXE || 'conda', ['env', 'list', '--json'], { cwd: repo, timeout: 10000 }));
    const env = envs.envs.find((p: string) => path.basename(p) === 'study-kb');
    if (env) python = path.join(env, process.platform === 'win32' ? 'python.exe' : 'bin/python');
  } catch { /* The environment page can select an interpreter when conda is absent. */ }
  if (!python) for (const [exe, args] of [['python', []], ['py', ['-3']]] as [string, string[]][]) {
    try { const p = (await runProcess(exe, [...args, '-c', 'import sys;print(sys.executable)'], { cwd: repo, timeout: 10000 })).trim(); if (fs.existsSync(p)) { python = p; break; } } catch { /* Try the next local launcher. */ }
  }
}
const c = { repo, root: path.resolve(process.env.STUDY_KB_ROOT || repo), config,
  python: python || 'python', port: 8765, token: randomBytes(32).toString('hex') };
const { app } = await createApp(c);
try {
  await app.listen({ host: '127.0.0.1', port: c.port });
} catch (error: any) {
  if (error.code !== 'EADDRINUSE') throw error;
  console.error(`Study KB：端口 ${c.port} 已被占用。请使用原终端里的完整启动链接；若要重启，先在原终端按 Ctrl+C，再运行 npm start。\nPort ${c.port} is in use. Use the launch link from the existing terminal, or stop that service with Ctrl+C before restarting.`);
  await app.close(); process.exit(1);
}
const url = `http://127.0.0.1:${c.port}/#token=${c.token}`;
console.log(`Study KB: ${url}`);
if (!process.argv.includes('--no-open')) {
  const command = process.platform === 'win32' ? 'rundll32' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  const args = process.platform === 'win32' ? ['url.dll,FileProtocolHandler', url] : [url];
  const child = spawn(command, args, { windowsHide: true, detached: true, stdio: 'ignore' });
  child.on('error', () => {}); child.unref();
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { void app.close().then(() => process.exit()); });
