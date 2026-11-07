import { useContext, useEffect, useState } from 'react';
import { api } from './api.ts';
import { Language, useT, type Key } from './i18n.tsx';

export const guides: [string, Key][] = [['quick-start', 'quickStart'], ['models-and-quota', 'quota'], ['add-a-book', 'addBook'], ['read-and-obsidian', 'readingGuide'], ['troubleshooting', 'troubleshooting'], ['glossary', 'glossary'], ['privacy-and-safety', 'privacy']];
export function Help({ route }: { route: string }) {
  const t = useT(); const lang = useContext(Language); const [text, setText] = useState(''); const [query, setQuery] = useState('');
  const slug = route.split('/')[1] || 'quick-start';
  useEffect(() => { void api('/help/' + lang + '/' + slug).then(d => setText(d.text)).catch(e => setText(String(e))); }, [lang, slug]);
  let inCode = false;
  return <><div className="section-heading"><h2>{t('help')}</h2><input aria-label={t('search')} placeholder={t('search')} value={query} onChange={e => setQuery(e.target.value)}/></div><div className="help-layout"><nav>{guides.map(([name, label]) => <a className={slug === name ? 'selected' : ''} href={'#help/' + name} key={name}>{t(label)}</a>)}</nav><article className="guide">{text.split('\n').map((line, i) => {
    if (line.startsWith('```')) { inCode = !inCode; return null; }
    if (query && !line.toLocaleLowerCase().includes(query.toLocaleLowerCase())) return null;
    if (inCode) return <pre key={i}>{line || ' '}</pre>;
    if (line.startsWith('# ')) return <h1 key={i}>{line.slice(2)}</h1>;
    if (line.startsWith('## ')) return <h2 key={i} id={line.slice(3).toLowerCase().replace(/\s+/g, '-')}>{line.slice(3)}</h2>;
    if (line.startsWith('- ')) return <p className="guide-bullet" key={i}>• {line.slice(2)}</p>;
    return <p key={i}>{line}</p>;
  })}</article></div></>;
}
