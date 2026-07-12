/* Orbital AI — Fleet Management Dashboard (build-free SPA).
   Talks to the FastAPI cloud on the same origin; live updates over /ws. */

const API = "";
const state = {
  facility: null,
  industries: [],
  vendors: [],
  robots: [],
  alerts: [],
  activeTab: "All",
  ws: null,
};

// ── helpers ──────────────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function getJSON(path) { const r = await fetch(API + path); if (!r.ok) throw new Error(path + " -> " + r.status); return r.json(); }
async function postJSON(path, body) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

const STATE_STYLE = {
  active:   ["bg-emerald-500/15", "text-emerald-400", "Active"],
  idle:     ["bg-slate-500/15", "text-slate-300", "Idle"],
  charging: ["bg-sky-500/15", "text-sky-400", "Charging"],
  halted:   ["bg-red-500/20", "text-red-400", "Halted"],
  offline:  ["bg-slate-700", "text-slate-500", "Offline"],
};
function driftColor(d) { return d >= 0.5 ? "text-red-400" : d >= 0.1 ? "text-amber-400" : "text-emerald-400"; }
function batteryColor(p) { return p < 15 ? "bg-red-500" : p < 50 ? "bg-amber" : "bg-emerald-500"; }
function fmtSecs(s) { if (s == null) return "—"; if (s < 90) return `${Math.round(s)}s`; if (s < 5400) return `${(s/60).toFixed(1)}m`; return `${(s/3600).toFixed(1)}h`; }
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString(); }

// ── stats ────────────────────────────────────────────────────────────────────
function renderStats() {
  const total = state.robots.length;
  const active = state.robots.filter((r) => r.state === "active").length;
  const halted = state.robots.filter((r) => r.state === "halted").length;
  const avgDrift = total ? state.robots.reduce((a, r) => a + r.drift_delta_m, 0) / total : 0;
  const openAlerts = state.alerts.filter((a) => !a.acknowledged).length;
  const cards = [
    ["Fleet", total, "text-slate-100"],
    ["Active", active, "text-emerald-400"],
    ["Halted", halted, halted ? "text-red-400" : "text-slate-100"],
    ["Avg drift", avgDrift.toFixed(3) + " m", driftColor(avgDrift)],
    ["Open alerts", openAlerts, openAlerts ? "text-amber-400" : "text-slate-100"],
  ];
  $("#stats").innerHTML = "";
  for (const [label, val, cls] of cards) {
    $("#stats").appendChild(el(`
      <div class="bg-ink-800 border border-ink-600 rounded-xl px-4 py-3">
        <div class="text-xs text-slate-400">${label}</div>
        <div class="text-2xl font-bold ${cls} mt-1">${val}</div>
      </div>`));
  }
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function renderTabs() {
  const tabs = ["All", ...state.industries];
  $("#tabs").innerHTML = "";
  for (const t of tabs) {
    const activeCls = t === state.activeTab ? "bg-amber text-ink-900 font-semibold" : "bg-ink-700 text-slate-300 hover:bg-ink-600";
    const btn = el(`<button class="px-3 py-1.5 rounded-lg text-sm transition ${activeCls}">${esc(t)}</button>`);
    btn.onclick = () => { state.activeTab = t; renderTabs(); renderFleet(); };
    $("#tabs").appendChild(btn);
  }
}

// ── fleet ─────────────────────────────────────────────────────────────────────
function renderFleet() {
  const list = state.activeTab === "All" ? state.robots : state.robots.filter((r) => r.industry === state.activeTab);
  const grid = $("#fleet");
  grid.innerHTML = "";
  if (!list.length) { grid.appendChild(el(`<div class="text-slate-500 text-sm">No robots in this category.</div>`)); return; }
  for (const r of list) {
    const [bg, fg, label] = STATE_STYLE[r.state] || STATE_STYLE.offline;
    const card = el(`
      <div class="bg-ink-800 border border-ink-600 rounded-xl p-4 hover:border-amber/50 transition">
        <div class="flex items-start justify-between gap-2">
          <div>
            <div class="font-semibold">${esc(r.vendor)} <span class="text-slate-400 font-normal">${esc(r.model)}</span></div>
            <div class="text-xs text-slate-500 mt-0.5">${esc(r.id)} · ${esc(r.industry)}</div>
          </div>
          <span class="text-xs px-2 py-1 rounded-full ${bg} ${fg} whitespace-nowrap">${label}</span>
        </div>
        <div class="mt-3 grid grid-cols-2 gap-3 text-sm">
          <div>
            <div class="text-xs text-slate-400">Drift Δ</div>
            <div class="font-semibold ${driftColor(r.drift_delta_m)}">${r.drift_delta_m.toFixed(3)} m</div>
          </div>
          <div>
            <div class="text-xs text-slate-400">Battery</div>
            <div class="flex items-center gap-2 mt-1">
              <div class="h-1.5 flex-1 rounded-full bg-ink-600 overflow-hidden">
                <div class="h-full ${batteryColor(r.battery_pct)}" style="width:${Math.max(2, r.battery_pct)}%"></div>
              </div>
              <span class="text-xs text-slate-400">${Math.round(r.battery_pct)}%</span>
            </div>
          </div>
        </div>
        <div class="mt-2 text-xs text-slate-400 truncate">Task: <span class="text-slate-300">${esc(r.current_task || "—")}</span></div>
        <div class="mt-3 flex gap-2">
          <button data-act="details" class="flex-1 px-3 py-1.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-sm">Details</button>
          ${r.state === "halted"
            ? `<button data-act="resume" class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium">Resume</button>`
            : `<button data-act="estop" class="px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium">E-Stop</button>`}
        </div>
      </div>`);
    card.querySelector('[data-act="details"]').onclick = () => openRobot(r.id);
    const estopBtn = card.querySelector('[data-act="estop"]');
    if (estopBtn) estopBtn.onclick = () => postJSON(`/api/dashboard/robot/${r.id}/estop`).catch(console.error);
    const resumeBtn = card.querySelector('[data-act="resume"]');
    if (resumeBtn) resumeBtn.onclick = () => postJSON(`/api/dashboard/robot/${r.id}/resume`).catch(console.error);
    grid.appendChild(card);
  }
}

// ── alerts ────────────────────────────────────────────────────────────────────
const SEV_STYLE = { critical: "text-red-400", warning: "text-amber-400", info: "text-sky-400" };
function renderAlerts() {
  $("#alert-count").textContent = state.alerts.filter((a) => !a.acknowledged).length;
  const box = $("#alerts");
  box.innerHTML = "";
  if (!state.alerts.length) { box.appendChild(el(`<div class="px-4 py-6 text-sm text-slate-500">No alerts. Fleet nominal.</div>`)); return; }
  for (const a of state.alerts.slice(0, 60)) {
    const sev = SEV_STYLE[a.severity] || "text-slate-300";
    const row = el(`
      <div class="px-4 py-3 ${a.acknowledged ? "opacity-50" : ""}">
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-semibold ${sev} uppercase tracking-wide">${esc(a.type)}</span>
          <span class="text-[11px] text-slate-500">${fmtTime(a.ts)}</span>
        </div>
        <div class="text-sm mt-1 text-slate-300">${esc(a.message)}</div>
        ${a.acknowledged ? "" : `<button class="mt-2 text-xs text-amber hover:underline">Acknowledge</button>`}
      </div>`);
    const ack = row.querySelector("button");
    if (ack) ack.onclick = async () => { await postJSON(`/api/dashboard/alerts/${a.id}/ack`); a.acknowledged = true; renderAlerts(); renderStats(); };
    box.appendChild(row);
  }
}

// ── benchmark ─────────────────────────────────────────────────────────────────
async function loadBenchmark() {
  try {
    const data = await getJSON("/api/dashboard/benchmark");
    const rows = [...data.vendors].sort((a, b) => a.mean_drift_m - b.mean_drift_m);
    const tb = $("#benchmark");
    tb.innerHTML = "";
    for (const v of rows) {
      tb.appendChild(el(`
        <tr class="border-b border-ink-700/60">
          <td class="px-4 py-2 font-medium">${esc(v.vendor)}</td>
          <td class="px-4 py-2 text-slate-400">${v.robots}</td>
          <td class="px-4 py-2 text-slate-400">${v.samples}</td>
          <td class="px-4 py-2 ${driftColor(v.mean_drift_m)}">${v.mean_drift_m.toFixed(3)} m</td>
          <td class="px-4 py-2 ${driftColor(v.p95_drift_m)}">${v.p95_drift_m.toFixed(3)} m</td>
          <td class="px-4 py-2 text-slate-300">${fmtSecs(v.mtbd_seconds)}</td>
          <td class="px-4 py-2 text-slate-300">${v.mean_recovery_latency_seconds == null ? "—" : v.mean_recovery_latency_seconds + "s"}</td>
          <td class="px-4 py-2 text-slate-300">${v.env_degradation_score ?? "—"}</td>
        </tr>`));
    }
  } catch (e) { console.error(e); }
}

// ── robot detail (business card) ───────────────────────────────────────────────
async function openRobot(id) {
  try {
    const r = await getJSON(`/api/dashboard/robot/${id}`);
    const [bg, fg, label] = STATE_STYLE[r.state] || STATE_STYLE.offline;
    const card = $("#modal-robot .modal-card");
    card.innerHTML = `
      <div class="p-5">
        <div class="flex items-start justify-between">
          <div>
            <div class="text-lg font-bold">${esc(r.vendor)} ${esc(r.model)}</div>
            <div class="text-xs text-slate-500">${esc(r.id)} · ${esc(r.industry)} · ${esc(r.facility_id)}</div>
          </div>
          <span class="text-xs px-2 py-1 rounded-full ${bg} ${fg}">${label}</span>
        </div>
        <p class="text-sm text-slate-400 mt-3">${esc(r.oem_brief)}</p>
        <div class="grid grid-cols-2 gap-3 mt-4 text-sm">
          ${metric("Drift Δ", r.drift_delta_m.toFixed(3) + " m", driftColor(r.drift_delta_m))}
          ${metric("Battery", Math.round(r.battery_pct) + "%")}
          ${metric("MTBD", fmtSecs(r.mtbd_seconds))}
          ${metric("Recovery latency", r.recovery_latency_seconds == null ? "—" : r.recovery_latency_seconds.toFixed(2) + "s")}
          ${metric("Env degradation", r.env_degradation_score ?? "—")}
          ${metric("Uptime", fmtSecs(r.uptime_seconds))}
        </div>
        <div class="mt-4 text-xs text-slate-400 space-y-1">
          <div>Ground truth (ARIA): <span class="text-slate-300">x ${r.pose_external.x.toFixed(2)}, y ${r.pose_external.y.toFixed(2)}</span></div>
          <div>Self-report (robot): <span class="text-slate-300">x ${r.pose_internal.x.toFixed(2)}, y ${r.pose_internal.y.toFixed(2)}</span></div>
          <div>Task: <span class="text-slate-300">${esc(r.current_task || "—")}</span></div>
        </div>
        <div class="mt-5 flex gap-2 justify-end">
          <button id="modal-close" class="px-3 py-2 rounded-lg bg-ink-700 hover:bg-ink-600 text-sm">Close</button>
          ${r.state === "halted"
            ? `<button id="modal-action" class="px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium">Resume</button>`
            : `<button id="modal-action" class="px-3 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium">E-Stop</button>`}
        </div>
      </div>`;
    card.querySelector("#modal-close").onclick = closeModals;
    card.querySelector("#modal-action").onclick = async () => {
      const action = r.state === "halted" ? "resume" : "estop";
      await postJSON(`/api/dashboard/robot/${id}/${action}`);
      closeModals();
    };
    $("#modal-robot").classList.remove("hidden");
  } catch (e) { console.error(e); }
}
function metric(label, val, cls = "text-slate-100") {
  return `<div class="bg-ink-700/50 rounded-lg px-3 py-2"><div class="text-xs text-slate-400">${label}</div><div class="font-semibold ${cls} mt-0.5">${val}</div></div>`;
}

// ── dispatch task ───────────────────────────────────────────────────────────────
function openDispatch() {
  const opts = state.robots.map((r) => `<option value="${r.id}">${esc(r.vendor)} ${esc(r.model)} (${esc(r.id)})</option>`).join("");
  const card = $("#modal-dispatch .modal-card");
  card.innerHTML = `
    <div class="p-5">
      <div class="text-lg font-bold">Dispatch Task</div>
      <p class="text-xs text-slate-500 mt-1">Assign a mission to a robot. In production this routes through Open-RMF on the edge.</p>
      <label class="block text-sm mt-4 mb-1 text-slate-300">Robot</label>
      <select id="d-robot" class="w-full bg-ink-700 border border-ink-600 rounded-lg px-3 py-2 text-sm">${opts}</select>
      <label class="block text-sm mt-3 mb-1 text-slate-300">Task description</label>
      <input id="d-desc" class="w-full bg-ink-700 border border-ink-600 rounded-lg px-3 py-2 text-sm" placeholder="e.g. Restock aisle 4" />
      <div class="mt-5 flex gap-2 justify-end">
        <button id="d-cancel" class="px-3 py-2 rounded-lg bg-ink-700 hover:bg-ink-600 text-sm">Cancel</button>
        <button id="d-submit" class="px-3 py-2 rounded-lg bg-amber hover:bg-amber-600 text-ink-900 text-sm font-semibold">Dispatch</button>
      </div>
    </div>`;
  card.querySelector("#d-cancel").onclick = closeModals;
  card.querySelector("#d-submit").onclick = async () => {
    const robot_id = card.querySelector("#d-robot").value;
    const description = card.querySelector("#d-desc").value.trim() || "Manual task";
    await postJSON("/api/dashboard/tasks", { robot_id, description, waypoints: [] });
    closeModals();
  };
  $("#modal-dispatch").classList.remove("hidden");
}

function closeModals() { $("#modal-robot").classList.add("hidden"); $("#modal-dispatch").classList.add("hidden"); }

// ── live connection ────────────────────────────────────────────────────────────
function setConn(ok) {
  $("#conn-dot").className = `w-2.5 h-2.5 rounded-full ${ok ? "bg-emerald-500 pulse" : "bg-red-500"}`;
  $("#conn-label").textContent = ok ? "live" : "reconnecting…";
}
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connectWS, 2000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "fleet") { state.robots = msg.robots; renderFleet(); renderStats(); }
    else if (msg.type === "alert") {
      state.alerts.unshift(msg.alert); state.alerts = state.alerts.slice(0, 200);
      renderAlerts(); renderStats();
      const box = $("#alerts").firstChild; if (box) box.classList.add("flash");
    }
  };
}

// ── boot ─────────────────────────────────────────────────────────────────────
async function init() {
  $("#btn-dispatch").onclick = openDispatch;
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });

  const fleet = await getJSON("/api/dashboard/fleet");
  state.facility = fleet.facility;
  state.industries = fleet.industries;
  state.vendors = fleet.vendors;
  state.robots = fleet.robots;
  $("#facility-name").textContent = fleet.facility.name;
  renderTabs(); renderFleet(); renderStats();

  state.alerts = await getJSON("/api/dashboard/alerts");
  renderAlerts();
  await loadBenchmark();
  setInterval(loadBenchmark, 5000);
  connectWS();
}

init().catch((e) => { console.error(e); $("#conn-label").textContent = "error"; });
