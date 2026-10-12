/* ASTRA mobile client.
   Reads the backend's loopback API through the app's own origin (the launcher
   reverse-proxies /api/v1 and /bridge to the backend). No secrets live here.

   Bilingual (EN/AR, RTL-aware). Honest by design: the "market state" panel is
   descriptive, and the analytics carry a live edge badge so the app never
   implies a predictive ability it does not have. */

"use strict";

const API = "api/v1/";
const BRIDGE = "bridge/";
const REFRESH_MS = 15000;
const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

/* ── i18n ───────────────────────────────────────────────────────────── */

const STRINGS = {
  en: {
    title_sub: "XAUUSD · shadow mode",
    tab_status: "Status", tab_signal: "State", tab_chart: "Chart",
    tab_conn: "Data", tab_bridge: "Bridge", tab_ai: "AI",
    ai_title: "AI advisor",
    ai_warn: "Second opinion from an external model. Outside the validated system, not scored. Not financial advice.",
    ai_price: "Live price", ai_engine: "Engine read",
    ai_news: "Used news", ai_model: "Model", ai_status: "Status",
    ai_refresh: "Ask again",
    ai_stance_long: "AI leans long", ai_stance_short: "AI leans short",
    ai_stance_neutral: "AI is neutral", ai_stance_none: "No opinion yet",
    ai_conf: "{p}% own confidence", ai_risks: "Risks", ai_ok: "Advisory loaded",
    ai_off: "AI advisor not configured",
    ai_off_sub: "Set the advisor API key on the server to enable it.",
    ai_unavail: "AI advisor unreachable",
    ver: "decision support · not financial advice",
    connecting: "connecting",
    alerts_off: "Signal alerts off",
    alerts_on: "Alerts on — you'll be told when the state changes",
    lang_toggle: "العربية",
    theme: "Theme", refresh: "Refresh", alerts: "Alerts",
    health: "System health", waiting: "waiting for data",
    mode: "Mode", startup: "Startup", decision: "Decision data",
    symbol: "Symbol", source: "Source", lasterr: "Last error",
    yes: "yes", no: "no",
    market_state: "Market state",
    descriptive: "Descriptive read of the last closed bars. Not a prediction.",
    regime: "Regime", direction: "Direction", score: "Score",
    confidence: "Confidence",
    entry: "Reference", stop: "Stop", target: "Target", rr: "R:R",
    no_decision: "no decision recorded yet",
    context_txt: "H4 bias {h4} · M15 trigger {m15} · volatility {vol}",
    edge_none: "Edge: none measured",
    edge_none_sub: "OOS 2025 · AUC ≈ 0.50",
    data_quality: "Data quality",
    dq_freshness: "Freshness", dq_lastbar: "Last closed bar",
    dq_bars: "Closed bars", dq_range: "Range", dq_quality: "Quality",
    conn_title: "Connection & health",
    conn_hint: "The app reaches the backend over one origin; these are the live links.",
    conn_ep: "Endpoint", conn_state: "State", conn_lat: "Latency",
    bridge_title: "Bridge",
    bridge_note: "The bridge is the loopback market-data link. It never executes trades and is bound to 127.0.0.1 only.",
    process: "Process", mt5_ready: "MT5 ready", broker: "Broker",
    server: "Server", transport: "Transport",
    chart_meta: "{tf} · {n} closed bars · range {lo}–{hi} · {fresh}",
    chart_none: "no closed bars available",
    chart_unavail: "chart unavailable: {err}",
    levels_title: "Price alerts",
    levels_note: "You are told when the price crosses a level you set.",
    add: "Add", added: "Added level",
    crossed_above: "crossed above", crossed_below: "crossed below",
    apk: "Download the Android app (.apk)",
    plans: "Plans & pricing",
    overview: "Overview", data: "Data",
  },
  ar: {
    title_sub: "الذهب · وضع الظل",
    tab_status: "الحالة", tab_signal: "السوق", tab_chart: "الشارت",
    tab_conn: "البيانات", tab_bridge: "الجسر", tab_ai: "الذكاء",
    ai_title: "المستشار الذكي",
    ai_warn: "رأي ثانٍ من نموذج خارجي. خارج النظام المُتحقَّق منه، وغير مُقيَّم. ليس نصيحة مالية.",
    ai_price: "السعر اللحظي", ai_engine: "قراءة المحرك",
    ai_news: "استخدم الأخبار", ai_model: "النموذج", ai_status: "الحالة",
    ai_refresh: "اسأل من جديد",
    ai_stance_long: "الذكاء يميل للشراء", ai_stance_short: "الذكاء يميل للبيع",
    ai_stance_neutral: "الذكاء محايد", ai_stance_none: "لا رأي بعد",
    ai_conf: "ثقة النموذج {p}%", ai_risks: "المخاطر", ai_ok: "تم تحميل الرأي",
    ai_off: "المستشار الذكي غير مُهيَّأ",
    ai_off_sub: "اضبط مفتاح المستشار على الخادم لتفعيله.",
    ai_unavail: "المستشار الذكي غير متاح",
    ver: "دعم قرار · ليس نصيحة مالية",
    connecting: "جارٍ الاتصال",
    alerts_off: "تنبيهات الإشارة متوقفة",
    alerts_on: "التنبيهات مفعّلة — سنخبرك عند تغيّر حالة السوق",
    lang_toggle: "English",
    theme: "المظهر", refresh: "تحديث", alerts: "تنبيهات",
    health: "صحة النظام", waiting: "في انتظار البيانات",
    mode: "الوضع", startup: "الإقلاع", decision: "بيانات القرار",
    symbol: "الرمز", source: "المصدر", lasterr: "آخر خطأ",
    yes: "نعم", no: "لا",
    market_state: "حالة السوق",
    descriptive: "قراءة وصفية لآخر الشموع المغلقة. ليست تنبؤًا.",
    regime: "النظام", direction: "الاتجاه", score: "التقييم",
    confidence: "الثقة",
    entry: "المرجع", stop: "الإيقاف", target: "الهدف", rr: "المخاطرة/العائد",
    no_decision: "لا يوجد قرار مسجّل بعد",
    context_txt: "ميول H4 {h4} · محفّز M15 {m15} · التقلب {vol}",
    edge_none: "الحافة: لا شيء مُقاس",
    edge_none_sub: "خارج العيّنة 2025 · AUC ≈ 0.50",
    data_quality: "جودة البيانات",
    dq_freshness: "الحداثة", dq_lastbar: "آخر شمعة مغلقة",
    dq_bars: "الشموع المغلقة", dq_range: "المدى", dq_quality: "الجودة",
    conn_title: "الاتصال والصحة",
    conn_hint: "الواجهة تصل للباك عبر أصل واحد؛ هذه الروابط الحيّة.",
    conn_ep: "الوجهة", conn_state: "الحالة", conn_lat: "زمن الاستجابة",
    bridge_title: "الجسر",
    bridge_note: "الجسر هو رابط بيانات السوق على loopback. لا ينفّذ صفقات أبدًا ومربوط بـ 127.0.0.1 فقط.",
    process: "العملية", mt5_ready: "MT5 جاهز", broker: "الوسيط",
    server: "السيرفر", transport: "النقل",
    chart_meta: "{tf} · {n} شمعة مغلقة · المدى {lo}–{hi} · {fresh}",
    chart_none: "لا توجد شموع مغلقة",
    chart_unavail: "الشارت غير متاح: {err}",
    levels_title: "تنبيهات السعر",
    levels_note: "سنخبرك عند اختراق السعر لمستوى تحدّده أنت.",
    add: "إضافة", added: "أُضيف المستوى",
    crossed_above: "اخترق صعودًا", crossed_below: "اخترق هبوطًا",
    apk: "حمّل تطبيق أندرويد (.apk)",
    plans: "الخطط والأسعار",
    overview: "نظرة عامة", data: "البيانات",
  },
};

let lang = "en";
const T = (key) => (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
function fmt(str, vars) {
  return str.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : "--"));
}

/* ── helpers ────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const num = (v, d = 2) =>
  v === null || v === undefined || v === "" ? "--" : Number(v).toFixed(d);
const yn = (v) => (v ? T("yes") : T("no"));

let activeTf = "M15";
let alertsOn = false;
let lastSignalKey = null;
let lastPrice = null;
let priceLevels = []; // { value, crossed }
const LEVELS_KEY = "astra-levels";
const connStats = {}; // path -> { ok, ms }

/* ── theme ──────────────────────────────────────────────────────────── */

const THEME_KEY = "astra-theme";
const LANG_KEY = "astra-lang";

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

async function getJson(url) {
  const t0 = performance.now();
  try {
    const res = await fetch(url, { cache: "no-store" });
    connStats[url] = { ok: res.ok, ms: Math.round(performance.now() - t0) };
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } catch (err) {
    if (!connStats[url]) connStats[url] = { ok: false, ms: Math.round(performance.now() - t0) };
    throw err;
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

/* ── AI advisor (isolated, unvalidated) ─────────────────────────────── */

const AI_STANCE_KEY = {
  LONG: "ai_stance_long", SHORT: "ai_stance_short", NEUTRAL: "ai_stance_neutral",
};

// Reads the isolated advisor through the same-origin proxy. A failure here is
// shown as "unreachable" and changes nothing else in the app: the advisor is
// outside the system and must never affect the validated views.
async function loadAdvisor(force) {
  const stanceEl = $("ai-stance");
  const setStatus = (text) => { $("ai-status").textContent = text; };
  try {
    const env = await getJson("advisor/v1/advisor" + (force ? "?refresh=1" : ""));
    const a = (env && env.advisor) || {};
    const snap = a.snapshot || {};
    const op = a.opinion;

    $("ai-price").textContent = snap.price != null ? num(snap.price) : "--";
    const eng = (snap.analysis || {}).signal || {};
    $("ai-engine").textContent = snap.analysis_available
      ? (eng.direction || "NONE") : T("ai_stance_none");
    $("ai-model").textContent = a.model || "--";
    $("ai-news").textContent = op ? yn(op.news_used) : "--";

    stanceEl.className = "ai-stance";
    if (op) {
      stanceEl.textContent = T(AI_STANCE_KEY[op.stance] || "ai_stance_neutral");
      stanceEl.classList.add(op.stance.toLowerCase());
      $("ai-conf").textContent = T("ai_conf").replace(
        "{p}", Math.round((op.confidence || 0) * 100));
      $("ai-thesis").textContent = op.thesis || "";
      $("ai-risks").textContent = op.risks ? `${T("ai_risks")}: ${op.risks}` : "";
      setStatus(T("ai_ok"));
    } else if (!a.configured) {
      stanceEl.textContent = T("ai_off");
      stanceEl.classList.add("neutral");
      $("ai-conf").textContent = "";
      $("ai-thesis").textContent = a.note || T("ai_off_sub");
      $("ai-risks").textContent = "";
      setStatus(T("ai_off"));
    } else {
      stanceEl.textContent = T("ai_stance_none");
      stanceEl.classList.add("neutral");
      $("ai-conf").textContent = "";
      $("ai-thesis").textContent = a.note || "";
      $("ai-risks").textContent = "";
      setStatus(T("ai_unavail"));
    }
  } catch (err) {
    stanceEl.textContent = T("ai_unavail");
    stanceEl.className = "ai-stance neutral";
    $("ai-conf").textContent = "";
    $("ai-thesis").textContent = "";
    $("ai-risks").textContent = "";
    setStatus(T("ai_unavail"));
  }
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
  const dir = (DIR_LABEL[lang] || DIR_LABEL.en)[s.direction || "NONE"];
  const text = `ASTRA: ${dir}` +
    (s.score != null ? ` (${num(s.score, 1)})` : "");
  toast(text);

  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("ASTRA", { body: text, tag: "astra-signal" }); }
    catch (e) { /* notification constructor can throw on some browsers */ }
  }
  if (navigator.vibrate) navigator.vibrate([40, 60, 40]);
}

async function toggleAlerts() {
  const btn = $("alerts");
  if (alertsOn) {
    alertsOn = false;
    btn.setAttribute("aria-pressed", "false");
    toast(T("alerts_off"));
    return;
  }
  alertsOn = true;
  btn.setAttribute("aria-pressed", "true");
  toast(T("alerts_on"));
  if ("Notification" in window && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
  }
}

/* ── price levels ───────────────────────────────────────────────────── */

function loadLevels() {
  try {
    const raw = localStorage.getItem(LEVELS_KEY);
    priceLevels = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(priceLevels)) priceLevels = [];
  } catch (e) { priceLevels = []; }
}

function saveLevels() {
  try { localStorage.setItem(LEVELS_KEY, JSON.stringify(priceLevels)); }
  catch (e) { /* ignore */ }
}

function renderLevels() {
  const list = $("level-list");
  list.innerHTML = "";
  priceLevels.forEach((lv, i) => {
    const li = document.createElement("li");
    li.className = "lvl" + (lv.crossed ? " crossed" : "");
    const span = document.createElement("span");
    span.textContent = num(lv.value);
    const del = document.createElement("button");
    del.type = "button";
    del.setAttribute("aria-label", "remove");
    del.textContent = "×";
    del.addEventListener("click", () => {
      priceLevels.splice(i, 1);
      saveLevels();
      renderLevels();
    });
    li.appendChild(span);
    li.appendChild(del);
    list.appendChild(li);
  });
}

function addLevel(value) {
  if (!Number.isFinite(value)) return;
  priceLevels.push({ value, crossed: false });
  priceLevels.sort((a, b) => a.value - b.value);
  saveLevels();
  renderLevels();
  toast(`${T("added")} ${num(value)}`);
}

function notify(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body, tag: "astra-level" }); }
    catch (e) { /* some browsers throw */ }
  }
  if (navigator.vibrate) navigator.vibrate([40, 60, 40]);
}

function checkLevels(price) {
  if (lastPrice == null) { lastPrice = price; return; }
  let changed = false;
  priceLevels.forEach((lv) => {
    const crossedUp = lastPrice < lv.value && price >= lv.value;
    const crossedDown = lastPrice > lv.value && price <= lv.value;
    if (crossedUp || crossedDown) {
      const dir = crossedUp ? T("crossed_above") : T("crossed_below");
      toast(`ASTRA: ${num(price)} ${dir} ${num(lv.value)}`);
      notify("ASTRA", `${num(price)} ${dir} ${num(lv.value)}`);
      lv.crossed = true;
      changed = true;
    }
  });
  lastPrice = price;
  if (changed) renderLevels();
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
    renderBridge(br);
    maybeAlert(analysis);
    banner(aggregate === "ONLINE" ? "online"
      : aggregate === "OFFLINE" ? "offline" : "degraded",
      `${aggregate.toLowerCase()} · ${br.broker || "no source"}`);
  } catch (err) {
    banner("offline", "backend unreachable");
  } finally {
    btn.classList.remove("busy");
  }
  loadTicker();
  loadChart();
  renderConn();
}

async function loadChart() {
  try {
    const env = await bridgeEnvelope(`v1/candles?tf=${activeTf}&limit=120`);
    renderChart(env.payload || {});
    renderDataQuality(env);
  } catch (err) {
    $("chart-meta").textContent = fmt(T("chart_unavail"), { err: err.message });
  }
  renderConn();
}

// A prominent live price in the header. The price itself comes from the live
// tick (the freshest quote the bridge has); the closed-bar series only supplies
// the reference for the change. Sampling the price from the newest *closed* bar
// would show a price up to a full interval old.
async function loadTicker() {
  try {
    const [candleEnv, tickEnv] = await Promise.all([
      bridge(`v1/candles?tf=${activeTf}&limit=2`),
      bridge(`v1/tick?symbol=XAUUSD`).catch(() => null),
    ]);
    const bars = candleEnv.bars || candleEnv.candles || [];
    if (!bars.length) { $("ticker").hidden = true; return; }
    const last = bars[bars.length - 1];
    const prev = bars[bars.length - 2] || last;
    const live = tickEnv && Number(tickEnv.last) > 0 ? Number(tickEnv.last) : null;
    const price = live !== null ? live : last.close;
    const change = price - prev.close;
    const pct = prev.close ? (change / prev.close) * 100 : 0;

    $("price").textContent = num(price);
    const chg = $("price-chg");
    chg.textContent = `${change >= 0 ? "+" : ""}${num(change)}  `
      + `${change >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
    chg.className = "ticker-chg " + (change > 0 ? "up" : change < 0 ? "down" : "");
    $("price-basis").textContent = live !== null
      ? `live · gold-api.com`
      : `${activeTf} close · gold-api.com`;
    $("ticker").hidden = false;
    checkLevels(price);
  } catch (err) {
    $("ticker").hidden = true;
  }
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
      if (view === "conn") renderConn();
      if (view === "ai") loadAdvisor(false);
    });
  });
}

function init() {
  initLang();
  initTheme();
  loadLevels();
  renderLevels();
  buildTimeframes();
  bindTabs();
  $("refresh").addEventListener("click", refresh);
  $("ai-refresh").addEventListener("click", () => loadAdvisor(true));
  $("alerts").addEventListener("click", toggleAlerts);
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
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

document.addEventListener("DOMContentLoaded", init);
