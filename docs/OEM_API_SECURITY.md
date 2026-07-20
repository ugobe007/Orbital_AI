# OEM API Security Strategy

**Companion to:** [OEM_API_CONNECTIONS.md](./OEM_API_CONNECTIONS.md)  
**Date:** 2026-07-18

## Collected vs missing (summary)

| Bucket | Count | Notes |
|--------|-------|-------|
| Vendors with public control surfaces cataloged | 8 | + Pudu Open Platform (HMAC-SHA1) |
| Endpoints in `list_oem_endpoints()` | 31+ | Dry-run clients record call shapes |
| Adapters wired to `oem_api` | **8** | All vendors via dedicated adapters |
| **High** documentation gaps | 2 | Arc OpenAPI, Fourier FSM command matrix |

### Missing (actionable)

1. **Agility Arc OpenAPI** — partner-gated; confirm path/schema under NDA.
2. **Fourier Aurora per-FSM command matrix** — needed for safe `inject_waypoint`.
3. **Unitree G1 namespace matrix** — verify vs Go2 Nav2 path.
4. **AgiBot HAL estop/pose RPCs** — beyond PncService nav.
5. **MagicLab ROS2 topic + LCM IDL** — full schemas from SDK packages.
6. **Deep Robotics UDP binary layout** — from MotionSDK headers.
7. **Secret rotation / revoke** — env + Fly secrets loader exists (`fleet_adapters/secrets.py`); add HashiCorp/rotation policy in lab.

---

## Security strategy (defense in depth)

### Phase 1 — Network isolation (P0)

| Control | Implementation |
|---------|----------------|
| VLAN 10 cameras | No egress; edge only |
| VLAN 20 robots | No internet; edge only |
| VLAN 30 edge mgmt | Outbound mTLS to Orbital cloud only |
| Deny | Robot APIs reachable from office Wi‑Fi / public internet |

Already scaffolded: `docs/HARDWARE_GATE.md`, `scripts/gen_mtls_certs.sh`.

### Phase 2 — Transport encryption (P0/P1)

| Plane | Control |
|-------|---------|
| ROS 2 / DDS (Unitree, Deep, MagicLab, Fourier) | **SROS2** enclaves (`ROS_SECURITY_ENABLE=true`) |
| Spot gRPC | Spot SDK TLS + authenticated sessions |
| Agility Arc / AimDK HTTP | TLS 1.2+ only; pin CA where possible |
| Deep UDP / MagicLab LCM | **Stay on VLAN 20**; no cross-VLAN multicast; treat as cleartext → isolate |
| Edge → Orbital cloud | Client mTLS (`ORBITAL_MTLS_*`) |

### Phase 3 — Identity & secrets (P1)

| Asset | Practice |
|-------|----------|
| Spot username/password | `ORBITAL_SECRET_SPOT_JSON` via Fly secrets / vault; never commit |
| Arc API keys | `ORBITAL_SECRET_ARC_API_KEY`; rotate on OEM offboard |
| AimDK / Deep / Fourier / MagicLab | `ORBITAL_SECRET_<ALIAS>_JSON` (see `.env.example`) |
| Loader | `fleet_adapters.secrets.resolve_oem_credentials` on `adapter.connect()` |
| AimDK robot reachability | Bind to robot VLAN IPs only |
| Aurora domain_id / robot_name | Config as identity, not secret — still protect DDS domain |
| Lease (BD) | Single edge process holds lease; watchdog releases on crash |
| Prod flag smoke | `python3 scripts/check_prod_security.py --strict` |

### Phase 4 — Authorization (P1)

| Layer | Already in Orbital | Enforce in prod |
|-------|--------------------|-----------------|
| OEM capability ceilings | `fleet_adapters` | Keep BD/Agility without `VELOCITY` |
| OEM scope grants | `scope_guard` / OEM API keys | `ORBITAL_STRICT_OEM_SCOPES=1` |
| Dashboard roles | Admin / Operator / Viewer | `ORBITAL_RBAC_ENFORCE=1` |
| Inject path | `mission.dispatch` or `control.velocity` | Deny unmanaged vendors in prod |

### Phase 5 — Runtime safety (P1)

| Control | Mechanism |
|---------|-----------|
| Drift halt | `SafetyWatchdog` + `PoseBus` (independent of inject) |
| E-Stop | `trigger_estop` via OEM client; never block on cloud |
| Rate limit | 10 Hz inject budget (`LatencyBench`); drop surplus |
| Audit | Persist `oem_api.calls` → Telemetry/SIEM | **Done (soft)** — `fleet_adapters/oem_apis/audit.py`; `GET /api/dashboard/oem-audit`; optional Influx `oem_api_call` |

### Phase 6 — Governance (P2/P3)

1. NDA + Arc OpenAPI acquisition; update `AgilityArcClient` paths.
2. Quarterly re-dump: `python3 scripts/dump_oem_apis.py` vs vendor release notes.
3. External audit package per `HARDWARE_GATE.md` (network diagram, cert inventory, RBAC dump).
4. Lab: enable SROS2; call-audit of `oem_api.calls` already sinks to memory/Influx (`GET /api/dashboard/oem-audit`).

---

## Priority roadmap

| Priority | Work | Exit |
|----------|------|------|
| **P0** | VLAN + no off-subnet UDP/cmd_vel + edge mTLS | Lab network checklist signed |
| **P1** | Secrets env loader + RBAC enforce + Arc NDA + Fourier FSM map | `check_prod_security.py --strict` green; docs updated |
| **P2** | SROS2 + call audit of `oem_api.calls` | **Call audit done (soft)**; SROS2 still lab |
| **P3** | External security audit | Auditor report filed |

**Done (code):** 8/8 adapters wired to `oem_api` (incl. Pudu); env secret loader; prod security check script; OEM call audit sink.

---

## What not to do

- Do not expose AimDK `:53176`, Spot SDK ports, or ROS DDS outside VLAN 20/30.
- Do not put Arc keys or Spot passwords in StageGate `.env` or git.
- Do not enable `use_hardware=True` without SROS2 / TLS plan for that transport.
- Do not treat Agility inferred REST paths as production contracts until partner OpenAPI is in-repo (redacted).
