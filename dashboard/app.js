/* Orbital AI — Fleet Control (build-free SPA).
   Same-origin FastAPI cloud; live updates over /ws. Supabase-inspired dark console. */

const API = "";
const MAX_SPEED = 2.5; // mirrors ORBITAL_MAX_SPEED_MPS
const state = {
  facility: null,
  industries: [],
  vendors: [],
  robots: [],
  alerts: [],
  activeTab: "All",
  ws: null,
  map: null,
  selectedRobot: null,
  orch: null,
  oems: [],
};

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

const STATE_STYLE = {
  active:   ["bg-brand/15", "text-brand", "Active"],
  idle:     ["bg-cta/15", "text-cta", "Idle"],
  charging: ["bg-azure/15", "text-azure", "Charging"],
  cooldown: ["bg-red-500/15", "text-red-400", "Task done"],
  halted:   ["bg-red-500/20", "text-red-400", "Halted"],
  offline:  ["bg-white/[0.06]", "text-ink-dim", "Offline"],
};
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

// ── stats ────────────────────────────────────────────────────────────────────
function renderStats() {
  const total = state.robots.length;
  const active = state.robots.filter((r) => r.state === "active").length;
  const cooldown = state.robots.filter((r) => r.state === "cooldown").length;
  const nav = state.robots.filter((r) => r.control_mode === "visual_nav").length;
  const halted = state.robots.filter((r) => r.state === "halted").length;
  const openAlerts = state.alerts.filter((a) => !a.acknowledged).length;
  const cards = [
    ["Fleet", total, "text-ink"],
    ["Working", active, "text-brand"],
    ["Between tasks", cooldown, cooldown ? "text-red-400" : "text-ink"],
    ["Visual-nav", nav, nav ? "text-azure" : "text-ink"],
    ["Halted", halted, halted ? "text-red-400" : "text-ink"],
    ["Open alerts", openAlerts, openAlerts ? "text-cta" : "text-ink"],
  ];
  const box = $("#stats"); box.innerHTML = "";
  for (const [label, val, cls] of cards) {
    box.appendChild(el(`
      <div class="card px-3.5 py-2.5">
        <div class="text-[11px] text-ink-dim">${label}</div>
        <div class="text-xl font-semibold mono ${cls} mt-0.5">${val}</div>
      </div>`));
  }
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function renderTabs() {
  const tabs = ["All", ...state.industries];
  $("#tabs").innerHTML = "";
  for (const t of tabs) {
    const on = t === state.activeTab;
    const cls = on ? "bg-brand text-[#052e1f] font-semibold" : "bg-surface-input text-ink-mut hover:text-ink hover:bg-line-strong";
    const btn = el(`<button class="px-2.5 py-1 rounded-md text-[12px] transition ${cls}">${esc(t)}</button>`);
    btn.onclick = () => { state.activeTab = t; renderTabs(); renderFleet(); };
    $("#tabs").appendChild(btn);
  }
}

// ── fleet ─────────────────────────────────────────────────────────────────────
function renderFleet() {
  const list = state.activeTab === "All" ? state.robots : state.robots.filter((r) => r.industry === state.activeTab);
  const grid = $("#fleet");
  grid.innerHTML = "";
  if (!list.length) { grid.appendChild(el(`<div class="text-ink-dim text-[13px]">No robots in this category.</div>`)); return; }
  for (const r of list) {
    const [bg, fg, label] = STATE_STYLE[r.state] || STATE_STYLE.offline;
    const sel = r.id === state.selectedRobot;
    const card = el(`
      <div class="card p-3.5 transition cursor-pointer ${sel ? "border-brand/60" : "hover:border-line-strong"}">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="font-semibold text-[13px] truncate">${esc(r.vendor)} <span class="text-ink-mut font-normal">${esc(r.model)}</span></div>
            <div class="text-[11px] text-ink-dim mt-0.5">${esc(r.id)} · ${esc(r.industry)}</div>
          </div>
          <span class="text-[10.5px] px-2 py-0.5 rounded-full ${bg} ${fg} whitespace-nowrap">${label}</span>
        </div>
        <div class="mt-2.5 flex items-center gap-1.5 text-[11px] ${MODE_STYLE[r.control_mode] || "text-ink-mut"}">
          <span class="w-1.5 h-1.5 rounded-full bg-current opacity-70"></span>${esc(MODE_LABEL[r.control_mode] || r.control_mode)}
        </div>
        <div class="mt-2.5 grid grid-cols-2 gap-3 text-[13px]">
          <div>
            <div class="text-[11px] text-ink-dim">Drift Δ</div>
            <div class="font-semibold mono ${driftColor(r.drift_delta_m)}">${r.drift_delta_m.toFixed(3)}m</div>
          </div>
          <div>
            <div class="text-[11px] text-ink-dim">Speed</div>
            <div class="font-semibold mono text-ink">${(r.speed_mps ?? 0).toFixed(2)} m/s</div>
          </div>
        </div>
        <div class="mt-2.5">
          <div class="flex items-center gap-2">
            <div class="h-1.5 flex-1 rounded-full bg-surface-input overflow-hidden">
              <div class="h-full ${batteryColor(r.battery_pct)}" style="width:${Math.max(2, r.battery_pct)}%"></div>
            </div>
            <span class="text-[11px] text-ink-mut mono">${Math.round(r.battery_pct)}%</span>
          </div>
        </div>
        <div class="mt-3 flex gap-2">
          <button data-act="select" class="flex-1 px-2.5 py-1.5 rounded-md bg-surface-input hover:bg-line-strong text-[12px]">Control</button>
          ${r.state === "halted"
            ? `<button data-act="resume" class="px-2.5 py-1.5 rounded-md bg-brand hover:bg-brand-600 text-[#052e1f] text-[12px] font-medium">Resume</button>`
            : `<button data-act="estop" class="px-2.5 py-1.5 rounded-md bg-red-600 hover:bg-red-500 text-white text-[12px] font-medium">E-Stop</button>`}
          <button data-act="details" class="px-2.5 py-1.5 rounded-md bg-surface-input hover:bg-line-strong text-[12px]">Info</button>
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
  const box = $("#control-panel");
  const modeTag = $("#control-mode");
  const r = robotById(state.selectedRobot);
  if (!r) {
    modeTag.textContent = "";
    box.innerHTML = `<div class="text-[13px] text-ink-dim py-6 text-center">Select a robot on the map or a fleet card to drive it.</div>`;
    return;
  }
  const [bg, fg, label] = STATE_STYLE[r.state] || STATE_STYLE.offline;
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
        <div class="font-semibold text-[14px] truncate">${esc(r.vendor)} ${esc(r.model)}</div>
        <div class="text-[11px] text-ink-dim">${esc(r.id)} · ${g.managed ? esc(r.vendor) + " OEM" : "unmanaged (open)"}</div>
      </div>
      <span class="text-[10.5px] px-2 py-0.5 rounded-full ${bg} ${fg} whitespace-nowrap">${label}</span>
    </div>

    <div class="mt-4 rounded-lg bg-surface-raised border border-line p-3 ${dim(canVel)}">
      ${sectionHead("Drive", "control.velocity", canVel)}
      <div class="flex items-center justify-between text-[11px] text-ink-mut mb-1.5">
        <span>Speed</span><span class="mono" id="speed-val">${(r.speed_mps ?? 0).toFixed(2)} m/s</span>
      </div>
      <input id="speed" type="range" class="speed" min="0.05" max="${MAX_SPEED}" step="0.05" value="${r.speed_mps ?? 0.6}" ${canVel ? "" : "disabled"} />
      <div class="flex justify-between text-[10px] text-ink-dim mono mt-1 mb-3"><span>0.05</span><span>${MAX_SPEED.toFixed(1)} m/s</span></div>
      <div class="text-[11px] text-ink-mut mb-1.5">Manual jog — drive along a heading</div>
      <div class="dpad max-w-[168px] mx-auto"></div>
      <div class="text-[10px] text-ink-dim text-center mt-2">overrides patrol · clears waypoints until stopped</div>
    </div>

    <div class="mt-3 rounded-lg bg-surface-raised border border-line p-3 ${dim(canVel)}">
      ${sectionHead("Navigate — visual waypoints", "control.velocity", canVel)}
      ${r.waypoints && r.waypoints.length
        ? `<div class="flex items-center justify-between">
             <span class="text-[12px] text-brand">en route · ${r.waypoints.length} pt${r.waypoints.length > 1 ? "s" : ""}</span>
             <button id="wp-clear" class="text-[12px] px-2.5 py-1 rounded-md bg-surface-input hover:bg-line-strong">Clear route</button>
           </div>`
        : `<div class="text-[12px] text-ink-dim">Click the map to set a waypoint. Shift-click to chain. Orbital drives it there by camera — bypassing onboard SLAM.</div>`}
    </div>

    <div class="mt-3 rounded-lg bg-surface-raised border border-line p-3">
      ${sectionHead("Safety", "control.estop", canEstop)}
      <div class="flex gap-2">
        ${halted
          ? `<button id="c-resume" class="flex-1 px-3 py-2 rounded-md ${canEstop ? "bg-brand hover:bg-brand-600 text-[#052e1f]" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[13px] font-semibold" ${canEstop ? "" : "disabled"}>${canEstop ? "Resume" : "Resume 🔒"}</button>`
          : `<button id="c-estop" class="flex-1 px-3 py-2 rounded-md ${canEstop ? "bg-red-600 hover:bg-red-500 text-white" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[13px] font-semibold" ${canEstop ? "" : "disabled"}>${canEstop ? "E-Stop" : "E-Stop 🔒"}</button>`}
        <button id="c-mission" class="px-3 py-2 rounded-md ${canMission ? "bg-surface-input hover:bg-line-strong" : "bg-surface-input text-ink-dim cursor-not-allowed"} text-[13px]" ${canMission ? "" : "disabled"} title="mission.dispatch">Task</button>
        <button id="c-details" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Info</button>
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
    // robots we can exercise this on: unmanaged vendors are open; managed need the grant.
    const robots = state.robots.filter((r) => vendorGrants(r.vendor).has(cap.scope)).length;
    const live = robots > 0;
    const accent = cap.kind === "control"
      ? (live ? "text-brand border-brand/30" : "text-ink-dim border-line")
      : (live ? "text-sky-300 border-sky-500/25" : "text-ink-dim border-line");
    box.appendChild(el(`
      <div class="rounded-lg bg-surface-raised border ${live ? "border-line-strong" : "border-line"} p-3">
        <div class="flex items-center gap-2">
          <span class="w-6 h-6 rounded-md flex items-center justify-center text-[13px] border ${accent}">${cap.glyph}</span>
          <div class="min-w-0">
            <div class="text-[12.5px] font-semibold truncate">${cap.label}</div>
            <div class="mono text-[9.5px] text-ink-dim">${cap.scope}</div>
          </div>
          <span class="ml-auto text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded ${cap.kind === "control" ? "bg-brand/10 text-brand" : "bg-sky-500/10 text-sky-300"}">${cap.kind}</span>
        </div>
        <div class="text-[11.5px] text-ink-mut mt-2 leading-snug">${cap.desc}</div>
        <div class="flex items-center justify-between mt-2 pt-2 border-t border-line text-[10.5px] text-ink-dim mono">
          <span>${partners} partner${partners === 1 ? "" : "s"}</span>
          <span class="${live ? "text-brand" : ""}">${robots} robot${robots === 1 ? "" : "s"}</span>
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
      const pts = [`${ex.x},${Y(ex.y)}`, ...r.waypoints.map((w) => `${w.x},${Y(w.y)}`)].join(" ");
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
function renderAlerts() {
  $("#alert-count").textContent = state.alerts.filter((a) => !a.acknowledged).length;
  const box = $("#alerts");
  box.innerHTML = "";
  if (!state.alerts.length) { box.appendChild(el(`<div class="px-4 py-6 text-[13px] text-ink-dim">No alerts. Fleet nominal.</div>`)); return; }
  for (const a of state.alerts.slice(0, 60)) {
    const sev = SEV_STYLE[a.severity] || "text-ink-mut";
    const row = el(`
      <div class="px-4 py-2.5 ${a.acknowledged ? "opacity-45" : ""}">
        <div class="flex items-center justify-between gap-2">
          <span class="text-[10.5px] font-semibold ${sev} uppercase tracking-wide">${esc(a.type)}</span>
          <span class="text-[10.5px] text-ink-dim mono">${fmtTime(a.ts)}</span>
        </div>
        <div class="text-[12.5px] mt-1 text-ink-mut">${esc(a.message)}</div>
        ${a.acknowledged ? "" : `<button class="mt-1.5 text-[11px] text-brand hover:underline">Acknowledge</button>`}
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
    const tb = $("#benchmark"); tb.innerHTML = "";
    for (const v of rows) {
      tb.appendChild(el(`
        <tr class="border-b border-line/60">
          <td class="px-4 py-2 font-medium">${esc(v.vendor)}</td>
          <td class="px-4 py-2 text-ink-mut mono">${v.robots}</td>
          <td class="px-4 py-2 text-ink-mut mono">${v.samples}</td>
          <td class="px-4 py-2 mono ${driftColor(v.mean_drift_m)}">${v.mean_drift_m.toFixed(3)}</td>
          <td class="px-4 py-2 mono ${driftColor(v.p95_drift_m)}">${v.p95_drift_m.toFixed(3)}</td>
          <td class="px-4 py-2 text-ink-mut mono">${fmtSecs(v.mtbd_seconds)}</td>
          <td class="px-4 py-2 text-ink-mut mono">${v.mean_recovery_latency_seconds == null ? "—" : v.mean_recovery_latency_seconds + "s"}</td>
          <td class="px-4 py-2 text-ink-mut mono">${v.env_degradation_score ?? "—"}</td>
        </tr>`));
    }
  } catch (e) { console.error(e); }
}

// ── OEM partners & scopes ──────────────────────────────────────────────────────
const ALL_SCOPES = [
  "telemetry.read", "state.read", "control.velocity", "control.estop",
  "control.teleop", "mission.dispatch", "camera.read", "map.read",
];
const OEM_STATUS_STYLE = {
  active: ["bg-brand/15", "text-brand"],
  pending: ["bg-amber-400/15", "text-amber-400"],
  suspended: ["bg-red-500/20", "text-red-400"],
};
async function loadOEMs() {
  try {
    const oems = await getJSON("/api/dashboard/oems");
    state.oems = oems;
    renderCapabilities();
    renderControlPanel();
    const tb = $("#oems"); tb.innerHTML = "";
    if (!oems.length) {
      tb.appendChild(el(`<tr><td colspan="7" class="px-4 py-6 text-[13px] text-ink-dim">No OEM partners yet. Onboard one with <code class="text-ink-mut">scripts/oem_onboard.py register</code>.</td></tr>`));
      return;
    }
    for (const o of oems) {
      const [bg, fg] = OEM_STATUS_STYLE[o.status] || ["bg-surface-input", "text-ink-mut"];
      const readiness = `${o.monitor_ready ? '<span class="text-brand">monitor</span>' : '<span class="text-ink-dim">monitor</span>'} · ${o.control_ready ? '<span class="text-brand">control</span>' : '<span class="text-ink-dim">control</span>'}`;
      const row = el(`
        <tr class="border-b border-line/60">
          <td class="px-4 py-2 font-medium">${esc(o.company_name)}</td>
          <td class="px-4 py-2 text-ink-mut">${esc(o.vendor)}</td>
          <td class="px-4 py-2 text-ink-mut">${esc(o.transport)}</td>
          <td class="px-4 py-2"><span class="text-[10.5px] px-2 py-0.5 rounded-full ${bg} ${fg}">${esc(o.status)}</span></td>
          <td class="px-4 py-2 text-ink-mut mono">${o.granted_scopes.length} / ${o.ceiling_scopes.length}</td>
          <td class="px-4 py-2 text-[12px]">${readiness}</td>
          <td class="px-4 py-2 text-right"><button class="px-2.5 py-1 rounded-md bg-surface-input hover:bg-line-strong text-[11px]">Manage</button></td>
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
      <div class="mt-5 flex gap-2 justify-between items-center">
        <button id="oem-suspend" class="px-3 py-2 rounded-md text-[13px] ${suspended ? "bg-brand hover:bg-brand-600 text-[#052e1f]" : "bg-surface-input hover:bg-line-strong text-red-400"}">${suspended ? "Reactivate" : "Suspend access"}</button>
        <div class="flex gap-2">
          <button id="oem-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>
          <button id="oem-save" class="px-3 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#1a1204] text-[13px] font-semibold">Save scopes</button>
        </div>
      </div>
    </div>`;
  card.querySelector("#oem-cancel").onclick = closeModals;
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

function loadFleetSoon() { getJSON("/api/dashboard/fleet").then((f) => { state.robots = f.robots; renderFleet(); renderControlPanel(); }).catch(() => {}); }

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
            <div class="text-[16px] font-bold">${esc(r.vendor)} ${esc(r.model)}</div>
            <div class="text-[11px] text-ink-dim">${esc(r.id)} · ${esc(r.industry)} · ${esc(r.facility_id)}</div>
          </div>
          <span class="text-[10.5px] px-2 py-0.5 rounded-full ${bg} ${fg}">${label}</span>
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
  $("#conn-dot").className = `w-2 h-2 rounded-full ${ok ? "bg-brand pulse" : "bg-red-500"}`;
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
    if (msg.type === "fleet") { state.robots = msg.robots; renderFleet(); renderStats(); renderMap(); renderControlPanel(); renderCapabilities(); }
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

// ── boot ─────────────────────────────────────────────────────────────────────
async function init() {
  initRail();
  $("#btn-dispatch").onclick = () => openDispatch();
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });

  const fleet = await getJSON("/api/dashboard/fleet");
  state.facility = fleet.facility;
  state.industries = fleet.industries;
  state.vendors = fleet.vendors;
  state.robots = fleet.robots;
  // Auto-select a live robot so the control surface is populated the moment the page loads.
  state.selectedRobot = (fleet.robots.find((r) => r.state !== "halted") || fleet.robots[0] || {}).id || null;
  $("#facility-name").textContent = fleet.facility.name;
  renderTabs(); renderFleet(); renderStats(); renderControlPanel();

  wireMap();
  await loadMap();

  state.alerts = await getJSON("/api/dashboard/alerts");
  renderAlerts();
  await loadBenchmark();
  await loadOEMs();
  await loadOrchestrator();
  setInterval(loadBenchmark, 5000);
  setInterval(loadOEMs, 8000);
  setInterval(loadOrchestrator, 5000);
  connectWS();
}

init().catch((e) => { console.error(e); $("#conn-label").textContent = "error"; });
