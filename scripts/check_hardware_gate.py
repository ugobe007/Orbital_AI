#!/usr/bin/env python3
"""Report Sprint D hardware-gate readiness (soft checks only — no lab gear required)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    # D1 recorded ArUco fixture
    fixture = ROOT / "testdata" / "aruco" / "frame_001.json"
    try:
        from aria_edge.aruco import ArucoPoseEstimator, load_recorded_frame

        frame, mapping, ext = load_recorded_frame(fixture)
        est = ArucoPoseEstimator(marker_to_robot=mapping, extrinsics={ext.camera_id: ext})
        dets = est.process_frame(frame)
        ok = len(dets) >= 1 and dets[0].robot_id in mapping.values()
        results.append(("D1 ArUco recorded-frame path", ok, f"{len(dets)} detections"))
    except Exception as exc:  # noqa: BLE001
        results.append(("D1 ArUco recorded-frame path", False, str(exc)))

    # D2 Unitree + latency bench (sim)
    try:
        from aria_edge.latency_bench import LatencyBench
        from fleet_adapters import get_adapter

        adapter = get_adapter("Unitree", "rbt-01")
        report = LatencyBench(hz=10, samples=20).run(adapter)
        results.append(
            ("D2 10 Hz latency bench (sim)", report.pass_, json.dumps(report.as_dict())),
        )
        results.append(
            ("D2 UnitreeAdapter registered", adapter.vendor == "Unitree", adapter.__class__.__name__),
        )
    except Exception as exc:  # noqa: BLE001
        results.append(("D2 latency / Unitree", False, str(exc)))

    # D3 docs + mTLS script present
    hw_doc = (ROOT / "docs" / "HARDWARE_GATE.md").is_file()
    mtls = (ROOT / "scripts" / "gen_mtls_certs.sh").is_file()
    results.append(("D3 HARDWARE_GATE.md", hw_doc, "docs/HARDWARE_GATE.md"))
    results.append(("D3 gen_mtls_certs.sh", mtls, "scripts/gen_mtls_certs.sh"))

    # Lab-only reminders (always "open")
    results.append(("D3 VLAN 10/20/30 configured (lab)", False, "lab-only"))
    results.append(("D3 SROS2 keystore on edge (lab)", False, "lab-only"))
    results.append(("D3 external security audit (lab)", False, "lab-only"))
    results.append(("D2 Unitree rclpy hardware bind (lab)", False, "lab-only"))

    width = max(len(name) for name, _, _ in results)
    print("Sprint D hardware gate — soft checks\n")
    failed = 0
    for name, ok, detail in results:
        if "lab-only" in detail:
            mark = "LAB"
        else:
            mark = "OK " if ok else "FAIL"
            if not ok:
                failed += 1
        print(f"  [{mark}] {name:<{width}}  {detail}")

    print()
    if failed:
        print(f"{failed} soft check(s) failed.")
        return 1
    print("All soft checks passed. Complete LAB rows on-site before pilot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
