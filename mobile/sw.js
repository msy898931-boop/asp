/* ASTRA service worker — offline shell, live data.
   Live endpoints (API, bridge, advisor) are network-only so readings are never
   stale. The shell is network-first with a cache fallback: a deploy is picked
   up on the next load instead of the load after next, and the app still opens
   offline. */

const SHELL = "astra-shell-v16";
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
  const live = url.pathname.includes("/api/") ||
               url.pathname.includes("/bridge/") ||
               url.pathname.includes("/tracker/") ||
               url.pathname.includes("/advisor/");
  if (live || event.request.method !== "GET") return; // network only

  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(event.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
