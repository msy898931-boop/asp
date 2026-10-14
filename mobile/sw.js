/* ASTRA service worker — offline shell, live data.
   Live endpoints (API, bridge, advisor) are network-only so readings are never
   stale. The shell is network-first with a cache fallback: a deploy is picked
   up on the next load instead of the load after next, and the app still opens
   offline. */

const SHELL = "astra-shell-v20";
const ASSETS = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./pricing.html",
  "./pricing.css",
  "./performance.html",
  "./performance.css",
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

/* ── notifications ────────────────────────────────────────────────────
   Two jobs, one handler. Android Chrome throws when a page calls
   `new Notification()` directly, so the page posts the message to this worker
   and the worker shows it — that path works everywhere the app runs. The same
   handler serves real Web Push payloads when the server sends one. */

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "notify") return;
  event.waitUntil(self.registration.showNotification(data.title || "ASTRA", {
    body: data.body || "",
    tag: data.tag || "astra",
    icon: "./icons/icon-192.png",
    badge: "./icons/icon-192.png",
    renotify: true,
    vibrate: [40, 60, 40],
  }));
});

self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (e) { /* plain text */ }
  const title = payload.title || "ASTRA";
  const body = payload.body || (event.data ? event.data.text() : "");
  event.waitUntil(self.registration.showNotification(title, {
    body,
    tag: payload.tag || "astra-push",
    icon: "./icons/icon-192.png",
    badge: "./icons/icon-192.png",
    vibrate: [40, 60, 40],
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) return client.focus();
      }
      return self.clients.openWindow("./");
    })
  );
});
