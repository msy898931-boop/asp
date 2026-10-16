/* ASTRA Terminal — the web console for the ASTRA backend.
 *
 * One rule governs every panel: show the backend's real answer, and when the
 * answer is "unknown" say so — never render a plausible-looking zero in its
 * place. Unknown is not safe, so it is never dressed as a value.
 *
 * Data reaches the page through the app's own origin (serve_mobile.py
 * reverse-proxies /api/v1 to the backend), so the page holds no secret and no
 * cross-origin credentials. Bilingual AR/EN, RTL-aware. */

"use strict";

const API = "api/v1/";
const BRIDGE = "bridge/";
const REFRESH_MS = 20000;
const FETCH_TIMEOUT_MS = 15000;

const ENDPOINTS = [
  "health", "system/state", "bridge/status", "analysis/latest",
  "signals/latest", "timeframes", "risk/latest", "governance/status",
  "research/status", "shadow/positions", "shadow/outcomes",
  "probability/latest", "analysis/history", "audit/recent",
];

/* ── i18n ───────────────────────────────────────────────────────────── */

const STRINGS = {
  ar: {
    title_sub: "لوحة السوق · وضع الظلّ",
    lang_toggle: "English", theme: "المظهر", refresh: "تحديث",
    connecting: "جارٍ الاتصال",
    banner_offline: "الباكند غير متاح",
    banner_locked: "الباكند محمي بمفتاح (401)",
    banner_partial: "بعض القراءات غير متاحة",
    banner_live: "مباشر",
    banner_updated: "آخر تحديث {t} UTC",
    nav_overview: "النظرة العامة", nav_timeframes: "الإطارات",
    nav_services: "الخدمات", nav_audit: "التدقيق",
    nav_risk: "المخاطر", nav_console: "الطرفية",
    ov_sub: "حالة السوق والبنية والخدمات في لمحة",
    ov_waiting: "بانتظار حالة الباكند…",
    ov_warnings: "تحذيرات تشغيلية",
    ov_shortcuts: "اختصارات",
    no_warnings: "لا توجد أسباب تدهور مُبلَّغ عنها.",
    edge_none: "حافة الاتجاه: غير مُقاسة",
    edge_none_sub: "OOS 2025 · AUC ≈ 0.50",
    edge_vol: "حافة التقلّب: مُقاسة",
    edge_vol_sub: "OOS 2025 · AUC ≈ 0.74 · تتنبأ بالحجم لا بالاتجاه",
    tf_note: "تغطية تسعة إطارات زمنية: هل وصلت الشمعة المغلقة، وما جودة قرارها. اضغط أياً منها للتفصيل.",
    tf_observed: "{n} / {t} مُلاحَظ", tf_grade: "{n} جاهز للقرار",
    tf_observed_label: "مُلاحَظ", tf_unobserved: "غير مُلاحَظ",
    tf_closed: "شمعة مغلقة", tf_no_closed: "لا شمعة مغلقة",
    tf_decision: "قرار", tf_freshness: "الحداثة",
    sv_sub: "الروابط الحيّة التي يقرأ منها التطبيق",
    sv_note: "كل صفٍّ نداءٌ حقيقي إلى الباكند عبر أصل التطبيق نفسه. القيمة المعروضة من الاستجابة، لا مُلفَّقة.",
    sv_ep: "نقطة النهاية", sv_state: "الحالة", sv_lat: "الزمن",
    sv_bridge: "الجسر / MT5",
    bridge_note: "الجسر هو رابط بيانات السوق على الحلقة المحلية. لا ينفّذ صفقات أبداً ومرتبط بـ127.0.0.1 فقط.",
    au_sub: "سجلّات التدقيق والحوادث النشطة",
    au_note: "تُعرض بيانات تدقيق عامة فقط. رموز المصادقة والاعتمادات لا تُكشَف في هذه الواجهة أبداً.",
    au_records: "سجلّات", au_stream: "حجم السجل", au_incidents: "حوادث نشطة",
    au_empty: "لا سجلّات تدقيق",
    au_empty_sub: "أعاد الباكند سجلّاً فارغاً.",
    rk_sub: "حدود المخاطر والجاهزية والصلاحيات",
    rk_readout: "قراءة النظام",
    rk_note: "هذا نظام ظلّي. لا يملك تفويض تنفيذ، ولن ينفّذ صفقة من هذه الواجهة.",
    cn_sub: "شغّل نداءً وشاهد الاستجابة الخام",
    cn_note: "اختر نقطة نهاية وشغّلها. تُعرض الاستجابة كما وصلت من الباكند، بلا تجميل — مفيدة لمعرفة ما يراه التطبيق فعلاً.",
    cn_run: "تشغيل",
    foot_app: "تطبيق الجوال", foot_perf: "الأداء", foot_plans: "الخطط",
    foot_disclaimer: "دعم قرار فقط. ليس نصيحة مالية.",
    /* metric labels */
    m_instrument: "الأداة", m_mode: "الوضع", m_aggregate: "الحالة الكلية",
    m_decision_grade: "جاهزية القرار", m_startup: "بدء التشغيل",
    m_coverage: "تغطية الإطارات", m_grade: "الدرجة",
    m_bridge: "الجسر / المُرسِل", m_risk: "حدود المخاطر",
    m_open_pos: "صفقات مفتوحة", m_auth: "تفويض التنفيذ",
    m_research: "وضع البحث", m_experiments: "تجارب",
    m_proposal: "مقترح", m_shadow_only: "ظلّي فقط", m_ready: "جاهز",
    m_version: "الإصدار", m_strategy: "الاستراتيجية",
    /* values */
    v_yes: "نعم", v_no: "لا", v_na: "غير متاح",
    v_open: "مفتوح", v_closed: "مغلق",
  },
  en: {
    title_sub: "Market console · shadow mode",
    lang_toggle: "العربية", theme: "Theme", refresh: "Refresh",
    connecting: "connecting",
    banner_offline: "backend unreachable",
    banner_locked: "backend locked (401)",
    banner_partial: "some readings unavailable",
    banner_live: "live",
    banner_updated: "updated {t} UTC",
    nav_overview: "Overview", nav_timeframes: "Timeframes",
    nav_services: "Services", nav_audit: "Audit",
    nav_risk: "Risk", nav_console: "Console",
    ov_sub: "Market state, structure and services at a glance",
    ov_waiting: "waiting for backend state…",
    ov_warnings: "Operational warnings",
    ov_shortcuts: "Shortcuts",
    no_warnings: "No degraded reasons reported.",
    edge_none: "Direction edge: none measured",
    edge_none_sub: "OOS 2025 · AUC ≈ 0.50",
    edge_vol: "Volatility edge: measured",
    edge_vol_sub: "OOS 2025 · AUC ≈ 0.74 · predicts size, not direction",
    tf_note: "Nine-timeframe coverage: whether the closed bar arrived and what its decision grade is. Tap one for detail.",
    tf_observed: "{n} / {t} observed", tf_grade: "{n} decision-grade",
    tf_observed_label: "observed", tf_unobserved: "unobserved",
    tf_closed: "closed bar", tf_no_closed: "no closed bar",
    tf_decision: "decision", tf_freshness: "freshness",
    sv_sub: "The live links the app reads from",
    sv_note: "Each row is a real call to the backend over the app's own origin. The value shown is the response, never fabricated.",
    sv_ep: "Endpoint", sv_state: "State", sv_lat: "Latency",
    sv_bridge: "Bridge / MT5",
    bridge_note: "The bridge is the loopback market-data link. It never executes trades and is bound to 127.0.0.1 only.",
    au_sub: "Audit records and active incidents",
    au_note: "Only public audit metadata is shown. Authentication tokens and credentials are never exposed in this interface.",
    au_records: "Records", au_stream: "Stream size", au_incidents: "Active incidents",
    au_empty: "No audit records",
    au_empty_sub: "The backend returned an empty audit stream.",
    rk_sub: "Risk bounds, readiness and authority",
    rk_readout: "System readout",
    rk_note: "This is a shadow system. It holds no execution authority and will not place a trade from this interface.",
    cn_sub: "Run a call and watch the raw response",
    cn_note: "Pick an endpoint and run it. The response is shown exactly as it arrived — useful to see what the app really reads.",
    cn_run: "Run",
    foot_app: "Phone app", foot_perf: "Performance", foot_plans: "Plans",
    foot_disclaimer: "Decision support only. Not financial advice.",
    m_instrument: "Instrument", m_mode: "Mode", m_aggregate: "Aggregate",
    m_decision_grade: "Decision grade", m_startup: "Startup",
    m_coverage: "Timeframe coverage", m_grade: "Grade",
    m_bridge: "Bridge / sender", m_risk: "Risk bounds",
    m_open_pos: "Open positions", m_auth: "Execution authority",
    m_research: "Research mode", m_experiments: "Experiments",
    m_proposal: "Proposal", m_shadow_only: "Shadow only", m_ready: "Ready",
    m_version: "Version", m_strategy: "Strategy",
    v_yes: "yes", v_no: "no", v_na: "unavailable",
    v_open: "open", v_closed: "closed",
  },
};

let lang = "ar";
const T = (key) => (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;

/* ── theme + language ───────────────────────────────────────────────── */

const THEME_KEY = "astra-theme";
const LANG_KEY = "astra-lang";

function applyTheme() {
  const t = document.documentElement.getAttribute("data-theme") || "dark";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", t === "light" ? "#F4F0E6" : "#0B0E13");
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
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* private mode */ }
}

function $(id) { return document.getElementById(id); }

/* ── fetch ──────────────────────────────────────────────────────────── */

async function getJson(url, timeout = FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, { cache: "no-store", signal: ctrl.signal });
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) {
      const err = new Error("HTTP " + res.status);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

/* The backend wraps every answer in { data: ... }. Unwrap it, but keep a
   non-object data (a list, a string) rather than dropping it to {}. */
async function api(path) {
  const body = await getJson(API + path);
  if (body && Object.prototype.hasOwnProperty.call(body, "data")) {
    return body.data;
  }
  return body;
}

async function bridge(path) {
  const body = await getJson(BRIDGE + path);
  if (body && typeof body.payload === "object" && body.payload !== null) {
    return body.payload;
  }
  return body;
}

/* ── formatting ─────────────────────────────────────────────────────── */

function num(v, digits = 2) {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return null;
  return Number(v).toFixed(digits).replace(/\.0+$/, "");
}

function fmtEpochUTC(sec) {
  if (sec === null || sec === undefined || !Number.isFinite(Number(sec)) || Number(sec) <= 0) return null;
  const d = new Date(Number(sec) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

function fmtAge(sec) {
  const s = Number(sec);
  if (!Number.isFinite(s) || s < 0) return null;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

/* Map a backend state word to a tone. "unknown"/absent is never "ok". */
function tone(word) {
  const w = String(word || "").toUpperCase();
  if (!w || w === "UNKNOWN" || w === "OFFLINE" || w === "STARTING") return "unknown";
  if (w === "OK" || w === "READY" || w === "LIVE" || w === "OBSERVED" ||
      w === "CONNECTED" || w === "UP" || w === "PASS") return "ok";
  if (w === "DEGRADED" || w === "STALE" || w === "WARMING" || w === "PARTIAL" ||
      w === "RECONNECTING") return "warn";
  if (w === "DOWN" || w === "FAILED" || w === "ERROR" || w === "BLOCKED") return "bad";
  return "unknown";
}

const yn = (v) => (v === true ? T("v_yes") : v === false ? T("v_no") : null);

/* ── metric + readout builders ──────────────────────────────────────── */

function metric(label, value, src, cls) {
  const wrap = document.createElement("div");
  wrap.className = "metric";
  const l = document.createElement("span");
  l.className = "metric-label";
  l.textContent = label;
  const v = document.createElement("span");
  v.className = "metric-value" + (cls ? " " + cls : "");
  v.textContent = value === null || value === undefined ? T("v_na") : String(value);
  if (value === null || value === undefined) v.classList.add("unknown");
  const s = document.createElement("span");
  s.className = "metric-src";
  s.textContent = src;
  wrap.append(l, v, s);
  return wrap;
}

function readoutRow(dl, label, value, cls) {
  const d = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value === null || value === undefined ? T("v_na") : String(value);
  if (cls) dd.className = cls;
  d.append(dt, dd);
  dl.appendChild(d);
}

/* ── state cache ────────────────────────────────────────────────────── */

const state = {
  health: null, system: null, bridge: null, analysis: null,
  timeframes: null, risk: null, governance: null, research: null,
  lastUpdated: null, route: "overview",
};

/* ── panel: overview ────────────────────────────────────────────────── */

function renderOverview() {
  const h = state.health || {};
  const sys = state.system || {};
  const br = state.bridge || {};
  const tfs = Array.isArray(state.timeframes) ? state.timeframes : [];
  const observed = tfs.filter((t) => t && t.observed).length;
  const graded = tfs.filter((t) => t && t.decision_grade).length;

  const agg = $("aggregate");
  const aggWord = h.aggregate || sys.mode || null;
  agg.className = "aggregate " + tone(aggWord);
  $("agg-value").textContent = aggWord || "--";
  const reasons = Array.isArray(h.degraded_reasons) ? h.degraded_reasons : [];
  $("agg-note").textContent = h.decision_grade_data === true
    ? (lang === "ar" ? "البيانات جاهزة للقرار." : "Decision-grade data is available.")
    : (lang === "ar"
        ? "البيانات غير جاهزة للقرار. الحالات المجهولة تُعامَل كغير آمنة."
        : "Decision-grade data is NOT available. Unknown states are treated as not safe.");

  const m = $("matrix");
  m.textContent = "";
  m.append(
    metric(T("m_instrument"), h.symbol || state.analysis?.symbol || "XAUUSD", "config", null),
    metric(T("m_mode"), sys.mode || null, "system/state", tone(sys.mode)),
    metric(T("m_aggregate"), h.aggregate || null, "health", tone(h.aggregate)),
    metric(T("m_decision_grade"), yn(h.decision_grade_data), "health", h.decision_grade_data === true ? "ok" : "warn"),
    metric(T("m_startup"), sys.startup_stage || null, "system/state", tone(sys.startup_stage)),
    metric(T("m_coverage"), tfs.length ? `${observed}/${tfs.length}` : null, "timeframes", observed > 0 ? "ok" : "warn"),
    metric(T("m_grade"), tfs.length ? String(graded) : null, "timeframes", graded > 0 ? "ok" : "warn"),
    metric(T("m_bridge"), br.bridge_process_state || br.process_state || null, "bridge/status", tone(br.bridge_process_state || br.process_state)),
    metric(T("m_ready"), yn(sys.ready), "system/state", sys.ready === true ? "ok" : "warn"),
    metric(T("m_shadow_only"), yn(sys.shadow_only), "system/state", "ok"),
    metric(T("m_version"), h.version || null, "health", null),
    metric(T("m_strategy"), h.strategy_version || null, "health", null),
  );

  const wl = $("warnings");
  wl.textContent = "";
  if (!reasons.length) {
    const li = document.createElement("li");
    li.textContent = T("no_warnings");
    wl.appendChild(li);
  } else {
    reasons.forEach((r) => {
      const li = document.createElement("li");
      li.textContent = typeof r === "string" ? r : JSON.stringify(r);
      wl.appendChild(li);
    });
  }

  const sc = $("shortcuts");
  sc.textContent = "";
  [["timeframes", T("nav_timeframes")], ["services", T("nav_services")],
   ["audit", T("nav_audit")], ["risk", T("nav_risk")], ["console", T("nav_console")]
  ].forEach(([route, label]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "shortcut";
    b.textContent = label;
    b.addEventListener("click", () => go(route));
    sc.appendChild(b);
  });
}

/* ── panel: timeframes ──────────────────────────────────────────────── */

function renderTimeframes() {
  const tfs = Array.isArray(state.timeframes) ? state.timeframes : [];
  const grid = $("tf-grid");
  grid.textContent = "";
  const observed = tfs.filter((t) => t && t.observed).length;
  const graded = tfs.filter((t) => t && t.decision_grade).length;
  $("tf-summary").textContent = tfs.length
    ? T("tf_observed").replace("{n}", observed).replace("{t}", tfs.length) +
      " · " + T("tf_grade").replace("{n}", graded)
    : "--";

  if (!tfs.length) {
    const p = document.createElement("p");
    p.className = "panel-note";
    p.textContent = T("v_na");
    grid.appendChild(p);
    return;
  }

  tfs.forEach((tf) => {
    const quality = (tf && tf.quality) || {};
    const cellState = !tf.observed ? "UNOBSERVED" : (quality.state || "UNKNOWN");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tf-cell";
    btn.dataset.state = cellState;
    btn.setAttribute("role", "listitem");

    const name = document.createElement("span");
    name.className = "tf-name";
    name.textContent = tf.timeframe || "?";

    const badge = document.createElement("span");
    badge.className = "tf-badge " + (tf.observed ? tone(quality.state) : "unknown");
    badge.textContent = tf.observed ? (quality.state || T("tf_observed_label")) : T("tf_unobserved");

    const closed = document.createElement("span");
    closed.className = "tf-quality";
    closed.textContent = tf.has_closed_bar ? T("tf_closed") : T("tf_no_closed");

    const fresh = document.createElement("span");
    fresh.className = "tf-fresh";
    const age = tf.freshness && (tf.freshness.age_seconds ?? tf.freshness.age_sec ?? tf.freshness);
    fresh.textContent = age != null && Number.isFinite(Number(age))
      ? T("tf_freshness") + " " + (fmtAge(age) || "--")
      : T("tf_decision") + " " + (tf.decision_grade ? "✓" : "—");

    btn.append(name, badge, closed, fresh);
    btn.addEventListener("click", () => openTfDetail(tf));
    grid.appendChild(btn);
  });
}

function openTfDetail(tf) {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  scrim.setAttribute("role", "dialog");
  scrim.setAttribute("aria-modal", "true");
  const box = document.createElement("div");
  box.className = "tf-detail panel";
  const h = document.createElement("h3");
  h.className = "panel-title sm";
  h.textContent = (tf.timeframe || "?") + " — " + T("tf_decision");
  box.appendChild(h);
  const dl = document.createElement("dl");
  dl.className = "readout";
  const q = tf.quality || {};
  readoutRow(dl, T("tf_observed_label"), yn(tf.observed));
  readoutRow(dl, T("tf_closed"), yn(tf.has_closed_bar));
  readoutRow(dl, T("sv_state"), q.state || null, tone(q.state));
  readoutRow(dl, T("tf_decision"), yn(tf.decision_grade), tf.decision_grade ? "ok" : "warn");
  readoutRow(dl, T("tf_freshness"), fmtEpochUTC(tf.last_successful_update));
  box.appendChild(dl);
  const impacts = Array.isArray(tf.capability_impact) ? tf.capability_impact : [];
  if (impacts.length) {
    const ul = document.createElement("ul");
    ul.className = "reasons";
    impacts.forEach((im) => {
      const li = document.createElement("li");
      li.textContent = `${im.capability}: ${im.impact} — ${im.reason || ""}`;
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "text-btn run";
  close.textContent = lang === "ar" ? "إغلاق" : "Close";
  close.addEventListener("click", () => scrim.remove());
  box.appendChild(close);
  scrim.appendChild(box);
  scrim.addEventListener("click", (e) => { if (e.target === scrim) scrim.remove(); });
  document.body.appendChild(scrim);
}

/* ── panel: services ────────────────────────────────────────────────── */

async function renderServices() {
  const rows = $("svc-rows");
  rows.textContent = "";
  const head = document.createElement("div");
  head.className = "svc-row svc-head";
  head.innerHTML = `<span>${T("sv_ep")}</span><span>${T("sv_state")}</span><span class="svc-lat">${T("sv_lat")}</span>`;
  rows.appendChild(head);

  await Promise.all(ENDPOINTS.map(async (ep) => {
    const row = document.createElement("div");
    row.className = "svc-row";
    const t0 = performance.now();
    let stateWord = "UNKNOWN", cls = "unknown";
    try {
      const res = await fetch(API + ep, { cache: "no-store" });
      const ms = Math.round(performance.now() - t0);
      stateWord = res.ok ? "OK" : "HTTP " + res.status;
      cls = res.ok ? "ok" : (res.status >= 500 ? "bad" : "warn");
      row.innerHTML = `<span class="svc-name">${esc(ep)}</span>` +
        `<span class="svc-state ${cls}">${esc(stateWord)}</span>` +
        `<span class="svc-lat">${ms} ms</span>`;
    } catch (e) {
      row.innerHTML = `<span class="svc-name">${esc(ep)}</span>` +
        `<span class="svc-state bad">${esc(lang === "ar" ? "غير متاح" : "unreachable")}</span>` +
        `<span class="svc-lat">—</span>`;
    }
    rows.appendChild(row);
  }));

  const br = state.bridge || {};
  const dl = $("bridge-readout");
  dl.textContent = "";
  readoutRow(dl, T("sv_state"), br.bridge_process_state || br.process_state || null,
             tone(br.bridge_process_state || br.process_state));
  readoutRow(dl, "MT5", yn(br.mt5_ready), br.mt5_ready ? "ok" : "warn");
  readoutRow(dl, T("m_instrument"), br.resolved_symbol || br.bridge_symbol || null);
  readoutRow(dl, "Broker", br.broker || null);
  readoutRow(dl, "Server", br.server || null);
  readoutRow(dl, "Transport", br.transport || null);
}

/* ── panel: audit ───────────────────────────────────────────────────── */

function renderAudit(data) {
  const d = data || {};
  const records = Array.isArray(d.audit_records) ? d.audit_records : [];
  const incidents = Array.isArray(d.active_incidents) ? d.active_incidents : [];
  const m = $("audit-metrics");
  m.textContent = "";
  m.append(
    metric(T("au_records"), d.count != null ? String(d.count) : String(records.length), "audit/recent", null),
    metric(T("au_stream"), d.audit_stream_size != null ? String(d.audit_stream_size) : null, "backend buffer", null),
    metric(T("au_incidents"), String(incidents.length), "open", incidents.length ? "warn" : "ok"),
  );
  const body = $("audit-body");
  body.textContent = "";
  if (!records.length) {
    const p = document.createElement("p");
    p.className = "panel-note";
    p.textContent = T("au_empty") + " — " + T("au_empty_sub");
    body.appendChild(p);
  } else {
    const pre = document.createElement("pre");
    pre.className = "console-out";
    pre.textContent = JSON.stringify(records.slice(0, 20), null, 2);
    body.appendChild(pre);
  }
}

/* ── panel: risk & system ───────────────────────────────────────────── */

function renderRisk() {
  const r = state.risk || {};
  const g = state.governance || {};
  const rs = state.research || {};
  const sys = state.system || {};
  const m = $("risk-metrics");
  m.textContent = "";
  m.append(
    metric(T("m_open_pos"), r.open_positions != null ? String(r.open_positions) : null, "risk/latest", null),
    metric(T("m_risk"), r.aggregate_open_risk_fraction != null
      ? (Number(r.aggregate_open_risk_fraction) * 100).toFixed(2) + "%" : null, "risk/latest", null),
    metric(T("m_auth"), yn(g.live_trading_authorised), "governance/status",
           g.live_trading_authorised === true ? "warn" : "ok"),
    metric(T("m_research"), rs.mode || null, "research/status", rs.mode ? "ok" : null),
    metric(T("m_experiments"), rs.experiment_count != null ? String(rs.experiment_count) : null, "research/status", null),
    metric(T("m_ready"), yn(sys.ready), "system/state", sys.ready === true ? "ok" : "warn"),
  );
  const dl = $("risk-readout");
  dl.textContent = "";
  readoutRow(dl, T("m_mode"), sys.mode || null, tone(sys.mode));
  readoutRow(dl, T("m_shadow_only"), yn(sys.shadow_only), "ok");
  readoutRow(dl, T("m_startup"), sys.startup_stage || null, tone(sys.startup_stage));
  readoutRow(dl, T("m_bridge"), sys.bridge_state || null, tone(sys.bridge_state));
  readoutRow(dl, T("m_proposal"), r.proposal_available ? T("v_yes") : (r.proposal_reason || T("v_no")));
  readoutRow(dl, T("m_auth"), yn(g.live_trading_authorised),
             g.live_trading_authorised === true ? "warn" : "ok");
}

/* ── console ────────────────────────────────────────────────────────── */

function initConsole() {
  const sel = $("cn-endpoint");
  sel.textContent = "";
  ENDPOINTS.forEach((ep) => {
    const o = document.createElement("option");
    o.value = ep;
    o.textContent = ep;
    sel.appendChild(o);
  });
  $("cn-run").addEventListener("click", runConsole);
}

async function runConsole() {
  const ep = $("cn-endpoint").value;
  const out = $("cn-out");
  const meta = $("cn-meta");
  out.textContent = lang === "ar" ? "…جارٍ التشغيل" : "…running";
  meta.textContent = "";
  const t0 = performance.now();
  try {
    const body = await getJson(API + ep);
    const ms = Math.round(performance.now() - t0);
    out.textContent = JSON.stringify(body, null, 2);
    meta.textContent = `HTTP 200 · ${ms} ms · ${ep}`;
  } catch (e) {
    const ms = Math.round(performance.now() - t0);
    out.textContent = e.body ? JSON.stringify(e.body, null, 2)
                             : (e.message || String(e));
    meta.textContent = `${e.message || "error"} · ${ms} ms · ${ep}`;
  }
}

/* ── routing ────────────────────────────────────────────────────────── */

const ROUTES = ["overview", "timeframes", "services", "audit", "risk", "console"];

function go(route) {
  if (!ROUTES.includes(route)) route = "overview";
  state.route = route;
  document.querySelectorAll(".panel").forEach((p) => {
    p.hidden = p.id !== "view-" + route;
  });
  document.querySelectorAll(".tabs .tab").forEach((t) => {
    t.setAttribute("aria-selected", t.dataset.view === route ? "true" : "false");
  });
  if (location.hash !== "#" + route) {
    history.replaceState(null, "", "#" + route);
  }
  window.scrollTo({ top: 0 });
  if (route === "services") renderServices();
}

function initTabs() {
  document.querySelectorAll(".tabs .tab").forEach((t) => {
    t.addEventListener("click", () => go(t.dataset.view));
  });
  window.addEventListener("hashchange", () => go(location.hash.slice(1)));
}

/* ── banner ─────────────────────────────────────────────────────────── */

function banner(kind, text) {
  const b = $("banner");
  b.classList.remove("online", "degraded", "offline");
  b.classList.add(kind === "ok" ? "online" : kind);
  $("banner-text").textContent = text;
}

/* ── refresh loop ───────────────────────────────────────────────────── */

async function refresh() {
  const specs = [
    ["health", "health"], ["system", "system/state"], ["bridge", "bridge/status"],
    ["analysis", "analysis/latest"], ["timeframes", "timeframes"], ["risk", "risk/latest"],
    ["governance", "governance/status"], ["research", "research/status"],
  ];
  const settled = await Promise.allSettled(specs.map(([, path]) => api(path)));

  let firstError = null;
  let anyOk = false;
  settled.forEach((res, i) => {
    const key = specs[i][0];
    if (res.status === "fulfilled") {
      state[key] = res.value;
      anyOk = true;
    } else {
      if (!firstError) firstError = res.reason;
      state[key] = null;
    }
  });

  if (!anyOk) {
    // Name the real failure. A 401 is a locked backend, not a dead one, and
    // saying "unreachable" for a gate would send the operator down the wrong
    // path. Anything else is a genuine reachability problem.
    const status = firstError && firstError.status;
    if (status === 401) banner("degraded", T("banner_locked"));
    else banner("offline", T("banner_offline"));
    renderOverview();
    renderTimeframes();
    renderRisk();
    return;
  }

  // Audit is fetched separately: it is metadata, and a failure there must not
  // blank the operational panels above it.
  let audit = null;
  try { audit = await api("audit/recent"); } catch (e) { audit = null; }
  state.lastUpdated = new Date();

  if (firstError) banner("degraded", T("banner_partial"));
  else banner("ok", T("banner_live") + " · " + T("banner_updated").replace(
    "{t}", fmtEpochUTC(Math.floor(Date.now() / 1000))));

  renderOverview();
  renderTimeframes();
  renderAudit(audit);
  renderRisk();
  if (state.route === "services") renderServices();
}

/* ── boot ───────────────────────────────────────────────────────────── */

function boot() {
  const forced = new URLSearchParams(location.search).get("lang");
  if (forced === "ar" || forced === "en") {
    lang = forced;
    try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* private mode */ }
  } else {
    try { lang = localStorage.getItem(LANG_KEY) || document.documentElement.lang || "en"; }
    catch (e) { lang = document.documentElement.lang || "en"; }
    if (lang !== "ar" && lang !== "en") lang = "en";
  }

  applyTheme();
  applyLang();
  initTabs();
  initConsole();

  $("lang").addEventListener("click", () => {
    lang = lang === "ar" ? "en" : "ar";
    applyLang();
    renderOverview();
    renderTimeframes();
    renderRisk();
    if (state.route === "services") renderServices();
  });
  $("theme").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* private mode */ }
    applyTheme();
  });
  $("refresh").addEventListener("click", refresh);

  go(location.hash.slice(1) || "overview");
  refresh();
  setInterval(refresh, REFRESH_MS);
}

/* Escape untrusted text before it goes anywhere near innerHTML. */
function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
