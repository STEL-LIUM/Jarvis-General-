const CACHE = 'jarvis-pwa-v1';
const PRECACHE = ['/', '/manifest.json'];

self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE).then(c => c.addAll(PRECACHE)).catch(() => {})
    );
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        ).then(() => clients.claim())
    );
});

self.addEventListener('fetch', e => {
    // Never intercept API calls — they need live network
    if (e.request.url.includes('/api/')) return;
    if (e.request.method !== 'GET') return;

    e.respondWith(
        caches.match(e.request).then(cached => {
            if (cached) return cached;
            return fetch(e.request).then(resp => {
                if (resp && resp.status === 200 && resp.type === 'basic') {
                    const clone = resp.clone();
                    caches.open(CACHE).then(c => c.put(e.request, clone));
                }
                return resp;
            });
        })
    );
});
