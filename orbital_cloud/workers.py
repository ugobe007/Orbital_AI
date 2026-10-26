"""People on the floor (Sprint G5–G7).

Workers are live-only: positions are kept in memory and never written to SQLite, and a
check-in that goes quiet for ``worker_stale_s`` drops off the map and out of the safety
check. Names are optional, so the default is pseudonymous.

Positions come from phone check-ins, badge readers, or the Digital Net's people detection
(``POST /api/v1/workers/{id}/pose``). The simulator walks a small crowd between stations so
the demo shows robots slowing and stopping for people.
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import pathing
from .config import WAREHOUSE, settings
from .models import Point, Worker, WorkerPoseIn, WorkerStatus

_WALK_MPS = 1.1
_DWELL_S = (4.0, 10.0)
_SIM_ROLES = ("picker", "supervisor", "maintenance")


class WorkerStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workers: dict[str, Worker] = {}

    def upsert(self, worker_id: str, body: WorkerPoseIn, *, source: str = "checkin",
               now: Optional[float] = None) -> Worker:
        now = time.time() if now is None else now
        worker = Worker(
            id=worker_id, display_name=body.display_name, role=body.role, status=body.status,
            pose=Point(x=body.x, y=body.y), last_seen=now, source=source,
        )
        with self._lock:
            self._workers[worker_id] = worker
        return worker

    def remove(self, worker_id: str) -> bool:
        with self._lock:
            return self._workers.pop(worker_id, None) is not None

    def get(self, worker_id: str, now: Optional[float] = None) -> Optional[Worker]:
        return next((w for w in self.list(now) if w.id == worker_id), None)

    def list(self, now: Optional[float] = None) -> list[Worker]:
        """Live workers; stale check-ins are dropped as a side effect."""
        now = time.time() if now is None else now
        with self._lock:
            for wid in [wid for wid, w in self._workers.items()
                        if w.source != "sim" and now - w.last_seen > settings.worker_stale_s]:
                del self._workers[wid]
            return sorted((w.model_copy() for w in self._workers.values()), key=lambda w: w.id)

    def nearest(self, x: float, y: float, *, on_shift_only: bool = False,
                now: Optional[float] = None) -> tuple[Optional[Worker], float]:
        best: Optional[Worker] = None
        best_d = math.inf
        for w in self.list(now):
            if on_shift_only and w.status != WorkerStatus.ON_SHIFT:
                continue
            d = math.hypot(w.pose.x - x, w.pose.y - y)
            if d < best_d:
                best, best_d = w, d
        return best, best_d

    def clear(self) -> None:
        with self._lock:
            self._workers.clear()


worker_store = WorkerStore()


def proximity_speed_cap(x: float, y: float, now: Optional[float] = None) -> Optional[float]:
    """Speed cap for a robot at (x, y) because a person is within the slow radius."""
    _, d = worker_store.nearest(x, y, now=now)
    return settings.human_slow_speed_mps if d <= settings.human_slow_radius_m else None


def person_within_stop_radius(x: float, y: float, *, release: bool = False,
                              now: Optional[float] = None) -> Optional[tuple[Worker, float]]:
    """The person inside the stop radius, if any. ``release`` widens the radius a little so a
    halted robot doesn't flicker between stop and go while someone hovers at the edge."""
    radius = settings.human_stop_radius_m + (0.4 if release else 0.0)
    worker, d = worker_store.nearest(x, y, now=now)
    return (worker, d) if worker is not None and d <= radius else None


def worker_label(w: Worker) -> str:
    return f"{w.display_name or w.id} ({w.role})"


# ── Simulated crowd ────────────────────────────────────────────────────────────

@dataclass
class _Walker:
    id: str
    role: str
    route: list[tuple[float, float]] = field(default_factory=list)
    dwell_until: float = 0.0


class SimCrowd:
    """A few simulated people who walk between stations and pause there to work."""

    def __init__(self) -> None:
        self._walkers: list[_Walker] = []

    def spawn(self, count: int) -> None:
        stations = _stations()
        self._walkers = []
        for i in range(max(0, count)):
            wid = f"wkr-{i + 1:02d}"
            x, y = random.choice(stations)
            worker_store.upsert(
                wid, WorkerPoseIn(x=x, y=y, role=_SIM_ROLES[i % len(_SIM_ROLES)]), source="sim",
            )
            self._walkers.append(_Walker(id=wid, role=_SIM_ROLES[i % len(_SIM_ROLES)],
                                         dwell_until=time.time() + random.uniform(*_DWELL_S)))

    def tick(self, dt: float, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for walker in self._walkers:
            current = worker_store.get(walker.id, now)
            if current is None:
                continue
            x, y = current.pose.x, current.pose.y
            if not walker.route:
                if now < walker.dwell_until:
                    worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                    continue
                goal = random.choice(_stations())
                walker.route = pathing.plan((x, y), goal) or [goal]
            tx, ty = walker.route[0]
            d = math.hypot(tx - x, ty - y)
            step = _WALK_MPS * dt
            if d <= step:
                x, y = tx, ty
                walker.route.pop(0)
                if not walker.route:
                    walker.dwell_until = now + random.uniform(*_DWELL_S)
            else:
                x, y = x + (tx - x) / d * step, y + (ty - y) / d * step
            worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)


def _pose_in(w: Worker, x: float, y: float) -> WorkerPoseIn:
    return WorkerPoseIn(x=x, y=y, display_name=w.display_name, role=w.role, status=w.status)


def _stations() -> list[tuple[float, float]]:
    points = [(float(s["x"]), float(s["y"])) for s in WAREHOUSE.get("stations", [])]
    return points or [(WAREHOUSE["width_m"] / 2, WAREHOUSE["height_m"] / 2)]


sim_crowd = SimCrowd()
