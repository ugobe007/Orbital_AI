/* Orbital AI — Fleet Control (build-free SPA).
   Same-origin FastAPI cloud; live updates over /ws. Supabase-inspired dark console. */

const API = "";
const EMBED = new URLSearchParams(location.search).has("embed");
const MAX_SPEED = 2.5; // mirrors ORBITAL_MAX_SPEED_MPS
const state = {
  facility: null,
  industries: [],
  vendors: [],
  robots: [],
  sequence: null,       // current fleet mission sequence (theme + objective + per-robot goals)
  alerts: [],
  activeTab: "All",
  statusFilter: null,   // fleet filter by robot state (set from the overview chips)
  connLive: false,
  ws: null,
  map: null,
  selectedRobot: null,
  orch: null,
  oems: [],
  catalog: null,        // /oem-catalog: vendors + scopes + transports + default policies
  wizard: null,         // onboarding wizard working state
};

// Fleet states, ordered for the overview distribution bar + chips.
const STATUS_META = [
  { key: "active",   label: "Working",       color: "#00be7d" },
  { key: "cooldown", label: "Between tasks", color: "#e5484d" },
  { key: "idle",     label: "Idle",          color: "#ffa01f" },
  { key: "charging", label: "Charging",      color: "#00a5da" },
  { key: "halted",   label: "Halted",        color: "#ff3b6b" },
  { key: "offline",  label: "Offline",       color: "#5b667a" },
];

// The control surface Orbital exposes to operators. Each capability is gated by an API scope
// the OEM must unlock — this is the contract 3rd-party robot vendors integrate against.
const CAPABILITIES = [
  { scope: "telemetry.read", label: "Telemetry", glyph: "📡", kind: "monitor", desc: "Live sensor + drift stream" },
  { scope: "state.read", label: "State", glyph: "❤", kind: "monitor", desc: "Battery, mode, health" },
  { scope: "map.read", label: "Spatial map", glyph: "▦", kind: "monitor", desc: "Facility floor + localization" },
  { scope: "camera.read", label: "Camera", glyph: "◉", kind: "monitor", desc: "Overhead + onboard feeds" },
  { scope: "control.velocity", label: "Drive", glyph: "→", kind: "control", desc: "Speed, direction & visual waypoints" },
  { scope: "control.estop", label: "Safety stop", glyph: "■", kind: "control", desc: "Emergency stop / resume" },
  { scope: "control.teleop", label: "Teleop", glyph: "✥", kind: "control", desc: "Direct remote operation" },
  { scope: "mission.dispatch", label: "Mission", glyph: "◆", kind: "control", desc: "Assign tasks & routes" },
];

const FLEET_CATEGORIES = ["All", "Humanoids", "Cleaning", "Delivery", "Inventory"];

function categoryMatches(industry, category) {
  if (category === "All") return true;
  const low = (industry || "").toLowerCase();
  if (category === "Humanoids") return low.includes("humanoid");
  if (category === "Cleaning") return low.includes("clean");
  if (category === "Delivery") return low.includes("deliver");
  if (category === "Inventory") return low.includes("invent");
  return industry === category;
}

function industryAccent(industry) {
  const low = (industry || "").toLowerCase();
  if (low.includes("humanoid")) return "#00a5da";
  if (low.includes("clean")) return "#ffa01f";
  if (low.includes("deliver")) return "#a855f7";
  if (low.includes("invent")) return "#38bdf8";
  return "#3dbfe2";
}

function capStatusKey(cap, partners, robots) {
  if (partners === 0) return "unlocked";
  if (cap.kind === "control" && robots > 0) return "live";
  if (cap.kind === "monitor") return "monitor";
  if (cap.kind === "control" && partners > 0) return "live";
  return "unlocked";
}

function inlineStatus(label, color) {
  return `<span class="sb-inline" style="color:${color}"><span class="sb-inline-dot"></span>${esc(label)}</span>`;
}

// ── helpers ──────────────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const robotById = (id) => state.robots.find((r) => r.id === id);

async function getJSON(path) { const r = await fetch(API + path); if (!r.ok) throw new Error(path + " -> " + r.status); return r.json(); }
async function reqJSON(method, path, body) {
  const r = await fetch(API + path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
  if (!r.ok) {
    let detail = r.status; try { detail = (await r.json()).detail || detail; } catch (_) {}
    const err = new Error(String(detail)); err.status = r.status; err.detail = detail; throw err;
  }
  return r.json();
}
const postJSON = (path, body) => reqJSON("POST", path, body);

async function control(path, body, okMsg) {
  try { await postJSON(path, body); if (okMsg) toast(okMsg); }
  catch (e) {
    if (e.status === 403) toast(`Blocked: ${e.detail}`, "warn");
    else toast(`Failed: ${e.detail}`, "warn");
  }
}
function toast(msg, kind = "info") {
  const c = kind === "warn" ? "bg-red-500/90 text-white" : "bg-surface-raised text-ink border border-line-strong";
  const t = el(`<div class="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-lg shadow-xl text-[13px] ${c}">${esc(msg)}</div>`);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

const STATE_COLOR = {
  active: "#00be7d", idle: "#ffa01f", charging: "#00a5da", cooldown: "#e5484d", halted: "#e5484d", offline: "#828c9b",
};
const STATE_LABEL = {
  active: "Active", idle: "Idle", charging: "Charging", cooldown: "Task done", halted: "Halted", offline: "Offline",
};
const CAP_STATUS_COLOR = { live: "#00be7d", monitor: "#3dbfe2", unlocked: "#828c9b" };
const MODE_LABEL = {
  patrol: "Autonomous patrol", visual_nav: "Visual-nav (SLAM bypass)", manual: "Manual jog",
  charging: "Charging", halted: "Halted", idle: "Idle", cooldown: "Between tasks",
};
const MODE_STYLE = {
  patrol: "text-ink-mut", visual_nav: "text-azure", manual: "text-cta",
  charging: "text-azure", halted: "text-red-400", idle: "text-cta", cooldown: "text-red-400",
};
// Resolve which scopes a vendor's OEM has unlocked (mirrors the cloud scope guard):
// unmanaged vendors are permissive; suspended OEMs grant nothing.
function vendorGrants(vendor) {
  const matches = state.oems.filter((o) => (o.vendor || "").toLowerCase() === (vendor || "").toLowerCase());
  if (!matches.length) return { managed: false, has: () => true };
  const p = matches.find((x) => x.status === "active") || matches[0];
  const granted = new Set(p.status === "suspended" ? [] : p.granted_scopes);
  return { managed: true, has: (s) => granted.has(s) };
}
function driftColor(d) { return d >= 0.5 ? "text-red-400" : d >= 0.1 ? "text-amber-400" : "text-brand"; }
function batteryColor(p) { return p < 15 ? "bg-red-500" : p < 50 ? "bg-amber-400" : "bg-brand"; }
function fmtSecs(s) { if (s == null) return "—"; if (s < 90) return `${Math.round(s)}s`; if (s < 5400) return `${(s/60).toFixed(1)}m`; return `${(s/3600).toFixed(1)}h`; }
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString(); }

// ── overview: live metrics + interactive status distribution ───────────────────
function fleetMetrics() {
  const rb = state.robots;
  const total = rb.length;
  const counts = {};
  for (const s of STATUS_META) counts[s.key] = 0;
  for (const r of rb) counts[r.state] = (counts[r.state] || 0) + 1;
  const nav = rb.filter((r) => r.control_mode === "visual_nav").length;
  const openAlerts = state.alerts.filter((a) => !a.acknowledged).length;
  const avgDrift = total ? rb.reduce((s, r) => s + (r.drift_delta_m || 0), 0) / total : 0;
  const avgBatt = total ? rb.reduce((s, r) => s + (r.battery_pct || 0), 0) / total : 0;
  return { total, counts, nav, openAlerts, avgDrift, avgBatt };
}

function renderStats() {
  const m = fleetMetrics();
  // Four live headline metrics (not static counts — drift + battery are derived live).
  const tiles = [
    ["Fleet", String(m.total), "text-ink"],
    ["Avg drift Δ", m.avgDrift.toFixed(3) + "m", m.avgDrift >= 0.5 ? "text-red-400" : m.avgDrift >= 0.1 ? "text-amber-400" : "text-brand"],
    ["Fleet battery", Math.round(m.avgBatt) + "%", m.avgBatt < 25 ? "text-red-400" : m.avgBatt < 50 ? "text-amber-400" : "text-brand"],
    ["Open alerts", String(m.openAlerts), m.openAlerts ? "text-cta" : "text-ink"],
  ];
  const box = $("#stats"); box.innerHTML = "";
  for (const [label, val, cls] of tiles) {
    box.appendChild(el(`
      <div class="rounded-lg bg-surface-raised border border-line px-2.5 py-2">
        <div class="text-[10px] text-ink-dim leading-tight">${label}</div>
        <div class="text-[17px] font-semibold mono ${cls} mt-0.5 leading-none">${val}</div>
      </div>`));
  }
  renderFleetStatus(m);
  renderTicker(m);
}

// Human labels for the mission leg each robot is on.
const PHASE_LABEL = {
  en_route_pickup: "→ pickup",
  working: "working",
  carrying: "carrying",
  idle: "idle",
};
const PHASE_COLOR = {
  en_route_pickup: "#00a5da",
  working: "#ffa01f",
  carrying: "#00be7d",
  idle: "#5b667a",
};

function renderSequence() {
  const bar = $("#sequence-bar");
  if (!bar) return;
  const s = state.sequence;
  if (!s || !s.label) {
    bar.innerHTML = `<div class="text-[11px] text-ink-dim">Waiting for the fleet to pick up its first sequence…</div>`;
    return;
  }
  const goals = (s.assignments || []).filter((a) => a.goal);
  const chips = goals.slice(0, 9).map((a) => {
    const ph = a.phase || "idle";
    return `<span class="inline-flex items-center gap-1 rounded-md bg-surface-input border border-line px-1.5 py-0.5">
        <span class="mono text-ink-mut">${esc(a.robot_id)}</span>
        <span class="text-ink-dim">${esc(a.goal)}</span>
        <span class="mono text-[9px]" style="color:${PHASE_COLOR[ph] || "#5b667a"}">${PHASE_LABEL[ph] || ph}</span>
      </span>`;
  }).join("");
  const ends = Math.max(0, Math.round(s.ends_in_s ?? 0));
  bar.innerHTML = `
    <div class="flex items-center gap-2 flex-wrap">
      <span class="tag" style="background:#1b1533;color:#b7a6ff;border-color:#7c5cff">SEQUENCE ${esc(String(s.id ?? ""))}</span>
      <span class="font-semibold text-[13px]">${esc(s.label)}</span>
      <span class="text-[11.5px] text-ink-dim">${esc(s.objective || "")}</span>
      <span class="flex-1"></span>
      <span class="text-[10.5px] text-ink-dim mono">new sequence in <span style="color:#b7a6ff">${ends}s</span></span>
    </div>
    <div class="mt-2 flex items-center gap-1.5 flex-wrap text-[10px]">${chips}</div>`;
}

function renderFleetStatus(m) {
  const box = $("#fleet-status");
  if (!box) return;
  const total = m.total || 1;
  const segs = STATUS_META
    .filter((s) => m.counts[s.key] > 0)
    .map((s) => `<div class="status-seg" title="${s.label}: ${m.counts[s.key]}" style="width:${(m.counts[s.key] / total) * 100}%;background:${s.color};${state.statusFilter && state.statusFilter !== s.key ? "opacity:.3" : ""}"></div>`)
    .join("");
  box.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <div class="text-[11px] uppercase tracking-wide text-ink-dim">Fleet status · live</div>
      <div class="text-[10.5px] text-ink-dim mono">${state.statusFilter ? `filtered: ${STATUS_META.find((s)=>s.key===state.statusFilter)?.label || state.statusFilter}` : "click to filter"}</div>
    </div>
    <div class="flex h-2.5 rounded-full overflow-hidden bg-surface-input mb-3">${segs || '<div class="w-full" style="background:#5b667a"></div>'}</div>
    <div id="status-chips" class="grid grid-cols-3 gap-1.5 flex-1 content-start"></div>`;
  const chips = box.querySelector("#status-chips");
  for (const s of STATUS_META) {
    const on = state.statusFilter === s.key;
    const chip = el(`
      <div class="status-chip ${on ? "on" : ""}">
        <span class="tick-dot" style="color:${s.color};background:${s.color}"></span>
        <span class="text-[11px] text-ink-mut flex-1 truncate">${s.label}</span>
        <span class="text-[12px] font-semibold mono ${m.counts[s.key] ? "text-ink" : "text-ink-dim"}">${m.counts[s.key]}</span>
      </div>`);
    chip.onclick = () => {
      if (EMBED) return;
      state.statusFilter = on ? null : s.key;
      renderStats(); renderFleet();
      document.getElementById("sec-fleet")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    chips.appendChild(chip);
  }
}

// ── live monitoring ticker ─────────────────────────────────────────────────────
function renderTicker(m) {
  const track = $("#ticker-track");
  if (!track) return;
  m = m || fleetMetrics();
  const activeOems = state.oems.filter((o) => o.status === "active").length;
  const items = [
    { dot: state.connLive ? "#00be7d" : "#e5484d", label: "Link", val: state.connLive ? "LIVE" : "RECONNECTING" },
    { dot: "#00a5da", label: "Monitoring", val: `${m.total} robots` },
    { dot: "#00be7d", label: "Working", val: m.counts.active },
    { dot: "#e5484d", label: "Between tasks", val: m.counts.cooldown },
    { dot: "#3dbfe2", label: "Visual-nav", val: m.nav },
    { dot: "#ffa01f", label: "Idle", val: m.counts.idle },
    { dot: "#ff3b6b", label: "Halted", val: m.counts.halted },
    { dot: "#ffa01f", label: "Open alerts", val: m.openAlerts },
    { dot: "#00a5da", label: "Avg drift", val: m.avgDrift.toFixed(3) + "m" },
    { dot: "#00be7d", label: "Fleet battery", val: Math.round(m.avgBatt) + "%" },
    { dot: "#7fd6f2", label: "OEM partners", val: `${activeOems}/${state.oems.length}` },
    { dot: "#828c9b", label: "Facility", val: state.facility?.name || "—" },
  ];
  const one = items.map((i) => `
    <span class="tick">
      <span class="tick-dot" style="color:${i.dot};background:${i.dot}"></span>
      <span class="tick-label">${esc(i.label)}</span>
      <span class="tick-val">${esc(i.val)}</span>
    </span>`).join("");
  track.innerHTML = one + one; // duplicate for a seamless -50% loop
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function renderTabs() {
  $("#tabs").innerHTML = "";
  for (const t of FLEET_CATEGORIES) {
    const on = t === state.activeTab;
    const btn = el(`<button type="button" class="seg-tab ${on ? "on" : ""}">${esc(t)}</button>`);
    btn.onclick = () => { state.activeTab = t; renderTabs(); renderFleet(); };
    $("#tabs").appendChild(btn);
  }
}

// ── fleet ─────────────────────────────────────────────────────────────────────
function renderFleetHeader() {
  const k = document.getElementById("fleet-kicker");
  const c = document.getElementById("fleet-count");
  const n = state.robots.length;
  if (k) k.textContent = `FLT-${String(n).padStart(2, "0")}`;
  if (c) c.textContent = `${n} robots · filter by category`;
}

function renderFleet() {
  renderFleetHeader();
  let list = state.robots.filter((r) => categoryMatches(r.industry, state.activeTab));
  if (state.statusFilter) list = list.filter((r) => r.state === state.statusFilter);
  renderFleetFilter();
  const grid = $("#fleet");
  grid.innerHTML = "";
  if (!list.length) {
    const label = state.statusFilter ? ` (${STATUS_META.find((s) => s.key === state.statusFilter)?.label || state.statusFilter})` : "";
    grid.appendChild(el(`<div class="text-ink-dim text-[13px]">No robots in this category${label}.</div>`));
    return;
  }
  for (const r of list) {
    const label = STATE_LABEL[r.state] || r.state;
    const stateColor = STATE_COLOR[r.state] || "#828c9b";
    const sel = r.id === state.selectedRobot;
    const accent = industryAccent(r.industry);
    const speed = r.speed_mps ?? 0;
    const card = el(`
      <div class="card fleet-card ${sel ? "selected" : ""}">
        <div class="flex items-start justify-between gap-2">
          <div class="flex gap-2.5 min-w-0">
            <div class="fleet-icon" style="background:${accent}18;border-color:${accent}44;color:${accent}">⬡</div>
            <div class="min-w-0">
              <div class="font-semibold text-[13px] truncate">${esc(r.vendor)} ${esc(r.model)}</div>
              <div class="text-[11px] mono mt-0.5" style="color:#3dbfe2">${esc(r.id)} · ${esc(r.industry)}</div>
            </div>
          </div>
          <span>${inlineStatus(label, stateColor)}</span>
        </div>
        <div class="mt-2 flex items-center gap-1.5 text-[11px] ${MODE_STYLE[r.control_mode] || "text-ink-mut"}">
          <span class="w-1.5 h-1.5 rounded-full bg-current"></span>${esc(MODE_LABEL[r.control_mode] || r.control_mode)}
        </div>
        <div class="fleet-metrics">
          <div class="fleet-metric">
            <div class="fleet-metric-val ${driftColor(r.drift_delta_m)}">${r.drift_delta_m.toFixed(3)}m</div>
            <div class="fleet-metric-lbl">Drift Δ</div>
          </div>
          <div class="fleet-metric">
            <div class="fleet-metric-val" style="color:#3dbfe2">${speed.toFixed(2)} m/s</div>
            <div class="fleet-metric-lbl">Speed</div>
          </div>
          <div class="fleet-metric">
            <div class="fleet-metric-val" style="color:#3dbfe2">${Math.round(r.battery_pct)}%</div>
            <div class="fleet-metric-lbl">Battery</div>
          </div>
        </div>
        <div class="mt-2.5 h-1 rounded-full bg-surface-input overflow-hidden">
          <div class="h-full ${batteryColor(r.battery_pct)}" style="width:${Math.max(2, r.battery_pct)}%"></div>
        </div>
        <div class="mt-3 flex gap-2">
          <button data-act="select" class="flex-1 px-2 py-1.5 rounded-md bg-surface-input hover:bg-line-strong text-[12px]">Control</button>
          ${r.state === "halted"
            ? `<button data-act="resume" class="flex-1 px-2 py-1.5 rounded-md bg-brand hover:bg-brand-600 text-[#052e1f] text-[12px] font-semibold">Resume</button>`
            : `<button data-act="estop" class="flex-1 px-2 py-1.5 rounded-md text-[12px] font-semibold" style="background:#dc2626;color:#fff">E-Stop</button>`}
          <button data-act="details" class="flex-1 px-2 py-1.5 rounded-md bg-surface-input hover:bg-line-strong text-[12px]">Info</button>
        </div>
      </div>`);
    card.querySelector('[data-act="select"]').onclick = () => selectRobot(r.id);
    card.querySelector('[data-act="details"]').onclick = () => openRobot(r.id);
    const estopBtn = card.querySelector('[data-act="estop"]');
    if (estopBtn) estopBtn.onclick = (e) => { e.stopPropagation(); control(`/api/dashboard/robot/${r.id}/estop`); };
    const resumeBtn = card.querySelector('[data-act="resume"]');
    if (resumeBtn) resumeBtn.onclick = (e) => { e.stopPropagation(); control(`/api/dashboard/robot/${r.id}/resume`); };
    grid.appendChild(card);
  }
}

function renderFleetFilter() {
  const box = $("#fleet-filter");
  if (!box) return;
  box.innerHTML = "";
  if (!state.statusFilter) return;
  const s = STATUS_META.find((x) => x.key === state.statusFilter);
  const chip = el(`<button class="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-input hover:bg-line-strong text-[11.5px]">
      <span class="tick-dot" style="color:${s?.color};background:${s?.color}"></span>
      ${esc(s?.label || state.statusFilter)}<span class="text-ink-dim ml-1">✕ clear</span>
    </button>`);
  chip.onclick = () => { state.statusFilter = null; renderStats(); renderFleet(); };
  box.appendChild(chip);
}

// ── control panel (selected robot) ──────────────────────────────────────────────
const DIRS = [
  ["↖", 135], ["↑", 90], ["↗", 45],
  ["←", 180], ["stop", null], ["→", 0],
  ["↙", 225], ["↓", 270], ["↘", 315],
];
function selectRobot(id) { state.selectedRobot = id; renderMap(); renderControlPanel(); renderFleet(); }

// A scope pill: green "unlocked" or dim "locked · scope" — makes the OEM control contract explicit.
function scopeTag(scope, ok) {
  return ok
    ? `<span class="mono text-[9.5px] px-1.5 py-0.5 rounded bg-brand/10 text-brand">${scope}</span>`
    : `<span class="mono text-[9.5px] px-1.5 py-0.5 rounded bg-white/[0.08] text-ink-dim">🔒 ${scope}</span>`;
}
function sectionHead(title, scope, ok) {
  return `<div class="flex items-center justify-between mb-2">
      <span class="text-[11px] uppercase tracking-wide text-ink-mut">${title}</span>${scopeTag(scope, ok)}
    </div>`;
}

function renderControlPanel() {
  if (EMBED) return;
  const box = $("#control-panel");
  const modeTag = $("#control-mode");
  const r = robotById(state.selectedRobot);
  if (!r) {
    modeTag.textContent = "";
    box.innerHTML = `<div class="text-[13px] text-ink-dim py-6 text-center">Select a robot on the map or a fleet card to drive it.</div>`;
    return;
  }
  const label = STATE_LABEL[r.state] || r.state;
  const stateColor = STATE_COLOR[r.state] || "#828c9b";
  modeTag.innerHTML = `<span class="${MODE_STYLE[r.control_mode] || "text-ink-mut"}">${esc(MODE_LABEL[r.control_mode] || r.control_mode)}</span>`;
  const halted = r.state === "halted";
  const g = vendorGrants(r.vendor);
  const canVel = g.has("control.velocity");
  const canEstop = g.has("control.estop");
  const canMission = g.has("mission.dispatch");
  const dim = (ok) => ok ? "" : "opacity-40 pointer-events-none";

  box.innerHTML = `
    <div class="flex items-center justify-between gap-2">
      <div class="min-w-0">
        <div class="font-semibold text-[13px] truncate">${esc(r.vendor)} ${esc(r.model)}</div>
        <div class="text-[10.5px] text-ink-dim">${esc(r.id)} · ${g.managed ? esc(r.vendor) + " OEM" : "unmanaged (open)"}</div>
      </div>
      ${inlineStatus(label, stateColor)}
    </div>

    ${(() => {
      // Always explain what this robot is doing right now (mission if it has one, else its state).
      const missionText = r.mission
        || (r.state === "charging" ? "Charging at the dock"
          : r.state === "halted" ? "Safety stop engaged — awaiting resume"
          : r.state === "idle" ? "Idle — will join the next sequence"
          : "Autonomous patrol");
      const activity = r.current_task || PHASE_LABEL[r.mission_phase] || MODE_LABEL[r.control_mode] || "";
      const col = PHASE_COLOR[r.mission_phase] || "#5b667a";
      return `<div class="mt-2 rounded-lg border p-2" style="background:#12101f;border-color:#2a2440">
        <div class="text-[9px] uppercase tracking-wide text-ink-dim">What it's doing now</div>
        <div class="text-[12px] font-medium mt-0.5 leading-snug">${esc(missionText)}</div>
        ${activity ? `<div class="text-[10.5px] text-ink-mut mt-0.5"><span style="color:${col}">●</span> ${esc(activity)}</div>` : ""}
      </div>`;
    })()}

    <div class="mt-2.5 rounded-lg bg-surface-raised border border-line p-2.5 ${dim(canVel)}">
      ${sectionHead("Drive", "control.velocity", canVel)}
      <div class="flex items-center justify-between text-[10.5px] text-ink-mut mb-1">
        <span>Speed</span><span class="mono" id="speed-val">${(r.speed_mps ?? 0).toFixed(2)} m/s</span>
      </div>
      <input id="speed" type="range" class="speed" min="0.05" max="${MAX_SPEED}" step="0.05" value="${r.speed_mps ?? 0.6}" ${canVel ? "" : "disabled"} />
      <div class="flex justify-between text-[9.5px] text-ink-dim mono mt-0.5 mb-2"><span>0.05</span><span>${MAX_SPEED.toFixed(1)} m/s</span></div>
      <div class="text-[10.5px] text-ink-mut mb-1">Manual jog — heading</div>
      <div class="dpad max-w-[132px] mx-auto"></div>
      <div class="text-[9.5px] text-ink-dim text-center mt-1.5">overrides patrol · clears waypoints</div>
    </div>

    <div class="mt-2 rounded-lg bg-surface-raised border border-line p-2.5 ${dim(canVel)}">
      ${sectionHead("Navigate — visual waypoints", "control.velocity", canVel)}
      ${r.waypoints && r.waypoints.length
        ? `<div class="flex items-center justify-between">
             <span class="text-[11.5px] text-brand">en route · ${r.waypoints.length} pt${r.waypoints.length > 1 ? "s" : ""}</span>
             <button id="wp-clear" class="text-[11.5px] px-2 py-0.5 rounded-md bg-surface-input hover:bg-line-strong">Clear</button>
           </div>`
        : `<div class="text-[11px] text-ink-dim leading-snug">Click the map to set a waypoint · shift-click to chain. Orbital drives it by camera, bypassing SLAM.</div>`}
    </div>

    <div class="mt-2 rounded-lg bg-surface-raised border border-line p-2.5">
      ${sectionHead("Safety", "control.estop", canEstop)}
      <div class="flex gap-1.5">
        ${halted
          ? `<button id="c-resume" class="flex-1 px-2.5 py-1.5 rounded-md ${canEstop ? "bg-brand hover:bg-brand-600 text-[#052e1f]" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[12px] font-semibold" ${canEstop ? "" : "disabled"}>${canEstop ? "Resume" : "Resume 🔒"}</button>`
          : `<button id="c-estop" class="flex-1 px-2.5 py-1.5 rounded-md ${canEstop ? "bg-red-600 hover:bg-red-500 text-white" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[12px] font-semibold" ${canEstop ? "" : "disabled"}>${canEstop ? "E-Stop" : "E-Stop 🔒"}</button>`}
        <button id="c-mission" class="px-2.5 py-1.5 rounded-md ${canMission ? "bg-surface-input hover:bg-line-strong" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[12px]" ${canMission ? "" : "disabled"} title="mission.dispatch">Task</button>
        <button id="c-details" class="px-2.5 py-1.5 rounded-md bg-surface-input hover:bg-line-strong text-[12px]">Info</button>
      </div>
    </div>`;

  const slider = box.querySelector("#speed");
  const label2 = box.querySelector("#speed-val");
  if (slider) {
    slider.addEventListener("input", () => { label2.textContent = `${(+slider.value).toFixed(2)} m/s`; });
    slider.addEventListener("change", () => control(`/api/dashboard/robot/${r.id}/speed`, { speed_mps: +slider.value }));
  }
  const pad = box.querySelector(".dpad");
  for (const [glyph, heading] of DIRS) {
    if (heading === null) {
      const stop = el(`<button class="stop" title="Stop jog">■</button>`);
      stop.onclick = () => control(`/api/dashboard/robot/${r.id}/drive/stop`);
      pad.appendChild(stop);
    } else {
      const b = el(`<button title="${heading}°">${glyph}</button>`);
      b.onclick = () => control(`/api/dashboard/robot/${r.id}/drive`, { heading_deg: heading, speed_mps: slider ? +slider.value : (r.speed_mps ?? 0.6) });
      pad.appendChild(b);
    }
  }
  const wpClear = box.querySelector("#wp-clear");
  if (wpClear) wpClear.onclick = () => control(`/api/dashboard/robot/${r.id}/navigate/clear`);
  const estop = box.querySelector("#c-estop");
  if (estop && !estop.disabled) estop.onclick = () => control(`/api/dashboard/robot/${r.id}/estop`);
  const resume = box.querySelector("#c-resume");
  if (resume && !resume.disabled) resume.onclick = () => control(`/api/dashboard/robot/${r.id}/resume`);
  const mission = box.querySelector("#c-mission");
  if (mission && !mission.disabled) mission.onclick = () => openDispatch(r.id);
  box.querySelector("#c-details").onclick = () => openRobot(r.id);
}

// ── control capabilities catalog (OEM/operator control contract) ────────────────
function renderCapabilities() {
  const box = $("#capabilities");
  if (!box) return;
  const activeOems = state.oems.filter((o) => o.status === "active");
  box.innerHTML = "";
  for (const cap of CAPABILITIES) {
    const partners = activeOems.filter((o) => (o.granted_scopes || []).includes(cap.scope)).length;
    const robots = state.robots.filter((r) => vendorGrants(r.vendor).has(cap.scope)).length;
    const st = capStatusKey(cap, partners, robots);
    const stLabel = st.toUpperCase();
    const stColor = CAP_STATUS_COLOR[st] || "#828c9b";
    const iconColor = st === "live" ? "var(--brand)" : st === "monitor" ? "var(--azure)" : "var(--ink-dim)";
    box.appendChild(el(`
      <div class="card tight">
        <div class="card-body">
          <div class="flex justify-between items-start mb-2">
            <span class="fleet-icon" style="width:28px;height:28px;background:${iconColor}10;border-color:${iconColor}44;color:${iconColor}">${cap.glyph}</span>
            ${inlineStatus(stLabel, stColor)}
          </div>
          <div class="text-[13px] font-semibold">${cap.label}</div>
          <div class="mono text-[10px] text-ink-dim mt-0.5">${cap.scope}</div>
          <div class="text-[11.5px] text-ink-mut mt-2 leading-snug">${cap.desc}</div>
          <div class="flex justify-between mt-2 pt-2 border-t border-line text-[10.5px] text-ink-dim mono">
            <span>${partners} partner${partners === 1 ? "" : "s"}</span>
            <span style="color:${robots > 0 ? "var(--azure-light)" : "inherit"}">${robots} robot${robots === 1 ? "" : "s"}</span>
          </div>
        </div>
      </div>`));
  }
}

// ── warehouse map ──────────────────────────────────────────────────────────────
const ROBOT_FILL = { active: "#00be7d", idle: "#ffa01f", charging: "#00a5da", cooldown: "#e5484d", halted: "#e5484d", offline: "#5b667a" };

async function loadMap() {
  try { state.map = await getJSON("/api/dashboard/map"); renderMap(); }
  catch (e) { console.error(e); }
}
function clickToWorld(evt, svg) {
  const m = state.map, rect = svg.getBoundingClientRect();
  const fx = (evt.clientX - rect.left) / rect.width;
  const fy = (evt.clientY - rect.top) / rect.height;
  return { x: fx * m.width_m, y: (1 - fy) * m.height_m };
}

function renderMap() {
  const svg = $("#map"), m = state.map;
  if (!svg || !m) return;
  const W = m.width_m, H = m.height_m;
  const Y = (y) => H - y;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.aspectRatio = `${W} / ${H}`;

  const p = [];
  p.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#0b0f18" stroke="#1b2230" stroke-width="0.06"/>`);
  for (let gx = 2; gx < W; gx += 2) p.push(`<line x1="${gx}" y1="0" x2="${gx}" y2="${H}" stroke="#141a26" stroke-width="0.02"/>`);
  for (let gy = 2; gy < H; gy += 2) p.push(`<line x1="0" y1="${gy}" x2="${W}" y2="${gy}" stroke="#141a26" stroke-width="0.02"/>`);

  if (m.dock) {
    const d = m.dock;
    p.push(`<rect x="${d.x}" y="${Y(d.y + d.h)}" width="${d.w}" height="${d.h}" rx="0.08" fill="#0b2e3a" stroke="#00a5da" stroke-width="0.03" opacity="0.85"/>`);
    p.push(`<text x="${d.x + d.w / 2}" y="${Y(d.y + d.h) + d.h / 2 + 0.16}" fill="#3dbfe2" font-size="0.42" text-anchor="middle" opacity="0.95">DOCK</text>`);
  }
  for (const r of m.racks) {
    p.push(`<rect x="${r.x}" y="${Y(r.y + r.h)}" width="${r.w}" height="${r.h}" rx="0.08" fill="#161c28" stroke="#2f3a4c" stroke-width="0.03"/>`);
    p.push(`<text x="${r.x + r.w / 2}" y="${Y(r.y + r.h / 2) + 0.14}" fill="#828c9b" font-size="0.42" text-anchor="middle">${esc(r.id)}</text>`);
  }
  for (const c of (m.charge_stations || [])) {
    p.push(`<circle cx="${c.x}" cy="${Y(c.y)}" r="0.42" fill="#00a5da" opacity="0.16"/>`);
    p.push(`<text x="${c.x}" y="${Y(c.y) + 0.16}" fill="#3dbfe2" font-size="0.5" text-anchor="middle">⚡</text>`);
  }
  // Named work stations — the pick/drop points missions shuttle between (labelled so the
  // operator can read "Aisle AB → Dock" straight off the floor).
  for (const s of (m.stations || [])) {
    p.push(`<rect x="${s.x - 0.34}" y="${Y(s.y) - 0.34}" width="0.68" height="0.68" rx="0.1" fill="#0b2e3a" stroke="#2f6d82" stroke-width="0.035"/>`);
    p.push(`<text x="${s.x}" y="${Y(s.y) + 0.5}" fill="#5f7486" font-size="0.3" text-anchor="middle">${esc(s.id)}</text>`);
  }
  // Overhead camera rig — Orbital's ground-truth localization. Drawn as a violet camera glyph
  // with a soft coverage halo so it's never mistaken for a robot or a waypoint.
  for (const cam of (m.cameras || [])) {
    const cov = cam.coverage_m || 4.0;
    p.push(`<circle cx="${cam.x}" cy="${Y(cam.y)}" r="${cov}" fill="#7c5cff" opacity="0.05"/>`);
    p.push(`<circle cx="${cam.x}" cy="${Y(cam.y)}" r="${cov}" fill="none" stroke="#7c5cff" stroke-width="0.02" stroke-dasharray="0.2 0.22" opacity="0.35"/>`);
    p.push(`<g transform="translate(${cam.x},${Y(cam.y)})">`);
    p.push(`<rect x="-0.26" y="-0.19" width="0.52" height="0.38" rx="0.08" fill="#1b1533" stroke="#7c5cff" stroke-width="0.045"/>`);
    p.push(`<circle cx="0" cy="0" r="0.11" fill="none" stroke="#b7a6ff" stroke-width="0.05"/>`);
    p.push(`<rect x="0.2" y="-0.1" width="0.14" height="0.2" rx="0.04" fill="#7c5cff"/>`);
    p.push(`</g>`);
  }

  const byId = Object.fromEntries(state.robots.map((r) => [r.id, r]));
  for (const r of state.robots) {
    const ex = r.pose_external, ins = r.pose_internal;
    const selected = r.id === state.selectedRobot;
    // Hand-off link: dashed amber line to the peer that received this robot's payload.
    const partner = r.handoff_partner ? byId[r.handoff_partner] : null;
    if (partner) {
      const pe = partner.pose_external;
      p.push(`<line x1="${ex.x}" y1="${Y(ex.y)}" x2="${pe.x}" y2="${Y(pe.y)}" stroke="#ffa01f" stroke-width="0.05" stroke-dasharray="0.2 0.16" opacity="0.85"/>`);
      p.push(`<circle cx="${pe.x}" cy="${Y(pe.y)}" r="0.16" fill="#ffa01f" opacity="0.9"/>`);
    }
    if (r.waypoints && r.waypoints.length) {
      // Draw the camera-planned route the robot actually follows (bends around racks),
      // falling back to a straight line to the waypoints if no route is published.
      const line = (r.path && r.path.length) ? r.path : r.waypoints;
      const pts = [`${ex.x},${Y(ex.y)}`, ...line.map((w) => `${w.x},${Y(w.y)}`)].join(" ");
      p.push(`<polyline points="${pts}" fill="none" stroke="#00a5da" stroke-width="0.05" stroke-dasharray="0.25 0.18" opacity="0.9"/>`);
      r.waypoints.forEach((w, i) => {
        const last = i === r.waypoints.length - 1;
        p.push(`<circle cx="${w.x}" cy="${Y(w.y)}" r="${last ? 0.24 : 0.16}" fill="${last ? "#00a5da" : "#0b0f18"}" stroke="#00a5da" stroke-width="0.05"/>`);
      });
    }
    // manual jog heading indicator
    if (r.control_mode === "manual") {
      p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.42" fill="none" stroke="#ffa01f" stroke-width="0.05" stroke-dasharray="0.12 0.1"/>`);
    }
    if (r.drift_delta_m > 0.05) {
      p.push(`<line x1="${ins.x}" y1="${Y(ins.y)}" x2="${ex.x}" y2="${Y(ex.y)}" stroke="#4a5568" stroke-width="0.03" stroke-dasharray="0.12 0.12" opacity="0.85"/>`);
      p.push(`<circle cx="${ins.x}" cy="${Y(ins.y)}" r="0.16" fill="none" stroke="#828c9b" stroke-width="0.045" opacity="0.85"/>`);
    }
    if (selected) p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.46" fill="none" stroke="#00a5da" stroke-width="0.07"/>`);
    const fill = ROBOT_FILL[r.state] || ROBOT_FILL.offline;
    const halo = r.state === "active" ? `<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.4" fill="${fill}" opacity="0.16" class="robot-pulse"/>` : "";
    p.push(`<g data-robot="${esc(r.id)}" style="cursor:pointer">`);
    p.push(halo);
    p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.24" fill="${fill}" stroke="#0b0f18" stroke-width="0.06"/>`);
    const hx = ex.x + Math.cos(ex.theta) * 0.4, hy = ex.y + Math.sin(ex.theta) * 0.4;
    p.push(`<line x1="${ex.x}" y1="${Y(ex.y)}" x2="${hx}" y2="${Y(hy)}" stroke="#e6eaef" stroke-width="0.05"/>`);
    p.push(`<text x="${ex.x + 0.34}" y="${Y(ex.y) - 0.26}" fill="#aab4c1" font-size="0.36" font-weight="600">${esc(r.id)}</text>`);
    p.push(`</g>`);
  }
  svg.innerHTML = p.join("");
  renderMapSelection();
}

function renderMapSelection() {
  const box = $("#map-selection");
  if (!box) return;
  box.innerHTML = "";
  if (!state.selectedRobot) return;
  const r = robotById(state.selectedRobot);
  const nav = r && r.waypoints && r.waypoints.length;
  box.appendChild(el(`<span class="text-[11px] px-2 py-0.5 rounded-full bg-brand/15 text-brand">${esc(state.selectedRobot)}${nav ? " · en route" : ""}</span>`));
  const desel = el(`<button class="text-[11px] px-2 py-0.5 rounded-md bg-surface-input hover:bg-line-strong">Deselect</button>`);
  desel.onclick = () => { state.selectedRobot = null; renderMap(); renderControlPanel(); renderFleet(); };
  box.appendChild(desel);
}

async function setWaypoint(id, x, y, append) {
  let waypoints = [{ x, y }];
  if (append) {
    const r = robotById(id);
    const existing = (r && r.waypoints) ? r.waypoints.map((w) => ({ x: w.x, y: w.y })) : [];
    waypoints = [...existing, { x, y }];
  }
  try { await postJSON(`/api/dashboard/robot/${id}/navigate`, { waypoints }); toast(`Waypoint set for ${id}`); }
  catch (e) {
    if (e.status === 403) toast(`Blocked: ${e.detail} — grant control.velocity to this OEM`, "warn");
    else toast(`Failed: ${e.detail}`, "warn");
  }
}

function wireMap() {
  const svg = $("#map");
  if (!svg) return;
  svg.addEventListener("click", (e) => {
    const hit = e.target.closest("[data-robot]");
    if (hit) { selectRobot(hit.getAttribute("data-robot")); return; }
    if (state.selectedRobot) {
      const p = clickToWorld(e, svg);
      setWaypoint(state.selectedRobot, +p.x.toFixed(2), +p.y.toFixed(2), e.shiftKey);
    }
  });
}

// ── logic panel ────────────────────────────────────────────────────────────────
function renderLogic() {
  const box = $("#logic");
  if (!box) return;
  const o = state.orch;
  const decisions = (o && o.decisions) || [];
  const auto = decisions.filter((d) => d.auto_executed).length;
  const orchLine = o
    ? `${o.enabled ? "on" : "off"} · ${decisions.length} decision${decisions.length === 1 ? "" : "s"} · ${auto} auto-executed`
    : "—";
  $("#logic-orch").textContent = o ? `orchestrator ${orchLine}` : "";
  const cards = [
    ["1 · Control hierarchy",
     "Each tick resolves one command source per robot in priority order: <span class='text-brand'>visual-nav waypoints</span> → <span class='text-amber-400'>manual jog</span> → <span class='text-ink-mut'>autonomous patrol</span>. Setting one supersedes the others."],
    ["2 · Drift &amp; ARIA correction",
     "Overhead cameras give the <span class='text-brand'>ground-truth pose</span>; the robot's onboard SLAM <span class='text-ink-mut'>self-report</span> accumulates odometric drift. ARIA decays that drift back toward zero every tick — the dashed link on the map is the live gap."],
    ["3 · Safety halt",
     "When drift crosses the halt threshold, ARIA fires an <span class='text-red-400'>auto E-Stop</span> + alert. Sim-triggered halts re-converge and auto-recover; a manual E-Stop waits for an operator Resume."],
    ["4 · Orchestrator",
     `A supervisory loop scans the fleet on a fixed cadence and takes safety-first actions — proactive charge dispatch on low battery, auto E-Stop on critical anomalies. <span class="text-ink-mut mono">${esc(orchLine)}</span>`],
  ];
  box.innerHTML = "";
  for (const [title, body] of cards) {
    box.appendChild(el(`
      <div class="bg-surface-raised border border-line rounded-lg p-3.5">
        <div class="font-semibold text-[12.5px] mb-1.5">${title}</div>
        <div class="text-[12px] text-ink-mut leading-relaxed">${body}</div>
      </div>`));
  }
}
async function loadOrchestrator() {
  try { state.orch = await getJSON("/api/dashboard/orchestrator"); renderLogic(); }
  catch (_) {}
}

// ── alerts ────────────────────────────────────────────────────────────────────
const SEV_STYLE = { critical: "text-red-400", warning: "text-amber-400", info: "text-sky-300" };
const SEV_DOT = { critical: "#ff3b6b", warning: "#ffa01f", info: "#3dbfe2" };
async function ackAlert(id) {
  const a = state.alerts.find((x) => x.id === id);
  if (!a || a.acknowledged) return;
  await postJSON(`/api/dashboard/alerts/${id}/ack`);
  a.acknowledged = true;
}
function renderAlerts() {
  const unacked = state.alerts.filter((a) => !a.acknowledged);
  $("#alert-count").textContent = unacked.length;
  const ackAllBtn = $("#ack-all");
  if (ackAllBtn) {
    ackAllBtn.classList.toggle("hidden", unacked.length === 0);
    ackAllBtn.onclick = async () => {
      await Promise.all(unacked.map((a) => ackAlert(a.id)));
      renderAlerts(); renderStats();
    };
  }
  const box = $("#alerts");
  box.innerHTML = "";
  if (!state.alerts.length) { box.appendChild(el(`<div class="px-4 py-5 text-[12px] text-ink-dim">No alerts. Fleet nominal.</div>`)); return; }
  // Unacknowledged first, then most-recent acknowledged — dense single-line rows.
  const ordered = [...state.alerts].sort((a, b) => (a.acknowledged - b.acknowledged) || (b.ts - a.ts));
  for (const a of ordered.slice(0, 60)) {
    const dot = SEV_DOT[a.severity] || "#828c9b";
    const detail = a.message || a.type;
    const row = el(`
      <div class="group flex items-center gap-2 px-3 py-1.5 ${a.acknowledged ? "opacity-40" : "cursor-pointer hover:bg-surface-raised/60"}" title="${esc(a.type)} · ${esc(a.message || "")}">
        <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:${dot}"></span>
        <span class="text-[11.5px] text-ink-mut truncate flex-1">${esc(detail)}</span>
        <span class="text-[9.5px] text-ink-dim mono shrink-0">${fmtTime(a.ts)}</span>
        ${a.acknowledged ? `<span class="text-[9px] text-ink-dim shrink-0">✓</span>` : `<span class="ack text-[10px] text-brand shrink-0 opacity-0 group-hover:opacity-100">ack</span>`}
      </div>`);
    if (!a.acknowledged) row.onclick = async () => { await ackAlert(a.id); renderAlerts(); renderStats(); };
    box.appendChild(row);
  }
}

// ── benchmark ─────────────────────────────────────────────────────────────────
async function loadBenchmark() {
  try {
    const data = await getJSON("/api/dashboard/benchmark");
    const rows = [...data.vendors].sort((a, b) => a.mean_drift_m - b.mean_drift_m);
    const tb = $("#benchmark"); tb.innerHTML = "";
    for (const v of rows) {
      const env = v.env_degradation_score ?? 0;
      const envColor = env >= 0.5 ? "#e5484d" : env >= 0.48 ? "#ffa01f" : "#00be7d";
      tb.appendChild(el(`
        <tr>
          <td class="font-medium">${esc(v.vendor)}</td>
          <td class="mono text-ink-mut">${v.robots}</td>
          <td class="mono text-ink-mut">${Number(v.samples).toLocaleString()}</td>
          <td class="mono" style="color:#3dbfe2">${v.mean_drift_m.toFixed(3)}</td>
          <td class="mono" style="color:#3dbfe2">${v.p95_drift_m.toFixed(3)}</td>
          <td class="mono text-ink-mut">${fmtSecs(v.mtbd_seconds)}</td>
          <td class="mono text-ink-mut">${v.mean_recovery_latency_seconds == null ? "—" : v.mean_recovery_latency_seconds.toFixed(2) + "s"}</td>
          <td>
            <div class="flex items-center gap-2">
              <div class="env-bar"><span style="width:${Math.min(100, env * 100)}%;background:${envColor}"></span></div>
              <span class="mono text-[12px]" style="color:${envColor}">${env.toFixed(3)}</span>
            </div>
          </td>
        </tr>`));
    }
  } catch (e) { console.error(e); }
}

// ── OEM partners & scopes ──────────────────────────────────────────────────────
const ALL_SCOPES = [
  "telemetry.read", "state.read", "control.velocity", "control.estop",
  "control.teleop", "mission.dispatch", "camera.read", "map.read",
];
const OEM_STATUS_COLOR = { active: "#00be7d", pending: "#ffa01f", suspended: "#e5484d" };
async function loadOEMs() {
  try {
    const oems = await getJSON("/api/dashboard/oems");
    state.oems = oems;
    renderCapabilities();
    renderControlPanel();
    renderTicker();
    const tb = $("#oems"); tb.innerHTML = "";
    if (!oems.length) {
      tb.appendChild(el(`<tr><td colspan="7" class="text-ink-dim">No OEM partners yet. Use <button class="text-azure hover:underline" style="background:none;border:none;cursor:pointer;padding:0" onclick="openWizard()">Onboard robot API</button> to register one.</td></tr>`));
      return;
    }
    for (const o of oems) {
      const statusColor = OEM_STATUS_COLOR[o.status] || "#828c9b";
      const readiness = [
        o.control_ready ? `<span style="color:#ffa01f">control</span>` : `<span class="text-ink-dim">control</span>`,
        o.monitor_ready ? `<span style="color:#3dbfe2">monitor</span>` : `<span class="text-ink-dim">monitor</span>`,
      ].join(" · ");
      const row = el(`
        <tr>
          <td class="font-medium">${esc(o.company_name)}</td>
          <td class="text-ink-mut">${esc(o.vendor)}</td>
          <td><span class="transport-tag">${esc(o.transport)}</span></td>
          <td>${inlineStatus(o.status, statusColor)}</td>
          <td class="mono font-semibold" style="color:#00be7d">${o.granted_scopes.length} / ${o.ceiling_scopes.length}</td>
          <td class="text-[12px]">${readiness}</td>
          <td class="text-right"><button class="sb-btn text-[11px]">Manage</button></td>
        </tr>`);
      row.querySelector("button").onclick = () => openOEM(o.oem_id);
      tb.appendChild(row);
    }
  } catch (e) { console.error(e); }
}

async function openOEM(oemId) {
  const o = await getJSON(`/api/dashboard/oems/${oemId}`);
  const ceiling = new Set(o.ceiling_scopes);
  const granted = new Set(o.granted_scopes);
  const rows = ALL_SCOPES.map((s) => {
    const inCeiling = ceiling.has(s);
    const checked = granted.has(s) ? "checked" : "";
    const dis = inCeiling ? "" : "disabled";
    const hint = inCeiling ? "" : `<span class="text-[10.5px] text-ink-dim ml-1">(outside ${esc(o.transport)} ceiling)</span>`;
    return `<label class="flex items-center gap-2 py-1 ${inCeiling ? "" : "opacity-40"}">
        <input type="checkbox" data-scope="${s}" ${checked} ${dis} class="accent-brand" />
        <span class="text-[13px]">${s}</span>${hint}
      </label>`;
  }).join("");
  const suspended = o.status === "suspended";
  const card = $("#modal-oem .modal-card");
  card.innerHTML = `
    <div class="p-5">
      <div class="text-[16px] font-bold">${esc(o.company_name)}</div>
      <div class="text-[11px] text-ink-dim">${esc(o.oem_id)} · ${esc(o.vendor)} · ${esc(o.transport)} · <span class="uppercase">${esc(o.status)}</span></div>
      <p class="text-[12px] text-ink-mut mt-2">Toggle the API scopes this OEM has unlocked for Orbital. Scopes outside their protocol's ceiling can't be granted.</p>
      <div class="mt-4 grid grid-cols-2 gap-x-6">${rows}</div>
      ${o.policies ? `<div class="mt-4 pt-3 border-t border-line">
        <div class="text-[11px] uppercase tracking-wide text-ink-dim mb-1.5">Governance policies</div>
        <div class="flex flex-wrap gap-1.5 text-[11px]">
          <span class="mono px-1.5 py-0.5 rounded bg-surface-input text-ink-mut">max ${(+o.policies.max_speed_mps).toFixed(2)} m/s</span>
          <span class="mono px-1.5 py-0.5 rounded bg-surface-input text-ink-mut">drift halt ${(+o.policies.drift_halt_threshold_m).toFixed(2)} m</span>
          <span class="mono px-1.5 py-0.5 rounded bg-surface-input ${o.policies.auto_estop_on_critical ? "text-brand" : "text-ink-dim"}">auto-estop ${o.policies.auto_estop_on_critical ? "on" : "off"}</span>
          <span class="mono px-1.5 py-0.5 rounded bg-surface-input ${o.policies.require_approval_for_teleop ? "text-brand" : "text-ink-dim"}">teleop approval ${o.policies.require_approval_for_teleop ? "on" : "off"}</span>
          <span class="mono px-1.5 py-0.5 rounded bg-surface-input text-ink-mut">zone: ${esc(o.policies.geofence)}</span>
        </div>
      </div>` : ""}
      <div class="mt-5 flex gap-2 justify-between items-center">
        <div class="flex gap-2">
          <button id="oem-suspend" class="px-3 py-2 rounded-md text-[13px] ${suspended ? "bg-brand hover:bg-brand-600 text-[#052e1f]" : "bg-surface-input hover:bg-line-strong text-red-400"}">${suspended ? "Reactivate" : "Suspend access"}</button>
          <button id="oem-remove" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px] text-ink-dim hover:text-red-400" title="Off-board this partner">Remove</button>
        </div>
        <div class="flex gap-2">
          <button id="oem-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>
          <button id="oem-save" class="px-3 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#1a1204] text-[13px] font-semibold">Save scopes</button>
        </div>
      </div>
    </div>`;
  card.querySelector("#oem-cancel").onclick = closeModals;
  card.querySelector("#oem-remove").onclick = async () => {
    const btn = card.querySelector("#oem-remove");
    if (btn.dataset.confirm !== "1") { btn.dataset.confirm = "1"; btn.textContent = "Click again to confirm"; btn.classList.add("text-red-400"); return; }
    await reqJSON("DELETE", `/api/dashboard/oems/${oemId}`);
    closeModals(); loadOEMs(); loadFleetSoon();
    toast(`${o.company_name} off-boarded`);
  };
  card.querySelector("#oem-suspend").onclick = async () => {
    await postJSON(`/api/dashboard/oems/${oemId}/${suspended ? "reactivate" : "suspend"}`);
    closeModals(); loadOEMs();
    toast(suspended ? "OEM reactivated" : "OEM suspended");
  };
  card.querySelector("#oem-save").onclick = async () => {
    const boxes = [...card.querySelectorAll("input[data-scope]")];
    const want = new Set(boxes.filter((b) => b.checked).map((b) => b.dataset.scope));
    const toGrant = [...want].filter((s) => !granted.has(s));
    const toRevoke = [...granted].filter((s) => !want.has(s));
    if (toGrant.length) await postJSON(`/api/dashboard/oems/${oemId}/grant`, { scopes: toGrant });
    if (toRevoke.length) await postJSON(`/api/dashboard/oems/${oemId}/revoke`, { scopes: toRevoke });
    closeModals(); loadOEMs(); loadFleetSoon();
    toast("Scopes updated");
  };
  $("#modal-oem").classList.remove("hidden");
}

function loadFleetSoon() { getJSON("/api/dashboard/fleet").then((f) => { state.robots = f.robots; if (f.sequence) state.sequence = f.sequence; renderFleet(); renderControlPanel(); renderSequence(); }).catch(() => {}); }

// ── robot detail (business card) ───────────────────────────────────────────────
async function openRobot(id) {
  try {
    const r = await getJSON(`/api/dashboard/robot/${id}`);
    const label = STATE_LABEL[r.state] || r.state;
    const stateColor = STATE_COLOR[r.state] || "#828c9b";
    const card = $("#modal-robot .modal-card");
    card.innerHTML = `
      <div class="p-5">
        <div class="flex items-start justify-between">
          <div>
            <div class="text-[16px] font-bold">${esc(r.vendor)} ${esc(r.model)}</div>
            <div class="text-[11px] text-ink-dim">${esc(r.id)} · ${esc(r.industry)} · ${esc(r.facility_id)}</div>
          </div>
          ${inlineStatus(label, stateColor)}
        </div>
        <p class="text-[13px] text-ink-mut mt-3">${esc(r.oem_brief)}</p>
        <div class="grid grid-cols-2 gap-2.5 mt-4 text-[13px]">
          ${metric("Drift Δ", r.drift_delta_m.toFixed(3) + " m", driftColor(r.drift_delta_m))}
          ${metric("Speed", (r.speed_mps ?? 0).toFixed(2) + " m/s")}
          ${metric("Battery", Math.round(r.battery_pct) + "%")}
          ${metric("MTBD", fmtSecs(r.mtbd_seconds))}
          ${metric("Recovery latency", r.recovery_latency_seconds == null ? "—" : r.recovery_latency_seconds.toFixed(2) + "s")}
          ${metric("Uptime", fmtSecs(r.uptime_seconds))}
        </div>
        <div class="mt-4 text-[12px] text-ink-mut space-y-1">
          <div>Ground truth (ARIA): <span class="text-ink mono">x ${r.pose_external.x.toFixed(2)}, y ${r.pose_external.y.toFixed(2)}</span></div>
          <div>Self-report (robot): <span class="text-ink mono">x ${r.pose_internal.x.toFixed(2)}, y ${r.pose_internal.y.toFixed(2)}</span></div>
          <div>Mode: <span class="${MODE_STYLE[r.control_mode] || "text-ink"}">${esc(MODE_LABEL[r.control_mode] || r.control_mode)}</span></div>
        </div>
        ${vitalsBlock(r.sensors)}
        ${controlNote(r.control)}
        <div class="mt-5 flex gap-2 justify-end">
          <button id="modal-close" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Close</button>
          ${actionButton(r)}
        </div>
      </div>`;
    card.querySelector("#modal-close").onclick = closeModals;
    const actionBtn = card.querySelector("#modal-action");
    if (actionBtn && !actionBtn.disabled) actionBtn.onclick = async () => {
      const action = r.state === "halted" ? "resume" : "estop";
      await control(`/api/dashboard/robot/${id}/${action}`);
      closeModals();
    };
    $("#modal-robot").classList.remove("hidden");
  } catch (e) { console.error(e); }
}

function actionButton(r) {
  const allowed = !r.control || r.control.estop;
  const resume = r.state === "halted";
  const base = "px-3 py-2 rounded-md text-white text-[13px] font-medium";
  if (!allowed) {
    return `<button id="modal-action" disabled title="OEM has not granted control.estop"
      class="${base} bg-surface-input text-ink-dim cursor-not-allowed">${resume ? "Resume" : "E-Stop"} 🔒</button>`;
  }
  return resume
    ? `<button id="modal-action" class="${base} bg-brand hover:bg-brand-600 !text-[#052e1f]">Resume</button>`
    : `<button id="modal-action" class="${base} bg-red-600 hover:bg-red-500">E-Stop</button>`;
}

function controlNote(c) {
  if (!c || !c.managed) return "";
  const chip = (ok, label) =>
    `<span class="px-2 py-0.5 rounded-full text-[10.5px] ${ok ? "bg-brand/15 text-brand" : "bg-red-500/15 text-red-400"}">${ok ? "✓" : "✕"} ${label}</span>`;
  return `<div class="mt-4 flex items-center gap-2 flex-wrap">
      <span class="text-[12px] text-ink-mut">OEM grants:</span>
      ${chip(c.estop, "E-Stop")} ${chip(c.velocity, "Velocity")} ${chip(c.mission, "Mission")}
    </div>`;
}

function vitalsBlock(s) {
  if (!s) return `<div class="mt-4 text-[12px] text-ink-dim italic">No live sensor telemetry yet.</div>`;
  const cells = [];
  if (s.battery) {
    const b = s.battery;
    cells.push(metric("Battery pack", `${b.pct != null ? Math.round(b.pct) + "%" : "—"}`, batteryTempColor(b.temperature_c)));
    if (b.temperature_c != null) cells.push(metric("Batt temp", b.temperature_c.toFixed(1) + " °C", tempColor(b.temperature_c, 45, 55)));
    if (b.voltage_v != null) cells.push(metric("Voltage", b.voltage_v.toFixed(1) + " V"));
    if (b.current_a != null) cells.push(metric("Current", b.current_a.toFixed(1) + " A"));
  }
  if (s.motors && s.motors.length) {
    const hottest = s.motors.reduce((m, x) => (x.temperature_c ?? -1) > (m.temperature_c ?? -1) ? x : m, s.motors[0]);
    cells.push(metric(`Hottest motor (${s.motors.length})`, hottest.temperature_c != null ? `${esc(hottest.joint)} ${hottest.temperature_c.toFixed(0)}°C` : esc(hottest.joint), tempColor(hottest.temperature_c, 60, 75)));
  }
  if (s.spatial) {
    const sp = s.spatial;
    cells.push(metric("Spatial (x,y,z)", `${sp.x.toFixed(1)}, ${sp.y.toFixed(1)}, ${sp.z.toFixed(1)}`));
    if (sp.linear_velocity_mps != null) cells.push(metric("Lin. vel", sp.linear_velocity_mps.toFixed(2) + " m/s"));
  }
  if (s.imu && s.imu.accel && s.imu.accel.length === 3) {
    const mag = Math.hypot(...s.imu.accel);
    cells.push(metric("IMU accel |a|", mag.toFixed(2) + " m/s²"));
  }
  for (const [k, v] of Object.entries(s.temperatures_c || {})) cells.push(metric(`Temp: ${esc(k)}`, Number(v).toFixed(1) + " °C", tempColor(v, 65, 80)));
  return `<div class="mt-4">
      <div class="text-[11px] uppercase tracking-wide text-ink-dim mb-2">Live vitals ${s.ts ? `· ${fmtTime(s.ts)}` : ""}</div>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[13px]">${cells.join("")}</div>
    </div>`;
}
function tempColor(t, warn, hot) { if (t == null) return "text-ink"; return t >= hot ? "text-red-400" : t >= warn ? "text-amber-400" : "text-brand"; }
function batteryTempColor(t) { return tempColor(t, 45, 55); }
function metric(label, val, cls = "text-ink") {
  return `<div class="bg-surface-raised border border-line rounded-lg px-3 py-2"><div class="text-[11px] text-ink-dim">${label}</div><div class="font-semibold mono ${cls} mt-0.5">${val}</div></div>`;
}

// ── dispatch task ───────────────────────────────────────────────────────────────
function openDispatch(presetId) {
  const opts = state.robots.map((r) => `<option value="${r.id}" ${r.id === presetId ? "selected" : ""}>${esc(r.vendor)} ${esc(r.model)} (${esc(r.id)})</option>`).join("");
  const card = $("#modal-dispatch .modal-card");
  card.innerHTML = `
    <div class="p-5">
      <div class="text-[16px] font-bold">Dispatch Task</div>
      <p class="text-[12px] text-ink-dim mt-1">Assign a mission to a robot. In production this routes through Open-RMF on the edge.</p>
      <label class="block text-[13px] mt-4 mb-1 text-ink-mut">Robot</label>
      <select id="d-robot" class="w-full bg-surface-input border border-line rounded-md px-3 py-2 text-[13px]">${opts}</select>
      <label class="block text-[13px] mt-3 mb-1 text-ink-mut">Task description</label>
      <input id="d-desc" class="w-full bg-surface-input border border-line rounded-md px-3 py-2 text-[13px]" placeholder="e.g. Restock aisle 4" />
      <div class="mt-5 flex gap-2 justify-end">
        <button id="d-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>
        <button id="d-submit" class="px-3 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#1a1204] text-[13px] font-semibold">Dispatch</button>
      </div>
    </div>`;
  card.querySelector("#d-cancel").onclick = closeModals;
  card.querySelector("#d-submit").onclick = async () => {
    const robot_id = card.querySelector("#d-robot").value;
    const description = card.querySelector("#d-desc").value.trim() || "Manual task";
    await control("/api/dashboard/tasks", { robot_id, description, waypoints: [] }, "Task dispatched");
    closeModals();
  };
  $("#modal-dispatch").classList.remove("hidden");
}

function closeModals() { document.querySelectorAll(".modal").forEach((m) => m.classList.add("hidden")); }

// ── live connection ────────────────────────────────────────────────────────────
function setConn(ok) {
  state.connLive = ok;
  $("#conn-dot").className = `w-2 h-2 rounded-full ${ok ? "bg-brand pulse" : "bg-red-500"}`;
  $("#conn-label").textContent = ok ? "live" : "reconnecting…";
  renderTicker();
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
    if (msg.type === "fleet") { state.robots = msg.robots; if (msg.sequence) state.sequence = msg.sequence; onFleetUpdate(); }
    else if (msg.type === "alert") {
      state.alerts.unshift(msg.alert); state.alerts = state.alerts.slice(0, 200);
      renderAlerts(); renderStats();
      const box = $("#alerts").firstChild; if (box) box.classList.add("flash");
    }
  };
}

// ── left nav rail: click-to-scroll + scroll-spy active highlight ───────────────
function initRail() {
  const btns = [...document.querySelectorAll(".rail-btn[data-target]")];
  if (!btns.length) return;
  const byId = {};
  btns.forEach((b) => {
    byId[b.dataset.target] = b;
    b.onclick = () => {
      const t = document.getElementById(b.dataset.target);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });
  const sections = btns.map((b) => document.getElementById(b.dataset.target)).filter(Boolean);
  const obs = new IntersectionObserver((entries) => {
    const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
    if (!vis.length) return;
    btns.forEach((x) => x.classList.remove("active"));
    const b = byId[vis[0].target.id];
    if (b) b.classList.add("active");
  }, { rootMargin: "-35% 0px -55% 0px", threshold: [0, 0.25, 0.5] });
  sections.forEach((s) => obs.observe(s));
}

// ── onboarding wizard (new robot API → permissions → policies) ─────────────────
async function loadCatalog() {
  try { state.catalog = await getJSON("/api/dashboard/oem-catalog"); } catch (e) { console.error(e); }
}

const WZ_STEPS = ["Partner", "Permissions", "Policies", "Review"];

function openWizard() {
  if (!state.catalog) { toast("Catalog still loading — try again in a moment", "warn"); loadCatalog(); return; }
  const cat = state.catalog;
  state.wizard = {
    step: 1,
    result: null,
    data: {
      company_name: "", vendor: "", customVendor: "", contact_email: "",
      transport: cat.transports[0] || "ros2", website: "",
      scopes: new Set(),
      policies: { ...cat.default_policies },
    },
  };
  $("#modal-wizard").classList.remove("hidden");
  renderWizard();
}
window.openWizard = openWizard;

function wzVendorName() {
  const d = state.wizard.data;
  return d.vendor === "__other__" ? d.customVendor.trim() : d.vendor;
}
function wzCeiling() {
  const d = state.wizard.data, cat = state.catalog;
  const all = new Set(cat.scopes.map((s) => s.value));
  if (d.vendor === "__other__" || !d.vendor) return all;
  const found = cat.vendors.find((v) => v.vendor === d.vendor);
  return new Set(found ? found.ceiling_scopes : [...all]);
}
function wzStep1Valid() {
  const d = state.wizard.data;
  const emailOk = /.+@.+\..+/.test(d.contact_email.trim());
  return d.company_name.trim() && emailOk && (d.vendor && (d.vendor !== "__other__" || d.customVendor.trim()));
}

function renderWizard() {
  if (state.wizard.result) return renderWizardDone();
  const wz = state.wizard, d = wz.data, cat = state.catalog;
  const dots = WZ_STEPS.map((label, i) => {
    const n = i + 1;
    const cls = n === wz.step ? "active" : n < wz.step ? "done" : "";
    return `<div class="step-dot ${cls}"><span class="num">${n < wz.step ? "✓" : n}</span>${label}</div>`
      + (i < WZ_STEPS.length - 1 ? `<span class="step-sep"></span>` : "");
  }).join("");

  let bodyHtml = "";
  if (wz.step === 1) {
    const vendorOpts = cat.vendors.map((v) => `<option value="${esc(v.vendor)}" ${d.vendor === v.vendor ? "selected" : ""}>${esc(v.vendor)}</option>`).join("");
    const transportOpts = cat.transports.map((t) => `<option value="${t}" ${d.transport === t ? "selected" : ""}>${t}</option>`).join("");
    bodyHtml = `
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="sm:col-span-2">
          <label class="block text-[11px] text-ink-mut mb-1">Company name *</label>
          <input id="wz-company" class="wz-input" placeholder="e.g. Acme Robotics" value="${esc(d.company_name)}" />
        </div>
        <div>
          <label class="block text-[11px] text-ink-mut mb-1">Robot vendor / platform *</label>
          <select id="wz-vendor" class="wz-select">
            <option value="" ${!d.vendor ? "selected" : ""} disabled>Select a platform…</option>
            ${vendorOpts}
            <option value="__other__" ${d.vendor === "__other__" ? "selected" : ""}>Other (custom)…</option>
          </select>
        </div>
        <div>
          <label class="block text-[11px] text-ink-mut mb-1">Control transport</label>
          <select id="wz-transport" class="wz-select">${transportOpts}</select>
        </div>
        <div id="wz-custom-wrap" class="sm:col-span-2 ${d.vendor === "__other__" ? "" : "hidden"}">
          <label class="block text-[11px] text-ink-mut mb-1">Custom vendor name *</label>
          <input id="wz-custom" class="wz-input" placeholder="e.g. Nimbus Dynamics" value="${esc(d.customVendor)}" />
          <div class="text-[10.5px] text-ink-dim mt-1">Unknown platforms default to the ROS 2 cmd_vel adapter; unsupported scopes are dropped automatically.</div>
        </div>
        <div>
          <label class="block text-[11px] text-ink-mut mb-1">Contact email *</label>
          <input id="wz-email" class="wz-input" placeholder="partners@acme.example" value="${esc(d.contact_email)}" />
        </div>
        <div>
          <label class="block text-[11px] text-ink-mut mb-1">Website (optional)</label>
          <input id="wz-website" class="wz-input" placeholder="https://acme.example" value="${esc(d.website)}" />
        </div>
      </div>`;
  } else if (wz.step === 2) {
    const ceiling = wzCeiling();
    const cards = cat.scopes.map((s) => {
      const inCeiling = ceiling.has(s.value);
      const on = d.scopes.has(s.value);
      return `
        <label class="wz-scope ${on ? "on" : ""} ${inCeiling ? "" : "locked"}" data-scope="${s.value}">
          <input type="checkbox" class="accent-brand mt-0.5" ${on ? "checked" : ""} ${inCeiling ? "" : "disabled"} />
          <span class="min-w-0">
            <span class="text-[13px] font-medium">${esc(s.label)}</span>
            <span class="mono text-[10px] text-ink-dim block">${s.value}${inCeiling ? "" : " · outside " + esc(wzVendorName() || "vendor") + " ceiling"}</span>
          </span>
        </label>`;
    }).join("");
    bodyHtml = `
      <p class="text-[12px] text-ink-mut mb-3">Unlock the slices of <span class="text-ink">${esc(wzVendorName())}</span>'s robot API that Orbital may call. Scopes outside the platform's protocol ceiling are locked. <span class="text-brand">control.velocity</span> + <span class="text-brand">control.estop</span> are required for full control-readiness.</p>
      <div id="wz-scopes" class="grid grid-cols-1 sm:grid-cols-2 gap-2">${cards}</div>`;
  } else if (wz.step === 3) {
    const p = d.policies;
    bodyHtml = `
      <p class="text-[12px] text-ink-mut mb-3">Governance bounds Orbital honours for this partner's fleet on top of raw scope grants.</p>
      <div class="space-y-4">
        <div>
          <div class="flex items-center justify-between text-[12px] mb-1"><span>Max commanded speed</span><span class="mono text-azure" id="wz-speed-val">${(+p.max_speed_mps).toFixed(2)} m/s</span></div>
          <input id="wz-speed" type="range" class="speed" min="0.1" max="2.5" step="0.05" value="${p.max_speed_mps}" />
        </div>
        <div>
          <div class="flex items-center justify-between text-[12px] mb-1"><span>Drift halt threshold</span><span class="mono text-azure" id="wz-drift-val">${(+p.drift_halt_threshold_m).toFixed(2)} m</span></div>
          <input id="wz-drift" type="range" class="speed" min="0.1" max="1.5" step="0.05" value="${p.drift_halt_threshold_m}" />
        </div>
        <label class="flex items-center justify-between gap-3 py-1.5 border-t border-line">
          <span class="text-[13px]">Auto E-Stop on critical drift<span class="block text-[10.5px] text-ink-dim">let the orchestrator halt autonomously</span></span>
          <span class="switch"><input id="wz-autoestop" type="checkbox" ${p.auto_estop_on_critical ? "checked" : ""}/><span class="track"></span></span>
        </label>
        <label class="flex items-center justify-between gap-3 py-1.5 border-t border-line">
          <span class="text-[13px]">Require operator approval for teleop<span class="block text-[10.5px] text-ink-dim">teleop needs an explicit hand-on</span></span>
          <span class="switch"><input id="wz-teleop" type="checkbox" ${p.require_approval_for_teleop ? "checked" : ""}/><span class="track"></span></span>
        </label>
        <div class="pt-1 border-t border-line">
          <label class="block text-[11px] text-ink-mut mb-1">Geofence / allowed zone</label>
          <input id="wz-geofence" class="wz-input" value="${esc(p.geofence)}" placeholder="facility" />
        </div>
      </div>`;
  } else {
    const scopes = [...d.scopes];
    const p = d.policies;
    const row = (k, v) => `<div class="flex items-center justify-between py-1 border-b border-line/60"><span class="text-[12px] text-ink-mut">${k}</span><span class="text-[12.5px] mono">${v}</span></div>`;
    bodyHtml = `
      <p class="text-[12px] text-ink-mut mb-3">Review, then create the partner. We'll mint a one-time API key.</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
        <div>
          ${row("Company", esc(d.company_name) || "—")}
          ${row("Vendor", esc(wzVendorName()) || "—")}
          ${row("Transport", esc(d.transport))}
          ${row("Contact", esc(d.contact_email) || "—")}
        </div>
        <div>
          ${row("Max speed", (+p.max_speed_mps).toFixed(2) + " m/s")}
          ${row("Drift halt", (+p.drift_halt_threshold_m).toFixed(2) + " m")}
          ${row("Auto E-Stop", p.auto_estop_on_critical ? "on" : "off")}
          ${row("Teleop approval", p.require_approval_for_teleop ? "required" : "off")}
        </div>
      </div>
      <div class="mt-3">
        <div class="text-[11px] text-ink-mut mb-1.5">Permissions unlocked (${scopes.length})</div>
        <div class="flex flex-wrap gap-1.5">
          ${scopes.length ? scopes.map((s) => `<span class="mono text-[10px] px-1.5 py-0.5 rounded bg-brand/10 text-brand">${s}</span>`).join("") : '<span class="text-[12px] text-ink-dim">None — partner starts pending, monitor-only.</span>'}
        </div>
      </div>`;
  }

  const backBtn = wz.step > 1
    ? `<button id="wz-back" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Back</button>`
    : `<button id="wz-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>`;
  const nextBtn = wz.step < 4
    ? `<button id="wz-next" class="px-4 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#1a1204] text-[13px] font-semibold">Continue</button>`
    : `<button id="wz-create" class="px-4 py-2 rounded-md bg-brand hover:bg-brand-600 text-[#052e1f] text-[13px] font-semibold">Create partner</button>`;

  $("#modal-wizard .modal-card").innerHTML = `
    <div class="p-5">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="text-[16px] font-bold">Onboard robot API</div>
          <div class="text-[11px] text-ink-dim">register a 3rd-party robot platform, unlock its API, set governance</div>
        </div>
        <button id="wz-x" class="text-ink-dim hover:text-ink text-[18px] leading-none">✕</button>
      </div>
      <div class="step-dots mt-4 mb-4 flex-wrap">${dots}</div>
      <div>${bodyHtml}</div>
      <div class="mt-5 flex items-center justify-between">
        ${backBtn}
        <div class="flex items-center gap-2">
          <span class="text-[11px] text-ink-dim">Step ${wz.step} of 4</span>
          ${nextBtn}
        </div>
      </div>
    </div>`;
  wireWizard();
}

function wireWizard() {
  const card = $("#modal-wizard .modal-card");
  const wz = state.wizard, d = wz.data;
  card.querySelector("#wz-x").onclick = closeWizard;
  const cancel = card.querySelector("#wz-cancel");
  if (cancel) cancel.onclick = closeWizard;
  const back = card.querySelector("#wz-back");
  if (back) back.onclick = () => { wz.step--; renderWizard(); };

  if (wz.step === 1) {
    const bind = (id, key) => { const e = card.querySelector(id); if (e) e.oninput = () => { d[key] = e.value; }; };
    bind("#wz-company", "company_name"); bind("#wz-email", "contact_email");
    bind("#wz-website", "website"); bind("#wz-custom", "customVendor");
    card.querySelector("#wz-vendor").onchange = (e) => {
      d.vendor = e.target.value;
      d.scopes = new Set();  // ceiling changed → reset picks
      card.querySelector("#wz-custom-wrap").classList.toggle("hidden", d.vendor !== "__other__");
    };
    card.querySelector("#wz-transport").onchange = (e) => { d.transport = e.target.value; };
  } else if (wz.step === 2) {
    card.querySelectorAll(".wz-scope").forEach((lbl) => {
      const scope = lbl.dataset.scope;
      const cb = lbl.querySelector("input");
      if (cb.disabled) return;
      lbl.onclick = (e) => {
        if (e.target !== cb) cb.checked = !cb.checked;
        if (cb.checked) d.scopes.add(scope); else d.scopes.delete(scope);
        lbl.classList.toggle("on", cb.checked);
      };
    });
  } else if (wz.step === 3) {
    const sp = card.querySelector("#wz-speed"), spv = card.querySelector("#wz-speed-val");
    sp.oninput = () => { d.policies.max_speed_mps = +sp.value; spv.textContent = (+sp.value).toFixed(2) + " m/s"; };
    const dr = card.querySelector("#wz-drift"), drv = card.querySelector("#wz-drift-val");
    dr.oninput = () => { d.policies.drift_halt_threshold_m = +dr.value; drv.textContent = (+dr.value).toFixed(2) + " m"; };
    card.querySelector("#wz-autoestop").onchange = (e) => { d.policies.auto_estop_on_critical = e.target.checked; };
    card.querySelector("#wz-teleop").onchange = (e) => { d.policies.require_approval_for_teleop = e.target.checked; };
    card.querySelector("#wz-geofence").oninput = (e) => { d.policies.geofence = e.target.value; };
  }

  const next = card.querySelector("#wz-next");
  if (next) next.onclick = () => {
    if (wz.step === 1 && !wzStep1Valid()) { toast("Fill company, a valid email, and a vendor", "warn"); return; }
    wz.step++; renderWizard();
  };
  const create = card.querySelector("#wz-create");
  if (create) create.onclick = createOEM;
}

async function createOEM() {
  const d = state.wizard.data;
  const body = {
    company_name: d.company_name.trim(),
    vendor: wzVendorName(),
    contact_email: d.contact_email.trim(),
    transport: d.transport,
    website: d.website.trim() || null,
    scopes: [...d.scopes],
    policies: d.policies,
  };
  const btn = $("#modal-wizard #wz-create");
  if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
  try {
    state.wizard.result = await postJSON("/api/dashboard/oems", body);
    renderWizardDone();
    loadOEMs();
  } catch (e) {
    toast(`Failed: ${e.detail}`, "warn");
    if (btn) { btn.disabled = false; btn.textContent = "Create partner"; }
  }
}

function renderWizardDone() {
  const { profile, credential } = state.wizard.result;
  const scopes = profile.granted_scopes || [];
  const readiness = `${profile.monitor_ready ? '<span class="text-brand">monitor-ready</span>' : '<span class="text-ink-dim">monitor pending</span>'} · ${profile.control_ready ? '<span class="text-brand">control-ready</span>' : '<span class="text-ink-dim">control pending</span>'}`;
  $("#modal-wizard .modal-card").innerHTML = `
    <div class="p-5">
      <div class="flex items-center gap-2.5">
        <span class="w-9 h-9 rounded-full flex items-center justify-center glow-green" style="background:rgba(0,190,125,0.12);border:1px solid rgba(0,190,125,0.5);color:#00be7d">✓</span>
        <div>
          <div class="text-[16px] font-bold">${esc(profile.company_name)} onboarded</div>
          <div class="text-[11px] text-ink-dim">${esc(profile.oem_id)} · ${esc(profile.vendor)} · ${esc(profile.transport)} · ${readiness}</div>
        </div>
      </div>
      <div class="mt-4 rounded-lg border p-3" style="background:rgba(255,160,31,0.06);border-color:rgba(255,160,31,0.35)">
        <div class="text-[11px] text-cta font-semibold uppercase tracking-wide mb-1">⚠ API key — shown once</div>
        <div class="text-[11px] text-ink-mut mb-2">Give this to the partner. Orbital stores only a hash; it can't be recovered.</div>
        <div class="flex items-center gap-2">
          <code class="flex-1 mono text-[12px] bg-surface-input border border-line rounded-md px-2.5 py-2 break-all">${esc(credential.api_key)}</code>
          <button id="wz-copy" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[12px] whitespace-nowrap">Copy</button>
        </div>
      </div>
      <div class="mt-3">
        <div class="text-[11px] text-ink-mut mb-1.5">Permissions unlocked (${scopes.length})</div>
        <div class="flex flex-wrap gap-1.5">
          ${scopes.length ? scopes.map((s) => `<span class="mono text-[10px] px-1.5 py-0.5 rounded bg-brand/10 text-brand">${s}</span>`).join("") : '<span class="text-[12px] text-ink-dim">None yet — partner is pending. Grant scopes from OEM Partners.</span>'}
        </div>
      </div>
      <div class="mt-5 flex justify-end gap-2">
        <button id="wz-done" class="px-4 py-2 rounded-md bg-brand hover:bg-brand-600 text-[#052e1f] text-[13px] font-semibold">Done</button>
      </div>
    </div>`;
  const copy = $("#modal-wizard #wz-copy");
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(credential.api_key); copy.textContent = "Copied ✓"; }
    catch (_) { toast("Copy failed — select manually", "warn"); }
  };
  $("#modal-wizard #wz-done").onclick = () => { closeWizard(); document.getElementById("sec-partners")?.scrollIntoView({ behavior: "smooth" }); };
}

function closeWizard() { $("#modal-wizard").classList.add("hidden"); state.wizard = null; }

// ── embed mode (marketing site iframe) — warehouse map + live metrics only ─
function initEmbedMode() {
  if (!EMBED) return;
  document.documentElement.classList.add("embed-mode", "embed-preview");
  document.body.classList.add("embed-mode", "embed-preview");
  document.getElementById("rail")?.classList.add("embed-hidden");
  document.querySelector("header")?.classList.add("embed-hidden");
  document.getElementById("sec-capabilities")?.classList.add("embed-hidden");
  document.getElementById("sec-fleet")?.classList.add("embed-hidden");
  document.getElementById("sec-benchmark")?.classList.add("embed-hidden");
  document.getElementById("sec-partners")?.classList.add("embed-hidden");
  document.querySelector("section:has(#logic)")?.classList.add("embed-hidden");
  document.getElementById("btn-onboard")?.classList.add("embed-hidden");
  document.getElementById("btn-dispatch")?.classList.add("embed-hidden");
  document.getElementById("embed-hero")?.querySelector("aside")?.classList.add("embed-hidden");
  document.getElementById("sequence-bar")?.classList.add("embed-hidden");
  document.querySelector("#sec-map .card-head")?.classList.add("embed-hidden");
}

let _embedRenderTimer = null;
function scheduleEmbedRender() {
  if (_embedRenderTimer) return;
  _embedRenderTimer = setTimeout(() => {
    _embedRenderTimer = null;
    renderStats();
    renderSequence();
    renderMap();
  }, 250);
}

function onFleetUpdate() {
  if (EMBED) scheduleEmbedRender();
  else {
    renderFleet();
    renderStats();
    renderSequence();
    renderMap();
    renderControlPanel();
    renderCapabilities();
  }
}

// ── boot ─────────────────────────────────────────────────────────────────────
async function init() {
  initEmbedMode();
  if (!EMBED) initRail();
  if (!EMBED) {
    $("#btn-dispatch").onclick = () => openDispatch();
    $("#btn-onboard").onclick = () => openWizard();
    const onboard2 = $("#btn-onboard-2"); if (onboard2) onboard2.onclick = () => openWizard();
    document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });
  }

  const fleet = await getJSON("/api/dashboard/fleet");
  state.facility = fleet.facility;
  state.industries = fleet.industries;
  state.vendors = fleet.vendors;
  state.robots = fleet.robots;
  state.sequence = fleet.sequence || null;
  state.selectedRobot = (fleet.robots.find((r) => r.state !== "halted") || fleet.robots[0] || {}).id || null;
  const facilityEl = $("#facility-name");
  if (facilityEl) facilityEl.textContent = fleet.facility.name;

  if (EMBED) {
    renderStats();
    renderSequence();
  } else {
    renderTabs();
    renderFleet();
    renderStats();
    renderSequence();
    renderControlPanel();
  }

  wireMap();
  await loadMap();

  if (!EMBED) {
    state.alerts = await getJSON("/api/dashboard/alerts");
    renderAlerts();
    await loadBenchmark();
    await loadCatalog();
    await loadOEMs();
    await loadOrchestrator();
    setInterval(loadBenchmark, 5000);
    setInterval(loadOEMs, 8000);
    setInterval(loadOrchestrator, 5000);
  }
  connectWS();
}

init().catch((e) => { console.error(e); $("#conn-label").textContent = "error"; });
