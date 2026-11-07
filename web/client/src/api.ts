function acceptLaunchLink() {
  const match = location.hash.match(/^#token=([a-f0-9]+)$/);
  if (!match) return false;
  sessionStorage.setItem('study-kb-token', match[1]);
  history.replaceState(null, '', '/#dashboard');
  return true;
}
acceptLaunchLink();
window.addEventListener('hashchange', () => { if (acceptLaunchLink()) location.reload(); });
export async function api<T = any>(url: string, data?: unknown, method?: string): Promise<T> {
  const form = data instanceof FormData;
  const response = await fetch('/api' + url, {
    method: method || (data === undefined ? 'GET' : 'POST'),
    headers: { 'x-console-token': sessionStorage.getItem('study-kb-token') || '', ...(!form && data !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: data === undefined ? undefined : form ? data : JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}
export const navigate = (route: string) => { location.hash = route; };
