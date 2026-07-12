"""Orbital AI Cloud — the shared monitor+control service for StageGate and ReadyForRobots.

This package implements the software layer of the ARIA/Orbital AI platform:
  * Module 6 — Cloud Orchestration (edge<->cloud API: missions, trajectory, telemetry, alerts)
  * Module 7 — Fleet Management Dashboard (fleet, robot detail, tasks, alerts, benchmark)
  * Benchmark Library metrics (drift delta, MTBD, recovery latency, env degradation score)

The patent-core edge modules (CV pipeline, TF Hijack, fleet adapters, safety halt) live in
the separate hardware-bound `aria-core` repo. Here, a built-in simulator stands in for a real
ARIA Edge Node so the entire stack is demoable without cameras or robots.
"""

__version__ = "0.1.0"
