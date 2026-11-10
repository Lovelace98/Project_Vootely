const CACHE_NAME = 'vootely-cache-v2';

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
// IMPORTANT: Only handle GET requests!
// Bypassing non-GET requests (POST, PUT, DELETE, PATCH) is critical to prevent
// a known Safari/WebKit bug where event.respondWith(fetch(event.request))
// drops the POST request body / multipart formData stream, causing Django
// to receive an empty body and fail with "CSRF token missing."
self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') {
        return;
    }
    event.respondWith(fetch(event.request));
});
