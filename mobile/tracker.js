/* ASTRA mobile client — signal tracker and trade journal. */
"use strict";
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
  const listEl = $("trk-list");
  const noteEl = $("trk-note");
  if (warming) {
    $("trk-now").innerHTML = "";
    $("trk-stats").innerHTML = "";
    $("jrn-today").innerHTML = "";
    listEl.innerHTML = `<p class="panel-note">${T("tracker_warming")}</p>`;
    noteEl.textContent = "";
    return;
  }
  renderNow(data && data.last_bar);
  if (!data || typeof data !== "object") {
    $("trk-stats").innerHTML = "";
    $("jrn-today").innerHTML = "";
    listEl.innerHTML = "";
    noteEl.textContent = T("tracker_empty");
    return;
  }
  trkData = data;
  renderJournal(data);

  const s = data.summary || {};
  const bits = [];
  if (data.cost_usd != null) bits.push(T("tracker_spread").replace("{c}", num(data.cost_usd)));
  if (data.horizon_bars != null) bits.push(
    T("tracker_horizon").replace("{h}", num((data.horizon_bars * 15) / 60, 1)));
  bits.push(s.sufficient ? "" : T("tracker_insufficient"));
  noteEl.textContent = bits.filter(Boolean).join(" · ");
}

/* ── trade journal ──────────────────────────────────────────────────── */

let trkData = null;
let jrnScope = "today";

function dayKey(sec) {
  const d = new Date(sec * 1000);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

// 1 oz of gold moves $1 per $1/oz move, so a signal's dollar result is its net
// R times the stop distance. That is the minimum position — the honest baseline.
function jrnUsd(r) {
  if (r.r_net == null || r.entry == null || r.stop == null) return null;
  return r.r_net * Math.abs(r.entry - r.stop);
}

// Where a still-open idea stands right now, in R, against the live price.
function jrnOpenR(r) {
  if (lastPrice == null || r.entry == null || r.stop == null) return null;
  const risk = Math.abs(r.entry - r.stop);
  if (!risk) return null;
  const dir = r.direction === "LONG" ? 1 : -1;
  return ((lastPrice - r.entry) * dir) / risk;
}

function jrnStat(label, value, cls) {
  return `<div class="trk-stat"><span class="trk-stat-label">${label}</span>` +
    `<span class="trk-stat-value ${cls || ""}">${value}</span></div>`;
}

function renderJrnStrip(rows) {
  const el = $("jrn-today");
  if (!el) return;
  const closed = rows.filter((r) => r.r_net != null);
  const wins = closed.filter((r) => r.r_net > 0).length;
  const losses = closed.length - wins;
  const netR = closed.reduce((a, r) => a + r.r_net, 0);
  const netUsd = closed.reduce((a, r) => a + (jrnUsd(r) || 0), 0);
  const open = rows.find((r) => r.outcome === "OPEN");
  const openR = open ? jrnOpenR(open) : null;

  const netCls = netR > 0 ? "up" : netR < 0 ? "down" : "";
  const openCls = openR == null ? "" : openR >= 0 ? "up" : "down";
  el.innerHTML =
    jrnStat(T("jrn_trades"), String(closed.length)) +
    jrnStat(T("jrn_record"), `${wins} / ${losses}`, wins >= losses ? "up" : "down") +
    jrnStat(T("jrn_net_r"), `${netR >= 0 ? "+" : ""}${num(netR, 2)}R`, netCls) +
    jrnStat(T("jrn_net_usd"), `${netUsd >= 0 ? "+" : ""}$${num(netUsd, 2)}`, netCls) +
    jrnStat(T("jrn_open"), openR == null ? "—"
      : `${openR >= 0 ? "+" : ""}${num(openR, 2)}R`, openCls);
}

function renderJrnStats(rows) {
  const s = (trkData && trkData.summary) || {};
  const closed = rows.filter((r) => r.r_net != null);
  const wins = closed.filter((r) => r.r_net > 0).length;
  const losses = closed.length - wins;
  const netR = closed.reduce((a, r) => a + r.r_net, 0);
  const gross = closed.reduce((a, r) => a + (r.r_gross || 0), 0);
  const netCls = netR > 0 ? "up" : netR < 0 ? "down" : "";
  $("trk-stats").innerHTML =
    trkStat(T("tracker_count"), String(rows.length)) +
    trkStat(T("tracker_wins"), String(wins), "up") +
    trkStat(T("tracker_losses"), String(losses), "down") +
    trkStat(T("tracker_net"), `${netR >= 0 ? "+" : ""}${num(netR, 2)}R`, netCls) +
    trkStat(T("tracker_gross"), `${gross >= 0 ? "+" : ""}${num(gross, 2)}R`) +
    trkStat(T("tracker_winrate"),
      s.sufficient ? fmt("{p}%", { p: s.win_rate }) : "—");
}

function jrnRow(r) {
  const dirCls = r.direction === "LONG" ? "up" : "down";
  const oc = TRK_OUTCOME_KEY[r.outcome] || r.outcome;
  let rCls = "";
  let rTxt = "—";
  if (r.r_net != null) {
    rCls = r.r_net >= 0 ? "up" : "down";
    rTxt = `${r.r_net >= 0 ? "+" : ""}${num(r.r_net, 2)}R`;
  } else if (r.outcome === "OPEN") {
    const live = jrnOpenR(r);
    if (live != null) { rCls = live >= 0 ? "up" : "down";
      rTxt = `${live >= 0 ? "+" : ""}${num(live, 2)}R`; }
  }
  const usd = r.r_net != null ? jrnUsd(r) : null;
  const usdTxt = usd == null ? "" :
    ` · ${usd >= 0 ? "+" : ""}$${num(usd, 2)}`;
  const t = new Date(r.time * 1000);
  const hhmm = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  return `<div class="trk-row">` +
    `<div class="trk-top">` +
    `<span class="trk-dir ${dirCls}">${r.direction}</span>` +
    `<span class="trk-r ${rCls}">${rTxt}</span></div>` +
    `<div class="trk-levels">${T("entry")} ${num(r.entry)} · ` +
    `${T("stop")} ${num(r.stop)} · ${T("target")} ${num(r.target)}</div>` +
    `<div class="trk-sub">` +
    `<span class="trk-oc ${rCls}">${T(oc)}${usdTxt}</span>` +
    `<span class="trk-meta">${hhmm} · ${mapI18n(NOW_REGIME, r.regime || "UNKNOWN")} · RSI ${num(r.rsi, 0)}</span>` +
    `</div></div>`;
}

function renderJrnList(rows) {
  const listEl = $("trk-list");
  if (!rows.length) {
    listEl.innerHTML = `<p class="panel-note">${T("tracker_empty")}</p>`;
    return;
  }
  let html = "";
  let lastDay = null;
  rows.forEach((r) => {
    if (jrnScope === "all") {
      const k = dayKey(r.time);
      if (k !== lastDay) {
        lastDay = k;
        const d = new Date(r.time * 1000);
        html += `<div class="jrn-day">${d.toLocaleDateString()}</div>`;
      }
    }
    html += jrnRow(r);
  });
  listEl.innerHTML = html;
}

function renderJournal(data) {
  const all = (data.signals || []).slice().reverse();  // newest first
  const today = dayKey(Math.floor(Date.now() / 1000));
  const rows = jrnScope === "today"
    ? all.filter((r) => dayKey(r.time) === today)
    : all;
  renderJrnStrip(rows);
  renderJrnStats(rows);
  renderJrnList(rows);
}

function bindJournalFilter() {
  const box = $("jrn-filter");
  if (!box) return;
  box.querySelectorAll(".jrn-fbtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      jrnScope = btn.dataset.scope;
      box.querySelectorAll(".jrn-fbtn").forEach((b) =>
        b.setAttribute("aria-selected", b === btn ? "true" : "false"));
      if (trkData) renderJournal(trkData);
    });
  });
}

async function loadTracker(attempt = 0) {
  setPanelLoading("tracker", true);
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
  } finally {
    setPanelLoading("tracker", false);
  }
}

