# Sprint Prioritization — Abstraction-first

**Principle:** Orbital AI is the robot-control abstraction. Ship testable interfaces and
sim backends first; gate hardware (cameras, ROS SDKs, VLAN) behind Sprint D.

**Source:** [GAP_ANALYSIS.md](./GAP_ANALYSIS.md) · [ARIA_ORBITAL_AI_BUILD_GUIDE.md](./ARIA_ORBITAL_AI_BUILD_GUIDE.md)

---

## Sprint A — Faithful core loop (sim)

**Goal:** Guide-correct ARIA inject path, still 100% simulated.

| ID | Work | Exit criteria | Status |
|----|------|---------------|--------|
| A1 | Reimplement Module 2: `T_delta`, trajectory lookahead, `inject_waypoint` | Golden unit tests for transform + inject | **Done** (`tests/test_tf_hijack.py`) |
| A2 | Align `FleetAdapter` with guide (`inject_waypoint`, `get_internal_pose`, `trigger_estop`) | Keep capability ceilings; adapters still sim | **Done** |
| A3 | Edge `CloudSync` **pulls** missions + trajectories each tick | Edge uses cloud trajectory, not only local error | **Done** (seed/cache + HTTP when enabled) |
| A4 | Split safety into independent process + shared pose bus | Halt still fires if correct path is wedged | **Partial** — `PoseBus` + `SafetyWatchdog`; OS process spawn deferred |

**Maps to guide:** S1-04, S1-05 (interface), S1-07, S2-06 (consumer side)

---

## Sprint B — Adapter protocol stubs

**Goal:** Per-vendor contracts without real robot SDKs.

| ID | Work | Exit criteria |
|----|------|---------------|
| B1 | Per-vendor modules with recorded topics / REST / gRPC method names | Registry still sim-backed |
| B2 | Fake BD + Agility servers for integration tests | pytest exercises protocol shapes |
| B3 | Simulated `TFPublisher` recording `map→odom` | Tests assert TF stream existence/rate |

**Maps to guide:** S1-06 (sim), S2-01/S3-01/S4-01/S4-02 (stubs)

---

## Sprint C — Persistence, map, security scaffolding, RBAC

**Goal:** Production-shaped seams without hardware.

| ID | Work | Exit criteria |
|----|------|---------------|
| C1 | `TelemetryStore` interface (memory ↔ optional Influx) | Benchmark API unchanged |
| C2 | Synthetic occupancy payload for `GET /api/v1/map/{facility}` | Edge/dashboard can consume map |
| C3 | Optional mTLS on edge↔cloud in local/docker test | Certs in `docs/` or `scripts/` only |
| C4 | Dashboard RBAC: Admin / Operator / Viewer | Routes enforce roles |

**Maps to guide:** S2-03, Module 6 map, S3-03/S3-04 (scaffold), Module 7 RBAC

---

## Sprint D — Hardware gate (after A–B)

**Goal:** Only after abstraction is faithful.

| ID | Work |
|----|------|
| D1 | ArUco on recorded frames → same `PoseSource` interface |
| D2 | Real Unitree adapter + 10 Hz latency bench (S1-08) |
| D3 | Lab VLAN / mTLS / SROS2 ops + external audit |

**Do not start D until A1–A4 pass CI.**

---

## Explicitly defer

- Open-RMF traffic scheduler (S2-02)
- YOLOv8 Strategy B (S4-03)
- Pilot site hardware (S4-04)
- AWS migration (Fly is fine for abstraction testing)
- StageGate product features (CRM outreach, tradeshow) — client repo

---

## Working agreement

1. Guides and plans live in `docs/` (this repo).
2. Implement Sprint A items as PRs; CI must stay green.
3. Update `GAP_ANALYSIS.md` status column when a module moves Done/Partial.
4. StageGate consumes Orbital via API — no StageGate deploy tokens or Railway config in this repo.
