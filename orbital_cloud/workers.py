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
# People keep this far from a vehicle's footprint (forks included) and walk around one that
# blocks their way for longer than _DETOUR_AFTER_S.
_VEHICLE_KEEP_M = 0.8
_DETOUR_AFTER_S = 1.0
_HORN_HOLD_S = 2.5   # after a forklift's horn, stay put this long so it can get by
_ESCAPE_S = 8.0
_ESCAPE_KEEP_M = 0.72
_HORN_CLEAR_M = 2.7   # from a honking forklift's centre: clear of its forks when it turns
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
    goal: Optional[tuple[float, float]] = None
    blocked_s: float = 0.0
    detour_at: float = -1e9
    hold_until: float = 0.0
    displaced: bool = False   # stepped off the route for a vehicle: re-plan before walking on
    escaping: bool = False    # walking out of a honking forklift's way
    escape_at: float = 0.0


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

    def tick(self, dt: float, now: Optional[float] = None, horns: Optional[dict] = None) -> None:
        """Walk everyone one step. ``horns`` maps a person to the footprint of a forklift that is
        waiting on them; they step out of its way, even mid-task."""
        from .objects import vehicle_boxes  # lazy: objects is the heavier module
        now = time.time() if now is None else now
        boxes = [b for _, b in vehicle_boxes(now=now)]
        for walker in self._walkers:
            current = worker_store.get(walker.id, now)
            if current is None:
                continue
            x, y = current.pose.x, current.pose.y
            horn = (horns or {}).get(walker.id)
            if walker.escaping and now - walker.escape_at > _ESCAPE_S:
                walker.escaping, walker.route = False, []
            if horn is not None and (not walker.escaping or walker.blocked_s > 0):
                escape = _escape_route(boxes or [horn], horn, x, y)
                if escape:
                    walker.route, walker.escaping, walker.blocked_s = escape, True, 0.0
                    walker.escape_at = now
                    walker.hold_until = walker.dwell_until = 0.0
                else:
                    walker.hold_until = now + _HORN_HOLD_S
                    x, y = _step_away(boxes or [horn], x, y, _WALK_MPS * dt, from_centre=horn)
                    walker.displaced = True
            if now < walker.hold_until:
                worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                continue
            if not walker.route:
                if now < walker.dwell_until:
                    worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                    continue
                walker.goal = _beside(random.choice(_stations()))
                walker.route = pathing.plan((x, y), walker.goal) or [walker.goal]
            elif walker.displaced and walker.goal and not walker.escaping:
                walker.route = pathing.plan((x, y), walker.goal) or walker.route
            walker.displaced = False
            tx, ty = walker.route[0]
            d = math.hypot(tx - x, ty - y)
            step = _WALK_MPS * dt
            nx, ny = (tx, ty) if d <= step else (x + (tx - x) / d * step, y + (ty - y) / d * step)
            if _in_rack(nx, ny) and not _in_rack(x, y):
                # A route snapped onto the grid can clip a rack corner; never walk through one.
                walker.escaping = False
                walker.route = (pathing.plan((x, y), walker.goal) if walker.goal else []) or []
                worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                continue
            # Escaping a stopped forklift, people may pass a little closer (it waits for them).
            keep = _ESCAPE_KEEP_M if walker.escaping else _VEHICLE_KEEP_M

            def too_close(px: float, py: float):
                return next((b for b in boxes if b.dist(px, py) < keep and b.dist(px, py) < b.dist(x, y) - 1e-3), None)

            hit = too_close(nx, ny)
            if hit is not None and d > step:
                # Slide along the vehicle's edge rather than freeze: same step, angled off.
                ux, uy = (tx - x) / d, (ty - y) / d
                for a in (0.26, -0.26, 0.52, -0.52, 0.79, -0.79):
                    c, s = math.cos(a), math.sin(a)
                    sx, sy = x + (ux * c - uy * s) * step, y + (ux * s + uy * c) * step
                    if too_close(sx, sy) is None and not _in_rack(sx, sy):
                        x, y, hit = sx, sy, None
                        walker.blocked_s = 0.0
                        break
                if hit is None:
                    worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                    continue
            if hit is not None:
                walker.blocked_s += dt
                # Too close to a vehicle: step back out of its way before anything else.
                if hit.dist(x, y) < _VEHICLE_KEEP_M + 0.3:
                    x, y = _step_away(boxes, x, y, step)
                    walker.displaced = True
                if walker.blocked_s >= _DETOUR_AFTER_S and now - walker.detour_at >= 3.0 and walker.goal:
                    walker.detour_at = now
                    rect = hit.bounds(_VEHICLE_KEEP_M + 0.3)
                    gx, gy = walker.goal
                    if not (rect[0] <= gx <= rect[2] and rect[1] <= gy <= rect[3]):
                        walker.route = pathing.plan((x, y), walker.goal, avoid=[rect]) or walker.route
                worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)
                continue
            walker.blocked_s = 0.0
            x, y = nx, ny
            if d <= step:
                walker.route.pop(0)
                if not walker.route:
                    if walker.escaping:
                        walker.escaping, walker.hold_until = False, now + _HORN_HOLD_S
                    else:
                        walker.dwell_until = now + random.uniform(*_DWELL_S)
            worker_store.upsert(walker.id, _pose_in(current, x, y), source="sim", now=now)


def _escape_route(boxes, horn, x: float, y: float) -> list[tuple[float, float]]:
    """A short walk to the nearest spot well clear of every vehicle and outside the circle the
    honking forklift's forks sweep when it turns, routed around it. Empty if there is none."""
    w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
    avoid = [horn.bounds(_VEHICLE_KEEP_M - 0.2)]
    best: list[tuple[float, float]] = []
    best_len = math.inf
    for r in (1.5, 2.5, 3.5, 4.5):
        for k in range(16):
            a = k * math.pi / 8
            gx, gy = x + math.cos(a) * r, y + math.sin(a) * r
            if not (0.5 <= gx <= w - 0.5 and 0.5 <= gy <= h - 0.5) or pathing._point_blocked(gx, gy):
                continue
            if min(b.dist(gx, gy) for b in boxes) < _VEHICLE_KEEP_M + 1.0 \
                    or math.hypot(gx - horn.cx, gy - horn.cy) < _HORN_CLEAR_M:
                continue
            # Straight along a rack face first: the planner's grid keeps robot clearance from
            # racks, which a person pressed against one doesn't need.
            route = [(gx, gy)]
            if not _route_clear(boxes, x, y, route):
                route = pathing.plan((x, y), (gx, gy), avoid=avoid)
                if not route or not _route_clear(boxes, x, y, route):
                    continue
            length = sum(math.dist(p, q) for p, q in zip([(x, y), *route], route))
            if length < best_len:
                best, best_len = route, length
        if best:
            break
    return best


def _route_clear(boxes, x: float, y: float, route: list[tuple[float, float]]) -> bool:
    """The walk never passes closer to a vehicle than the person already is (or the escape
    margin, if that's larger), and never through a rack."""
    floor = min(min(b.dist(x, y) for b in boxes), _ESCAPE_KEEP_M) - 1e-3
    for (ax, ay), (bx, by) in zip([(x, y), *route], route):
        n = max(1, int(math.hypot(bx - ax, by - ay) / 0.1))
        for i in range(1, n + 1):
            px, py = ax + (bx - ax) * i / n, ay + (by - ay) * i / n
            if _in_rack(px, py) or min(b.dist(px, py) for b in boxes) < floor:
                return False
    return True


def _in_rack(x: float, y: float, margin: float = 0.0) -> bool:
    """A person can stand right at a rack face, closer than the robots' planning clearance."""
    return any(r["x"] - margin < x < r["x"] + r["w"] + margin and r["y"] - margin < y < r["y"] + r["h"] + margin
               for r in WAREHOUSE.get("racks", []))


def _step_away(boxes, x: float, y: float, step: float, from_centre=None) -> tuple[float, float]:
    """One step in whichever direction best opens the gap to the nearest vehicle (there may be
    one on each side), avoiding racks and walls. With ``from_centre`` (a honking forklift), step
    away from its centre instead, so the person clears the arc its forks sweep when it turns,
    without stepping toward any vehicle. Stays put if nothing helps."""
    w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
    gap0 = min(b.dist(x, y) for b in boxes)

    def score(px: float, py: float) -> float:
        if from_centre is not None:
            return math.hypot(px - from_centre.cx, py - from_centre.cy)
        return min(b.dist(px, py) for b in boxes)

    best, best_score = (x, y), score(x, y)
    for k in range(16):
        a = k * math.pi / 8
        nx, ny = x + math.cos(a) * step, y + math.sin(a) * step
        if not (0.3 <= nx <= w - 0.3 and 0.3 <= ny <= h - 0.3) or _in_rack(nx, ny):
            continue
        if min(b.dist(nx, ny) for b in boxes) < min(gap0, _VEHICLE_KEEP_M):
            continue
        if score(nx, ny) > best_score:
            best, best_score = (nx, ny), score(nx, ny)
    return best


def _pose_in(w: Worker, x: float, y: float) -> WorkerPoseIn:
    return WorkerPoseIn(x=x, y=y, display_name=w.display_name, role=w.role, status=w.status)


def _beside(station: tuple[float, float]) -> tuple[float, float]:
    """A free spot about a metre from a station, so people work next to robots, not on top
    of the point robots drive to."""
    sx, sy = station
    w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
    for dx, dy in random.sample([(1.1, 0), (-1.1, 0), (0, 1.1), (0, -1.1)], 4):
        x, y = sx + dx, sy + dy
        if 0.3 <= x <= w - 0.3 and 0.3 <= y <= h - 0.3 and not pathing._point_blocked(x, y):
            return x, y
    return station


def _stations() -> list[tuple[float, float]]:
    points = [(float(s["x"]), float(s["y"])) for s in WAREHOUSE.get("stations", [])]
    return points or [(WAREHOUSE["width_m"] / 2, WAREHOUSE["height_m"] / 2)]


sim_crowd = SimCrowd()
