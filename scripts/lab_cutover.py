#!/usr/bin/env python3
"""Lab cutover smoke — soft gates + optional recorded-frame edge tick.

Usage:
  python3 scripts/lab_cutover.py           # soft checks + recorded ArUco tick
  python3 scripts/lab_cutover.py --bench   # also run 10 Hz Unitree latency bench
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "testdata" / "aruco" / "frame_001.json"
CERTS = ROOT / "scripts" / "mtls" / "certs"


def _banner(title: str) -> None:
    print(f"\n=== {title} ===")


def run_soft_gate() -> int:
    from scripts.check_hardware_gate import main as gate_main  # type: ignore

    # check_hardware_gate is a script module; import via path run
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_hardware_gate", ROOT / "scripts" / "check_hardware_gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return int(mod.main())


def run_aruco_edge_tick() -> None:
    _banner("Recorded ArUco → EdgeAgent tick")
    os.environ.setdefault("ARIA_CV_MODE", "aruco")
    os.environ.setdefault("ARIA_ARUCO_FIXTURE", str(FIXTURE))
    # Reload settings after env set
    import importlib
    import aria_edge.config as cfg

    importlib.reload(cfg)
    import aria_edge.lab_runtime as lab

    importlib.reload(lab)

    from aria_edge.aruco import load_recorded_frame
    from aria_edge.types import Pose2D

    frame, _mapping, _ext = load_recorded_frame(FIXTURE)
    agent = lab.build_lab_edge(cloud_enabled=False)
    # Internal poses slightly drifted so inject path can exercise if trajectory set
    internal = {
        "rbt-01": Pose2D(12.35, 7.75, 0.0),
        "rbt-02": Pose2D(10.9, 8.7, 0.0),
    }
    agent.set_trajectory("rbt-01", [(12.0, 8.0), (13.0, 8.0), (14.0, 8.0)])
    results = agent.tick(frame, internal)
    print(json.dumps(results, indent=2))


def run_latency_bench() -> None:
    _banner("10 Hz Unitree latency bench (sim)")
    from aria_edge.latency_bench import LatencyBench
    from fleet_adapters import get_adapter

    report = LatencyBench(hz=10, samples=30).run(get_adapter("Unitree", "rbt-01"))
    print(json.dumps(report.as_dict(), indent=2))
    if not report.pass_:
        raise SystemExit("latency bench FAILED")


def check_mtls_certs() -> None:
    _banner("mTLS cert inventory")
    needed = ["ca.crt", "client.crt", "client.key", "server.crt", "server.key"]
    if not CERTS.is_dir():
        print(f"MISSING {CERTS} — run ./scripts/gen_mtls_certs.sh")
        return
    for name in needed:
        p = CERTS / name
        print(f"  {'OK' if p.is_file() else 'MISSING':7} {p}")


def print_lab_next() -> None:
    _banner("On-site LAB next (cannot automate)")
    print("""  1. Print ArUco 4x4 markers IDs 0–10 (15 cm), mount on Unitree
  2. Configure switch VLANs 10 (cams) / 20 (robots) / 30 (edge)
  3. Install requirements-hw.txt + ros-humble-rclpy on edge host
  4. export ARIA_UNITREE_HARDWARE=1 ARIA_CV_MODE=aruco ARIA_CLOUD_SYNC=1
  5. export ORBITAL_MTLS_CERT/KEY/CA → scripts/mtls/certs/client.*
  6. SROS2 keystore per docs/HARDWARE_GATE.md
  7. Schedule external security audit with package listed in HARDWARE_GATE.md
""")


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbital AI lab cutover smoke")
    parser.add_argument("--bench", action="store_true", help="run 10 Hz latency bench")
    parser.add_argument("--skip-gate", action="store_true")
    args = parser.parse_args()

    print("Orbital AI — lab cutover")
    check_mtls_certs()
    rc = 0 if args.skip_gate else run_soft_gate()
    run_aruco_edge_tick()
    if args.bench:
        run_latency_bench()
    print_lab_next()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
