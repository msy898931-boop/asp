/* ASTRA mobile client — the isolated SMC/ICT analysis panel. */
"use strict";
/* ── SMC/ICT engine (isolated, unvalidated) ─────────────────────────── */

const SMC_BIAS_KEY = {
  LONG: "ai_stance_long", SHORT: "ai_stance_short", NEUTRAL: "ai_stance_neutral",
};

const SMC_DIR_KEY = { LONG: "ai_dir_long", SHORT: "ai_dir_short" };

// One plan as a card: the model's own numbers, shown verbatim. The confidence
// is labelled an estimate wherever it appears, because it is the model's claim
// and not a measured edge.
function smcPlanCard(p, index) {
  const card = document.createElement("div");
  card.className = "ai-card";
  const head = document.createElement("div");
  head.className = "ai-card-dir";
  const dir = T(SMC_DIR_KEY[p.direction] || "ai_stance_neutral");
  head.textContent = `${index}. ${dir}`;
  card.appendChild(head);

  const grid = document.createElement("div");
  grid.className = "ai-card-grid";
  const rows = [
    ["ai_card_entry", p.entry], ["ai_card_stop", p.stop],
    ["ai_card_rr", p.reward_risk],
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
  if (p.confidence != null) {
    const div = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = T("eng_winrate");
    const dd = document.createElement("dd");
    dd.textContent = fmt(T("eng_winrate_est"),
                         { p: Math.round(p.confidence * 100) });
    div.appendChild(dt);
    div.appendChild(dd);
    grid.appendChild(div);
  }
  card.appendChild(grid);

  // The methodology's own vocabulary: what liquidity was swept, and which
  // point of interest the entry sits at. Both are the model's Arabic phrases.
  if (p.sweep) {
    const row = document.createElement("div");
    row.className = "smc-fact";
    row.textContent = `${T("smc_sweep")}: ${p.sweep}`;
    card.appendChild(row);
  }
  if (p.poi) {
    const row = document.createElement("div");
    row.className = "smc-fact";
    row.textContent = `${T("smc_poi")}: ${p.poi}`;
    card.appendChild(row);
  }

  const ladder = Array.isArray(p.targets) ? p.targets.filter((x) => x != null) : [];
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

  if (p.rationale) {
    const note = document.createElement("div");
    note.className = "eng-rationale";
    note.textContent = p.rationale;
    card.appendChild(note);
  }
  return card;
}

// The project's measured record for this engine, shown above the model's own
// numbers. It is the one line in the panel that is a measurement rather than a
// claim, so it sits apart and never wears the confidence's clothes.
function smcMeasuredBlock(rec) {
  if (!rec || rec.n == null) return null;
  const block = document.createElement("div");
  block.className = "eng-playbook";
  const head = document.createElement("div");
  head.className = "eng-playbook-school";
  head.textContent = T("smc_measured");
  block.appendChild(head);
  const line = document.createElement("div");
  line.className = "eng-playbook-measured";
  line.textContent = fmt(T("smc_measured_line"),
                         { n: rec.n, w: rec.win_pct, r: rec.r_per_trade });
  block.appendChild(line);
  if (rec.clears_gate === false) {
    const gate = document.createElement("div");
    gate.className = "eng-playbook-measured";
    gate.textContent = T("smc_measured_gate");
    block.appendChild(gate);
  }
  if (rec.control_win_pct != null) {
    const ctrl = document.createElement("div");
    ctrl.className = "eng-playbook-measured";
    ctrl.textContent = fmt(T("smc_measured_control"),
                           { c: rec.control_win_pct });
    block.appendChild(ctrl);
  }
  return block;
}

// Reads the isolated SMC engine through the same-origin proxy. A failure here
// is shown as "unreachable" and changes nothing else in the app: the engine
// sits outside the validated system and must never affect the other panels.
async function loadSmc(force) {
  const statusEl = $("smc-status");
  const plansEl = $("smc-plans");
  const setStatus = (text) => { statusEl.textContent = text; };
  setPanelLoading("smc", true);
  try {
    const env = await getJson("smc/v1/smc" + (force ? "?refresh=1" : ""));
    const e = (env && env.smc) || {};
    const analysis = e.analysis;

    plansEl.innerHTML = "";
    $("smc-read").textContent = "";
    $("smc-meta").textContent = "";

    if (!e.configured) {
      setStatus(T("smc_unconfigured"));
      $("smc-read").textContent = e.note || T("smc_off_sub");
      return;
    }
    if (!analysis) {
      setStatus(e.rate_limited ? T("smc_paused") : T("smc_unreachable"));
      $("smc-read").textContent = e.note || "";
      return;
    }

    const biasEl = $("smc-bias");
    biasEl.textContent = T(SMC_BIAS_KEY[analysis.bias] || "ai_stance_neutral");
    biasEl.className = "ai-stance " + String(analysis.bias || "neutral").toLowerCase();
    $("smc-read").textContent = analysis.market_read || "";

    const plans = analysis.plans || [];
    const eng = analysis.engine || {};
    // The measured record goes first, above the model's own numbers: it is the
    // one line here that is a measurement, and it should be read before the
    // claims, not after them.
    const measured = smcMeasuredBlock(eng.measured_record);
    if (measured) plansEl.appendChild(measured);
    if (!plans.length) {
      const empty = document.createElement("p");
      empty.className = "eng-empty";
      empty.textContent = T("smc_no_plans");
      plansEl.appendChild(empty);
    } else {
      plans.forEach((p, i) => plansEl.appendChild(smcPlanCard(p, i + 1)));
    }

    const tf = (eng.timeframes_read || []).join(" ");
    const bits = [];
    if (e.model) bits.push(`${T("ai_model")}: ${e.model}`);
    if (tf) bits.push(`${T("eng_timeframes")}: ${tf}`);
    if (analysis.news_caution) {
      bits.push(analysis.key_event
        ? `${T("smc_news_caution")}: ${analysis.key_event}`
        : T("smc_news_caution"));
    }
    const rejected = (analysis.rejected || []).length;
    if (rejected) bits.push(fmt(T("eng_rejected"), { n: rejected }));
    $("smc-meta").textContent = bits.join(" · ");
    setStatus(T("smc_ok"));
  } catch (err) {
    // A spent daily analysis quota is a 429 the server explains in JSON; show
    // it as a distinct message so the user knows it resets, not that the engine
    // is down. Any other failure stays "unreachable".
    const p = err && err.payload;
    if (err && err.status === 429 && p && p.meter) {
      setStatus(T("eng_quota"));
      const reset = p.meter.reset_at
        ? new Date(p.meter.reset_at * 1000).toLocaleString() : "";
      $("smc-read").textContent = fmt(T("eng_quota_sub"),
        { used: p.meter.used, limit: p.meter.limit, reset: reset });
    } else {
      setStatus(T("smc_unreachable"));
      $("smc-read").textContent = "";
      $("smc-meta").textContent = "";
      plansEl.innerHTML = "";
    }
    $("smc-bias").textContent = "--";
    $("smc-bias").className = "ai-stance neutral";
  } finally {
    setPanelLoading("smc", false);
  }
}
