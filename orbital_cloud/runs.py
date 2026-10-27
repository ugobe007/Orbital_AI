"""Sprint K — recorded task runs, Golden Runs, and run scoring.

A *run* is one robot doing one named task (a station-to-station leg such as ``Aisle AB → Dock``):
camera ground truth, the robot's own pose estimate, drift and state, sampled over time. The
operator marks one run per (vendor, task) as the **golden run**; every later run of that task is
scored against it on path deviation, cycle time and recoveries, so a deployment can be validated
("is today's run as good as the reference?") and the runs exported as training data
(``scripts/export_dataset.py``).

Runs come from the simulator's recorder or are uploaded by an edge
(``POST /api/v1/runs/{facility}``). Recent runs live in memory; golden runs are persisted when
``ORBITAL_DB_PATH`` is set, so the reference survives a restart.
"""
from __future__ import annotations

import math
import os
import threading
import time
import uuid
from collections import OrderedDict
from typing import Iterable, Optional

from . import persistence
from .config import settings
from .models import Run, RunIn, RunOutcome, RunSample, RunScore, RunSummary

MAX_DEV_M = float(os.getenv("ORBITAL_GOLDEN_MAX_DEV_M", "0.5") or "0.5")
SLOW_RATIO = float(os.getenv("ORBITAL_GOLDEN_SLOW_RATIO", "1.25") or "1.25")
_MAX_RUNS = int(os.getenv("ORBITAL_RUNS_MAX", "200") or "200")
# A RunSample costs ~1 KB in memory; the cloud runs on a 256 MB machine, so recent runs share
# one sample budget and a single run (e.g. a robot latched by an E-Stop mid-leg) is capped.
_MAX_SAMPLES = int(os.getenv("ORBITAL_RUN_MAX_SAMPLES", "1500") or "1500")
_MAX_TOTAL_SAMPLES = int(os.getenv("ORBITAL_RUNS_MAX_TOTAL_SAMPLES", "40000") or "40000")
_SAMPLE_EVERY_S = 0.2


def golden_key(vendor: str, task_key: str) -> str:
    return f"{vendor}|{task_key}"


# ── Scoring ───────────────────────────────────────────────────────────────────

def recoveries(samples: Iterable[RunSample]) -> int:
    """Times the robot went into a halt during the run (each needed a recovery)."""
    count, prev = 0, None
    for s in samples:
        if s.state == "halted" and prev != "halted":
            count += 1
        prev = s.state
    return count


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dist_to_path(px: float, py: float, path: list[tuple[float, float]]) -> float:
    if len(path) == 1:
        return math.hypot(px - path[0][0], py - path[0][1])
    return min(_seg_dist(px, py, *path[i], *path[i + 1]) for i in range(len(path) - 1))


def path_deviation(run: list[RunSample], golden: list[RunSample]) -> tuple[float, float]:
    """(mean, max) distance between the two ground-truth paths, measured both ways so a run
    that cuts a corner *or* wanders off is caught."""
    a = [(s.x, s.y) for s in run]
    b = [(s.x, s.y) for s in golden]
    there = [_dist_to_path(x, y, b) for x, y in a]
    back = [_dist_to_path(x, y, a) for x, y in b]
    both = there + back
    return sum(both) / len(both), max(both)


def score(run: RunIn, golden: Optional[Run]) -> RunScore:
    cycle = run.ended_at - run.started_at
    rec = recoveries(run.samples)
    max_drift = max((s.drift_m for s in run.samples), default=0.0)
    out = RunScore(verdict="pass", cycle_time_s=round(cycle, 2), recoveries=rec, max_drift_m=round(max_drift, 3))
    reasons: list[tuple[str, str]] = []
    if run.outcome == RunOutcome.ABORTED:
        reasons.append(("aborted", "Pre-empted before it finished (new mission or operator override)."))
    elif run.outcome == RunOutcome.FAILED:
        reasons.append(("failed", "The robot did not finish the task."))
    if golden is None:
        out.verdict = reasons[0][0] if reasons else "no_golden"
        out.reasons = [r for _, r in reasons] or ["No golden run for this task yet."]
        return out

    g_cycle = golden.ended_at - golden.started_at
    g_rec = recoveries(golden.samples)
    mean_dev, max_dev = path_deviation(run.samples, golden.samples)
    out.golden_id = golden.id
    out.golden_cycle_time_s = round(g_cycle, 2)
    out.cycle_ratio = round(cycle / g_cycle, 3) if g_cycle > 0 else None
    out.mean_dev_m = round(mean_dev, 3)
    out.max_dev_m = round(max_dev, 3)
    out.golden_recoveries = g_rec
    if max_dev > MAX_DEV_M:
        reasons.append(("deviated", f"Path strayed up to {max_dev:.2f} m from the golden run "
                                    f"(limit {MAX_DEV_M:.2f} m)."))
    if out.cycle_ratio is not None and out.cycle_ratio > SLOW_RATIO:
        reasons.append(("slow", f"Took {cycle:.1f} s vs {g_cycle:.1f} s golden "
                                f"({(out.cycle_ratio - 1) * 100:.0f}% slower)."))
    if rec > g_rec:
        reasons.append(("unstable", f"{rec} safety halt{'s' if rec != 1 else ''} vs {g_rec} in the golden run."))
    if reasons:
        out.verdict = reasons[0][0]
        out.reasons = [r for _, r in reasons]
    return out


# ── Store ─────────────────────────────────────────────────────────────────────

class RunStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: "OrderedDict[str, Run]" = OrderedDict()
        self._golden: dict[str, Run] = {}
        self._sample_count = 0
        self.version = 0
        self._load_golden()

    def _load_golden(self) -> None:
        persistence.init_db()
        for data in persistence.load_golden_runs():
            try:
                run = Run.model_validate_json(data)
            except Exception:  # noqa: BLE001 — skip a corrupt row rather than crash boot
                continue
            self._golden[golden_key(run.vendor, run.task_key)] = run

    def add(self, body: RunIn, facility_id: Optional[str] = None, *, auto_golden: bool = False) -> Run:
        run = Run(**body.model_dump(), id=f"run-{uuid.uuid4().hex[:10]}",
                  facility_id=facility_id or settings.facility_id)
        with self._lock:
            self._runs[run.id] = run
            self._sample_count += len(run.samples)
            while len(self._runs) > 1 and (len(self._runs) > _MAX_RUNS
                                           or self._sample_count > _MAX_TOTAL_SAMPLES):
                _, old = self._runs.popitem(last=False)
                self._sample_count -= len(old.samples)
            key = golden_key(run.vendor, run.task_key)
            if (auto_golden and key not in self._golden and run.outcome == RunOutcome.COMPLETE
                    and recoveries(run.samples) == 0):
                self._set_golden(run)
            self.version += 1
        return run

    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            return self._runs.get(run_id) or next((g for g in self._golden.values() if g.id == run_id), None)

    def golden_for(self, vendor: str, task_key: str) -> Optional[Run]:
        return self._golden.get(golden_key(vendor, task_key))

    def set_golden(self, run_id: str) -> Optional[Run]:
        with self._lock:
            run = self.get(run_id)
            if run is None:
                return None
            self._set_golden(run)
            return run

    def _set_golden(self, run: Run) -> None:
        key = golden_key(run.vendor, run.task_key)
        self._golden[key] = run
        self.version += 1
        persistence.save_golden_run(key, run.model_dump_json())

    def clear_golden(self, vendor: str, task_key: str) -> bool:
        key = golden_key(vendor, task_key)
        with self._lock:
            if self._golden.pop(key, None) is None:
                return False
            self.version += 1
        persistence.delete_golden_run(key)
        return True

    def summary(self, run: Run) -> RunSummary:
        golden = self.golden_for(run.vendor, run.task_key)
        is_golden = golden is not None and golden.id == run.id
        if is_golden:
            run_score = score(run, None).model_copy(update={"verdict": "golden", "reasons": []})
        else:
            run_score = score(run, golden)
        return RunSummary(
            id=run.id, robot_id=run.robot_id, vendor=run.vendor, task_key=run.task_key,
            started_at=run.started_at, ended_at=run.ended_at, outcome=run.outcome, source=run.source,
            samples=len(run.samples), golden=is_golden, score=run_score,
        )

    def list(self, *, limit: int = 50, task_key: Optional[str] = None, robot_id: Optional[str] = None,
             vendor: Optional[str] = None) -> list[Run]:
        with self._lock:
            runs = list(reversed(self._runs.values()))
        out = [r for r in runs
               if (task_key is None or r.task_key == task_key)
               and (robot_id is None or r.robot_id == robot_id)
               and (vendor is None or r.vendor == vendor)]
        return out[:max(0, limit)]

    def golden_runs(self) -> list[Run]:
        with self._lock:
            return sorted(self._golden.values(), key=lambda r: (r.task_key, r.vendor))

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._sample_count = 0
            for key in list(self._golden):
                persistence.delete_golden_run(key)
            self._golden.clear()
            self.version += 1


run_store = RunStore()


def runs_message(limit: int = 30) -> dict:
    return {"type": "runs",
            "runs": [run_store.summary(r).model_dump(mode="json") for r in run_store.list(limit=limit)]}


# ── Recorder (the simulator's runs; an edge does the same and uploads) ────────

class _Open:
    __slots__ = ("robot_id", "vendor", "task_key", "started_at", "samples", "last_t")

    def __init__(self, robot_id: str, vendor: str, task_key: str, now: float) -> None:
        self.robot_id, self.vendor, self.task_key = robot_id, vendor, task_key
        self.started_at = now
        self.samples: list[RunSample] = []
        self.last_t = -math.inf


class RunRecorder:
    def __init__(self, store: RunStore) -> None:
        self._store = store
        self._open: dict[str, _Open] = {}

    def start(self, robot, task_key: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.abort(robot.id, now)
        rec = _Open(robot.id, robot.vendor, task_key, now)
        self._open[robot.id] = rec
        self.sample(robot, now, force=True)

    def recording(self, robot_id: str) -> bool:
        return robot_id in self._open

    def sample(self, robot, now: Optional[float] = None, *, force: bool = False) -> None:
        rec = self._open.get(robot.id)
        if rec is None:
            return
        now = time.time() if now is None else now
        t = now - rec.started_at
        if not force and t - rec.last_t < _SAMPLE_EVERY_S:
            return
        if len(rec.samples) >= _MAX_SAMPLES:
            return
        ext, ins = robot.pose_external, robot.pose_internal
        state = robot.state.value if hasattr(robot.state, "value") else str(robot.state)
        rec.samples.append(RunSample(
            t=round(max(0.0, t), 3), x=round(ext.x, 4), y=round(ext.y, 4), theta=round(ext.theta, 4),
            ix=round(ins.x, 4), iy=round(ins.y, 4), itheta=round(ins.theta, 4),
            drift_m=round(robot.drift_delta_m, 4), speed_mps=round(robot.speed_mps, 3), state=state,
        ))
        rec.last_t = t

    def finish(self, robot, outcome: RunOutcome = RunOutcome.COMPLETE,
               now: Optional[float] = None) -> Optional[Run]:
        if robot.id not in self._open:
            return None
        now = time.time() if now is None else now
        self.sample(robot, now, force=True)
        return self._close(robot.id, outcome, now)

    def abort(self, robot_id: str, now: Optional[float] = None) -> Optional[Run]:
        if robot_id not in self._open:
            return None
        return self._close(robot_id, RunOutcome.ABORTED, time.time() if now is None else now)

    def _close(self, robot_id: str, outcome: RunOutcome, now: float) -> Optional[Run]:
        rec = self._open.pop(robot_id)
        if len(rec.samples) < 2:
            return None
        body = RunIn(robot_id=rec.robot_id, vendor=rec.vendor, task_key=rec.task_key,
                     started_at=rec.started_at, ended_at=now, outcome=outcome,
                     samples=rec.samples, source="sim")
        return self._store.add(body, auto_golden=True)

    def clear(self) -> None:
        self._open.clear()


recorder = RunRecorder(run_store)
