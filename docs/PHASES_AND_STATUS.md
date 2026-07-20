# Orbital AI — Phases & Completed Work

**Audience:** Business partner review  
**Date:** 2026-07-19 (updated)  
**Product:** Orbital AI — robot-control abstraction (cloud + ARIA edge + fleet adapters)  
**Repo / deploy:** [ugobe007/Orbital_AI](https://github.com/ugobe007/Orbital_AI) (**public**) · Fly app `orbital-ai` · https://orbital-ai.io  

> **What Orbital is:** A vendor-neutral control and monitoring layer so facilities can run mixed robot fleets (Unitree, Boston Dynamics Spot, Agility Digit, AgiBot, Deep Robotics, Fourier, MagicLab) through one API and dashboard.  
> **What it is not:** StageGate (separate client product). Client apps consume Orbital over API; they do not share deploy secrets.

---

## Executive summary

| Area | Status |
|------|--------|
| Software abstraction (sim-first) | **Complete** — Sprints A–C done; CI green; live on Fly |
| OEM API research & adapter wiring | **Complete (code)** — 7 vendors, 27 endpoints, all adapters wired |
| Fleet sim (multi-lead / shuttle) | **Complete** — 2–3 leads publish relays; others oscillate |
| Lab / hardware cutover | **Day-1 runbook ready** — [LAB_CUTOVER_DAY1.md](./LAB_CUTOVER_DAY1.md); on-site not started |
| Production security lockdown | **Flags set on Fly** — RBAC + OEM scopes + tokens deployed; anon viewers allowed for public demo |
| OEM call audit (Security Phase 5) | **Soft done** — in-memory (+ optional Influx); `GET /api/dashboard/oem-audit` |
| Pilot site | **Deferred** until hardware gate LAB rows are signed |

**Bottom line for partners:** The control abstraction is built and demoable in simulation. Next investment is lab hardware + network security (VLAN / SROS2), not more core software scaffolding.

---

## How we sequenced the work

```
Sprint A  →  Sprint B  →  Sprint C  →  Sprint D (soft)  →  Lab hardware  →  Pilot
 Core loop    Protocols    Cloud/RBAC    CI gates           On-site gear     Live site
```

Security hardening is organized as **Phases 1–6** (network → transport → identity → authorization → runtime → governance).

---

## Phase / sprint status

### Sprint A — Faithful core control loop (simulation) — **Done**
### Sprint B — Per-vendor protocol contracts — **Done**
### Sprint C — Cloud production seams — **Done** (RBAC opt-in; Fly secrets configured)
### Sprint D — Hardware gate — **Soft done / Lab open** — see [HARDWARE_GATE.md](./HARDWARE_GATE.md)

### OEM API program — **Code complete**; Arc OpenAPI + Fourier FSM still partner-gated

### Security phases

| Phase | Focus | Status |
|-------|--------|--------|
| **1** Network | VLAN 10/20/30 | **Open** (lab) |
| **2** Transport | SROS2, TLS, mTLS | mTLS scaffold **done**; SROS2 **open** |
| **3** Identity | Per-OEM secrets | Env loader **done**; vault rotation **open** |
| **4** Authorization | RBAC + OEM scopes | **Fly secrets deployed** (anon viewer for public demo) |
| **5** Runtime | Watchdog, estop, rate limit, **audit** | Watchdog/estop/bench **done**; **call audit soft done** |
| **6** Governance | Arc NDA, quarterly dump, external audit | Scripts/docs **done**; audit package **open** |

---

## What is live today

| Asset | Detail |
|-------|--------|
| GitHub | `ugobe007/Orbital_AI` · `main` · **public** |
| Cloud | Fly `orbital-ai` (SJC) · https://orbital-ai.io · dashboard `/app/` |
| Demo | Simulated multi-lead fleet (no robots required) |
| Tests | ~150+ pytest cases in CI |
| Fly secrets | Contact/Resend + `ORBITAL_RBAC_*` + `ORBITAL_STRICT_OEM_SCOPES` |
| OEM audit API | `GET /api/dashboard/oem-audit` (Admin) |

---

## Explicitly deferred (by design)

- Open-RMF multi-robot traffic scheduler  
- YOLOv8 camera Strategy B  
- Paid pilot site hardware commissioning  
- AWS migration (Fly is sufficient for abstraction testing)  
- StageGate CRM / tradeshow product work (separate repo)

---

## Recommended next decisions (partner discussion)

1. **Lab cutover date** — Use [LAB_CUTOVER_DAY1.md](./LAB_CUTOVER_DAY1.md) (BOM + 5h timeline). Cameras + Unitree (or Spot) + VLANs.
2. **Public demo posture** — Keep anon viewers (`ORBITAL_RBAC_ANON_VIEWER=1`) or lock all APIs (`=0`).
3. **Partner API packs** — Agility Arc OpenAPI (NDA); Fourier FSM matrix.
4. **External security audit** — After Phase 1–2 green in the lab.

---

## Document map

| Document | Use when… |
|----------|-----------|
| [PHASES_AND_STATUS.md](./PHASES_AND_STATUS.md) | **This page** — partner / exec review |
| [SPRINT_PRIORITIZATION.md](./SPRINT_PRIORITIZATION.md) | Sprint A–D engineering checklist |
| [OEM_API_CONNECTIONS.md](./OEM_API_CONNECTIONS.md) | Which OEM APIs we collected |
| [OEM_API_SECURITY.md](./OEM_API_SECURITY.md) | How we secure OEM control planes |
| [HARDWARE_GATE.md](./HARDWARE_GATE.md) | On-site lab checklist before pilot |
| [LAB_CUTOVER_DAY1.md](./LAB_CUTOVER_DAY1.md) | First lab day BOM + timeline |
| [FLY_PRODUCTION.md](./FLY_PRODUCTION.md) | Fly RBAC secrets & lockdown |

Config reference: repo root `.env.example`.
