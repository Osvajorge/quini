const CACHE = 'quini-v2';
const STATIC = ['/', '/index.html'];
const DATA_URL = '/data/predictions.json';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Click on a notification → focus existing tab or open new
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(clients => {
      for (const c of clients) {
        if (c.url.includes(self.registration.scope) && 'focus' in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});

// Periodic background sync (Chrome only, requires user install + permission)
self.addEventListener('periodicsync', e => {
  if (e.tag === 'check-new-bets') {
    e.waitUntil(fetch('/data/predictions.json').catch(() => {}));
  }
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // predictions.json: network first, cache fallback (always fresh data)
  if (url.pathname.includes('predictions.json') || url.pathname.includes('history.json')) {
    e.respondWith(
      fetch(e.request)
        .then(r => { caches.open(CACHE).then(c => c.put(e.request, r.clone())); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Static assets: cache first
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(r => {
      if (r.ok && e.request.method === 'GET') {
        caches.open(CACHE).then(c => c.put(e.request, r.clone()));
      }
      return r;
    }))
  );
});
