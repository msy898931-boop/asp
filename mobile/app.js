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
    ai_event: "Key event", ai_plan: "Trade plan",
    ai_plan_bias: "Bias", ai_plan_trigger: "Trigger", ai_plan_entry: "Entry",
    ai_plan_stop: "Stop", ai_plan_target: "Target", ai_plan_note: "Note",
    ai_headlines: "Headlines", ai_events: "High-impact events ahead",
    ai_review: "Self-review", ai_review_none: "no scored calls yet",
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
    perf: "Performance",
    overview: "Overview", data: "Data",
    tab_tracker: "Signals",
    tracker_title: "Signal tracker",
    tracker_warn: "Replayed from closed bars. Unvalidated and not scored for long enough to trust. Not financial advice.",
    tracker_insufficient: "Not enough scored trades yet — no win rate is stated.",
    tracker_hold: "Hold",
    tracker_winrate: "Win rate",
    tracker_net: "Net (after spread)",
    tracker_gross: "Gross R",
    tracker_count: "Signals", tracker_wins: "Wins", tracker_losses: "Losses",
    tracker_open: "live now",
    tracker_empty: "No trade ideas in the current window.",
    tracker_warming: "Connecting to the signal tracker…",
    now_trade: "Trade on this bar",
    now_no_trade: "No trade on this bar",
    now_reason: "Reason",
    now_read: "Market read",
    now_plan: "On your account",
    now_plan_warn: "Requested risk is below gold's smallest lot (1 oz). Smallest possible position: 1 oz = {r} ({p}% of the account).",
    now_plan_ok: "Size {oz} oz · risk {r} ({p}%).",
    now_open: "Open now",
    tracker_spread: "spread {c}/oz", tracker_horizon: "{h}h horizon",
    trk_open: "Open", trk_target: "Target", trk_stop: "Stop", trk_timeout: "Timed out",
  },
  ar: {
    title_sub: "الذهب · وضع الظل",
    tab_status: "الحالة", tab_signal: "السوق", tab_chart: "الشارت",
    tab_conn: "البيانات", tab_bridge: "الجسر", tab_ai: "الذكاء",
    ai_title: "المستشار الذكي",
    ai_warn: "رأي ثانٍ من نموذج خارجي. خارج النظام المُتحقَّق منه، وغير مُقيَّم. ليس نصيحة مالية.",
    ai_price: "السعر اللحظي", ai_engine: "قراءة المحرك",
    ai_news: "استخدم الأخبار", ai_model: "النموذج", ai_status: "الحالة",
    ai_event: "الخبر المهم", ai_plan: "خطة الصفقة",
    ai_plan_bias: "الاتجاه", ai_plan_trigger: "الشرط", ai_plan_entry: "الدخول",
    ai_plan_stop: "الوقف", ai_plan_target: "الهدف", ai_plan_note: "ملاحظة",
    ai_headlines: "العناوين", ai_events: "أخبار عالية التأثير قادمة",
    ai_review: "المراجعة الذاتية", ai_review_none: "ما في نتائج مُقيَّمة بعد",
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
    perf: "شفافية الأداء",
    overview: "نظرة عامة", data: "البيانات",
    tab_tracker: "الإشارات",
    tracker_title: "متتبّع الإشارات",
    tracker_warn: "مُعاد تشغيله من الشموع المغلقة. غير مُتحقَّق منه ولم يُقيَّس زمنًا كافيًا. ليس نصيحة مالية.",
    tracker_insufficient: "لا صفقات محسومة كافية بعد — لا تُذكر نسبة إصابة.",
    tracker_hold: "قيد الاحتفاظ",
    tracker_winrate: "نسبة الإصابة",
    tracker_net: "الصافي (بعد السبريد)",
    tracker_gross: "إجمالي R",
    tracker_count: "الإشارات", tracker_wins: "أصابت", tracker_losses: "خسرت",
    tracker_open: "مفتوحة الآن",
    tracker_empty: "لا إشارات في النافذة الحالية.",
    tracker_warming: "جارٍ الاتصال بمتتبّع الإشارات…",
    now_trade: "صفقة على هذه الشمعة",
    now_no_trade: "لا صفقة على هذه الشمعة",
    now_reason: "السبب",
    now_read: "قراءة السوق",
    now_plan: "على حسابك",
    now_plan_warn: "المخاطرة المطلوبة أقل من أصغر حجم للذهب (1 أونصة). أصغر صفقة ممكنة: 1 أونصة = {r} ({p}% من الحساب).",
    now_plan_ok: "الحجم {oz} أونصة · مخاطرة {r} ({p}%).",
    now_open: "مفتوحة الآن",
    tracker_spread: "سبريد {c}/أونصة", tracker_horizon: "أفق {h} ساعة",
    trk_open: "مفتوحة", trk_target: "هدف", trk_stop: "وقف", trk_timeout: "انتهت المدّة",
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
let swReg = null;   // the service worker registration, used to show notifications
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

    const hl = $("ai-headlines");
    const headlines = (snap.news || []).slice(0, 5);
    hl.innerHTML = "";
    $("ai-headlines-title").hidden = !headlines.length;
    hl.hidden = !headlines.length;
    for (const h of headlines) {
      const li = document.createElement("li");
      li.textContent = h.title;
      hl.appendChild(li);
    }

    const evs = $("ai-events");
    const events = (snap.events || []).slice(0, 5);
    evs.innerHTML = "";
    $("ai-events-title").hidden = !events.length;
    evs.hidden = !events.length;
    for (const e of events) {
      const li = document.createElement("li");
      const when = e.in_hours != null ? ` · ${e.in_hours}h` : "";
      const fc = e.forecast ? ` · ${T("ai_plan_note")}: ${e.forecast}` : "";
      li.textContent = `${e.country} · ${e.title_ar || e.title}${when}${fc}`;
      evs.appendChild(li);
    }

    const ev = $("ai-event");
    if (op && op.event) {
      ev.hidden = false;
      ev.textContent = `${T("ai_event")}: ${op.event}`;
    } else {
      ev.hidden = true;
    }

    const planEl = $("ai-plan");
    planEl.innerHTML = "";
    if (op && op.plan) {
      planEl.hidden = false;
      const title = document.createElement("h3");
      title.className = "ai-sub";
      title.textContent = T("ai_plan");
      planEl.appendChild(title);
      const rows = [
        ["ai_plan_bias", op.plan.bias], ["ai_plan_trigger", op.plan.trigger],
        ["ai_plan_entry", op.plan.entry], ["ai_plan_stop", op.plan.stop],
        ["ai_plan_target", op.plan.target], ["ai_plan_note", op.plan.note],
      ];
      for (const [k, v] of rows) {
        if (!v) continue;
        const div = document.createElement("div");
        const dt = document.createElement("dt");
        dt.textContent = T(k);
        const dd = document.createElement("dd");
        dd.textContent = v;
        div.appendChild(dt);
        div.appendChild(dd);
        planEl.appendChild(div);
      }
    } else {
      planEl.hidden = true;
    }

    const rv = $("ai-review");
    const review = a.review || {};
    if (review.scored) {
      rv.hidden = false;
      const hit = Math.round((review.hit_rate || 0) * 100);
      rv.textContent = `${T("ai_review")}: ${review.correct}/${review.scored} (${hit}%)`;
    } else {
      rv.hidden = false;
      rv.textContent = `${T("ai_review")}: ${T("ai_review_none")}`;
    }

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
    for (const id of ["ai-headlines", "ai-events", "ai-plan", "ai-event",
                      "ai-review", "ai-headlines-title", "ai-events-title"]) {
      const el = $(id);
      el.hidden = true;
      if (el.tagName === "UL" || el.tagName === "DIV") el.innerHTML = "";
    }
    setStatus(T("ai_unavail"));
  }
}

/* ── signal tracker ─────────────────────────────────────────────────── */

const TRK_OUTCOME_KEY = {
  OPEN: "trk_open", TARGET: "trk_target", STOP: "trk_stop",
  TIMEOUT: "trk_timeout",
};

function trkStat(label, value, cls) {
  return `<div class="trk-stat"><span class="trk-stat-label">${label}</span>` +
    `<span class="trk-stat-value ${cls || ""}">${value}</span></div>`;
}

const NOW_REASON = {
  en: {
    NO_DATA: "not enough bars yet",
    STRUCTURE_INCOMPLETE: "price structure is not complete",
    VOLATILE: "volatility is too high — excluded from trading",
    NO_EDGE: "no clear edge (the votes disagree)",
    IN_POSITION: "an idea is already open",
    COOLDOWN: "cooling down after the last trade",
    JUST_RESOLVED: "the previous trade just closed",
  },
  ar: {
    NO_DATA: "ما في شموع كافية بعد",
    STRUCTURE_INCOMPLETE: "الهيكل السعري غير مكتمل",
    VOLATILE: "التقلّب عالٍ — مستثنى من التداول",
    NO_EDGE: "ما في حافة واضحة (الإشارات متعارضة)",
    IN_POSITION: "في صفقة مفتوحة أصلاً",
    COOLDOWN: "فترة تهدئة بعد آخر صفقة",
    JUST_RESOLVED: "الصفقة السابقة انتهت للتوّ",
  },
};

const NOW_REGIME = {
  en: { TRENDING_UP: "uptrend", TRENDING_DOWN: "downtrend", RANGING: "ranging",
        QUIET: "quiet", VOLATILE: "volatile", UNKNOWN: "unknown" },
  ar: { TRENDING_UP: "صعود", TRENDING_DOWN: "هبوط", RANGING: "عرضي",
        QUIET: "هادئ", VOLATILE: "متذبذب", UNKNOWN: "غير معروف" },
};

const NOW_BIAS = {
  en: { BULLISH: "bullish", BEARISH: "bearish", RANGE: "range",
        INCOMPLETE: "incomplete" },
  ar: { BULLISH: "صاعد", BEARISH: "هابط", RANGE: "عرضي",
        INCOMPLETE: "غير مكتمل" },
};

function renderNow(status) {
  const el = $("trk-now");
  if (!el) return;
  if (!status || !status.decision) { el.innerHTML = ""; el.className = "trk-now"; return; }
  const map = (m, k) => (m[lang] || m.en)[k] || k;
  if (status.decision === "TRADE") {
    const s = status.signal || {};
    const cls = s.direction === "LONG" ? "up" : "down";
    const read = status.read || {};
    const plan = status.plan;
    let planHtml = "";
    if (plan) {
      planHtml = plan.below_min_lot
        ? `<div class="trk-now-plan warn">${fmt(T("now_plan_warn"),
            { r: "$" + num(plan.min_risk_usd), p: num(plan.min_risk_pct) })}</div>`
        : `<div class="trk-now-plan">${fmt(T("now_plan_ok"),
            { oz: num(plan.position_oz, 2), r: "$" + num(plan.risk_usd),
              p: num(plan.risk_pct) })}</div>`;
    }
    el.className = "trk-now trade " + cls;
    el.innerHTML =
      `<div class="trk-now-head"><span class="trk-dir ${cls}">${T("now_trade")} · ${s.direction}</span>` +
      `<span class="trk-r">${num(s.entry)}</span></div>` +
      `<div class="trk-now-body">${T("entry")} ${num(s.entry)} · ${T("stop")} ${num(s.stop)} · ${T("target")} ${num(s.target)}</div>` +
      `<div class="trk-now-reason">${T("now_reason")}: ${map(NOW_REGIME, read.regime)} · ${map(NOW_BIAS, read.bias)} · RSI ${num(read.rsi, 1)}</div>` +
      planHtml;
    return;
  }
  const read = status.read || {};
  const held = status.open;
  let body = "";
  if (read.close != null) {
    body = `<div class="trk-now-body">${T("now_read")}: ${map(NOW_REGIME, read.regime)} · ` +
      `${map(NOW_BIAS, read.bias)} · RSI ${num(read.rsi, 1)} · ${num(read.close)}</div>`;
  }
  let heldHtml = "";
  if (held) {
    const hcls = held.direction === "LONG" ? "up" : "down";
    heldHtml = `<div class="trk-now-body">${T("now_open")}: ` +
      `<span class="trk-dir ${hcls}">${held.direction}</span> ${num(held.entry)} · ` +
      `${T("stop")} ${num(held.stop)} · ${T("target")} ${num(held.target)}</div>`;
  }
  el.className = "trk-now";
  el.innerHTML =
    `<div class="trk-now-head"><span>${T("now_no_trade")}</span></div>` +
    `<div class="trk-now-reason">${T("now_reason")}: ${map(NOW_REASON, status.reason)}</div>` +
    body + heldHtml;
}

function renderTracker(data, warming) {
  const statsEl = $("trk-stats");
  const listEl = $("trk-list");
  const noteEl = $("trk-note");
  if (warming) {
    $("trk-now").innerHTML = "";
    statsEl.innerHTML = "";
    listEl.innerHTML = `<p class="panel-note">${T("tracker_warming")}</p>`;
    noteEl.textContent = "";
    return;
  }
  renderNow(data && data.last_bar);
  if (!data || typeof data !== "object") {
    statsEl.innerHTML = "";
    listEl.innerHTML = "";
    noteEl.textContent = T("tracker_empty");
    return;
  }
  const s = data.summary || {};
  const netCls = (s.total_r_net || 0) > 0 ? "up" : (s.total_r_net || 0) < 0 ? "down" : "";
  statsEl.innerHTML =
    trkStat(T("tracker_count"), String(s.total_signals ?? 0)) +
    trkStat(T("tracker_wins"), String(s.wins ?? 0), "up") +
    trkStat(T("tracker_losses"), String(s.losses ?? 0), "down") +
    trkStat(T("tracker_net"), fmt("{v}R", { v: num(s.total_r_net, 2) }), netCls) +
    trkStat(T("tracker_gross"), fmt("{v}R", { v: num(s.total_r_gross, 2) })) +
    trkStat(T("tracker_winrate"),
      s.sufficient ? fmt("{p}%", { p: s.win_rate }) : "—");

  const bits = [];
  if (data.cost_usd != null) bits.push(T("tracker_spread").replace("{c}", num(data.cost_usd)));
  if (data.horizon_bars != null) bits.push(
    T("tracker_horizon").replace("{h}", num((data.horizon_bars * 15) / 60, 1)));
  bits.push(s.sufficient ? "" : T("tracker_insufficient"));
  noteEl.textContent = bits.filter(Boolean).join(" · ");

  const rows = (data.signals || []).slice().reverse();
  if (!rows.length) {
    listEl.innerHTML = `<p class="panel-note">${T("tracker_empty")}</p>`;
    return;
  }
  listEl.innerHTML = rows.map((r) => {
    const dirCls = r.direction === "LONG" ? "up" : "down";
    const oc = TRK_OUTCOME_KEY[r.outcome] || r.outcome;
    const rCls = r.r_net == null ? "" : r.r_net >= 0 ? "up" : "down";
    const rTxt = r.r_net == null ? "—" : `${r.r_net >= 0 ? "+" : ""}${num(r.r_net, 2)}R`;
    return `<div class="trk-row">` +
      `<div class="trk-top">` +
      `<span class="trk-dir ${dirCls}">${r.direction}</span>` +
      `<span class="trk-r ${rCls}">${rTxt}</span></div>` +
      `<div class="trk-levels">${T("entry")} ${num(r.entry)} · ` +
      `${T("stop")} ${num(r.stop)} · ${T("target")} ${num(r.target)}</div>` +
      `<div class="trk-sub">` +
      `<span class="trk-oc ${rCls}">${T(oc)}</span>` +
      `<span class="trk-meta">${r.regime || ""} · RSI ${num(r.rsi, 0)}</span>` +
      `</div></div>`;
  }).join("");
}

async function loadTracker(attempt = 0) {
  try {
    const env = await getJson("tracker/v1/signals");
    renderTracker(env && env.tracker);
  } catch (err) {
    // The tracker runs as a sidecar that comes up a few seconds after the
    // container. Retry briefly before falling back to the empty state, so a
    // cold start reads as "connecting", not "no signals".
    if (attempt < 3) {
      renderTracker(null, true);
      await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
      return loadTracker(attempt + 1);
    }
    renderTracker(null);
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
    showNotification("ASTRA", text, "astra-signal");
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
    showNotification(title, body, "astra-level");
  }
  if (navigator.vibrate) navigator.vibrate([40, 60, 40]);
}

/* Android Chrome throws when a page builds a Notification directly, so route
   the message through the service worker: `showNotification` is the one path
   that works on both desktop and Android, and it survives the page being
   backgrounded. Fall back to the constructor where there is no worker. */
function showNotification(title, body, tag) {
  try {
    if (swReg && swReg.active && swReg.active.postMessage) {
      swReg.active.postMessage({ type: "notify", title, body, tag });
      return;
    }
  } catch (e) { /* fall through to the constructor */ }
  try { new Notification(title, { body, tag }); } catch (e) { /* some browsers throw */ }
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
      if (view === "tracker") loadTracker();
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
    navigator.serviceWorker.register("sw.js")
      .then((reg) => { swReg = reg; })
      .catch(() => {});
  }
}

document.addEventListener("DOMContentLoaded", init);
