/* ASTRA mobile client — the volatility panel. */
"use strict";
async function loadVolatility() {
  setPanelLoading("vol", true);
  let data = null;
  try {
    data = await getJson("vol.json");
  } catch (err) {
    data = null;
  } finally {
    setPanelLoading("vol", false);
  }
  renderVolatility(data);
}

function renderVolatility(data) {
  const pct = $("vol-pct");
  const tier = $("vol-tier");
  const fill = $("vol-fill");
  const mark = $("vol-mark");
  const verdict = $("vol-verdict");
  const meta = $("vol-meta");

  if (!data || !data.available) {
    const reason = (data && data.reason) || "backend unreachable";
    pct.textContent = "--";
    pct.className = "vol-pct";
    tier.textContent = "—";
    tier.className = "vol-tier calm";
    fill.style.width = "0%";
    fill.className = "";
    verdict.textContent = fmt(T("vol_unavail"), { reason });
    meta.textContent = "";
    return;
  }

  const p = data.probability;
  const th = data.threshold;
  pct.textContent = `${Math.round(p * 100)}%`;
  pct.className = "vol-pct " + data.tier;
  tier.textContent = T(`vol_tier_${data.tier}`);
  tier.className = "vol-tier " + data.tier;
  fill.style.width = `${Math.round(p * 100)}%`;
  fill.className = data.tier;
  mark.style.left = `${Math.round(th * 100)}%`;
  verdict.textContent = T(`vol_verdict_${data.tier}`);
  verdict.className = "vol-verdict " + data.tier;
  const at = new Date().toLocaleTimeString();
  meta.textContent = fmt(T("vol_meta"), {
    th: Math.round(th * 100),
    tf: (data.timeframes || []).join(" "),
    at,
  });
}

// akoan: does the news explain the results? The server runs the same engine the
// CLI does, so this panel cannot disagree with the report. It is deliberately
// slow to change (the study replays the corpus), so it is fetched on demand.
