# Orbital AI — Cloud + Fleet Dashboard

The shared **monitor & control** layer of the Orbital AI / ARIA platform, built to be
consumed by **StageGate first, then ReadyForRobots** — one service, two clients.

This repo is the *software* half of the platform (Modules 6 & 7 + the Benchmark
Library). The patent-core **ARIA Edge Node** (CV pipeline, TF Hijack, fleet adapters,
safety-halt controller) is hardware/ROS 2-bound and lives in a separate `aria-core`
repo on the lab bench. A built-in **simulated edge** stands in for that hardware so the
entire stack is demoable today — no cameras, no robots.

```
StageGate (TS)  ─┐
                 ├─►  Orbital AI Cloud (this repo, FastAPI)  ◄── ARIA Edge Node(s)
ReadyForRobots ─┘        REST + WebSocket + Fleet Dashboard        (real, or simulated)
```

## What's implemented

| Module | Status |
|---|---|
| **6 — Cloud Orchestration** | `/api/v1/missions`, `/trajectory`, `/map`, `POST /telemetry`, `POST /alerts` |
| **7 — Fleet Dashboard API** | `/api/dashboard/fleet`, `/robot/{id}`, `/tasks`, `/alerts`, `/benchmark[/{vendor}]` |
| **Monitor + Control** | `POST /robot/{id}/estop`, `/resume`, task dispatch, live WebSocket `/ws` |
| **Benchmark Library** | drift delta, MTBD, recovery latency, environmental degradation score |
| **Fleet Dashboard UI** | amber theme, industry tabs, robot business cards, live alerts, benchmark table |
| **Simulated ARIA edge** | pose/drift generation, degradation spikes, safety-halt E-Stops |

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
