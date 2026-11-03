const CACHE = 'kufibot-shell-v17';
const SHELL = ['/', '/setup', '/assets/style.css', '/assets/mobile-control.css', '/assets/app.js', '/assets/connection.js', '/assets/mobile-control.js', '/assets/device-settings.js', '/assets/network-handoff.js', '/assets/performance.js', '/assets/icon.svg', '/assets/pwa.js', '/assets/icon-180.png', '/assets/icon-192.png', '/assets/icon-512.png', '/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('kufibot-shell-') && key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== location.origin || !SHELL.includes(url.pathname)) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok) {
      const copy = response.clone();
      event.waitUntil(caches.open(CACHE).then(cache => cache.put(request, copy)));
    }
    return response;
  }).catch(() => caches.match(request)));
});
