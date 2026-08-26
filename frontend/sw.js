/* Service worker.

   Scope split:
   - App shell: cache-first, so the checklist opens instantly and works with
     no network at all.
   - Google Fonts: cache-first at runtime, so the typography survives offline
     after the first load.
   - /api/: never touched here. api.js does its own caching, because it needs
     to know whether a response was fresh or stale in order to say so on
     screen. A service worker handing back a cached body silently would make
     that impossible.
*/

// Bumping this drops the previous caches in `activate`. Needed once here to
// clear the stale shell left behind by the old cache-first worker.
const VERSION = 'v3';
const SHELL = `tracker-shell-${VERSION}`;
const FONTS = `tracker-fonts-${VERSION}`;

const ASSETS = [
  './',
  './index.html',
  './app.webmanifest',
  './css/app.css',
  './js/app.js',
  './js/api.js',
  './js/chart.js',
  './js/format.js',
  './js/store.js',
  './js/ui.js',
  './js/views/check.js',
  './js/views/coins.js',
  './js/views/setup.js',
  './js/views/wallet.js',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      // addAll is all-or-nothing; one 404 would leave the app with no cache
      // at all, so each asset is added on its own.
      .then((cache) => Promise.all(ASSETS.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('tracker-') && key !== SHELL && key !== FONTS)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function isFont(url) {
  return url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com';
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  if (url.pathname.startsWith('/api/')) return;

  if (isFont(url)) {
    event.respondWith(
      caches.open(FONTS).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        try {
          const response = await fetch(request);
          cache.put(request, response.clone());
          return response;
        } catch {
          return new Response('', { status: 504 });
        }
      }),
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  // Stale-while-revalidate. Plain cache-first opened instantly but pinned the
  // app to whatever was cached first: a deployed fix never reached an
  // installed PWA, because nothing ever asked the network again. Now the
  // cached copy is served immediately and refreshed in the background, so the
  // next launch runs the new version — and offline still works.
  event.respondWith(
    (async () => {
      const cache = await caches.open(SHELL);
      const hit = await cache.match(request);

      const refresh = fetch(request)
        .then((response) => {
          if (response.ok && response.type === 'basic') {
            cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => null);

      if (hit) return hit;

      const response = await refresh;
      if (response) return response;

      // A navigation that misses the cache still has to land somewhere.
      if (request.mode === 'navigate') {
        const shell = await cache.match('./index.html');
        if (shell) return shell;
      }
      return new Response('', { status: 504 });
    })(),
  );
});
