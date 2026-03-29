var CACHE_SHELL = 'forest-shell-v3';
var CACHE_TILES = 'forest-tiles-v1';
var CACHE_DATA = 'forest-data-v1';

// App shell files to cache on install
var SHELL_FILES = [
    './',
    './index.html',
    './manifest.json',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
];

// Install: cache the app shell
self.addEventListener('install', function(e) {
    e.waitUntil(
        caches.open(CACHE_SHELL).then(function(cache) {
            return cache.addAll(SHELL_FILES);
        }).then(function() {
            return self.skipWaiting();
        })
    );
});

// Activate: clean old caches
self.addEventListener('activate', function(e) {
    e.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(keys.filter(function(k) {
                return k !== CACHE_SHELL && k !== CACHE_TILES && k !== CACHE_DATA;
            }).map(function(k) { return caches.delete(k); }));
        }).then(function() {
            return self.clients.claim();
        })
    );
});

// Fetch strategy:
// - Shell files: cache-first
// - Tile/WMS requests: cache-first, then network (and cache the response)
// - Identify/API requests: network-only (no point caching these)
self.addEventListener('fetch', function(e) {
    var url = e.request.url;

    // Skip non-GET
    if (e.request.method !== 'GET') return;

    // Identify/API requests: network-first, cache the response for offline
    if (url.indexOf('/identify?') !== -1 || (url.indexOf('corsproxy.io') !== -1 && url.indexOf('identify') !== -1)) {
        e.respondWith(
            caches.open(CACHE_DATA).then(function(cache) {
                return fetch(e.request).then(function(response) {
                    if (response.ok) {
                        cache.put(e.request, response.clone());
                    }
                    return response;
                }).catch(function() {
                    return cache.match(e.request).then(function(cached) {
                        return cached || new Response('{"results":[]}', {
                            headers: { 'Content-Type': 'application/json' }
                        });
                    });
                });
            })
        );
        return;
    }

    // Other corsproxy requests: network only
    if (url.indexOf('corsproxy.io') !== -1) return;

    // Tile requests (OSM, Esri, WMS): cache-first, fallback to network, cache result
    if (isTileRequest(url)) {
        e.respondWith(
            caches.open(CACHE_TILES).then(function(cache) {
                return cache.match(e.request).then(function(cached) {
                    if (cached) return cached;
                    return fetch(e.request).then(function(response) {
                        if (response.ok) {
                            cache.put(e.request, response.clone());
                        }
                        return response;
                    }).catch(function() {
                        // Offline and not cached — return a transparent 1x1 PNG
                        return new Response(
                            Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAA0lEQVQI12P4z8BQDwAEgAF/QualIQAAAABJRU5ErkJggg=='), function(c) { return c.charCodeAt(0); }),
                            { headers: { 'Content-Type': 'image/png' } }
                        );
                    });
                });
            })
        );
        return;
    }

    // App shell: cache-first
    e.respondWith(
        caches.match(e.request).then(function(cached) {
            return cached || fetch(e.request);
        })
    );
});

function isTileRequest(url) {
    return url.indexOf('tile.openstreetmap.org') !== -1
        || url.indexOf('arcgisonline.com') !== -1
        || url.indexOf('WMSServer') !== -1
        || url.indexOf('WmsServer') !== -1
        || url.indexOf('ags.geology.sk') !== -1;
}
