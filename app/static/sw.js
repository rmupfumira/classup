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

const CACHE_VERSION = 'v6';
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
  if (event.data === 'SKIP_WAITING' || (event.data && event.data.type === 'SKIP_WAITING')) {
    self.skipWaiting();
  }
});

// ============================================================================
// Web Push
//
// Servers send an encrypted payload via the user's push service (Apple/FCM/
// Mozilla). The browser wakes the service worker and fires `push` even when
// the app isn't open. We MUST call showNotification() inside the event;
// silent push (where the SW does work without a visible notification) is
// allowed on Chrome but iOS Safari penalises apps that do it and will
// eventually revoke the push permission. So we always show something.
//
// Payload contract (what the server sends as JSON):
// {
//   "title":  string  (required-ish — falls back to "ClassUp")
//   "body":   string  (the main message)
//   "url":    string  (where notificationclick should navigate)
//   "tag":    string  (collapse-key — same tag replaces previous notification)
//   "icon":   string  (URL; defaults to /static/img/icons/icon-192.png)
// }
// ============================================================================
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    // Server sent text/plain — wrap as a body so we still show something
    try { data = { title: 'ClassUp', body: event.data ? event.data.text() : '' }; }
    catch (_) { data = {}; }
  }

  const title = data.title || 'ClassUp';
  const options = {
    body: data.body || '',
    icon: data.icon || '/static/img/icons/icon-192.png',
    badge: '/static/img/icons/icon-192.png',
    tag: data.tag || undefined,
    // renotify=true makes a same-tag replacement still vibrate/sound; without
    // it, replacements are silent (good for "unread count goes from 1 → 2")
    renotify: !!data.tag,
    data: { url: data.url || '/' },
    // requireInteraction keeps the notification on screen until tapped on
    // Chrome desktop; iOS ignores this. Leave it false so the user isn't
    // forced to interact with every routine message.
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Clicking a notification — focus an existing window if it's already on the
// target URL, otherwise open a new one. Always close the notification first.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        try {
          // Match by pathname so query strings don't prevent reuse
          const clientPath = new URL(client.url).pathname;
          const targetPath = new URL(targetUrl, self.location.origin).pathname;
          if (clientPath === targetPath && 'focus' in client) {
            return client.focus();
          }
        } catch (e) { /* malformed URL — fall through to openWindow */ }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});

// Push service may revoke a subscription (expired auth, user reset, etc.).
// `pushsubscriptionchange` fires when that happens; we'd re-subscribe + tell
// the server. For now just log — the next regular subscribe call (from the
// notifications settings page) will refresh the row.
self.addEventListener('pushsubscriptionchange', (event) => {
  // Best-effort: re-subscribe using the old applicationServerKey and POST
  // the new endpoint to the server. The page-level fallback handles this
  // better since it knows the auth context.
  console.warn('[SW] pushsubscriptionchange — page will refresh subscription on next visit');
});
