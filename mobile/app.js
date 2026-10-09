/* ASTRA mobile client.
   Reads the backend's loopback API through the app's own origin (the launcher
   reverse-proxies /api/v1 and /bridge to the backend). No secrets live here. */

"use strict";

const API = "api/v1/";
const BRIDGE = "bridge/";
const REFRESH_MS = 15000;
const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

const $ = (id) => document.getElementById(id);
const num = (v, d = 2) =>
  v === null || v === undefined || v === "" ? "--" : Number(v).toFixed(d);
const yn = (v) => (v ? "yes" : "no");

let activeTf = "M15";
let alertsOn = false;
let lastSignalKey = null;

/* ── theme ──────────────────────────────────────────────────────────── */

const THEME_KEY = "astra-theme";

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#F4F0E6" : "#0B0E13");
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

async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
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

/* ── status ─────────────────────────────────────────────────────────── */

const GAUGE_FRACTION = { ONLINE: 1, DEGRADED: 0.55, OFFLINE: 0.12, UNKNOWN: 0.3 };

function setGauge(aggregate) {
  const arc = $("gauge-arc");
  const needle = $("needle");
  const frac = GAUGE_FRACTION[aggregate] ?? 0.3;
  const len = arc.getTotalLength();
  arc.style.strokeDasharray = `${len}`;
  arc.style.strokeDashoffset = `${len * (1 - frac)}`;
  arc.style.stroke =
    aggregate === "ONLINE" ? "var(--up)"
    : aggregate === "OFFLINE" ? "var(--down)"
    : "var(--gold)";
  needle.style.transform = `rotate(${frac * 270 - 135}deg)`;
}

function gradeClass(value, good, bad) {
  if (value === good) return "ok";
  if (value === bad) return "bad";
  return "warn";
}

function renderStatus(snap) {
  const health = snap.health || {};
  const system = snap.system || {};
  const br = snap.bridge || {};

  const aggregate = health.aggregate || "UNKNOWN";
  $("health-value").textContent = aggregate;
  $("health-sub").textContent = health.decision_grade_data
    ? "decision-grade data"
    : "no decision-grade data";
  setGauge(aggregate);

  const mode = system.mode || "--";
  const startup = system.startup_stage || "--";
  $("r-mode").textContent = mode;
  $("r-mode").className = gradeClass(mode, "ONLINE", "DEGRADED");
  $("r-startup").textContent = startup;
  $("r-startup").className = startup === "READY" ? "ok" : "warn";
  $("r-decision").textContent = yn(health.decision_grade_data);
  $("r-decision").className = health.decision_grade_data ? "ok" : "warn";
  $("r-symbol").textContent = br.resolved_symbol || "(none)";
  $("r-source").textContent = br.broker || "(none)";
  $("r-error").textContent = br.last_error || "(none)";
  $("r-error").className = br.last_error ? "bad" : "ok";

  // Bridge panel (same snapshot, no extra request).
  $("b-process").textContent = br.bridge_process_state || "--";
  $("b-process").className = br.bridge_process_state === "ONLINE" ? "ok" : "warn";
  $("b-ready").textContent = yn(br.mt5_ready);
  $("b-ready").className = br.mt5_ready ? "ok" : "warn";
  $("b-symbol").textContent = br.resolved_symbol || "(none)";
  $("b-broker").textContent = br.broker || "(none)";
  $("b-server").textContent = br.server || "(none)";
  $("b-transport").textContent = br.transport || "(none)";

  const reasons = Array.isArray(health.degraded_reasons)
    ? health.degraded_reasons : [];
  const list = $("reasons");
  list.innerHTML = "";
  reasons.slice(0, 8).forEach((r) => {
    const li = document.createElement("li");
    li.textContent = r;
    list.appendChild(li);
  });

  return aggregate;
}

/* ── signal ─────────────────────────────────────────────────────────── */

function renderSignal(analysis) {
  const signal = analysis.signal || {};
  const context = analysis.context || {};
  const levels = analysis.levels || {};

  const dir = signal.direction || "NONE";
  const el = $("sig-direction");
  el.textContent = dir;
  el.className = "direction " +
    (dir === "UP" ? "up" : dir === "DOWN" ? "down" : "flat");
  $("sig-regime").textContent = context.regime || "UNKNOWN";

  $("l-entry").textContent = num(levels.entry);
  $("l-stop").textContent = num(levels.stop_loss);
  $("l-tp").textContent = num(levels.take_profit);
  $("l-rr").textContent = num(levels.reward_risk);

  const score = signal.score;
  const conf = signal.confidence_lo != null && signal.confidence_hi != null
    ? null : undefined;
  const scorePct = score == null ? 0 : Math.max(0, Math.min(100, score));
  $("b-score").style.setProperty("--fill", `${scorePct}%`);
  $("v-score").textContent = num(score, 1);
  $("b-conf").style.setProperty("--fill",
    `${signal.probability ? signal.probability * 100 : 0}%`);
  $("v-conf").textContent = signal.probability
    ? num(signal.probability * 100, 0) + "%" : "n/a";

  $("sig-context").textContent =
    `H4 bias ${context.h4_bias || "?"} · M15 trigger ${context.m15_trigger || "?"}`
    + ` · volatility ${context.volatility_state || "?"}`;
  $("sig-time").textContent = analysis.timestamp
    ? `as of ${analysis.timestamp}` : "no decision recorded yet";
  void conf;
}

/* ── chart ──────────────────────────────────────────────────────────── */

function renderChart(payload) {
  const bars = payload.bars || payload.candles || [];
  const svg = $("chart");
  const axis = $("axis");
  svg.innerHTML = "";
  axis.innerHTML = "";
  if (!bars.length) {
    $("chart-meta").textContent = "no closed bars available";
    return;
  }

  const W = 320, H = 160, pad = 8;
  let lo = Infinity, hi = -Infinity;
  bars.forEach((b) => { lo = Math.min(lo, b.low); hi = Math.max(hi, b.high); });
  const span = hi - lo || 1;
  const y = (p) => pad + (H - 2 * pad) * (1 - (p - lo) / span);
  const step = (W - 2 * pad) / bars.length;
  const bodyW = Math.max(1.5, step * 0.6);

  const ns = "http://www.w3.org/2000/svg";
  const add = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    svg.appendChild(el);
    return el;
  };

  // Gridlines + price labels at even fractions of the visible range.
  [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
    const gy = pad + (H - 2 * pad) * f;
    if (f > 0 && f < 1) {
      add("line", { x1: pad, x2: W - pad, y1: gy, y2: gy,
                    stroke: "var(--edge)", "stroke-width": 1 });
    }
    const label = document.createElement("span");
    label.textContent = (hi - span * f).toFixed(2);
    label.style.top = `${(gy / H) * 100}%`;
    axis.appendChild(label);
  });

  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const x = pad + i * step + step / 2;
    const color = up ? "var(--up)" : "var(--down)";
    add("line", { x1: x, x2: x, y1: y(b.high), y2: y(b.low),
                  stroke: color, "stroke-width": 1 });
    const top = Math.min(y(b.open), y(b.close));
    const h = Math.max(1, Math.abs(y(b.open) - y(b.close)));
    add("rect", { x: x - bodyW / 2, y: top, width: bodyW, height: h, fill: color });
  });

  // Current (newest close) price line, tied to its own axis label.
  const newest = bars[bars.length - 1];
  const cy = y(newest.close);
  add("line", { x1: pad, x2: W - pad, y1: cy, y2: cy, class: "price-line" });
  const last = document.createElement("span");
  last.textContent = num(newest.close);
  last.style.top = `${(cy / H) * 100}%`;
  last.style.color = "var(--gold)";
  last.style.fontWeight = "600";
  axis.appendChild(last);

  $("chart-meta").textContent =
    `${payload.timeframe || activeTf} · ${bars.length} closed bars · `
    + `range ${num(lo)}–${num(hi)} · ${payload.freshness || "?"}`;
}

/* ── alerts ─────────────────────────────────────────────────────────── */

function signalKey(analysis) {
  const s = analysis.signal || {};
  return `${s.direction || "NONE"}|${analysis.timestamp || ""}`;
}

function maybeAlert(analysis) {
  const key = signalKey(analysis);
  const changed = lastSignalKey !== null && key !== lastSignalKey;
  lastSignalKey = key;
  if (!changed || !alertsOn) return;

  const s = analysis.signal || {};
  const dir = s.direction || "NONE";
  const text = `ASTRA: signal changed to ${dir}`
    + (s.score != null ? ` (score ${Number(s.score).toFixed(1)})` : "");
  toast(text);

  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("ASTRA signal", { body: text, tag: "astra-signal" }); }
    catch (e) { /* notification constructor can throw on some browsers */ }
  }
  if (navigator.vibrate) navigator.vibrate([40, 60, 40]);
}

async function toggleAlerts() {
  const btn = $("alerts");
  if (alertsOn) {
    alertsOn = false;
    btn.setAttribute("aria-pressed", "false");
    toast("Signal alerts off");
    return;
  }
  alertsOn = true;
  btn.setAttribute("aria-pressed", "true");
  toast("Signal alerts on — you'll be told when the signal changes");
  if ("Notification" in window && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
  }
}

/* ── orchestration ──────────────────────────────────────────────────── */

async function refresh() {
  const btn = $("refresh");
  btn.classList.add("busy");
  try {
    const [health, system, br, analysis] = await Promise.all([
      api("health"), api("system/state"), api("bridge/status"),
      api("analysis/latest"),
    ]);
    const aggregate = renderStatus({ health, system, bridge: br });
    renderSignal(analysis);
    maybeAlert(analysis);
    banner(aggregate === "ONLINE" ? "online"
      : aggregate === "OFFLINE" ? "offline" : "degraded",
      `${aggregate.toLowerCase()} · ${br.broker || "no source"}`);
  } catch (err) {
    banner("offline", "backend unreachable");
  } finally {
    btn.classList.remove("busy");
  }
}

async function loadChart() {
  try {
    const payload = await bridge(`v1/candles?tf=${activeTf}&limit=120`);
    renderChart(payload);
  } catch (err) {
    $("chart-meta").textContent = "chart unavailable: " + err.message;
  }
}

function banner(kind, text) {
  const el = $("banner");
  el.className = "banner " + kind;
  $("banner-text").textContent = text;
}

function buildTimeframes() {
  const row = $("tf-row");
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
    });
    row.appendChild(b);
  });
}

function bindTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) =>
        t.setAttribute("aria-selected", t === tab ? "true" : "false"));
      const view = tab.dataset.view;
      document.querySelectorAll(".panel").forEach((p) =>
        (p.hidden = p.id !== `view-${view}`));
      if (view === "chart") loadChart();
    });
  });
}

function init() {
  initTheme();
  buildTimeframes();
  bindTabs();
  $("refresh").addEventListener("click", refresh);
  $("alerts").addEventListener("click", toggleAlerts);
  $("theme").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light"
      ? "dark" : "light";
    applyTheme(next);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

document.addEventListener("DOMContentLoaded", init);
