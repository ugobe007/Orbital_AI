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
from fleet_adapters import get_adapter
from fleet_adapters.oem_apis import get_oem_client, list_oem_endpoints

# Fleet adapters now own an ``oem_api`` client (dry-run by default):
unitree = get_adapter("Unitree", "rbt-01")
unitree.connect()
unitree.inject_waypoint("rbt-01", (1.0, 2.0))
assert unitree.oem_api.calls[-1].payload["type"] == "nav2_msgs/action/NavigateToPose"

spot = get_adapter("Boston Dynamics", "spot-1", endpoint="192.168.50.3")
spot.connect({"username": "user", "password": "***"})
spot.inject_waypoint("spot-1", (1.2, 0.4))  # OEM client + optional FakeBosdynServer

digit = get_adapter("Agility Robotics", "digit-1", api_key="arc-key")
digit.connect({"api_key": "arc-key"})
digit.inject_waypoint("digit-1", (0.5, 0.1))

pudu = get_adapter("Pudu Robotics", "pudu-01")
pudu.connect({"app_key": "…", "app_secret": "…"})
pudu.inject_waypoint("pudu-01", (1.0, 0.5))

# All vendors expose ``adapter.oem_api`` (AgiBot / Deep / Fourier / MagicLab / Pudu too):
agibot = get_adapter("AgiBot", "a2-1", endpoint="192.168.100.110")
agibot.connect()
agibot.inject_waypoint("a2-1", (0.8, 0.2))

# Credentials from env (Fly secrets): ORBITAL_SECRET_ARC_API_KEY, ORBITAL_SECRET_PUDU_JSON, …
# See fleet_adapters/secrets.py and docs/OEM_API_SECURITY.md

# Standalone client (same shapes):
client = get_oem_client("Boston Dynamics", "spot-1", host="192.168.50.3", dry_run=True)
assert client.connect({"username": "user"})
assert client.inject_waypoint(1.2, 0.4, 0.0)

list_oem_endpoints()
```

Lab hardware: `get_adapter("Unitree", "rbt-01", use_hardware=True)` (requires `rclpy`),
or `get_adapter("Boston Dynamics", ..., use_hardware=True)` / Agility with API key.

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
| **Pudu Robotics** | REST (HMAC-SHA1) | `POST …/v1/api/robot/task` (confirm path) | [pudurobotics.com](https://www.pudurobotics.com/) | httpx + ApiAppKey/Secret |

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

### Pudu Robotics (Open Platform)

- Cloud REST with **HMAC-SHA1** (`ApiAppKey` / `ApiAppSecret`).
- Signing string: `x-date`, method, Accept, Content-Type, Content-MD5, canonical path+sorted query
  (strip `/release|/test|/prepub` before signing) — see `fleet_adapters/oem_apis/pudu.py`.
- Sample health: `GET …/pudu-entry/data-open-platform-service/v1/api/healthCheck`.
- Test host: `https://open-platform-test.pudutech.com` (replace with your account domain).
- Task / status / estop paths are **placeholders** until confirmed against your Pudu OpenAPI pack.
- Secrets: `ORBITAL_SECRET_PUDU_JSON={"app_key":"…","app_secret":"…"}` or `_API_KEY` + `_APP_SECRET`.

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
| 2 | Set `dry_run=False` / `use_hardware=True` + Fly secrets for credentials | Robotics |
| 3 | ~~Wire all adapters to OEM clients~~ **done** (incl. Pudu) | — |
| 4 | Confirm Agility Arc OpenAPI + Pudu task/status paths with partners | Integrations |
| 5 | Lab integration test per vendor (fake server or robot) | QA |
| 6 | Keep `list_oem_endpoints()` in sync when vendors change APIs | Docs |
| 7 | Prod flags: `ORBITAL_RBAC_ENFORCE=1`, `ORBITAL_STRICT_OEM_SCOPES=1` | Ops |

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
