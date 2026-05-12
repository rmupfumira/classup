/**
 * ClassUp Service Worker
 *
 * Strategies:
 * - **Navigation requests** (HTML pages): network-first with cache fallback,
 *   then offline page if both fail. Keeps SSR pages fresh while offering an
 *   offline experience.
 * - **Static assets** (/static/css, /static/js): stale-while-revalidate so the
 *   UI loads instantly from cache and updates in the background.
 * - **Icons / images** under /static/img: cache-first (they rarely change),
 *   evicted via cache version bump.
 * - **API requests** (/api/v1/*): always network-first; never cache GETs of
 *   tenant-scoped data so users don't see stale data after another user
 *   changes it. Only the offline fallback HTML response is shown.
 *
 * Bump CACHE_VERSION below to force all clients to drop the old cache and
 * refetch — do this on every meaningful UI/static asset change.
 */

const CACHE_VERSION = 'v3';
const PRECACHE = `classup-precache-${CACHE_VERSION}`;
const RUNTIME = `classup-runtime-${CACHE_VERSION}`;

// Files we want immediately available offline. The login page is precached so
// expired sessions show a meaningful page rather than a blank screen.
const PRECACHE_URLS = [
  '/offline',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/img/icons/icon-192.png',
  '/static/img/icons/icon-512.png',
  '/static/img/icons/apple-touch-icon-180.png',
  '/static/manifest.webmanifest',
];

// ============================================================================
// Install — precache the shell
// ============================================================================
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => {
      // addAll is atomic — if any one fails, the install fails and we keep the
      // old SW. Use individual adds with catch so a missing file doesn't block.
      return Promise.all(
        PRECACHE_URLS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn('[SW] Precache miss:', url, err);
          })
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// ============================================================================
// Activate — clean up old caches
// ============================================================================
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== PRECACHE && k !== RUNTIME)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ============================================================================
// Fetch routing
// ============================================================================
self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Only handle GETs — POST/PUT/DELETE always hit the network and never cache
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Skip cross-origin requests (CDN, fonts, analytics, etc.) — let the browser
  // handle them normally
  if (url.origin !== self.location.origin) return;

  // Skip Server-Sent Events / WebSocket upgrades
  if (request.headers.get('accept')?.includes('text/event-stream')) return;

  // Skip authenticated API requests entirely — never cache user data
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkOnlyWithJsonOffline(request));
    return;
  }

  // Navigation (HTML pages)
  if (request.mode === 'navigate' || (request.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  // Static images / icons → cache-first
  if (url.pathname.startsWith('/static/img/')) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Other static assets (css, js) → stale-while-revalidate
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // Default: network with cache fallback
  event.respondWith(networkFirst(request));
});

// ============================================================================
// Strategies
// ============================================================================

async function networkFirstNavigation(request) {
  try {
    const response = await fetch(request);
    // Cache successful HTML responses so we can serve them when offline
    if (response && response.ok && response.type === 'basic') {
      const cache = await caches.open(RUNTIME);
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (err) {
    const cache = await caches.open(RUNTIME);
    const cached = await cache.match(request);
    if (cached) return cached;
    // Last resort — the offline page
    const offline = await caches.match('/offline');
    if (offline) return offline;
    return new Response('Offline', {
      status: 503,
      headers: { 'Content-Type': 'text/plain' },
    });
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(RUNTIME);
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function networkOnlyWithJsonOffline(request) {
  try {
    return await fetch(request);
  } catch (err) {
    return new Response(
      JSON.stringify({
        status: 'error',
        message: 'You appear to be offline. Please check your connection and retry.',
        offline: true,
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(RUNTIME);
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (err) {
    return new Response('', { status: 504 });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(RUNTIME);
  const cached = await cache.match(request);
  const networkPromise = fetch(request)
    .then((response) => {
      if (response && response.ok) {
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })
    .catch(() => null);
  return cached || (await networkPromise) || new Response('', { status: 504 });
}

// ============================================================================
// Messaging — let the page tell us to skip waiting (used by "new version" UI)
// ============================================================================
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
