const SW_VERSION = "bbs-pwa-v3";
const STATIC_CACHE = `${SW_VERSION}-static`;
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;
const OFFLINE_URL = "/offline";
const PRECACHE_URLS = [
    "/",
    OFFLINE_URL,
    "/static/style.css",
    "/static/script.js",
    "/manifest.webmanifest",
    "/static/icons/pwa-icon-192.png",
    "/static/icons/pwa-icon-512.png",
    "/static/icons/apple-touch-icon-180.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(STATIC_CACHE);
        await cache.addAll(PRECACHE_URLS);
        await self.skipWaiting();
    })());
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const cacheNames = await caches.keys();
        await Promise.all(
            cacheNames
                .filter((cacheName) => cacheName !== STATIC_CACHE && cacheName !== RUNTIME_CACHE)
                .map((cacheName) => caches.delete(cacheName))
        );
        await self.clients.claim();
    })());
});

self.addEventListener("message", (event) => {
    if (event.data && event.data.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
});

async function cacheSuccessResponse(cacheName, request, response) {
    if (!response || response.status !== 200 || response.type === "error") {
        return response;
    }

    const cache = await caches.open(cacheName);
    await cache.put(request, response.clone());
    return response;
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(STATIC_CACHE);
    const cachedResponse = await cache.match(request);

    const networkPromise = fetch(request)
        .then((response) => cacheSuccessResponse(STATIC_CACHE, request, response))
        .catch(() => null);

    if (cachedResponse) {
        return cachedResponse;
    }

    const networkResponse = await networkPromise;
    return networkResponse || Response.error();
}

async function networkFirst(request) {
    try {
        const networkResponse = await fetch(request);
        return cacheSuccessResponse(RUNTIME_CACHE, request, networkResponse);
    } catch (error) {
        const cache = await caches.open(RUNTIME_CACHE);
        const cachedResponse = await cache.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }
        return caches.match(OFFLINE_URL);
    }
}

async function navigationHandler(request) {
    try {
        const networkResponse = await fetch(request);
        await cacheSuccessResponse(RUNTIME_CACHE, request, networkResponse);
        return networkResponse;
    } catch (error) {
        const cachedPage = await caches.match(request);
        if (cachedPage) {
            return cachedPage;
        }

        const homeFromCache = await caches.match("/");
        if (homeFromCache) {
            return homeFromCache;
        }

        const offlinePage = await caches.match(OFFLINE_URL);
        return offlinePage || Response.error();
    }
}

function offlineJsonResponse(message) {
    return new Response(JSON.stringify({ success: false, message }), {
        status: 503,
        headers: { "Content-Type": "application/json" }
    });
}

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }

    if (request.mode === "navigate") {
        event.respondWith(navigationHandler(request));
        return;
    }

    if (url.pathname.startsWith("/api/")) {
        event.respondWith(
            fetch(request).catch(() => offlineJsonResponse("You are offline right now."))
        );
        return;
    }

    if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
        event.respondWith(staleWhileRevalidate(request));
        return;
    }

    event.respondWith(networkFirst(request));
});
