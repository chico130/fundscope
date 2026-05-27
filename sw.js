const CACHE_NAME = 'fundscope-v3';

const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/portfolio.html',
  '/markets.html',
  '/news.html',
  '/earnings.html',
  '/search.html',
  '/stock.html',
  '/watchlist.html',
  '/manifest.json',
  '/favicon.svg',
  '/icon-192.png',
  '/icon-512.png',
];

// Only cache public, non-authenticated data endpoints
const DATA_URLS = [
  '/markets.json',
  '/earnings.json',
  '/news.json',
  '/data.json',
];

// ── Install: pre-cache static shell ──────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

// ── Activate: purge old caches ────────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) return;

  // Never intercept API endpoints — they require auth and vary per request
  if (url.pathname.startsWith('/api/')) return;

  const isDataRequest = DATA_URLS.some(d => url.pathname === d || url.pathname.startsWith(d + '?'));

  if (isDataRequest) {
    // Network First, Cache Fallback (timeout 4 s)
    event.respondWith(networkFirstWithTimeout(event.request, 4000));
  } else {
    // Cache First, Network Fallback
    event.respondWith(cacheFirst(event.request));
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Offline — recurso não disponível.', { status: 503 });
  }
}

async function networkFirstWithTimeout(request, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(request, { signal: controller.signal });
    clearTimeout(timer);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    clearTimeout(timer);
    const cached = await caches.match(request);
    return cached ?? new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } });
  }
}
