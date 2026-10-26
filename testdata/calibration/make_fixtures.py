#!/usr/bin/env python3
"""Regenerate the synthetic calibration fixtures in this folder.

A 4 x 3 grid of floor markers seen by eight overlapping views, with 3 mm / 0.005 rad noise.
Each fixture carries ``truth`` so tests can check the solver against ground truth.
"""
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
random.seed(7)

truth = {}
for j in range(3):
    for i in range(4):
        truth[4 * j + i] = (2.0 + i * 3.5, 2.0 + j * 4.0, random.uniform(-math.pi, math.pi))

VIEWS = [  # capture id, configured pose (x, y, yaw), markers in view
    ("cam-01", (3.5, 4.0, 0.10), [0, 1, 4, 5]),
    ("cam-02", (7.0, 4.0, -0.05), [1, 2, 5, 6]),
    ("cam-03", (10.5, 4.0, 1.60), [2, 3, 6, 7]),
    ("cam-04", (3.5, 8.0, 3.10), [4, 5, 8, 9]),
    ("cam-05", (7.0, 8.0, 0.70), [5, 6, 9, 10]),
    ("cam-06", (10.5, 8.0, -1.20), [6, 7, 10, 11]),
    ("cam-07", (5.0, 6.0, 0.30), [0, 4, 5, 8, 10]),
    ("cam-08", (9.0, 6.0, -0.40), [1, 3, 6, 11]),
]


def _local(pose, m):
    cx, cy, ct = pose
    mx, my, mt = truth[m]
    c, s = math.cos(-ct), math.sin(-ct)
    dx, dy = mx - cx, my - cy
    return c * dx - s * dy, s * dx + c * dy, math.atan2(math.sin(mt - ct), math.cos(mt - ct))


def captures(bump=None, moved=None):
    out = []
    for cid, pose, markers in VIEWS:
        real = pose
        if moved and moved[0] == cid:
            real = (pose[0] + moved[1], pose[1], pose[2])
        obs = []
        for m in markers:
            x, y, t = _local(real, m)
            x += random.gauss(0, 0.003)
            y += random.gauss(0, 0.003)
            t += random.gauss(0, 0.005)
            if bump and bump[:2] == (cid, m):
                x += bump[2]
            obs.append({"marker_id": m, "x": round(x, 5), "y": round(y, 5), "theta": round(t, 5)})
        out.append({"id": cid, "expected_pose": {"x": pose[0], "y": pose[1], "theta": pose[2]},
                    "observations": obs})
    return out


def write(name, caps):
    base = {
        "origin_marker": 0,
        "origin_pose": {"x": truth[0][0], "y": truth[0][1], "theta": truth[0][2]},
        "anchors": [{"marker_id": 3, "x": truth[3][0], "y": truth[3][1]}],
        "truth": {str(k): {"x": v[0], "y": v[1], "theta": v[2]} for k, v in truth.items()},
        "captures": caps,
    }
    (HERE / f"{name}.json").write_text(json.dumps(base, indent=1) + "\n")


if __name__ == "__main__":
    write("rig_good", captures())
    write("rig_bumped_marker", captures(bump=("cam-05", 9, 0.15)))
    write("rig_moved_camera", captures(moved=("cam-03", 0.10)))
    split = captures()
    split.append({"id": "cam-09", "observations": [
        {"marker_id": 20, "x": 0.5, "y": 0.2, "theta": 0.0},
        {"marker_id": 21, "x": -0.6, "y": 0.4, "theta": 0.1},
    ]})
    write("rig_split", split)
