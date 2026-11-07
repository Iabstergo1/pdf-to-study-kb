import { useEffect, useId, useRef, type ReactNode } from 'react';

const stack: string[] = [];
let previousOverflow = '';
export function Dialog({ children, label, onClose, drawer = false, className = '', busy = false }: {
  children: ReactNode; label: string; onClose: () => void; drawer?: boolean; className?: string; busy?: boolean;
}) {
  const element = useRef<HTMLElement>(null), id = useId(), close = useRef(onClose), blocked = useRef(busy);
  close.current = onClose; blocked.current = busy;
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement;
    if (!stack.length) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    stack.push(id);
    const focusable = () => [...element.current!.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(node => node.getClientRects().length);
    (focusable()[0] || element.current)?.focus();
    const key = (event: KeyboardEvent) => {
      if (stack.at(-1) !== id) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); if (!blocked.current) close.current(); }
      if (event.key === 'Tab') {
        const nodes = focusable(), first = nodes[0], last = nodes.at(-1);
        if (!first) { event.preventDefault(); element.current?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || document.activeElement === element.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener('keydown', key, true);
    return () => { stack.splice(stack.indexOf(id), 1); window.removeEventListener('keydown', key, true); if (!stack.length) document.body.style.overflow = previousOverflow; if (previousFocus?.isConnected) previousFocus.focus(); };
  }, [id]);
  return <div className={drawer ? 'drawer-backdrop' : 'modal-backdrop'} onClick={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <section ref={element} tabIndex={-1} role="dialog" aria-modal="true" aria-label={label} className={(drawer ? 'log-drawer' : 'modal') + ' ' + className}>{children}</section>
  </div>;
}
