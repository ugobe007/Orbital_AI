# Orbital AI — Phases & Completed Work

**Audience:** Business partner review  
**Date:** 2026-07-18  
**Product:** Orbital AI — robot-control abstraction (cloud + ARIA edge + fleet adapters)  
**Repo / deploy:** [ugobe007/Orbital_AI](https://github.com/ugobe007/Orbital_AI) · Fly app `orbital-ai` · https://orbital-ai.io  
**Latest engineering commit (OEM + security):** `3942519`

> **What Orbital is:** A vendor-neutral control and monitoring layer so facilities can run mixed robot fleets (Unitree, Boston Dynamics Spot, Agility Digit, AgiBot, Deep Robotics, Fourier, MagicLab) through one API and dashboard.  
> **What it is not:** StageGate (separate client product). Client apps consume Orbital over API; they do not share deploy secrets.

---

## Executive summary

| Area | Status |
|------|--------|
| Software abstraction (sim-first) | **Complete** — Sprints A–C done; CI green; live on Fly |
| OEM API research & adapter wiring | **Complete (code)** — 7 vendors, 27 endpoints, all adapters wired |
| Lab / hardware cutover | **Not started on-site** — checklists ready; needs cameras, robots, VLANs |
| Production security lockdown | **Code ready; flags off on Fly** — turn on RBAC + OEM scopes when ready |
| Pilot site | **Deferred** until hardware gate LAB rows are signed |

**Bottom line for partners:** The control abstraction is built and demoable in simulation. The next investment is lab hardware + network security, not more core software scaffolding.

---

## How we sequenced the work

We followed an **abstraction-first** plan: prove the control loop and OEM interfaces in software before buying/binding live robots.

```
Sprint A  →  Sprint B  →  Sprint C  →  Sprint D (soft)  →  Lab hardware  →  Pilot
 Core loop    Protocols    Cloud/RBAC    CI gates           On-site gear     Live site
```

Security hardening is organized as **Phases 1–6** (network → transport → identity → authorization → runtime → governance). Software for several phases exists; VLAN/SROS2/audit still require the lab.

---

## Phase / sprint status

### Sprint A — Faithful core control loop (simulation)

**Goal:** Correct ARIA inject path (transform → waypoint → robot) without hardware.

| Item | Status |
|------|--------|
| Module 2: transform (`T_delta`), trajectory lookahead, waypoint inject | **Done** |
| Fleet adapter guide API (`inject_waypoint`, `get_internal_pose`, `trigger_estop`) | **Done** |
| Edge pulls missions / trajectories from cloud | **Done** |
| Safety isolation (`PoseBus` + `SafetyWatchdog`) | **Partial** — thread/bus isolation done; separate OS process deferred |

---

### Sprint B — Per-vendor protocol contracts

**Goal:** Document and test each OEM’s control surface without requiring real SDKs.

| Item | Status |
|------|--------|
| Protocol contracts (ROS 2 / gRPC / REST / UDP / LCM names) | **Done** |
| Fake Boston Dynamics + Agility servers for tests | **Done** |
| Simulated TF publisher (`map→odom` ~30 Hz) | **Done** |

---

### Sprint C — Cloud production seams

**Goal:** Persistence, map, security scaffolding, dashboard roles — still without robots.

| Item | Status |
|------|--------|
| Telemetry store (memory ± optional Influx) | **Done** |
| Facility occupancy map API (synthetic grid) | **Done** |
| Edge ↔ cloud mTLS client + cert generation script | **Done** (scaffold) |
| Dashboard RBAC (Admin / Operator / Viewer) | **Done** (opt-in via env flag) |

---

### Sprint D — Hardware gate (soft vs lab)

**Goal:** Soft pieces pass in CI; live gear is a separate on-site checklist.

| Item | Soft (in repo / CI) | Lab (on-site) |
|------|---------------------|---------------|
| ArUco pose on recorded frames | **Done** | Live cameras / markers ☐ |
| Unitree adapter + 10 Hz latency bench | **Done** (sim) | Real Unitree + `rclpy` ☐ |
| VLAN / mTLS / SROS2 / external audit runbook | **Docs + scripts** | Network + audit ☐ |

**Do not run a paid pilot until LAB rows in [HARDWARE_GATE.md](./HARDWARE_GATE.md) are checked.**

---

### OEM API program (cross-cutting)

| Deliverable | Status |
|-------------|--------|
| Public API research for 7 OEMs | **Done** — [OEM_API_CONNECTIONS.md](./OEM_API_CONNECTIONS.md) |
| Dry-run clients (27 endpoints) | **Done** — `fleet_adapters/oem_apis/` |
| Adapters wired so `get_adapter(vendor)` owns `oem_api` | **Done** — 7/7 |
| Env / Fly secret credential loader | **Done** — `fleet_adapters/secrets.py` |
| Security strategy (Phases 1–6) | **Documented** — [OEM_API_SECURITY.md](./OEM_API_SECURITY.md) |
| Partner-gated Arc OpenAPI (exact schemas) | **Open** — needs NDA / partner pack |
| Fourier FSM command matrix for safe inject | **Open** — needs vendor docs |

---

### Security phases (ops + lab)

| Phase | Focus | Software | Lab / ops |
|-------|--------|----------|-----------|
| **1** Network | VLAN 10/20/30; no robot internet | Runbook | **Open** |
| **2** Transport | SROS2, TLS, mTLS edge→cloud | mTLS client done | SROS2 / VLAN **open** |
| **3** Identity | Per-OEM secrets, leases | Env loader done | Vault rotation policy **open** |
| **4** Authorization | RBAC + OEM scopes | Code done | Flip Fly flags **when ready** |
| **5** Runtime | Watchdog, estop, rate limit, audit | Watchdog / estop / bench done | Call-audit → SIEM **open** |
| **6** Governance | Arc NDA, quarterly API dump, external audit | Scripts/docs | Audit package **open** |

---

## What is live today

| Asset | Detail |
|-------|--------|
| GitHub | `ugobe007/Orbital_AI` · `main` |
| Cloud | Fly `orbital-ai` (SJC) · https://orbital-ai.io · dashboard at `/app/` |
| Demo mode | Built-in simulated fleet (no robots required) |
| Tests | ~145 pytest cases green in CI |
| Fly secrets already set | Contact/inbox: Resend keys, `ORBITAL_ADMIN_TOKEN`, `ORBITAL_CONTACT_FROM` |
| Fly secrets **not** required yet for demo | OEM robot keys (those belong on edge/lab when robots are connected) |

---

## Explicitly deferred (by design)

- Open-RMF multi-robot traffic scheduler  
- YOLOv8 camera Strategy B  
- Paid pilot site hardware commissioning  
- AWS migration (Fly is sufficient for abstraction testing)  
- StageGate CRM / tradeshow product work (separate repo)

---

## Recommended next decisions (partner discussion)

1. **Lock down the public dashboard?**  
   Set Fly secrets: `ORBITAL_RBAC_ENFORCE=1`, `ORBITAL_STRICT_OEM_SCOPES=1`, `ORBITAL_RBAC_TOKENS=…`  
   Smoke: `python3 scripts/check_prod_security.py --strict`

2. **Schedule lab cutover?**  
   Cameras + Unitree (or Spot) + VLANs per [HARDWARE_GATE.md](./HARDWARE_GATE.md).

3. **Pursue partner API packs?**  
   Agility Arc OpenAPI under NDA; Fourier FSM matrix — unblocks production-safe inject for those OEMs.

4. **External security audit timing?**  
   After Phase 1–2 (VLAN + transport) are green in the lab.

---

## Document map (engineering detail)

| Document | Use when… |
|----------|-----------|
| [PHASES_AND_STATUS.md](./PHASES_AND_STATUS.md) | **This page** — partner / exec review |
| [SPRINT_PRIORITIZATION.md](./SPRINT_PRIORITIZATION.md) | Sprint A–D engineering checklist |
| [GAP_ANALYSIS.md](./GAP_ANALYSIS.md) | Module-by-module vs build guide |
| [OEM_API_CONNECTIONS.md](./OEM_API_CONNECTIONS.md) | Which OEM APIs we collected |
| [OEM_API_SECURITY.md](./OEM_API_SECURITY.md) | How we secure OEM control planes |
| [HARDWARE_GATE.md](./HARDWARE_GATE.md) | On-site lab checklist before pilot |
| [ARIA_ORBITAL_AI_BUILD_GUIDE.md](./ARIA_ORBITAL_AI_BUILD_GUIDE.md) | Full architecture (Modules 1–7) |
| [ARIA_ORBITAL_AI_Presentation_Script.md](./ARIA_ORBITAL_AI_Presentation_Script.md) | Narrative / pitch script |

Config reference: repo root `.env.example`.
