const CACHE_NAME = 'fundscope-v1';

const STATIC_ASSETS = [
  '/fundscope/',
  '/fundscope/index.html',
  '/fundscope/portfolio.html',
  '/fundscope/markets.html',
  '/fundscope/news.html',
  '/fundscope/earnings.html',
  '/fundscope/search.html',
  '/fundscope/stock.html',
  '/fundscope/watchlist.html',
  '/fundscope/manifest.json',
  '/fundscope/favicon.svg',
  '/fundscope/icon-192.png',
  '/fundscope/icon-512.png',
];

const DATA_URLS = [
  '/fundscope/portfolio.json',
  '/fundscope/markets.json',
  '/fundscope/earnings.json',
  '/fundscope/news.json',
  '/fundscope/data.json',
  '/fundscope/data/beta/beta_analysis.json',
  '/fundscope/data/beta/beta_summary.json',
  '/fundscope/data/beta/beta_trades.json',
  '/fundscope/logs/bonnie_log.json',
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

  // Only handle same-origin requests within /fundscope/
  if (url.origin !== location.origin || !url.pathname.startsWith('/fundscope')) return;

  const isDataRequest = DATA_URLS.some(d => url.pathname.startsWith(d.replace('/fundscope', '')));

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
    return cached ?? new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
}
