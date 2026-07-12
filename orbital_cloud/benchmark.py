"""Benchmark Library report generator (Module 5).

Turns the per-vendor rollups the store computes into a human-readable brief — the
same numbers Orbital AI licenses back to OEMs.
"""
from __future__ import annotations

from .store import Store


def render_benchmark_report(store: Store) -> str:
    lines = ["Orbital AI — Benchmark Library (cross-vendor drift performance)", ""]
    rows = [store.benchmark(v) for v in store.vendors()]
    # Rank by cleanest operation (lowest mean drift) so the strongest OEM leads.
    rows.sort(key=lambda r: r.mean_drift_m)
    if not rows:
        return "No vendors registered."
    for r in rows:
        mtbd = f"{r.mtbd_seconds}s" if r.mtbd_seconds is not None else "n/a"
        rec = f"{r.mean_recovery_latency_seconds}s" if r.mean_recovery_latency_seconds is not None else "n/a"
        score = r.env_degradation_score if r.env_degradation_score is not None else "n/a"
        lines.append(
            f"  • {r.vendor}: {r.robots} robot(s), {r.samples} samples | "
            f"mean drift {r.mean_drift_m}m, p95 {r.p95_drift_m}m | "
            f"MTBD {mtbd}, recovery {rec}, env-score {score}"
        )
    return "\n".join(lines)
