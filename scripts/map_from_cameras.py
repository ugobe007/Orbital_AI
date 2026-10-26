#!/usr/bin/env python3
"""Build the floor's blocked cells from the camera rig (Sprint J1–J2).

Usage:
  python3 scripts/map_from_cameras.py testdata/multiview/rig_8cam.json testdata/multiview/pallet_frames.json
  python3 scripts/map_from_cameras.py rig.json frames.json --out occupancy.json
  python3 scripts/map_from_cameras.py rig.json frames.json --upload https://orbital-ai.io --facility sf-01

RIG is a camera rig file (see ``aria_edge.multiview.load_rig``). FRAMES holds feature tracks
per frame — ``{"frames": [{"t": seconds, "tracks": {"feature-id": [["camera-id", u, v], ...]}}]}``
— from the edge's multi-view feature matcher. Each feature seen by two or more cameras is
placed in 3-D, the points are fused into a decaying voxel volume, and the slice between the
floor and robot height becomes the blocked cells the planner routes around.

Exit code is 0 when the cameras agree, 1 when too many features were rejected (a camera has
probably moved: re-run calibration), 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aria_edge.multiview import OccupancyVolume, load_rig, triangulate_tracks  # noqa: E402

MAX_REJECTED_SHARE = 0.25


def run(rig_path: Path, frames_path: Path, *, max_rms_px: float, cell_m: float, ttl_s: float) -> tuple[dict, dict]:
    rig = load_rig(rig_path)
    frames = json.loads(frames_path.read_text())["frames"]
    if not frames:
        raise ValueError("no frames")
    volume = OccupancyVolume()
    placed = rejected = 0
    worst: Counter[str] = Counter()
    rms: list[float] = []
    for frame in frames:
        tracks = {tid: [(str(c), float(u), float(v)) for c, u, v in s] for tid, s in frame["tracks"].items()}
        report = triangulate_tracks(rig, tracks, max_rms_px=max_rms_px)
        volume.add_points([tri.point for _, tri in report.points], now=float(frame["t"]))
        placed += len(report.points)
        rejected += len(report.rejected)
        rms.extend(tri.rms_px for _, tri in report.points)
        for reason in report.rejected.values():
            if "worst: camera " in reason:
                worst[reason.rsplit("worst: camera ", 1)[1].rstrip(")")] += 1
    now = float(frames[-1]["t"])
    payload = volume.payload(now, cell_m=cell_m, ttl_s=ttl_s)
    summary = {
        "cameras": len(rig), "frames": len(frames), "placed": placed, "rejected": rejected,
        "rms_px": round(sum(rms) / len(rms), 3) if rms else None,
        "worst_cameras": worst.most_common(3), "blocked_cells": len(payload["cells"]),
    }
    return payload, summary


def print_summary(s: dict, cell_m: float) -> None:
    total = s["placed"] + s["rejected"]
    print(f"{s['cameras']} cameras, {s['frames']} frames: placed {s['placed']} of {total} features in 3-D"
          + (f", mean reprojection error {s['rms_px']} px" if s["rms_px"] is not None else ""))
    if s["rejected"]:
        print(f"  {s['rejected']} rejected (seen by fewer than two cameras, or the views disagree)")
    for cam, n in s["worst_cameras"]:
        print(f"  - camera {cam} was the outlier for {n} features: check it has not been knocked")
    print(f"{s['blocked_cells']} floor cells blocked ({cell_m} m squares)")


def upload(base_url: str, facility: str, payload: dict) -> dict:
    import httpx

    resp = httpx.post(f"{base_url.rstrip('/')}/api/v1/occupancy/{facility}", json=payload, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rig", type=Path)
    ap.add_argument("frames", type=Path)
    ap.add_argument("--max-rms-px", type=float, default=3.0, help="reject a feature whose views disagree by more")
    ap.add_argument("--cell", type=float, default=0.25, help="floor cell size in metres")
    ap.add_argument("--ttl", type=float, default=3.0, help="seconds the cloud keeps the slice without a refresh")
    ap.add_argument("--out", type=Path, help="write the occupancy payload JSON here")
    ap.add_argument("--upload", metavar="URL", help="send the blocked cells to an Orbital cloud")
    ap.add_argument("--facility", default="facility-sf-001")
    args = ap.parse_args(argv)

    try:
        payload, summary = run(args.rig, args.frames, max_rms_px=args.max_rms_px, cell_m=args.cell, ttl_s=args.ttl)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print_summary(summary, args.cell)
    if args.out:
        args.out.write_text(json.dumps(payload) + "\n")
        print(f"Wrote {args.out}")
    if args.upload:
        try:
            upload(args.upload, args.facility, payload)
        except Exception as exc:  # noqa: BLE001 — the local result above is still valid
            print(f"upload failed: {exc}", file=sys.stderr)
            return 2
        print(f"Uploaded to {args.upload} for {args.facility}")
    total = summary["placed"] + summary["rejected"]
    return 1 if total and summary["rejected"] / total > MAX_REJECTED_SHARE else 0


if __name__ == "__main__":
    sys.exit(main())
