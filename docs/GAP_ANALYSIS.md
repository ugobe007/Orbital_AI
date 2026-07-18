# Gap Analysis — Desktop Orbital_AI vs Build Guide

**Date:** 2026-07-18  
**Guide:** [ARIA_ORBITAL_AI_BUILD_GUIDE.md](./ARIA_ORBITAL_AI_BUILD_GUIDE.md)  
**Repo:** Desktop `Orbital_AI` (simulation-first control abstraction)  
**Lens:** Testable abstraction without hardware — not full pilot readiness.

## Module status

| Module | Guide focus | Status | Primary locations |
|--------|-------------|--------|-------------------|
| 1 CV Pipeline | ArUco/YOLO → `P_external` | Scaffold | `aria_edge/cv_pipeline.py` (sim only) |
| 2 TF Hijack | 10 Hz `T_delta` + inject waypoint | Partial | `aria_edge/waypoint_generator.py`, `edge_agent.py` (P-controller ≠ guide math) |
| 3 Fleet Adapters | Per-OEM SDK + `inject_waypoint` | Partial | `fleet_adapters/*` (sim + capability ceilings) |
| 4 Safety Halt | Independent 20 Hz watchdog | Partial | `aria_edge/safety_halt.py` (logic OK; same process as edge tick) |
| 5 Benchmark | Drift / MTBD / recovery / env | Done (sim) | `orbital_cloud/store.py`, `benchmark.py` (no InfluxDB) |
| 6 Cloud Orchestration | Missions / trajectory / map / telemetry | Done | `orbital_cloud/routers/edge.py` (+ orchestrator, OEM) |
| 7 Dashboard | Fleet API + amber UI + RBAC | Done API / Partial UI | `routers/dashboard.py`, `dashboard/` (no RBAC/CRM outreach) |
| Cybersecurity | VLAN / mTLS / SROS2 | Missing | Comments only; OEM scopes ≠ transport security |

## What already works for testing

- Edge ↔ cloud telemetry + alerts contract
- Multi-vendor capability ceilings + OEM scope enforcement
- Benchmark math with pytest coverage
- Dashboard + live WebSocket fleet UI
- CI (pytest, CSS build, site smoke) + Fly deploy on `main`

## Highest-impact gaps (abstraction, still no hardware)

1. Module 2 algorithm fidelity (`T_delta`, trajectory lookahead, `inject_waypoint`)
2. Adapter API parity with the guide + per-vendor protocol stubs
3. Edge **pull** of missions/trajectories each tick
4. Safety halt as an isolated process on a shared pose bus
5. Occupancy map stub → real `/api/v1/map` payload (even synthetic)
6. Optional mTLS on edge↔cloud for test harness
7. Dashboard RBAC (Admin / Operator / Viewer)

## Guide sprint items vs sim

| Sprint | Mostly sim-satisfied | Still open |
|--------|----------------------|------------|
| S1 Core loop | Sim CV, sim Unitree adapter, edge tick @ 10 Hz config | Lab hardware, ArUco, true TF hijack, TF publisher, latency harness, isolated safety process |
| S2 Multi-robot & benchmark | MTBD/report, FastAPI cloud, trajectory API endpoints | Open-RMF, InfluxDB, edge trajectory consumer, real AgiBot SDK |
| S3 BD & dashboard | Dashboard API, sim BD ceilings | Real bosdyn gRPC, SROS2/mTLS, security audit, RBAC |
| S4 Pilot | Sim Fourier/Deep/MagicLab/Agility registry | Real SDKs, YOLO B, hardware pilot, live OEM licensing data |

## Structural note

Guide assumes `aria-core/aria/...` + ROS Humble. Desktop uses flat `aria_edge/` + `fleet_adapters/` + `orbital_cloud/` with **no** ROS/CUDA in `requirements.txt` by design. Hardware sprints add those deps; abstraction sprints should not wait on them.
