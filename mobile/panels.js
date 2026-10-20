/* ASTRA mobile client — the status, market, quality,
   connection and chart panels. */
"use strict";
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
    ? T("decision") : T("waiting");
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

function renderBridge(br) {
  $("b-process").textContent = br.bridge_process_state || "--";
  $("b-process").className = br.bridge_process_state === "ONLINE" ? "ok" : "warn";
  $("b-ready").textContent = yn(br.mt5_ready);
  $("b-ready").className = br.mt5_ready ? "ok" : "warn";
  $("b-symbol").textContent = br.resolved_symbol || "(none)";
  $("b-broker").textContent = br.broker || "(none)";
  $("b-server").textContent = br.server || "(none)";
  $("b-transport").textContent = br.transport || "(none)";
}

/* ── market state ───────────────────────────────────────────────────── */
const DIR_LABEL = {
  en: { UP: "UP", DOWN: "DOWN", NONE: "FLAT" },
  ar: { UP: "صعود", DOWN: "هبوط", NONE: "محايد" },
};

function renderSignal(analysis) {
  const signal = analysis.signal || {};
  const context = analysis.context || {};
  const levels = analysis.levels || {};

  const dir = signal.direction || "NONE";
  const el = $("sig-direction");
  el.textContent = (DIR_LABEL[lang] || DIR_LABEL.en)[dir] || dir;
  el.className = "direction " +
    (dir === "UP" ? "up" : dir === "DOWN" ? "down" : "flat");
  $("sig-regime").textContent = context.regime || "UNKNOWN";

  $("l-entry").textContent = num(levels.entry);
  $("l-stop").textContent = num(levels.stop_loss);
  $("l-tp").textContent = num(levels.take_profit);
  $("l-rr").textContent = num(levels.reward_risk);

  // The scaled ladder is only shown when the proposal actually carries one;
  // a single-target payload leaves the row hidden rather than guessing rungs.
  const ladder = Array.isArray(levels.targets)
    ? levels.targets.filter((t) => t != null) : [];
  const ladderRow = $("l-ladder-row");
  ladderRow.hidden = ladder.length <= 1;
  $("l-ladder").textContent = ladder.length > 1
    ? ladder.map((t) => num(t)).join(" ← ") : "--";

  const score = signal.score;
  const scorePct = score == null ? 0 : Math.max(0, Math.min(100, score));
  $("b-score").style.setProperty("--fill", `${scorePct}%`);
  $("v-score").textContent = num(score, 1);

  // Honest: a calibrated probability is only shown when the backend actually
  // produced one. Otherwise the bar stays empty and reads "n/a" — never a
  // number invented from an uncalibrated score.
  const hasProb = signal.probability != null && signal.probability_calibrated;
  $("b-conf").style.setProperty("--fill", hasProb ? `${signal.probability * 100}%` : "0%");
  $("v-conf").textContent = hasProb ? num(signal.probability * 100, 0) + "%" : "n/a";

  $("sig-context").textContent = fmt(T("context_txt"), {
    h4: context.h4_bias || "?",
    m15: context.m15_trigger || "?",
    vol: context.volatility_state || "?",
  });
  $("sig-time").textContent = analysis.timestamp
    ? analysis.timestamp : T("no_decision");
}

/* ── data quality ───────────────────────────────────────────────────── */

function renderDataQuality(env) {
  const payload = (env && env.payload) || {};
  const bars = payload.bars || payload.candles || [];
  if (!bars.length) {
    ["dq-freshness", "dq-lastbar", "dq-bars", "dq-range", "dq-quality"]
      .forEach((id) => ($(id).textContent = "--"));
    return;
  }
  const last = bars[bars.length - 1];
  $("dq-freshness").textContent = payload.freshness || "--";
  $("dq-lastbar").textContent = last.time
    ? new Date(last.time * 1000).toLocaleString(lang === "ar" ? "ar" : undefined)
    : "--";
  $("dq-bars").textContent = String(bars.length);
  let lo = Infinity, hi = -Infinity;
  bars.forEach((b) => { lo = Math.min(lo, b.low); hi = Math.max(hi, b.high); });
  $("dq-range").textContent = `${num(lo)}–${num(hi)}`;
  $("dq-quality").textContent = (env && env.quality) || "--";
}

/* ── connection detail ──────────────────────────────────────────────── */

function renderConn() {
  const rows = [
    ["health", API + "health"],
    ["system/state", API + "system/state"],
    ["bridge/status", API + "bridge/status"],
    ["analysis/latest", API + "analysis/latest"],
    ["candles(" + activeTf + ")", BRIDGE + `v1/candles?tf=${activeTf}&limit=120`],
  ];
  const tbody = $("conn-rows");
  tbody.innerHTML = "";
  rows.forEach(([label, url]) => {
    const s = connStats[url];
    const tr = document.createElement("tr");
    const state = s ? (s.ok ? "OK" : "ERR") : "--";
    tr.innerHTML =
      `<td>${label}</td>` +
      `<td class="${s ? (s.ok ? "ok" : "bad") : "warn"}">${state}</td>` +
      `<td>${s ? s.ms + " ms" : "--"}</td>`;
    tbody.appendChild(tr);
  });
}

/* ── chart ──────────────────────────────────────────────────────────── */

function ema(values, period) {
  const k = 2 / (period + 1);
  const out = [];
  let prev = null;
  values.forEach((v, i) => {
    prev = prev == null ? v : v * k + prev * (1 - k);
    out[i] = i < period - 1 ? null : prev;
  });
  return out;
}

function renderChart(payload) {
  const bars = payload.bars || payload.candles || [];
  const svg = $("chart");
  const axis = $("axis");
  svg.innerHTML = "";
  axis.innerHTML = "";
  if (!bars.length) {
    $("chart-meta").textContent = T("chart_none");
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

  // Two EMAs as descriptive context. They summarise past closes; they do not
  // forecast, and nothing here should be read as a trade trigger.
  const closes = bars.map((b) => b.close);
  [[9, "ema-fast"], [21, "ema-slow"]].forEach(([period, cls]) => {
    const vals = ema(closes, period);
    let d = "";
    vals.forEach((v, i) => {
      if (v == null) return;
      const x = pad + i * step + step / 2;
      d += `${d ? "L" : "M"}${x.toFixed(2)} ${y(v).toFixed(2)} `;
    });
    if (d) add("path", { d, class: cls, fill: "none" });
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

  $("chart-meta").textContent = fmt(T("chart_meta"), {
    tf: payload.timeframe || activeTf,
    n: bars.length,
    lo: num(lo), hi: num(hi),
    fresh: payload.freshness || "?",
  });
}

async function loadChart() {
  setPanelLoading("chart", true);
  try {
    const env = await bridgeEnvelope(`v1/candles?tf=${activeTf}&limit=120`);
    renderChart(env.payload || {});
    renderDataQuality(env);
  } catch (err) {
    $("chart-meta").textContent = fmt(T("chart_unavail"), { err: err.message });
  } finally {
    setPanelLoading("chart", false);
  }
  renderConn();
}

// The one measured edge, re-scored by the server from the same artifact and
// code the bot uses. The route returns null fields when it cannot score, so the
// panel degrades to "unavailable" rather than inventing a number.
