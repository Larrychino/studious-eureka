/* MedVerify staff app and manager dashboard. Plain JS, no build step. */
"use strict";

const state = {
  token: localGet("mv_token"),
  user: null,
  homes: [],
  homeId: Number(localGet("mv_home")) || null,
  tab: localGet("mv_tab") || "now",
  scanMode: "bookin",
  pendingLabel: null,
  pendingPack: null,
};

// ---------- utilities ----------
function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } }
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function asDate(s) { if (!s) return null; return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z"); }
function fmtTime(s) { const d = asDate(s); return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"; }
function fmtDateTime(s) { const d = asDate(s); return d ? d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—"; }
function fmtDate(s) { if (!s) return "—"; const d = new Date(s + (s.length === 10 ? "T00:00:00" : "")); return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }); }
function ago(s) { const d = asDate(s); if (!d) return "never"; const m = Math.round((Date.now() - d) / 60000); if (m < 1) return "just now"; if (m < 60) return `${m} min ago`; const h = Math.round(m / 60); return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`; }
const ROLE_NAMES = { carer: "Carer", senior: "Senior carer", manager: "Manager", group_admin: "Group admin", pharmacist: "Pharmacist", admin: "Admin" };
const isSenior = () => ["senior", "manager", "group_admin", "admin"].includes(state.user?.role);
const isManager = () => ["manager", "group_admin", "admin"].includes(state.user?.role);
const isStaff = () => ["carer", "senior", "manager", "group_admin", "admin"].includes(state.user?.role);

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body = opts.body;
  if (body && typeof body !== "string") { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (res.status === 401 && state.token) { signOut(); throw new Error("Please sign in again"); }
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Error ${res.status}`);
  }
  return data;
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message); throw e; }
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.value === "") out[el.name] = null;
    else if (el.type === "number") out[el.name] = Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

// ---------- sign in ----------
function renderLogin(error) {
  $("#app").innerHTML = `
  <div class="login card">
    <h1>MedVerify</h1>
    <p class="muted">Medicines safety for care homes</p>
    <form id="login-form" class="stack">
      <div><label for="u">Username</label><input id="u" name="username" autocomplete="username" required></div>
      <div><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required></div>
      ${error ? `<p class="badge bad">${esc(error)}</p>` : ""}
      <button class="primary" type="submit">Sign in</button>
    </form>
    <div id="demo-hint"></div>
  </div>`;
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/auth/login", { body: formData(e.target) });
      state.token = r.token; state.user = r.user; localSet("mv_token", r.token);
      await boot();
    } catch (err) { renderLogin(err.message); }
  });
  api("/api/health").then((h) => {
    if (h.demo) $("#demo-hint").innerHTML = `<p class="demo-users muted">Demo accounts (password <code>medverify-demo</code>):
      <code>carer1</code> <code>senior1</code> <code>manager1</code> <code>group1</code> <code>pharm1</code> <code>founder</code></p>`;
  }).catch(() => {});
}

async function signOut() {
  try { if (state.token) await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${state.token}` } }); } catch { /* offline */ }
  state.token = null; state.user = null; localSet("mv_token", null); renderLogin();
}

// ---------- shell ----------
function tabsFor() {
  const tabs = [];
  if (isStaff()) tabs.push(["now", "Now"], ["scan", "Scan"]);
  if (state.homeId) tabs.push(["fridges", "Fridges"], ["stock", "Stock & MAR"]);
  if (isSenior()) tabs.push(["dashboard", "Dashboard"]);
  if (isSenior() && state.homeId) tabs.push(["audit", "Audit"]);
  tabs.push(["rules", "Stability rules"]);
  if (isManager() && state.homeId) tabs.push(["settings", "Settings"]);
  if (state.user.role === "admin") tabs.push(["discovery", "Discovery"]);
  return tabs;
}

function renderShell() {
  const tabs = tabsFor();
  if (!tabs.some(([k]) => k === state.tab)) state.tab = tabs[0][0];
  const homeSelect = state.homes.length > 1
    ? `<select id="home-select" aria-label="Home">${state.homes.map((h) => `<option value="${h.id}" ${h.id === state.homeId ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select>`
    : `<span>${esc(state.homes[0]?.name || "")}</span>`;
  $("#app").innerHTML = `
    <header class="topbar">
      <span class="brand">MedVerify</span>${homeSelect}
      <span class="spacer"></span>
      <span class="who">${esc(state.user.display_name)} · ${ROLE_NAMES[state.user.role]}</span>
      ${state.user.home_id ? `<button id="shift-btn" title="Shift status">${state.user.on_shift ? "On shift" : "Off shift"}</button>` : ""}
      <button id="signout">Sign out</button>
    </header>
    <nav class="tabs" aria-label="Sections">${tabs.map(([k, n]) => `<button data-tab="${k}" ${k === state.tab ? 'aria-current="page"' : ""}>${n}<span class="badge bad" data-badge="${k}" hidden></span></button>`).join("")}</nav>
    <main id="view"><p class="empty">Loading…</p></main>`;
  $("#signout").onclick = signOut;
  const hs = $("#home-select");
  if (hs) hs.onchange = () => { state.homeId = Number(hs.value); localSet("mv_home", hs.value); renderShell(); };
  const sb = $("#shift-btn");
  if (sb) sb.onclick = async () => {
    state.user = await act(() => api("/api/me/shift", { body: { on_shift: !state.user.on_shift } }), "Shift updated");
    sb.textContent = state.user.on_shift ? "On shift" : "Off shift";
  };
  for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => { state.tab = b.dataset.tab; localSet("mv_tab", state.tab); renderShell(); };
  stopCamera();
  VIEWS[state.tab]().catch((e) => { $("#view").innerHTML = `<p class="badge bad">${esc(e.message)}</p>`; });
  refreshAlertBadge();
}

async function refreshAlertBadge() {
  if (!state.homeId) return;
  try {
    const alerts = await api(`/api/homes/${state.homeId}/alerts`);
    const el = document.querySelector('[data-badge="now"]');
    if (el) { el.hidden = !alerts.length; el.textContent = alerts.length; }
  } catch { /* ignore */ }
}

// ---------- NOW: alerts and the round ----------
function alertCard(a) {
  const canResolve = !(a.type === "stock_mismatch" && state.user.role === "carer");
  return `<div class="card alert ${esc(a.severity)}" data-alert="${a.id}">
    <div class="row between"><strong>${esc(a.title)}</strong><span class="badge ${a.severity === "critical" || a.severity === "high" ? "bad" : "warn"}">${esc(a.severity)}</span></div>
    <p>${esc(a.message)}</p>
    <small>Raised ${fmtDateTime(a.raised_at)}${a.seen_at ? ` · seen ${fmtTime(a.seen_at)}` : ""}</small>
    ${a.context?.excursion_id ? `<p><button class="small" data-open-excursion="${a.context.excursion_id}">Item-by-item instructions</button></p>` : ""}
    ${a.resolved_at ? `<p class="badge ok">Resolved: ${esc(a.action_taken)}</p>` : `
    <form class="row" data-resolve="${a.id}">
      ${a.seen_at ? "" : `<button type="button" class="small" data-seen="${a.id}">Seen</button>`}
      ${canResolve ? `<input name="action_taken" placeholder="What did you do?" required minlength="3" style="flex:1;min-width:160px">
      <button class="small primary">Resolve</button>` : `<small>The senior on shift resolves stock mismatches.</small>`}
    </form>`}
  </div>`;
}

function wireAlerts(root, reload) {
  root.querySelectorAll("[data-seen]").forEach((b) => b.onclick = async () => { await act(() => api(`/api/alerts/${b.dataset.seen}/seen`, { method: "POST" })); reload(); });
  root.querySelectorAll("form[data-resolve]").forEach((f) => f.onsubmit = async (e) => {
    e.preventDefault();
    await act(() => api(`/api/alerts/${f.dataset.resolve}/resolve`, { body: formData(f) }), "Alert resolved");
    reload(); refreshAlertBadge();
  });
  root.querySelectorAll("[data-open-excursion]").forEach((b) => b.onclick = () => showExcursion(Number(b.dataset.openExcursion)));
}

async function viewNow() {
  const v = $("#view");
  if (!state.homeId) { v.innerHTML = `<p class="empty">Choose a home.</p>`; return; }
  const [alerts, round] = await Promise.all([api(`/api/homes/${state.homeId}/alerts`), api(`/api/homes/${state.homeId}/round?window_min=90`)]);
  v.innerHTML = `
    <h1>Now</h1>
    ${state.user.home_id && !state.user.on_shift ? `<p class="notice">You are marked off shift, so alerts go to others. Tap "Off shift" at the top to start your shift.</p>` : ""}
    <h2>Alerts ${alerts.length ? `<span class="badge bad">${alerts.length}</span>` : ""}</h2>
    ${alerts.length ? alerts.map(alertCard).join("") : `<div class="card flat empty">No open alerts</div>`}
    <h2>Medicines round (next 90 minutes)</h2>
    ${round.length ? round.map((r) => `
      <div class="card">
        <div class="row between"><strong>${esc(r.medicine)} ${esc(r.strength || "")}</strong><span class="badge info">${esc(r.due_local)}</span></div>
        <p>${esc(r.resident_name || "Resident " + r.resident_ref)} · ${esc(r.tray || "no tray")}<br><small>${esc(r.dose_instructions || "")}</small></p>
        ${r.status === "quarantined" ? `<p class="badge bad">Quarantined — do not give</p>` :
          r.recorded ? `<p class="badge ok">Recorded: ${esc(r.recorded)}</p>` : `
          <div class="row">
            <button class="ok" data-admin="${r.item_id}" data-outcome="given">Given</button>
            <button class="warn" data-admin="${r.item_id}" data-outcome="refused">Refused</button>
            <button class="bad" data-admin="${r.item_id}" data-outcome="spoilt">Spoilt</button>
          </div>`}
      </div>`).join("") : `<div class="card flat empty">Nothing due in the next 90 minutes</div>`}`;
  wireAlerts(v, viewNow);
  v.querySelectorAll("[data-admin]").forEach((b) => b.onclick = async () => {
    const outcome = b.dataset.outcome;
    let notes = null;
    if (outcome !== "given") { notes = prompt(`Reason the dose was ${outcome}:`); if (notes === null) return; }
    await act(() => api(`/api/items/${b.dataset.admin}/administrations`, { body: { outcome, notes } }), `Recorded ${outcome}`);
    viewNow();
  });
}

// ---------- SCAN: booking in and delivery at the door ----------
let camera = { stream: null, timer: null };
function stopCamera() {
  if (camera.timer) clearInterval(camera.timer);
  if (camera.stream) camera.stream.getTracks().forEach((t) => t.stop());
  camera = { stream: null, timer: null };
}

function scannerHtml(id, placeholder) {
  return `<div class="scanner stack" id="${id}">
    <div class="row"><button type="button" class="primary" data-cam>Scan with camera</button><small class="muted" data-cam-msg></small></div>
    <video playsinline muted hidden></video>
    <label>Or type / paste the code</label>
    <div class="row"><input data-manual placeholder="${esc(placeholder)}" style="flex:1;min-width:200px"><button type="button" data-use>Use</button></div>
  </div>`;
}

function wireScanner(id, onCode) {
  const root = document.getElementById(id);
  const video = root.querySelector("video");
  const msg = root.querySelector("[data-cam-msg]");
  root.querySelector("[data-use]").onclick = () => { const v = root.querySelector("[data-manual]").value.trim(); if (v) onCode(v); };
  root.querySelector("[data-manual]").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); root.querySelector("[data-use]").click(); } };
  root.querySelector("[data-cam]").onclick = async () => {
    if (!("BarcodeDetector" in window)) { msg.textContent = "This browser cannot read barcodes from the camera. Use a handheld scanner or type the code."; return; }
    try {
      stopCamera();
      const detector = new window.BarcodeDetector({ formats: ["qr_code", "data_matrix", "code_128", "ean_13"] });
      camera.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      video.srcObject = camera.stream; video.hidden = false; await video.play();
      msg.textContent = "Point the camera at the code";
      camera.timer = setInterval(async () => {
        try {
          const codes = await detector.detect(video);
          if (codes.length) { const raw = codes[0].rawValue; stopCamera(); video.hidden = true; msg.textContent = ""; if (navigator.vibrate) navigator.vibrate(80); onCode(raw); }
        } catch { /* frame not ready */ }
      }, 300);
    } catch (e) { msg.textContent = "Camera unavailable: " + e.message; }
  };
}

async function viewScan() {
  const v = $("#view");
  v.innerHTML = `
    <h1>Scan</h1>
    <div class="row" role="tablist">
      <button class="${state.scanMode === "bookin" ? "primary" : ""}" data-mode="bookin">Book in a medicine</button>
      <button class="${state.scanMode === "delivery" ? "primary" : ""}" data-mode="delivery">Delivery at the door</button>
    </div>
    <div id="scan-body" class="card" style="margin-top:12px"></div>`;
  v.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => { state.scanMode = b.dataset.mode; state.pendingLabel = null; state.pendingPack = null; viewScan(); });
  state.scanMode === "bookin" ? renderBookIn() : renderDelivery();
}

async function renderBookIn() {
  const body = $("#scan-body");
  if (!state.pendingLabel) {
    body.innerHTML = `<h2>1. Scan the pharmacy label</h2>${scannerHtml("sc-label", "MV1|…")}`;
    wireScanner("sc-label", async (code) => {
      try {
        const r = await api("/api/labels/parse", { body: { text: code } });
        if (r.kind !== "pharmacy_label") { toast("That is a pack barcode. Scan the pharmacy label first."); return; }
        state.pendingLabel = { text: code, ...r.label }; renderBookIn();
      } catch (e) { toast(e.message); }
    });
    return;
  }
  const L = state.pendingLabel;
  const [trays, fridges] = await Promise.all([api(`/api/homes/${state.homeId}/trays`), api(`/api/homes/${state.homeId}/fridges`)]);
  const tray = trays.find((t) => t.resident_ref === L.resident_ref);
  body.innerHTML = `
    <h2>2. Check and confirm</h2>
    <table><tbody>
      <tr><th>Medicine</th><td>${esc(L.medicine_name)} ${esc(L.strength || "")}</td></tr>
      <tr><th>Resident</th><td>${esc(L.resident_ref)}</td></tr>
      <tr><th>Dose</th><td>${esc(L.dose_instructions || "")} ${L.dose_times.length ? `(${L.dose_times.join(", ")})` : ""}</td></tr>
      <tr><th>Expiry</th><td>${fmtDate(L.expiry)}</td></tr>
      <tr><th>Label</th><td>${esc(L.label_code)}</td></tr>
      ${state.pendingPack ? `<tr><th>Pack</th><td>GTIN ${esc(state.pendingPack.gtin || "—")} · batch ${esc(state.pendingPack.batch || "—")} · exp ${fmtDate(state.pendingPack.expiry)}</td></tr>` : ""}
    </tbody></table>
    <form id="bookin-form" class="stack">
      <label for="tray">Where does it go?</label>
      <select id="tray" name="place">
        ${tray ? `<option value="tray:${tray.id}">${esc(tray.label)} (resident's tray)</option>` : ""}
        ${trays.filter((t) => t !== tray && !t.resident_id).map((t) => `<option value="tray:${t.id}">${esc(t.label)} (unassigned tray)</option>`).join("")}
        ${fridges.map((f) => `<option value="fridge:${f.id}">${esc(f.name)} — no tray</option>`).join("")}
      </select>
      ${state.pendingPack ? "" : `<details><summary>Optional: scan the pack barcode (checks GTIN, batch and expiry)</summary>${scannerHtml("sc-pack", "GS1 DataMatrix e.g. (01)…(17)…(10)…")}</details>`}
      ${tray ? `<p class="muted">After confirming, place the item on ${esc(tray.label)}. The tray weighs it automatically.</p>` : ""}
      <div class="row"><button class="primary" id="bookin-submit">Book in</button><button type="button" id="bookin-cancel">Cancel</button></div>
    </form>`;
  if (!state.pendingPack) wireScanner("sc-pack", async (code) => {
    try {
      const r = await api("/api/labels/parse", { body: { text: code } });
      if (r.kind !== "gs1_pack") { toast("That is not a pack barcode"); return; }
      state.pendingPack = { text: code, ...r.pack }; renderBookIn();
    } catch (e) { toast(e.message); }
  });
  $("#bookin-cancel").onclick = () => { state.pendingLabel = null; state.pendingPack = null; renderBookIn(); };
  $("#bookin-form").onsubmit = async (e) => {
    e.preventDefault();
    const [kind, id] = e.target.place.value.split(":");
    const payload = { label_text: L.text, pack_text: state.pendingPack?.text || null, tray_id: kind === "tray" ? Number(id) : null, fridge_id: kind === "fridge" ? Number(id) : null };
    await act(() => api(`/api/homes/${state.homeId}/items/book-in`, { body: payload }), "Booked in");
    state.pendingLabel = null; state.pendingPack = null; renderBookIn();
  };
}

function renderDelivery() {
  const body = $("#scan-body");
  body.innerHTML = `<h2>Scan the delivery box</h2>
    <p class="muted">Scan the code on the box before you sign for it.</p>
    ${scannerHtml("sc-box", "BOX-001")}<div id="delivery-result" style="margin-top:12px"></div>`;
  wireScanner("sc-box", async (code) => {
    try {
      const r = await api("/api/deliveries/check", { body: { box_code: code } });
      $("#delivery-result").innerHTML = r.result === "green"
        ? `<div class="verdict green">ACCEPT<small>${esc(r.reason)}</small></div><p>Book the items in next.</p>`
        : `<div class="verdict red">REFUSE<small>${esc(r.reason)}</small></div><p>Do not accept the box. The pharmacy has been told and asked for a replacement.</p>`;
    } catch (e) { $("#delivery-result").innerHTML = `<p class="badge bad">${esc(e.message)}</p>`; }
  });
}

// ---------- FRIDGES ----------
function sparkline(readings, min, max) {
  if (!readings.length) return `<p class="muted">No readings in the last 24 hours</p>`;
  const W = 300, H = 64, temps = readings.map((r) => r.temp_c);
  const lo = Math.min(min - 1, ...temps), hi = Math.max(max + 1, ...temps);
  const t0 = asDate(readings[0].ts).getTime(), t1 = asDate(readings[readings.length - 1].ts).getTime() || t0 + 1;
  const x = (r) => ((asDate(r.ts).getTime() - t0) / Math.max(1, t1 - t0)) * W;
  const y = (t) => H - ((t - lo) / (hi - lo)) * H;
  const pts = readings.map((r) => `${x(r).toFixed(1)},${y(r.temp_c).toFixed(1)}`).join(" ");
  const hot = readings.filter((r) => r.temp_c < min || r.temp_c > max).map((r) => `<circle class="hot" cx="${x(r).toFixed(1)}" cy="${y(r.temp_c).toFixed(1)}" r="2"/>`).join("");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Temperature over the last 24 hours">
    <rect class="band" x="0" y="${y(max).toFixed(1)}" width="${W}" height="${(y(min) - y(max)).toFixed(1)}"/>
    <polyline class="line" points="${pts}"/>${hot}</svg>
    <div class="row between"><small>24 h ago</small><small>range ${min}–${max}°C</small><small>now</small></div>`;
}

async function viewFridges() {
  const v = $("#view");
  const [fridges, excursions, quarantined] = await Promise.all([
    api(`/api/homes/${state.homeId}/fridges`), api(`/api/homes/${state.homeId}/excursions?days=30`),
    api(`/api/homes/${state.homeId}/items?status=quarantined`)]);
  const readings = await Promise.all(fridges.map((f) => api(`/api/fridges/${f.id}/readings?hours=24`)));
  v.innerHTML = `
    <h1>Fridges</h1>
    <div class="grid">${fridges.map((f, i) => `
      <div class="card">
        <div class="row between"><strong>${esc(f.name)}</strong>${f.status === "ok" ? `<span class="badge ok">In range</span>` : f.status === "out_of_range" ? `<span class="badge bad">Out of range</span>` : `<span class="badge">No data</span>`}</div>
        <div class="temp ${f.status === "ok" ? "ok" : f.status === "out_of_range" ? "bad" : "none"}">${f.last_temp_c == null ? "—" : f.last_temp_c.toFixed(1) + "°C"}</div>
        <small>${f.has_sensor ? `Sensor · last reading ${ago(f.last_reading_at)}` : "No sensor: record by hand"}${f.door_open_since ? ` · <span class="badge warn">door open since ${fmtTime(f.door_open_since)}</span>` : ""}</small>
        ${sparkline(readings[i], f.min_c, f.max_c)}
        <form class="row" data-manual="${f.id}" style="margin-top:.5rem">
          <input name="temp_c" type="number" step="0.1" min="-40" max="60" placeholder="Hand reading °C" required style="flex:1;min-width:120px">
          <button class="small">Record</button>
        </form>
      </div>`).join("")}</div>
    <h2>Quarantined items ${quarantined.length ? `<span class="badge warn">${quarantined.length}</span>` : ""}</h2>
    ${quarantined.length ? quarantined.map((it) => `
      <div class="card">
        <strong>${esc(it.medicine_name)} ${esc(it.strength || "")}</strong> <small>label ${esc(it.label_code)} · resident ${esc(it.resident_ref)} · ${esc(it.fridge || "")}</small>
        <p class="muted">Keep it marked "Do not use" until the pharmacist answers.</p>
        ${isSenior() || state.user.role === "pharmacist" ? `
        <form class="form-grid" data-quarantine="${it.id}">
          <div><label>Pharmacist's answer</label><select name="outcome"><option value="release">Safe to use</option><option value="discard">Discard</option></select></div>
          <div><label>Pharmacist who advised</label><input name="advised_by" required></div>
          <div><label>New expiry (if any)</label><input name="new_expiry" type="date"></div>
          <div><label>Note</label><input name="note"></div>
          <div style="align-self:end"><button class="primary">Record answer</button></div>
        </form>` : ""}
      </div>`).join("") : `<div class="card flat empty">No quarantined items</div>`}
    <h2>Temperature excursions (30 days)</h2>
    ${excursions.length ? `<div class="table-wrap card"><table><thead><tr><th>Fridge</th><th>Started</th><th>Ended</th><th>Range</th><th>Minutes out</th><th></th></tr></thead><tbody>
      ${excursions.map((e) => `<tr><td>${esc(e.fridge)}</td><td>${fmtDateTime(e.started_at)}</td><td>${e.ended_at ? fmtDateTime(e.ended_at) : `<span class="badge bad">ongoing</span>`}</td>
        <td>${e.min_c.toFixed(1)}–${e.max_c.toFixed(1)}°C</td><td>${e.assessed ? Math.round(e.minutes_out) : "…"}${e.data_gap ? ` <span class="badge warn">gap</span>` : ""}</td>
        <td>${e.assessed ? `<button class="small" data-open-excursion="${e.id}">Instructions</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="card flat empty">No excursions</div>`}
    <div id="excursion-detail"></div>`;
  v.querySelectorAll("form[data-manual]").forEach((f) => f.onsubmit = async (e) => {
    e.preventDefault();
    await act(() => api(`/api/fridges/${f.dataset.manual}/manual-reading`, { body: formData(f) }), "Reading recorded");
    viewFridges();
  });
  v.querySelectorAll("form[data-quarantine]").forEach((f) => f.onsubmit = async (e) => {
    e.preventDefault();
    await act(() => api(`/api/items/${f.dataset.quarantine}/quarantine-resolution`, { body: formData(f) }), "Answer recorded");
    viewFridges();
  });
  v.querySelectorAll("[data-open-excursion]").forEach((b) => b.onclick = () => showExcursion(Number(b.dataset.openExcursion)));
}

async function showExcursion(id) {
  if (state.tab !== "fridges") { state.tab = "fridges"; localSet("mv_tab", "fridges"); renderShell(); await new Promise((r) => setTimeout(r, 400)); }
  const e = await api(`/api/excursions/${id}`);
  const box = $("#excursion-detail") || $("#view");
  box.innerHTML = `<div class="card" id="exc-${e.id}">
    <h2>${esc(e.fridge)}: what to do with each medicine</h2>
    <p class="muted">Out of range ${fmtDateTime(e.started_at)} to ${e.ended_at ? fmtTime(e.ended_at) : "now"} · ${e.min_c.toFixed(1)}–${e.max_c.toFixed(1)}°C · ${Math.round(e.minutes_out)} minutes</p>
    ${e.decisions.map((d) => `<div class="decision ${esc(d.decision)}">
      <div>${esc(d.instruction)}</div>
      ${d.acknowledged_at ? `<small>Done ${fmtDateTime(d.acknowledged_at)}</small>` : `<button class="small" data-ack="${d.id}">Done</button>`}
    </div>`).join("") || `<p class="muted">No medicines were in this fridge.</p>`}
    <p><small>Instructions come only from pharmacist-approved stability rules. If unsure, the system says quarantine and ask the pharmacist.</small></p>
  </div>`;
  box.querySelectorAll("[data-ack]").forEach((b) => b.onclick = async () => { await act(() => api(`/api/decisions/${b.dataset.ack}/acknowledge`, { method: "POST" }), "Marked done"); showExcursion(id); });
  box.scrollIntoView({ behavior: "smooth" });
}

// ---------- STOCK & MAR ----------
async function viewStock() {
  const v = $("#view");
  const [items, residents, recon, replacements] = await Promise.all([
    api(`/api/homes/${state.homeId}/items`), api(`/api/homes/${state.homeId}/residents`),
    api(`/api/homes/${state.homeId}/reconciliation?days=7`), api(`/api/homes/${state.homeId}/replacements`)]);
  const statusBadge = (s) => `<span class="badge ${s === "in_stock" ? "ok" : s === "quarantined" ? "warn" : s === "discarded" ? "bad" : ""}">${esc(s.replace(/_/g, " "))}</span>`;
  const mismatches = recon.filter((r) => r.mismatch);
  v.innerHTML = `
    <h1>Stock & MAR</h1>
    <div class="card">
      <div class="row between"><h2>Physical vs recorded (7 days)</h2>${isSenior() ? `<button class="small" id="run-recon">Check now</button>` : ""}</div>
      <p><span class="badge ${mismatches.length ? "bad" : "ok"}">${mismatches.length} mismatches</span> <span class="badge ok">${recon.filter((r) => !r.mismatch).length} verified</span></p>
      ${recon.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Result</th><th>Detail</th></tr></thead><tbody>
        ${recon.slice(0, 50).map((r) => `<tr><td>${fmtDateTime(r.created_at)}</td><td><span class="badge ${r.mismatch ? "bad" : "ok"}">${esc(r.outcome.replace(/_/g, " "))}</span></td><td>${esc(r.detail)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">Nothing reconciled yet.</p>`}
    </div>
    <div class="card">
      <h2>Digital MAR chart</h2>
      <div class="row"><select id="mar-resident">${residents.map((r) => `<option value="${r.id}">${esc(r.display_name || r.ref)}</option>`).join("")}</select>
      <button id="mar-print" class="small">Print</button></div>
      <div id="mar" class="table-wrap" style="margin-top:.75rem"></div>
    </div>
    <div class="card">
      <h2>Medicine register</h2>
      ${isSenior() ? `<details><summary>Import administrations from our eMAR (CSV)</summary>
        <p class="muted">Columns: record_id, label_code, timestamp (ISO 8601), outcome (given/refused/spoilt), carer, notes</p>
        <textarea id="emar-csv" placeholder="record_id,label_code,timestamp,outcome,carer"></textarea>
        <button id="emar-import" class="small">Import</button></details>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Medicine</th><th>Resident</th><th>Where</th><th>Expiry</th><th>Status</th></tr></thead><tbody>
        ${items.map((i) => `<tr><td>${esc(i.medicine_name)} ${esc(i.strength || "")}<br><small>${esc(i.label_code)}${i.unit_weight_g ? ` · ${i.unit_weight_g} g` : i.awaiting_weight ? " · waiting for tray weight" : ""}</small></td>
          <td>${esc(i.resident_name || i.resident_ref || "")}</td><td>${esc(i.tray || i.fridge || "")}</td>
          <td>${fmtDate(i.effective_expiry)}${i.adjusted_expiry ? ` <span class="badge info">shortened</span>` : ""}</td><td>${statusBadge(i.status)}</td></tr>`).join("")}
      </tbody></table></div>
    </div>
    <div class="card"><h2>Replacement requests</h2>
      ${replacements.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Reason</th><th>Status</th></tr></thead><tbody>
        ${replacements.map((r) => `<tr><td>${fmtDateTime(r.created_at)}</td><td>${esc(r.reason)}</td><td>${esc(r.status)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">None</p>`}
    </div>`;
  const loadMar = async () => {
    const rid = $("#mar-resident").value; if (!rid) { $("#mar").innerHTML = `<p class="muted">No residents yet.</p>`; return; }
    const m = await api(`/api/homes/${state.homeId}/mar?resident_id=${rid}&days=7`);
    const mark = (cell) => cell.map((c) => `<span class="mark-${c.outcome}" title="${esc(c.outcome)} at ${c.time}">${c.outcome === "given" ? "✓" : c.outcome === "refused" ? "R" : "S"} <small>${c.time}</small></span>`).join("<br>");
    $("#mar").innerHTML = `<table><thead><tr><th>Medicine</th><th>Time</th>${m.days.map((d) => `<th class="mar">${new Date(d + "T00:00:00").toLocaleDateString([], { weekday: "short", day: "numeric" })}</th>`).join("")}</tr></thead><tbody>
      ${m.rows.map((r) => `<tr><td>${esc(r.medicine)} ${esc(r.strength || "")}<br><small>${esc(r.dose_instructions || "")}</small></td><td>${esc(r.time)}</td>${r.cells.map((c) => `<td class="mar">${mark(c) || "<small class='muted'>·</small>"}</td>`).join("")}</tr>`).join("")}
    </tbody></table><p><small>✓ given · R refused · S spoilt</small></p>`;
  };
  $("#mar-resident").onchange = loadMar; loadMar();
  $("#mar-print").onclick = () => window.print();
  const rr = $("#run-recon"); if (rr) rr.onclick = async () => { const r = await act(() => api(`/api/homes/${state.homeId}/reconcile`, { method: "POST" })); toast(`${r.length} new results`); viewStock(); };
  const ei = $("#emar-import"); if (ei) ei.onclick = async () => {
    const r = await act(() => fetch(`/api/homes/${state.homeId}/emar/import`, { method: "POST", headers: { Authorization: `Bearer ${state.token}`, "Content-Type": "text/csv" }, body: $("#emar-csv").value }).then((x) => x.json()));
    toast(`Imported ${r.imported}, duplicates ${r.duplicates_skipped}${r.errors?.length ? `, ${r.errors.length} errors` : ""}`);
  };
}

// ---------- DASHBOARD ----------
async function viewDashboard() {
  const v = $("#view");
  const d = await api("/api/dashboard?days=7");
  const kpi = (n, l) => `<div class="card"><div class="kpi">${n}</div><div class="kpi-label">${l}</div></div>`;
  v.innerHTML = `
    <h1>Dashboard <small class="muted">last 7 days</small></h1>
    <div class="grid">
      ${kpi(d.homes.length, "homes")}
      ${kpi(d.totals.open_alerts, `open alerts (${d.totals.open_critical} critical)`)}
      ${kpi(d.totals.mismatches_caught, "stock mismatches caught")}
      ${kpi(d.totals.excursions, "fridge excursions")}
      ${kpi(d.totals.fridge_alerts_handled, "fridge alerts handled")}
      ${kpi(Math.round(d.totals.staff_minutes_saved_estimate / 60 * 10) / 10 + " h", "staff time saved (estimate)")}
    </div>
    <div class="card table-wrap"><table><thead><tr><th>Home</th><th>Fridges</th><th>Alerts</th><th>Mismatches</th><th>Excursions</th><th>Quarantined</th><th>Expiring 7d</th><th>£/month</th></tr></thead><tbody>
      ${d.homes.map((h) => `<tr><td><strong>${esc(h.home.name)}</strong>
          ${h.fridges_out_of_range.length ? `<br><span class="badge bad">Out of range: ${esc(h.fridges_out_of_range.join(", "))}</span>` : ""}
          ${h.sensors_offline.length ? `<br><span class="badge warn">Offline: ${esc(h.sensors_offline.join(", "))}</span>` : ""}</td>
        <td>${h.fridges}</td><td>${h.open_alerts}${h.open_critical ? ` <span class="badge bad">${h.open_critical}</span>` : ""}</td>
        <td>${h.mismatches_caught}</td><td>${h.excursions}</td><td>${h.items_quarantined}</td><td>${h.items_expiring_7d}</td><td>£${h.monthly_price_gbp}</td></tr>`).join("")}
    </tbody></table></div>
    <p><small>Time saved uses assumptions to be replaced by pilot measurements: ${d.assumptions.minutes_per_manual_fridge_check} min per manual fridge check per day,
      ${d.assumptions.minutes_per_excursion_phone_decision} min per excursion phone call, ${d.assumptions.minutes_per_mismatch_investigation} min per mismatch found at a manual count.</small></p>`;
}

// ---------- AUDIT ----------
async function viewAudit() {
  const v = $("#view");
  const rows = await api(`/api/homes/${state.homeId}/audit?limit=200`);
  v.innerHTML = `
    <h1>Audit log</h1>
    <div class="card row">
      ${isManager() ? `<button class="primary" id="export">Export CSV for CQC</button><button id="verify">Check log is untampered</button>` : `<small>Managers can export the log.</small>`}
      <span id="verify-result"></span>
    </div>
    <div class="card table-wrap"><table><thead><tr><th>Time</th><th>Who</th><th>What</th><th>Details</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${fmtDateTime(r.ts)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td><small>${esc(r.details ? JSON.stringify(r.details) : "")}</small></td></tr>`).join("")}
    </tbody></table></div>`;
  const ex = $("#export"); if (ex) ex.onclick = async () => {
    const res = await fetch(`/api/homes/${state.homeId}/audit.csv`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (!res.ok) { toast("Export failed"); return; }
    const url = URL.createObjectURL(await res.blob()); const a = document.createElement("a");
    a.href = url; a.download = `medverify-audit-${state.homeId}.csv`; a.click(); URL.revokeObjectURL(url);
  };
  const vb = $("#verify"); if (vb) vb.onclick = async () => {
    const r = await api("/api/audit/verify");
    $("#verify-result").innerHTML = r.intact ? `<span class="badge ok">Intact: no entries changed or removed</span>` : `<span class="badge bad">Broken at entry ${r.first_bad_entry}</span>`;
  };
}

// ---------- RULES ----------
async function viewRules() {
  const v = $("#view");
  const rules = await api("/api/rules");
  const canEdit = ["pharmacist", "admin"].includes(state.user.role);
  v.innerHTML = `
    <h1>Stability rules</h1>
    <p class="notice">Every "safe to use" rule must come from published stability data (e.g. the SmPC) and be approved by a named pharmacist. Draft rules are never used: items without an approved rule are quarantined.</p>
    ${rules.map((r) => `<div class="card">
      <div class="row between"><strong>${esc(r.name)}</strong><span class="badge ${r.usable ? "ok" : r.status === "retired" ? "" : "warn"}">${r.usable ? "in use" : esc(r.status)}</span></div>
      <p>Matches ${r.match_gtin ? `GTIN ${esc(r.match_gtin)}` : `names containing "${esc(r.match_name)}"`} · discard below ${r.min_allowed_c}°C or above ${r.max_allowed_c}°C ·
        max ${Math.round(r.max_minutes_out)} min out of 2–8°C${r.new_expiry_days != null ? ` · new expiry ${r.new_expiry_days} days` : ""}</p>
      <small>Source: ${esc(r.source_reference || "none")}<br>${r.approved_by_name ? `Approved by ${esc(r.approved_by_name)} on ${fmtDate(r.approved_at?.slice(0, 10))}` : "Not approved"}</small>
      ${r.notes ? `<p><small>${esc(r.notes)}</small></p>` : ""}
      <div class="row" style="margin-top:.5rem">
        ${state.user.role === "pharmacist" && r.status === "draft" ? `<button class="small primary" data-approve="${r.id}">Approve</button>` : ""}
        ${canEdit && r.status !== "retired" ? `<button class="small" data-retire="${r.id}">Retire</button>` : ""}
      </div></div>`).join("")}
    ${canEdit ? `<div class="card"><h2>New rule (draft)</h2>
      <form id="rule-form" class="form-grid">
        <div><label>Name</label><input name="name" required></div>
        <div><label>Match medicine name containing</label><input name="match_name"></div>
        <div><label>or GTIN</label><input name="match_gtin" inputmode="numeric"></div>
        <div><label>Discard below (°C)</label><input name="min_allowed_c" type="number" step="0.1" value="2"></div>
        <div><label>Discard above (°C)</label><input name="max_allowed_c" type="number" step="0.1" value="8"></div>
        <div><label>Max total minutes outside 2–8°C</label><input name="max_minutes_out" type="number" min="0" value="0"></div>
        <div><label>New expiry (days) if used</label><input name="new_expiry_days" type="number" min="0"></div>
        <div><label>Published source</label><input name="source_reference" placeholder="SmPC section 6.4, date"></div>
        <div><label>Notes</label><input name="notes"></div>
        <div style="align-self:end"><button class="primary">Save draft</button></div>
      </form></div>` : ""}`;
  v.querySelectorAll("[data-approve]").forEach((b) => b.onclick = async () => { if (!confirm("Approve this rule under your name and registration?")) return; await act(() => api(`/api/rules/${b.dataset.approve}/approve`, { method: "POST" }), "Approved"); viewRules(); });
  v.querySelectorAll("[data-retire]").forEach((b) => b.onclick = async () => { await act(() => api(`/api/rules/${b.dataset.retire}/retire`, { method: "POST" }), "Retired"); viewRules(); });
  const f = $("#rule-form"); if (f) f.onsubmit = async (e) => { e.preventDefault(); await act(() => api("/api/rules", { body: formData(f) }), "Draft saved"); viewRules(); };
}

// ---------- SETTINGS ----------
async function viewSettings() {
  const v = $("#view");
  const [home, users, fridges, trays, residents, quote] = await Promise.all([
    api(`/api/homes/${state.homeId}`), api(`/api/homes/${state.homeId}/users`), api(`/api/homes/${state.homeId}/fridges`),
    api(`/api/homes/${state.homeId}/trays`), api(`/api/homes/${state.homeId}/residents`), api(`/api/homes/${state.homeId}/quote`)]);
  v.innerHTML = `
    <h1>Settings · ${esc(home.name)}</h1>
    <div class="card"><h2>Home</h2>
      <form id="home-form" class="form-grid">
        <div><label>Mismatch window (min)</label><input name="reconcile_window_min" type="number" value="${home.reconcile_window_min}"></div>
        <div><label>Fridge alert delay (min)</label><input name="excursion_grace_min" type="number" value="${home.excursion_grace_min}"></div>
        <div><label>Sensor offline after (min)</label><input name="sensor_offline_min" type="number" value="${home.sensor_offline_min}"></div>
        <div><label>Door open alert after (min)</label><input name="door_open_alert_min" type="number" value="${home.door_open_alert_min}"></div>
        <div><label>eMAR system</label><input name="emar_system" value="${esc(home.emar_system || "")}" placeholder="blank = MedVerify digital MAR"></div>
        <div><label>CQC location ID</label><input name="cqc_location_id" value="${esc(home.cqc_location_id || "")}"></div>
        <label class="inline"><input type="checkbox" name="allow_resident_names" ${home.allow_resident_names ? "checked" : ""}> Data agreement allows resident names</label>
        <div style="align-self:end"><button class="primary">Save</button></div>
      </form></div>
    <div class="card"><h2>Subscription</h2>
      ${quote.lines.map((l) => `<div class="row between"><span>${esc(l.plan.replace(/_/g, " "))} × ${l.qty}</span><span>£${l.total_gbp.toFixed(2)}</span></div>`).join("")}
      <div class="row between"><strong>Per month</strong><strong>£${quote.monthly_gbp.toFixed(2)}</strong></div>
      <small>£${quote.annual_gbp.toFixed(2)} a year · 12-month contract · hardware lent</small></div>
    <div class="grid">
      <div class="card"><h2>Fridges</h2>${fridges.map((f) => `<p>${esc(f.name)} <small>${f.min_c}–${f.max_c}°C ${f.has_sensor ? "· sensor" : ""}</small></p>`).join("")}
        <form id="fridge-form" class="row"><input name="name" placeholder="New fridge name" required style="flex:1"><button class="small">Add</button></form></div>
      <div class="card"><h2>Residents</h2><p class="muted">Internal IDs only${home.allow_resident_names ? "" : " (names not allowed by data agreement)"}.</p>
        ${residents.map((r) => `<p>${esc(r.ref)}${r.display_name ? ` · ${esc(r.display_name)}` : ""}</p>`).join("")}
        <form id="res-form" class="row"><input name="ref" placeholder="e.g. R-004" required style="flex:1"><button class="small">Add</button></form></div>
      <div class="card"><h2>Trays</h2>${trays.map((t) => `<p>${esc(t.label)} <small>${t.resident_ref ? "resident " + esc(t.resident_ref) : "unassigned"} ${t.has_device ? "· device" : ""}</small></p>`).join("")}
        <form id="tray-form" class="stack"><input name="label" placeholder="Tray label" required>
          <select name="fridge_id">${fridges.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join("")}</select>
          <select name="resident_id"><option value="">No resident yet</option>${residents.map((r) => `<option value="${r.id}">${esc(r.ref)}</option>`).join("")}</select>
          <button class="small">Add tray</button></form></div>
    </div>
    <div class="card"><h2>Register a device</h2>
      <form id="dev-form" class="form-grid">
        <div><label>Type</label><select name="kind"><option value="fridge_sensor">Fridge sensor</option><option value="weight_tray">Weight tray</option></select></div>
        <div><label>Device ID</label><input name="external_id" required></div>
        <div><label>Fridge (sensor)</label><select name="fridge_id"><option value="">—</option>${fridges.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join("")}</select></div>
        <div><label>Tray (weight tray)</label><select name="tray_id"><option value="">—</option>${trays.map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join("")}</select></div>
        <div style="align-self:end"><button class="primary">Register</button></div>
      </form><div id="dev-key"></div></div>
    <div class="card"><h2>Staff</h2>
      <div class="table-wrap"><table><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>On shift</th></tr></thead><tbody>
        ${users.map((u) => `<tr><td>${esc(u.display_name)}</td><td>${esc(u.username)}</td><td>${ROLE_NAMES[u.role]}</td><td>${u.on_shift ? "Yes" : ""}</td></tr>`).join("")}</tbody></table></div>
      <form id="user-form" class="form-grid">
        <div><label>Name</label><input name="display_name" required></div>
        <div><label>Username</label><input name="username" required minlength="3"></div>
        <div><label>Role</label><select name="role"><option value="carer">Carer</option><option value="senior">Senior carer</option><option value="manager">Manager</option></select></div>
        <div><label>Temporary password</label><input name="password" type="password" minlength="8" required></div>
        <div><label>Mobile (for text alerts)</label><input name="phone" type="tel"></div>
        <div style="align-self:end"><button class="primary">Add staff</button></div>
      </form></div>
    <div class="card"><h2>Label maker</h2><p class="muted">Produces the QR text for a pharmacy label (for pharmacies without MedVerify labels, or testing).</p>
      <form id="label-form" class="form-grid">
        <div><label>Label code</label><input name="label_code" required></div>
        <div><label>Medicine</label><input name="medicine_name" required></div>
        <div><label>Strength</label><input name="strength"></div>
        <div><label>Resident ID</label><input name="resident_ref" required></div>
        <div><label>Dose instructions</label><input name="dose_instructions"></div>
        <div><label>Times (08:00;18:00)</label><input name="dose_times"></div>
        <div><label>Expiry</label><input name="expiry" type="date"></div>
        <div style="align-self:end"><button class="primary">Make label</button></div>
      </form><pre id="label-out" style="white-space:pre-wrap;word-break:break-all"></pre></div>`;
  const reload = (msg) => { toast(msg); viewSettings(); };
  $("#home-form").onsubmit = async (e) => { e.preventDefault(); const d = formData(e.target); d.emar_system = d.emar_system || ""; await act(() => api(`/api/homes/${state.homeId}`, { method: "PATCH", body: d })); reload("Saved"); };
  $("#fridge-form").onsubmit = async (e) => { e.preventDefault(); await act(() => api(`/api/homes/${state.homeId}/fridges`, { body: formData(e.target) })); reload("Fridge added"); };
  $("#res-form").onsubmit = async (e) => { e.preventDefault(); await act(() => api(`/api/homes/${state.homeId}/residents`, { body: formData(e.target) })); reload("Resident added"); };
  $("#tray-form").onsubmit = async (e) => { e.preventDefault(); const d = formData(e.target); d.fridge_id = Number(d.fridge_id); d.resident_id = d.resident_id ? Number(d.resident_id) : null; await act(() => api(`/api/homes/${state.homeId}/trays`, { body: d })); reload("Tray added"); };
  $("#user-form").onsubmit = async (e) => { e.preventDefault(); await act(() => api(`/api/homes/${state.homeId}/users`, { body: formData(e.target) })); reload("Staff added"); };
  $("#dev-form").onsubmit = async (e) => {
    e.preventDefault(); const d = formData(e.target); d.fridge_id = d.fridge_id ? Number(d.fridge_id) : null; d.tray_id = d.tray_id ? Number(d.tray_id) : null;
    const r = await act(() => api(`/api/homes/${state.homeId}/devices`, { body: d }), "Device registered");
    $("#dev-key").innerHTML = `<p class="notice">Device key (shown once — put it on the device now): <code>${esc(r.api_key)}</code></p>`;
  };
  $("#label-form").onsubmit = async (e) => {
    e.preventDefault(); const d = formData(e.target); d.dose_times = (d.dose_times || "").split(/[;,]/).map((s) => s.trim()).filter(Boolean);
    const r = await act(() => api("/api/labels/build", { body: d })); $("#label-out").textContent = r.qr_text;
  };
}

// ---------- DISCOVERY (Phase 1) ----------
async function viewDiscovery() {
  const v = $("#view");
  const [s, list] = await Promise.all([api("/api/discovery/summary"), api("/api/discovery/interviews")]);
  const sig = Object.values(s.signals);
  v.innerHTML = `
    <h1>Customer discovery</h1>
    <p class="muted">30 interviews in 90 days. Ask about the last time it happened, never "would you use…". Never record anything that identifies a resident.</p>
    <div class="grid">
      <div class="card"><div class="kpi">${s.total}/${s.target_total}</div><div class="kpi-label">interviews done</div>
        ${Object.entries(s.by_group).map(([g, c]) => `<div class="row between"><small>${esc(g.replace("_", " "))}</small><small>${c.done}/${c.target}</small></div>`).join("")}</div>
      <div class="card"><div class="kpi">${s.gate_validate_value}/10</div><div class="kpi-label">Gate 1: interviews scoring a problem 2 or 3</div>
        <span class="badge ${s.gate_validate_met ? "ok" : "warn"}">${s.gate_validate_met ? "Gate met: move to Prototype" : "Not yet"}</span></div>
      <div class="card"><div class="kpi">${esc(s.headline_label || "—")}</div><div class="kpi-label">headline problem (most 3s)</div></div>
    </div>
    <div class="card table-wrap"><table><thead><tr><th>Go signal</th><th>Rule</th><th>Now</th><th></th></tr></thead><tbody>
      ${sig.map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(x.rule)}</td><td>${x.value}/${x.target}</td><td><span class="badge ${x.met ? "ok" : "warn"}">${x.met ? "go" : "not yet"}</span></td></tr>`).join("")}
    </tbody></table>
    ${s.not_written_up_within_24h.length ? `<p class="badge warn">${s.not_written_up_within_24h.length} interviews not written up within 24 hours</p>` : ""}</div>
    <div class="card"><h2>Add interview</h2>
      <p class="muted">Score 0 never happens · 1 happens but tolerated · 2 painful and recurring · 3 painful and already spent money or time fixing it.</p>
      <form id="iv-form" class="form-grid">
        <div><label>Group</label><select name="group"><option value="manager">Care home manager</option><option value="carer">Senior carer / nurse</option><option value="pharmacist">Community pharmacist</option><option value="icb_pharmacist">NHS care home pharmacist</option></select></div>
        <div><label>Interviewee (initials or role)</label><input name="interviewee_label" required></div>
        <div><label>Organisation</label><input name="organisation"></div>
        <div><label>Date</label><input name="held_on" type="date" required value="${new Date().toISOString().slice(0, 10)}"></div>
        ${["stock_mismatch", "fridge_excursion", "delivery_temperature"].map((p) => `<div><label>${p.replace("_", " ")} (0–3)</label><input name="score_${p}" type="number" min="0" max="3" value="0" required></div>`).join("")}
        <label class="inline"><input type="checkbox" name="still_on_paper"> Home still on paper charts</label>
        <label class="inline"><input type="checkbox" name="pilot_interest"> Would take part in a pilot</label>
        <div><label>Money or time already spent</label><input name="money_or_time_spent"></div>
        <div><label>Workarounds they built</label><input name="workarounds"></div>
        <div style="grid-column:1/-1"><label>Notes (no resident details)</label><textarea name="notes"></textarea></div>
        <div style="align-self:end"><button class="primary">Save</button></div>
      </form></div>
    <div class="card table-wrap"><table><thead><tr><th>Date</th><th>Who</th><th>Group</th><th>Stock</th><th>Fridge</th><th>Delivery</th><th>Paper</th><th>Pilot</th><th></th></tr></thead><tbody>
      ${list.map((i) => `<tr><td>${fmtDate(i.held_on)}</td><td>${esc(i.interviewee_label)}<br><small>${esc(i.organisation || "")}</small></td><td>${esc(i.group)}</td>
        <td>${i.score_stock_mismatch}</td><td>${i.score_fridge_excursion}</td><td>${i.score_delivery_temperature}</td><td>${i.still_on_paper ? "Yes" : ""}</td><td>${i.pilot_interest ? "Yes" : ""}</td>
        <td><button class="small" data-del="${i.id}">Delete</button></td></tr>`).join("")}
    </tbody></table></div>`;
  $("#iv-form").onsubmit = async (e) => { e.preventDefault(); const d = formData(e.target); d.written_up = true; await act(() => api("/api/discovery/interviews", { body: d }), "Saved"); viewDiscovery(); };
  v.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { if (!confirm("Delete this interview?")) return; await act(() => api(`/api/discovery/interviews/${b.dataset.del}`, { method: "DELETE" })); viewDiscovery(); });
}

const VIEWS = { now: viewNow, scan: viewScan, fridges: viewFridges, stock: viewStock, dashboard: viewDashboard, audit: viewAudit, rules: viewRules, settings: viewSettings, discovery: viewDiscovery };

// ---------- boot ----------
async function boot() {
  if (!state.token) { renderLogin(); return; }
  try {
    state.user = await api("/api/me");
    state.homes = await api("/api/homes");
  } catch { renderLogin(); return; }
  if (!state.homes.some((h) => h.id === state.homeId)) state.homeId = state.user.home_id || state.homes[0]?.id || null;
  renderShell();
}

// Keep the alert count fresh while the app is open (staff alerts arrive in-app).
setInterval(() => { if (state.user && document.visibilityState === "visible") refreshAlertBadge(); }, 30000);
boot();
