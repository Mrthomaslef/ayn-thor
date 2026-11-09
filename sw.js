// Self-clearing Service Worker v9 — purges any old cached index.html
const CACHE_NAME = "ayn-thor-switch-dl-v9";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  // Always network-first so updates appear immediately
  event.respondWith(
    fetch(event.request, { cache: "no-store" }).catch(() => caches.match(event.request))
  );
});
