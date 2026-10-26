# Hardware Gate — Sprint D (lab readiness)

**Status:** Abstraction sprints A–C are done. This document is the gate checklist before
live cameras / Unitree / VLAN cutover. Soft pieces (recorded ArUco, latency bench, ops
runbooks) live in-repo and pass CI; hardware boxes must be checked on-site.

Related code:
- D1: `aria_edge/aruco.py`, `testdata/aruco/`
- D2: `fleet_adapters/unitree.py`, `aria_edge/latency_bench.py`
- D3: this doc + `scripts/check_hardware_gate.py` + `scripts/mtls/`

---

## D1 — ArUco on recorded frames

| Step | Action | Done |
|------|--------|------|
| 1 | Print laminated ArUco 4×4 markers (IDs 0–10), 15 cm | ☐ |
| 2 | Mount marker on Unitree top plate; note ID → robot_id map | ☐ |
| 3 | Capture overhead frames; export JSON via fixture schema in `testdata/aruco/` | ☐ |
| 4 | Run `ArucoPoseEstimator` on recorded frames (CI covers fixture path) | ☐ |
| 5 | Optional: install `opencv-python-headless` (see `requirements-hw.txt`) for live detect | ☐ |
| 6 | Calibrate camera extrinsics into facility meters | ☐ |
| 7 | **Rig calibration pass (Sprint H):** floor markers captured from every camera with overlap; `scripts/calibrate_rig.py` exits 0 (every residual under `ORBITAL_CALIB_RESIDUAL_M`, default 5 cm) and the dashboard's Rig Calibration panel reads "clean" | ☐ |

Offline fixture schema: `camera_id`, `markers[].{marker_id,tvec,yaw}`, `marker_map`, `calibration`.

### Rig calibration pass

1. Tape floor markers so every camera sees at least two, and neighbouring cameras share at least
   one. Tape-measure the origin marker and one marker a few metres away (the anchor).
2. Capture each camera (or walk a handheld pass) and export one JSON per view, either in the
   `testdata/aruco/` frame format or the captures format in `testdata/calibration/`.
3. Solve and upload:

```bash
python3 scripts/calibrate_rig.py captures/ --origin 0 --origin-pose 2.0:2.0 \
  --anchor 3:12.5:2.0 --out marker_map.json --upload "$ORBITAL_CLOUD_URL" --facility facility-sf-001
```

4. Fix what the report names: re-seat a bumped marker ("Marker 9 … disagrees by 15 cm"),
   re-mount a moved camera or update its extrinsics, or add an overlapping capture for markers
   that are not linked to the origin. Re-run until it prints "Rig is clean." (exit code 0).

```bash
python3 -c "
from pathlib import Path
from aria_edge.aruco import ArucoPoseEstimator, load_recorded_frame
frame, mapping, ext = load_recorded_frame('testdata/aruco/frame_001.json')
est = ArucoPoseEstimator(marker_to_robot=mapping, extrinsics={ext.camera_id: ext})
print(est.process_frame(frame))
"
```

---

## D2 — Unitree adapter + 10 Hz latency (S1-08)

| Step | Action | Done |
|------|--------|------|
| 1 | Unitree G1 on Robot VLAN 20; edge host can reach ROS 2 DDS | ☐ |
| 2 | Install `unitree_ros2` / `rclpy` on edge (`requirements-hw.txt`) | ☐ |
| 3 | `get_adapter('Unitree', 'rbt-01', use_hardware=True)` after SDK install | ☐ |
| 4 | Suppress onboard SLAM; start `SimulatedTFPublisher` / real TF publisher | ☐ |
| 5 | Run latency bench — p95 inject ≤ 100 ms at 10 Hz | ☐ |

```bash
python3 -c "
from fleet_adapters import get_adapter
from aria_edge.latency_bench import LatencyBench
a = get_adapter('Unitree', 'rbt-01')  # sim path in CI
print(LatencyBench(hz=10, samples=30).run(a).as_dict())
"
```

Pass criteria (S1-08): **p95 ≤ 100 ms** at 10 Hz for `inject_waypoint`.

---

## D3 — VLAN / mTLS / SROS2 + external audit

### VLAN architecture (guide §11.1)

| VLAN | ID | Members | Egress |
|------|----|---------|--------|
| Camera | 10 | PoE cameras | No egress; to edge only |
| Robot | 20 | Robots | No internet; to edge only |
| Edge mgmt | 30 | ARIA edge | Outbound mTLS to Orbital cloud only |

| Step | Action | Done |
|------|--------|------|
| 1 | Configure managed switch with VLANs 10/20/30 | ☐ |
| 2 | Verify camera VLAN cannot reach internet | ☐ |
| 3 | Verify robot VLAN cannot reach internet | ☐ |
| 4 | Edge host dual-homed / trunked to all three | ☐ |
| 5 | Generate mTLS material: `./scripts/gen_mtls_certs.sh` | ☐ |
| 6 | Install client certs on edge; set `ORBITAL_MTLS_*` | ☐ |
| 7 | Cloud terminates TLS and requires client certs | ☐ |
| 8 | SROS2 keystore (see below) | ☐ |
| 9 | External security audit scheduled / completed | ☐ |

### SROS2 (guide §11.3)

On a ROS 2 Humble+ host:

```bash
sudo mkdir -p /etc/aria/sros2_keystore
ros2 security create_keystore /etc/aria/sros2_keystore
ros2 security create_enclave /etc/aria/sros2_keystore /aria_pose_estimator
ros2 security create_enclave /etc/aria/sros2_keystore /aria_waypoint_injector
ros2 security create_enclave /etc/aria/sros2_keystore /aria_safety_halt
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_KEYSTORE=/etc/aria/sros2_keystore
export ROS_SECURITY_STRATEGY=Enforce
```

### External audit package

Provide the auditor:

1. This checklist (signed completion dates)
2. Network diagram (VLANs + mTLS path)
3. Cert inventory (CA, edge client, cloud server — no private keys in the packet)
4. SROS2 enclave list
5. OEM scope / RBAC policy dump (`ORBITAL_RBAC_ENFORCE=1` config)
6. Incident response: who can `trigger_estop`, halt thresholds

---

## Auto-check (CI-safe)

```bash
python3 scripts/check_hardware_gate.py
python3 scripts/lab_cutover.py --bench
```

Reports which soft gates are green in this repo vs which remain lab-only.

### Prep already done in-repo (2026-07-18)

| Item | Location |
|------|----------|
| Soft D1–D3 CI | green on `main` |
| mTLS CA/client/server | `scripts/mtls/certs/` (gitignored; regenerate with `./scripts/gen_mtls_certs.sh`) |
| Lab edge factories | `aria_edge/lab_runtime.py` (`ARIA_CV_MODE`, `ARIA_UNITREE_HARDWARE`) |
| Cutover smoke | `scripts/lab_cutover.py` |
| Lab A/B recording | `EdgeAgent.start_run` / `finish_run`, `correction_enabled` |
| Results sheet | `scripts/lab_report.py` |

---

## D4 — Real numbers (A/B on the lab robot)

The gate is not passed by "it works"; it is passed by a number we can publish. Run the same task
on the same robot and floor in two conditions, measured against the overhead cameras:

| Condition | Edge setting | Run `source` |
|-----------|--------------|--------------|
| Baseline: the robot on its own SLAM | `agent.correction_enabled = False` (Orbital observes, records and still safety-halts, but never steers) | `lab-baseline` |
| With Orbital camera correction | `agent.correction_enabled = True` | `lab-orbital` |

| Step | Action | Done |
|------|--------|------|
| 1 | Rig calibration pass is clean (D1 step 7) so ground truth is trustworthy | ☐ |
| 2 | Pick one repeatable task between two marked stations (e.g. `Aisle AB → Dock`) | ☐ |
| 3 | Alternate conditions run by run (B, O, B, O…) so battery and floor changes hit both equally; at least **10 complete runs each** | ☐ |
| 4 | Wrap each run in `agent.start_run(robot_id, task)` … `agent.finish_run(robot_id)`; with `CloudSync(enabled=True)` it uploads to `POST /api/v1/runs/{facility}` | ☐ |
| 5 | Save the latency bench: `LatencyBench(...).run(adapter).as_dict()` → `bench.json` | ☐ |
| 6 | `python3 scripts/lab_report.py --url "$ORBITAL_CLOUD_URL" --site "<site, robot>" --latency bench.json --calibration marker_map.json --out docs/results/<date>.md` exits 0 | ☐ |
| 7 | Commit the results sheet; the first clean Orbital run of the task is now its golden run on the dashboard's Validation panel | ☐ |

`lab_report.py` refuses to present simulator runs as lab results and flags any condition with
fewer than `--min-runs` complete runs, so a sheet that exits 0 is safe to quote.
