import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { expect, test, vi } from 'vitest';
import { createApp } from '../src/app.ts';
import { repo } from '../src/config.ts';
import { usageTotals } from '../../client/src/usage.tsx';
import { AuditStatus } from '../../client/src/reports.tsx';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

vi.mock('../../client/src/api.ts', () => ({ api: vi.fn(), navigate: vi.fn() }));

test('personal study API preserves wiki bytes, protects writes and rejects stale ratings', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-personal-api-'));
  const page = path.join(root, 'wiki/domains/demo/concepts/card.md'); fs.mkdirSync(path.dirname(page), { recursive: true });
  const original = '---\ntype: concept\nstatus: published\ndomain: demo\ncanonical_id: demo.card\nsource_refs: [demo]\n---\n正文 ==高亮==\n\n> [!question] 为什么？\n> > [!success]- 答案\n> > 因为 $a<b$。\n';
  fs.writeFileSync(page, original);
  const { app } = await createApp({ root, repo, config: path.join(root, 'private'), python: process.env.STUDY_KB_PYTHON || 'python', port: 8765, token: 'fixture-token' });
  const headers = { host: '127.0.0.1:8765', 'x-console-token': 'fixture-token' };
  try {
    const denied = await app.inject({ method: 'POST', url: '/api/study/preferences', headers: { host: headers.host }, payload: { sources: ['demo'] } });
    expect(denied.statusCode).toBe(403);
    expect(fs.existsSync(path.join(root, 'pipeline-workspace/state/study-kb.sqlite'))).toBe(false);
    const cards = (await app.inject({ url: '/api/study/cards', headers })).json().cards;
    expect(cards).toHaveLength(1);
    const request = { id: cards[0].id, revision: cards[0].revision, version: 0, rating: 3, mode: 'practice', schedule: true, event_id: randomUUID() };
    const saved = await app.inject({ method: 'POST', url: '/api/study/review', headers, payload: request });
    expect(saved.statusCode, saved.body).toBe(200); expect(saved.json().due).toBeTruthy();
    const retry = await app.inject({ method: 'POST', url: '/api/study/review', headers, payload: request }); expect(retry.json().duplicate).toBe(true);
    const stale = await app.inject({ method: 'POST', url: '/api/study/review', headers, payload: { ...request, event_id: randomUUID() } });
    expect(stale.statusCode).toBe(400); expect(stale.json().error).toContain('another tab'); expect(stale.body).not.toContain('Traceback');
    const markdown = await app.inject({ method: 'POST', url: '/api/reader/render', headers, payload: { text: '# 标题\n\n$a<b$ <script>alert(1)</script>' } });
    expect(markdown.json().html).toContain('<h1>标题</h1>'); expect(markdown.json().html).not.toContain('<script>');
    const backup = await app.inject({ url: '/api/study/export', headers }); expect(backup.headers['content-disposition']).toContain('study-records.json');
    expect(fs.readFileSync(page, 'utf8')).toBe(original);
  } finally { await app.close(); }
}, 30000);

test('usage distinguishes total share from input cache hit rate and marks incomplete jobs', () => {
  const value = usageTotals([{ status: 'succeeded', progress: { tokens: { input: 1227226, output: 36294, cacheRead: 3034880, cacheWrite: 0, total: 4298400 } } }, { command: 'ingest', status: 'failed' }]);
  expect(value.total).toBe(4298400); expect(value.hitRate).toBeCloseTo(0.7121, 3); expect(value.incomplete).toBe(1);
});

test('source review display keeps inherited history separate and explains missing revisions', () => {
  const page = { path: 'domains/demo/concepts/card.md', date: '2026-07-21', sources: ['old-book'], scope: 'whole-source', outcome: 'no-findings', version: 'unconfirmed', report: 'pipeline-workspace/reports/kb-qa/old-book.md', available: true };
  const value = { total: 117, historical: 0, current: 0, changed: 0, unconfirmed: 0, whole: 0, sampled: 0, pages: [], related_pages: [page], proposals_open: 8 };
  const html = renderToStaticMarkup(createElement(AuditStatus, { value }));
  expect(html).toContain('本资料暂无内容审查记录');
  expect(html).toContain('0 / 117');
  expect(html).toContain('其他范围的页面审查历史');
  expect(html).toContain('不计入本资料的内容审查覆盖');
  expect(html).toContain('old-book');
  expect(html).not.toContain('纳入整源检查的页面');
  const own = renderToStaticMarkup(createElement(AuditStatus, { value: { ...value, historical: 1, unconfirmed: 1, whole: 1, pages: [page], related_pages: [] } }));
  expect(own).toContain('历史审查未记录版本');
  expect(own).toContain('不表示发现了内容错误');
});
