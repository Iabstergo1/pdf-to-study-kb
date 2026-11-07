import fs from 'node:fs';
import path from 'node:path';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import type { AppConfig } from '../config.ts';

export function realPath(file: string): string {
  let cur = path.resolve(file); const tail: string[] = [];
  while (!fs.existsSync(cur)) { const parent = path.dirname(cur); if (parent === cur) break; tail.unshift(path.basename(cur)); cur = parent; }
  return path.join(fs.realpathSync.native(cur), ...tail);
}
export function inside(root: string, file: string) {
  const rel = path.relative(realPath(root), realPath(file));
  return rel === '' || rel !== '..' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel);
}
export function listFiles(root: string, readPath: (raw: string) => string) {
  const found: string[] = [];
  const visit = (dir: string) => { for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    if (found.length >= 10000 || item.isSymbolicLink() || ['.git', 'node_modules', '.venv', '__pycache__'].includes(item.name) || item.name.startsWith('.env')) continue;
    const file = path.join(dir, item.name); try { readPath(file); } catch { continue; }
    if (item.isDirectory()) visit(file); else found.push(file);
  } };
  if (fs.statSync(root).isDirectory()) visit(root); else found.push(root);
  return found;
}
export function ingestFiles(c: AppConfig, source: string, raw: unknown) {
  const readPath = (name: unknown) => {
    const file = checkPath('read', name, c, source);
    if (!inside(path.join(c.root, 'wiki'), file) && !inside(path.join(c.root, 'pipeline-workspace/staging', source), file)) throw new Error('File listing outside wiki or this source staging is not allowed');
    return file;
  };
  return listFiles(readPath(raw), readPath).map(file => path.relative(c.root, file).replaceAll('\\', '/'));
}
export function checkPath(kind: 'read' | 'write', raw: unknown, c: AppConfig, source: string) {
  if (typeof raw !== 'string' || !raw || /[\0]/.test(raw) || /^[@~]/.test(raw)) throw new Error('Use a workspace path');
  const file = realPath(path.resolve(c.root, raw));
  if (inside(c.config, file) || file.split(path.sep).some(s => s === '.git' || s === '.env')) throw new Error('Private path is not allowed');
  if (kind === 'read') {
    if (!inside(c.root, file) && !inside(c.repo, file)) throw new Error('Read outside workspace is not allowed');
  } else {
    if (!inside(c.root, file)) throw new Error('Write outside workspace is not allowed');
    const staging = path.join(c.root, 'pipeline-workspace/staging', source);
    if (!inside(path.join(c.root, 'wiki'), file) && !['digest.md', 'arbitration/decisions.json'].some(rel => realPath(path.join(staging, rel)) === file)) throw new Error('Write outside wiki or this source digest/decisions is not allowed');
  }
  return file;
}
const FLAGS: Record<string, string[]> = {
  'add-source': ['source', 'domain', 'path', 'fmt'], profile: ['source'], 'source-convert': ['source', 'backend', 'mineru-policy'],
  'source-audit': ['source', 'strict'], 'arbitration-status': ['source', 'json'], 'arbitration-apply': ['source'], windows: ['source'], workorder: ['source'],
  'preflight-eval': ['source', 'strict'], 'ingest-start': ['source'], 'ingest-done': ['source'],
  'show-window': ['source', 'window', 'plain', 'verbose'], 'window-start': ['source', 'window', 'hash'],
  'window-done': ['source', 'window', 'writes', 'proposals'], 'window-fail': ['source', 'window', 'error'],
  'resolve-concept': ['mention', 'domain', 'alias', 'ref-source', 'ref-sections'],
  'check-write': ['source', 'path'], 'snapshot-page': ['source', 'path'], lint: ['source'],
  next: ['source', 'resume-packet', 'json'], status: ['json'], 'ingest-stats': ['source', 'json'],
  'sync-assets': ['source'], 'sync-overview-sources': ['apply'], 'check-session': ['id', 'saved'],
};
const BOOLS = new Set(['strict', 'plain', 'verbose', 'resume-packet', 'json', 'apply', 'saved']);
export function checkArgs(args: unknown, source: string, c: AppConfig): asserts args is string[] {
  if (!Array.isArray(args) || !args.length || args.some(a => typeof a !== 'string' || /[\0\r\n]/.test(a))) throw new Error('Invalid command arguments. Use pipeline with {"args":["command","--source","' + source + '"]}. To open a file use the separate read tool with {"path":"..."}.');
  const [cmd, ...rest] = args as string[]; const flags = FLAGS[cmd];
  if (!flags) throw new Error('Command not allowed: ' + cmd + '. read/write/edit are separate tools, not pipeline commands. Read the ingest skill for command examples; there is no help or read-source command.');
  const seen = new Set<string>();
  for (let i = 0; i < rest.length; i++) {
    const flag = rest[i].slice(2);
    if (!rest[i].startsWith('--') || !flags.includes(flag) || seen.has(flag) && flag !== 'alias') throw new Error('Flag not allowed: ' + rest[i]);
    seen.add(flag);
    if (BOOLS.has(flag)) continue;
    const value = rest[++i];
    if (!value || value.startsWith('-')) throw new Error('Invalid flag value');
    if (['source', 'ref-source'].includes(flag) && value !== source) throw new Error('Another source is not allowed');
    if (flag === 'path') {
      if (cmd === 'add-source') { if (!inside(path.join(c.root, 'books'), path.resolve(c.root, value))) throw new Error('Source must be uploaded first'); }
      else if (!inside(path.join(c.root, 'wiki'), path.resolve(c.root, 'wiki', value))) throw new Error('Page outside wiki');
    }
  }
  if (flags.includes('source') && !['next'].includes(cmd) && !seen.has('source')) throw new Error('Source is required');
  if (cmd === 'resolve-concept' && !seen.has('ref-source')) throw new Error('Reference source is required');
}
export function guard(c: AppConfig, source: string, run: (args: string[]) => Promise<string>) {
  return (pi: ExtensionAPI) => { pi.on('tool_call', async event => {
    try {
      if (event.toolName === 'pipeline') {
        checkArgs(event.input.args, source, c);
        if (event.input.args[0] === 'sync-overview-sources' && event.input.args.includes('--apply')) {
          const result = await run(['check-write', '--source', source, '--path', 'overview.md']);
          if (!result.startsWith('ALLOW ')) throw new Error(result);
        }
      }
      else if (event.toolName === 'files') checkPath('read', (event.input as Record<string, unknown>).path, c, source);
      else if (['read', 'write', 'edit'].includes(event.toolName)) {
        const kind = event.toolName === 'read' ? 'read' : 'write';
        const file = checkPath(kind, (event.input as Record<string, unknown>).path, c, source);
        if (kind === 'write' && inside(path.join(c.root, 'wiki'), file)) {
          const result = await run(['check-write', '--source', source, '--path', path.relative(path.join(c.root, 'wiki'), file).replaceAll('\\', '/')]);
          if (!result.startsWith('ALLOW ')) throw new Error(result);
        }
      } else throw new Error('Tool not allowed');
    } catch (error) { return { block: true, reason: String(error) }; }
  }); };
}
