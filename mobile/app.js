/* ASTRA mobile client.
   Reads the backend's loopback API through the app's own origin (the launcher
   reverse-proxies /api/v1 and /bridge to the backend). No secrets live here.

   Bilingual (EN/AR, RTL-aware). Honest by design: the "market state" panel is
   descriptive, and the analytics carry a live edge badge so the app never
   implies a predictive ability it does not have.

   This file is the first of the ordered classic scripts the page loads (see
   the <script> list in index.html and the ASSETS list in sw.js). The panel,
   advisor, tracker, alert, volatility, news and ticker scripts follow it and
   share this one global scope. */

"use strict";

const API = "api/v1/";
const BRIDGE = "bridge/";
const REFRESH_MS = 15000;
const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

// No panel may hang forever. The default is generous because the akoan study
// legitimately takes ~100 s on a cold server; the News tab passes its own,
// larger budget. Aborting turns a silent spinner into an honest error line.
const FETCH_TIMEOUT_MS = 20000;
const AKOAN_TIMEOUT_MS = 150000;

/* ── helpers ────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const num = (v, d = 2) =>
  v === null || v === undefined || v === "" ? "--" : Number(v).toFixed(d);
const yn = (v) => (v ? T("yes") : T("no"));
const mapI18n = (m, k) => (m[lang] || m.en)[k] || k;

// A panel that is fetching gets a shimmer (see .panel.loading in styles.css) so
// a slow read looks like work, not a frozen screen. Every caller pairs the two.
function setPanelLoading(view, on) {
  const el = $("view-" + view);
  if (el) el.classList.toggle("loading", !!on);
}

let activeTf = "M15";
let alertsOn = false;
let swReg = null;   // the service worker registration, used to show notifications
let akoanData;      // last akoan reading, so a language switch can re-render it
let lastSignalKey = null;
let lastPrice = null;
let priceLevels = []; // { value, crossed }
let refreshing = false;   // an in-flight live poll; stops slow polls from stacking
let currentView = "status"; // the visible tab, so polling only fetches what is shown
const LEVELS_KEY = "astra-levels";
const connStats = {}; // path -> { ok, ms }

// The last successful live read, so a phone with no network still opens to the
// most recent reading instead of a blank panel. Only live data is cached (not
// the akoan study, which is heavy and re-rendered from its own state).
const CACHE_KEY = "astra-last-read";
const CACHE_VERSION = 1;
let offlineMode = false;  // true while the app is showing the cached reading

// A cold start shows placeholders, not a wall of "--". Cleared the first time a
// real reading (live or cached) paints.
function setSkeleton(on) {
  const d = document.documentElement;
  if (on) {
    d.setAttribute("data-skeleton", "true");
    d.setAttribute("aria-busy", "true");
  } else {
    d.removeAttribute("data-skeleton");
    d.removeAttribute("aria-busy");
  }
}

/* ── last-reading cache ─────────────────────────────────────────────────
   Written on every successful live poll and read back only when the network
   fails, so the app degrades to "here is the last thing I knew" rather than an
   empty screen. It is never used to fake freshness: the banner says "offline",
   and the reading's own age is shown next to the gauge. */

function saveLastRead(snap) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      v: CACHE_VERSION, at: Date.now(), snap,
    }));
  } catch (e) { /* private mode, or quota — caching is a bonus, not a need */ }
}

function loadLastRead() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== CACHE_VERSION || !parsed.snap) return null;
    return parsed;
  } catch (e) {
    return null;
  }
}

// "3m ago" / "2h ago" / "4d ago" in the active language.
function agoText(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return lang === "ar" ? `${s} ث` : `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return lang === "ar" ? `${m} د` : `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return lang === "ar" ? `${h} س` : `${h}h ago`;
  const d = Math.round(h / 24);
  return lang === "ar" ? `${d} ي` : `${d}d ago`;
}

// Render a stored snapshot through the same paths a live one uses. When
// `offline` is true the panels are also labelled as a cached read; on a cold
// online start we paint the panels only and let the live poll relabel them.
function renderSnapshot(snap, offline) {
  if (!snap) return;
  renderStatus({
    health: snap.health || {}, system: snap.system || {}, bridge: snap.bridge || {},
  });
  renderSignal(snap.analysis || {});
  renderBridge(snap.bridge || {});
  if (snap.ticker) renderTicker(snap.ticker);
  setSkeleton(false);
  if (!offline) return;
  offlineMode = true;
  const at = snap.at;
  banner("offline", `${T("cached_offline")}${at ? " · " + agoText(at) : ""}`);
  $("health-sub").textContent = T("cached_offline_sub");
}

/* ── theme ──────────────────────────────────────────────────────────── */

const THEME_KEY = "astra-theme";
const LANG_KEY = "astra-lang";

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#F4F0E6" : "#0B0E13");
  // Keep the install colours in step with a mid-session switch, not just the
  // next cold load, so an install started now gets the right splash.
  const link = document.querySelector('link[rel="manifest"]');
  if (link) link.setAttribute("href", theme === "light" ? "manifest-light.webmanifest" : "manifest.webmanifest");
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
}

function initTheme() {
  let theme = null;
  try { theme = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
  if (!theme) {
    theme = window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  applyTheme(theme);
}

function applyLang() {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = T(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    el.setAttribute("aria-label", T(el.getAttribute("data-i18n-aria")));
  });
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* ignore */ }
  rerenderAkoan();
  // Switching language flips the bar's direction, which moves which side
  // overflows; recompute the fades once layout settles.
  requestAnimationFrame(updateTabFades);
}

function initLang() {
  let saved = null;
  try { saved = localStorage.getItem(LANG_KEY); } catch (e) { /* ignore */ }
  if (!saved) {
    saved = (navigator.language || "en").toLowerCase().startsWith("ar") ? "ar" : "en";
  }
  lang = saved;
  applyLang();
}

/* ── toast ──────────────────────────────────────────────────────────── */

let toastTimer = null;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => { el.hidden = true; }, 350);
  }, 4200);
}

async function getJson(url, timeoutMs = FETCH_TIMEOUT_MS) {
  const t0 = performance.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { cache: "no-store", signal: ctrl.signal });
    connStats[url] = { ok: res.ok, ms: Math.round(performance.now() - t0) };
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } catch (err) {
    if (!connStats[url]) connStats[url] = { ok: false, ms: Math.round(performance.now() - t0) };
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// Backend API envelope: { data: {...} }
async function api(path) {
  const body = await getJson(API + path);
  return body && typeof body.data === "object" ? body.data : {};
}

// Bridge envelope: { payload: {...} }
async function bridge(path) {
  const body = await getJson(BRIDGE + path);
  return body && typeof body.payload === "object" ? body.payload : {};
}

// Full bridge envelope, for callers that also need top-level fields (quality).
async function bridgeEnvelope(path) {
  return getJson(BRIDGE + path);
}

/* ── orchestration ──────────────────────────────────────────────────── */

async function refresh() {
  const btn = $("refresh");
  // A slow backend (a cold start, say) can outlast the 15s poll interval.
  // Without this guard the next poll starts on top of the last one, requests
  // pile up, and the phone keeps a stale answer coming. One at a time.
  if (refreshing) return;
  refreshing = true;
  btn.classList.add("busy");
  try {
    const [health, system, br, analysis] = await Promise.all([
      api("health"), api("system/state"), api("bridge/status"),
      api("analysis/latest"),
    ]);
    const aggregate = renderStatus({ health, system, bridge: br });
    renderSignal(analysis);
    renderBridge(br);
    maybeAlert(analysis);
    offlineMode = false;
    setSkeleton(false);
    banner(aggregate === "ONLINE" ? "online"
      : aggregate === "OFFLINE" ? "offline" : "degraded",
      `${aggregate.toLowerCase()} · ${br.broker || "no source"}`);
    // Remember this read so the next cold start with no network is not blank.
    saveLastRead({ health, system, bridge: br, analysis, at: Date.now() });
  } catch (err) {
    // The network failed. Fall back to the last reading if we have one, and
    // say plainly that it is the last reading rather than a fresh one.
    const cached = loadLastRead();
    if (cached) {
      renderSnapshot(cached.snap, true);
    } else {
      offlineMode = true;
      // Nothing cached and nothing live: stop pretending to load and show the
      // honest empty readout under the error banner.
      setSkeleton(false);
      banner("offline", "backend unreachable");
    }
  } finally {
    btn.classList.remove("busy");
    refreshing = false;
  }
  // Secondary panels cost the most data (the candle history is the biggest
  // payload) and only matter while they are on screen. Refresh them on their
  // own cadence, and only for the tab the user is actually looking at.
  loadTicker();
  if (currentView === "chart") loadChart();
  if (currentView === "vol") loadVolatility();
  renderConn();
}

function banner(kind, text) {
  const el = $("banner");
  el.className = "banner " + kind;
  $("banner-text").textContent = text;
}

function buildTimeframes() {
  const row = $("tf-row");
  row.innerHTML = "";
  TIMEFRAMES.forEach((tf) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = tf;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", tf === activeTf ? "true" : "false");
    b.addEventListener("click", () => {
      activeTf = tf;
      [...row.children].forEach((c) =>
        c.setAttribute("aria-selected", c === b ? "true" : "false"));
      loadChart();
      loadTicker();
    });
    row.appendChild(b);
  });
}

/* The bar is one row of six, so on a phone nothing is hidden and no edge fade
   is needed. The function stays (and clears the classes) because a wide screen
   in a landscape or desktop window can still make the row overflow; when it
   does, the fades return. */
function updateTabFades() {
  const el = document.querySelector(".tabs");
  if (!el) return;
  const max = el.scrollWidth - el.clientWidth;
  if (max <= 2) {
    el.classList.remove("more-left", "more-right");
    return;
  }
  const pos = Math.abs(el.scrollLeft);
  const rtl = getComputedStyle(el).direction === "rtl";
  const atStart = pos <= 2;
  const atEnd = pos >= max - 2;
  el.classList.toggle("more-left", rtl ? !atEnd : !atStart);
  el.classList.toggle("more-right", rtl ? !atStart : !atEnd);
}

// The secondary views live behind More so the bar is one row on a phone.
const SECONDARY_VIEWS = ["tracker", "ai", "engine", "conn", "bridge"];

function selectView(view) {
  const tabs = [...document.querySelectorAll(".tab")];
  const target = tabs.find((t) => t.dataset.view === view);
  tabs.forEach((t) =>
    t.setAttribute("aria-selected", t === target ? "true" : "false"));
  // More reads as selected while one of its views is open, so the bar still
  // says where you are once the sheet has closed.
  const more = document.querySelector(".tab-more");
  if (more) {
    more.setAttribute("aria-selected",
      SECONDARY_VIEWS.indexOf(view) !== -1 ? "true" : "false");
  }
  currentView = view;
  document.querySelectorAll(".panel").forEach((p) =>
    (p.hidden = p.id !== `view-${view}`));
  updateTabFades();
  if (view === "chart") loadChart();
  if (view === "vol") loadVolatility();
  if (view === "akoan") loadAkoan();
  if (view === "conn") renderConn();
  if (view === "tracker") loadTracker();
  if (view === "ai") loadAdvisor(false);
  if (view === "engine") loadEngine(false);
}

function setMoreOpen(open) {
  const sheet = document.querySelector("#tabs-more");
  const more = document.querySelector(".tab-more");
  if (!sheet || !more) return;
  sheet.hidden = !open;
  more.setAttribute("aria-expanded", open ? "true" : "false");
}

function bindTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      if (tab.classList.contains("tab-more")) {
        const sheet = document.querySelector("#tabs-more");
        setMoreOpen(sheet ? sheet.hidden : false);
        return;
      }
      setMoreOpen(false);
      selectView(tab.dataset.view);
    });
  });
  // A tap anywhere outside the sheet or the More button closes it.
  document.addEventListener("click", (e) => {
    const sheet = document.querySelector("#tabs-more");
    if (sheet && !sheet.hidden
        && !e.target.closest("#tabs-more") && !e.target.closest(".tab-more")) {
      setMoreOpen(false);
    }
  });
  const bar = document.querySelector(".tabs");
  if (bar) {
    bar.addEventListener("scroll", updateTabFades, { passive: true });
    updateTabFades();
  }
}

function init() {
  initLang();
  initTheme();
  loadLevels();
  renderLevels();
  buildTimeframes();
  bindTabs();
  bindJournalFilter();
  $("refresh").addEventListener("click", refresh);
  $("ai-refresh").addEventListener("click", () => loadAdvisor(true));
  $("eng-refresh").addEventListener("click", () => loadEngine(true));
  $("alerts").addEventListener("click", toggleAlerts);
  $("push-test").addEventListener("click", sendTestPush);
  $("level-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = parseFloat($("level-input").value);
    addLevel(v);
    $("level-input").value = "";
  });
  $("lang").addEventListener("click", () => {
    lang = lang === "ar" ? "en" : "ar";
    applyLang();
    $("lang").textContent = T("lang_toggle");
    refresh();
  });
  $("theme").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light"
      ? "dark" : "light";
    applyTheme(next);
  });
  $("lang").textContent = T("lang_toggle");
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  // The OS tells us the network went away or came back; use it to label the
  // banner immediately instead of waiting for the next poll to time out.
  window.addEventListener("online", () => refresh());
  window.addEventListener("offline", () => {
    offlineMode = true;
    banner("offline", T("cached_offline"));
  });
  window.addEventListener("resize", updateTabFades);
  window.addEventListener("orientationchange", () => setTimeout(updateTabFades, 150));

  // Paint the last reading first so a cold, offline start is never blank, then
  // let the live poll overwrite it the moment the backend answers. With no
  // cached reading at all, show skeletons until the first live answer lands.
  const cached = loadLastRead();
  if (cached) {
    renderSnapshot(cached.snap, false);
  } else {
    setSkeleton(true);
  }

  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js")
      .then((reg) => { swReg = reg; return restoreAlerts(); })
      .catch(() => {});
  }
}

document.addEventListener("DOMContentLoaded", init);

