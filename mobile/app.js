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
  svg.innerHTML = "";
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

  // Baseline grid.
  [0.25, 0.5, 0.75].forEach((f) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", pad); line.setAttribute("x2", W - pad);
    line.setAttribute("y1", pad + (H - 2 * pad) * f);
    line.setAttribute("y2", pad + (H - 2 * pad) * f);
    line.setAttribute("stroke", "#232B36");
    line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
  });

  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const x = pad + i * step + step / 2;
    const color = up ? "#4FB477" : "#D1584F";

    const wick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    wick.setAttribute("x1", x); wick.setAttribute("x2", x);
    wick.setAttribute("y1", y(b.high)); wick.setAttribute("y2", y(b.low));
    wick.setAttribute("stroke", color);
    wick.setAttribute("stroke-width", "1");
    svg.appendChild(wick);

    const top = Math.min(y(b.open), y(b.close));
    const h = Math.max(1, Math.abs(y(b.open) - y(b.close)));
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x - bodyW / 2);
    rect.setAttribute("y", top);
    rect.setAttribute("width", bodyW);
    rect.setAttribute("height", h);
    rect.setAttribute("fill", color);
    svg.appendChild(rect);
  });

  const newest = bars[bars.length - 1];
  $("chart-meta").textContent =
    `${payload.timeframe || activeTf} · ${bars.length} closed bars · `
    + `last ${num(newest.close)} · ${payload.freshness || "?"}`;
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
  buildTimeframes();
  bindTabs();
  $("refresh").addEventListener("click", refresh);
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
