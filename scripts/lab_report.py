#!/usr/bin/env python3
"""Turn a lab day into publishable numbers (fix #1: a real deployment with real numbers).

The headline claim is an A/B on the same robot, task and floor: runs where the robot navigates
on its own SLAM (``baseline``) against runs with Orbital's camera correction on (``orbital``),
both measured against the overhead cameras' ground truth. Upload each lab run to
``POST /api/v1/runs/{facility}`` with ``"source": "lab-baseline"`` or ``"source": "lab-orbital"``,
then:

  python3 scripts/lab_report.py --url https://orbital-ai.io --site "SF lab, Unitree G1" \\
      --latency bench.json --calibration marker_map.json --out docs/results/2026-11-03.md
  python3 scripts/lab_report.py --input runs.json --out results.md      # offline

Per condition it reports: runs and completion rate; position error (robot's own pose vs camera
ground truth) mean / p95 / max over every sample; end-point error at the drop-off; cycle time;
and safety halts per run. Runs from the simulator are labelled SIMULATION, never as lab numbers.
Exit code 0 when both conditions have at least ``--min-runs`` complete runs, 1 otherwise, 2 on
bad input.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _error(sample: dict) -> float:
    if sample.get("ix") is not None and sample.get("iy") is not None:
        return math.hypot(sample["ix"] - sample["x"], sample["iy"] - sample["y"])
    return float(sample.get("drift_m") or 0.0)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1)]


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _halts(samples: list[dict]) -> int:
    count, prev = 0, None
    for s in samples:
        if s.get("state") == "halted" and prev != "halted":
            count += 1
        prev = s.get("state")
    return count


def condition_stats(runs: list[dict]) -> dict:
    complete = [r for r in runs if r.get("outcome", "complete") == "complete"]
    errors = [_error(s) for r in complete for s in r["samples"]]
    end_errors = [_error(r["samples"][-1]) for r in complete]
    cycles = [r["ended_at"] - r["started_at"] for r in complete]
    return {
        "runs": len(runs),
        "complete": len(complete),
        "completion_rate": len(complete) / len(runs) if runs else None,
        "samples": len(errors),
        "error_mean_m": _mean(errors),
        "error_p95_m": _p95(errors) if errors else None,
        "error_max_m": max(errors) if errors else None,
        "end_error_mean_m": _mean(end_errors),
        "end_error_p95_m": _p95(end_errors) if end_errors else None,
        "cycle_mean_s": _mean(cycles),
        "halts_per_run": _mean([float(_halts(r["samples"])) for r in complete]),
        "robots": sorted({r["robot_id"] for r in runs}),
        "vendors": sorted({r.get("vendor", "") for r in runs} - {""}),
        "tasks": sorted({r["task_key"] for r in runs}),
    }


def _reduction(base: Optional[float], new: Optional[float]) -> Optional[float]:
    if base is None or new is None or base <= 0:
        return None
    return (base - new) / base


def build_report(runs: list[dict], *, baseline: str, orbital: str, min_runs: int,
                 latency: Optional[dict] = None, calibration: Optional[dict] = None,
                 site: str = "") -> dict:
    by = {"baseline": [r for r in runs if r.get("source") == baseline],
          "orbital": [r for r in runs if r.get("source") == orbital]}
    stats = {k: condition_stats(v) for k, v in by.items()}
    b, o = stats["baseline"], stats["orbital"]
    simulated = any(r.get("source") == "sim" for r in by["baseline"] + by["orbital"])
    enough = b["complete"] >= min_runs and o["complete"] >= min_runs
    return {
        "site": site,
        "generated_at": time.strftime("%Y-%m-%d %H:%M %Z"),
        "simulated": simulated,
        "sources": {"baseline": baseline, "orbital": orbital},
        "min_runs": min_runs,
        "enough_runs": enough,
        "baseline": b,
        "orbital": o,
        "improvement": {
            "error_mean": _reduction(b["error_mean_m"], o["error_mean_m"]),
            "error_p95": _reduction(b["error_p95_m"], o["error_p95_m"]),
            "end_error_mean": _reduction(b["end_error_mean_m"], o["end_error_mean_m"]),
            "halts_per_run": _reduction(b["halts_per_run"], o["halts_per_run"]),
            "cycle_time_ratio": (o["cycle_mean_s"] / b["cycle_mean_s"])
            if b["cycle_mean_s"] and o["cycle_mean_s"] else None,
        },
        "latency": latency,
        "calibration": None if calibration is None else {
            "ok": calibration.get("ok"), "rms_m": calibration.get("rms_m"),
            "markers": len(calibration.get("markers") or []),
            "captures": len(calibration.get("captures") or []),
        },
    }


def _cm(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f} cm"


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _change(reduction: Optional[float]) -> str:
    return "—" if reduction is None else f"{-reduction * 100:+.0f}%"


def _verb(reduction: Optional[float]) -> str:
    return "increased" if reduction is not None and reduction < 0 else "cut"


def _num(v: Optional[float], fmt: str = "{:.1f}") -> str:
    return "—" if v is None else fmt.format(v)


def render_markdown(rep: dict) -> str:
    b, o, imp = rep["baseline"], rep["orbital"], rep["improvement"]
    lines = [f"# Lab results{' — ' + rep['site'] if rep['site'] else ''}", ""]
    if rep["simulated"]:
        lines += ["> **SIMULATION — not lab results.** These runs came from the simulator; do not "
                  "publish them as deployment numbers.", ""]
    elif not rep["enough_runs"]:
        lines += [f"> **Not enough runs to publish.** Each condition needs at least {rep['min_runs']} "
                  f"complete runs (baseline {b['complete']}, Orbital {o['complete']}).", ""]
    lines += [
        f"Generated {rep['generated_at']}. Baseline = robot on its own SLAM (`{rep['sources']['baseline']}`); "
        f"Orbital = camera correction on (`{rep['sources']['orbital']}`). Error is the distance between "
        f"where the robot thinks it is and where the overhead cameras see it.",
        "",
    ]
    if b["complete"] and o["complete"] and imp["error_mean"] is not None:
        err, end = imp["error_mean"], imp["end_error_mean"]
        lines += [f"**Headline:** Orbital {_verb(err)} mean position error by {_pct(abs(err))} "
                  f"({_cm(b['error_mean_m'])} → {_cm(o['error_mean_m'])}) and {_verb(end)} drop-off error by "
                  f"{_pct(None if end is None else abs(end))} "
                  f"({_cm(b['end_error_mean_m'])} → {_cm(o['end_error_mean_m'])}).", ""]
    lines += [
        "| Metric | Baseline (SLAM only) | With Orbital | Change |",
        "|---|---|---|---|",
        f"| Runs (complete / total) | {b['complete']} / {b['runs']} | {o['complete']} / {o['runs']} | |",
        f"| Completion rate | {_pct(b['completion_rate'])} | {_pct(o['completion_rate'])} | |",
        f"| Position error, mean | {_cm(b['error_mean_m'])} | {_cm(o['error_mean_m'])} | {_change(imp['error_mean'])} |",
        f"| Position error, p95 | {_cm(b['error_p95_m'])} | {_cm(o['error_p95_m'])} | {_change(imp['error_p95'])} |",
        f"| Position error, max | {_cm(b['error_max_m'])} | {_cm(o['error_max_m'])} | |",
        f"| Drop-off error, mean | {_cm(b['end_error_mean_m'])} | {_cm(o['end_error_mean_m'])} | {_change(imp['end_error_mean'])} |",
        f"| Cycle time, mean | {_num(b['cycle_mean_s'])} s | {_num(o['cycle_mean_s'])} s | "
        f"{_num(imp['cycle_time_ratio'], '{:.2f}')}× |",
        f"| Safety halts per run | {_num(b['halts_per_run'], '{:.2f}')} | {_num(o['halts_per_run'], '{:.2f}')} | |",
        f"| Samples | {b['samples']} | {o['samples']} | |",
        "",
        f"Robots: {', '.join(sorted(set(b['robots']) | set(o['robots']))) or '—'}. "
        f"Vendors: {', '.join(sorted(set(b['vendors']) | set(o['vendors']))) or '—'}. "
        f"Tasks: {', '.join(sorted(set(b['tasks']) | set(o['tasks']))) or '—'}.",
        "",
    ]
    lat = rep.get("latency")
    if lat:
        lines += ["## Correction latency", "",
                  f"{lat.get('samples')} samples at {lat.get('hz_target')} Hz: mean {_num(lat.get('mean_ms'))} ms, "
                  f"p95 {_num(lat.get('p95_ms'))} ms, max {_num(lat.get('max_ms'))} ms "
                  f"(budget {lat.get('budget_ms')} ms) — **{'pass' if lat.get('pass') else 'fail'}**.", ""]
    cal = rep.get("calibration")
    if cal:
        lines += ["## Camera-rig calibration", "",
                  f"{cal['markers']} markers from {cal['captures']} captures, RMS {_cm(cal['rms_m'])} — "
                  f"**{'clean' if cal['ok'] else 'needs fixing'}**.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Orbital cloud base URL (reads /api/dashboard/runs)")
    src.add_argument("--input", type=Path, help="JSON list of runs (e.g. saved from the runs API)")
    ap.add_argument("--baseline", default="lab-baseline", help="run source for SLAM-only runs")
    ap.add_argument("--orbital", default="lab-orbital", help="run source for runs with Orbital correction")
    ap.add_argument("--min-runs", type=int, default=10)
    ap.add_argument("--latency", type=Path, help="LatencyBench JSON (as_dict output)")
    ap.add_argument("--calibration", type=Path, help="calibrate_rig.py --out report JSON")
    ap.add_argument("--site", default="")
    ap.add_argument("--out", type=Path, help="write the markdown results sheet here")
    ap.add_argument("--json", type=Path, help="write the raw numbers here")
    args = ap.parse_args(argv)

    try:
        if args.url:
            from scripts.export_dataset import fetch_runs

            runs = fetch_runs(args.url, os.getenv("ORBITAL_TOKEN"), None, None, 500, False)
        else:
            data = json.loads(args.input.read_text())
            runs = data.get("runs", []) if isinstance(data, dict) else data
        runs = [r for r in runs if len(r.get("samples") or []) >= 2]
        latency = json.loads(args.latency.read_text()) if args.latency else None
        calibration = json.loads(args.calibration.read_text()) if args.calibration else None
    except Exception as exc:  # noqa: BLE001 — network, HTTP status or JSON errors all mean "no input"
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rep = build_report(runs, baseline=args.baseline, orbital=args.orbital, min_runs=args.min_runs,
                       latency=latency, calibration=calibration, site=args.site)
    md = render_markdown(rep)
    print(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md + "\n")
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2) + "\n")
    return 0 if rep["enough_runs"] and not rep["simulated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
