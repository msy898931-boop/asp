/* ASTRA mobile client — the AI advisor panel. */
"use strict";
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
  setPanelLoading("ai", true);
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

    // The trade card: exact levels computed server-side, shown verbatim.
    const cardEl = $("ai-card");
    cardEl.innerHTML = "";
    const card = a.trade_card || {};
    if (card.card) {
      cardEl.hidden = false;
      const c = card.card;
      const title = document.createElement("h3");
      title.className = "ai-sub";
      title.textContent = T("ai_card");
      cardEl.appendChild(title);
      const dir = document.createElement("div");
      dir.className = "ai-card-dir";
      // `agreement` lives on the inner card ("4/5"); reading it off the
      // envelope would stringify the whole object as [object Object].
      const agree = c.agreement ? ` · ${c.agreement}` : "";
      dir.textContent = `${T(AI_STANCE_KEY[c.direction] || "ai_stance_neutral")}`
        + agree;
      cardEl.appendChild(dir);
      const grid = document.createElement("div");
      grid.className = "ai-card-grid";
      const rows = [
        ["ai_card_entry", c.entry], ["ai_card_stop", c.stop],
        ["ai_card_target", c.target], ["ai_card_rr", c.rr],
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
      cardEl.appendChild(grid);
      const tf = card.timeframes || {};
      const bits = Object.keys(tf).map((k) => {
        const arrow = { LONG: "▲", SHORT: "▼", NEUTRAL: "–" }[tf[k].direction] || "–";
        return `${k} ${arrow} ${tf[k].rsi}`;
      });
      if (bits.length) {
        const line = document.createElement("div");
        line.className = "ai-card-tf";
        line.textContent = bits.join("  ");
        cardEl.appendChild(line);
      }
    } else {
      cardEl.hidden = true;
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
    } else if (a.rate_limited) {
      // The model's daily quota is spent: the opinion is paused, but the
      // snapshot and the trade card above are still live, so show that plainly
      // instead of a blank "unavailable" panel.
      stanceEl.textContent = T("ai_paused");
      stanceEl.classList.add("neutral");
      $("ai-conf").textContent = "";
      $("ai-thesis").textContent = T("ai_paused_sub");
      $("ai-risks").textContent = "";
      setStatus(T("ai_paused"));
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
                      "ai-card", "ai-review", "ai-headlines-title",
                      "ai-events-title"]) {
      const el = $(id);
      el.hidden = true;
      if (el.tagName === "UL" || el.tagName === "DIV") el.innerHTML = "";
    }
    setStatus(T("ai_unavail"));
  } finally {
    setPanelLoading("ai", false);
  }
}

