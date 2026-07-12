# Orbital AI — Cloud + Edge + Fleet Dashboard

The shared **monitor & control** platform of Orbital AI / ARIA, built to be consumed by
**StageGate first, then ReadyForRobots** — one platform, two clients.

This repo now scaffolds **all three runtime layers** of the platform behind clean
interfaces, with deterministic *simulated* backends so the full edge→cloud loop runs and
tests today — no cameras, no robots, no ROS 2 graph.

```
Robot Network (VLAN) ── ARIA Edge Node (aria_edge/) ──outbound──► Orbital AI Cloud (orbital_cloud/)
     robots              CV • TF-Hijack • Safety-Halt              REST + WS + Dashboard + Orchestrator
        ▲                        │                                          ▲
        └──── Fleet Adapters (fleet_adapters/) ────┘             StageGate ─┤  (embeds dashboard,
             Unitree/BD/Agility/… normalized                  ReadyForRobots┘   proxies the API)
```

### Packages

| Package | Layer | Modules |
|---|---|---|
| `orbital_cloud/` | Cloud | 6 Orchestration API, 7 Fleet Dashboard, Benchmark Library, **Orchestrator** (autonomy), **OEM onboarding** |
| `aria_edge/` | Edge (on-prem GPU) | 1 CV pipeline, 2 Micro-Waypoint Generator / TF-Hijack, 4 Safety-Halt Controller, `edge_agent` |
| `fleet_adapters/` | Edge/Cloud | 3 Fleet Adapters — one interface over ROS 2 / gRPC / cloud-REST OEMs |

## What's implemented

| Module | Status |
|---|---|
| **6 — Cloud Orchestration** | `/api/v1/missions`, `/trajectory`, `/map`, `POST /telemetry`, `POST /alerts` |
| **7 — Fleet Dashboard API** | `/api/dashboard/fleet`, `/robot/{id}`, `/tasks`, `/alerts`, `/benchmark[/{vendor}]` |
| **Monitor + Control** | `POST /robot/{id}/estop`, `/resume`, task dispatch, live WebSocket `/ws` |
| **Benchmark Library** | drift delta, MTBD, recovery latency, environmental degradation score |
| **Fleet Dashboard UI** | amber theme, industry tabs, robot business cards, live alerts, benchmark table |
| **Simulated ARIA edge** | pose/drift generation, degradation spikes, safety-halt E-Stops |
| **Orchestrator (autonomy)** | `/api/dashboard/orchestrator[/run]` — auto E-Stop, charge dispatch, review flags, optional LLM narrative |
| **ARIA Edge scaffold** | `aria_edge/` — CV → drift → safety-halt → TF-Hijack correction → cloud sync (`edge_agent`) |
| **Fleet Adapters** | `fleet_adapters/` — per-OEM capability ceilings (ROS 2 cmd_vel vs BD gRPC vs Agility cloud) |
| **OEM Onboarding** | `/api/oem/*` — 3rd-party robot companies register + unlock scoped access to their API |
| **Warehouse map + visual nav** | `GET /api/dashboard/map`, `POST /robot/{id}/navigate[/clear]` — click-to-set waypoints, SLAM-bypass steering |
| **Persistence (optional)** | `ORBITAL_DB_PATH` → SQLite; OEM grants + waypoints survive restarts (live motion stays ephemeral) |

## OEM onboarding (3rd-party robot companies)

Robot OEMs join Orbital AI and choose exactly which parts of their robot API to unlock for
us. Each grant is bounded by the OEM's fleet-adapter **capability ceiling**, so an OEM can
only grant control their protocol supports (e.g. Boston Dynamics can grant E-Stop + missions
but not a cmd_vel override).

```
API:  POST /api/oem/register        → { partner, credential.api_key }   # key shown once
      POST /api/oem/{id}/scopes      → unlock scopes (Bearer api_key)     # telemetry/control/…
      GET  /api/oem/{id}/profile     → monitor_ready / control_ready
      DELETE /api/oem/{id}/scopes    → revoke

Scopes: telemetry.read · state.read · control.velocity · control.estop ·
        control.teleop · mission.dispatch · camera.read · map.read
```

### Scope enforcement in the control path

Grants aren't advisory — Orbital enforces them everywhere it issues a command:

- **Cloud** — `POST /robot/{id}/estop`, `/resume`, and `POST /tasks` resolve the robot's
  vendor → owning OEM → granted scopes and return **403** if the matching scope
  (`control.estop`, `mission.dispatch`) isn't granted.
- **ARIA edge** — the edge agent checks `control.velocity` before a corrective command and
  `control.estop` before a physical halt; if ungranted it still raises the cloud alert but
  reports `correct_blocked` / `halt_blocked` instead of commanding. Edges hydrate grants via
  `GET /api/oem/grants/{vendor}`.
- **Unmanaged vendors** (no OEM registered, e.g. the seed demo fleet) are **permissive** by
  default so the demo works; set `ORBITAL_STRICT_OEM_SCOPES=1` to deny them too.

### Rich telemetry (sensor / motor / spatial / thermal input)

`POST /api/v1/telemetry` accepts an optional multi-modal payload alongside the required
`delta_meters`: `battery` (pct/temp/voltage/current/cycles), per-joint `motors`
(temp/current/torque/position/velocity), `imu` (accel/gyro), 6-DoF `spatial`
(x/y/z + roll/pitch/yaw + velocities), a `temperatures_c` map, and vendor `extra` scalars.
The latest snapshot surfaces on `RobotDetail` and at `GET /api/dashboard/robot/{id}/sensors`;
battery % and floor pose also feed the live runtime.

### Where it renders

Both operator surfaces now render the new data:

- **Standalone Fleet Dashboard** (`dashboard/`, served at the cloud root) — the robot detail
  modal shows a **Live vitals** panel (battery/motor temps, spatial pose, IMU, thermal) and
  scope-aware controls (E-Stop disables with a lock when the OEM hasn't granted it); a new
  **OEM Partners & API Scopes** table lists partners with a Manage dialog to grant/revoke
  scopes and suspend/reactivate access.
- **StageGate `/admin/orbital`** — the same vitals panel, scope grant chips, scope-aware
  controls, and an OEM governance table, proxied same-origin via `/api/orbital/*`.

Operator OEM management lives under `GET/POST /api/dashboard/oems*` (operator RBAC in prod);
partner self-service grant/revoke stays on `/api/oem/{id}/scopes` with the partner's key.

### Warehouse map + visual-control waypoints

The standalone dashboard renders an interactive **Global Spatial Map** of the warehouse floor
(`GET /api/dashboard/map`): storage racks, charge pads, dock, and every robot drawn at its
**camera-observed (ground-truth) pose** with a faint self-report (SLAM) ghost + drift link.

Click a robot to select it, then click the floor to drop a waypoint — Orbital drives the robot
there via **visual control** (the overhead camera rig localizes + steers the external pose),
deliberately **bypassing the robot's onboard SLAM**, so odometric drift can't send it off course.
Shift-click appends waypoints to build a multi-stop route. This posts to
`POST /api/dashboard/robot/{id}/navigate` and is **gated on `control.velocity`** — robots whose
OEM hasn't granted velocity control return 403 (surfaced as a toast). `…/navigate/clear` cancels.

### Persistence

By default the service is fully in-memory (great for local dev + tests). Set `ORBITAL_DB_PATH`
(e.g. `/data/orbital.db` on a Fly volume) to persist the state operators actually change —
**OEM registrations/grants** and **per-robot waypoints** — so they survive restarts and redeploys.
Uses only stdlib `sqlite3`; live fleet motion is intentionally not persisted (it re-seeds).

Zero-dependency onboarding client for OEMs:

```bash
python3 scripts/oem_onboard.py register --url https://orbital.onstage.bot \
    --company "Acme Robotics" --vendor "Unitree" --email ops@acme.com --transport ros2
python3 scripts/oem_onboard.py unlock  --url ... --oem-id oem-xxxx --api-key orb_xxx \
    --scopes telemetry.read,control.velocity,control.estop
python3 scripts/oem_onboard.py profile --url ... --oem-id oem-xxxx
```

## Run it

```bash
cd Orbital_AI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn orbital_cloud.main:app --reload --port 8090
```

Open **http://localhost:8090** for the live Fleet Management Dashboard.

## Test

```bash
pytest -q
```

## Integrating the two apps (next step)

Both apps point at the same Orbital AI Cloud:

- **StageGate (TypeScript):** call the REST API / embed the dashboard; add an `Orbital`
  section that proxies `ORBITAL_AI_CLOUD_URL`.
- **ReadyForRobots (Python/FastAPI):** same API; can reuse the `orbital_cloud.models`
  Pydantic schema directly as a client contract.

The API is the shared contract — neither app reimplements fleet/telemetry/benchmark logic.

## Architecture notes

- **Latency:** real waypoint injection runs at 10Hz *on the edge* (sub-10ms). The cloud is
  for orchestration, telemetry, benchmarking, and the operator dashboard — never in the
  real-time control path.
- **Security (future):** edge↔cloud is outbound-only mTLS; robot subnet is VLAN-isolated;
  ROS 2 traffic uses SROS2. Not in the v0 software demo.
- **Storage (future):** swap the in-memory `Store` for Postgres (fleet/tasks) + InfluxDB
  (telemetry time-series) behind the same methods.
