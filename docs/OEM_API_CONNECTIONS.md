# OEM API Connections — Public Research & Implementation Map

**Date:** 2026-07-18  
**Scope:** Publicly documented control surfaces for Orbital fleet OEMs.  
**Code:** `fleet_adapters/oem_apis/`  
**Status:** Dry-run clients + endpoint catalog. Real SDK binds are lab follow-ups.

This document is the implementation brief for connecting ARIA `inject_waypoint` /
`get_internal_pose` / `trigger_estop` to each vendor’s **public** API. Partner-only
OpenAPI (e.g. full Agility Arc) is called out where documentation is gated.

---

## How to use in code

```python
from fleet_adapters.oem_apis import get_oem_client, list_oem_endpoints

client = get_oem_client("Boston Dynamics", "spot-1", host="192.168.50.3", dry_run=True)
assert client.connect({"username": "user", "password": "***"})
assert client.inject_waypoint(1.2, 0.4, 0.0)
print(client.calls[-1].op, client.calls[-1].payload)

# Full catalog for dashboards / OEM onboarding:
list_oem_endpoints()
```

CLI dump:

```bash
python3 -c "import json; from fleet_adapters.oem_apis import list_oem_endpoints; print(json.dumps(list_oem_endpoints(), indent=2))"
```

---

## Vendor summary

| Vendor | Transport | Primary inject primitive | Public docs home | SDK package |
|--------|-----------|--------------------------|------------------|-------------|
| **Unitree** | ROS 2 + Nav2 | `NavigateToPose` / `cmd_vel` → SDK `Move` | [unitree_ros2](https://github.com/unitreerobotics/unitree_ros2) | `rclpy`, `nav2_msgs`, unitree_sdk2 |
| **Boston Dynamics** | gRPC | `RobotCommand` SE2 trajectory (lease required) | [dev.bostondynamics.com](https://dev.bostondynamics.com/) | `bosdyn-client` |
| **Agility Robotics** | REST / WSS | `POST /api/v1/tasks` (Arc; partner OpenAPI) | [Agility Arc launch](https://www.agilityrobotics.com/) | httpx + API key |
| **AgiBot** | HTTP-RPC (AimDK) | `PncService/PlanningNaviToPose2D` | [open.agibot.com nav](https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation) | httpx |
| **Deep Robotics** | ROS 2 ↔ UDP | `/cmd_vel` + UDP `:43893` | [Lite3_ROS](https://github.com/DeepRoboticsLab/Lite3_ROS) | transfer + MotionSDK |
| **Fourier Robotics** | DDS (Aurora) | `AuroraClient` locomotion (FSM-gated) | [support.fftai.com](https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR1/SDK/Overview/) | `fourier_aurora_client` |
| **MagicLab** | ROS 2 + LCM | `/{ns}/goal_pose` + LCM status | [support.magiclab.top](https://support.magiclab.top/en/) | MagicDog-Ros2_SDK |

---

## Per-vendor implementation notes

### Unitree

- **Not** a cloud REST API. Control is on-robot ROS 2.
- Inject: Nav2 action `/{ns}/navigate_to_pose` (`nav2_msgs/action/NavigateToPose`).
- Fast path (10 Hz TF hijack): publish `geometry_msgs/Twist` on `/{ns}/cmd_vel`; bridge to Unitree high-level `Move(vx, vy, vyaw)`.
- Pose: `/{ns}/odom`.
- **Lab:** install `unitree_ros2`, Nav2, set `ARIA_UNITREE_HARDWARE=1`.

### Boston Dynamics (Spot)

- Authenticate → time sync → **acquire lease** → `RobotCommandClient.robot_command(...)`.
- Inject maps to `RobotCommandBuilder` SE2 trajectory helpers (see Spot SDK docs).
- No ROS TF hijack; command-level only (matches Orbital capability ceiling).
- E-Stop via EstopService keepalive pattern.
- **Lab:** `pip install bosdyn-client`, robot hostname + credentials.

### Agility Robotics (Digit / Arc)

- Cloud automation platform; marketing + integrator docs describe REST + WebSocket for tasks/telemetry/KPIs.
- Full OpenAPI is **partner-gated** — Orbital’s `FakeArcServer` + `AgilityArcClient` use the publicly inferred `/api/v1/tasks` shape; replace paths when Arc partner docs are issued.
- Auth: Bearer API key or OAuth2 (integrator sources).

### AgiBot (AimDK)

- Public PncService RPCs over HTTP, e.g.  
  `http://{robot}:53176/rpc/aimdk.protocol.PncService/PlanningNaviToPose2D`
- Body: `{ task_id, map_id, pose: { position: {x,y}, angle }, ackerman_mode }`.
- Prerequisites (vendor docs): relocalized; MC mode `RL_LOCOMOTION_DEFAULT`.
- Cancel/pause/resume/get-state RPCs documented on the same page.

### Deep Robotics (Lite3)

- Perception host ROS 2 `transfer` package bridges `/cmd_vel` → UDP motion host.
- MotionSDK: Sender ~`43893`, receiver ~`43897` (see MotionSDK README).
- Safety/halt should prefer UDP for latency; mission inject can use nav goals + cmd_vel.

### Fourier Robotics (GR-1 / Aurora)

- `pip install fourier_aurora_client`
- `AuroraClient.get_instance(domain_id=…, robot_name=…)` over DDS.
- Commands are **FSM-state dependent**; call `set_fsm_state` before locomotion.
- Guide preference: **do not** suppress internal SLAM — fuse via EKF.

### MagicLab (MagicDog)

- MagicDog-Ros2_SDK: topics/services + SLAM/nav.
- MagicDog-Motion_SDK: LCM between PC and control board.
- Suppress internal SLAM before ARIA TF publisher (per Orbital build guide).

---

## Orbital mapping (all vendors)

| Orbital method | Meaning |
|----------------|---------|
| `inject_waypoint(x,y[,θ])` | Send `W_internal` after TF hijack transform |
| `get_internal_pose()` | Robot self-report for drift |
| `trigger_estop()` | Non-blocking hardware/fleet stop |

Capability ceilings in `fleet_adapters` remain authoritative for what Orbital may grant OEMs.

---

## Implementation checklist (lab)

| # | Task | Owner |
|---|------|-------|
| 1 | Install vendor SDK on edge host (`requirements-hw.txt` + vendor packages) | Robotics |
| 2 | Set `dry_run=False`, pass host/credentials into `get_oem_client` | Robotics |
| 3 | Wire `UnitreeAdapter` / BD / Agility adapters to call these clients | Robotics |
| 4 | Confirm Agility Arc OpenAPI with partner; update paths | Integrations |
| 5 | Add per-vendor integration test against fake or lab robot | QA |
| 6 | Keep `list_oem_endpoints()` in sync when vendors change APIs | Docs |

---

## Research sources (retrieved 2026-07-18)

1. Unitree / Nav2 — unitree_ros2 GitHub; Nav2 NavigateToPose API docs; community Go2 Nav2 writeups  
2. Boston Dynamics — Spot SDK 5.1.4 docs (`RobotCommandClient`, lease, stand/trajectory)  
3. Agility — Arc launch press + integrator descriptions (REST/WSS; OpenAPI gated)  
4. AgiBot — open.agibot.com AimDK A2 navigation RPC table  
5. Deep Robotics — Lite3_ROS + Lite3_MotionSDK GitHub + perception manuals  
6. Fourier — support.fftai.com Aurora SDK developer guide; Gitee aurora SDK  
7. MagicLab — magiclab.top opensource + support.magiclab.top document center  

**Disclaimer:** Public docs change. Always verify against the vendor’s current SDK release notes before pilot.
