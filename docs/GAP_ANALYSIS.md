# Gap Analysis — Desktop Orbital_AI vs Build Guide

**Date:** 2026-07-18  
**Guide:** [ARIA_ORBITAL_AI_BUILD_GUIDE.md](./ARIA_ORBITAL_AI_BUILD_GUIDE.md)  
**Repo:** Desktop `Orbital_AI` (simulation-first control abstraction)  
**Lens:** Testable abstraction without hardware — not full pilot readiness.

## Module status

| Module | Guide focus | Status | Primary locations |
|--------|-------------|--------|-------------------|
| 1 CV Pipeline | ArUco/YOLO → `P_external` | Scaffold | `aria_edge/cv_pipeline.py` (sim only) |
| 2 TF Hijack | 10 Hz `T_delta` + inject waypoint | Done (sim) | `waypoint_generator.py` + `tf_publisher.py` (map→odom record @ 30 Hz) |
| 3 Fleet Adapters | Per-OEM SDK + `inject_waypoint` | Partial | Guide API + protocol contracts + fake BD/Arc servers; real SDKs still stub |
| 4 Safety Halt | Independent 20 Hz watchdog | Partial | `pose_bus.py` + `SafetyWatchdog` (thread/bus isolation); not yet a separate OS process |
| 5 Benchmark | Drift / MTBD / recovery / env | Done (sim) | `TelemetryStore` (memory ± optional Influx dual-write); benchmark API unchanged |
| 6 Cloud Orchestration | Missions / trajectory / map / telemetry | Done | Synthetic occupancy on `/api/v1/map`; edge pull_map |
| 7 Dashboard | Fleet API + amber UI + RBAC | Done API / Partial UI | RBAC Admin/Operator/Viewer when `ORBITAL_RBAC_ENFORCE=1` |
| Cybersecurity | VLAN / mTLS / SROS2 | Partial | Edge mTLS client + `scripts/gen_mtls_certs.sh`; VLAN/SROS2 still open |

## What already works for testing

- Edge ↔ cloud telemetry + alerts contract
- Multi-vendor capability ceilings + OEM scope enforcement
- Benchmark math with pytest coverage
- Dashboard + live WebSocket fleet UI
- CI (pytest, CSS build, site smoke) + Fly deploy on `main`

## Highest-impact gaps (abstraction, still no hardware)

1. ~~Module 2 algorithm fidelity~~ — Done (sim); see Sprint A1
2. ~~Per-vendor protocol stubs~~ — Done (Sprint B); real SDK bind still open
3. ~~Edge pull of missions/trajectories~~ — Done (cache + HTTP when enabled)
4. Safety halt as a **separate OS process** (bus exists; process spawn still open)
5. ~~Occupancy map~~ — Done (synthetic grid, Sprint C2)
6. ~~Optional mTLS harness~~ — Done (client + cert script; prod VLAN still open)
7. ~~Dashboard RBAC~~ — Done (enforce via `ORBITAL_RBAC_ENFORCE=1`)
8. Real OEM SDK binds (hardware — Sprint D)

## Guide sprint items vs sim

| Sprint | Mostly sim-satisfied | Still open |
|--------|----------------------|------------|
| S1 Core loop | Sim CV, sim Unitree adapter, edge tick @ 10 Hz config | Lab hardware, ArUco, true TF hijack, TF publisher, latency harness, isolated safety process |
| S2 Multi-robot & benchmark | MTBD/report, FastAPI cloud, trajectory API endpoints | Open-RMF, InfluxDB, edge trajectory consumer, real AgiBot SDK |
| S3 BD & dashboard | Dashboard API, sim BD, fake Arc/BD, RBAC, mTLS client | Real bosdyn gRPC, SROS2, security audit |
| S4 Pilot | Sim Fourier/Deep/MagicLab/Agility registry | Real SDKs, YOLO B, hardware pilot, live OEM licensing data |

## Structural note

Guide assumes `aria-core/aria/...` + ROS Humble. Desktop uses flat `aria_edge/` + `fleet_adapters/` + `orbital_cloud/` with **no** ROS/CUDA in `requirements.txt` by design. Hardware sprints add those deps; abstraction sprints should not wait on them.
