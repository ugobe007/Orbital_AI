#!/usr/bin/env python3
"""Export recorded task runs as a training / validation dataset (Sprint K3).

Usage:
  python3 scripts/export_dataset.py --url https://orbital-ai.io --out datasets/sf-001
  python3 scripts/export_dataset.py --url http://localhost:8090 --task "Aisle AB → Dock" --out ds/
  python3 scripts/export_dataset.py --input runs.json --out ds/          # offline: a JSON list of runs

Layout (LeRobot v2 style, JSONL instead of parquet so it needs nothing beyond the stdlib):

  meta/info.json          dataset description, fps, feature names and shapes
  meta/tasks.jsonl        {"task_index", "task"}
  meta/episodes.jsonl     {"episode_index", "tasks", "length", "robot_id", "vendor", "outcome",
                           "golden", "verdict", "run_id", ...}
  data/chunk-000/episode_000000.jsonl
                          one line per step: timestamp, frame_index, episode_index, task_index,
                          observation.state (robot's own x, y, theta), observation.ground_truth
                          (camera x, y, theta), observation.drift_m, action (vx, vy, omega to the
                          next step), next.done

Set ORBITAL_TOKEN to a viewer (or higher) bearer token when the cloud enforces RBAC.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

CHUNK = 1000
FEATURES = {
    "observation.state": {"dtype": "float32", "shape": [3], "names": ["x", "y", "theta"]},
    "observation.ground_truth": {"dtype": "float32", "shape": [3], "names": ["x", "y", "theta"]},
    "observation.drift_m": {"dtype": "float32", "shape": [1]},
    "action": {"dtype": "float32", "shape": [3], "names": ["vx", "vy", "omega"]},
    "timestamp": {"dtype": "float32", "shape": [1]},
    "frame_index": {"dtype": "int64", "shape": [1]},
    "episode_index": {"dtype": "int64", "shape": [1]},
    "task_index": {"dtype": "int64", "shape": [1]},
    "next.done": {"dtype": "bool", "shape": [1]},
}


def fetch_runs(url: str, token: Optional[str], task: Optional[str], robot: Optional[str],
               limit: int, golden_only: bool) -> list[dict]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=20.0) as http:
        if golden_only:
            summaries = http.get("/api/dashboard/runs/golden").raise_for_status().json()
            summaries = [s for s in summaries if (task is None or s["task_key"] == task)
                         and (robot is None or s["robot_id"] == robot)]
        else:
            params = {"limit": limit, **({"task_key": task} if task else {}), **({"robot_id": robot} if robot else {})}
            summaries = http.get("/api/dashboard/runs", params=params).raise_for_status().json()
        runs = []
        for s in summaries:
            detail = http.get(f"/api/dashboard/runs/{s['id']}").raise_for_status().json()
            runs.append({**detail["run"], "summary": detail["summary"]})
    return runs


def _action(a: dict, b: Optional[dict]) -> list[float]:
    if b is None or b["t"] <= a["t"]:
        return [0.0, 0.0, 0.0]
    dt = b["t"] - a["t"]
    dtheta = math.atan2(math.sin(b["theta"] - a["theta"]), math.cos(b["theta"] - a["theta"]))
    return [round((b["x"] - a["x"]) / dt, 4), round((b["y"] - a["y"]) / dt, 4), round(dtheta / dt, 4)]


def _fps(runs: list[dict]) -> float:
    gaps = [b["t"] - a["t"] for r in runs for a, b in zip(r["samples"], r["samples"][1:]) if b["t"] > a["t"]]
    if not gaps:
        return 0.0
    gaps.sort()
    return round(1.0 / gaps[len(gaps) // 2], 2)


def export(runs: list[dict], out: Path) -> dict:
    runs = sorted(runs, key=lambda r: r["started_at"])
    tasks: dict[str, int] = {}
    (out / "meta").mkdir(parents=True, exist_ok=True)
    total = 0
    with (out / "meta" / "episodes.jsonl").open("w") as episodes:
        for ep, run in enumerate(runs):
            task_index = tasks.setdefault(run["task_key"], len(tasks))
            path = out / "data" / f"chunk-{ep // CHUNK:03d}" / f"episode_{ep:06d}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            samples = run["samples"]
            with path.open("w") as f:
                for i, s in enumerate(samples):
                    nxt = samples[i + 1] if i + 1 < len(samples) else None
                    own = [s.get("ix"), s.get("iy"), s.get("itheta")]
                    f.write(json.dumps({
                        "timestamp": s["t"], "frame_index": i, "episode_index": ep, "task_index": task_index,
                        "observation.state": own if None not in own else [s["x"], s["y"], s["theta"]],
                        "observation.ground_truth": [s["x"], s["y"], s["theta"]],
                        "observation.drift_m": s.get("drift_m", 0.0),
                        "action": _action(s, nxt),
                        "robot_state": s.get("state", "active"),
                        "next.done": nxt is None,
                    }, ensure_ascii=False) + "\n")
            summary = run.get("summary") or {}
            score = summary.get("score") or {}
            episodes.write(json.dumps({
                "episode_index": ep, "tasks": [run["task_key"]], "length": len(samples),
                "run_id": run.get("id"), "robot_id": run["robot_id"], "vendor": run.get("vendor", ""),
                "facility_id": run.get("facility_id"), "source": run.get("source"),
                "outcome": run.get("outcome", "complete"), "golden": bool(summary.get("golden")),
                "verdict": score.get("verdict"), "cycle_time_s": round(run["ended_at"] - run["started_at"], 3),
                "max_dev_m": score.get("max_dev_m"), "recoveries": score.get("recoveries"),
            }, ensure_ascii=False) + "\n")
            total += len(samples)
    with (out / "meta" / "tasks.jsonl").open("w") as f:
        for task, idx in tasks.items():
            f.write(json.dumps({"task_index": idx, "task": task}, ensure_ascii=False) + "\n")
    info = {
        "codebase_version": "orbital-lerobot-v2-jsonl",
        "robot_type": sorted({r.get("vendor", "") for r in runs}),
        "total_episodes": len(runs), "total_frames": total, "total_tasks": len(tasks),
        "chunks_size": CHUNK, "fps": _fps(runs),
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.jsonl",
        "features": FEATURES,
        "notes": "observation.state is the robot's own (SLAM) pose; observation.ground_truth is "
                 "Orbital's overhead-camera pose. action is the finite-difference velocity to the next step.",
    }
    (out / "meta" / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return info


def load_input(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    runs: Iterable[dict] = data.get("runs", []) if isinstance(data, dict) else data
    return [r for r in runs if len(r.get("samples") or []) >= 2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Orbital cloud base URL")
    src.add_argument("--input", type=Path, help="JSON file with a list of runs (offline)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--task", help="only this task key, e.g. 'Aisle AB → Dock'")
    ap.add_argument("--robot", help="only this robot id")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--golden-only", action="store_true", help="export only the golden runs")
    args = ap.parse_args(argv)

    try:
        if args.url:
            runs = fetch_runs(args.url, os.getenv("ORBITAL_TOKEN"), args.task, args.robot, args.limit, args.golden_only)
        else:
            runs = [r for r in load_input(args.input)
                    if (args.task is None or r["task_key"] == args.task)
                    and (args.robot is None or r["robot_id"] == args.robot)]
    except Exception as exc:  # noqa: BLE001 — network, HTTP status or JSON errors all mean "no input"
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not runs:
        print("no runs to export", file=sys.stderr)
        return 1
    info = export(runs, args.out)
    print(f"Exported {info['total_episodes']} episodes, {info['total_frames']} frames, "
          f"{info['total_tasks']} tasks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
