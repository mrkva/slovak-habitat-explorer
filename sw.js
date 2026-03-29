var CACHE_SHELL = 'habitat-shell-v1';
var CACHE_TILES = 'habitat-tiles-v1';
var CACHE_DATA = 'habitat-data-v1';

var SHELL_FILES = [
    './',
    './index.html',
    './manifest.json',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
];

self.addEventListener('install', function(e) {
    e.waitUntil(
        caches.open(CACHE_SHELL).then(function(cache) {
            return cache.addAll(SHELL_FILES);
        }).then(function() {
            return self.skipWaiting();
        })
    );
});

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

self.addEventListener('fetch', function(e) {
    var url = e.request.url;
    if (e.request.method !== 'GET') return;

    // Identify API: network-first, cache for offline
    if (url.indexOf('/identify?') !== -1 || (url.indexOf('corsproxy.io') !== -1 && url.indexOf('identify') !== -1)) {
        var cacheUrl = normalizeIdentifyUrl(url);
        var cacheReq = new Request(cacheUrl);
        e.respondWith(
            caches.open(CACHE_DATA).then(function(cache) {
                return fetch(e.request).then(function(response) {
                    if (response.ok) cache.put(cacheReq, response.clone());
                    return response;
                }).catch(function() {
                    return cache.match(cacheReq).then(function(cached) {
                        return cached || new Response('{"results":[]}', {
                            headers: { 'Content-Type': 'application/json' }
                        });
                    });
                });
            })
        );
        return;
    }

    if (url.indexOf('corsproxy.io') !== -1) return;

    // Tiles: cache-first, network fallback
    if (isTileRequest(url)) {
        e.respondWith(
            caches.open(CACHE_TILES).then(function(cache) {
                return cache.match(e.request).then(function(cached) {
                    if (cached) return cached;
                    return fetch(e.request).then(function(response) {
                        if (response.ok) cache.put(e.request, response.clone());
                        return response;
                    }).catch(function() {
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

    // HTML/JSON: network-first (so updates arrive)
    var isHtml = url.indexOf('.html') !== -1 || url.endsWith('/') || url.indexOf('.json') !== -1;
    if (isHtml) {
        e.respondWith(
            fetch(e.request).then(function(response) {
                if (response.ok) {
                    caches.open(CACHE_SHELL).then(function(c) { c.put(e.request, response.clone()); });
                }
                return response.clone();
            }).catch(function() {
                return caches.match(e.request);
            })
        );
    } else {
        // JS/CSS: cache-first
        e.respondWith(
            caches.match(e.request).then(function(cached) {
                return cached || fetch(e.request);
            })
        );
    }
});

function normalizeIdentifyUrl(url) {
    // Strip viewport-dependent params so cached data matches regardless of pan/zoom
    var n = url.replace(/[&?]mapExtent=[^&]*/g, '').replace(/[&?]imageDisplay=[^&]*/g, '');
    // Snap geometry to 0.01-degree grid so save grid and tap coords share cache keys
    return n.replace(/geometry=([^&]+)/, function(m, coords) {
        var p = coords.split(',');
        if (p.length === 2) {
            var lng = (Math.round(parseFloat(p[0]) * 100) / 100).toFixed(2);
            var lat = (Math.round(parseFloat(p[1]) * 100) / 100).toFixed(2);
            return 'geometry=' + lng + ',' + lat;
        }
        return m;
    });
}

function isTileRequest(url) {
    return url.indexOf('tile.openstreetmap.org') !== -1
        || url.indexOf('arcgisonline.com') !== -1
        || url.indexOf('WMSServer') !== -1
        || url.indexOf('WmsServer') !== -1
        || url.indexOf('ags.geology.sk') !== -1;
}
