import { memo, useEffect, useRef, useState } from 'react';
import { api } from './api.ts';
import renderMath from 'katex/contrib/auto-render';
import 'katex/dist/katex.min.css';

export const textHtml = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
export const Html = memo(function Html({ html, inline = false }: { html: string; inline?: boolean }) {
  const element = useRef<HTMLElement | null>(null), Tag = inline ? 'span' : 'div';
  useEffect(() => { if (element.current) renderMath(element.current, { delimiters: [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }], throwOnError: false, trust: false }); }, [html]);
  return <Tag className={inline ? 'math-text' : 'page-body'} ref={node => { element.current = node; }} dangerouslySetInnerHTML={{ __html: html }}/>;
});

export function RenderedText({ text }: { text: string }) {
  const [html, setHtml] = useState('');
  useEffect(() => { let live = true; void api('/reader/render', { text }).then(result => { if (live) setHtml(result.html); }).catch(() => { if (live) setHtml('<pre>' + textHtml(text) + '</pre>'); }); return () => { live = false; }; }, [text]);
  return html ? <Html html={html}/> : <p>{text}</p>;
}
