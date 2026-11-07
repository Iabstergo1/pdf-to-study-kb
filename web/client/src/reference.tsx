import { useEffect, useRef, useState, type RefObject } from 'react';
import { api } from './api.ts';
import { Html } from './content.tsx';
import { useT } from './i18n.tsx';

export type TextSelection = { quote: string; prefix: string; suffix: string; start: number; end: number };
export function selectedText(element: HTMLElement): TextSelection | undefined {
  const selection = window.getSelection();
  if (!selection?.rangeCount || selection.isCollapsed) return;
  const range = selection.getRangeAt(0);
  if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) return;
  const before = range.cloneRange(); before.selectNodeContents(element); before.setEnd(range.startContainer, range.startOffset);
  const text = element.textContent || '', quote = range.toString(), start = before.toString().length, end = start + quote.length;
  if (!quote.trim()) return;
  return { quote, start, end, prefix: text.slice(Math.max(0, start - 40), start), suffix: text.slice(end, end + 40) };
}

export function quoteRange(element: HTMLElement, note: any): Range | undefined {
  const text = element.textContent || '', candidates: number[] = [];
  if (!note.quote) return;
  let start = text.indexOf(note.quote);
  while (start !== -1) {
    if ((!note.prefix || text.slice(Math.max(0, start - note.prefix.length), start) === note.prefix) && (!note.suffix || text.slice(start + note.quote.length, start + note.quote.length + note.suffix.length) === note.suffix)) candidates.push(start);
    start = text.indexOf(note.quote, start + 1);
  }
  if (candidates.length !== 1) return;
  const begin = candidates[0], end = begin + note.quote.length, range = document.createRange(), walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let offset = 0, node: Node | null, opened = false;
  while ((node = walker.nextNode())) {
    const length = node.textContent?.length || 0;
    if (!opened && begin >= offset && begin < offset + length) { range.setStart(node, begin - offset); opened = true; }
    if (opened && end <= offset + length) { range.setEnd(node, end - offset); return range; }
    offset += length;
  }
}

export function ReferencePanel({ page, previewPath, tab, setTab, selection, bodyRef, onHeading, onOpen, graph, onPreview }: {
  page: any; previewPath: string; tab: string; setTab: (tab: string) => void; selection?: TextSelection; bodyRef: RefObject<HTMLDivElement | null>;
  onHeading: (id: string) => void; onOpen: (path: string) => void; graph: any; onPreview: (path: string) => void;
}) {
  const t = useT(), [preview, setPreview] = useState<any>(), [evidence, setEvidence] = useState<any>(), [source, setSource] = useState(''), [number, setNumber] = useState(1);
  const [original, setOriginal] = useState<any>(), [imageMissing, setImageMissing] = useState(false), [zoom, setZoom] = useState(100);
  const [notes, setNotes] = useState<any[]>([]), [text, setText] = useState(''), [question, setQuestion] = useState(false), [noteSource, setNoteSource] = useState(''), [notePage, setNotePage] = useState('');
  const [color, setColor] = useState('yellow'), [editing, setEditing] = useState<any>(), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const noteId = useRef(crypto.randomUUID());
  const [unlinked, setUnlinked] = useState<string[]>([]);
  const loadNotes = () => api('/study/annotations?page=' + encodeURIComponent(page.path)).then(setNotes);
  useEffect(() => {
    if (!page) { setEvidence(undefined); setOriginal(undefined); setNotes([]); return; }
    let live = true;
    setEvidence(undefined); setOriginal(undefined); setNotes([]); setError(''); setNotice(''); setEditing(undefined); setQuestion(false); setNotePage(''); noteId.current = crypto.randomUUID();
    setText(sessionStorage.getItem('study-note-draft:' + page.path) || '');
    void api('/reader/info/evidence?page=' + encodeURIComponent(page.path)).then(value => { if (live) { setEvidence(value); setSource(value.sources[0]?.source || ''); setNoteSource(value.sources[0]?.source || ''); setNumber(value.sources[0]?.windows[0]?.start || 1); } }).catch(e => { if (live) setError(String(e)); });
    void api('/study/annotations?page=' + encodeURIComponent(page.path)).then(value => { if (live) setNotes(value); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [page?.path, page?.revision]);

  useEffect(() => {
    if (!previewPath) return;
    let live = true; setPreview(undefined);
    void api('/reader/page?path=' + encodeURIComponent(previewPath)).then(value => { if (live) setPreview(value); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [previewPath]);
  const sourceInfo = evidence?.sources.find((item: any) => item.source === source);
  useEffect(() => {
    if (!source || tab !== 'source' || !sourceInfo?.pages) return;
    let live = true; setOriginal(undefined); setImageMissing(false);
    void api('/reader/info/source-page?' + new URLSearchParams({ source, page: String(number) })).then(value => { if (live) setOriginal(value); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [source, number, tab, sourceInfo?.pages]);
  useEffect(() => {
    const element = bodyRef.current;
    if (!element || !page || element.dataset.page !== page.path) return;
    const colors = ['yellow', 'green', 'blue'];
    const frame = requestAnimationFrame(() => {
      const missing: string[] = [];
      for (const color of colors) {
        const ranges = notes.filter(note => (note.color || 'yellow') === color).flatMap(note => { const range = quoteRange(element, note); if (!range && note.quote) missing.push(note.id); return range ? [range] : []; });
        if ((CSS as any).highlights && (window as any).Highlight) (CSS as any).highlights.set('study-' + color, new (window as any).Highlight(...ranges));
      }
      setUnlinked(missing);
    });
    return () => { cancelAnimationFrame(frame); for (const color of colors) (CSS as any).highlights?.delete('study-' + color); };
  }, [notes, page?.path, page?.html, bodyRef]);
  const save = async () => {
    if (!page || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/study/' + (question ? 'flag' : 'annotation'), { ...selection, id: noteId.current, ...(editing ? { id: editing.id, version: editing.version } : {}), page: page.path, revision: page.revision, text, color, ...(question ? { source: noteSource, source_page: notePage ? Number(notePage) : undefined } : {}) });
      noteId.current = crypto.randomUUID(); sessionStorage.removeItem('study-note-draft:' + page.path); setText(''); setEditing(undefined); setNotice(t(question ? 'questionTracked' : 'saved')); await loadNotes();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const locate = (note: any) => {
    const range = bodyRef.current && quoteRange(bodyRef.current, note);
    if (!range) return;
    const element = range.startContainer.parentElement;
    element?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
    const selected = window.getSelection(); selected?.removeAllRanges(); selected?.addRange(range);
  };
  return <>
    <nav className="reference-tabs" aria-label={t('reference')}>{[['toc', 'contents'], ['source', 'sourceCompare'], ['notes', 'personalNotes'], ...(previewPath ? [['preview', 'referencePreview']] : [])].map(([id, label]) => <button key={id} className={tab === id ? 'selected' : ''} onClick={() => setTab(id)}>{t(label as 'contents')}</button>)}</nav>
    {error && <p className="notice error" role="alert">{error}</p>}
    {tab === 'toc' && <><h3>{t('contents')}</h3>{page?.toc?.map((heading: any) => <button className="toc-entry" key={heading.id} style={{ paddingLeft: 8 + (heading.level - 1) * 10 }} onClick={() => onHeading(heading.id)}>{heading.text}</button>)}<h3>{t('localGraph')}</h3><LocalGraph graph={graph} path={page?.path} go={onPreview}/><h3>{t('backlinks')}</h3>{page?.backlinks?.map((item: any) => <button className="toc-entry" key={item.path} onClick={() => onPreview(item.path)}>{item.title} ↗</button>)}</>}
    {tab === 'preview' && (preview ? <div className="reference-preview"><div className="section-heading"><h3>{preview.title}</h3><button onClick={() => onOpen(preview.path)}>{t('openPage')} ↗</button></div><div onClick={event => { const link = (event.target as HTMLElement).closest('a[href^="#/"]'); if (link && !(event as any).ctrlKey && !(event as any).metaKey) { event.preventDefault(); onPreview(decodeURIComponent(link.getAttribute('href')!.slice(2)).split('?')[0]); } }}><Html html={preview.html}/></div></div> : <p>{t('loading')}</p>)}
    {tab === 'source' && <>{!evidence ? <p>{t('loading')}</p> : !evidence.sources.length ? <p>{t('sourceNoEvidence')}</p> : <>
      <label>{t('source')}<select value={source} onChange={e => { setSource(e.target.value); const next = evidence.sources.find((s: any) => s.source === e.target.value); setNumber(next.windows[0]?.start || 1); }}>
        {evidence.sources.map((item: any) => <option key={item.source}>{item.source}</option>)}</select></label>
      <p className="muted">{t(sourceInfo?.precision === 'window' ? 'sourcePrecisionHint' : 'sourceOnlyHint')}</p>
      {!!sourceInfo?.windows.length && <label>{t('relatedWindows')}<select value={sourceInfo.windows.find((w: any) => w.start <= number && w.end >= number)?.id || ''} onChange={e => { const window = sourceInfo.windows.find((w: any) => w.id === e.target.value); if (window) setNumber(window.start); }}><option value="">—</option>{sourceInfo.windows.map((w: any) => <option key={w.id} value={w.id}>{w.start}–{w.end} · {w.id}</option>)}</select></label>}
      {sourceInfo?.pages > 0 ? <><div className="source-pager"><button disabled={number <= 1} aria-label={t('previous')} onClick={() => setNumber(n => n - 1)}>←</button><label>{t('sourcePageNumber')}<input type="number" min={1} max={sourceInfo.pages} value={number} onChange={e => { const value = Number(e.target.value); if (value >= 1 && value <= sourceInfo.pages) setNumber(value); }}/></label><span>/ {sourceInfo.pages}</span><button disabled={number >= sourceInfo.pages} aria-label={t('next')} onClick={() => setNumber(n => n + 1)}>→</button></div>
        <label>{t('imageZoom')}<input type="range" min={75} max={200} step={25} value={zoom} onChange={e => setZoom(Number(e.target.value))}/></label>
        {imageMissing ? <p className="muted">{t('sourceNoImage')}</p> : <div className="source-image-scroll"><a href={'/api/reader/source-image?' + new URLSearchParams({ source, page: String(number) })} target="_blank" rel="noreferrer" title={t('imageFull')}><img style={{ width: zoom + '%', maxWidth: 'none' }} src={'/api/reader/source-image?' + new URLSearchParams({ source, page: String(number) })} alt={`${source} p.${number}`} onError={() => setImageMissing(true)}/></a></div>}
        <details open><summary>{t('sourceText')}</summary>{original ? <pre className="source-text">{original.text || t('sourceNoEvidence')}</pre> : <p>{t('loading')}</p>}</details>
        <button onClick={() => { setTab('notes'); setQuestion(true); setNoteSource(source); setNotePage(String(number)); }}>{t('flagQuestion')}</button>
      </> : <p>{t('sourceNoEvidence')}</p>}
    </>}</>}
    {tab === 'notes' && <><p className="muted">{t('noteIndependent')}</p>{page && <form className="annotation-form" onSubmit={e => { e.preventDefault(); void save(); }}>
      {selection && <blockquote>{selection.quote}</blockquote>}
      <label>{t('noteText')}<textarea value={text} onChange={e => { setText(e.target.value); sessionStorage.setItem('study-note-draft:' + page.path, e.target.value); }} rows={4} required={question}/></label>
      <div className="note-colors">{['yellow', 'green', 'blue'].map(value => <button type="button" key={value} className={'color-' + value + (color === value ? ' chosen' : '')} aria-label={value} aria-pressed={color === value} onClick={() => setColor(value)}/>)}</div>
      <label className="check-line"><input type="checkbox" checked={question} disabled={!!editing} onChange={e => setQuestion(e.target.checked)}/>{t('flagQuestion')}</label>
      {question && <><label>{t('source')}<select required value={noteSource} onChange={e => setNoteSource(e.target.value)}>{evidence?.sources.map((item: any) => <option key={item.source}>{item.source}</option>)}</select></label><label>{t('referencePageOptional')}<input type="number" min={1} max={evidence?.sources.find((s: any) => s.source === noteSource)?.pages || undefined} value={notePage} onChange={e => setNotePage(e.target.value)}/></label></>}
      <button className="primary" disabled={busy || !selection && !text.trim()}>{t(question ? 'questionSave' : 'noteSave')}</button>{editing && <button type="button" onClick={() => setEditing(undefined)}>{t('back')}</button>}
    </form>}{notice && <p role="status" className="notice">{notice}</p>}{!notes.length && <p className="muted">{t('noteEmpty')}</p>}
      {notes.map(note => <article className="annotation" key={note.id}><blockquote>{note.quote}</blockquote><p>{note.text}</p><small>{new Date(note.at).toLocaleString()}</small>{unlinked.includes(note.id) && <p className="notice">{t('noteUnlinked')}</p>}<div className="button-row"><button disabled={!note.quote || unlinked.includes(note.id)} onClick={() => locate(note)}>{t('noteLocate')}</button>{note.kind === 'question' ? <a href={'#reports?source=' + encodeURIComponent(note.source) + '&tab=queue'}>{t('reviewQueue')} ↗</a> : <><button disabled={!selection} onClick={() => { setEditing(note); setText(note.text); setQuestion(false); }}>{t('noteRelink')}</button><button disabled={busy} onClick={() => { setBusy(true); void api('/study/delete-annotation', { id: note.id, version: note.version }).then(loadNotes).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t('noteRemove')}</button></>}</div></article>)}
    </>}
  </>;
}

function LocalGraph({ graph, path, go }: any) {
  const center = graph.nodes.find((n: any) => n.path === path); if (!center) return null;
  const edges = graph.edges.filter((e: any) => e.source === center.id || e.target === center.id);
  const ids = new Set([center.id, ...edges.flatMap((e: any) => [e.source, e.target])]);
  const nodes = [center, ...graph.nodes.filter((n: any) => n.id !== center.id && ids.has(n.id)).slice(0, 15)];
  const points = new Map(nodes.map((n: any, i: number) => [n.id, i === 0 ? { x: 120, y: 100 } : { x: 120 + Math.cos((i - 1) * Math.PI * 2 / (nodes.length - 1)) * 76, y: 100 + Math.sin((i - 1) * Math.PI * 2 / (nodes.length - 1)) * 76 }]));
  return <svg className="local-graph" viewBox="0 0 240 200">{edges.map((e: any) => { const a: any = points.get(e.source), b: any = points.get(e.target); return a && b && <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}/>; })}{nodes.map((n: any) => { const p: any = points.get(n.id); return <g key={n.id} onClick={() => go(n.path)} role="link" aria-label={n.label} tabIndex={0} onKeyDown={e => { if (e.key === 'Enter') go(n.path); }}><circle cx={p.x} cy={p.y} r={n.id === center.id ? 7 : 4}/><text x={p.x + 9} y={p.y + 4}>{n.label.slice(0, 10)}</text></g>; })}</svg>;
}
