#!/usr/bin/env python3
"""Solve a camera-rig marker map and say what to fix (Sprint H).

Usage:
  python3 scripts/calibrate_rig.py testdata/calibration/rig_good.json
  python3 scripts/calibrate_rig.py captures/ --origin 0 --origin-pose 1.0:2.0 --anchor 3:12.5:2.0 --out marker_map.json
  python3 scripts/calibrate_rig.py captures.json --upload https://orbital-ai.io --facility sf-01

INPUT is either a captures JSON file (see ``aria_edge.calibration.captures_from_dict``) or a
directory of recorded ArUco frame JSONs (``testdata/aruco`` format), one frame per capture.
``--anchor ID:X:Y`` adds a tape-measured marker position; one anchor a few metres from the
origin pins the map's rotation. Exit code is 0 when the rig is clean, 1 when something needs
fixing, 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aria_edge.calibration import (  # noqa: E402
    DEFAULT_RESIDUAL_M,
    CalibrationReport,
    captures_from_frames,
)


def load(path: Path, origin: int | None, anchors: list[str], origin_pose: str | None = None) -> dict:
    if path.is_dir():
        frames = sorted(path.glob("*.json"))
        if not frames:
            raise ValueError(f"no *.json frames in {path}")
        caps = captures_from_frames(frames)
        if not caps:
            raise ValueError(f"no frame in {path} has any markers")
        data = {
            "origin_marker": origin if origin is not None else min(o.marker_id for c in caps for o in c.observations),
            "captures": [
                {"id": c.id,
                 "expected_pose": None if c.expected is None else
                 {"x": c.expected.x, "y": c.expected.y, "theta": c.expected.theta},
                 "observations": [{"marker_id": o.marker_id, "x": o.x, "y": o.y, "theta": o.theta}
                                  for o in c.observations]}
                for c in caps
            ],
        }
    else:
        data = json.loads(path.read_text())
        data.pop("truth", None)
        if origin is not None:
            data["origin_marker"] = origin
    if origin_pose:
        parts = [float(v) for v in origin_pose.split(":")]
        if len(parts) not in (2, 3):
            raise ValueError(f"--origin-pose wants X:Y or X:Y:THETA, got {origin_pose!r}")
        data["origin_pose"] = {"x": parts[0], "y": parts[1], "theta": parts[2] if len(parts) == 3 else 0.0}
    for spec in anchors:
        mid, x, y = spec.split(":")
        data.setdefault("anchors", []).append({"marker_id": int(mid), "x": float(x), "y": float(y)})
    return data


def print_report(report: CalibrationReport) -> None:
    print(f"Marker map: {len(report.marker_map())} markers, {len(report.captures)} captures, "
          f"RMS {report.rms_m * 100:.1f} cm (threshold {report.threshold_m * 100:.0f} cm)")
    for m in report.markers:
        if m.status != "ok":
            err = f"off by {m.max_m * 100:.1f} cm, " if m.status == "likely_error" else ""
            print(f"  marker {m.marker_id}: {m.status.replace('_', ' ')}"
                  f" ({err}{m.views} view{'s' if m.views != 1 else ''})")
    for c in report.captures:
        if c.status != "ok":
            print(f"  capture {c.id}: {c.status.replace('_', ' ')}")
    print("Rig is clean." if report.ok else "Fix these:")
    for hint in report.hints:
        print(f"  - {hint}")


def upload(base_url: str, facility: str, data: dict) -> dict:
    import httpx

    resp = httpx.post(f"{base_url.rstrip('/')}/api/v1/calibration/{facility}", json=data, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--origin", type=int, default=None, help="origin marker id (default: from the file, else lowest id)")
    ap.add_argument("--origin-pose", metavar="X:Y[:THETA]", help="floor position of the origin marker (default 0:0:0)")
    ap.add_argument("--anchor", action="append", default=[], metavar="ID:X:Y")
    ap.add_argument("--threshold", type=float, default=DEFAULT_RESIDUAL_M, help="residual in metres that flags a marker")
    ap.add_argument("--out", type=Path, help="write the marker map + report JSON here")
    ap.add_argument("--upload", metavar="URL", help="send the captures to an Orbital cloud for the dashboard")
    ap.add_argument("--facility", default="facility-sf-001")
    args = ap.parse_args(argv)

    from aria_edge.calibration import solve_dict

    try:
        data = load(args.input, args.origin, args.anchor, args.origin_pose)
        data["threshold_m"] = args.threshold
        report = solve_dict(data, threshold_m=args.threshold)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print_report(report)
    if args.out:
        args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
        print(f"Wrote {args.out}")
    if args.upload:
        try:
            upload(args.upload, args.facility, data)
        except Exception as exc:  # noqa: BLE001 — the local report above is still valid
            print(f"upload failed: {exc}", file=sys.stderr)
            return 2
        print(f"Uploaded to {args.upload} for {args.facility}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
