# Roadmap — Physical AI Harness (Sprints E–I)

**Date:** 2026-10-24
**Input:** Competitive review of [Auki](https://www.auki.com/) (Cactus retail copilot, posemesh domains)
**Builds on:** [SPRINT_PRIORITIZATION.md](./SPRINT_PRIORITIZATION.md) (Sprints A–D)

## Positioning

Auki sells a "physical AI harness": a live, shared model of a site (cameras + phones + robots)
that frontier models can read and act on. Orbital already has the hard parts — camera-observed
ground-truth poses, a deterministic orchestrator, and per-OEM scope enforcement on every control
path. These sprints turn that into a product story Auki can't match:

> **Any AI model can run a mixed robot + human fleet, but only within the permissions the OEM
> granted and the zones the operator drew.**

## Principles (unchanged from A–D, plus two)

1. Sim-first. Every item ships with a simulated backend and pytest coverage; no hardware gate.
2. The deterministic policy is the only thing that auto-executes. LLMs and external agents
   **propose**; the policy or an operator **disposes**.
3. Every agent-issued command goes through the same `scope_guard` + RBAC checks as the
   dashboard. No side doors.
4. Every agent-issued command is attributable (actor recorded on the decision / audit log).

## Sequence

```
Sprint E  →  Sprint F  →  Sprint G  →  Sprint H  →  Sprint I
Next-action   Model        Zones &      Rig           Go-to-market
queue         harness      people       calibration   (demo, metrics, on-prem, history)
              (MCP)
```

E comes first because it gives agents (Sprint F) a safe write path: propose an action into the
queue rather than drive robots directly. G is what makes direct agent control defensible.
H is independent and can run in parallel with E–G; it feeds the lab cutover.

---

## Sprint E — Next-best-action queue

**Goal:** The orchestrator stops being only a watchdog and becomes a copilot: a ranked list of
"what should happen next", each with a value score, a rationale, and a suggested assignee.

| ID | Work | Files | Exit criteria |
|----|------|-------|---------------|
| E1 | `Recommendation` model: `id, kind, robot_id?, task?, score, rationale, assignee_type (robot\|person\|operator), status (proposed\|accepted\|dismissed\|done\|expired), source (policy\|agent), actor, created_at, expires_at` | `orbital_cloud/models.py` | Schema + round-trip test |
| E2 | `RecommendationQueue` in the store: add / rank / accept / dismiss / expire, dedupe on `(kind, robot_id)`, bounded size | `orbital_cloud/store.py` (or new `recommendations.py`) | Unit tests for ranking, dedupe, expiry |
| E3 | Orchestrator Policy 4: deterministic scoring that emits recommendations each tick — resume a halted robot once drift re-converges, rebalance idle robots onto queued tasks, pre-emptive charge before a long mission, review a robot whose MTBD is falling. Existing Policies 1–3 (auto E-Stop, charge, review) keep auto-executing as today. | `orbital_cloud/orchestrator.py` | `tests/test_orchestrator.py` cases per rule; Policies 1–3 behaviour unchanged |
| E4 | API: `GET /api/dashboard/actions`, `POST /actions/{id}/accept` (Operator; executes via existing store methods **after** `scope_guard.check_robot`, 403 on denial), `POST /actions/{id}/dismiss` | `orbital_cloud/routers/dashboard.py` | API tests incl. scope-denied accept |
| E5 | WebSocket broadcast `{"type":"actions"}` on change | `orbital_cloud/events.py`, orchestrator loop | Test client receives update |
| E6 | Dashboard "Next actions" panel: ranked cards, score, rationale, Accept / Dismiss, lock icon when scope missing | `dashboard/app.js`, `dashboard/index.html` | Manual check + site smoke still green |
| E7 | LLM narrative includes the top 3 recommendations (still advisory, still fail-open) | `orbital_cloud/orchestrator.py` | Narrative test with LLM disabled unchanged |

---

## Sprint F — Model harness (MCP server)

**Goal:** Any MCP-capable model (Claude, GPT, Cursor, a customer's own agent) can see the site and
act on it, bounded by RBAC, OEM scopes and the action queue.

**Design:** a separate `orbital_mcp/` package that is a thin client over the existing REST API
with a bearer token. It inherits RBAC and scope enforcement for free and keeps `orbital_cloud`
dependency-light (the `mcp` SDK goes in a new `requirements-mcp.txt`, not `requirements.txt`).

| ID | Work | Files | Exit criteria |
|----|------|-------|---------------|
| F1 | Package skeleton: stdio transport (desktop agents) + streamable-HTTP transport (hosted agents); config `ORBITAL_URL`, `ORBITAL_AGENT_TOKEN` | `orbital_mcp/server.py`, `requirements-mcp.txt` | Server lists tools in an MCP inspector |
| F2 | Read tools: `fleet_summary`, `list_robots`, `get_robot` (incl. sensors + control grants), `get_map`, `list_alerts`, `list_actions`, `benchmark` | `orbital_mcp/tools.py` | Tests against `TestClient`-backed cloud |
| F3 | Propose tool: `propose_action(kind, robot_id, rationale)` → creates a `source=agent` recommendation; operator accepts in the dashboard | cloud: `POST /api/dashboard/actions` (Operator) | Agent proposal appears in panel, attributed |
| F4 | Direct-control tools behind `ORBITAL_AGENT_DIRECT_CONTROL=1`: `dispatch_task`, `navigate_robot`, `set_speed`, `resume_robot`. `estop_robot` is **always** available (stopping is never gated). Default off. | `orbital_mcp/tools.py` | Scope-denied calls return the 403 reason to the model verbatim |
| F5 | Actor attribution: `X-Orbital-Actor: agent:<name>` header recorded on decisions, tasks and the OEM call audit | `orbital_cloud/rbac.py`, `store.py`, routers | Audit rows show the agent actor |
| F6 | Agent-specific rate limit + dry-run mode (`dry_run=true` returns what would happen) | cloud routers | Tests for limit + dry-run |
| F7 | README section "Connect your model" with Cursor / Claude Desktop config snippets | `README.md` | — |

**Not in scope:** Orbital hosting its own agent loop. Customers bring the model; Orbital is the harness.

---

## Sprint G — Zones and people on the map

**Goal:** The map becomes a site model, not just a floor plan: operator-drawn zones the planner
and safety layer respect, and human workers as first-class participants.

| ID | Work | Files | Exit criteria |
|----|------|-------|---------------|
| G1 | `Zone` model: polygon, `type (keepout\|slow\|drivable)`, `speed_cap_mps?`, `vendors?` (zone applies to these OEMs only). CRUD at `/api/dashboard/zones` (Operator), persisted in SQLite | `models.py`, `persistence.py`, `routers/dashboard.py` | CRUD + persistence tests |
| G2 | Planner honours keep-out zones as blocked cells; simulator caps speed in slow zones | `pathing.py` (`_point_blocked`), `simulator.py` | `tests/test_pathing.py`: route avoids keep-out |
| G3 | `OEMPolicies.geofence` resolves to zone ids instead of a free-text label | `models.py`, `oem_store.py` | Migration keeps `"facility"` = whole map |
| G4 | Edge enforcement: new `AlertType.GEOFENCE_BREACH`; `SafetyHaltController` halts a robot whose camera pose enters a keep-out zone. Zones ship to the edge in `GET /api/v1/map/{facility}` | `aria_edge/safety_halt.py`, `aria_edge/edge_agent.py`, `routers/edge.py` | Edge test: breach → halt + alert |
| G5 | `Worker` model (`id, display_name, role, pose, status, last_seen`), sim moves 2–3 workers; `POST /api/v1/workers/{id}/pose` for phone check-ins | `models.py`, `store.py`, `simulator.py`, `routers/edge.py` | Sim + API tests |
| G6 | Human-proximity safety: a robot within `ORBITAL_HUMAN_SLOW_RADIUS_M` of a worker is speed-capped; within `…_STOP_RADIUS_M`, it halts and raises `HUMAN_PROXIMITY` | `simulator.py`, `aria_edge/safety_halt.py` | Tests for both radii |
| G7 | Tasks / recommendations can be assigned to a worker (`assignee_type=person`) | `models.py`, `store.py` | Queue test with mixed assignees |
| G8 | Dashboard: draw / edit zones (polygon tool), workers rendered on the map, zone overlay | `dashboard/app.js` | Manual check |

**Privacy:** worker location is opt-in, pseudonymous by default (`display_name` optional), and
not persisted beyond the live session unless `ORBITAL_WORKER_HISTORY=1`.

---

## Sprint H — Camera-rig calibration with residuals

**Goal:** A guided setup for the overhead camera rig that tells the installer *which* marker is
wrong and by how much, so the lab cutover takes hours not days.

| ID | Work | Files | Exit criteria |
|----|------|-------|---------------|
| H1 | Multi-capture marker-map solver: given captures that each observe ≥2 markers, solve each marker's global `(x, y, θ)` anchored on an origin marker (2-D pose-graph, stdlib iterative least squares — no numpy in CI) | `aria_edge/calibration.py` | Recovers synthetic ground truth within 1 cm |
| H2 | Residual report: per-marker, per-capture error; flag "likely error" markers above `ORBITAL_CALIB_RESIDUAL_M`; detect disconnected capture groups (no shared markers) | `aria_edge/calibration.py` | Test injects 15 cm error on one marker → flagged; split groups → warning |
| H3 | Recorded fixtures: synthetic multi-capture sets alongside `testdata/aruco/` | `testdata/calibration/` | Used by H1/H2 tests |
| H4 | CLI `scripts/calibrate_rig.py` (frames dir → marker map JSON + report); edge uploads report to `POST /api/v1/calibration/{facility}` | `scripts/`, `routers/edge.py` | CLI test on fixtures |
| H5 | Dashboard "Calibration" panel: markers on the map, residual heat, likely-error list, "re-capture this area" hint | `dashboard/app.js` | Manual check |
| H6 | Add calibration pass as a row in `HARDWARE_GATE.md` and `LAB_CUTOVER_DAY1.md` | `docs/` | — |

---

## Sprint I — Go-to-market

| ID | Work | Files | Exit criteria |
|----|------|-------|---------------|
| I1 | Public read-only live demo embedded on the marketing site (anon Viewer is already allowed on Fly); control buttons render disabled with a "request a pilot" CTA | marketing site source, `dashboard/` | Embed loads in `smoke_site.py` |
| I2 | Headline metrics strip on the homepage fed by `GET /api/dashboard/benchmark` (drift reduction, MTBD, recovery latency) — clearly labelled "simulation" until lab numbers exist | marketing site source | — |
| I3 | "Start in a day" page: existing cameras + printed markers → monitoring on day one; robots and control scopes later | marketing site source | — |
| I4 | On-prem package: `docker-compose.yml` (cloud + SQLite volume), `ORBITAL_DEPLOY_MODE=onprem` disables all outbound calls unless configured | `Dockerfile`, new compose file, `config.py` | Compose up → tests pass in container |
| I5 | Provider-agnostic LLM narrative: OpenAI-compatible `ORBITAL_LLM_BASE_URL` (covers Anthropic gateways, local vLLM/Ollama) instead of hard-coded OpenAI | `orchestrator.py`, `config.py` | Narrative test with mock base URL |
| I6 | Site history: hourly snapshots of benchmark rollups + zone layout; `GET /api/dashboard/history?from=&to=`; compare two periods or two vendors in the benchmark tab | `persistence.py`, `telemetry_store.py`, `dashboard/app.js` | Snapshot + query tests |

---

## Dependencies and open decisions

| # | Decision / blocker | Needed by | Recommendation |
|---|---|---|---|
| 1 | Marketing site source is not in this repo (`site/` holds only the built bundle) | I1–I3 | Locate the source repo, or rebuild the homepage in-repo |
| 2 | Default for agent direct control | F4 | Off; enable per deployment after zones (G) are live |
| 3 | MCP transport for hosted use (stdio only vs also HTTP on Fly) | F1 | Both; HTTP behind Operator token |
| 4 | Worker-location consent and retention policy | G5–G6 | Opt-in, pseudonymous, no history by default |
| 5 | Whether human-proximity halt runs on the edge only or also in the cloud sim | G6 | Edge is authoritative; cloud mirrors for the demo |

## Explicitly not adopting

- Auki's decentralised network, token, and staking model — no payoff for operator/OEM customers.
- Building Orbital's own autonomous agent loop — Orbital is the harness; customers bring the model.

## Working agreement

Same as Sprints A–D: one PR per sprint item (or tight group), CI green, update the status column
here and in `GAP_ANALYSIS.md` as items land.
