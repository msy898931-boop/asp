/* ASTRA service worker — offline shell, live data.
   Live endpoints (API, bridge, advisor) are network-only so readings are never
   stale. The shell is network-first with a cache fallback: a deploy is picked
   up on the next load instead of the load after next, and the app still opens
   offline. The app keeps the *last reading* itself (localStorage), so offline
   it opens to that reading rather than an empty panel. */

const SHELL = "astra-shell-v36";
const ASSETS = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./i18n.js",
  "./panels.js",
  "./advisor.js",
  "./tracker.js",
  "./alerts.js",
  "./vol.js",
  "./akoan.js",
  "./ticker.js",
  "./terminal.html",
  "./terminal.css",
  "./terminal.js",
  "./pricing.html",
  "./pricing.css",
  "./performance.html",
  "./performance.css",
  "./manifest.webmanifest",
  "./manifest-light.webmanifest",
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

  // The push capability reading must be fresh, never cached: a stale one would
  // tell the app there is no VAPID key after the operator has set one.
  if (url.pathname.endsWith("/push.json")) return;

  // The APK download must reach the network untouched: serving a cached copy
  // would mask a fresh build, and a binary has no business in the shell cache.
  if (url.pathname.endsWith(".apk")) return;

  // Never touch the gate's handshake. /unlock?k=KEY carries the access key in
  // its URL; caching the request would persist that key in the device's cache
  // storage, and serving it from cache later would skip the cookie exchange.
  if (url.pathname.endsWith("/unlock")) return;

  event.respondWith(
    fetch(event.request)
      .then((res) => {
        // Cache only successful shell responses. Caching a 401 would serve the
        // gate's "access requires a key" page as the app shell on the next
        // offline load, which reads as a broken app rather than a locked one.
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(event.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(event.request).then((hit) => {
        if (hit) return hit;
        // A navigation for a path we never cached (a deep link, say) must still
        // land on the app shell rather than the browser's offline page.
        if (event.request.mode === "navigate") return caches.match("./index.html");
        return undefined;
      }))
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
