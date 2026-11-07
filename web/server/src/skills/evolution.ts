import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pipeline, runProcess } from '../engine/process.ts';
import { inside } from '../ingest/guard.ts';
import { readJson, saveJson, type AppConfig } from '../config.ts';

const candidateDir = (c: AppConfig, id: string) => {
  if (!/^[a-f0-9-]{36}$/.test(id)) throw new Error('Invalid candidate ID');
  return path.join(c.root, 'pipeline-workspace/skill-evolution/candidates', id);
};
const git = (c: AppConfig, args: string[], signal?: AbortSignal) => runProcess('git', args, { cwd: c.repo, signal, stdoutOnly: true });
export async function prepareEvolution(c: AppConfig, id: string, skill: string, signal: AbortSignal, log: (s: string) => void) {
  if (!/^[a-z][a-z-]+$/.test(skill) || !fs.existsSync(path.join(c.repo, '.agents/skills', skill, 'SKILL.md'))) throw new Error('Choose an existing project skill');
  // The original workflow assumes a clean base. Never hide or commit unrelated user changes.
  if ((await git(c, ['status', '--porcelain'], signal)).trim()) throw new Error('Skill evolution needs a clean Git checkout. Commit or otherwise handle existing project changes yourself before starting; this action does not commit them.');
  await pipeline(c, ['skill-mine'], signal, log);
  const base = (await git(c, ['rev-parse', 'HEAD'], signal)).trim();
  const worktree = path.join(c.repo, 'tmp', 'web-skill-' + id);
  if (!inside(path.join(c.repo, 'tmp'), worktree)) throw new Error('Invalid candidate workspace');
  await git(c, ['worktree', 'add', '-b', 'codex/web-skill-' + id, worktree, base], signal);
  for (const rel of ['pipeline-workspace/skill-evolution/backlog.yaml', 'wiki/Review-Queue']) {
    const from = path.join(c.root, rel), to = path.join(worktree, rel);
    if (fs.existsSync(from)) { fs.mkdirSync(path.dirname(to), { recursive: true }); fs.cpSync(from, to, { recursive: true, dereference: false }); }
  }
  saveJson(path.join(candidateDir(c, id), 'web-candidate.json'), { id, skill, base, worktree, state: 'editing' });
  return { ...c, root: worktree, repo: worktree };
}
export function collectEvolution(c: AppConfig, id: string) {
  const dir = candidateDir(c, id), metadata = readJson<any>(path.join(dir, 'web-candidate.json'), null);
  if (!metadata) throw new Error('Unknown candidate');
  if (metadata.state === 'adopted') return metadata;
  const diff = path.join(metadata.worktree, 'pipeline-workspace/skill-evolution/candidates', id, 'proposal.diff');
  if (fs.existsSync(diff)) { fs.copyFileSync(diff, path.join(dir, 'proposal.diff')); metadata.state = 'staged'; saveJson(path.join(dir, 'web-candidate.json'), metadata); }
  return metadata;
}
export async function evolutionPreview(c: AppConfig, id: string) {
  const dir = candidateDir(c, id), metadata = collectEvolution(c, id);
  if (metadata.state !== 'staged') throw new Error('Candidate has not been staged');
  if (!inside(path.join(c.repo, 'tmp'), metadata.worktree)) throw new Error('Invalid candidate workspace');
  const expected = [`.agents/skills/${metadata.skill}/SKILL.md`, `.claude/skills/${metadata.skill}/SKILL.md`];
  const changed = (await git({ ...c, repo: metadata.worktree }, ['diff', '--name-only', 'HEAD'])).trim().split(/\r?\n/).filter(Boolean).sort();
  if (JSON.stringify(changed) !== JSON.stringify(expected.sort())) throw new Error('Candidate must change only the selected SKILL.md in both trees');
  if (!fs.readFileSync(path.join(metadata.worktree, expected[0])).equals(fs.readFileSync(path.join(metadata.worktree, expected[1])))) throw new Error('Skill trees differ');
  const diff = (await git({ ...c, repo: metadata.worktree }, ['diff', 'HEAD', '--', ...expected])).replaceAll('\r\n', '\n');
  if (diff !== fs.readFileSync(path.join(dir, 'proposal.diff'), 'utf8').replaceAll('\r\n', '\n')) throw new Error('Candidate changed after staging; run gate and stage again');
  return { ...metadata, diff, revision: createHash('sha256').update(diff).digest('hex') };
}
export async function adoptEvolution(c: AppConfig, id: string, revision: string, signal: AbortSignal, log: (s: string) => void) {
  const value = await evolutionPreview(c, id);
  if (value.revision !== revision) throw new Error('Candidate changed; preview again');
  if ((await git(c, ['rev-parse', 'HEAD'], signal)).trim() !== value.base || (await git(c, ['status', '--porcelain'], signal)).trim()) throw new Error('The main checkout changed since this candidate started; reconcile it before adoption');
  const work = { ...c, repo: value.worktree, root: value.worktree };
  await pipeline(work, ['skill-gate', '--candidate', id], signal, log);
  // Commit the reviewed candidate only in its isolated branch; transfer with a fast-forward.
  await pipeline(work, ['skill-adopt', '--candidate', id], signal, log);
  await git(c, ['merge', '--ff-only', 'codex/web-skill-' + id], signal);
  saveJson(path.join(candidateDir(c, id), 'web-candidate.json'), { ...value, diff: undefined, state: 'adopted' });
  log('Skill candidate adopted. Restart the web service to load updated resources.\n');
}
