import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, navigate } from './api.ts';
import { Language, useT, type Key, type Lang } from './i18n.tsx';
import { Reader } from './reader.tsx';
import { Settings } from './settings.tsx';
import { Workflows, Maintenance, Decision } from './workflows.tsx';
import { Help } from './help.tsx';
import { LearningHome, Study } from './study.tsx';
import { ReportCenter, LearningPreferences } from './reports.tsx';
import { Dialog } from './dialog.tsx';
import { Usage } from './usage.tsx';
import { SourceList, SourceDetail, errorText, jobLabel, label, type Source, type Job } from './library.tsx';
import './style.css';

const routes: [string, Key, string][] = [['dashboard', 'dashboard', '◫'], ['reader', 'reader', '▧'], ['study', 'study', '◷'], ['library', 'library', '▤'], ['reports', 'reportCenter', '☷'], ['workflows', 'workflows', '◇'], ['settings', 'settings', '⚙'], ['tools', 'tools', '⌘'], ['doctor', 'doctor', '◉'], ['help', 'help', '?']];
function App() {
  const t = useT();
  const [route, setRoute] = useState(location.hash.slice(1) || 'dashboard');
  const [status, setStatus] = useState<any>({ sources: [] });
  const [jobs, setJobs] = useState<Job[]>([]); const [error, setError] = useState('');
  const [jobId, setJobId] = useState(''); const [logs, setLogs] = useState('');
  const [settings, setSettings] = useState<any>({ providers: [] });
  const [source, setSource] = useState('');
  const [consent, setConsent] = useState<{ source: string; mode: string }>();
  const [actionError, setActionError] = useState(''); const [submitting, setSubmitting] = useState(false);
  const [reworkInstructions, setReworkInstructions] = useState(''), [reworkConfirm, setReworkConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState('');
  useEffect(() => { setReworkInstructions(''); }, [source]);
  const run = async (fn: () => Promise<any>) => { setError(''); setBusy(true); try { return await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const refresh = async () => { const [st, js, ss] = await Promise.all([api('/status').catch(e => { setError(String(e)); return { sources: [] }; }), api('/jobs'), api('/settings')]); setStatus(st); setJobs(js); setSettings(ss); };
  useEffect(() => {
    const change = () => setRoute(location.hash.slice(1) || 'dashboard'); window.addEventListener('hashchange', change);
    void api('/session', {}).then(refresh).catch(e => setError(String(e)));
    const timer = setInterval(() => { void refresh().catch(() => {}); }, 3000);
    return () => { clearInterval(timer); window.removeEventListener('hashchange', change); };
  }, []);
  useEffect(() => {
    if (!jobId) return; setLogs('');
    const stream = new EventSource(`/api/jobs/${jobId}/events`);
    stream.onmessage = event => setLogs(old => (old + JSON.parse(event.data)).slice(-100000));
    stream.addEventListener('done', () => { stream.close(); void refresh();  });
    return () => stream.close();
  }, [jobId]);
  const job = async (url: string, data: any) => { const j = await api<Job>(url, data); setJobId(j.id); await refresh(); return j; };
  const showSource = (id: string) => { setSource(id); };
  const openAction = (mode: string) => { setActionError(''); setReworkConfirm(''); setConsent({ source, mode }); };
  const cli = (command: string, params: any = {}) => run(() => job('/jobs', { command, params }));
  const activeRoute = route.startsWith('/') ? 'reader' : route.split('/')[0].split('?')[0];
  const sources: Source[] = status.sources || [];
  const sourceJob = jobs.find(j => j.source === source && ['ingest', 'prepare'].includes(j.command));
  const activity = status.active_job || jobs[0];
  const submitAction = async () => {
    if (!consent || submitting) return; setActionError(''); setSubmitting(true);
    try {
      if (consent.mode === 'prepare') { const detail = await api('/library/' + encodeURIComponent(consent.source)); await job('/library/prepare', detail.prepare); }
      else { if (!settings.selected) throw new Error(t('selectModel')); await job('/ingest', { ...settings.selected, ...consent, ...(consent.mode === 'rework' ? { instructions: reworkInstructions, confirm: reworkConfirm } : {}) }); }
      setConsent(undefined);
      setReworkInstructions('');
    } catch (e) { setActionError(String(e)); } finally { setSubmitting(false); }
  };
  return <div className={`shell ${activeRoute === 'reader' ? 'reading' : ''}`}>
    <aside className="rail"><a className="brand" href="#dashboard"><span className="brand-mark">k</span><span>Study KB<small>{t('personalKnowledge')}</small></span></a>
      <nav>{routes.map(([r, label, icon]) => <a key={r} href={'#' + r} className={activeRoute === r ? 'selected' : ''}><span>{icon}</span>{t(label)}</a>)}</nav>
      <div className="rail-bottom"><div className="model-label">{t('currentModel')}</div><strong>{settings.selected?.model || t('noModel')}</strong><a href="#settings">{t('settings')} ↗</a></div>
    </aside>
    <div className="workspace"><header className="topbar"><span className="crumb">Study KB <span>/</span> {t(routes.find(r => r[0] === activeRoute)?.[1] || 'dashboard')}</span>{activity && <button className="task-dock" onClick={() => setJobId(activity.id)}><span className={'dot ' + activity.status}/><span><strong>{activity.source || label(activity.command, t)}</strong><small>{jobLabel(activity, t)} · {t('viewLogs')}</small></span><span>↗</span></button>}<div className="top-actions"><LanguageButtons/><button className="icon" title={t('theme')} onClick={() => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = next; localStorage.setItem('study-kb-theme', next); }}>◐</button><a className="icon" href="#help">?</a></div></header>
    {error && <div className="notice error global-error" role="alert"><span>{errorText(error, t)}</span><button onClick={() => setError('')}>×</button></div>}

    <main>
    {status.restart_required && <div className="notice error" role="alert">{t('restartRequired')}</div>}
    {!settings.selected && activeRoute === 'dashboard' && <div className="notice"><span>{t('firstRun')}</span><a href="#doctor">{t('doctor')} ↗</a><a href="#settings">{t('settings')} ↗</a></div>}
    {activeRoute === 'dashboard' && <><LearningHome/><div className="button-row"><a className="button-link" href="#library/add">＋ {t('add')}</a><a href="#workflows/kb-query">{t('workflows')} →</a></div>
      <div className="stats"><Stat value={sources.length} label={t('sources')}/><Stat value={sources.filter(s => s.current_status === 'published').length} label={t('published')}/><Stat value={new Set(sources.map(s => s.domain)).size} label={t('domain')}/></div>
      <Section title={t('recent')} action={<a href="#library">{t('view')} →</a>}/><SourceList sources={sources} jobs={jobs} onOpen={id => { navigate('library'); showSource(id); }}/>
      <Section title={t('jobs')}/><div className="job-list">{jobs.slice(0, 5).map(j => <button key={j.id} onClick={() => setJobId(j.id)}><span className={'dot ' + j.status}/><strong>{j.source || j.command}</strong><span>{jobLabel(j, t)}</span><time>{new Date(j.started_at).toLocaleString()}</time></button>)}{!jobs.length && <p className="muted">{t('noJobs')}</p>}</div></>}
    {activeRoute === 'library' && <><Section title={t('library')} action={<button className="primary" onClick={() => navigate('library/add')}>＋ {t('add')}</button>}/>
      {route === 'library/add' && <form className="panel upload-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void run(async () => { const data = new FormData(); data.set('file', form.get('file')!); const uploaded = await api('/library/upload?domain=' + encodeURIComponent(String(form.get('domain'))), data); const value = { ...uploaded, source: String(form.get('source') || uploaded.source), purpose: String(form.get('purpose') || '') }; setSource(value.source); await job('/library/prepare', value); navigate('library'); }); }}>
        <label className="drop-zone"><span className="upload-icon">↑</span><strong>{fileName || t('chooseFile')}</strong><span>{t('uploadHint')}</span><input name="file" type="file" onChange={e => setFileName(e.target.files?.[0]?.name || '')} required accept=".pdf,.docx,.pptx,.md"/></label><div className="form-row"><label>{t('domain')}<input name="domain" required pattern="[a-z0-9]+(-[a-z0-9]+)*" list="domains" placeholder="research-method"/><datalist id="domains">{[...new Set(sources.map(s => s.domain))].map(d => <option key={d}>{d}</option>)}</datalist></label><label>{t('source')}<input name="source" placeholder="my-book"/></label></div><label>{t('purpose')}<textarea name="purpose" rows={2}/><small>{t('purposeHint')}</small></label><p className="muted">{t('preprocessing')}</p><button className="primary" disabled={busy || !!status.active_job}>{t('upload')}</button></form>}
      <SourceList sources={sources} jobs={jobs} selected={source} onOpen={id => setSource(old => old === id ? '' : id)}>
        {sources.find(s => s.source_id === source) && <SourceDetail key={source} source={sources.find(s => s.source_id === source)!} job={sourceJob} jobs={jobs.filter(j => j.source === source)} active={!!status.active_job} selectedModel={settings.selected} onAction={openAction} onLog={setJobId} runCommand={(command, params, confirm) => job('/jobs', { command, params, confirm })}/>}
      </SourceList>
      <details className="panel vault-tools"><summary>{t('openVault')}</summary><p>{t('obsidianHelp')}</p><code>{status.workspace}/wiki</code><button onClick={() => navigator.clipboard.writeText(status.workspace + '/wiki')}>{t('copy')}</button><a href="#/overview.md">{t('reader')} ↗</a></details>
      <a className="text-link" href="#tools">{t('maintenance')} →</a></>}
    {activeRoute === 'workflows' && <Workflows route={route} sources={sources} settings={settings} jobs={jobs} active={!!status.active_job} onJob={job} onLog={setJobId}/>}
    {activeRoute === 'tools' && <Maintenance job={job} active={!!status.active_job}/>}
    {activeRoute === 'settings' && <><Settings settings={settings} refresh={refresh} run={run} job={job} active={!!status.active_job}/><LearningPreferences/></>}
    {activeRoute === 'doctor' && <Doctor run={run} init={() => cli('init-vault')}/>}
    {activeRoute === 'reader' && <Reader route={route} run={run} cli={cli} active={!!status.active_job}/>}
    {activeRoute === 'study' && <Study route={route}/>}
    {activeRoute === 'reports' && <ReportCenter route={route} sources={sources}/>}
    {activeRoute === 'help' && <Help route={route}/>}
    </main></div>
    {jobId && <Dialog drawer label={t('logs')} onClose={() => setJobId('')}><Section title={t('logs')} action={<button onClick={() => setJobId('')}>{t('collapse')}</button>}/><p className="muted">{t('collapseHint')}</p><div className="button-row"><span className="badge">{jobs.find(j => j.id === jobId) ? jobLabel(jobs.find(j => j.id === jobId)!, t) : t('loading')}</span>{status.active_job?.id === jobId && <button className="stop-task" onClick={() => { setActionError(''); setConsent({ source: status.active_job.source || '', mode: 'cancel' }); }}>{t('cancel')}</button>}</div><strong>{jobs.find(j => j.id === jobId)?.source}</strong><Usage job={jobs.find(j => j.id === jobId)}/>{jobs.find(j => j.id === jobId)?.progress?.question && <Decision job={jobs.find(j => j.id === jobId)!} refresh={refresh}/>}<pre aria-live="polite">{logs || t('waitingOutput')}</pre></Dialog>}
    {consent && <Dialog label={t(consent.mode === 'prepare' ? 'continuePrepare' : consent.mode as Key)} onClose={() => setConsent(undefined)} busy={submitting}><Section title={t(consent.mode === 'prepare' ? 'continuePrepare' : consent.mode as Key)} action={<button disabled={submitting} aria-label={t('close')} onClick={() => setConsent(undefined)}>×</button>}/><small>{consent.source}</small><p>{t((consent.mode + 'Description') as Key)}</p>{!['prepare', 'cancel'].includes(consent.mode) && <><p>{t('consent')}</p><strong>{settings.selected?.provider} / {settings.selected?.model || t('noModel')}</strong>{!settings.verified && !actionError && <div className="notice">{t('visionRequired')} <a href="#settings" onClick={() => setConsent(undefined)}>{t('settings')} →</a></div>}</>}
      {consent.mode === 'rework' && <><label>{t('workflowRequest')}<textarea value={reworkInstructions} onChange={e => setReworkInstructions(e.target.value)} rows={4}/></label><label>{t('confirmation')}<code>{consent.source}</code><input value={reworkConfirm} onChange={e => setReworkConfirm(e.target.value)}/></label></>}
      {actionError && <div className="notice error" role="alert">{errorText(actionError, t)}{(/image test/i.test(actionError) || !settings.selected) && <a href="#settings" onClick={() => setConsent(undefined)}>{t('settings')} →</a>}</div>}
      <div className="button-row"><button className="primary" disabled={submitting || (consent.mode !== 'cancel' && !!status.active_job)} onClick={consent.mode === 'cancel' ? () => { setSubmitting(true); void api('/jobs/cancel', {}).then(() => setConsent(undefined)).catch(e => setActionError(String(e))).finally(() => setSubmitting(false)); } : submitAction}>{submitting ? t('submitting') : consent.mode === 'cancel' ? t('cancel') : consent.mode === 'prepare' ? t('continuePrepare') : t('send')}</button><button disabled={submitting} onClick={() => setConsent(undefined)}>{t('back')}</button></div></Dialog>}

  </div>;
}
export function Section({ title, action }: { title: string; action?: React.ReactNode }) { return <div className="section-heading"><h2>{title}</h2>{action}</div>; }
function Stat({ value, label }: { value: React.ReactNode; label: string }) { return <div className="stat"><strong>{value}</strong><span>{label}</span></div>; }
function Doctor({ run, init }: { run: (fn: () => Promise<any>) => void; init: () => void }) { const t = useT(); const [checks, setChecks] = useState<any[]>([]); const [loading, setLoading] = useState(false); const load = () => run(async () => { setLoading(true); try { setChecks(await api('/doctor')); } finally { setLoading(false); } }); useEffect(() => { load(); }, []); return <><Section title={t('doctor')} action={<button onClick={load}>{t('refresh')}</button>}/><p className="muted">{t('doctorIntro')}</p>{loading && <p>{t('loading')}</p>}<div className="doctor-grid">{checks.map(c => <section className="panel" key={c.id}><div className="section-heading"><h3>{c.id}</h3><span className={'badge ' + (c.ok ? 'good' : 'warn')}>{c.ok ? t('pass') : ['gpu', 'obsidian'].includes(c.id) ? t('optional') : t('needsAttention')}</span></div><pre>{c.detail === 'Optional' ? t('optionalHint') : c.detail}</pre>{!c.ok && c.fix && <><small>{t(['gpu', 'obsidian'].includes(c.id) ? 'optionalInstall' : 'fix')}</small><pre>{c.fix}</pre>{c.id === 'vault' && <button onClick={init}>{t('initVault')}</button>}</>}</section>)}</div></>; }
const languageEvent = new EventTarget();
function LanguageButtons() { return <button className="icon" onClick={() => languageEvent.dispatchEvent(new Event('toggle'))}>中 / EN</button>; }
function Root() { const [lang, setLang] = useState<Lang>(localStorage.getItem('study-kb-lang') === 'en' ? 'en' : 'zh'); useEffect(() => { const toggle = () => setLang(old => old === 'zh' ? 'en' : 'zh'); languageEvent.addEventListener('toggle', toggle); return () => languageEvent.removeEventListener('toggle', toggle); }, []); useEffect(() => { localStorage.setItem('study-kb-lang', lang); document.documentElement.lang = lang; }, [lang]); return <Language.Provider value={lang}><App/></Language.Provider>; }
document.documentElement.dataset.theme = localStorage.getItem('study-kb-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
createRoot(document.getElementById('root')!).render(<Root/>);
