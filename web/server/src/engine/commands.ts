import { z } from 'zod';

const text = z.string().min(1).max(1024).refine(s => !s.startsWith('-') && !/[\r\n\0]/.test(s));
export const sourceId = text.refine(s => /^[\p{L}\p{N}_][\p{L}\p{N}_.-]{0,127}$/u.test(s));
export const domainId = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/).max(80);
const bool = z.boolean().optional();
const src = { source: sourceId };
type Spec = { kind: 'read' | 'task' | 'apply' | 'maintenance'; params: Record<string, z.ZodType>; fixed?: string[]; target?: string; writes?: boolean; description?: string; flags?: Record<string, string> };
const command = (kind: Spec['kind'], params: Spec['params'] = {}, extra: Partial<Spec> = {}): Spec => ({ kind, params, ...extra });
export const commands: Record<string, Spec> = {
  status: command('read', {}, { fixed: ['--json'] }), next: command('read', {}, { fixed: ['--json'] }),
  'ingest-stats': command('read', src, { fixed: ['--json'] }),
  'arbitration-status': command('read', src, { fixed: ['--json'] }),
  'init-vault': command('task', {}, { writes: true }),
  'preflight-eval': command('task', { ...src, strict: bool }, { writes: true }),
  'vault-lint': command('task'), 'graph-lint': command('task'),
  ...Object.fromEntries(['rebuild-registry', 'rebuild-graph', 'rebuild-quiz', 'rebuild-propositions', 'rebuild-source-images', 'export-anki'].map(n => [n, command('task', {}, { writes: true })])),
  'export-site': command('task', { with_images: bool, format: z.enum(['html', 'json']).optional() }, { writes: true }),
  'sync-assets': command('task', src, { writes: true }),
  'add-source': command('task', { ...src, domain: domainId, path: text, fmt: z.enum(['pdf', 'md', 'docx', 'pptx']) }, { writes: true }),
  profile: command('task', src, { writes: true }),
  'source-convert': command('task', { ...src, backend: z.enum(['auto', 'pymupdf', 'mineru']).optional(), mineru_policy: z.enum(['conservative', 'aggressive']).optional() }, { writes: true }),
  'source-audit': command('task', { ...src, strict: bool }, { writes: true }),
  ...Object.fromEntries(['arbitration-apply', 'windows', 'workorder'].map(n => [n, command('task', src, { writes: true })])),
  'arbitration-resolve': command('task', { ...src, page: z.number().int().positive(), decision: z.enum(['render', 'ignore']), reason: text }, { writes: true }),
  'reset-source': command('apply', { ...src, to: z.enum(['registered', 'profiled', 'converted', 'windowed', 'workorder_ready']) }, { writes: true, target: 'source' }),
  'retract-source': command('apply', { ...src, to: z.enum(['workorder_ready', 'registered']).optional() }, { writes: true, target: 'source' }),
  'staging-clean': command('apply', src, { writes: true, target: 'source' }),
  'sync-overview-sources': command('apply', {}, { writes: true }),
  'proposals-resolve': command('apply', { ids: z.array(z.number().int()).min(1).optional(), signature: text.optional(), source: sourceId.optional(), all_matching: bool }, { writes: true, target: 'source' }),
  unlock: command('maintenance'),
  fail: command('maintenance', { ...src, stage: z.enum(['registered', 'profiled', 'converted', 'windowed', 'workorder_ready', 'ingest_waiting', 'ingesting', 'ingested', 'lint']), error: text }, { writes: true, target: 'source' }),
};
// Every CLI command is explicitly classified. New CLI commands are rejected until reviewed here.
const categories: Record<Spec['kind'], string[]> = {
  read: ['status', 'next', 'ingest-stats', 'arbitration-status', 'review-coverage', 'graph-lint', 'vault-lint', 'check-session', 'study-read', 'reader-info'],
  task: ['init-vault', 'rebuild-registry', 'rebuild-graph', 'rebuild-quiz', 'rebuild-propositions', 'rebuild-source-images', 'export-anki', 'export-site'],
  apply: ['relocate-source', 'adopt-vault', 'revise-adopted', 'reuse-source', 'reseal-source', 'sync-overview-sources', 'staging-clean', 'reset-source', 'retract-source', 'proposals-resolve'],
  maintenance: ['content-review', 'study-write', 'unlock', 'add-source', 'profile', 'source-convert', 'source-audit', 'arbitration-apply', 'arbitration-resolve', 'windows', 'preflight-eval', 'fail', 'apply-obsidian-style', 'workorder', 'reopen', 'sync-assets', 'show-window', 'ingest-start', 'ingest-done', 'window-start', 'window-done', 'window-fail', 'resolve-concept', 'check-write', 'snapshot-page', 'lint', 'promotion-candidates', 'promote-concept', 'skill-mine', 'skill-gate', 'skill-stage', 'skill-adopt'],
};
export function loadCommands(catalogue: { name: string; description: string; fields: { name: string; flag: string; type: string; repeat: boolean; required: boolean; help: string; choices?: string[] }[] }[]) {
  for (const entry of catalogue) {
    const kind = (Object.keys(categories) as Spec['kind'][]).find(kind => categories[kind].includes(entry.name));
    if (!kind) throw new Error('Unclassified CLI command: ' + entry.name);
    if (entry.fields.some(f => f.name === 'apply') !== (kind === 'apply')) throw new Error('CLI confirmation changed: ' + entry.name);
    const params: Spec['params'] = {}; const flags: Record<string, string> = {};
    for (const field of entry.fields) {
      if (field.name === 'apply') continue;
      const key = entry.name === 'proposals-resolve' && field.name === 'id' ? 'ids' : field.name;
      let schema: z.ZodType = field.type === 'boolean' ? z.boolean() : field.choices?.length ? z.enum(field.choices as [string, ...string[]]) : field.type === 'integer' ? z.number().int() : ['source', 'ref_source'].includes(key) ? sourceId : key === 'domain' ? domainId : text;
      if (field.repeat) schema = z.array(schema).min(1);
      if (!field.required) schema = schema.optional();
      params[key] = schema.describe(field.help || field.flag); flags[key] = field.flag;
    }
    commands[entry.name] = { kind, params, flags, description: entry.description, writes: kind !== 'read', target: entry.fields.some(f => f.name === 'source') ? 'source' : entry.fields.some(f => f.name === 'candidate') ? 'candidate' : entry.fields.some(f => f.name === 'id') ? 'id' : undefined };
  }
}
export function buildArgs(name: string, params: unknown = {}, apply = false) {
  const spec = commands[name];
  if (!spec || apply && spec.kind !== 'apply') throw new Error('Command not allowed');
  const values = z.object(spec.params).strict().parse(params);
  const args = [name];
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === false) continue;
    const flag = spec.flags?.[key] || '--' + (key === 'ids' ? 'id' : key.replaceAll('_', '-'));
    if (value === true) args.push(flag);
    else if (Array.isArray(value)) for (const item of value) args.push(flag, String(item));
    else args.push(flag, String(value));
  }
  args.push(...spec.fixed || []);
  if (apply) args.push('--apply');
  return args;
}
export function target(name: string, params: Record<string, unknown>) {
  return String(params[commands[name].target || ''] || name);
}
