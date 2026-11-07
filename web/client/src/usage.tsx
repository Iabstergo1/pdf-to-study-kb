import { useState } from 'react';
import { useT } from './i18n.tsx';

export function usageTotals(jobs: any[]) {
  const result = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0, recorded: 0, incomplete: 0 };
  for (const job of jobs) {
    const value = job.progress?.tokens;
    if (!value) { if (job.command === 'ingest' || job.progress?.model) result.incomplete++; continue; }
    result.recorded++;
    for (const key of ['input', 'output', 'cacheRead', 'cacheWrite', 'total'] as const) result[key] += Number(value[key]) || 0;
    if (['failed', 'cancelled', 'interrupted', 'paused'].includes(job.status)) result.incomplete++;
  }
  return { ...result, hitRate: result.input + result.cacheRead > 0 ? result.cacheRead / (result.input + result.cacheRead) : null };
}
export function Usage({ job, jobs }: { job?: any; jobs?: any[] }) {
  const t = useT(), [all, setAll] = useState(false);
  const selected = all && jobs ? jobs : job ? [job] : [];
  const value = usageTotals(selected);
  return <section className="usage-panel" aria-label={t('tokens')}>
    <div className="section-heading"><strong>{t('tokens')}</strong>{jobs && <select aria-label={t('usageScope')} value={all ? 'all' : 'job'} onChange={e => setAll(e.target.value === 'all')}><option value="job">{t('thisExecution')}</option><option value="all">{t('sourceExecutions')}</option></select>}</div>
    <dl>{(['input', 'cacheRead', 'output', ...(value.cacheWrite ? ['cacheWrite'] : [])] as const).map(key => <div key={key}><dt>{t(key as 'input' | 'cacheRead' | 'output' | 'cacheWrite')}</dt><dd>{value.recorded ? value[key as 'input'].toLocaleString() : '—'}</dd></div>)}</dl>
    <p className="muted">{t('totalUsage')}: {value.recorded ? value.total.toLocaleString() : '—'}{value.hitRate !== null && <> · {t('cacheHitRate')}: {(value.hitRate * 100).toFixed(1)}%</>}</p>
    <small>{t('cacheRateHint')} {t('usageHint')}{value.incomplete > 0 && <> {t('usageIncomplete')}</>}</small><p className="muted">{t('noCostEstimate')}</p>
  </section>;
}
