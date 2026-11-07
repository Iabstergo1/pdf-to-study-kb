import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

export const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
export function configDir() {
  return process.platform === 'win32' ? path.join(process.env.APPDATA || os.homedir(), 'study-kb')
    : process.platform === 'darwin' ? path.join(os.homedir(), 'Library/Application Support/study-kb')
      : path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'study-kb');
}
export function readJson<T>(file: string, fallback: T): T {
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : fallback;
}
export function saveJson(file: string, value: unknown) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file + '.tmp', JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(file + '.tmp', file);
}
export type AppConfig = { root: string; repo: string; config: string; python: string; port: number; token: string };
export function redact(text: string, secrets: string[] = []) {
  for (const secret of secrets.filter(Boolean).sort((a, b) => b.length - a.length)) text = text.split(secret).join('[redacted]');
  return text.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [redacted]').replace(/sk-[A-Za-z0-9_-]{8,}/g, '[redacted]');
}
