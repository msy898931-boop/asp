/* ASTRA service worker — offline shell, live data.
   The app shell is cached so it opens instantly (and offline); API and bridge
   calls always go to the network so readings are never stale. */

const SHELL = "astra-shell-v7";
const ASSETS = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const live = url.pathname.includes("/api/") || url.pathname.includes("/bridge/");
  if (live || event.request.method !== "GET") return; // network only

  event.respondWith(
    caches.match(event.request).then((hit) =>
      hit || fetch(event.request).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(event.request, copy)).catch(() => {});
        return res;
      })
    )
  );
});
