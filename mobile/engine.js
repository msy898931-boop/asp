/* ASTRA mobile client — the isolated AI analysis engine panel. */
"use strict";
/* ── AI analysis engine (isolated, unvalidated) ─────────────────────── */

const ENG_BIAS_KEY = {
  LONG: "ai_stance_long", SHORT: "ai_stance_short", NEUTRAL: "ai_stance_neutral",
};

const ENG_DIR_KEY = { LONG: "ai_dir_long", SHORT: "ai_dir_short" };

// One trade as a card: the model's own numbers, shown verbatim. The win-rate is
// labelled as an estimate wherever it appears, because it is the model's claim
// and not a measured edge.
function engTradeCard(t, index) {
  const card = document.createElement("div");
  card.className = "ai-card";
  const head = document.createElement("div");
  head.className = "ai-card-dir";
  const dir = T(ENG_DIR_KEY[t.direction] || "ai_stance_neutral");
  const tf = t.timeframe ? ` · ${t.timeframe}` : "";
  head.textContent = `${index}. ${dir}${tf}`;
  card.appendChild(head);

  const grid = document.createElement("div");
  grid.className = "ai-card-grid";
  const rows = [
    ["ai_card_entry", t.entry], ["ai_card_stop", t.stop],
    ["ai_card_rr", t.reward_risk],
  ];
  for (const [k, v] of rows) {
    if (v == null) continue;
    const div = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = T(k);
    const dd = document.createElement("dd");
    dd.textContent = num(v);
    div.appendChild(dt);
    div.appendChild(dd);
    grid.appendChild(div);
  }
  if (t.win_rate != null) {
    const div = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = T("eng_winrate");
    const dd = document.createElement("dd");
    dd.textContent = fmt(T("eng_winrate_est"),
                         { p: Math.round(t.win_rate * 100) });
    div.appendChild(dt);
    div.appendChild(dd);
    grid.appendChild(div);
  }
  card.appendChild(grid);

  const ladder = Array.isArray(t.targets) ? t.targets.filter((x) => x != null) : [];
  if (ladder.length) {
    const block = document.createElement("div");
    block.className = "ai-card-targets";
    const lbl = document.createElement("div");
    lbl.className = "ai-card-targets-label";
    lbl.textContent = fmt(T("ai_card_targets"), { n: ladder.length });
    block.appendChild(lbl);
    const list = document.createElement("ol");
    list.className = "ai-card-targets-list";
    for (const x of ladder) {
      const li = document.createElement("li");
      li.textContent = num(x);
      list.appendChild(li);
    }
    block.appendChild(list);
    card.appendChild(block);
  }

  if (t.rationale) {
    const note = document.createElement("div");
    note.className = "eng-rationale";
    note.textContent = t.rationale;
    card.appendChild(note);
  }
  if (t.anchor && t.anchor.anchored === false && t.anchor.note) {
    const warn = document.createElement("div");
    warn.className = "eng-anchor";
    warn.textContent = fmt(T("eng_anchor_warn"), { note: t.anchor.note });
    card.appendChild(warn);
  }
  return card;
}

// Reads the isolated engine through the same-origin proxy. A failure here is
// shown as "unreachable" and changes nothing else in the app: the engine sits
// outside the validated system and must never affect the other panels.
async function loadEngine(force) {
  const statusEl = $("eng-status");
  const tradesEl = $("eng-trades");
  const setStatus = (text) => { statusEl.textContent = text; };
  setPanelLoading("engine", true);
  try {
    const env = await getJson("engine/v1/engine" + (force ? "?refresh=1" : ""));
    const e = (env && env.engine) || {};
    const analysis = e.analysis;

    tradesEl.innerHTML = "";
    $("eng-read").textContent = "";
    $("eng-meta").textContent = "";

    if (!e.configured) {
      setStatus(T("eng_unconfigured"));
      $("eng-read").textContent = e.note || T("eng_off_sub");
      return;
    }
    if (!analysis) {
      setStatus(e.rate_limited ? T("eng_paused") : T("eng_unreachable"));
      $("eng-read").textContent = e.note || "";
      return;
    }

    const biasEl = $("eng-bias");
    biasEl.textContent = T(ENG_BIAS_KEY[analysis.bias] || "ai_stance_neutral");
    biasEl.className = "ai-stance " + String(analysis.bias || "neutral").toLowerCase();
    $("eng-read").textContent = analysis.market_read || "";

    const trades = analysis.trades || [];
    if (!trades.length) {
      const empty = document.createElement("p");
      empty.className = "eng-empty";
      empty.textContent = T("eng_no_trades");
      tradesEl.appendChild(empty);
    } else {
      trades.forEach((t, i) => tradesEl.appendChild(engTradeCard(t, i + 1)));
    }

    const eng = analysis.engine || {};
    const tf = (eng.timeframes_read || []).join(" ");
    const bits = [];
    if (e.model) bits.push(`${T("ai_model")}: ${e.model}`);
    if (tf) bits.push(`${T("eng_timeframes")}: ${tf}`);
    if (analysis.news_used) bits.push(T("eng_news_used"));
    if (analysis.key_event) bits.push(`${T("ai_event")}: ${analysis.key_event}`);
    const rejected = (analysis.rejected || []).length;
    if (rejected) bits.push(fmt(T("eng_rejected"), { n: rejected }));
    $("eng-meta").textContent = bits.join(" · ");
    setStatus(T("eng_ok"));
  } catch (err) {
    // A spent daily analysis quota is a 429 the server explains in JSON; show
    // it as a distinct message so the user knows it resets, not that the engine
    // is down. Any other failure stays "unreachable".
    const p = err && err.payload;
    if (err && err.status === 429 && p && p.meter) {
      setStatus(T("eng_quota"));
      const reset = p.meter.reset_at
        ? new Date(p.meter.reset_at * 1000).toLocaleString() : "";
      $("eng-read").textContent = fmt(T("eng_quota_sub"),
        { used: p.meter.used, limit: p.meter.limit, reset: reset });
    } else {
      setStatus(T("eng_unreachable"));
      $("eng-read").textContent = "";
      $("eng-meta").textContent = "";
      tradesEl.innerHTML = "";
    }
    $("eng-bias").textContent = "--";
    $("eng-bias").className = "ai-stance neutral";
  } finally {
    setPanelLoading("engine", false);
  }
}
