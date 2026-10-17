/* ASTRA mobile client — the price ticker. */
"use strict";
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

    renderTicker({ price, change, pct, live: live !== null, tf: activeTf });
    // Remember the ticker too, so an offline start still shows a price.
    const cached = loadLastRead();
    if (cached && !offlineMode) {
      saveLastRead(Object.assign({}, cached.snap, {
        ticker: { price, change, pct, live: live !== null, tf: activeTf },
      }));
    }
    checkLevels(price);
    // Keep an open idea's live R fresh while the journal tab is on screen.
    const jrn = $("view-tracker");
    if (jrn && !jrn.hidden && trkData) renderJournal(trkData);
  } catch (err) {
    // No live ticker; if we have a cached one and we are offline, show it.
    if (offlineMode) {
      const cached = loadLastRead();
      if (cached && cached.snap && cached.snap.ticker) {
        renderTicker(cached.snap.ticker);
        return;
      }
    }
    $("ticker").hidden = true;
  }
}

// Draws the header price from a {price, change, pct} reading. Kept separate from
// the fetch so a cached reading renders through the same path.
function renderTicker(t) {
  $("price").textContent = num(t.price);
  const chg = $("price-chg");
  chg.textContent = `${t.change >= 0 ? "+" : ""}${num(t.change)}  `
    + `${t.change >= 0 ? "+" : ""}${Number(t.pct).toFixed(2)}%`;
  chg.className = "ticker-chg "
    + (t.change > 0 ? "up" : t.change < 0 ? "down" : "");
  $("price-basis").textContent = t.live
    ? "live · gold-api.com"
    : `${t.tf || activeTf} close · gold-api.com`;
  $("ticker").hidden = false;
}

