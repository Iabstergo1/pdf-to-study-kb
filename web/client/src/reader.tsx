import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api, navigate } from './api.ts';
import { Html, textHtml } from './content.tsx';
import { ReferencePanel, selectedText, type TextSelection } from './reference.tsx';
import { useT } from './i18n.tsx';
export { Html } from './content.tsx';

const defaults = { explorer: 240, reference: 330, collapsed: false, referenceHidden: false, focus: false, font: 17, leading: 1.9 };
function remembered(key: string) { try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch { return {}; } }
function Splitter({ value, onChange, side }: { value: number; onChange: (value: number) => void; side: 'explorer' | 'reference' }) {
  const t = useT(), drag = useRef<{ x: number; width: number } | undefined>(undefined);
  return <div className={'splitter splitter-' + side} role="separator" aria-label={t('readerResize')} aria-orientation="vertical" aria-valuenow={value} aria-valuemin={180} aria-valuemax={640} tabIndex={0}
    onPointerDown={e => { drag.current = { x: e.clientX, width: value }; e.currentTarget.setPointerCapture(e.pointerId); document.body.classList.add('resizing'); }}
    onPointerMove={e => { if (drag.current) onChange(drag.current.width + (e.clientX - drag.current.x) * (side === 'reference' ? -1 : 1)); }}
    onPointerUp={() => { drag.current = undefined; document.body.classList.remove('resizing'); }} onLostPointerCapture={() => { drag.current = undefined; document.body.classList.remove('resizing'); }}
    onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); onChange(value + (e.key === 'ArrowRight' ? 16 : -16) * (side === 'reference' ? -1 : 1)); } }}/>;
}

export function Reader({ route, run, cli, active }: any) {
  const t = useT(), [data, setData] = useState<any>(), [missing, setMissing] = useState(false), [page, setPage] = useState<any>(), [pageMissing, setPageMissing] = useState(false);
  const [layout, setLayout] = useState({ ...defaults, ...remembered('study-kb-reader-layout') }), [layoutReady, setLayoutReady] = useState(false);
  const [filters, setFilters] = useState({ query: '', domain: '', type: '', source: '', ...remembered('study-kb-reader-filters') });
  const [graph, setGraph] = useState<any>({ nodes: [], edges: [] }), [graphLoaded, setGraphLoaded] = useState(false), [items, setItems] = useState<any[]>([]);
  const [drawer, setDrawer] = useState(''), [tab, setTab] = useState('toc'), [hover, setHover] = useState<any>(), [pinned, setPinned] = useState(''), [referencePage, setReferencePage] = useState<any>();
  const [selection, setSelection] = useState<TextSelection>(), [selectedResult, setSelectedResult] = useState(0), [stale, setStale] = useState(false), [readingStates, setReadingStates] = useState<any>({});
  const search = useRef<HTMLInputElement>(null), body = useRef<HTMLDivElement>(null), graphFrame = useRef<HTMLIFrameElement>(null);
  const hoverOpen = useRef<number | undefined>(undefined), hoverClose = useRef<number | undefined>(undefined), hoveredLink = useRef<Element | null>(null);
  const [pathPart, queryPart = ''] = route.split('?'), params = new URLSearchParams(queryPart);
  const current = pathPart.startsWith('/') ? decodeURIComponent(pathPart.slice(1)) : pathPart.startsWith('reader/') ? decodeURIComponent(pathPart.slice(7)) : '';
  const view = ['browse', 'graph', 'quiz', 'propositions'].includes(current) ? current : '';
  const currentPath = view ? '' : current || localStorage.getItem('study-kb-last-page') || 'overview.md', heading = params.get('heading');
  const layoutChange = (change: Partial<typeof defaults>) => setLayout((old: typeof defaults) => ({ ...old, ...change }));
  const reportError = (error: unknown) => run(async () => { throw error; });
  const refresh = () => api('/reader').then(value => { setData(value); setMissing(false); }).catch(() => setMissing(true));
  const checkFreshness = () => { void api('/reader/info/freshness').then(value => setStale(value.stale)).catch(reportError); };
  useEffect(() => {
    void Promise.all([api('/study/state'), api('/reader')]).then(([study, reader]) => { setReadingStates(study.state); if (study.settings.reader) setLayout({ ...defaults, ...study.settings.reader }); setData(reader); setMissing(false); }).catch(() => setMissing(true)).finally(() => setLayoutReady(true));
    void api('/reader/graph').then(setGraph).catch(reportError);
    const focus = () => checkFreshness(); window.addEventListener('focus', focus); const timer = window.setInterval(() => { if (document.visibilityState === 'visible') checkFreshness(); }, 30000); checkFreshness();
    return () => { window.removeEventListener('focus', focus); clearInterval(timer); clearTimeout(hoverOpen.current); clearTimeout(hoverClose.current); };
  }, []);
  useEffect(() => { if (!active) { void refresh(); checkFreshness(); } }, [active]);
  useEffect(() => {
    localStorage.setItem('study-kb-reader-layout', JSON.stringify(layout));
    if (!layoutReady) return;
    const timer = setTimeout(() => { void api('/study/preferences', { reader: layout }).catch(reportError); }, 500);
    return () => clearTimeout(timer);
  }, [layout, layoutReady]);
  useEffect(() => { localStorage.setItem('study-kb-reader-filters', JSON.stringify(filters)); }, [filters]);
  useEffect(() => {
    setHover(undefined); setSelection(undefined); clearTimeout(hoverOpen.current); clearTimeout(hoverClose.current); hoveredLink.current = null;
    if (view === 'quiz') { navigate('study/practice'); return; }
    if (view === 'browse') { setFilters({ query: params.get('q') || '', domain: params.get('domain') || '', type: params.get('type') || '', source: params.get('source') || '' }); return; }
    if (view === 'graph') { setGraphLoaded(true); setTab(pinned ? 'preview' : 'toc'); return; }
    if (view === 'propositions') { void api('/reader/propositions').then(setItems).catch(reportError); return; }
    if (!data) return;
    let live = true; setPageMissing(false);
    if (!data.pages.some((p: any) => p.path === currentPath)) { setPage(undefined); setPageMissing(true); return; }
    void api('/reader/page?path=' + encodeURIComponent(currentPath)).then(value => { if (live) { setPage(value); localStorage.setItem('study-kb-last-page', currentPath); setDrawer(''); } }).catch(e => { if (live) reportError(e); });
    return () => { live = false; };
  }, [currentPath, view, data, queryPart]);
  useEffect(() => {
    if (!page || view || page.path !== currentPath) return;
    const saved = readingStates['reading:' + page.path];
    const position = Number(localStorage.getItem('study-kb-progress:' + page.path) ?? saved?.scroll ?? 0);
    let y = position, timer: number | undefined;
    const save = () => { localStorage.setItem('study-kb-progress:' + page.path, String(y)); void api('/study/progress', { page: page.path, title: page.title, scroll: y }).catch(reportError); };
    const frame = requestAnimationFrame(() => {
      const target = heading && page.toc.find((h: any) => h.id === heading || h.text === heading);
      if (target) body.current?.querySelector('#' + CSS.escape(target.id))?.scrollIntoView({ block: 'start' });
      else window.scrollTo(0, position);
      y = window.scrollY;
    });
    const scroll = () => { y = window.scrollY; clearTimeout(timer); timer = window.setTimeout(save, 700); };
    window.addEventListener('scroll', scroll, { passive: true });
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); window.removeEventListener('scroll', scroll); save(); };
  }, [page, currentPath, view, heading]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { clearTimeout(hoverOpen.current); clearTimeout(hoverClose.current); setHover(undefined); setSelection(undefined); setDrawer(''); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); if (layout.collapsed) layoutChange({ collapsed: false }); setDrawer('explorer'); search.current?.focus(); }
      if (!event.ctrlKey && !event.metaKey && event.key.toLowerCase() === 'z' && !(event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]')) layoutChange({ focus: !layout.focus });
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [layout]);
  const openPreview = (path: string) => {
    if (!data?.pages.some((p: any) => p.path === path)) return;
    setPinned(path); setTab('preview'); setDrawer('context'); layoutChange({ referenceHidden: false, focus: false }); setHover(undefined);
    void api('/reader/page?path=' + encodeURIComponent(path)).then(setReferencePage).catch(reportError);
  };
  useEffect(() => {
    const receive = (event: MessageEvent) => { if (event.origin === location.origin && event.source === graphFrame.current?.contentWindow && event.data?.type === 'study-kb:preview') openPreview(event.data.path); };
    window.addEventListener('message', receive); return () => window.removeEventListener('message', receive);
  }, [data]);
  const ranked = useMemo(() => {
    const terms = filters.query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    return (data?.pages || []).filter((p: any) => (!filters.domain || p.navigation_domain === filters.domain) && (!filters.type || p.type === filters.type) && (!filters.source || p.source_refs.includes(filters.source))).map((p: any) => {
      const title = p.title.toLocaleLowerCase(), aliases = p.aliases.join(' ').toLocaleLowerCase(), body = p.search_text.toLocaleLowerCase();
      return { page: p, score: terms.every((term: string) => title.includes(term) || aliases.includes(term) || body.includes(term)) ? terms.reduce((score: number, term: string) => score + (title.includes(term) ? 8 : 0) + (aliases.includes(term) ? 6 : 0) + (body.includes(term) ? 1 : 0), terms.length ? 0 : 1) : 0 };
    }).filter((p: any) => p.score).sort((a: any, b: any) => b.score - a.score || a.page.path.localeCompare(b.page.path));
  }, [data, filters]);
  const go = (path: string) => { setDrawer(''); navigate('/' + encodeURI(path)); };
  const browse = (changes: any = {}) => { const value = { ...filters, ...changes }; navigate('reader/browse?' + new URLSearchParams({ q: value.query, domain: value.domain, type: value.type, source: value.source })); };
  const label = (key: string, fallback: string) => key === 'overview' ? t('overview') : key === 'cross-domain' ? t('crossDomain') : fallback;
  const openReference = (target: string) => { setTab(target); setDrawer('context'); layoutChange({ referenceHidden: false, focus: false }); };
  const closeHover = () => { clearTimeout(hoverOpen.current); clearTimeout(hoverClose.current); hoveredLink.current = null; hoverClose.current = window.setTimeout(() => setHover(undefined), 200); };
  const showHover = (link: Element, x: number, y: number) => {
    clearTimeout(hoverClose.current);
    if (hoveredLink.current === link) return;
    hoveredLink.current = link; clearTimeout(hoverOpen.current);
    const path = decodeURIComponent(link.getAttribute('href')!.slice(2)).split('?')[0], found = data.pages.find((p: any) => p.path === path);
    if (found) hoverOpen.current = window.setTimeout(() => setHover({ ...found, x: Math.max(12, Math.min(x, window.innerWidth - 350)), y: Math.max(12, Math.min(y + 16, window.innerHeight - 330)) }), 150);
  };
  if (missing) return <div className="empty"><span>▧</span><h2>{t('reader')}</h2><p>{t('noReader')}</p><button className="primary" disabled={active} onClick={() => cli('export-site', { format: 'json' })}>{t('buildReader')}</button></div>;
  if (!data) return <p>{t('loading')}</p>;
  const allowed = new Set(ranked.map((r: any) => r.page.path));
  const clampWidth = (side: 'explorer' | 'reference', width: number) => layoutChange({ [side]: Math.max(side === 'explorer' ? 180 : 240, Math.min(side === 'explorer' ? 420 : 640, window.innerWidth - 600, width)) });
  return <div className={'reader-workspace' + (layout.focus ? ' is-focus' : '')}>
    <div className="reader-toolbar"><button onClick={() => history.back()} title={t('readerBack')}>← {t('back')}</button><button onClick={() => { const narrow = window.innerWidth <= 1050; layoutChange({ collapsed: narrow ? false : !layout.collapsed, focus: false }); setDrawer(narrow ? drawer === 'explorer' ? '' : 'explorer' : ''); }}>{t(layout.collapsed ? 'showExplorer' : 'collapseExplorer')}</button><button onClick={() => layoutChange({ focus: !layout.focus })}>{t(layout.focus ? 'exitFocus' : 'focusMode')}</button><button onClick={() => { const narrow = window.innerWidth <= 1450; layoutChange({ referenceHidden: narrow ? false : !layout.referenceHidden, focus: false }); setDrawer(narrow ? drawer === 'context' ? '' : 'context' : ''); }}>{t('reference')}</button>
      <details className="reader-settings"><summary>{t('readingSettings')}</summary><div><label>{t('readerFont')}<input type="range" min={14} max={24} value={layout.font} onChange={e => layoutChange({ font: Number(e.target.value) })}/></label><label>{t('readerLeading')}<input type="range" min={1.4} max={2.4} step={0.1} value={layout.leading} onChange={e => layoutChange({ leading: Number(e.target.value) })}/></label></div></details>
    </div>
    {stale && <div className="reader-update notice"><span>{t('readerUpdated')}</span><button disabled={active} onClick={() => cli('export-site', { format: 'json' })}>{t('refresh')}</button></div>}
    <div className={'reader-shell' + (layout.collapsed ? ' explorer-collapsed' : '') + (layout.referenceHidden ? ' reference-hidden' : '')} style={{ '--explorer-width': layout.explorer + 'px', '--reference-width': layout.reference + 'px', '--reading-font': layout.font + 'px', '--reading-leading': layout.leading } as CSSProperties}>
      <div className="reader-mobile"><button onClick={() => { layoutChange({ collapsed: false }); setDrawer('explorer'); }}>{t('explorer')}</button><button onClick={() => { layoutChange({ referenceHidden: false }); setDrawer('context'); }}>{t('reference')}</button></div>
      {drawer && <button aria-label={t('close')} className="reader-scrim" onClick={() => setDrawer('')}/>}
      <aside className={'explorer ' + (drawer === 'explorer' ? 'open' : '')}><div className="section-heading"><h3>{t('explorer')}</h3><button className="icon" onClick={() => browse()} title={t('readerBrowse')}>▤</button></div>
        <input ref={search} aria-label={t('search')} placeholder={t('search')} value={filters.query} onChange={e => { setFilters((old: any) => ({ ...old, query: e.target.value })); setSelectedResult(0); }} onKeyDown={e => { if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedResult(i => Math.min(i + 1, ranked.length - 1)); } if (e.key === 'ArrowUp') { e.preventDefault(); setSelectedResult(i => Math.max(i - 1, 0)); } if (e.key === 'Enter' && ranked[selectedResult]) go(ranked[selectedResult].page.path); }}/>
        <div className="reader-filters"><select aria-label={t('domain')} value={filters.domain} onChange={e => browse({ domain: e.target.value })}><option value="">{t('allDomains')}</option>{data.filters.domains.map((d: any) => <option key={d.value} value={d.value}>{label(d.value, d.label)}</option>)}</select><select aria-label={t('allTypes')} value={filters.type} onChange={e => browse({ type: e.target.value })}><option value="">{t('allTypes')}</option>{data.filters.types.map((v: string) => <option key={v}>{v}</option>)}</select><select aria-label={t('allSources')} value={filters.source} onChange={e => browse({ source: e.target.value })}><option value="">{t('allSources')}</option>{data.filters.sources.map((v: string) => <option key={v}>{v}</option>)}</select></div>
        <div className="reader-views"><a href="#reader/graph">{t('graph')}</a><a href="#study/practice">{t('quiz')}</a><a href="#reader/propositions">{t('propositions')}</a></div>
        <nav className="page-tree">{filters.query ? ranked.map((r: any, i: number) => <a key={r.page.path} className={i === selectedResult ? 'selected' : ''} href={'#/' + encodeURI(r.page.path)}><strong>{r.page.title}</strong><small>{r.page.search_text.slice(0, 95)}</small></a>) : data.tree.filter((branch: any) => branch.types.some((group: any) => group.pages.some((p: any) => allowed.has(p.path)))).map((branch: any) => <details key={branch.domain_key} open><summary>{label(branch.domain_key, branch.domain)}</summary>{branch.types.filter((group: any) => group.pages.some((p: any) => allowed.has(p.path))).map((group: any) => <details key={group.type} open={page?.type === group.type || !!filters.domain || !!filters.type || !!filters.source}><summary>{group.type}</summary>{group.pages.filter((p: any) => allowed.has(p.path)).map((p: any) => <a className={page?.path === p.path ? 'selected' : ''} aria-current={page?.path === p.path ? 'page' : undefined} key={p.path} href={'#/' + encodeURI(p.path)}>{p.title}</a>)}</details>)}</details>)}{!ranked.length && <p>{t('noResults')}</p>}</nav>
        <details className="reader-downloads"><summary>{t('download')}</summary><button onClick={() => cli('export-site')} disabled={active}>{t('offline')}</button><a href="/api/download/site" download>HTML ↓</a><button onClick={() => cli('export-anki')} disabled={active}>{t('anki')}</button><a href="/api/download/anki" download>TSV ↓</a></details>
      </aside>
      <Splitter side="explorer" value={layout.explorer} onChange={value => clampWidth('explorer', value)}/>
      <article className={'reading-page' + (view === 'graph' ? ' graph-page' : '')} data-path={view ? undefined : page?.path}
        onMouseOver={event => { const link = (event.target as HTMLElement).closest('a[href^="#/"]'); if (link) showHover(link, event.clientX, event.clientY); else closeHover(); }}
        onMouseOut={event => { const link = (event.target as HTMLElement).closest('a[href^="#/"]'); if (link && !link.contains(event.relatedTarget as Node | null)) closeHover(); }}
        onMouseLeave={closeHover} onFocusCapture={event => { const link = (event.target as HTMLElement).closest('a[href^="#/"]'); if (link) { const rect = link.getBoundingClientRect(); showHover(link, rect.left, rect.bottom); } }} onBlurCapture={closeHover}>
        {graphLoaded && <div className="graph-embedded" hidden={view !== 'graph'}><h1>{t('graph')}</h1><p className="muted">{t('graphEmbeddedHint')}</p><iframe ref={graphFrame} title={t('graph')} src="/api/reader/graph-view?preview=1"/></div>}
        {view === 'browse' ? <><div className="eyebrow">{t('readerBrowse')}</div><h1>{label(filters.domain, filters.domain.replace('domain:', '')) || t('allDomains')}{filters.type && ' / ' + filters.type}</h1><p className="muted">{ranked.length} {t('written')}</p><div className="browse-results">{ranked.map(({ page: p }: any) => <a key={p.path} href={'#/' + encodeURI(p.path)}><small>{p.type} · {p.domain}</small><h3>{p.title}</h3><p>{p.search_text.slice(0, 160)}</p></a>)}</div></> : view === 'propositions' ? <><h1>{t('propositions')}</h1>{items.map((item: any, i) => <section className="proposition-card" key={i}><h3><Html inline html={textHtml(item.name)}/></h3><p><Html inline html={textHtml(item.statement)}/></p><a href={'#/' + encodeURI(item.rel)}>{item.title} ↗</a></section>)}</> : !view && (pageMissing ? <div className="empty"><p>{t('readerMissingPage')}</p><a href="#reader/browse">{t('readerBrowse')}</a></div> : page?.path === currentPath ? <>
          <nav className="breadcrumbs">{page.breadcrumb.map((b: any, i: number) => b.kind === 'page' ? <span key={i} aria-current="page">{b.label}</span> : <a key={i} href={'#reader/browse?' + new URLSearchParams({ domain: b.kind === 'domain' ? b.target : b.target.split('|')[0], ...(b.kind === 'type' ? { type: b.label } : {}) })}>{b.kind === 'domain' ? label(b.target, b.label) : b.label}<span> / </span></a>)}</nav><h1>{page.title}</h1>
          <div className="reading-meta"><span>{page.type}</span><button onClick={() => openReference('source')}>{t('sourceCompare')}</button><a href={'#study/practice?page=' + encodeURIComponent(page.path)}>{t('freePractice')}</a><a href={page.obsidian_uri}>{t('openObsidian')} ↗</a></div>
          <div ref={body} data-page={page.path} className="knowledge-content" onMouseUp={() => { if (body.current) setSelection(selectedText(body.current)); }} onKeyUp={() => { if (body.current) setSelection(selectedText(body.current)); }}><Html html={page.html}/></div>
          {selection && <div className="selection-actions"><span>{selection.quote.slice(0, 35)}{selection.quote.length > 35 ? '…' : ''}</span><button disabled={!page.revision} onClick={() => openReference('notes')}>{t('addNote')}</button><button aria-label={t('close')} onClick={() => setSelection(undefined)}>×</button></div>}
          <div className="page-pagination">{[page.prev, page.next].map((p, i) => p ? <a key={i} href={'#/' + encodeURI(p)}><small>{t(i === 0 ? 'previous' : 'next')}</small><strong>{data.pages.find((v: any) => v.path === p)?.title}</strong></a> : <span key={i}/>)}</div>
        </> : <p>{t('loading')}</p>)}
      </article>
      <Splitter side="reference" value={layout.reference} onChange={value => clampWidth('reference', value)}/>
      <aside className={'reading-context ' + (drawer === 'context' ? 'open' : '')} aria-label={t('reference')}><div className="section-heading"><h3>{t('reference')}</h3><button aria-label={t('close')} className="icon" onClick={() => { setDrawer(''); layoutChange({ referenceHidden: true }); }}>×</button></div>
        <ReferencePanel page={view ? referencePage : page} previewPath={pinned} tab={tab} setTab={setTab} selection={selection} bodyRef={body} onHeading={id => { navigate('/' + encodeURI((view ? referencePage : page).path) + '?heading=' + encodeURIComponent(id)); setDrawer(''); }} onOpen={go} graph={graph} onPreview={openPreview}/>
      </aside>
      {hover && <div className="page-preview" role="dialog" aria-label={hover.title} style={{ left: hover.x, top: hover.y }} onMouseEnter={() => { clearTimeout(hoverClose.current); clearTimeout(hoverOpen.current); }} onMouseLeave={closeHover} onFocusCapture={() => clearTimeout(hoverClose.current)} onBlurCapture={closeHover}><strong>{hover.title}</strong><p>{hover.search_text.slice(0, 320)}</p><div className="button-row"><a href={'#/' + encodeURI(hover.path)}>{t('openPage')} ↗</a><button onClick={() => openPreview(hover.path)}>{t('pinReference')}</button></div></div>}
    </div>
  </div>;
}
