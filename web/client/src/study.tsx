import { useEffect, useRef, useState } from 'react';
import { api, navigate } from './api.ts';
import { Html, textHtml } from './content.tsx';
import { Dialog } from './dialog.tsx';
import { useT } from './i18n.tsx';

export function LearningHome() {
  const t = useT(), [summary, setSummary] = useState<any>(), [error, setError] = useState('');
  useEffect(() => { void api('/study/summary').then(setSummary).catch(e => setError(String(e))); }, []);
  const recent = summary?.recent?.[0], fallback = localStorage.getItem('study-kb-last-page');
  return <section className="learning-home"><div><div className="eyebrow">{t('studyHome')}</div><h1>{t('subtitle')}</h1><p>{t('intro')}</p></div>
    {error && <p role="alert" className="notice error">{error}</p>}
    <div className="learning-cards"><a className="learning-card" href={'#/' + encodeURI(recent?.page || fallback || 'overview.md')}><span>↗</span><small>{t('continueReading')}</small><strong>{recent?.title || recent?.page?.split('/').at(-1)?.replace('.md', '') || t('reader')}</strong></a>
      <a className="learning-card primary-card" href="#study/planned"><span>◷</span><small>{t('todayReview')}</small><strong>{summary ? `${summary.completed} / ${summary.planned}` : '…'}</strong><progress max={Math.max(1, summary?.planned || 1)} value={summary?.completed || 0}/><small>{summary?.followups > 0 ? `${t('studyFollowups')}: ${summary.followups}` : summary?.settings.sources.length ? t('studyPlan') : t('studyUnenrolled')}</small></a>
      <a className="learning-card" href="#study/practice"><span>◇</span><small>{t('freePractice')}</small><strong>{summary?.total ?? '…'} {t('quiz')}</strong><small>{t('allSources')}</small></a></div>
  </section>;
}

export function Study({ route }: { route: string }) {
  const t = useT(), [base, queryString = ''] = route.split('?'), view = base.split('/')[1] || 'planned';
  const params = new URLSearchParams(queryString), source = params.get('source') || '', domain = params.get('domain') || '', page = params.get('page') || '';
  const [summary, setSummary] = useState<any>(), [cards, setCards] = useState<any[]>([]), [history, setHistory] = useState<any[]>([]);
  const [session, setSession] = useState<any>(), [savedSession, setSavedSession] = useState<any>(), [revealed, setRevealed] = useState(false), [schedule, setSchedule] = useState(false);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [undo, setUndo] = useState<any>(), [due, setDue] = useState<string | null>();
  const [backup, setBackup] = useState<any>(), [preview, setPreview] = useState<any>(), [policy, setPolicy] = useState('keep'), [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [visible, setVisible] = useState(40), locked = useRef(false), eventId = useRef(crypto.randomUUID());
  const refreshSummary = () => api('/study/summary').then(setSummary);
  const loadCards = () => api('/study/cards?' + new URLSearchParams({ mode: view === 'planned' ? 'planned' : 'practice', source, domain, page })).then(result => { setCards(result.cards); return result.cards; });
  useEffect(() => {
    let live = true;
    setSession(undefined); setUndo(undefined); setError(''); setVisible(40);
    void Promise.all([api('/study/summary'), api('/study/state'), api('/study/cards?' + new URLSearchParams({ mode: view === 'planned' ? 'planned' : 'practice', source, domain, page }))]).then(([sum, state, result]) => {
      if (live) { setSummary(sum); setSavedSession(state.state['practice-session']); setCards(result.cards); }
    }).catch(e => { if (live) setError(String(e)); });
    if (view === 'history') void api('/study/history').then(value => { if (live) setHistory(value); }).catch(e => setError(String(e)));
    return () => { live = false; };
  }, [view, source, domain, page]);
  const current = session && cards.find(card => card.id === session.remaining[0]);
  const persist = async (next: any) => { setSession(next); setSavedSession(next); await api('/study/session', next); };
  const start = async (ids?: string[]) => {
    setBusy(true); setError('');
    try {
      const fresh = await loadCards(), available = new Set(fresh.map((c: any) => c.id));
      const remaining = ids ? ids.filter(id => available.has(id)) : fresh.map((c: any) => c.id);
      await persist({ mode: view, source, domain, page, schedule, remaining, done: 0, total: remaining.length });
      setRevealed(false); setDue(undefined); eventId.current = crypto.randomUUID();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const answer = async (rating: number) => {
    if (!current || !revealed || current.unanswered || locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try {
      const result = await api('/study/review', { id: current.id, revision: current.revision, version: current.version, event_id: eventId.current, rating, mode: view === 'planned' ? 'planned' : 'practice', schedule: session.schedule });
      setUndo({ event_id: result.event_id, card_id: current.id }); setDue(result.due || null); eventId.current = crypto.randomUUID(); setRevealed(false);
      await persist({ ...session, remaining: session.remaining.slice(1), done: session.done + 1 });
      setRevealed(false); eventId.current = crypto.randomUUID();
      void refreshSummary().catch(e => setError(String(e)));
    } catch (e) { setError(String(e)); } finally { setBusy(false); locked.current = false; }
  };
  const undoRating = async () => {
    if (!undo || busy) return;
    setBusy(true); setError('');
    try {
      await api('/study/undo', { event_id: undo.event_id });
      const all = await api('/study/cards'); setCards(all.cards);
      await persist({ ...session, remaining: [undo.card_id, ...session.remaining], done: Math.max(0, session.done - 1) });
      setUndo(undefined); setDue(undefined); setRevealed(false); eventId.current = crypto.randomUUID(); await refreshSummary();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest('input,textarea,select,button,a,[contenteditable="true"]') || busy || !current) return;
      if (event.code === 'Space') { event.preventDefault(); setRevealed(true); }
      if (revealed && ['1', '2', '3', '4'].includes(event.key)) { event.preventDefault(); void answer(Number(event.key)); }
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [current, revealed, busy, session]);
  const filter = (key: string, value: string) => { const p = new URLSearchParams(queryString); if (value) p.set(key, value); else p.delete(key); navigate('study/practice?' + p); };
  const downloadHistory = () => {
    const text = '# 我的学习足迹\n\n' + history.map(r => `- ${r.at} · ${r.card.stem} · ${r.undone ? t('studyUndo') : [t('studyAgain'), t('studyHard'), t('studyGood'), t('studyEasy')][r.rating - 1]}\n`).join('');
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' })); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'study-history.md'; anchor.click(); URL.revokeObjectURL(url);
  };
  return <div className="study-workspace"><div className="section-heading"><h2>{t('study')}</h2><a href="#reader">{t('reader')} ↗</a></div>
    <nav className="study-tabs">{[['planned', 'studyPlan'], ['practice', 'freePractice'], ['settings', 'studySettings'], ['history', 'studyHistory']].map(([id, label]) => <a key={id} className={view === id ? 'selected' : ''} href={'#study/' + id}>{t(label as 'studyPlan')}</a>)}</nav>
    {error && <div className="notice error" role="alert">{error}<button onClick={() => { setError(''); setRevealed(false); void loadCards().catch(e => setError(String(e))); }}>{t('refresh')}</button></div>}
    {!summary ? <p>{t('loading')}</p> : view === 'settings' ? <>
      <form className="panel" key={JSON.stringify(summary.settings)} onSubmit={e => { e.preventDefault(); const values = new FormData(e.currentTarget); setBusy(true); void api('/study/preferences', { sources: values.getAll('sources'), daily_limit: Number(values.get('daily_limit')), new_limit: Number(values.get('new_limit')), timezone_offset: -new Date().getTimezoneOffset() }).then(refreshSummary).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>
        <h3>{t('studyEnrol')}</h3><div className="source-checklist">{summary.sources.map((id: string) => <label key={id}><input type="checkbox" name="sources" value={id} defaultChecked={summary.settings.sources.includes(id)}/>{id}</label>)}</div>
        <div className="form-row"><label>{t('studyDaily')}<input name="daily_limit" type="number" min={1} max={500} defaultValue={summary.settings.daily_limit} required/></label><label>{t('studyNew')}<input name="new_limit" type="number" min={0} max={100} defaultValue={summary.settings.new_limit} required/></label></div><p className="muted">{t('studyCapHint')}</p><button className="primary" disabled={busy}>{t('save')}</button>
      </form><section className="panel"><h3>{t('studySettings')}</h3><p>{t('studySavedLocally')}</p><div className="button-row"><a className="button-link" href="/api/study/export" download>{t('studyBackup')}</a><label className="file-button">{t('studyRestore')}<input type="file" accept="application/json,.json" onChange={e => { const file = e.target.files?.[0]; if (!file) return; setBusy(true); setError(''); void file.text().then(JSON.parse).then(async value => { const result = await api('/study/import', { backup: value }); setBackup(value); setPreview(result); setPolicy('keep'); setReplaceConfirmed(false); }).catch(e => setError(String(e))).finally(() => setBusy(false)); e.target.value = ''; }}/></label></div></section>
    </> : view === 'history' ? <><div className="section-heading"><h3>{t('studyHistory')}</h3><button disabled={!history.length} onClick={downloadHistory}>{t('studyExportFootprint')}</button></div>{!history.length && <p className="muted">{t('studyNoHistory')}</p>}{history.slice(0, visible).map(item => <article className="history-item" key={item.id}><div><a href={'#/' + encodeURI(item.card.rel)}>{item.card.title}</a><p>{item.card.stem}</p><small>{new Date(item.at).toLocaleString()} · {t(item.mode === 'planned' ? 'studyPlan' : 'freePractice')}</small></div><span className="badge">{item.undone ? t('studyUndo') : [t('studyAgain'), t('studyHard'), t('studyGood'), t('studyEasy')][item.rating - 1]}</span></article>)}{history.length > visible && <button onClick={() => setVisible(n => n + 40)}>{t('next')}</button>}</> : <>
      {!session && <>{view === 'practice' && <div className="study-filters"><label>{t('source')}<select value={source} onChange={e => filter('source', e.target.value)}><option value="">{t('allSources')}</option>{summary.sources.map((id: string) => <option key={id}>{id}</option>)}</select></label><label>{t('domain')}<select value={domain} onChange={e => filter('domain', e.target.value)}><option value="">{t('allDomains')}</option>{summary.domains.map((id: string) => <option key={id}>{id}</option>)}</select></label></div>}
        {view === 'planned' && <section className="plan-summary"><strong>{t('todayReview')} {summary.completed} / {summary.planned}</strong><progress value={summary.completed} max={Math.max(1, summary.planned)}/><small>{t('studyRepetitions')}: {summary.repetitions}</small>{summary.followups > 0 && <small>{t('studyFollowups')}: {summary.followups}</small>}<details><summary>{t('studyBacklog')}</summary><p>{summary.due} · {t('studyCapHint')}</p></details><a href="#study/settings">{t('studySettings')} →</a></section>}
        {view === 'practice' && <label className="check-line"><input type="checkbox" checked={schedule} onChange={e => setSchedule(e.target.checked)}/>{t('studySchedulePractice')}<small>{t('studyPracticeHint')}</small></label>}
        <div className="button-row"><button className="primary" disabled={busy || !cards.length} onClick={() => void start()}>{t('studyStart')} · {cards.length}</button>{savedSession?.remaining?.length > 0 && savedSession.mode === view && savedSession.source === source && savedSession.domain === domain && savedSession.page === page && <button disabled={busy} onClick={() => { const known = new Set(cards.map(c => c.id)); const restored = { ...savedSession, remaining: savedSession.remaining.filter((id: string) => known.has(id)) }; setSession(restored); setRevealed(false); eventId.current = crypto.randomUUID(); }}>{t('studyResume')}</button>}</div>
        {!cards.length && <p className="muted">{view === 'planned' ? t('studyPlanEmpty') : t('noResults')}</p>}
        <div className="question-list">{cards.slice(0, visible).map((card, index) => <button key={card.id} onClick={() => void start(cards.slice(index).map(c => c.id))}><small>{card.title}</small><Html inline html={textHtml(card.stem)}/>{card.unanswered && <small>{t('studyUnanswered')}</small>}</button>)}</div>{cards.length > visible && <button onClick={() => setVisible(n => n + 40)}>{t('next')}</button>}
      </>}
      {session && <><div className="study-progress"><span>{session.done} / {session.total}</span><progress value={session.done} max={Math.max(1, session.total)}/><button disabled={busy} onClick={() => { void persist(session).then(() => setSession(undefined)).catch(e => setError(String(e))); }}>{t('studyPause')}</button></div>
        {current ? <article className="flashcard" key={current.id}><div className="eyebrow">{current.title}</div>{current.changed && <p className="notice">{t('studyChanged')}</p>}<h2><Html inline html={textHtml(current.stem)}/></h2>
          {!revealed ? <button className="primary reveal-answer" disabled={busy} onClick={() => setRevealed(true)}>{t('answer')}</button> : <><div className="flashcard-answer">{current.unanswered ? <p>{t('studyUnanswered')}</p> : <Html html={current.answer_html}/>}</div><a href={'#/' + encodeURI(current.rel)} target="_blank" rel="noreferrer">{current.title} ↗</a><div className="rating-buttons">{(['studyAgain', 'studyHard', 'studyGood', 'studyEasy'] as const).map((label, i) => <button key={label} disabled={busy || current.unanswered} onClick={() => void answer(i + 1)}><kbd>{i + 1}</kbd>{t(label)}</button>)}</div></>}
          <div className="flashcard-footer"><small>{t('studyKeyboard')}</small><button disabled={busy} onClick={() => { void persist({ ...session, remaining: session.remaining.slice(1), total: session.total - 1 }).catch(e => setError(String(e))); setRevealed(false); eventId.current = crypto.randomUUID(); }}>{t('studySkip')}</button></div>
        </article> : <section className="empty"><span>✓</span><h2>{t('studyFinished')}</h2><p>{t('studyFinishedHint')}</p><button onClick={() => { setSession(undefined); void loadCards().catch(e => setError(String(e))); }}>{t('back')}</button><a href="#study/practice">{t('freePractice')} →</a></section>}
        <div className="study-feedback" aria-live="polite">{due !== undefined && <span>{due ? `${t('studyDue')}: ${new Date(due).toLocaleString()}` : t('studyNoSchedule')}</span>}{undo && <button disabled={busy} onClick={() => void undoRating()}>{t('studyUndo')}</button>}</div>
      </>}
    </>}
    {preview && <Dialog label={t('studyImportPreview')} onClose={() => setPreview(undefined)} busy={busy}><div className="section-heading"><h3>{t('studyImportPreview')}</h3><button disabled={busy} onClick={() => setPreview(undefined)}>×</button></div><p>{t('studyAdditions')}: {preview.additions} · {t('studyConflicts')}: {preview.conflicts.length}</p>{preview.replaced_history > 0 && <p>{t('studyHistoryReplaced')}: {preview.replaced_history}</p>}<select aria-label={t('studyConflicts')} value={policy} onChange={e => setPolicy(e.target.value)}><option value="keep">{t('studyKeep')}</option><option value="replace">{t('studyReplace')}</option></select>{preview.conflicts.length > 0 && <details><summary>{t('studyConflicts')}</summary>{preview.conflicts.slice(0, 100).map((item: any) => <p key={item.table + item.id}><code>{item.table}: {item.id}</code></p>)}</details>}{policy === 'replace' && preview.conflicts.length > 0 && <label className="check-line"><input type="checkbox" checked={replaceConfirmed} onChange={e => setReplaceConfirmed(e.target.checked)}/>{t('studyReplaceConfirm')}</label>}<button className="primary" disabled={busy || policy === 'replace' && preview.conflicts.length > 0 && !replaceConfirmed} onClick={() => { setBusy(true); void api('/study/import', { backup, apply: true, policy, confirm: replaceConfirmed ? 'replace' : undefined }).then(async () => { setPreview(undefined); await refreshSummary(); }).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t('studyImportApply')}</button></Dialog>}
  </div>;
}
