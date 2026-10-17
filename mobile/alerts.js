/* ASTRA mobile client — background alerts, Web Push and
   price levels. */
"use strict";
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

/* ── background alerts (Web Push) ──────────────────────────────────────
   Real push needs a VAPID key from the server. When the server has none
   (/push.json says available:false) this is a silent no-op and the in-app
   alerts stay the only channel — the same graceful degradation the AI tab
   uses when no advisor key is set. Returns true only when a subscription
   was actually registered, so the caller can tell the user which happened. */
function urlBase64ToUint8Array(base64) {
  const pad = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function syncPush(on) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  let reg = swReg;
  try {
    if (!reg) reg = await navigator.serviceWorker.ready;
  } catch (e) { return false; }
  if (!reg || !reg.pushManager) return false;
  let info;
  try {
    info = await (await fetch("/push.json", { cache: "no-store" })).json();
  } catch (e) { return false; }
  if (!info || !info.available || !info.public_key) return false;
  try {
    let sub = await reg.pushManager.getSubscription();
    if (on) {
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(info.public_key),
        });
      }
      await fetch("/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub.toJSON()),
      });
      return true;
    }
    if (sub) {
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      await fetch("/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint }),
      });
    }
    return false;
  } catch (e) { return false; }   // permission denied, or no push service
}

async function toggleAlerts() {
  const btn = $("alerts");
  if (alertsOn) {
    alertsOn = false;
    btn.setAttribute("aria-pressed", "false");
    $("push-test").hidden = true;
    toast(T("alerts_off"));
    syncPush(false);
    return;
  }
  alertsOn = true;
  btn.setAttribute("aria-pressed", "true");
  let pushed = false;
  if ("Notification" in window) {
    if (Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
    }
    if (Notification.permission === "granted") pushed = await syncPush(true);
  }
  $("push-test").hidden = !pushed;
  toast(pushed ? T("push_on") : T("alerts_on"));
}

/* A deliberate, one-tap proof that the background path works: ask the server to
   send a test notification to the registered devices, then report what it did. */
async function sendTestPush() {
  const btn = $("push-test");
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    const res = await fetch("/push/test", { method: "POST" });
    const info = await res.json().catch(() => ({}));
    if (info.sent > 0) toast(T("push_test_sent"));
    else if (!info.available) toast(T("push_test_off"));
    else toast(T("push_test_none"));
  } catch (e) {
    toast(T("push_test_none"));
  } finally {
    btn.disabled = false;
  }
}

/* On boot the subscription survives but `alertsOn` does not, so the test button
   would vanish on every reload. Re-derive it: if this device still has a live
   push subscription, the background path is on and the proof button belongs. */
async function restoreAlerts() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  try {
    const reg = swReg || await navigator.serviceWorker.ready;
    if (!reg || !reg.pushManager) return;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    alertsOn = true;
    $("alerts").setAttribute("aria-pressed", "true");
    $("push-test").hidden = false;
  } catch (e) { /* no worker, or no subscription: nothing to restore */ }
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

