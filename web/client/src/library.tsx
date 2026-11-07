import { useEffect, useRef, useState } from 'react';
import { Html } from './content.tsx';
import { Usage } from './usage.tsx';
import { AuditStatus } from './reports.tsx';
import { api } from './api.ts';
import { messages, useT, type Key } from './i18n.tsx';

export type Source = { source_id: string; domain: string; current_stage: string; current_status: string };
export type Job = { id: string; command: string; source?: string; mode?: string; status: string; message?: string; progress?: any; error_kind?: string; started_at: string };
export const label = (value: string, t: ReturnType<typeof useT>) => value in messages ? t(value as Key) : value;
export function errorText(error: unknown, t: ReturnType<typeof useT>) {
  const text = String(error).replace(/^Error: /, '');
  if (/Pass the image test/i.test(text)) return t('visionRequired');
  if (/Open the launch link/i.test(text)) return t('tokenHelp');
  if (/Another job|A job is running|Stop the running job/i.test(text)) return t('jobRunning');
  if (/vault is being edited/i.test(text)) return t('vaultBusy');
  if (/Ingest stopped before publication/i.test(text)) return t('stoppedBeforePublish');
  return text;
}
export function jobLabel(job: Job, t: ReturnType<typeof useT>) {
  return job.progress?.question || job.status === 'succeeded' && job.progress?.waiting ? t(job.progress.waiting === 'human' ? 'awaitHuman' : 'awaitArbitration') : label(job.status, t);
}
export function tokenLabel(job?: Job) { const total = job?.progress?.tokens?.total; return typeof total === 'number' && total > 0 ? total.toLocaleString() : '—'; }
function sourceLabel(source: Source, job: Job | undefined, t: ReturnType<typeof useT>) {
  if (job?.status === 'running') return jobLabel(job, t);
  if (source.current_status === 'published') return t('published');
  if (job?.progress?.waiting) return jobLabel(job, t);
  if (job && ['failed', 'paused', 'interrupted', 'cancelled'].includes(job.status)) return jobLabel(job, t);
  return label(source.current_stage, t);
}
export function SourceList({ sources, jobs, selected, onOpen, children }: { sources: Source[]; jobs: Job[]; selected?: string; onOpen: (id: string) => void; children?: React.ReactNode }) {
  const t = useT();
  return sources.length ? <div className="source-list">{sources.map(source => <article key={source.source_id} className={'source-item' + (selected === source.source_id ? ' expanded' : '')}>
    <button className="source-card" aria-expanded={selected === source.source_id} onClick={() => onOpen(source.source_id)}>
      <span className="book-icon">▤</span><span><strong>{source.source_id}</strong><small>{source.domain}</small></span>
      <span className={'badge' + (source.current_status === 'published' ? ' good' : '')}>{sourceLabel(source, jobs.find(j => j.source === source.source_id && ['ingest', 'prepare'].includes(j.command)), t)}</span><span aria-hidden="true">{selected === source.source_id ? '−' : '+'}</span>
    </button>{selected === source.source_id && children}
  </article>)}</div> : <div className="empty"><span>▤</span><p>{t('empty')}</p></div>;
}
export function SourceDetail({ source, job, jobs, active, selectedModel, onAction, onLog, runCommand }: { source: Source; job?: Job; jobs: Job[]; active: boolean; selectedModel: any; onAction: (mode: string) => void; onLog: (id: string) => void; runCommand: (command: string, params: any, confirm?: string) => Promise<any> }) {
  const t = useT(); const [detail, setDetail] = useState<any>(); const [error, setError] = useState('');
  const [reports, setReports] = useState<any[]>([]); const [report, setReport] = useState(''); const [reportPath, setReportPath] = useState('');
  const [reportLoading, setReportLoading] = useState(false); const [busy, setBusy] = useState(false); const request = useRef(0);
  const id = source.source_id;
  useEffect(() => {
    let mounted = true;
    const load = async () => { try { const value = await api('/library/' + encodeURIComponent(id)); if (mounted) { setDetail(value); setError(''); } } catch (e) { if (mounted) setError(String(e)); } };
    void load(); const timer = setInterval(load, 5000);
    void api('/reader/info/reports?source=' + encodeURIComponent(id)).then(items => { if (mounted) setReports(items); }).catch(e => { if (mounted) setError(String(e)); });
    return () => { mounted = false; request.current++; clearInterval(timer); };
  }, [id, job?.status]);
  const openReport = async (file: string) => {
    const current = ++request.current; setReport(''); setReportPath(file); setReportLoading(true);
    try { const value = await api('/report?path=' + encodeURIComponent(file)); if (current === request.current) setReport(value.html); }
    catch (e) { if (current === request.current) setError(String(e)); }
    finally { if (current === request.current) setReportLoading(false); }
  };
  const resolve = async (params: object, confirm: string) => { setBusy(true); setError(''); try { await runCommand('arbitration-resolve', { source: id, ...params }, confirm); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const row = detail?.stats?.source || source;
  const published = row.current_status === 'published';
  const preparing = ['registered', 'profiled', 'converted', 'windowed'].includes(row.current_stage);
  const repairing = row.current_stage === 'lint' && row.current_status === 'failed';
  const resume = ['ingesting', 'ingested', 'lint'].includes(row.current_stage);
  const mode = repairing ? 'repair' : resume ? 'resume' : 'start';
  const human = detail?.arbitration?.needs_human || [];
  const waiting = human.length ? 'awaitHuman' : detail?.arbitration?.pending ? 'awaitArbitration' : null;
  const phase = published ? 'published' : job?.status === 'running' ? job.progress?.phase : preparing ? 'prepare' : job?.progress?.phase || 'initialize';
  const step = ['published', 'finish'].includes(phase) ? 3 : phase === 'synthesis' ? 2 : preparing || phase === 'arbitration' ? 0 : 1;
  const windows = detail?.stats?.windows;
  const finished = windows?.finished ?? 0; const planned = windows?.planned ?? 0;
  return <section className="source-detail" aria-label={id}>
    <div className="source-status"><div><span className="eyebrow">{t('sourceProgress')}</span><h3>{published ? t('published') : waiting ? t(waiting) : active && job?.status === 'running' ? label(phase || 'running', t) : sourceLabel(row, job, t)}</h3></div>{job && <button onClick={() => onLog(job.id)}>{t('viewLogs')} ↗</button>}</div>
    <ol className="stage-progress">{(['prepareStage', 'windowStage', 'synthesisStage', 'finishStage'] as Key[]).map((name, i) => <li key={name} aria-current={i === step ? 'step' : undefined} className={i === step ? 'selected' : i < step ? 'passed' : ''}><span>{i < step ? '✓' : i + 1}</span>{t(name)}</li>)}</ol>
    {job?.message && <div className="notice error" role="alert"><p>{errorText(job.message, t)}</p>{job.error_kind === 'quota' && <p>{t('quotaHint')}</p>}</div>}
    {error && <div className="notice error" role="alert">{errorText(error, t)}</div>}
    <div className="source-next"><div><strong>{published ? t('readReady') : active ? t('jobRunning') : human.length ? t('humanHint') : t((mode + 'Hint') as Key)}</strong><p>{published ? t('contentReviewHint') : preparing ? t('arbitrationHint') : t('resumeHintDetail')}</p></div>
      {published ? <a className="primary" href={'#/sources/' + encodeURIComponent(id) + '.md'}>{t('reader')} →</a> : <button className="primary" disabled={active || !detail || human.length > 0} onClick={() => onAction(mode)}>{t(mode as Key)} →</button>}
    </div>
    {published && <div className="button-row"><a href={'#workflows/kb-qa/' + encodeURIComponent(id)}>{t('contentQA')} ↗</a><a href={'#workflows/kb-postmortem/' + encodeURIComponent(id)}>{t('postmortem')} ↗</a><button disabled={active} onClick={() => onAction('rework')}>{t('rework')}</button></div>}
    {!published && <p className="muted">{t('currentModel')}：{selectedModel?.model || t('noModel')}{!selectedModel && <> · <a href="#settings">{t('selectModel')}</a></>}</p>}
    {preparing && !active && <button className="text-link" onClick={() => onAction('prepare')}>{t('continuePrepare')} · {t('noModelUse')}</button>}
    <div className="stats compact"><Stat value={detail?.stats?.evidence?.pages ?? detail?.profile?.page_count ?? '—'} label={t('totalPages')}/><Stat value={detail?.stats?.evidence?.needs_vision ?? '—'} label={t('visionPages')}/><Stat value={`${finished} / ${planned || '—'}`} label={t('completedWindows')}/><Stat value={detail?.stats?.page_inventory?.total ?? '—'} label={t('written')}/></div>
    {planned > 0 && <><progress className="window-progress" max={planned} value={finished}/><p className="muted">{job?.status === 'running' && job.progress?.window ? `${t('currentWindow')} ${job.progress.window} · ` : ''}{t('windowCountHint')}</p></>}
    <Usage job={job} jobs={jobs}/><AuditStatus value={detail?.review}/><a className="text-link" href={'#reports?source=' + encodeURIComponent(id)}>{t('reportCenter')} →</a>
    {detail?.stats?.learning_goal && <details><summary>{t('purpose')}</summary><p>{detail.stats.learning_goal}</p></details>}
    {human.map((page: any) => <div className="arbitration" key={page.page}><img src={`/api/library/${encodeURIComponent(id)}/image/${page.page}`} alt={`${t('sourcePages')} ${page.page}`}/><div><strong>{t('sourcePages')} {page.page}</strong><p>{page.reasons.join(', ')}</p><form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); const decision = ((e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement).value; void resolve({ page: page.page, decision, reason: data.get('reason') }, String(data.get('confirm'))); }}><label>{t('reason')}<input name="reason" required/></label><label>{t('confirmation')}<code>{id}</code><input name="confirm" required pattern={id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/></label><div className="button-row"><button value="render" disabled={active || busy}>{t('keepImage')}</button><button value="ignore" disabled={active || busy}>{t('ignoreImage')}</button></div></form></div></div>)}
    <details className="source-reports"><summary>{t('reports')} <span className="badge">{reports.length}</span></summary>{reports.length ? reports.map(item => <button className={'report-link' + (item.path === reportPath ? ' selected' : '')} key={item.path} disabled={!item.exists} onClick={() => openReport(item.path)}>{item.title}{!item.exists && ' · ' + t('reportUnavailable')}</button>) : <p className="muted">{t('noReports')}</p>}{reportLoading && <p>{t('loading')}</p>}{report && <div className="report"><Html html={report}/></div>}</details>
  </section>;
}
function Stat({ value, label }: { value: React.ReactNode; label: string }) { return <div className="stat"><strong>{value}</strong><span>{label}</span></div>; }
