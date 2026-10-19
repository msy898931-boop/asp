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
    v_open: "مفتوح", v_closed: "مغلق", v_none: "لا شيء",
    /* market & trades */
    nav_market: "السوق والصفقات",
    nav_news: "الأخبار والذهب",
    nw_sub: "قراءة الأخبار العالمية وتأثيرها على الذهب لحظة بلحظة",
    nw_upcoming: "أحداث قادمة عالية التأثير",
    nw_upcoming_sub: "التقويم الاقتصادي · بالتوقيت العالمي",
    nw_released: "نتائج صدرت للتو",
    nw_released_sub: "الفعلي مقابل التقدير → الأثر على الذهب",
    nw_headlines: "عناوين الذهب",
    nw_headlines_sub: "آخر ما كُتب عن XAUUSD",
    nw_caveat: "قراءة ميكانيكية للأخبار: الفعلي مقابل التقدير، والأثر على الذهب مباشر مع الدولار فقط. أثر الأخبار على نتائج النظام ما انقاس بعد، فلا يُدَّعى حافة.",
    nw_window_ACTIVE: "نافذة خبر نشطة",
    nw_window_UPCOMING: "خبر مهم قريب",
    nw_window_CLEAR: "السوق خالي من الأخبار المهمة",
    nw_window_UNKNOWN: "حالة الأخبار غير معروفة",
    nw_minutes: "{n} د",
    nw_in_minutes: "بعد {n} د",
    nw_ago_minutes: "قبل {n} د",
    nw_empty_upcoming: "لا أحداث عالية التأثير في المدى القريب.",
    nw_empty_released: "لا نتائج صدرت حديثاً.",
    nw_empty_headlines: "لا عناوين متاحة الآن.",
    nw_impact_gold_pressure: "ضغط على الذهب",
    nw_impact_gold_support: "دعم للذهب",
    nw_impact_indirect: "الأثر على الذهب غير مباشر",
    nw_impact_none: "مطابق للتقدير — بلا مفاجأة",
    nw_impact_unknown: "بلا تقدير ولا سابق — ما في قراءة",
    nw_act: "الفعلي", nw_fc: "التقدير", nw_prev: "السابق",
    nw_prep_badge: "تحضير",
    nw_prep_band: "المدى المتوقّع",
    nw_prep_p90: "الحالات العنيفة",
    nw_prep_up: "كسر صعودي",
    nw_prep_down: "كسر هبوطي",
    nw_prep_note: "الاتجاه غير متوقّع — نتحضّر للحجم لا للجهة.",
    nw_vs_forecast: "مقابل التقدير", nw_vs_previous: "مقابل السابق",
    mk_sub: "الاتجاه وخطة الصفقة من محرّك القرار نفسه",
    mk_plan: "خطة الصفقة",
    mk_plan_wait: "بانتظار قرار مُقيَّم…",
    mk_plan_na: "لا خطة صفقة: الباكند لم يُعِد مستويات صالحة بعد.",
    mk_positions: "مراكز الظلّ المفتوحة",
    mk_outcomes: "آخر النتائج",
    mk_decision: "آخر قرار مُسجَّل",
    mk_decision_none: "لا قرارات مُسجَّلة في السجلّ بعد.",
    why_no_direction: "النظام والبنية ما اتفقوا على اتجاه — لا حاجة للدخول.",
    why_not_eligible: "النظام الحالي غير مؤهّل للدخول حسب السياسة.",
    why_risk_denied: "المحرّك رفض الصفقة: المخاطرة أو جودة السوق دون الحد.",
    why_awaiting: "لا قرار مُقيَّم من الباكند بعد.",
    why_locked: "الباكند محمي بمفتاح — أدخل المفتاح لعرض القرار.",
    dc_signal: "الإشارة", dc_time: "الوقت", dc_tf: "الإطار", dc_score: "التقييم",
    dc_ref: "السعر المرجعي", dc_last: "آخر سعر", dc_drift: "الانحراف",
    dc_outcome: "النتيجة", dc_data: "البيانات", dc_structure: "البنية",
    dc_eligibility: "الأهلية", dc_mode: "الوضع",
    mk_caveat: "هذا نظام ظلّي: المستويات والمراكز والنتائج كلها محاكاة، ولا يملك تفويض تنفيذ.",
    mk_score_note: "تقييم خام — ليس احتمالاً.",
    d_long: "شراء", d_short: "بيع", d_none: "لا اتجاه",
    d_up: "صاعد", d_down: "هابط", d_range: "عرضي",
    vl_HIGH: "مرتفع", vl_LOW: "منخفض", vl_NORMAL: "طبيعي",
    st_OPEN: "مفتوح", st_STOP: "وقف", st_TARGET: "هدف", st_TIMEOUT: "انتهت المدة",
    dq_VALID: "صالح", dq_DEGRADED: "متدهور", dq_INVALID: "غير صالح",
    dq_STALE: "قديم", dq_MISSING: "ناقص", dq_UNKNOWN: "مجهول",
    el_ELIGIBLE: "مؤهّل", el_NOT_ELIGIBLE: "غير مؤهّل",
    pl_entry: "الدخول", pl_stop: "الوقف", pl_target: "الهدف",
    pl_rr: "المخاطرة/العائد", pl_risk: "المخاطرة المقترحة",
    cp_symbol: "الأداة", cp_regime: "النظام", cp_h4: "اتجاه H4",
    cp_m15: "زناد M15", cp_vol: "التقلّب", cp_agreement: "توافق الإطارات",
    cp_confidence: "الثقة", cp_score: "التقييم",
    cp_calibrated: "احتمال مُعايَر", cp_uncalibrated: "احتمال غير مُعايَر",
    rg_TRENDING_UP: "صاعد", rg_TRENDING_DOWN: "هابط", rg_RANGING: "عرضي",
    rg_TREND_UP: "صاعد", rg_TREND_DOWN: "هابط", rg_RANGE: "عرضي",
    rg_VOLATILE: "متقلّب", rg_QUIET: "هادئ",
    pos_entry: "الدخول", pos_stop: "الوقف", pos_target: "الهدف",
    pos_lots: "اللوت", pos_risk: "المخاطرة", pos_pnl: "الربح/الخسارة",
    pos_r: "R", pos_tf: "الإطار", pos_reason: "السبب",
    pos_none_open: "لا مراكز ظلّ مفتوحة.",
    pos_none_outcomes: "لا نتائج مسجّلة بعد.",
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
    v_open: "open", v_closed: "closed", v_none: "none",
    nav_market: "Market & trades",
    nav_news: "News & gold",
    nw_sub: "A live read of world news and what it means for gold",
    nw_upcoming: "Upcoming high-impact events",
    nw_upcoming_sub: "Economic calendar · all times UTC",
    nw_released: "Just released",
    nw_released_sub: "Actual vs forecast → the gold read",
    nw_headlines: "Gold headlines",
    nw_headlines_sub: "The latest on XAUUSD",
    nw_caveat: "A mechanical news read: actual vs forecast, with the gold link asserted only for the dollar. The news effect on system results is not measured yet, so no edge is claimed.",
    nw_window_ACTIVE: "Blackout window active",
    nw_window_UPCOMING: "High-impact event near",
    nw_window_CLEAR: "Clear of high-impact events",
    nw_window_UNKNOWN: "News state unknown",
    nw_minutes: "{n} min",
    nw_in_minutes: "in {n} min",
    nw_ago_minutes: "{n} min ago",
    nw_empty_upcoming: "No high-impact events in the near term.",
    nw_empty_released: "Nothing released recently.",
    nw_empty_headlines: "No headlines available right now.",
    nw_impact_gold_pressure: "pressure on gold",
    nw_impact_gold_support: "support for gold",
    nw_impact_indirect: "gold link indirect",
    nw_impact_none: "inline with forecast — no surprise",
    nw_impact_unknown: "no forecast or previous — no read",
    nw_act: "Actual", nw_fc: "Forecast", nw_prev: "Previous",
    nw_prep_badge: "Prep",
    nw_prep_band: "Expected move",
    nw_prep_p90: "Violent tenth",
    nw_prep_up: "Break up",
    nw_prep_down: "Break down",
    nw_prep_note: "Direction is not predicted — we prepare for the size, not the side.",
    nw_vs_forecast: "vs forecast", nw_vs_previous: "vs previous",
    mk_sub: "Direction and the trade plan, straight from the decision engine",
    mk_plan: "Trade plan",
    mk_plan_wait: "waiting for an evaluated decision…",
    mk_plan_na: "No trade plan: the backend has not returned valid levels yet.",
    mk_positions: "Open shadow positions",
    mk_outcomes: "Recent outcomes",
    mk_decision: "Last recorded decision",
    mk_decision_none: "No decisions recorded in the ledger yet.",
    why_no_direction: "The regime and structure did not agree on a direction — nothing to enter.",
    why_not_eligible: "The current regime is not eligible for entry under policy.",
    why_risk_denied: "The engine denied the trade: risk or market quality below the bound.",
    why_awaiting: "No evaluated decision from the backend yet.",
    why_locked: "The backend is key-protected — enter the key to see the decision.",
    dc_signal: "Signal", dc_time: "Time", dc_tf: "TF", dc_score: "Score",
    dc_ref: "Reference price", dc_last: "Last price", dc_drift: "Drift",
    dc_outcome: "Outcome", dc_data: "Data", dc_structure: "Structure",
    dc_eligibility: "Eligibility", dc_mode: "Mode",
    mk_caveat: "This is a shadow system: levels, positions and outcomes are all simulated, and it holds no execution authority.",
    mk_score_note: "raw score — not a probability.",
    d_long: "LONG", d_short: "SHORT", d_none: "NONE",
    d_up: "UP", d_down: "DOWN", d_range: "RANGE",
    vl_HIGH: "HIGH", vl_LOW: "LOW", vl_NORMAL: "NORMAL",
    st_OPEN: "OPEN", st_STOP: "STOP", st_TARGET: "TARGET", st_TIMEOUT: "TIMEOUT",
    dq_VALID: "VALID", dq_DEGRADED: "DEGRADED", dq_INVALID: "INVALID",
    dq_STALE: "STALE", dq_MISSING: "MISSING", dq_UNKNOWN: "UNKNOWN",
    el_ELIGIBLE: "ELIGIBLE", el_NOT_ELIGIBLE: "NOT ELIGIBLE",
    pl_entry: "Entry", pl_stop: "Stop", pl_target: "Target",
    pl_rr: "R:R", pl_risk: "Suggested risk",
    cp_symbol: "Instrument", cp_regime: "Regime", cp_h4: "H4 bias",
    cp_m15: "M15 trigger", cp_vol: "Volatility", cp_agreement: "MTF agreement",
    cp_confidence: "Confidence", cp_score: "Score",
    cp_calibrated: "calibrated probability", cp_uncalibrated: "uncalibrated probability",
    rg_TRENDING_UP: "trending up", rg_TRENDING_DOWN: "trending down", rg_RANGING: "ranging",
    rg_TREND_UP: "trending up", rg_TREND_DOWN: "trending down", rg_RANGE: "ranging",
    rg_VOLATILE: "volatile", rg_QUIET: "quiet",
    pos_entry: "Entry", pos_stop: "Stop", pos_target: "Target",
    pos_lots: "Lots", pos_risk: "Risk", pos_pnl: "P/L",
    pos_r: "R", pos_tf: "TF", pos_reason: "Reason",
    pos_none_open: "No open shadow positions.",
    pos_none_outcomes: "No outcomes recorded yet.",
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
  market: null, signals: null, analysisLocked: false,
  news: null,
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
  [["market", T("nav_market")], ["news", T("nav_news")],
   ["timeframes", T("nav_timeframes")],
   ["services", T("nav_services")], ["audit", T("nav_audit")],
   ["risk", T("nav_risk")], ["console", T("nav_console")]
  ].forEach(([route, label]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "shortcut";
    b.textContent = label;
    b.addEventListener("click", () => go(route));
    sc.appendChild(b);
  });
}

/* ── panel: market & trades ─────────────────────────────────────────── */

/* Map a backend direction word to a tone and a spoken label. The backend
   returns LONG / SHORT / NONE / UP / DOWN / RANGE / UNKNOWN — every one of
   these is passed through, and anything unrecognised reads as "unknown"
   rather than being guessed into a side. */
function dirClass(word) {
  const w = String(word || "").toUpperCase();
  if (w === "LONG" || w === "UP" || w === "TREND_UP" || w === "BULLISH") return "up";
  if (w === "SHORT" || w === "DOWN" || w === "TREND_DOWN" || w === "BEARISH") return "down";
  return "flat";
}

function dirLabel(word) {
  const w = String(word || "").toUpperCase();
  if (w === "LONG" || w === "SHORT" || w === "NONE") return T("d_" + w.toLowerCase());
  if (w === "UP" || w === "BULLISH") return T("d_up");
  if (w === "DOWN" || w === "BEARISH") return T("d_down");
  if (w === "RANGE") return T("d_range");
  if (!w) return T("d_none");
  return word; // an unrecognised word is shown as-is, never guessed into a side
}

function regimeLabel(word) {
  const key = "rg_" + String(word || "").toUpperCase();
  return STRINGS[lang][key] || STRINGS.en[key] || (word || null);
}

/* The volatility band is its own vocabulary (HIGH / LOW / NORMAL / UNKNOWN). */
function volLabel(word) {
  const w = String(word || "").toUpperCase();
  const key = "vl_" + w;
  return STRINGS[lang][key] || STRINGS.en[key] || (word || null);
}

/* A position/outcome state word (OPEN / STOP / TARGET / TIMEOUT). */
function stateLabel(word) {
  const w = String(word || "").toUpperCase();
  const key = "st_" + w;
  return STRINGS[lang][key] || STRINGS.en[key] || (word || null);
}

/* A data-quality word (VALID / DEGRADED / STALE / MISSING …). */
function dataLabel(word) {
  const w = String(word || "").toUpperCase();
  const key = "dq_" + w;
  return STRINGS[lang][key] || STRINGS.en[key] || (word || null);
}

/* An eligibility word (ELIGIBLE / NOT_ELIGIBLE). */
function eligLabel(word) {
  const w = String(word || "").toUpperCase();
  const key = "el_" + w;
  return STRINGS[lang][key] || STRINGS.en[key] || (word || null);
}

/* Why the panel is showing no trade plan. This is a heuristic read of the
   context the backend already sent, never a fabricated reason: it names the
   gate the values are consistent with and nothing more. */
function whyNoTrade(a) {
  if (a.locked) return T("why_locked");
  const ctx = a.context || {};
  const sig = a.signal || {};
  if (a.available !== true) return T("why_awaiting");
  const w = String(sig.direction || "NONE").toUpperCase();
  if (w === "NONE") {
    const r = String(ctx.regime || "UNKNOWN").toUpperCase();
    if (r === "RANGING" || r === "VOLATILE" || r === "UNKNOWN")
      return T("why_not_eligible");
    return T("why_no_direction");
  }
  return T("why_risk_denied");
}

function chip(label, value, cls) {
  const el = document.createElement("span");
  el.className = "chip" + (cls ? " " + cls : "");
  const l = document.createElement("span");
  l.textContent = label;
  const b = document.createElement("b");
  b.textContent = value === null || value === undefined || value === "" ? T("v_na") : String(value);
  el.append(l, b);
  return el;
}

function planCell(label, value, cls) {
  const el = document.createElement("div");
  el.className = "plan-cell";
  const l = document.createElement("span");
  l.className = "plan-label";
  l.textContent = label;
  const v = document.createElement("span");
  v.className = "plan-value" + (cls ? " " + cls : "") +
    (value === null || value === undefined ? " unknown" : "");
  v.textContent = value === null || value === undefined ? T("v_na") : String(value);
  el.append(l, v);
  return el;
}

function posCard(p, closed) {
  const card = document.createElement("div");
  card.className = "pos-card";

  const head = document.createElement("div");
  head.className = "pos-head";
  const dir = document.createElement("span");
  dir.className = "pos-dir " + dirClass(p.direction);
  dir.textContent = dirLabel(p.direction);
  const st = document.createElement("span");
  const stWord = closed ? (p.exit_state || p.outcome_class || null) : p.state;
  st.className = "pos-state " + tone(stWord);
  st.textContent = stWord ? stateLabel(stWord) : T("v_na");
  head.append(dir, st);

  const row = document.createElement("div");
  row.className = "pos-row";
  const pair = (label, value, cls) => {
    const s = document.createElement("span");
    s.textContent = label + " ";
    const b = document.createElement("b");
    b.className = cls || "";
    b.textContent = value === null || value === undefined ? T("v_na") : String(value);
    s.appendChild(b);
    return s;
  };
  row.append(
    pair(T("pos_entry"), num(p.entry_price)),
    pair(T("pos_stop"), num(p.stop_price)),
    pair(T("pos_target"), num(p.target_price)),
    pair(T("pos_lots"), num(p.lots, 3)),
  );
  if (closed) {
    const r = num(p.realized_r);
    const pnl = num(p.realized_pnl);
    row.append(
      pair(T("pos_r"), r === null ? null : (Number(p.realized_r) >= 0 ? "+" : "") + r,
           Number(p.realized_r) >= 0 ? "up" : "down"),
      pair(T("pos_pnl"), pnl === null ? null : (Number(p.realized_pnl) >= 0 ? "+" : "") + pnl,
           Number(p.realized_pnl) >= 0 ? "up" : "down"),
      pair(T("pos_tf"), p.timeframe || null),
    );
  } else if (p.timeframe) {
    row.append(pair(T("pos_tf"), p.timeframe));
  }

  card.append(head, row);
  return card;
}

function renderMarket() {
  const a = state.market || {};
  const ctx = a.context || {};
  const sig = a.signal || {};
  const levels = a.levels || {};
  // Availability is the document's own presence: a 200 with a context is a real
  // read even when it carries no direction; null is a locked or dead backend.
  a.available = state.market != null;
  a.locked = state.analysisLocked === true;

  // Direction hero. `signal.direction` is the engine's own word; when the
  // analysis surface is absent it is null, which reads as "unavailable" — not
  // as a decision of NONE.
  const dirWord = sig.direction || null;
  const hero = $("mk-hero");
  hero.className = "dir-hero " + dirClass(dirWord);
  $("mk-dir").textContent = dirWord ? dirLabel(dirWord) : T("v_na");
  $("mk-dir").className = "direction " + dirClass(dirWord);

  // Probability is shown ONLY when the backend marks it calibrated (RULE C);
  // otherwise the raw score is named as a score, never dressed as a probability.
  let meta;
  if (sig.probability_calibrated === true && sig.probability != null) {
    meta = T("cp_calibrated") + " · " + num(sig.probability, 3);
  } else if (sig.score != null) {
    meta = T("cp_score") + " " + num(sig.score, 3) + " · " + T("mk_score_note");
  } else {
    meta = T("v_na");
  }
  $("mk-prob").textContent = meta;

  const chips = $("mk-chips");
  chips.textContent = "";
  chips.append(
    chip(T("cp_symbol"), a.symbol || null),
    chip(T("cp_regime"), regimeLabel(ctx.regime), tone(ctx.regime)),
    chip(T("cp_h4"), dirLabel(ctx.h4_bias), dirClass(ctx.h4_bias)),
    chip(T("cp_m15"), dirLabel(ctx.m15_trigger), dirClass(ctx.m15_trigger)),
    chip(T("cp_vol"), volLabel(ctx.volatility_state),
         ctx.volatility_state === "HIGH" ? "warn" : null),
    chip(T("cp_agreement"), ctx.mtf_agreement),
    chip(T("cp_confidence"), num(sig.confidence, 2)),
  );

  // Trade plan: entry / stop / target come from the live risk proposal. When
  // the backend returns null levels, the panel says so instead of drawing a
  // plausible plan from nothing.
  const grid = $("mk-levels");
  grid.textContent = "";
  const havePlan = levels.entry != null || levels.stop_loss != null ||
                   levels.take_profit != null;
  $("mk-plan-note").hidden = havePlan;
  if (havePlan) {
    grid.append(
      planCell(T("pl_entry"), num(levels.entry), dirClass(dirWord)),
      planCell(T("pl_stop"), num(levels.stop_loss), "warn"),
      planCell(T("pl_target"), num(levels.take_profit), dirClass(dirWord)),
      planCell(T("pl_rr"), num(levels.reward_risk, 2)),
      planCell(T("pl_risk"), levels.suggested_risk_pct == null
        ? null : num(levels.suggested_risk_pct, 2) + "%"),
    );
  } else {
    // No plan: name the gate the engine is sitting on instead of leaving a
    // blank "waiting" line the operator cannot act on.
    $("mk-plan-note").textContent = whyNoTrade(a);
  }

  // Last recorded decision, straight from the prediction ledger. This is the
  // one view that survives a restart with no live context: it shows the frozen
  // contract values (score is a score, probability stays N/A per RULE C).
  const s = state.signals;
  const dl = $("mk-decision");
  dl.textContent = "";
  $("mk-decision-note").hidden = !!(s && s.available);
  if (s && s.available) {
    const drift = (s.last_price != null && s.reference_price != null)
      ? num(Number(s.last_price) - Number(s.reference_price)) : null;
    readoutRow(dl, T("cp_symbol"), s.symbol || null);
    readoutRow(dl, T("dc_tf"), s.trigger_timeframe || null);
    readoutRow(dl, T("dc_signal"), s.signal ? dirLabel(s.signal) : null, dirClass(s.signal));
    readoutRow(dl, T("dc_time"), s.trigger_time != null
      ? fmtEpochUTC(Math.floor(Number(s.trigger_time) / 1000)) : null);
    readoutRow(dl, T("dc_score"), num(s.score, 3));
    readoutRow(dl, T("cp_confidence"), num(s.confidence, 2));
    readoutRow(dl, T("dc_ref"), num(s.reference_price));
    readoutRow(dl, T("dc_last"), num(s.last_price));
    readoutRow(dl, T("dc_drift"), drift, drift == null ? null : (drift >= 0 ? "ok" : "bad"));
    readoutRow(dl, T("dc_outcome"), yn(s.has_outcome));
    readoutRow(dl, T("dc_data"), dataLabel(s.data_state), tone(s.data_state));
    readoutRow(dl, T("dc_structure"), dirLabel(s.structure_state), dirClass(s.structure_state));
    readoutRow(dl, T("dc_eligibility"), eligLabel(s.eligibility_state), tone(s.eligibility_state));
    readoutRow(dl, T("dc_mode"), s.system_mode || null);
  }

  // Positions and outcomes come straight from the shadow simulator.
  const positions = Array.isArray(state.positions) ? state.positions : [];
  const open = positions.filter((p) => p && p.state === "OPEN");
  const pl = $("mk-positions");
  pl.textContent = "";
  if (!open.length) {
    const e = document.createElement("div");
    e.className = "pos-empty";
    e.textContent = T("pos_none_open");
    pl.appendChild(e);
  } else {
    open.forEach((p) => pl.appendChild(posCard(p, false)));
  }

  const outcomes = Array.isArray(state.outcomes) ? state.outcomes : [];
  const ol = $("mk-outcomes");
  ol.textContent = "";
  if (!outcomes.length) {
    const e = document.createElement("div");
    e.className = "pos-empty";
    e.textContent = T("pos_none_outcomes");
    ol.appendChild(e);
  } else {
    outcomes.slice(-5).reverse().forEach((o) => ol.appendChild(posCard(o, true)));
  }
}

/* ── panel: news & gold ─────────────────────────────────────────────── */

/* A plain text badge (no label/value split), for country tags and reads. */
function nwChip(text, cls) {
  const el = document.createElement("span");
  el.className = "chip" + (cls ? " " + cls : "");
  el.textContent = text;
  return el;
}

/* A titled list row: a headline or an event. `meta` is the small right-hand
   line, `chips` an optional list of [text, cls]. */
function nwRow(title, meta, chips) {
  const card = document.createElement("div");
  card.className = "nw-card";
  const head = document.createElement("div");
  head.className = "nw-card-head";
  const t = document.createElement("span");
  t.className = "nw-card-title";
  t.textContent = title || T("v_na");
  const m = document.createElement("span");
  m.className = "nw-card-meta";
  m.textContent = meta || "";
  head.append(t, m);
  card.appendChild(head);
  if (chips && chips.length) {
    const row = document.createElement("div");
    row.className = "nw-chips";
    chips.forEach(([text, cls]) => {
      if (text) row.appendChild(nwChip(text, cls || null));
    });
    card.appendChild(row);
  }
  return card;
}

/* The mechanical gold read for one released event, as a short labelled chip. */
function nwImpact(r) {
  if (r.surprise === "unknown") return [T("nw_impact_unknown"), "warn"];
  if (r.currency_dir === 0) return [T("nw_impact_none"), null];
  if (!r.certain) return [T("nw_impact_indirect"), null];
  return r.gold_dir < 0
    ? [T("nw_impact_gold_pressure"), "down"]
    : [T("nw_impact_gold_support"), "up"];
}

function nwEmpty(el, text) {
  const e = document.createElement("div");
  e.className = "nw-empty";
  e.textContent = text;
  el.appendChild(e);
}

function renderNews() {
  const d = state.news || {};
  const win = d.window || {};
  const box = $("nw-window");

  // Window hero: the state word plus the Arabic reason the engine produced.
  const stateWord = String(win.state || "UNKNOWN").toUpperCase();
  box.className = "nw-window " + (stateWord === "ACTIVE" ? "bad"
    : stateWord === "UPCOMING" ? "warn" : stateWord === "CLEAR" ? "ok" : "unknown");
  $("nw-window-state").textContent = T("nw_window_" + stateWord);
  $("nw-window-text").textContent = win.reason || (win.event
    ? (win.event.title_ar || win.event.title) : "");
  $("nw-window-src").textContent = d.available === false
    ? (d.reason || "news.json") : "news.json";

  // Upcoming schedule.
  const up = Array.isArray(d.upcoming) ? d.upcoming : [];
  const ul = $("nw-upcoming");
  ul.textContent = "";
  if (!up.length) {
    nwEmpty(ul, T("nw_empty_upcoming"));
  } else {
    up.forEach((e) => {
      const when = e.in_minutes != null
        ? T("nw_in_minutes").replace("{n}", e.in_minutes)
        : (e.at_utc ? fmtEpochUTC(e.at_utc) : "");
      ul.appendChild(nwRow(
        e.title_ar || e.title,
        [e.country, when].filter(Boolean).join(" · "),
        [[e.country || "", null]],
      ));
    });
  }

  // Just-released events with the gold read.
  const rel = Array.isArray(d.released) ? d.released : [];
  const rl = $("nw-released");
  rl.textContent = "";
  if (!rel.length) {
    nwEmpty(rl, T("nw_empty_released"));
  } else {
    rel.forEach((r) => {
      const when = r.minutes_ago != null
        ? T("nw_ago_minutes").replace("{n}", r.minutes_ago) : "";
      const vals = [
        T("nw_act") + " " + (r.actual == null ? "—" : r.actual),
        T("nw_fc") + " " + (r.forecast == null ? "—" : r.forecast),
        T("nw_prev") + " " + (r.previous == null ? "—" : r.previous),
      ].join(" · ");
      const [impact, cls] = nwImpact(r);
      rl.appendChild(nwRow(
        r.title_ar || r.title,
        [r.country, when].filter(Boolean).join(" · "),
        [[vals, null], [impact, cls]],
      ));
    });
  }

  // Headlines.
  const hl = Array.isArray(d.headlines) ? d.headlines : [];
  const hll = $("nw-headlines");
  hll.textContent = "";
  if (!hl.length) {
    nwEmpty(hll, T("nw_empty_headlines"));
  } else {
    hl.forEach((h) => {
      const src = h.source || "";
      // The RSS title often already ends with " - Source"; drop the repeat
      // rather than printing the source twice.
      const title = (src && h.title.endsWith(" - " + src))
        ? h.title.slice(0, -(src.length + 3)) : h.title;
      hll.appendChild(nwRow(title, src || h.published || ""));
    });
  }

  renderPrep();
}

/* The pre-news prep plan. Hidden unless a high-impact release is inside the
   prep horizon, because an empty bracket is noise. It never names a side. */
function renderPrep() {
  const box = $("nw-prep");
  const f = state.forecast || {};
  const plan = f.plan || null;
  const ev = f.event || null;

  if (!plan || !ev || f.available === false) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  $("nw-prep-src").textContent = f.available === false
    ? (f.reason || "forecast.json") : "forecast.json";
  $("nw-prep-title").textContent = [
    (ev.title_ar || ev.title),
    ev.country ? "(" + ev.country + ")" : "",
    plan.in_minutes != null
      ? T("nw_in_minutes").replace("{n}", plan.in_minutes) : "",
  ].filter(Boolean).join(" · ");

  // The measured band and the two break levels, side by side.
  const lv = plan.levels || {};
  const mv = plan.expected_move || {};
  const band = $("nw-prep-band");
  band.textContent = "";
  const cells = [
    [T("nw_prep_band"), mv.median != null ? "~$" + mv.median : "—"],
    [T("nw_prep_p90"), mv.p90 != null ? "~$" + mv.p90 : "—"],
    [T("nw_prep_up"), lv.break_up != null ? lv.break_up : "—"],
    [T("nw_prep_down"), lv.break_down != null ? lv.break_down : "—"],
  ];
  cells.forEach(([label, value]) => {
    const c = document.createElement("div");
    c.className = "nw-prep-cell";
    const l = document.createElement("span");
    l.className = "nw-prep-cell-label";
    l.textContent = label;
    const v = document.createElement("span");
    v.className = "nw-prep-cell-value";
    v.textContent = value;
    c.appendChild(l);
    c.appendChild(v);
    band.appendChild(c);
  });

  const ol = $("nw-prep-steps");
  ol.textContent = "";
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  steps.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s;
    ol.appendChild(li);
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

const ROUTES = ["overview", "market", "news", "timeframes", "services", "audit", "risk", "console"];

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
  if (route === "market") renderMarket();
  if (route === "news") renderNews();
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
    ["positions", "shadow/positions"], ["outcomes", "shadow/outcomes"],
    ["signals", "signals/latest"],
  ];
  const settled = await Promise.allSettled(specs.map(([, path]) => api(path)));

  let firstError = null;
  let anyOk = false;
  let analysisLocked = false;
  settled.forEach((res, i) => {
    const key = specs[i][0];
    if (res.status === "fulfilled") {
      state[key] = res.value;
      anyOk = true;
    } else {
      if (!firstError) firstError = res.reason;
      if (key === "analysis" && res.reason && res.reason.status === 401)
        analysisLocked = true;
      state[key] = null;
    }
  });
  // The market panel reads the same analysis document as the overview; keep one
  // fetch and one truth rather than calling the endpoint twice.
  state.market = state.analysis;
  state.analysisLocked = analysisLocked;

  // News is its own origin route and its own concern: it reads public feeds,
  // not the backend, so a dead backend must not blank it. Fetched once, before
  // the early return below, and best-effort throughout.
  try { state.news = await getJson("news.json", 25000); } catch (e) { state.news = null; }
  // The prep plan reads the calendar too, and like the news digest it must
  // survive a dead backend. Fetched here so both faces agree on one event.
  try { state.forecast = await getJson("forecast.json", 25000); }
  catch (e) { state.forecast = null; }

  if (!anyOk) {
    // Name the real failure. A 401 is a locked backend, not a dead one, and
    // saying "unreachable" for a gate would send the operator down the wrong
    // path. Anything else is a genuine reachability problem.
    const status = firstError && firstError.status;
    if (status === 401) banner("degraded", T("banner_locked"));
    else banner("offline", T("banner_offline"));
    renderOverview();
    renderMarket();
    renderNews();
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
  renderMarket();
  renderNews();
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
    renderMarket();
    renderNews();
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
