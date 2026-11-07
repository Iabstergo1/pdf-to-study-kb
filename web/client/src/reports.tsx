import { useEffect, useRef, useState } from 'react';
import { api, navigate } from './api.ts';
import { Html } from './content.tsx';
import { useT } from './i18n.tsx';

export function AuditStatus({ value }: { value: any }) {
  const t = useT();
  if (!value) return null;
  const pageRow = (page: any) => <div className="audit-page" key={page.path}>
    <a href={'#/' + encodeURI(page.path)}>{page.path.split('/').at(-1)}</a>
    <small>{page.date} · {page.sources?.join(', ') || t('reportUnassigned')} · {page.scope} · {page.outcome} · {t(page.version === 'current' ? 'auditCurrent' : page.version === 'changed' ? 'auditChanged' : 'auditUnconfirmed')}</small>
    {page.available ? <a href={'#reports?report=' + encodeURIComponent(page.report)}>{t('reportRead')} ↗</a> : <small>{t('reportUnavailable')}</small>}
  </div>;
  return <section className="audit-status"><h3>{t('contentAudit')}</h3>
    {!value.historical ? <span className="badge">{t('auditNone')} · 0 / {value.total}</span> : <>
      <div className="badge-row"><span className="badge">{t('auditHistory')}: {value.historical} / {value.total}</span>{value.current > 0 && <span className="badge good">{t('auditCurrent')}: {value.current}</span>}{value.unconfirmed > 0 && <span className="badge">{t('auditUnconfirmed')}: {value.unconfirmed}</span>}{value.changed > 0 && <span className="badge warn">{t('auditChanged')}: {value.changed}</span>}</div>
      {value.unconfirmed > 0 && <p className="muted">{t('auditUnconfirmedHint')}</p>}
      <p>{t('auditWhole')}: {value.whole} · {t('auditSample')}: {value.sampled}</p>
      {value.missing_reports > 0 && <p>{t('auditMissing')}: {value.missing_reports}</p>}{value.findings_open > 0 && <p>{t('auditOpen')}: {value.findings_open}</p>}
      <details><summary>{t('auditDetails')}</summary>{value.pages.map(pageRow)}</details>
    </>}
    {value.related_pages?.length > 0 && <details><summary>{t('auditRelated')}: {value.related_pages.length}</summary><p>{t('auditRelatedHint')}</p>{value.related_pages.map(pageRow)}</details>}
    {value.proposals_open > 0 && <p>{t('reviewOpen')}: {value.proposals_open}</p>}<small>{t('auditScopeHint')}</small>
    {value.batches?.length > 0 && <p>{t('batchAcceptance')}: {value.batches.filter((b: any) => b.accepted).length} / {value.batches.length} {t('batchCountHint')} · <a href={'#reports?source=' + encodeURIComponent(value.source)}>{t('batchDetails')} ↗</a></p>}
  </section>;
}

function ReviewBatch({ batch, reload }: { batch: any; reload: () => Promise<void> }) {
  const t = useT(), [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { setConfirmed(false); }, [batch.token]);
  return <section className="panel"><h3>{t('batchAcceptance')} · {batch.created_at.slice(0, 10)}</h3>
    <p>{t(batch.completed_at ? 'batchReviewed' : 'batchIncomplete')} · {t(batch.method === 'full-page' ? 'batchFull' : 'batchSample')} · {batch.scope_count} / {batch.source_total} {t('batchDenominator')}</p>
    <p>{t('batchAffected')}: {batch.affected.length} · {t('reviewOpen')}: {batch.open_ids.length} · {t(batch.accepted ? 'batchAccepted' : 'batchNotAccepted')}</p>
    <details><summary>{t('batchDetails')}</summary>{batch.pages.map((p: any) => <p key={p.path}><a href={'#/' + encodeURI(p.path)}>{p.path}</a> · {t(p.version === 'current' ? 'auditCurrent' : p.version === 'changed' ? 'auditChanged' : 'batchIncomplete')} · {t(p.outcome === 'no-findings' ? 'batchNoFindings' : p.outcome === 'findings' ? 'batchFindings' : 'batchInconclusive')}</p>)}{batch.runs.filter((r: any) => r.completed_at).map((r: any) => <p key={r.id}><a href={'#reports?source=' + encodeURIComponent(batch.source) + '&report=' + encodeURIComponent(r.report)}>{t('reportRead')} · {r.completed_at}</a></p>)}{batch.acceptances.map((a: any) => <p key={a.id}>{t('batchAccepted')} · {a.accepted_at} · {t(a.current ? 'auditCurrent' : 'batchAffected')}</p>)}</details>
    {batch.completed_at && batch.affected.length > 0 && <a className="text-link" href={'#workflows/kb-qa/' + encodeURIComponent(batch.source) + '?review=' + batch.id}>{t('batchRecheck')} ↗</a>}
    {!batch.accepted && <form onSubmit={e => { e.preventDefault(); setBusy(true); setError(''); void api('/content-review/accept', { run_id: batch.id, token: batch.token, confirmed }).then(reload).catch(e => setError(String(e))).finally(() => setBusy(false)); }}><label className="check-line"><input type="checkbox" checked={confirmed} disabled={!batch.can_accept} onChange={e => setConfirmed(e.target.checked)}/>{t('batchConfirm')}</label><button disabled={busy || !confirmed || !batch.can_accept}>{t('batchAccept')}</button></form>}
    {error && <p className="notice error" role="alert">{error}</p>}
  </section>;
}

export function ReportCenter({ route, sources }: { route: string; sources: any[] }) {
  const t = useT(), params = new URLSearchParams(route.split('?')[1] || ''), source = params.get('source') || '', tab = params.get('tab') || 'reports';
  const [reports, setReports] = useState<any[]>([]), [queue, setQueue] = useState<any[]>([]), [audit, setAudit] = useState<any>();
  const [selected, setSelected] = useState(''), [html, setHtml] = useState(''), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [all, setAll] = useState(false), [search, setSearch] = useState(''), [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<any[]>([]), [chosen, setChosen] = useState<number[]>([]), [verified, setVerified] = useState(false);
  const loadSequence = useRef(0);
  const load = async () => {
    const sequence = ++loadSequence.current;
    const q = source ? '?source=' + encodeURIComponent(source) : '';
    const [items, proposals, review] = await Promise.all([api('/reader/info/reports' + q), api('/reader/info/proposals' + q), source ? api('/reader/info/review' + q) : Promise.resolve(undefined)]);
    if (sequence !== loadSequence.current) return;
    setReports(items); setQueue(proposals); setAudit(review);
    setChecks([]); setChosen([]); setVerified(false);
  };
  const linkedReport = params.get('report') || '';
  useEffect(() => { setError(''); setSelected(linkedReport); setHtml(''); setReports([]); setQueue([]); setAudit(undefined); setChecks([]); void load().catch(e => setError(String(e))); return () => { loadSequence.current++; }; }, [source, linkedReport]);
  useEffect(() => {
    if (!selected) return;
    let live = true; setLoading(true); setHtml('');
    void api('/report?path=' + encodeURIComponent(selected)).then(result => { if (live) setHtml(result.html); }).catch(e => { if (live) setError(String(e)); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [selected]);
  const change = (value: object) => navigate('reports?' + new URLSearchParams({ source, tab, ...value }));
  const filtered = reports.filter(item => (item.title + item.path).toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <div className="report-center"><div className="section-heading"><h2>{t('reportCenter')}</h2><button onClick={() => void load().catch(e => setError(String(e)))}>{t('refresh')}</button></div>
    <div className="study-filters"><label>{t('source')}<select value={source} onChange={e => change({ source: e.target.value })}><option value="">{t('allSources')}</option>{sources.map(s => <option key={s.source_id}>{s.source_id}</option>)}</select></label><nav className="study-tabs"><button className={tab === 'reports' ? 'selected' : ''} onClick={() => change({ tab: 'reports' })}>{t('reports')}</button><button className={tab === 'queue' ? 'selected' : ''} onClick={() => change({ tab: 'queue' })}>{t('reviewQueue')} · {queue.filter(item => item.status === 'open').length}</button></nav></div>
    {error && <div className="notice error" role="alert">{error}</div>}<AuditStatus value={audit}/>
    {audit?.batches?.map((b: any) => <ReviewBatch key={b.id} batch={b} reload={load}/>)}
    {source && <a className="text-link" href={'#workflows/kb-qa/' + encodeURIComponent(source)}>{t('batchStart')} ↗</a>}
    {tab === 'queue' && <p className="muted">{t('reviewQueueHint')} <a href={'#workflows/kb-review/' + encodeURIComponent(source)}>{t('reviewWithModel')} ↗</a></p>}
    {tab === 'queue' && <section className="panel"><h3>{t('checkOldFindings')}</h3><p className="muted">{t('checkOldHint')}</p><button disabled={busy} onClick={() => { setBusy(true); setError(''); setChosen([]); setVerified(false); void api('/content-review/checks' + (source ? '?source=' + encodeURIComponent(source) : '')).then(setChecks).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t('checkOldFindings')}</button>
      {checks.map(c => <div key={c.id}><label className="check-line"><input type="checkbox" checked={chosen.includes(c.id)} disabled={!c.passed || busy} onChange={e => { setChosen(e.target.checked ? [...chosen, c.id] : chosen.filter(id => id !== c.id)); setVerified(false); }}/><strong>#{c.id} · {c.kind} · {t(c.passed ? 'checkPassed' : 'checkManual')}</strong></label><p>{c.reason}</p><details><summary>{t('checkEvidence')}</summary><pre>{JSON.stringify(c.evidence, null, 2)}</pre></details></div>)}
      {checks.length > 0 && <><label className="check-line"><input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)}/>{t('checkConfirm')}</label><button disabled={busy || !chosen.length || !verified} onClick={() => { setBusy(true); setError(''); void api('/content-review/close', { confirmed: verified, items: checks.filter(c => chosen.includes(c.id)).map(c => ({ id: c.id, token: c.token, reason: c.reason })) }).then(load).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t('checkClose')} · {chosen.length}</button></>}
    </section>}
    {tab === 'reports' ? <><label>{t('search')}<input value={search} onChange={e => setSearch(e.target.value)}/></label><div className="report-layout"><nav aria-label={t('reports')}>{filtered.length ? filtered.map(item => <button key={item.path} disabled={!item.exists} className={selected === item.path ? 'selected' : ''} onClick={() => setSelected(item.path)}><strong>{item.title}</strong><small>{item.kind} · {item.sources.join(', ') || t('reportUnassigned')}</small>{!item.exists && <small>{t('reportUnavailable')}</small>}{item.associations.includes('page-history') && <small>{t('reportHistoryLink')}</small>}</button>) : <p>{t('noReports')}</p>}</nav><article className="report-document">{loading ? <p>{t('loading')}</p> : html ? <><small className="file-path">{selected}</small><Html html={html}/></> : <p className="muted">{t('reportRead')}</p>}</article></div></> : <><p className="muted">{t('reviewNoEdit')}</p><label className="check-line"><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)}/>{t('reviewAll')}</label>{!queue.some(item => all || item.status === 'open') && <p>{t('reviewNothing')}</p>}{queue.filter(item => all || item.status === 'open').map(item => <section className="panel" key={item.id}><div className="section-heading"><h3>#{item.id} · {item.kind}</h3><span className="badge">{item.status}</span></div><small>{item.source_id} · {item.created_at}</small>{item.target_path && <a className="text-link" href={'#/' + encodeURI(item.target_path)}>{item.target_path} ↗</a>}<div className="finding-text">{item.question ? <><blockquote>{item.question.quote}</blockquote><p>{item.question.text}</p><small>{item.question.source}{item.question.source_page && ` · ${t('sourcePageNumber')} ${item.question.source_page}`}</small></> : <pre>{item.reason}</pre>}</div>{item.decision && <p>{item.decision.decision} · {item.decision.reason} · {item.decision.at}</p>}{item.status === 'open' && <form onSubmit={e => { e.preventDefault(); const form = new FormData(e.currentTarget), decision = ((e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement).value; setBusy(true); setError(''); void api('/study/proposal-decision', { id: item.id, expected_status: item.status, reason: form.get('reason'), decision, verified: form.has('verified') }).then(load).catch(e => setError(String(e))).finally(() => setBusy(false)); }}><label>{t('reviewReason')}<textarea key={checks.find(c => c.id === item.id)?.token || item.id} name="reason" required rows={2} defaultValue={checks.find(c => c.id === item.id)?.reason || ''}/></label><label className="check-line"><input type="checkbox" name="verified"/>{t('reviewVerified')}</label><div className="button-row"><button disabled={busy} value="resolved">{t('reviewResolve')}</button><button disabled={busy} value="rejected">{t('reviewReject')}</button><button disabled={busy} value="deferred">{t('reviewDefer')}</button></div></form>}</section>)}</>}
  </div>;
}

export function LearningPreferences() {
  const t = useT(), [value, setValue] = useState<any>(), [error, setError] = useState('');
  useEffect(() => { void api('/reader/info/preferences').then(setValue).catch(e => setError(String(e))); }, []);
  return <section className="panel"><h3>{t('learningPreferences')}</h3><p>{t('goalScopeHint')}</p>{error && <p role="alert">{error}</p>}{value && <><details><summary>{t('globalLearningGoal')}</summary><code>{'wiki/_meta/purpose.md'}</code><pre>{value.global || '—'}</pre></details>{value.sources.map((item: any) => <details key={item.source}><summary>{t('sourceLearningGoal')} · {item.source}</summary><code>{`pipeline-workspace/staging/${item.source}/learning-goal.md`}</code><pre>{item.goal}</pre></details>)}</>}</section>;
}
