# Lab Cutover — Day 1 Runbook

**Goal:** First on-site day for Orbital AI hardware gate (Sprint D lab rows).  
**Soft gates:** already green in CI (`python3 scripts/lab_cutover.py --bench`).  
**Partner brief:** [PHASES_AND_STATUS.md](./PHASES_AND_STATUS.md)

---

## Before lab day (remote / office)

| # | Task | Owner | Done |
|---|------|-------|------|
| 1 | Confirm first robot: **Unitree** (default) or Spot | Partner | ☐ |
| 2 | Confirm overhead camera + mount location | Partner | ☐ |
| 3 | Confirm managed switch supports VLANs 10/20/30 | Partner | ☐ |
| 4 | Order BOM below (lead time) | Ops | ☐ |
| 5 | Print ArUco 4×4 markers IDs 0–10, **15 cm**, laminate | Ops | ☐ |
| 6 | Regenerate mTLS certs on edge laptop: `./scripts/gen_mtls_certs.sh` | Eng | ☐ |
| 7 | Soft verify on laptop: `python3 scripts/lab_cutover.py --bench` | Eng | ☐ |
| 8 | Book 4–6 hour lab block + spare battery / charger | Ops | ☐ |

---

## Bill of materials (minimum Day 1)

| Item | Qty | Notes |
|------|-----|-------|
| Unitree (G1 or Go2) **or** Spot | 1 | Prefer Unitree for public ROS 2 path |
| Overhead PoE camera | 1 | Fixed mount preferred |
| Managed PoE switch (VLAN-capable) | 1 | VLANs 10 / 20 / 30 |
| Edge host (NUC / laptop) | 1 | Dual-NIC or trunk to switch |
| Cat6 patch cables | 6+ | Cameras, robot AP, edge, uplink |
| Laminated ArUco markers | 11 | IDs 0–10, 15 cm |
| Tape / mounts for marker on robot top plate | — | Stable under motion |
| Spare robot battery + charger | 1 | Avoid mid-bench power loss |

Optional Day 1+: second camera, spare robot, UPS for edge.

---

## Day 1 timeline (suggested)

| Time | Block | Exit criteria |
|------|-------|----------------|
| 0:00–0:30 | Safety brief + power-up robot alone (no Orbital inject) | Robot walks in vendor app |
| 0:30–1:30 | **VLAN** — configure 10/20/30; deny internet on 10 & 20 | Ping tests fail from cam/robot to 8.8.8.8 |
| 1:30–2:00 | Edge on VLAN 30; install `requirements-hw.txt` + vendor SDK | `python3 -c "import rclpy"` (Unitree path) |
| 2:00–2:30 | Mount ArUco; map marker ID → `robot_id` | Written map in lab log |
| 2:30–3:30 | Recorded → live ArUco: capture frames, run estimator | Pose appears in facility meters |
| 3:30–4:30 | `use_hardware=True` Unitree inject + **10 Hz latency bench** | p95 ≤ 100 ms |
| 4:30–5:00 | mTLS edge→cloud smoke; document failures | Client cert presents; cloud accepts |
| 5:00 | Sign HARDWARE_GATE D1/D2/D3 rows that passed | Photo + checklist update |

**Stop rule:** If VLAN isolation fails, do **not** enable `use_hardware=True` on a routable network.

---

## Commands (edge host)

```bash
# Soft (no robot) — should already pass
python3 scripts/check_hardware_gate.py
python3 scripts/lab_cutover.py --bench

# After SDK + VLAN
export ARIA_UNITREE_HARDWARE=1
export ARIA_CV_MODE=aruco   # when live camera path is ready

python3 -c "
from fleet_adapters import get_adapter
from aria_edge.latency_bench import LatencyBench
a = get_adapter('Unitree', 'rbt-01', use_hardware=True)
print(LatencyBench(hz=10, samples=30).run(a).as_dict())
"
```

mTLS: see [HARDWARE_GATE.md](./HARDWARE_GATE.md) § D3 and `./scripts/gen_mtls_certs.sh`.

---

## Day 1 success definition

- [ ] Robot VLAN cannot reach the public internet  
- [ ] At least one ArUco pose published into Orbital’s pose path  
- [ ] Inject latency p95 ≤ 100 ms at 10 Hz **or** documented blocker with owner  
- [ ] HARDWARE_GATE.md rows updated with date / initials  

Pilot site remains **blocked** until remaining LAB rows are checked.

---

## After Day 1

1. Update [PHASES_AND_STATUS.md](./PHASES_AND_STATUS.md) lab row statuses.  
2. Schedule Day 2: SROS2 enclaves + second robot / Spot if needed.  
3. Only then consider `ORBITAL_RBAC_ANON_VIEWER=0` for a locked control surface.
