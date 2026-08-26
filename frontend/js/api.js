/* Backend client.

   Every GET falls back to the last successful response for that URL, kept in
   the Cache API. Offline the app then shows real numbers with a stale marker
   instead of empty screens — which matters because the wallet screen is the
   one you open on a train.
*/

import { getSettings } from './store.js';

const CACHE = 'tracker-api-v1';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function base() {
  return (getSettings().apiUrl || '').replace(/\/+$/, '');
}

function headers() {
  const { token } = getSettings();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function readCache(url) {
  if (!('caches' in window)) return null;
  try {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(url);
    if (!hit) return null;
    return {
      data: await hit.json(),
      stale: true,
      cachedAt: Number(hit.headers.get('x-cached-at')) || null,
    };
  } catch {
    return null;
  }
}

async function writeCache(url, data) {
  if (!('caches' in window)) return;
  try {
    const cache = await caches.open(CACHE);
    await cache.put(
      url,
      new Response(JSON.stringify(data), {
        headers: {
          'Content-Type': 'application/json',
          'x-cached-at': String(Date.now()),
        },
      }),
    );
  } catch {
    /* a full or unavailable cache must never break a working request */
  }
}

/* Raw backend errors are developer language. "Unauthorized" tells you
   nothing about what to do; a missing token and a wrong one look identical
   on screen and both are fixed in the same place. */
async function describe(response) {
  if (response.status === 401 || response.status === 403) {
    return getSettings().token
      ? 'Token is wrong — check it under Setup.'
      : 'No token configured — enter it under Setup.';
  }
  if (response.status === 404) return 'Endpoint not found.';
  const detail = await response.text().catch(() => '');
  return detail.slice(0, 200) || response.statusText || `Error ${response.status}`;
}

async function get(path, { signal } = {}) {
  const url = `${base()}${path}`;
  try {
    const response = await fetch(url, { headers: headers(), signal });
    if (!response.ok) {
      throw new ApiError(await describe(response), response.status);
    }
    const data = await response.json();
    await writeCache(url, data);
    return { data, stale: false, cachedAt: Date.now() };
  } catch (error) {
    // An auth failure must never be papered over with cached data: the whole
    // point is that you find out the token is wrong.
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      throw error;
    }
    const cached = await readCache(url);
    if (cached) return cached;
    if (error instanceof ApiError) throw error;
    throw new ApiError('Backend unreachable', 0);
  }
}

async function post(path) {
  const response = await fetch(`${base()}${path}`, { method: 'POST', headers: headers() });
  if (!response.ok) {
    throw new ApiError(await describe(response), response.status);
  }
  return response.json();
}

/* --- endpoints --------------------------------------------------------- */

export const api = {
  health: () => get('/api/health'),

  summary: (wallet) => get(`/api/wallet/${wallet}/summary`),
  positions: (wallet) => get(`/api/wallet/${wallet}/positions`),
  chart: (wallet, days = 30) => get(`/api/wallet/${wallet}/chart?days=${days}`),
  trades: (wallet, { limit = 25, offset = 0 } = {}) =>
    get(`/api/wallet/${wallet}/trades?limit=${limit}&offset=${offset}`),
  sync: (wallet) => post(`/api/wallet/${wallet}/sync`),

  coins: (params) => get(`/api/coins?${new URLSearchParams(params)}`),
  refreshCoins: () => post('/api/coins/refresh'),

  exportUrl: (wallet, kind) => `${base()}/api/wallet/${wallet}/export/${kind}.csv`,
};

/* Export links have to carry the token, and an <a download> cannot set a
   header. Fetching the CSV and handing over a blob keeps the token out of
   the URL and out of any proxy log. */
export async function downloadCsv(wallet, kind, filename) {
  const response = await fetch(api.exportUrl(wallet, kind), { headers: headers() });
  if (!response.ok) throw new ApiError('Export failed', response.status);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
