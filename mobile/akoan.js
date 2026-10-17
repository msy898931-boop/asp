/* ASTRA mobile client — the News (akoan) panel. */
"use strict";
async function loadAkoan() {
  const verdict = $("akoan-verdict");
  const sub = $("akoan-sub");
  const dims = $("akoan-dims");
  const base = $("akoan-base");
  const cov = $("akoan-cov");
  const note = $("akoan-note");
  setPanelLoading("akoan", true);
  verdict.textContent = T("akoan_loading");
  verdict.className = "akoan-verdict";
  sub.textContent = "";
  dims.innerHTML = "";
  base.textContent = "";
  cov.textContent = "";
  note.textContent = "";

  // The study replays the corpus; a cold server can take a while. Show elapsed
  // time and then the cached-forecast hint, so a slow read is visibly working
  // rather than a frozen panel.
  let seconds = 0;
  const tick = setInterval(() => {
    seconds += 1;
    if (seconds >= 20) {
      sub.textContent = fmt(T("akoan_slow"), { s: seconds });
    }
  }, 1000);

  let data = null;
  try {
    data = await getJson("akoan.json", AKOAN_TIMEOUT_MS);
  } catch (err) {
    data = (err && err.name === "AbortError")
      ? { available: false, reason: "timed out" } : null;
  } finally {
    clearInterval(tick);
    setPanelLoading("akoan", false);
  }
  akoanData = data;
  renderAkoan(data);
}

// The panel's text is built in JS, so a language switch must rebuild it from
// the last reading — applyLang only rewrites [data-i18n] nodes.
function rerenderAkoan() {
  if (akoanData !== undefined) renderAkoan(akoanData);
}

function _akoanPct(x) {
  return (x === null || x === undefined) ? "—" : `${(x * 100).toFixed(1)}%`;
}

function renderAkoan(data) {
  const verdict = $("akoan-verdict");
  const sub = $("akoan-sub");
  const dims = $("akoan-dims");
  const base = $("akoan-base");
  const cov = $("akoan-cov");
  const note = $("akoan-note");

  if (!data || !data.available) {
    const reason = (data && data.reason) || "backend unreachable";
    verdict.textContent = "—";
    verdict.className = "akoan-verdict";
    sub.textContent = fmt(T("akoan_unavail"), { reason });
    dims.innerHTML = "";
    base.textContent = "";
    cov.textContent = "";
    note.textContent = "";
    return;
  }

  const v = data.verdict || "INSUFFICIENT_DATA";
  verdict.textContent = T(`akoan_verdict_${v}`);
  verdict.className = "akoan-verdict " + v.toLowerCase();
  sub.textContent = T(`akoan_verdict_${v}_sub`);

  base.textContent = fmt(T("akoan_base"), {
    p: _akoanPct(data.base_rate),
    n: data.scored ?? 0,
  }) + (data.net_r !== undefined ? " · " + fmt(T("akoan_net"), { r: `${data.net_r >= 0 ? "+" : ""}${data.net_r}R` }) : "");

  dims.innerHTML = "";
  (data.dimensions || []).forEach((dim) => {
    const wrap = document.createElement("div");
    wrap.className = "akoan-dim";
    const title = document.createElement("div");
    title.className = "akoan-dim-title";
    title.textContent = T(`akoan_dim_${dim.name}`);
    wrap.appendChild(title);

    if (!dim.comparable) {
      const row = document.createElement("div");
      row.className = "akoan-row muted";
      const total = (dim.buckets || []).reduce((s, b) => s + (b.n || 0), 0);
      row.textContent = fmt(T("akoan_not_comparable"), { n: total });
      wrap.appendChild(row);
      dims.appendChild(wrap);
      return;
    }

    (dim.buckets || []).forEach((b) => {
      const row = document.createElement("div");
      row.className = "akoan-row";
      if (b.significant) row.classList.add("sig");

      const label = document.createElement("span");
      label.className = "akoan-label";
      label.textContent = T(`akoan_val_${b.value}`);

      const val = document.createElement("span");
      val.className = "akoan-val";
      if (b.insufficient) {
        val.textContent = fmt(T("akoan_insufficient"), { n: b.n });
        val.classList.add("muted");
      } else {
        const edge = (b.edge_vs_base === null || b.edge_vs_base === undefined)
          ? "" : ` · ${b.edge_vs_base >= 0 ? "+" : ""}${(b.edge_vs_base * 100).toFixed(1)}%`;
        val.textContent = `${_akoanPct(b.win_rate)} · n=${b.n}${edge}`
          + (b.significant ? ` ⭐ ${T("akoan_sig")}` : "");
      }

      row.appendChild(label);
      row.appendChild(val);
      wrap.appendChild(row);
    });
    dims.appendChild(wrap);
  });

  const c = data.coverage || {};
  cov.textContent = fmt(T("akoan_cov"), {
    events: c.events ?? 0, released: c.released ?? 0, days: c.span_days ?? 0,
  });
  note.textContent = T("akoan_footer");
}

// A prominent live price in the header. The price itself comes from the live
// tick (the freshest quote the bridge has); the closed-bar series only supplies
// the reference for the change. Sampling the price from the newest *closed* bar
// would show a price up to a full interval old.
