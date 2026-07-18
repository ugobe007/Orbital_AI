"""Sprint D2 — 10 Hz injection-loop latency bench (guide S1-08).

Measures wall-clock time for ``inject_waypoint`` over N ticks and reports whether
the loop stays within the 10 Hz budget (≤100 ms per inject p95).
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class LatencyReport:
    hz_target: float
    samples: int
    mean_ms: float
    p95_ms: float
    max_ms: float
    budget_ms: float
    pass_: bool
    timestamps_s: tuple[float, ...]

    def as_dict(self) -> dict:
        return {
            "hz_target": self.hz_target,
            "samples": self.samples,
            "mean_ms": self.mean_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
            "budget_ms": self.budget_ms,
            "pass": self.pass_,
        }


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


@dataclass
class LatencyBench:
    """Drive an adapter through inject cycles at a target rate."""

    hz: float = 10.0
    samples: int = 30
    waypoint: tuple[float, float] = (1.0, 0.0)

    @property
    def budget_ms(self) -> float:
        return 1000.0 / self.hz

    def run(self, adapter, *, robot_id: str | None = None) -> LatencyReport:
        rid = robot_id or getattr(adapter, "robot_id", "rbt-01")
        period = 1.0 / self.hz
        durations: list[float] = []
        for i in range(self.samples):
            tick_start = time.perf_counter()
            t0 = time.perf_counter()
            adapter.inject_waypoint(rid, self.waypoint)
            durations.append(time.perf_counter() - t0)
            # Pace the loop to the target Hz (sleep leftover of the period).
            elapsed = time.perf_counter() - tick_start
            remaining = period - elapsed
            if remaining > 0:
                time.sleep(remaining)

        ms = [d * 1000.0 for d in durations]
        p95 = _p95(ms)
        return LatencyReport(
            hz_target=self.hz,
            samples=len(ms),
            mean_ms=round(statistics.fmean(ms), 3),
            p95_ms=round(p95, 3),
            max_ms=round(max(ms), 3),
            budget_ms=round(self.budget_ms, 3),
            pass_=p95 <= self.budget_ms,
            timestamps_s=tuple(durations),
        )
