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
  actions: [],
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
  zones: [],            // keep-out / slow zones on the spatial map
  workers: [],          // people on the floor (live only)
  zoneDraft: null,      // { type, points: [{x, y}] } while the operator is drawing a zone
  runs: [],             // recent recorded task runs, scored against their golden runs
  runOverlay: null,     // { run, golden } drawn on the map when a run row is selected
  objects: [],          // tracked objects (pallets, carts, forklifts) from the camera rig
  clockSkew: 0,         // server time minus browser time, so objects expire on the server's clock
  kpis: null,           // running totals for the value bar (pallets shipped, deliveries, …)
  tickMs: 200,          // measured gap between live updates; motion is smoothed over it
  lastFleetAt: 0,
  animUntil: 0,         // performance.now() until which something on the map is still gliding
  mapDirty: true,       // floor, zones or overlays changed: redraw the static layer too
  cameraCells: [],      // floor cells the Digital Net sees as blocked: [x0, y0, x1, y1] (m)
  auth: null,           // /api/auth/me: who is signed in to this console
  calibration: null,    // latest rig-calibration report for this facility (Sprint H)
  busy: null,           // robot id with a stop/start request in flight
  demoStops: {},        // robot id → ms epoch a visitor's demo stop self-releases
  view: "live",         // live | fleet | runs | partners
  driveOpen: false,     // manual drive section expanded in the robot panel
};

// Fleet states, ordered for the overview distribution bar + chips.
const STATUS_META = [
  { key: "active",   label: "Working",       color: "#16A34A" },
  { key: "cooldown", label: "Between tasks", color: "#DC2626" },
  { key: "idle",     label: "Idle",          color: "#D97706" },
  { key: "charging", label: "Charging",      color: "#1D4ED8" },
  { key: "halted",   label: "Halted",        color: "#BE123C" },
  { key: "offline",  label: "Offline",       color: "#80858C" },
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

// A 403 for an anonymous visitor is almost always "you are a viewer" — say how to fix it.
function blockedMsg(e) {
  if (!state.auth?.signed_in && /role|viewer/.test(String(e.detail))) return "Sign in as an operator to control robots";
  return `Blocked: ${e.detail}`;
}
async function control(path, body, okMsg) {
  try {
    const out = await postJSON(path, body);
    const id = /\/robot\/([^/]+)\//.exec(path)?.[1];
    if (id && out?.auto_resume_s) state.demoStops[id] = Date.now() + out.auto_resume_s * 1000;
    if (okMsg) toast(okMsg);
  }
  catch (e) {
    if (e.status === 403) toast(blockedMsg(e), "warn");
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
  active: "#16A34A", idle: "#D97706", charging: "#1D4ED8", cooldown: "#DC2626", halted: "#DC2626", offline: "#4A4F57",
};
const STATE_LABEL = {
  active: "Active", idle: "Idle", charging: "Charging", cooldown: "Task done", halted: "Halted", offline: "Offline",
};
const MODE_LABEL = {
  patrol: "Autonomous patrol", visual_nav: "Visual-nav (SLAM bypass)", manual: "Manual jog",
  charging: "Charging", halted: "Halted", idle: "Idle", cooldown: "Between tasks",
};
const MODE_STYLE = {
  patrol: "text-ink-mut", visual_nav: "text-azure", manual: "text-cta",
  charging: "text-azure", halted: "text-red-700", idle: "text-cta", cooldown: "text-red-700",
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
function driftColor(d) { return d >= 0.5 ? "text-red-700" : d >= 0.1 ? "text-amber-700" : "text-brand"; }
function fmtSecs(s) { if (s == null) return "—"; if (s < 90) return `${Math.round(s)}s`; if (s < 5400) return `${(s/60).toFixed(1)}m`; return `${(s/3600).toFixed(1)}h`; }
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString(); }

// ── status strip: one row of live fleet numbers under the top bar ──────────────
function fleetMetrics() {
  const rb = state.robots;
  const total = rb.length;
  const counts = {};
  for (const s of STATUS_META) counts[s.key] = 0;
  for (const r of rb) counts[r.state] = (counts[r.state] || 0) + 1;
  const openAlerts = alertGroups().length;
  const avgDrift = total ? rb.reduce((s, r) => s + (r.drift_delta_m || 0), 0) / total : 0;
  const avgBatt = total ? rb.reduce((s, r) => s + (r.battery_pct || 0), 0) / total : 0;
  return { total, counts, openAlerts, avgDrift, avgBatt };
}

function renderStats() {
  const box = $("#stats");
  if (!box) return;
  const m = fleetMetrics();
  if (!box.dataset.built) {
    box.dataset.built = "1";
    box.innerHTML = `
      <span class="stat"><span class="stat-val" data-k="total"></span>robots</span>
      ${STATUS_META.map((s) => `<button class="stat" data-filter="${s.key}" title="Show ${s.label.toLowerCase()} robots in the fleet list">
        <span class="dot" style="background:${s.color}"></span>${s.label}<span class="stat-val" data-k="${s.key}"></span></button>`).join("")}
      <span class="stat-gap"></span>
      <span class="stat">Avg drift<span class="stat-val" data-k="drift"></span></span>
      <span class="stat">Battery<span class="stat-val" data-k="batt"></span></span>
      <button class="stat" data-go="alerts">Needs attention<span class="stat-val" data-k="alerts"></span></button>`;
    box.querySelectorAll("[data-filter]").forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.filter;
        state.statusFilter = state.statusFilter === k ? null : k;
        renderStats(); renderFleet();
        if (state.statusFilter) showView("fleet");
      };
    });
    box.querySelector('[data-go="alerts"]').onclick = () => { showView("live"); $("#sec-alerts")?.scrollIntoView({ behavior: "smooth", block: "nearest" }); };
  }
  const setK = (k, v, zero) => { const n = box.querySelector(`[data-k="${k}"]`); if (n) { if (n.textContent !== v) n.textContent = v; n.classList.toggle("zero", !!zero); } };
  setK("total", String(m.total));
  for (const s of STATUS_META) {
    setK(s.key, String(m.counts[s.key]), !m.counts[s.key]);
    box.querySelector(`[data-filter="${s.key}"]`)?.classList.toggle("on", state.statusFilter === s.key);
  }
  setK("drift", `${m.avgDrift.toFixed(3)} m`);
  setK("batt", `${Math.round(m.avgBatt)}%`);
  setK("alerts", String(m.openAlerts), !m.openAlerts);
}

// What the floor has delivered since the server started: the reason the fleet is there.
const KPI_TILES = [
  { k: "pallets_shipped", label: "Pallets shipped", sub: (v) => `${v.trucks_loaded} truck${v.trucks_loaded === 1 ? "" : "s"} loaded` },
  { k: "deliveries", label: "Robot deliveries", sub: (v, hrs) => hrs >= 0.05 ? `${Math.round(v.deliveries / hrs)} per hour` : "drop-offs completed" },
  { k: "slowdowns", label: "Safety slow-downs", sub: () => "near people or forklifts" },
  { k: "safety_stops", label: "Safety stops", sub: () => "a person came too close" },
  { k: "fleet_busy_pct", label: "Fleet busy", sub: () => "of robot time working", fmt: (n) => n == null ? "—" : `${n}%` },
];
function renderValueBar() {
  const box = $("#value-bar"), v = state.kpis;
  if (!box || !v) return;
  if (!box.dataset.built) {
    box.dataset.built = "1";
    box.innerHTML = KPI_TILES.map((t) => `<div class="kpi"><div class="kpi-label">${t.label}</div>
      <div class="kpi-val" data-k="${t.k}">—</div><div class="kpi-sub" data-sub="${t.k}"></div></div>`).join("")
      + `<div class="kpi-since" id="kpi-since"></div>`;
    box.hidden = false;
  }
  const hrs = (Date.now() / 1000 + state.clockSkew - v.since) / 3600;
  for (const t of KPI_TILES) {
    const el = box.querySelector(`[data-k="${t.k}"]`);
    const text = t.fmt ? t.fmt(v[t.k]) : String(v[t.k]);
    if (el.textContent !== text) {
      const grew = t.k !== "fleet_busy_pct" && el.textContent !== "—" && Number(text) > Number(el.textContent);
      el.textContent = text;
      if (grew) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
    }
    const sub = t.sub(v, hrs);
    const subEl = box.querySelector(`[data-sub="${t.k}"]`);
    if (subEl.textContent !== sub) subEl.textContent = sub;
  }
  const since = `since ${new Date(v.since * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  if ($("#kpi-since").textContent !== since) $("#kpi-since").textContent = since;
}

// Human labels for the mission leg each robot is on.
const PHASE_LABEL = {
  en_route_pickup: "→ pickup",
  working: "working",
  carrying: "carrying",
  idle: "idle",
};
const PHASE_COLOR = {
  en_route_pickup: "#1D4ED8",
  working: "#D97706",
  carrying: "#16A34A",
  idle: "#80858C",
};

function renderSequence() {
  const bar = $("#sequence-bar");
  if (!bar) return;
  const s = state.sequence;
  const html = !s || !s.label
    ? `<span class="seq-objective">Waiting for the fleet to pick up its first job…</span>`
    : `<span class="seq-kicker">NOW</span>
       <span class="seq-label">${esc(s.label)}</span>
       <span class="seq-objective" title="${esc(s.objective || "")}">${esc(s.objective || "")}</span>
       <span class="seq-timer">next job in ${Math.max(0, Math.round(s.ends_in_s ?? 0))}s</span>`;
  if (bar.innerHTML !== html) bar.innerHTML = html;
}

// ── fleet table ──────────────────────────────────────────────────────────────
function renderTabs() {
  const box = $("#tabs");
  if (!box) return;
  box.innerHTML = "";
  for (const t of FLEET_CATEGORIES) {
    const btn = el(`<button type="button" class="seg-tab ${t === state.activeTab ? "on" : ""}">${esc(t)}</button>`);
    btn.onclick = () => { state.activeTab = t; renderTabs(); renderFleet(); };
    box.appendChild(btn);
  }
}

function toggleButtonState(btn, r) {
  const halted = r.state === "halted";
  const access = stopStartAccess(r);
  const label = halted ? "Start" : "Stop";
  if (btn.textContent !== label) btn.textContent = label;
  const cls = `${btn.dataset.base} ${halted ? "start" : "stop"}`;
  if (btn.className !== cls) btn.className = cls;
  btn.disabled = !access.ok || state.busy === r.id;
  btn.title = access.why;
}

function renderFleet() {
  const tb = $("#fleet");
  if (!tb) return;
  let list = state.robots.filter((r) => categoryMatches(r.industry, state.activeTab));
  if (state.statusFilter) list = list.filter((r) => r.state === state.statusFilter);
  const count = $("#fleet-count");
  if (count) count.textContent = `${list.length} of ${state.robots.length} robots`;
  renderFleetFilter();

  const key = list.map((r) => r.id).join(",");
  if (tb.dataset.key !== key) {
    tb.dataset.key = key;
    tb.innerHTML = list.length ? "" : `<tr><td colspan="6" class="text-ink-dim">No robots match this filter.</td></tr>`;
    for (const r of list) {
      const row = el(`<tr data-id="${esc(r.id)}">
        <td><div class="font-semibold" data-c="name"></div><div class="mono text-[11px] text-azure" data-c="sub"></div></td>
        <td data-c="status"></td>
        <td class="doing" data-c="doing"></td>
        <td data-c="batt"></td>
        <td class="mono" data-c="drift"></td>
        <td><div class="row-actions">
          <button class="row-btn" data-base="row-btn" data-act="toggle"></button>
          <button class="row-btn" data-act="view">On floor</button>
          <button class="row-btn" data-act="details">Details</button>
        </div></td>
      </tr>`);
      const id = r.id;
      row.querySelector('[data-act="toggle"]').onclick = () => { const cur = robotById(id); if (cur) quickControl(cur, cur.state === "halted" ? "resume" : "estop"); };
      row.querySelector('[data-act="view"]').onclick = () => { selectRobot(id); showView("live"); };
      row.querySelector('[data-act="details"]').onclick = () => openRobot(id);
      tb.appendChild(row);
    }
  }
  for (const r of list) {
    const row = tb.querySelector(`tr[data-id="${CSS.escape(r.id)}"]`);
    if (!row) continue;
    row.classList.toggle("selected", r.id === state.selectedRobot);
    patch(row, '[data-c="name"]', `${esc(r.vendor)} ${esc(r.model)}`);
    patch(row, '[data-c="sub"]', `${esc(r.id)} · ${esc(r.industry)}`);
    patch(row, '[data-c="status"]', inlineStatus(STATE_LABEL[r.state] || r.state, STATE_COLOR[r.state] || "#4A4F57"));
    patch(row, '[data-c="doing"]', esc(robotDoing(r)));
    const bc = r.battery_pct < 15 ? "#DC2626" : r.battery_pct < 50 ? "#D97706" : "#16A34A";
    patch(row, '[data-c="batt"]', `<div class="batt"><div class="batt-bar"><span style="width:${Math.max(2, r.battery_pct)}%;background:${bc}"></span></div>${Math.round(r.battery_pct)}%</div>`);
    patch(row, '[data-c="drift"]', `<span class="${driftColor(r.drift_delta_m)}">${r.drift_delta_m.toFixed(3)} m</span>`);
    toggleButtonState(row.querySelector('[data-act="toggle"]'), r);
  }
}

function renderFleetFilter() {
  const box = $("#fleet-filter");
  if (!box) return;
  box.innerHTML = "";
  if (!state.statusFilter) return;
  const s = STATUS_META.find((x) => x.key === state.statusFilter);
  const chip = el(`<button class="pick on">${esc(s?.label || state.statusFilter)} ✕</button>`);
  chip.onclick = () => { state.statusFilter = null; renderStats(); renderFleet(); };
  box.appendChild(chip);
}

// ── robot panel (selected robot) ──────────────────────────────────────────────
const DIRS = [
  ["↖", 135], ["↑", 90], ["↗", 45],
  ["←", 180], ["stop", null], ["→", 0],
  ["↙", 225], ["↓", 270], ["↘", 315],
];
function selectRobot(id) { state.selectedRobot = id; renderMap(); renderControlPanel(); renderFleet(); }

function renderRobotPicker() {
  const box = $("#robot-picker");
  if (!box) return;
  const key = state.robots.map((r) => r.id).join(",");
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = "";
    for (const r of state.robots) {
      const b = el(`<button class="pick" role="option" data-id="${esc(r.id)}" title="${esc(r.vendor)} ${esc(r.model)}"><span class="dot"></span>${esc(r.model)}</button>`);
      b.onclick = () => selectRobot(r.id);
      box.appendChild(b);
    }
  }
  for (const r of state.robots) {
    const b = box.querySelector(`[data-id="${CSS.escape(r.id)}"]`);
    if (!b) continue;
    const on = r.id === state.selectedRobot;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", String(on));
    const dot = b.querySelector(".dot");
    const c = STATE_COLOR[r.state] || "#4A4F57";
    if (dot.style.background !== c) dot.style.background = c;
  }
}

function renderControlPanel() {
  if (EMBED) return;
  renderRobotPicker();
  const box = $("#control-panel");
  const modeTag = $("#control-mode");
  if (!box) return;
  const r = robotById(state.selectedRobot);
  if (!r) {
    box.dataset.key = "";
    if (modeTag) modeTag.textContent = "";
    box.innerHTML = `<div class="att-empty">Pick a robot above or click one on the floor.</div>`;
    return;
  }
  const g = vendorGrants(r.vendor);
  const canVel = g.has("control.velocity") && isOperator();
  const key = `${r.id}|${canVel}|${isOperator()}`;
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = `
      <div class="rp">
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <div class="rp-title" data-c="title"></div>
            <div class="rp-sub" data-c="sub"></div>
          </div>
          <span data-c="status"></span>
        </div>
        <div class="rp-now" data-c="nowbox">
          <div class="rp-label">Doing now</div>
          <div class="text-[13px] font-medium mt-0.5 leading-snug" data-c="doing"></div>
          <div class="text-[11.5px] text-ink-mut mt-0.5" data-c="task"></div>
        </div>
        <div class="rp-stats">
          <div><div class="rp-label">Battery</div><div class="rp-stat" data-c="batt"></div></div>
          <div><div class="rp-label">Speed</div><div class="rp-stat" data-c="speed"></div></div>
          <div><div class="rp-label">Drift</div><div class="rp-stat" data-c="drift"></div></div>
        </div>
        <div class="rp-actions">
          <button class="rp-primary" data-base="rp-primary" data-act="toggle"></button>
          <button data-act="task" ${isOperator() && g.has("mission.dispatch") ? "" : "disabled"} title="${isOperator() ? "" : "Sign in as an operator to dispatch tasks"}">Task</button>
          <button data-act="details">Details</button>
        </div>
        <div class="rp-note" data-c="note"></div>
        <details class="rp-drive" ${state.driveOpen ? "open" : ""}>
          <summary>Manual drive</summary>
          <div class="rp-drive-body">
            ${canVel ? `
              <div class="flex items-center justify-between text-[11.5px] text-ink-mut mb-1">
                <span>Speed</span><span class="mono" data-c="speedval"></span>
              </div>
              <input data-c="slider" type="range" class="speed w-full" min="0.05" max="${MAX_SPEED}" step="0.05" value="${r.speed_mps ?? 0.6}" />
              <div class="text-[11.5px] text-ink-mut mt-2 mb-1">Jog in a direction (overrides its route)</div>
              <div class="dpad max-w-[132px] mx-auto"></div>
              <div class="text-[11.5px] text-ink-mut mt-2" data-c="wp"></div>`
            : `<div class="text-[11.5px] text-ink-dim leading-snug">${isOperator()
                ? `${esc(r.vendor)} hasn't granted drive control to Orbital.`
                : "Sign in as an operator to drive robots, set waypoints, or change speed."}</div>`}
          </div>
        </details>
      </div>`;
    const id = r.id;
    box.querySelector('[data-act="toggle"]').onclick = () => { const cur = robotById(id); if (cur) quickControl(cur, cur.state === "halted" ? "resume" : "estop"); };
    box.querySelector('[data-act="task"]').onclick = () => openDispatch(id);
    box.querySelector('[data-act="details"]').onclick = () => openRobot(id);
    box.querySelector(".rp-drive").addEventListener("toggle", (e) => { state.driveOpen = e.target.open; });
    const slider = box.querySelector('[data-c="slider"]');
    if (slider) {
      slider.addEventListener("input", () => { box.querySelector('[data-c="speedval"]').textContent = `${(+slider.value).toFixed(2)} m/s`; });
      slider.addEventListener("change", () => control(`/api/dashboard/robot/${id}/speed`, { speed_mps: +slider.value }));
      const pad = box.querySelector(".dpad");
      for (const [glyph, heading] of DIRS) {
        const b = heading === null ? el(`<button class="stop" title="Stop jog">■</button>`) : el(`<button title="${heading}°">${glyph}</button>`);
        b.onclick = heading === null
          ? () => control(`/api/dashboard/robot/${id}/drive/stop`)
          : () => control(`/api/dashboard/robot/${id}/drive`, { heading_deg: heading, speed_mps: +slider.value });
        pad.appendChild(b);
      }
    }
  }

  if (modeTag) modeTag.textContent = MODE_LABEL[r.control_mode] || r.control_mode || "";
  patch(box, '[data-c="title"]', `${esc(r.vendor)} ${esc(r.model)}`);
  patch(box, '[data-c="sub"]', `${esc(r.id)} · ${esc(r.industry)}${r.fleet_role === "lead" ? " · lead" : ""}${r.simulated ? " · simulated" : ""}`);
  patch(box, '[data-c="status"]', inlineStatus(STATE_LABEL[r.state] || r.state, STATE_COLOR[r.state] || "#4A4F57"));
  box.querySelector('[data-c="nowbox"]').classList.toggle("halted", r.state === "halted");
  patch(box, '[data-c="doing"]', esc(robotDoing(r)));
  patch(box, '[data-c="task"]', r.state !== "halted" && r.current_task && r.current_task !== r.mission
    ? `<span style="color:${PHASE_COLOR[r.mission_phase] || "#80858C"}">●</span> ${esc(r.current_task)}` : "");
  patch(box, '[data-c="batt"]', `${Math.round(r.battery_pct)}%`);
  patch(box, '[data-c="speed"]', `${r.state === "active" ? (r.speed_mps ?? 0).toFixed(2) : "0.00"}`);
  patch(box, '[data-c="drift"]', `<span class="${driftColor(r.drift_delta_m)}">${r.drift_delta_m.toFixed(2)} m</span>`);
  toggleButtonState(box.querySelector('[data-act="toggle"]'), r);

  const access = stopStartAccess(r);
  let note;
  if (!access.ok) note = esc(access.why);
  else if (r.error_code === "DEMO_STOP") {
    const left = Math.max(0, Math.round(((state.demoStops[r.id] || 0) - Date.now()) / 1000));
    note = left ? `Starts again on its own in <span class="mono">${left}s</span> so the demo keeps running.` : "Starting again shortly.";
  } else if (r.simulated && !isOperator()) note = "Simulated robot: anyone can stop or start it in this demo.";
  else note = "Every stop and start is written to the audit log.";
  patch(box, '[data-c="note"]', note);

  const slider = box.querySelector('[data-c="slider"]');
  if (slider && document.activeElement !== slider) {
    patch(box, '[data-c="speedval"]', `${(r.speed_mps ?? 0).toFixed(2)} m/s`);
    if (+slider.value !== r.speed_mps && r.speed_mps != null) slider.value = r.speed_mps;
  }
  const wp = box.querySelector('[data-c="wp"]');
  if (wp) {
    const n = (r.waypoints || []).length;
    const html = n
      ? `Heading to a waypoint (${n} left) · <button class="panel-link" data-act="wpclear">Clear</button>`
      : "Or click the floor to send it to a waypoint. Shift-click to chain several.";
    if (wp.innerHTML !== html) {
      wp.innerHTML = html;
      const c = wp.querySelector('[data-act="wpclear"]');
      if (c) c.onclick = () => control(`/api/dashboard/robot/${r.id}/navigate/clear`);
    }
  }
}

// ── warehouse map ──────────────────────────────────────────────────────────────
const ZONE_COLOR = { keepout: "#DC2626", slow: "#D97706" };
const OBJECT_COLOR = { pallet: "#A16207", forklift: "#CA8A04", cart: "#0891B2", person: "#DB2777", default: "#4A4F57" };
const ROBOT_FILL = { active: "#16A34A", idle: "#D97706", charging: "#1D4ED8", cooldown: "#DC2626", halted: "#DC2626", offline: "#80858C" };

async function loadMap() {
  try {
    state.map = await getJSON("/api/dashboard/map");
    state.zones = state.map.zones || [];
    state.objects = state.map.objects || [];
    state.cameraCells = state.map.camera_cells || [];
    renderMap(); renderZoneBar();
  } catch (e) { console.error(e); }
}
// The drawing is letterboxed inside the SVG box when the box's shape differs from the floor's,
// so map the pointer through the SVG's own transform rather than the box.
function clickToWorld(evt, svg) {
  const pt = new DOMPoint(evt.clientX, evt.clientY).matrixTransform(svg.getScreenCTM().inverse());
  return { x: pt.x, y: state.map.height_m - pt.y };
}

// Live updates arrive a few times a second. Each moving thing glides from where it is drawn now
// to its newest position over one update interval, so motion reads as continuous.
const _glide = new Map();
function smoothPose(key, x, y, a = 0) {
  const now = performance.now();
  let g = _glide.get(key);
  if (!g) {
    g = { fx: x, fy: y, fa: a, tx: x, ty: y, ta: a, t0: now, dur: 1 };
    _glide.set(key, g);
  } else if (g.tx !== x || g.ty !== y || g.ta !== a) {
    const cur = glideAt(g, now);
    const jump = Math.hypot(x - cur.x, y - cur.y) > 3;
    Object.assign(g, jump ? { fx: x, fy: y, fa: a } : { fx: cur.x, fy: cur.y, fa: cur.a });
    Object.assign(g, { tx: x, ty: y, ta: a, t0: now, dur: state.tickMs * 1.05 });
  }
  g.seen = now;
  if (g.t0 + g.dur > state.animUntil) state.animUntil = g.t0 + g.dur;
  return glideAt(g, now);
}
function glideAt(g, now) {
  const k = Math.min(1, (now - g.t0) / g.dur);
  const da = Math.atan2(Math.sin(g.ta - g.fa), Math.cos(g.ta - g.fa));
  return { x: g.fx + (g.tx - g.fx) * k, y: g.fy + (g.ty - g.fy) * k, a: g.fa + da * k };
}
function noteLiveTick() {
  const now = performance.now();
  if (state.lastFleetAt) state.tickMs = Math.min(1000, Math.max(60, state.tickMs * 0.8 + (now - state.lastFleetAt) * 0.2));
  state.lastFleetAt = now;
  for (const [k, g] of _glide) if (now - g.seen > 10000) _glide.delete(k);
}

// A top-down forklift: yellow body with the cab and counterweight, forks reaching ahead to
// where a carried pallet sits. Drawn in the vehicle's own frame, then rotated to its yaw.
function forkliftGlyph(o, cx, cy, deg) {
  const L = o.size.length, Wd = o.size.width, h = Wd / 2;
  const forkFrom = L / 2 - 0.15, forkTo = L / 2 + L * 0.47;
  const label = `<text class="map-label" x="${cx}" y="${cy - Math.max(L, Wd) / 2 - 0.46}" fill="#854D0E" font-size="0.36" font-weight="700" text-anchor="middle">${esc(o.id)}</text>`
    + (o.activity ? `<text class="map-label" x="${cx}" y="${cy - Math.max(L, Wd) / 2 - 0.1}" fill="#3F3F46" font-size="0.28" text-anchor="middle">${esc(o.activity)}</text>` : "");
  return `<g transform="translate(${cx} ${cy}) rotate(${deg})">`
    + `<rect x="${forkFrom}" y="${-h * 0.62}" width="${forkTo - forkFrom}" height="0.08" rx="0.03" fill="#52525B"/>`
    + `<rect x="${forkFrom}" y="${h * 0.62 - 0.08}" width="${forkTo - forkFrom}" height="0.08" rx="0.03" fill="#52525B"/>`
    + `<rect x="${-L / 2}" y="${-h}" width="${L}" height="${Wd}" rx="0.16" fill="#FACC15" stroke="#A16207" stroke-width="0.05"/>`
    + `<rect x="${-L / 2}" y="${-h}" width="${L * 0.22}" height="${Wd}" rx="0.14" fill="#A16207" opacity="0.55"/>`
    + `<rect x="${-L * 0.1}" y="${-h * 0.7}" width="${L * 0.36}" height="${Wd * 0.7}" rx="0.08" fill="#1F2937" opacity="0.8"/>`
    + `<rect x="${L / 2 - 0.18}" y="${-h * 0.95}" width="0.12" height="${Wd * 0.95}" rx="0.03" fill="#3F3F46"/>`
    + `</g>` + label;
}

// A wooden pallet seen from above: deck boards across the stringers. Delivered pallets carry a
// small check so you can see the dock filling up for the truck.
function palletGlyph(o, cx, cy, deg) {
  const L = o.size.length, Wd = o.size.width, h = Wd / 2;
  const delivered = (o.activity || "").startsWith("Delivered");
  const carried = (o.activity || "") === "On the forks";
  let boards = "";
  for (let i = 0; i < 5; i++) {
    const bx = -L / 2 + 0.05 + i * (L - 0.1 - 0.12) / 4;
    boards += `<rect x="${bx}" y="${-h + 0.04}" width="0.12" height="${Wd - 0.08}" fill="#E8C48A"/>`;
  }
  const load = carried || delivered
    ? `<rect x="${-L / 2 + 0.1}" y="${-h + 0.1}" width="${L - 0.2}" height="${Wd - 0.2}" rx="0.05" fill="#B0875A" fill-opacity="0.55" stroke="#7C5A36" stroke-width="0.03"/>`
    : "";
  const check = delivered
    ? `<circle cx="${cx + L / 2 - 0.05}" cy="${cy - h + 0.05}" r="0.17" fill="#16A34A"/><path d="M${cx + L / 2 - 0.13} ${cy - h + 0.05} l0.06 0.06 l0.11 -0.12" stroke="#fff" stroke-width="0.045" fill="none"/>`
    : "";
  return `<g transform="translate(${cx} ${cy}) rotate(${deg})">`
    + `<rect x="${-L / 2}" y="${-h}" width="${L}" height="${Wd}" rx="0.05" fill="#A47148" stroke="#6B4423" stroke-width="0.04"/>`
    + boards + load + `</g>` + check;
}

// A box truck seen from above, cab away from the dock and the trailer's rear doors facing it.
// Only the end nearest the dock is inside the drawn yard; the rest runs off the top edge.
function truckGlyph(o, cx, cy, deg) {
  const L = o.size.length, Wd = o.size.width, h = Wd / 2;
  let ribs = "";
  for (let x = -L / 2 + 0.6; x < L / 2 - 0.3; x += 0.6) ribs += `<line x1="${x}" y1="${-h}" x2="${x}" y2="${h}" stroke="#D4D4D8" stroke-width="0.03"/>`;
  return `<g transform="translate(${cx} ${cy}) rotate(${deg})">`
    + `<rect x="${L / 2 + 0.15}" y="${-h * 0.9}" width="1.8" height="${Wd * 0.9}" rx="0.3" fill="#1D4ED8"/>`
    + `<rect x="${-L / 2}" y="${-h}" width="${L}" height="${Wd}" rx="0.08" fill="#FAFAFA" stroke="#52525B" stroke-width="0.05"/>`
    + ribs
    + `<rect x="${-L / 2}" y="${-h}" width="0.14" height="${Wd}" fill="#3F3F46"/>`
    + `<line x1="${-L / 2}" y1="0" x2="${-L / 2 + 0.14}" y2="0" stroke="#FAFAFA" stroke-width="0.03"/>`
    + `</g>`;
}

function renderMap() { state.mapDirty = true; scheduleMapFrame(); }
let _mapFrame = 0, _mapDrawnAt = 0;
function scheduleMapFrame() { if (!_mapFrame) _mapFrame = requestAnimationFrame(mapFrame); }
function mapFrame(ts) {
  _mapFrame = 0;
  if (!EMBED && state.view !== "live") return;           // showView("live") redraws
  const gliding = ts < state.animUntil;
  if (!state.mapDirty && !gliding) return;
  if (!state.mapDirty && ts - _mapDrawnAt < 30) { scheduleMapFrame(); return; }   // ~30 fps is plenty
  drawMap(state.mapDirty);
  state.mapDirty = false;
  _mapDrawnAt = ts;
  if (performance.now() < state.animUntil) scheduleMapFrame();
}

// Two layers: the floor, racks, zones and overlays only change on events; robots, people and
// objects are redrawn every frame while they glide.
function drawMap(full) {
  const svg = $("#map"), m = state.map;
  if (!svg || !m) return;
  let stat = svg.querySelector("#map-static"), dyn = svg.querySelector("#map-dyn");
  if (!stat || !dyn) {
    svg.innerHTML = `<g id="map-static"></g><g id="map-dyn"></g>`;
    stat = svg.querySelector("#map-static"); dyn = svg.querySelector("#map-dyn");
    full = true;
  }
  const W = m.width_m, H = m.height_m, yard = m.yard_m || 0;
  const Y = (y) => H - y;
  if (full) {
    svg.setAttribute("viewBox", `0 ${-yard} ${W} ${H + yard}`);
    svg.style.aspectRatio = `${W} / ${H + yard}`;
    stat.innerHTML = mapStaticLayer(m, W, H, yard, Y);
  }
  dyn.innerHTML = mapLiveLayer(m, H, yard, Y);
}

function mapStaticLayer(m, W, H, yard, Y) {
  const p = [];
  if (yard) {
    p.push(`<rect x="0" y="${-yard}" width="${W}" height="${yard}" fill="#E7E5E4"/>`);
    p.push(`<text x="0.4" y="${-yard + 0.55}" fill="#78716C" font-size="0.34" font-weight="600" letter-spacing="0.06">TRUCK YARD</text>`);
    if (m.dock) {
      const cx = m.dock.x + m.dock.w / 2;
      for (const dx of [-1.6, 1.6]) p.push(`<line x1="${cx + dx}" y1="${-yard}" x2="${cx + dx}" y2="0" stroke="#FAFAF9" stroke-width="0.08" stroke-dasharray="0.5 0.3"/>`);
    }
  }
  p.push(`<rect x="0" y="0" width="${W}" height="${H}" fill="#FFFFFF" stroke="#111418" stroke-width="0.06"/>`);
  for (let gx = 2; gx < W; gx += 2) p.push(`<line x1="${gx}" y1="0" x2="${gx}" y2="${H}" stroke="#D5D8D2" stroke-width="0.025"/>`);
  for (let gy = 2; gy < H; gy += 2) p.push(`<line x1="0" y1="${gy}" x2="${W}" y2="${gy}" stroke="#D5D8D2" stroke-width="0.025"/>`);

  if (m.dock) {
    const d = m.dock;
    p.push(`<rect x="${d.x}" y="${Y(d.y + d.h)}" width="${d.w}" height="${d.h}" rx="0.08" fill="#DBE4FB" stroke="#1D4ED8" stroke-width="0.03" opacity="0.85"/>`);
    p.push(`<text x="${d.x + d.w / 2}" y="${Y(d.y + d.h) + d.h / 2 + 0.16}" fill="#1D4ED8" font-size="0.42" text-anchor="middle" opacity="0.95">DOCK</text>`);
  }
  // Painted drop spots where forklifts leave outbound pallets for the truck.
  for (const [i, l] of (m.dock_lanes || []).entries()) {
    p.push(`<g><title>${esc(l.id)}</title><rect x="${l.x - 0.7}" y="${Y(l.y) - 0.6}" width="1.4" height="1.2" rx="0.06" fill="#EEF2FF" stroke="#1D4ED8" stroke-width="0.04" stroke-dasharray="0.16 0.1"/>`);
    p.push(`<text x="${l.x}" y="${Y(l.y) + 0.12}" fill="#1D4ED8" font-size="0.34" font-weight="600" text-anchor="middle" opacity="0.6">${i + 1}</text></g>`);
  }
  for (const r of m.racks) {
    p.push(`<rect x="${r.x}" y="${Y(r.y + r.h)}" width="${r.w}" height="${r.h}" rx="0.08" fill="#C9CCC6" stroke="#8A8F88" stroke-width="0.035"/>`);
    p.push(`<text x="${r.x + r.w / 2}" y="${Y(r.y + r.h / 2) + 0.14}" fill="#111418" font-size="0.42" font-weight="600" text-anchor="middle">${esc(r.id)}</text>`);
  }
  for (const c of (m.charge_stations || [])) {
    p.push(`<circle cx="${c.x}" cy="${Y(c.y)}" r="0.42" fill="#1D4ED8" opacity="0.16"/>`);
    p.push(`<text x="${c.x}" y="${Y(c.y) + 0.16}" fill="#1D4ED8" font-size="0.5" text-anchor="middle">⚡</text>`);
  }
  // Named work stations — the pick/drop points missions shuttle between (labelled so the
  // operator can read "Aisle AB → Dock" straight off the floor).
  for (const s of (m.stations || [])) {
    p.push(`<rect x="${s.x - 0.34}" y="${Y(s.y) - 0.34}" width="0.68" height="0.68" rx="0.1" fill="#DBE4FB" stroke="#1D4ED8" stroke-width="0.04"/>`);
    p.push(`<text x="${s.x}" y="${Y(s.y) + 0.5}" fill="#111418" font-size="0.3" font-weight="500" text-anchor="middle">${esc(s.id)}</text>`);
  }
  // Overhead camera rig — Orbital's ground-truth localization. Drawn as a violet camera glyph
  // with a soft coverage halo so it's never mistaken for a robot or a waypoint.
  for (const cam of (m.cameras || [])) {
    const cov = cam.coverage_m || 4.0;
    p.push(`<circle cx="${cam.x}" cy="${Y(cam.y)}" r="${cov}" fill="#6D28D9" opacity="0.04"/>`);
    p.push(`<circle cx="${cam.x}" cy="${Y(cam.y)}" r="${cov}" fill="none" stroke="#6D28D9" stroke-width="0.02" stroke-dasharray="0.2 0.22" opacity="0.6"/>`);
    p.push(`<g transform="translate(${cam.x},${Y(cam.y)})">`);
    p.push(`<rect x="-0.26" y="-0.19" width="0.52" height="0.38" rx="0.08" fill="#F3F0FF" stroke="#6D28D9" stroke-width="0.045"/>`);
    p.push(`<circle cx="0" cy="0" r="0.11" fill="none" stroke="#6D28D9" stroke-width="0.05"/>`);
    p.push(`<rect x="0.2" y="-0.1" width="0.14" height="0.2" rx="0.04" fill="#6D28D9"/>`);
    p.push(`</g>`);
  }

  // Keep-out (red) and slow (amber) zones: the planner never enters a keep-out and caps
  // speed inside a slow zone.
  for (const z of state.zones) {
    const color = ZONE_COLOR[z.type] || ZONE_COLOR.keepout;
    const pts = z.polygon.map(([x, y]) => `${x},${Y(y)}`).join(" ");
    p.push(`<polygon points="${pts}" fill="${color}" fill-opacity="0.18" stroke="${color}" stroke-width="0.06" stroke-dasharray="0.22 0.14"/>`);
    const cx = z.polygon.reduce((a, q) => a + q[0], 0) / z.polygon.length;
    const cy = z.polygon.reduce((a, q) => a + q[1], 0) / z.polygon.length;
    const label = z.type === "slow" ? `${z.name} · ≤${z.speed_cap_mps} m/s` : z.name;
    p.push(`<text x="${cx}" y="${Y(cy) + 0.12}" fill="${color}" font-size="0.32" font-weight="600" text-anchor="middle" opacity="0.95">${esc(label)}</text>`);
  }
  const draft = state.zoneDraft;
  if (draft && draft.points.length) {
    const color = ZONE_COLOR[draft.type];
    const pts = draft.points.map((q) => `${q.x},${Y(q.y)}`).join(" ");
    p.push(`<polygon points="${pts}" fill="${color}" fill-opacity="0.2" stroke="${color}" stroke-width="0.06"/>`);
    for (const q of draft.points) p.push(`<circle cx="${q.x}" cy="${Y(q.y)}" r="0.12" fill="${color}"/>`);
  }

  // Selected run vs its golden run: gold = the reference path, cyan = this run.
  const ov = state.runOverlay;
  if (ov) {
    const line = (samples, color, dash, width) => samples && samples.length > 1
      ? p.push(`<polyline points="${samples.map((s) => `${s.x},${Y(s.y)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="${width}" ${dash ? `stroke-dasharray="${dash}"` : ""} stroke-linejoin="round" opacity="0.95"/>`)
      : null;
    line(ov.golden?.samples, "#B45309", "0.3 0.15", 0.14);
    line(ov.run.samples, "#1D4ED8", null, 0.07);
    const end = ov.run.samples[ov.run.samples.length - 1];
    p.push(`<text x="${end.x}" y="${Y(end.y) - 0.45}" fill="#1D4ED8" font-size="0.32" font-weight="600" text-anchor="middle">${esc(ov.run.task_key)}</text>`);
  }

  // Floor cells the cameras see as blocked; the planner routes every robot around these.
  for (const [x0, y0, x1, y1] of state.cameraCells) {
    p.push(`<rect x="${x0}" y="${Y(y1)}" width="${x1 - x0}" height="${y1 - y0}" fill="#4A4F57" fill-opacity="0.32" stroke="#4A4F57" stroke-opacity="0.5" stroke-width="0.02"/>`);
  }

  if (yard && m.dock) p.push(`<line x1="${m.dock.x}" y1="0" x2="${m.dock.x + m.dock.w}" y2="0" stroke="#1D4ED8" stroke-width="0.14"/>`);
  return p.join("");
}

function mapLiveLayer(m, H, yard, Y) {
  const p = [];
  // Tracked objects: footprint rotated to the object's yaw, a motion arrow (1 s ahead) when it
  // is moving, and its label. Objects past their expiry are dropped even between updates.
  const now = Date.now() / 1000 + state.clockSkew;
  state.objects = state.objects.filter((o) => o.expires_at > now);
  const order = { truck: -1, forklift: 1, pallet: 2 };
  const objs = [...state.objects].sort((a, b) => (order[a.label] || 0) - (order[b.label] || 0)).map((o) => {
    const g = smoothPose(`o:${o.source}:${o.id}`, o.x, o.y, o.yaw);
    return { ...o, x: g.x, y: g.y, yaw: g.a };
  });
  for (const o of objs) {
    const color = OBJECT_COLOR[o.label] || OBJECT_COLOR.default;
    const L = o.size.length, Wd = o.size.width, deg = -o.yaw * 180 / Math.PI;
    const cx = o.x, cy = Y(o.y);
    const tip = `${esc(o.label)} ${esc(o.id)}${o.activity ? ` · ${esc(o.activity)}` : ""} · ${(o.confidence * 100).toFixed(0)}% · ${esc(o.source)}`;
    if (o.label === "truck") {
      const lx = o.x + o.size.width / 2 + 0.35;
      p.push(`<g class="map-object"><title>${tip}</title>${truckGlyph(o, cx, cy, deg)}</g>`);
      p.push(`<text class="map-label" x="${lx}" y="${-yard * 0.62}" fill="#1E3A8A" font-size="0.36" font-weight="700">${esc(o.id)}</text>`);
      if (o.activity) p.push(`<text class="map-label" x="${lx}" y="${-yard * 0.3}" fill="#3F3F46" font-size="0.28">${esc(o.activity)}</text>`);
      continue;
    }
    if (o.label === "forklift" || o.label === "pallet") {
      p.push(`<g class="map-object"><title>${tip}</title>`);
      p.push(o.label === "forklift" ? forkliftGlyph(o, cx, cy, deg) : palletGlyph(o, cx, cy, deg));
      p.push(`</g>`);
      continue;
    }
    p.push(`<g><title>${tip}</title>`);
    p.push(`<rect x="${cx - L / 2}" y="${cy - Wd / 2}" width="${L}" height="${Wd}" rx="0.06" fill="${color}" fill-opacity="0.18" stroke="${color}" stroke-width="0.045" transform="rotate(${deg} ${cx} ${cy})"/>`);
    p.push(`<line x1="${cx}" y1="${cy}" x2="${cx + Math.cos(o.yaw) * L / 2}" y2="${Y(o.y + Math.sin(o.yaw) * L / 2)}" stroke="${color}" stroke-width="0.04" opacity="0.7"/>`);
    if (Math.hypot(o.vx, o.vy) > 0.05) {
      const ax = o.x + o.vx, ay = o.y + o.vy;
      p.push(`<line x1="${cx}" y1="${cy}" x2="${ax}" y2="${Y(ay)}" stroke="${color}" stroke-width="0.05" stroke-dasharray="0.12 0.08"/>`);
      p.push(`<circle cx="${ax}" cy="${Y(ay)}" r="0.07" fill="${color}"/>`);
    }
    p.push(`<text x="${cx}" y="${cy - Wd / 2 - 0.12}" fill="${color}" font-size="0.28" font-weight="600" text-anchor="middle">${esc(o.label)}</text></g>`);
  }

  // Calibrated floor markers, coloured by how well the rig agrees on each one.
  for (const mk of (state.calibration?.markers || [])) {
    if (!mk.pose || mk.status === "unanchored") continue;
    const color = CALIB_COLOR[mk.status] || CALIB_COLOR.ok;
    const x = mk.pose.x, y = Y(mk.pose.y);
    p.push(`<g><title>Marker ${mk.marker_id} · ${esc(CALIB_LABEL[mk.status] || mk.status)}</title>`);
    p.push(`<rect x="${x - 0.13}" y="${y - 0.13}" width="0.26" height="0.26" fill="${color}" fill-opacity="0.25" stroke="${color}" stroke-width="0.04" transform="rotate(45 ${x} ${y})"/>`);
    p.push(`<text x="${x}" y="${y - 0.24}" fill="${color}" font-size="0.24" text-anchor="middle" opacity="0.9">${mk.marker_id}</text></g>`);
  }

  // People on the floor: a faint ring marks the radius inside which robots slow down.
  const slowR = m.people_safety?.slow_radius_m ?? 2.0;
  for (const w of state.workers) {
    const g = smoothPose(`w:${w.id}`, w.pose.x, w.pose.y);
    const x = g.x, y = Y(g.y), off = w.status !== "on_shift";
    p.push(`<circle cx="${x}" cy="${y}" r="${slowR}" fill="#DB2777" opacity="${off ? 0.03 : 0.06}"/>`);
    p.push(`<path d="M ${x} ${y - 0.3} L ${x + 0.26} ${y} L ${x} ${y + 0.3} L ${x - 0.26} ${y} Z" fill="${off ? "#C4B5BD" : "#DB2777"}" stroke="#FFFFFF" stroke-width="0.05"/>`);
    p.push(`<text x="${x}" y="${y + 0.62}" fill="#DB2777" font-size="0.3" text-anchor="middle" opacity="${off ? 0.6 : 0.95}">${esc(workerName(w))}</text>`);
  }

  // Lead-published relay waypoints (shared shuttle network).
  for (const rp of (state.sequence?.relay_points || [])) {
    p.push(`<circle cx="${rp.x}" cy="${Y(rp.y)}" r="0.38" fill="none" stroke="#B45309" stroke-width="0.05" stroke-dasharray="0.12 0.1" opacity="0.85"/>`);
    p.push(`<text x="${rp.x}" y="${Y(rp.y) - 0.48}" fill="#B45309" font-size="0.32" text-anchor="middle">${esc(rp.id)}</text>`);
  }

  const pose = {};
  for (const r of state.robots) {
    const e = smoothPose(`r:${r.id}`, r.pose_external.x, r.pose_external.y, r.pose_external.theta);
    const i = smoothPose(`ri:${r.id}`, r.pose_internal.x, r.pose_internal.y);
    pose[r.id] = { ex: { x: e.x, y: e.y, theta: e.a }, ins: { x: i.x, y: i.y } };
  }
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 380);
  for (const r of state.robots) {
    const { ex, ins } = pose[r.id];
    const selected = r.id === state.selectedRobot;
    // Hand-off link: dashed amber line to the peer that received this robot's payload.
    const partner = r.handoff_partner ? pose[r.handoff_partner] : null;
    if (partner) {
      const pe = partner.ex;
      p.push(`<line x1="${ex.x}" y1="${Y(ex.y)}" x2="${pe.x}" y2="${Y(pe.y)}" stroke="#D97706" stroke-width="0.05" stroke-dasharray="0.2 0.16" opacity="0.85"/>`);
      p.push(`<circle cx="${pe.x}" cy="${Y(pe.y)}" r="0.16" fill="#D97706" opacity="0.9"/>`);
    }
    if (r.waypoints && r.waypoints.length) {
      // Draw the camera-planned route the robot actually follows (bends around racks),
      // falling back to a straight line to the waypoints if no route is published.
      const line = (r.path && r.path.length) ? r.path : r.waypoints;
      const pts = [`${ex.x},${Y(ex.y)}`, ...line.map((w) => `${w.x},${Y(w.y)}`)].join(" ");
      p.push(`<polyline points="${pts}" fill="none" stroke="#1D4ED8" stroke-width="0.05" stroke-dasharray="0.25 0.18" opacity="0.9"/>`);
      r.waypoints.forEach((w, i) => {
        const last = i === r.waypoints.length - 1;
        p.push(`<circle cx="${w.x}" cy="${Y(w.y)}" r="${last ? 0.24 : 0.16}" fill="${last ? "#1D4ED8" : "#FFFFFF"}" stroke="#1D4ED8" stroke-width="0.05"/>`);
      });
    }
    // manual jog heading indicator
    if (r.control_mode === "manual") {
      p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.42" fill="none" stroke="#D97706" stroke-width="0.05" stroke-dasharray="0.12 0.1"/>`);
    }
    if (r.drift_delta_m > 0.05) {
      p.push(`<line x1="${ins.x}" y1="${Y(ins.y)}" x2="${ex.x}" y2="${Y(ex.y)}" stroke="#80858C" stroke-width="0.03" stroke-dasharray="0.12 0.12" opacity="0.85"/>`);
      p.push(`<circle cx="${ins.x}" cy="${Y(ins.y)}" r="0.16" fill="none" stroke="#4A4F57" stroke-width="0.045" opacity="0.85"/>`);
    }
    if (selected) p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.46" fill="none" stroke="#1D4ED8" stroke-width="0.07"/>`);
    const fill = ROBOT_FILL[r.state] || ROBOT_FILL.offline;
    const isLead = r.fleet_role === "lead";
    const halo = r.state === "active" ? `<circle cx="${ex.x}" cy="${Y(ex.y)}" r="${0.36 + 0.08 * pulse}" fill="${fill}" opacity="${0.1 + 0.1 * pulse}"/>` : "";
    p.push(`<g data-robot="${esc(r.id)}" style="cursor:pointer">`);
    p.push(halo);
    if (isLead) {
      p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.34" fill="none" stroke="#B45309" stroke-width="0.06"/>`);
    }
    p.push(`<circle cx="${ex.x}" cy="${Y(ex.y)}" r="0.24" fill="${fill}" stroke="#FFFFFF" stroke-width="0.06"/>`);
    const hx = ex.x + Math.cos(ex.theta) * 0.4, hy = ex.y + Math.sin(ex.theta) * 0.4;
    p.push(`<line x1="${ex.x}" y1="${Y(ex.y)}" x2="${hx}" y2="${Y(hy)}" stroke="#111418" stroke-width="0.05"/>`);
    const label = isLead ? `${r.id}★` : r.id;
    p.push(`<text x="${ex.x + 0.34}" y="${Y(ex.y) - 0.26}" fill="${isLead ? "#B45309" : "#111418"}" font-size="0.36" font-weight="600" stroke="#FFFFFF" stroke-width="0.08" paint-order="stroke">${esc(label)}</text>`);
    p.push(`</g>`);
  }
  return p.join("");
}

// ── robot panel helpers ─────────────────────────────────────────────────────────
function isOperator() {
  const a = state.auth;
  return !a || !a.sign_in_available || ["operator", "admin"].includes(a.role);
}
const HALT_REASON = {
  E_STOP: "Stopped by an operator E-Stop. Stays stopped until someone starts it.",
  DEMO_STOP: "Stopped from the demo. Starts again on its own in two minutes.",
  HUMAN_STOP: "Paused: a person is inside its stop radius. Resumes when they step away.",
  DRIFT_HALT: "Halted: its own position estimate drifted too far from the camera view.",
  AUTO_ESTOP: "Auto-halted by Orbital while it re-converges its position.",
};
function robotDoing(r) {
  if (r.state === "halted") return HALT_REASON[r.error_code] || "Safety stop engaged. Waiting to be started again.";
  if (r.state === "charging") return `Docked and charging (${Math.round(r.battery_pct)}%)`;
  if (r.state === "cooldown") return "Just delivered. Short pause before the next task.";
  if (r.control_mode === "visual_nav") return "Driving to an operator waypoint, steered by the overhead cameras";
  if (r.control_mode === "manual") return "Under manual control by an operator";
  return r.mission || r.current_task || (r.state === "idle" ? "Idle. Joins the next sequence." : "Autonomous patrol");
}
// Who may stop/start this robot from here: OEM must grant control.estop, and anonymous
// visitors only get the simulated demo fleet.
function stopStartAccess(r) {
  if (!vendorGrants(r.vendor).has("control.estop")) return { ok: false, why: `${r.vendor} hasn't granted stop/start to Orbital. This robot is monitor-only.` };
  if (!r.simulated && !isOperator()) return { ok: false, why: "Real robot. Sign in as an operator to stop or start it." };
  return { ok: true, why: "" };
}
async function quickControl(r, action) {
  state.busy = r.id; renderControlPanel(); renderFleet();
  try {
    const out = await postJSON(`/api/dashboard/robot/${r.id}/${action}`);
    if (out.auto_resume_s) state.demoStops[r.id] = Date.now() + out.auto_resume_s * 1000;
    if (action === "resume") delete state.demoStops[r.id];
    toast(action === "estop" ? `${r.model} (${r.id}) stopped` : `${r.model} (${r.id}) started`);
  } catch (e) {
    toast(e.status === 403 ? blockedMsg(e) : `Failed: ${e.detail}`, "warn");
  } finally {
    state.busy = null; renderControlPanel(); renderFleet();
  }
}
// Patch an element's HTML only when it changed, so live ticks never swap a node mid-click.
function patch(root, sel, html) {
  const n = root.querySelector(sel);
  if (n && n.innerHTML !== html) n.innerHTML = html;
  return n;
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
    if (e.status === 403) toast(/control\.velocity/.test(String(e.detail)) ? `Blocked: ${e.detail} — grant control.velocity to this OEM` : blockedMsg(e), "warn");
    else if (e.status === 409) toast(`Refused: ${e.detail}`, "warn");
    else toast(`Failed: ${e.detail}`, "warn");
  }
}

// ── zones ─────────────────────────────────────────────────────────────────────
function startZoneDraft(type) {
  state.zoneDraft = { type, points: [] };
  renderZoneBar(); renderMap();
}
function cancelZoneDraft() {
  state.zoneDraft = null;
  renderZoneBar(); renderMap();
}
async function finishZoneDraft() {
  const d = state.zoneDraft;
  if (!d) return;
  if (d.points.length < 3) { toast("A zone needs at least 3 points", "warn"); return; }
  const fallback = `${d.type === "slow" ? "Slow zone" : "Keep-out"} ${state.zones.filter((z) => z.type === d.type).length + 1}`;
  const body = {
    name: ($("#zone-name")?.value || "").trim() || fallback,
    type: d.type,
    polygon: d.points.map((q) => [q.x, q.y]),
  };
  if (d.type === "slow") body.speed_cap_mps = parseFloat($("#zone-cap")?.value) || 0.3;
  try {
    const zone = await postJSON("/api/dashboard/zones", body);
    if (!state.zones.some((z) => z.id === zone.id)) state.zones.push(zone);
    state.zoneDraft = null;
    toast(`Zone "${zone.name}" added — robots re-plan around it`);
  } catch (e) {
    toast(e.status === 403 ? blockedMsg(e) : `Failed: ${e.detail}`, "warn");
  }
  renderZoneBar(); renderMap();
}
async function deleteZone(id) {
  try {
    await reqJSON("DELETE", `/api/dashboard/zones/${id}`);
    state.zones = state.zones.filter((z) => z.id !== id);
    toast("Zone removed");
  } catch (e) {
    toast(e.status === 403 ? blockedMsg(e) : `Failed: ${e.detail}`, "warn");
  }
  renderZoneBar(); renderMap();
}

function renderZoneBar() {
  const bar = $("#zone-bar");
  if (!bar) return;
  const d = state.zoneDraft;
  bar.innerHTML = "";
  if (d) {
    const color = ZONE_COLOR[d.type];
    bar.appendChild(el(`<span class="text-[11.5px] font-medium" style="color:${color}">Drawing ${d.type === "slow" ? "slow zone" : "keep-out"} · ${d.points.length} pt${d.points.length === 1 ? "" : "s"}</span>`));
    bar.appendChild(el(`<input id="zone-name" placeholder="Name" maxlength="60" class="bg-surface-input border border-line rounded-md px-2 py-1 text-[12px] w-36"/>`));
    if (d.type === "slow") {
      bar.appendChild(el(`<label class="flex items-center gap-1 text-[11.5px] text-ink-mut">cap <input id="zone-cap" type="number" min="0.05" max="5" step="0.05" value="0.3" class="bg-surface-input border border-line rounded-md px-2 py-1 text-[12px] w-16"/> m/s</label>`));
    }
    const finish = el(`<button class="text-[11.5px] px-2.5 py-1 rounded-md bg-brand/20 text-brand hover:bg-brand/30">Finish</button>`);
    finish.onclick = finishZoneDraft;
    const cancel = el(`<button class="text-[11.5px] px-2.5 py-1 rounded-md bg-surface-input hover:bg-line-strong">Cancel</button>`);
    cancel.onclick = cancelZoneDraft;
    bar.append(finish, cancel);
    bar.appendChild(el(`<span class="text-[10.5px] text-ink-dim">click the floor to add corners · Enter or double-click to finish · Esc to cancel</span>`));
    return;
  }
  const zb = $("#btn-zones");
  if (zb) zb.textContent = state.zones.length ? `Zones · ${state.zones.length}` : "Zones";
  bar.appendChild(el(`<span class="text-[11.5px] text-ink-mut mr-1">${isOperator() ? "Keep robots out of an area, or slow them down in it:" : "Zones on this floor. Sign in as an operator to add or remove them."}</span>`));
  for (const [type, label] of isOperator() ? [["keepout", "+ Keep-out"], ["slow", "+ Slow zone"]] : []) {
    const b = el(`<button class="text-[11.5px] px-2.5 py-1 rounded-md border hover:bg-surface-input" style="border-color:${ZONE_COLOR[type]}66;color:${ZONE_COLOR[type]}">${label}</button>`);
    b.onclick = () => startZoneDraft(type);
    bar.appendChild(b);
  }
  for (const z of state.zones) {
    const color = ZONE_COLOR[z.type] || ZONE_COLOR.keepout;
    const vendors = z.vendors && z.vendors.length ? ` · ${z.vendors.join(", ")}` : "";
    const chip = el(`<span class="flex items-center gap-1.5 text-[11px] pl-2 pr-1 py-0.5 rounded-full bg-surface-raised border border-line">
      <span class="inline-block w-2 h-2 rounded-sm" style="background:${color}"></span>${esc(z.name)}<span class="text-ink-dim">${esc(vendors)}</span>
      <button class="px-1 text-ink-dim hover:text-red-700" title="Remove zone" aria-label="Remove zone ${esc(z.name)}">×</button></span>`);
    chip.querySelector("button").onclick = () => deleteZone(z.id);
    bar.appendChild(chip);
  }
}

function wireMap() {
  const svg = $("#map");
  if (!svg) return;
  svg.addEventListener("dblclick", (e) => {
    if (!state.zoneDraft) return;
    e.preventDefault();
    finishZoneDraft();
  });
  document.addEventListener("keydown", (e) => {
    if (!state.zoneDraft) return;
    if (e.key === "Enter") finishZoneDraft();
    else if (e.key === "Escape") cancelZoneDraft();
  });
  // The map redraws many times a second, so a robot can be replaced by a fresh element between
  // press and release and the browser then drops the click. Select on press instead.
  let selectedOnPress = false;
  svg.addEventListener("pointerdown", (e) => {
    const id = state.zoneDraft ? null : e.target.closest("[data-robot]")?.getAttribute("data-robot");
    selectedOnPress = !!id;
    if (id) selectRobot(id);
  });
  svg.addEventListener("click", (e) => {
    if (state.zoneDraft) {
      // A double-click fires two clicks first; ignore the second so it doesn't add a duplicate corner.
      if (e.detail > 1) return;
      const q = clickToWorld(e, svg);
      state.zoneDraft.points.push({ x: +q.x.toFixed(2), y: +Math.min(q.y, state.map.height_m).toFixed(2) });
      const name = $("#zone-name")?.value, cap = $("#zone-cap")?.value;
      renderZoneBar(); renderMap();
      if (name && $("#zone-name")) $("#zone-name").value = name;
      if (cap && $("#zone-cap")) $("#zone-cap").value = cap;
      return;
    }
    if (selectedOnPress || e.target.closest("[data-robot]")) { selectedOnPress = false; return; }
    if (state.selectedRobot) {
      const p = clickToWorld(e, svg);
      if (p.y > state.map.height_m) return;      // the truck yard is outside the floor
      setWaypoint(state.selectedRobot, +p.x.toFixed(2), +p.y.toFixed(2), e.shiftKey);
    }
  });
}


// ── validation: golden runs (Sprint K) ──────────────────────────────────────
const VERDICT_COLOR = { pass: "#16A34A", golden: "#B45309", slow: "#D97706", deviated: "#DC2626",
  unstable: "#BE123C", failed: "#DC2626", aborted: "#80858C", no_golden: "#4A4F57" };
const VERDICT_LABEL = { pass: "pass", golden: "★ golden", slow: "slow", deviated: "deviated",
  unstable: "unstable", failed: "failed", aborted: "pre-empted", no_golden: "no golden yet" };
async function loadRuns() {
  try { state.runs = await getJSON("/api/dashboard/runs?limit=30"); renderRuns(); }
  catch (_) {}
}
async function showRun(id) {
  if (state.runOverlay?.run.id === id) { state.runOverlay = null; renderRuns(); renderMap(); return; }
  try {
    const d = await getJSON(`/api/dashboard/runs/${encodeURIComponent(id)}`);
    state.runOverlay = { run: d.run, golden: d.golden };
    renderRuns(); renderMap();
    showView("live");
  } catch (e) { toast(`Could not load run: ${e.message || e}`, "warn"); }
}
async function makeGolden(id) {
  try {
    await postJSON(`/api/dashboard/runs/${encodeURIComponent(id)}/golden`);
    await loadRuns();
  } catch (e) { toast(`Could not set golden run: ${e.message || e}`, "warn"); }
}
function renderRuns() {
  const body = $("#runs");
  if (!body) return;
  const counts = {};
  for (const r of state.runs) counts[r.score.verdict] = (counts[r.score.verdict] || 0) + 1;
  const chips = $("#run-verdicts");
  if (chips) chips.innerHTML = Object.entries(counts).map(([v, n]) =>
    `<span class="text-[10px] px-1.5 py-0.5 rounded-full mono" style="color:${VERDICT_COLOR[v] || "#4A4F57"};background:${VERDICT_COLOR[v] || "#4A4F57"}1f">${n} ${esc(VERDICT_LABEL[v] || v)}</span>`).join("");
  if (!state.runs.length) {
    body.innerHTML = `<tr><td colspan="8" class="px-4 py-5 text-[12px] text-ink-dim">No runs recorded yet. Every station-to-station leg is recorded; the first clean run of a task becomes its golden run.</td></tr>`;
    return;
  }
  const selected = state.runOverlay?.run.id;
  body.innerHTML = "";
  for (const r of state.runs) {
    const s = r.score, color = VERDICT_COLOR[s.verdict] || "#4A4F57";
    const cycle = s.golden_cycle_time_s != null ? `${s.cycle_time_s.toFixed(1)} s <span class="text-ink-dim">/ ${s.golden_cycle_time_s.toFixed(1)}</span>` : `${s.cycle_time_s.toFixed(1)} s`;
    const dev = s.max_dev_m != null ? `${(s.max_dev_m * 100).toFixed(0)} cm` : "—";
    const canGolden = !r.golden && r.outcome === "complete";
    const row = el(`
      <tr class="cursor-pointer hover:bg-surface-raised/60 ${r.id === selected ? "bg-surface-raised" : ""}" title="${esc((s.reasons || []).join(" "))}">
        <td class="px-3 py-1.5 mono text-ink-dim">${fmtTime(r.ended_at)}</td>
        <td class="px-3 py-1.5">${esc(r.robot_id)} <span class="text-ink-dim">${esc(r.vendor)}</span></td>
        <td class="px-3 py-1.5 text-ink-mut">${esc(r.task_key)}</td>
        <td class="px-3 py-1.5 text-right mono">${cycle}</td>
        <td class="px-3 py-1.5 text-right mono">${dev}</td>
        <td class="px-3 py-1.5 text-right mono">${s.recoveries}</td>
        <td class="px-3 py-1.5"><span class="text-[10.5px] font-medium" style="color:${color}">${esc(VERDICT_LABEL[s.verdict] || s.verdict)}</span>
          ${s.reasons && s.reasons.length ? `<span class="text-[10.5px] text-ink-dim"> · ${esc(s.reasons[0])}</span>` : ""}</td>
        <td class="px-3 py-1.5 text-right">${canGolden ? `<button class="mk-golden text-[10.5px] text-brand hover:underline">Make golden</button>` : ""}</td>
      </tr>`);
    row.onclick = () => showRun(r.id);
    const btn = row.querySelector(".mk-golden");
    if (btn) btn.onclick = (e) => { e.stopPropagation(); makeGolden(r.id); };
    body.appendChild(row);
  }
}

// ── rig calibration (Sprint H) ────────────────────────────────────────────────
const CALIB_COLOR = { ok: "#16A34A", likely_error: "#DC2626", single_view: "#D97706", unanchored: "#4A4F57",
  moved: "#DC2626", high_residual: "#D97706" };
const CALIB_LABEL = { ok: "ok", likely_error: "likely bumped", single_view: "seen once", unanchored: "not linked",
  moved: "camera moved", high_residual: "poor fit" };
async function loadCalibration() {
  try {
    const data = await getJSON("/api/dashboard/calibration");
    state.calibration = data.calibration;
    renderCalibration(); renderMap();
  } catch (_) {}
}
function renderCalibration() {
  const box = $("#calibration"), badge = $("#calib-status");
  if (!box) return;
  const c = state.calibration;
  if (!c) {
    if (badge) { badge.textContent = "none"; badge.style.color = ""; }
    box.innerHTML = `<div class="px-4 py-4 text-[12px] text-ink-dim leading-relaxed">No calibration uploaded yet. Capture the floor markers, then run
      <span class="mono text-ink-mut">scripts/calibrate_rig.py captures.json --upload ${esc(location.origin)}</span>.
      <a href="/setup#calibrate" class="text-azure underline">Setup guide</a></div>`;
    return;
  }
  if (badge) {
    badge.textContent = c.ok ? "clean" : "needs fixing";
    badge.style.color = c.ok ? CALIB_COLOR.ok : CALIB_COLOR.likely_error;
  }
  const flagged = [
    ...c.markers.filter((m) => m.status !== "ok").map((m) =>
      ({ label: `Marker ${m.marker_id}`, status: m.status,
         detail: m.status === "likely_error" ? `off by ${(m.max_m * 100).toFixed(1)} cm` : `${m.views} view${m.views === 1 ? "" : "s"}` })),
    ...c.captures.filter((k) => k.status !== "ok").map((k) =>
      ({ label: `Capture ${k.id}`, status: k.status,
         detail: k.offset_m != null && k.status === "moved" ? `${(k.offset_m * 100).toFixed(0)} cm, ${k.offset_deg.toFixed(1)}°` : `${(k.rms_m * 100).toFixed(1)} cm RMS` })),
  ];
  const mapped = c.markers.filter((m) => m.pose && m.status !== "unanchored").length;
  const rows = flagged.map((f) => `
    <div class="flex items-center gap-2 px-3 py-1.5">
      <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:${CALIB_COLOR[f.status] || "#4A4F57"}"></span>
      <span class="text-[11.5px] text-ink truncate">${esc(f.label)}</span>
      <span class="text-[10.5px] text-ink-mut">${esc(CALIB_LABEL[f.status] || f.status)}</span>
      <span class="text-[10px] text-ink-dim mono ml-auto shrink-0">${esc(f.detail)}</span>
    </div>`).join("");
  const hints = (c.hints || []).map((h) => `<li>${esc(h)}</li>`).join("");
  box.innerHTML = `
    <div class="px-3 py-2 text-[11.5px] text-ink-mut">
      ${mapped} markers · ${c.captures.length} captures · RMS <span class="mono">${(c.rms_m * 100).toFixed(1)} cm</span>
      <span class="text-ink-dim">(limit ${(c.threshold_m * 100).toFixed(0)} cm) · ${fmtTime(c.received_at)}</span>
    </div>
    ${rows ? `<div class="divide-y divide-line/60 border-t border-line/60">${rows}</div>` : ""}
    ${hints ? `<ul class="px-4 py-2.5 border-t border-line/60 list-disc pl-6 space-y-1 text-[11.5px] text-ink-mut leading-relaxed">${hints}</ul>`
            : `<div class="px-3 pb-2.5 text-[11.5px]" style="color:${CALIB_COLOR.ok}">Every marker and camera agrees. Nothing to fix.</div>`}`;
}

// ── alerts ────────────────────────────────────────────────────────────────────
const SEV_STYLE = { critical: "text-red-700", warning: "text-amber-700", info: "text-sky-700" };
const SEV_DOT = { critical: "#BE123C", warning: "#D97706", info: "#1D4ED8" };
async function ackAlert(id) {
  const a = state.alerts.find((x) => x.id === id);
  if (!a || a.acknowledged) return;
  await postJSON(`/api/dashboard/alerts/${id}/ack`);
  a.acknowledged = true;
}
const ALERT_TITLE = {
  drift_exceeded: "Drifted off its own map",
  hijack_suspected: "Moving without a command",
  ghost_command: "Reports moving, cameras see it still",
  low_battery: "Low battery",
  human_proximity: "Person inside its stop radius",
};
// Open alerts collapsed to one row per robot + kind: the same person stop firing 30 times
// is one thing to look at, not 30.
function alertGroups() {
  const groups = new Map();
  for (const a of state.alerts) {
    if (a.acknowledged) continue;
    const k = `${a.robot_id}|${a.type}`;
    const g = groups.get(k) || { key: k, robotId: a.robot_id, type: a.type, severity: a.severity, latest: a, ids: [] };
    g.ids.push(a.id);
    if (a.ts > g.latest.ts) g.latest = a;
    if (a.severity === "critical") g.severity = "critical";
    groups.set(k, g);
  }
  const rank = { critical: 0, warning: 1, info: 2 };
  return [...groups.values()].sort((x, y) => (rank[x.severity] - rank[y.severity]) || (y.latest.ts - x.latest.ts));
}
function renderAlerts() {
  const groups = alertGroups();
  const count = $("#alert-count");
  if (count) { count.textContent = groups.length; count.classList.toggle("hot", groups.some((g) => g.severity === "critical")); }
  const ackAllBtn = $("#ack-all");
  if (ackAllBtn) {
    ackAllBtn.classList.toggle("hidden", groups.length === 0);
    ackAllBtn.onclick = async () => {
      await Promise.all(state.alerts.filter((a) => !a.acknowledged).map((a) => ackAlert(a.id)));
      renderAlerts(); renderStats();
    };
  }
  const box = $("#alerts");
  if (!box) return;
  const sig = groups.map((g) => `${g.key}:${g.ids.length}:${g.latest.id}`).join(",");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = "";
  if (!groups.length) { box.appendChild(el(`<div class="att-empty">Nothing needs attention. The floor is running normally.</div>`)); return; }
  for (const g of groups.slice(0, 30)) {
    const r = robotById(g.robotId);
    const who = r ? `${r.model} · ${r.id}` : g.robotId;
    const row = el(`
      <div class="att" title="${esc(g.latest.message || "")}">
        <span class="bar" style="background:${SEV_DOT[g.severity] || "#4A4F57"}"></span>
        <div class="att-main">
          <div class="font-medium">${esc(ALERT_TITLE[g.type] || g.type)}</div>
          <div class="att-meta">${esc(who)} · last ${new Date(g.latest.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</div>
        </div>
        ${g.ids.length > 1 ? `<span class="att-times">×${g.ids.length}</span>` : ""}
        <button class="panel-link self-start" data-act="clear" title="Mark as seen">Clear</button>
      </div>`);
    row.onclick = () => { if (r) selectRobot(r.id); };
    row.querySelector('[data-act="clear"]').onclick = async (e) => {
      e.stopPropagation();
      await Promise.all(g.ids.map(ackAlert));
      renderAlerts(); renderStats();
    };
    box.appendChild(row);
  }
}

// ── next actions (orchestrator Policy 4) ──────────────────────────────────────
const ACTION_LABEL = {
  resume: "Resume", handover: "Hand over task", precharge: "Pre-charge", reliability_review: "Review reliability",
  dispatch_task: "Dispatch task", assist: "Send a person",
};
function workerName(w) { return w.display_name || w.id; }
function assigneeText(a) {
  if (a.assignee_type === "person" && a.assignee_id) {
    const w = state.workers.find((x) => x.id === a.assignee_id);
    return `→ ${w ? workerName(w) : a.assignee_id}`;
  }
  return ASSIGNEE_LABEL[a.assignee_type] || "";
}
const ASSIGNEE_LABEL = { robot: "robot", person: "person", operator: "operator" };
async function loadActions() {
  try { state.actions = await getJSON("/api/dashboard/actions"); renderActions(); }
  catch (_) {}
}
async function resolveAction(id, verb) {
  try {
    const rec = await postJSON(`/api/dashboard/actions/${id}/${verb}`);
    toast(verb === "accept" ? `${ACTION_LABEL[rec.kind] || rec.kind}: ${rec.status}` : "Dismissed");
  } catch (e) {
    toast(e.status === 403 ? blockedMsg(e) : `Failed: ${e.detail}`, "warn");
  }
  await loadActions();
}
function renderActions() {
  const box = $("#actions");
  if (!box) return;
  const open = state.actions.filter((a) => a.status === "proposed");
  box.innerHTML = "";
  if (!open.length) return;
  box.appendChild(el(`<div class="rp-label px-3.5 pt-2.5">Suggested next steps</div>`));
  for (const a of open) {
    const blocked = !!a.blocked_reason;
    const acceptLabel = a.scope ? "Accept" : "Take it";
    const row = el(`
      <div class="px-3 py-2">
        <div class="flex items-center gap-2">
          <span class="mono text-[10.5px] px-1.5 py-0.5 rounded bg-surface-input text-brand shrink-0">${Math.round(a.score)}</span>
          <span class="text-[12px] font-semibold truncate">${esc(ACTION_LABEL[a.kind] || a.kind)}</span>
          <span class="text-[10.5px] text-ink-dim mono truncate">${esc(a.robot_id || "")}${a.target_robot_id ? " → " + esc(a.target_robot_id) : ""}</span>
          <span class="text-[10px] text-ink-dim ml-auto shrink-0">${esc(assigneeText(a))}${a.source === "agent" ? " · " + esc(a.actor || "agent") : ""}</span>
        </div>
        ${a.kind === "dispatch_task" && a.task ? `<div class="text-[11.5px] mt-1">Task: <span class="text-brand">${esc(a.task)}</span></div>` : ""}
        <div class="text-[11.5px] text-ink-mut leading-snug mt-1">${esc(a.rationale)}</div>
        <div class="flex items-center gap-3 mt-1.5">
          <button data-verb="accept" class="text-[11px] ${blocked ? "text-ink-dim cursor-not-allowed" : "text-brand hover:underline"}" ${blocked ? "disabled" : ""}
            title="${esc(blocked ? a.blocked_reason : "")}">${blocked ? "🔒 " : ""}${acceptLabel}</button>
          <button data-verb="dismiss" class="text-[11px] text-ink-dim hover:underline">Dismiss</button>
          ${blocked ? `<span class="text-[10px] text-amber-700 truncate">${esc(a.blocked_reason)}</span>` : ""}
        </div>
      </div>`);
    row.querySelectorAll("button[data-verb]").forEach((b) => {
      b.onclick = () => resolveAction(a.id, b.dataset.verb);
    });
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
      const envColor = env >= 0.5 ? "#DC2626" : env >= 0.48 ? "#D97706" : "#16A34A";
      tb.appendChild(el(`
        <tr>
          <td class="font-medium">${esc(v.vendor)}</td>
          <td class="mono text-ink-mut">${v.robots}</td>
          <td class="mono text-ink-mut">${Number(v.samples).toLocaleString()}</td>
          <td class="mono" style="color:#1D4ED8">${v.mean_drift_m.toFixed(3)}</td>
          <td class="mono" style="color:#1D4ED8">${v.p95_drift_m.toFixed(3)}</td>
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
const OEM_STATUS_COLOR = { active: "#16A34A", pending: "#D97706", suspended: "#DC2626" };
async function loadOEMs() {
  try {
    const oems = await getJSON("/api/dashboard/oems");
    state.oems = oems;
    renderControlPanel();
    const tb = $("#oems"); tb.innerHTML = "";
    if (!oems.length) {
      tb.appendChild(el(`<tr><td colspan="7" class="text-ink-dim">No OEM partners yet. Use <button class="text-azure hover:underline" style="background:none;border:none;cursor:pointer;padding:0" onclick="openWizard()">Onboard robot API</button> to register one.</td></tr>`));
      return;
    }
    for (const o of oems) {
      const statusColor = OEM_STATUS_COLOR[o.status] || "#4A4F57";
      const readiness = [
        o.control_ready ? `<span style="color:#D97706">control</span>` : `<span class="text-ink-dim">control</span>`,
        o.monitor_ready ? `<span style="color:#1D4ED8">monitor</span>` : `<span class="text-ink-dim">monitor</span>`,
      ].join(" · ");
      const row = el(`
        <tr>
          <td class="font-medium">${esc(o.company_name)}</td>
          <td class="text-ink-mut">${esc(o.vendor)}</td>
          <td><span class="transport-tag">${esc(o.transport)}</span></td>
          <td>${inlineStatus(o.status, statusColor)}</td>
          <td class="mono font-semibold" style="color:#16A34A">${o.granted_scopes.length} / ${o.ceiling_scopes.length}</td>
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
          <button id="oem-suspend" class="px-3 py-2 rounded-md text-[13px] ${suspended ? "bg-brand hover:bg-brand-600 text-[#FFFFFF]" : "bg-surface-input hover:bg-line-strong text-red-700"}">${suspended ? "Reactivate" : "Suspend access"}</button>
          <button id="oem-remove" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px] text-ink-dim hover:text-red-700" title="Off-board this partner">Remove</button>
        </div>
        <div class="flex gap-2">
          <button id="oem-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>
          <button id="oem-save" class="px-3 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#FFFFFF] text-[13px] font-semibold">Save scopes</button>
        </div>
      </div>
    </div>`;
  card.querySelector("#oem-cancel").onclick = closeModals;
  card.querySelector("#oem-remove").onclick = async () => {
    const btn = card.querySelector("#oem-remove");
    if (btn.dataset.confirm !== "1") { btn.dataset.confirm = "1"; btn.textContent = "Click again to confirm"; btn.classList.add("text-red-700"); return; }
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

function loadFleetSoon() { getJSON("/api/dashboard/fleet").then((f) => { state.robots = f.robots; state.workers = f.workers || state.workers; if (f.sequence) state.sequence = f.sequence; if (f.kpis) { state.kpis = f.kpis; renderValueBar(); } renderFleet(); renderControlPanel(); renderSequence(); }).catch(() => {}); }

// ── robot detail (business card) ───────────────────────────────────────────────
async function openRobot(id) {
  try {
    const r = await getJSON(`/api/dashboard/robot/${id}`);
    const label = STATE_LABEL[r.state] || r.state;
    const stateColor = STATE_COLOR[r.state] || "#4A4F57";
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
    ? `<button id="modal-action" class="${base} bg-brand hover:bg-brand-600 !text-[#FFFFFF]">Resume</button>`
    : `<button id="modal-action" class="${base} bg-red-600 hover:bg-red-500">E-Stop</button>`;
}

function controlNote(c) {
  if (!c || !c.managed) return "";
  const chip = (ok, label) =>
    `<span class="px-2 py-0.5 rounded-full text-[10.5px] ${ok ? "bg-brand/15 text-brand" : "bg-red-500/15 text-red-700"}">${ok ? "✓" : "✕"} ${label}</span>`;
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
function tempColor(t, warn, hot) { if (t == null) return "text-ink"; return t >= hot ? "text-red-700" : t >= warn ? "text-amber-700" : "text-brand"; }
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
        <button id="d-submit" class="px-3 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#FFFFFF] text-[13px] font-semibold">Dispatch</button>
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

// ── sign-in ─────────────────────────────────────────────────────────────────────
async function loadAuth() {
  try { state.auth = await getJSON("/api/auth/me"); } catch (_) { state.auth = null; }
  renderAuth();
}
function renderAuth() {
  const box = $("#auth-box");
  if (!box) return;
  const a = state.auth;
  box.innerHTML = "";
  if (a && a.signed_in) {
    const who = (a.actor || "").replace(/^console:/, "");
    box.appendChild(el(`<span class="text-[12px] font-medium" title="Signed in until ${new Date(a.expires_at * 1000).toLocaleString()}">${esc(who)} <span class="text-ink-dim">· ${esc(a.role)}</span></span>`));
    const out = el(`<button class="btn btn-ghost">Sign out</button>`);
    out.onclick = async () => { await postJSON("/api/auth/logout"); toast("Signed out — view only"); loadAuth(); };
    box.appendChild(out);
  } else {
    const btn = el(`<button id="btn-sign-in" class="btn btn-ghost">Sign in</button>`);
    btn.onclick = openSignIn;
    box.append(btn);
  }
  renderControlPanel(); renderFleet();
}
function openSignIn() {
  const card = $("#modal-sign-in .modal-card");
  const available = state.auth?.sign_in_available !== false;
  card.innerHTML = `
    <form class="p-5" autocomplete="on">
      <div class="text-[16px] font-bold">Sign in to the console</div>
      <p class="text-[12px] text-ink-dim mt-1">Use your operator or admin access key. Without one the console is view only.</p>
      ${available ? "" : `<p class="text-[12px] mt-3 px-3 py-2 rounded-md bg-amber-500/10 text-amber-700">Sign-in is not configured on this deployment yet. An admin must set <span class="mono">ORBITAL_RBAC_TOKENS</span>.</p>`}
      <label class="block text-[13px] mt-4 mb-1 text-ink-mut" for="si-name">Your name <span class="text-ink-dim">(shown in the audit log)</span></label>
      <input id="si-name" name="username" autocomplete="username" maxlength="40" class="w-full bg-surface-input border border-line rounded-md px-3 py-2 text-[13px]" placeholder="e.g. Ugo" />
      <label class="block text-[13px] mt-3 mb-1 text-ink-mut" for="si-key">Access key</label>
      <input id="si-key" name="password" type="password" autocomplete="current-password" required class="w-full bg-surface-input border border-line rounded-md px-3 py-2 text-[13px]" />
      <p id="si-error" class="text-[12px] text-red-700 mt-2 min-h-[1em]"></p>
      <div class="mt-3 flex gap-2 justify-end">
        <button type="button" id="si-cancel" class="px-3 py-2 rounded-md bg-surface-input hover:bg-line-strong text-[13px]">Cancel</button>
        <button type="submit" class="px-3 py-2 rounded-md bg-brand hover:bg-brand-600 text-[#FFFFFF] text-[13px] font-semibold">Sign in</button>
      </div>
    </form>`;
  card.querySelector("#si-cancel").onclick = closeModals;
  const nameInput = card.querySelector("#si-name");
  nameInput.value = localStorage.getItem("orbital.operatorName") || "";
  card.querySelector("form").onsubmit = async (ev) => {
    ev.preventDefault();
    const name = nameInput.value.trim();
    const key = card.querySelector("#si-key").value;
    try {
      const r = await postJSON("/api/auth/login", { key, name: name || null });
      if (name) localStorage.setItem("orbital.operatorName", name);
      closeModals();
      toast(`Signed in as ${r.role}`);
      loadAuth();
    } catch (e) {
      card.querySelector("#si-error").textContent = String(e.detail);
    }
  };
  $("#modal-sign-in").classList.remove("hidden");
  (nameInput.value ? card.querySelector("#si-key") : nameInput).focus();
}

// ── live connection ────────────────────────────────────────────────────────────
function setConn(ok) {
  state.connLive = ok;
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
    if (msg.type === "fleet") {
      noteLiveTick();
      state.robots = msg.robots; if (msg.workers) state.workers = msg.workers; if (msg.sequence) state.sequence = msg.sequence;
      if (msg.kpis) { state.kpis = msg.kpis; renderValueBar(); }
      onFleetUpdate();
    }
    else if (msg.type === "workers") { state.workers = msg.workers; renderMap(); }
    else if (msg.type === "alert") {
      state.alerts.unshift(msg.alert); state.alerts = state.alerts.slice(0, 200);
      renderAlerts(); renderStats();
      const box = $("#alerts").firstChild; if (box) box.classList.add("flash");
    }
    else if (msg.type === "actions") { state.actions = msg.actions; renderActions(); }
    else if (msg.type === "runs") { state.runs = msg.runs; renderRuns(); }
    else if (msg.type === "objects") {
      if (!state.facility || msg.facility_id === state.facility.id) {
        state.clockSkew = msg.now - Date.now() / 1000;
        state.objects = msg.objects; renderMap();
      }
    }
    else if (msg.type === "camera_occupancy") {
      if (!state.facility || msg.facility_id === state.facility.id) { state.cameraCells = msg.cells; renderMap(); }
    }
    else if (msg.type === "calibration") {
      if (!state.facility || msg.calibration.facility_id === state.facility.id) {
        state.calibration = msg.calibration; renderCalibration(); renderMap();
      }
    }
    else if (msg.type === "zones") { state.zones = msg.zones; renderMap(); if (!state.zoneDraft) renderZoneBar(); }
  };
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
    ? `<button id="wz-next" class="px-4 py-2 rounded-md bg-cta hover:bg-cta-600 text-[#FFFFFF] text-[13px] font-semibold">Continue</button>`
    : `<button id="wz-create" class="px-4 py-2 rounded-md bg-brand hover:bg-brand-600 text-[#FFFFFF] text-[13px] font-semibold">Create partner</button>`;

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
        <span class="w-9 h-9 rounded-full flex items-center justify-center glow-green" style="background:rgba(22,163,74,0.12);border:1px solid rgba(22,163,74,0.5);color:#16A34A">✓</span>
        <div>
          <div class="text-[16px] font-bold">${esc(profile.company_name)} onboarded</div>
          <div class="text-[11px] text-ink-dim">${esc(profile.oem_id)} · ${esc(profile.vendor)} · ${esc(profile.transport)} · ${readiness}</div>
        </div>
      </div>
      <div class="mt-4 rounded-lg border p-3" style="background:rgba(217,119,6,0.06);border-color:rgba(217,119,6,0.35)">
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
        <button id="wz-done" class="px-4 py-2 rounded-md bg-brand hover:bg-brand-600 text-[#FFFFFF] text-[13px] font-semibold">Done</button>
      </div>
    </div>`;
  const copy = $("#modal-wizard #wz-copy");
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(credential.api_key); copy.textContent = "Copied ✓"; }
    catch (_) { toast("Copy failed — select manually", "warn"); }
  };
  $("#modal-wizard #wz-done").onclick = () => { closeWizard(); showView("partners"); };
}

function closeWizard() { $("#modal-wizard").classList.add("hidden"); state.wizard = null; }

// ── embed mode (marketing site iframe) — warehouse map + live metrics only ─
function initEmbedMode() {
  if (!EMBED) return;
  document.documentElement.classList.add("embed-mode");
  document.body.classList.add("embed-mode");
}

// ── views: Live floor · Fleet · Runs · Partners (hash-routed, so links deep-link) ──
const VIEWS = ["live", "fleet", "runs", "partners"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "live";
  state.view = name;
  document.querySelectorAll("[data-view-panel]").forEach((p) => { p.hidden = p.dataset.viewPanel !== name; });
  document.querySelectorAll(".view-tab").forEach((t) => {
    const on = t.dataset.view === name;
    t.classList.toggle("on", on);
    if (on) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  });
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  if (name === "live") renderMap();
  if (name === "fleet") renderFleet();
}
function wireViews() {
  document.querySelectorAll(".view-tab").forEach((t) => {
    t.onclick = (e) => { e.preventDefault(); showView(t.dataset.view); window.scrollTo({ top: 0 }); };
  });
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  showView(location.hash.slice(1) || "live");

  const fsBtn = $("#btn-fullscreen"), mapPanel = $("#sec-map");
  if (!mapPanel.requestFullscreen) fsBtn.hidden = true;
  fsBtn.onclick = () => (document.fullscreenElement ? document.exitFullscreen() : mapPanel.requestFullscreen()).catch(() => {});
  document.addEventListener("fullscreenchange", () => {
    fsBtn.textContent = document.fullscreenElement ? "Exit full screen" : "Full screen";
    renderMap();
  });

  const legendBtn = $("#btn-legend"), legend = $("#map-legend");
  legendBtn.onclick = (e) => {
    e.stopPropagation();
    legend.hidden = !legend.hidden;
    legendBtn.setAttribute("aria-expanded", String(!legend.hidden));
  };
  document.addEventListener("click", (e) => {
    if (!legend.hidden && !legend.contains(e.target) && e.target !== legendBtn) {
      legend.hidden = true; legendBtn.setAttribute("aria-expanded", "false");
    }
  });
  const zonesBtn = $("#btn-zones"), drawer = $("#zone-bar");
  zonesBtn.onclick = () => {
    drawer.hidden = !drawer.hidden;
    zonesBtn.setAttribute("aria-expanded", String(!drawer.hidden));
    if (drawer.hidden && state.zoneDraft) cancelZoneDraft();
  };
  // Visitor demo stops count down in the robot panel.
  setInterval(() => { if (state.demoStops[state.selectedRobot]) renderControlPanel(); }, 1000);
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
  }
}

// ── boot ─────────────────────────────────────────────────────────────────────
async function init() {
  initEmbedMode();
  if (!EMBED) {
    $("#btn-dispatch").onclick = () => openDispatch();
    $("#btn-sign-in").onclick = openSignIn;
    loadAuth();
    const onboard2 = $("#btn-onboard-2"); if (onboard2) onboard2.onclick = () => openWizard();
    document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });
  }

  const fleet = await getJSON("/api/dashboard/fleet");
  state.facility = fleet.facility;
  state.industries = fleet.industries;
  state.vendors = fleet.vendors;
  state.robots = fleet.robots;
  if (fleet.kpis) state.kpis = fleet.kpis;
  state.workers = fleet.workers || [];
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
    renderValueBar();
    renderSequence();
    renderControlPanel();
  }

  wireMap();
  await loadMap();
  if (!EMBED) wireViews();

  if (!EMBED) {
    state.alerts = await getJSON("/api/dashboard/alerts");
    renderAlerts();
    await loadBenchmark();
    await loadCatalog();
    await loadOEMs();
    await loadActions();
    await loadRuns();
    await loadCalibration();
    setInterval(loadBenchmark, 5000);
    setInterval(loadOEMs, 8000);
  }
  connectWS();

  if (EMBED) {
    try {
      window.parent.postMessage({ type: "orbital-embed-ready" }, window.location.origin);
    } catch (_) { /* ignore */ }
  }
}

init().catch((e) => {
  console.error(e);
  const label = $("#conn-label");
  if (label) label.textContent = "error";
  if (EMBED) {
    try {
      window.parent.postMessage({ type: "orbital-embed-ready", error: String(e) }, window.location.origin);
    } catch (_) { /* ignore */ }
  }
});
