const CACHE_NAME = 'vootely-cache-v1';

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        return caches.delete(cache);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// Fetch event listener: Required to satisfy PWA criteria.
// Uses a network-only/pass-through strategy to avoid caching dynamic Django
// template responses, login sessions, or checkout screens.
self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
