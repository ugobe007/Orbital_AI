"""Tracked objects on the Global Spatial Map (Sprint J3/J4).

The camera rig sees more than robots: pallets, carts, forklifts, a dropped tote. The edge (or a
partner's shape/orientation/motion stack) publishes each one with a label, footprint, yaw and
velocity; the cloud keeps the live set per facility, expires what stops being reported, and
shows it on the map and to agents through MCP ``get_map``.
"""
from __future__ import annotations

import math
import random
import threading
import time
from typing import Optional

from .config import WAREHOUSE, settings
from .models import ObjectsIn, TrackedObject

_MAX_PER_FACILITY = 2000
_MAX_FACILITIES = 64


class ObjectStoreFull(RuntimeError):
    pass


class ObjectStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_facility: dict[str, dict[tuple[str, str], TrackedObject]] = {}

    def ingest(self, facility_id: str, batch: ObjectsIn, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            if facility_id not in self._by_facility and len(self._by_facility) >= _MAX_FACILITIES:
                for fid in list(self._by_facility):
                    self._expire(self._by_facility[fid], now)
                    if not self._by_facility[fid]:
                        del self._by_facility[fid]
                if len(self._by_facility) >= _MAX_FACILITIES:
                    raise ObjectStoreFull(f"tracking objects for {_MAX_FACILITIES} facilities already")
            live = self._by_facility.setdefault(facility_id, {})
            self._expire(live, now)
            if batch.replace:
                keep = {o.id for o in batch.objects}
                for key in [k for k in live if k[0] == batch.source and k[1] not in keep]:
                    del live[key]
            for o in batch.objects:
                key = (batch.source, o.id)
                prev = live.get(key)
                ttl = o.ttl_s or settings.object_ttl_s
                live[key] = TrackedObject(
                    **o.model_dump(), source=batch.source, facility_id=facility_id,
                    first_seen=prev.first_seen if prev else now, updated_at=now, expires_at=now + ttl,
                )
            if len(live) > _MAX_PER_FACILITY:
                for key in sorted(live, key=lambda k: live[k].updated_at)[:len(live) - _MAX_PER_FACILITY]:
                    del live[key]
        return len(batch.objects)

    def list(self, facility_id: str, now: Optional[float] = None) -> list[TrackedObject]:
        now = time.time() if now is None else now
        with self._lock:
            live = self._by_facility.get(facility_id, {})
            self._expire(live, now)
            return sorted(live.values(), key=lambda o: (o.label, o.id))

    def clear(self) -> None:
        with self._lock:
            self._by_facility.clear()

    @staticmethod
    def _expire(live: dict[tuple[str, str], TrackedObject], now: float) -> None:
        for key in [k for k, o in live.items() if o.expires_at <= now]:
            del live[key]


object_store = ObjectStore()


def objects_json(facility_id: Optional[str] = None) -> list[dict]:
    return [o.model_dump(mode="json") for o in object_store.list(facility_id or settings.facility_id)]


VEHICLE_LABELS = {"forklift"}
VEHICLE_SLOW_RADIUS_M = 2.5


def vehicle_speed_cap(x: float, y: float, facility_id: Optional[str] = None) -> Optional[float]:
    """Speed cap for a robot at (x, y) because a camera-tracked vehicle is close by."""
    for o in object_store.list(facility_id or settings.facility_id):
        if o.label in VEHICLE_LABELS and math.hypot(o.x - x, o.y - y) <= VEHICLE_SLOW_RADIUS_M:
            return settings.human_slow_speed_mps
    return None


def objects_message(facility_id: Optional[str] = None) -> dict:
    fid = facility_id or settings.facility_id
    return {"type": "objects", "facility_id": fid, "now": time.time(), "objects": objects_json(fid)}


# Where forklifts pull pallets from: (name, pallet x, pallet y, forklift x, forklift y). The
# forklift parks at its point facing the pallet, so the forks reach it.
PICK_FACES: list[tuple[str, float, float, float, float]] = [
    ("Rack G", 18.0, 5.1, 19.5, 5.1), ("Rack H", 18.0, 8.1, 19.5, 8.1),
    ("Rack E", 7.1, 8.0, 8.6, 8.0), ("Rack C", 11.8, 4.0, 13.3, 4.0),
    ("Rack F", 11.8, 10.0, 13.3, 10.0),
]
DOCK_LANES: list[tuple[str, float, float]] = [(d["id"], d["x"], d["y"]) for d in WAREHOUSE["dock_lanes"]]
PALLET_SIZE = {"length": 1.2, "width": 1.0, "height": 1.1}
FORKLIFT_SIZE = {"length": 2.2, "width": 1.1, "height": 2.1}
FORKLIFT_SPEED = 1.1        # m/s
LIFT_S = 2.5                # forks up / down
TRUCK_PICKUP_S = 35.0       # a delivered pallet waits this long before a truck takes it


class _Forklift:
    def __init__(self, fid: str, x: float, y: float) -> None:
        self.id, self.x, self.y, self.yaw = fid, x, y, 0.0
        self.vx = self.vy = 0.0
        self.phase = "idle"           # idle | to_pick | lifting | to_dock | lowering
        self.route: list[tuple[float, float]] = []
        self.pick: Optional[tuple[str, float, float, float, float]] = None
        self.lane: Optional[tuple[str, float, float]] = None
        self.pallet: Optional[str] = None
        self.wait = 0.0
        self.yielding = 0.0
        self.detoured = False

    @property
    def activity(self) -> str:
        if self.phase == "to_pick" and self.pick:
            return f"Heading to {self.pick[0]} for a pallet"
        if self.phase == "lifting" and self.pick:
            return f"Lifting a pallet at {self.pick[0]}"
        if self.phase == "to_dock" and self.lane:
            return f"Carrying a pallet to {self.lane[0]}"
        if self.phase == "lowering" and self.lane:
            return f"Setting the pallet down at {self.lane[0]}"
        return "Waiting for a free dock lane"

    def drive(self, dt: float, speed: float = FORKLIFT_SPEED) -> bool:
        """Advance along the route; True once the last waypoint is reached."""
        budget = speed * dt
        self.vx = self.vy = 0.0
        while self.route and budget > 0:
            tx, ty = self.route[0]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                self.yaw = math.atan2(dy, dx)
                self.vx, self.vy = speed * dx / dist, speed * dy / dist
            if dist <= budget:
                self.x, self.y = tx, ty
                budget -= dist
                self.route.pop(0)
            else:
                self.x += dx / dist * budget
                self.y += dy / dist * budget
                budget = 0
        if not self.route:
            self.vx = self.vy = 0.0
        return not self.route

    @property
    def fork_tip(self) -> tuple[float, float]:
        reach = FORKLIFT_SIZE["length"] / 2 + PALLET_SIZE["length"] / 2 - 0.2
        return self.x + math.cos(self.yaw) * reach, self.y + math.sin(self.yaw) * reach


class SimObjects:
    """Forklifts running pallets from the racks to the dock, the pallets themselves (waiting at a
    rack, on the forks, or delivered and waiting for a truck), and a cart crossing the bottom
    aisle. Routes go around the racks with the same planner the robots use."""

    def __init__(self, seed: int = 7) -> None:
        self.t = 0.0
        self.rng = random.Random(seed)
        self.forklifts = [_Forklift("forklift-1", 21.5, 8.1), _Forklift("forklift-2", 14.0, 1.2)]
        self.pallets: dict[str, dict] = {}     # id → {x, y, yaw, state, since}
        self._next_pallet = 1

    def _new_pallet(self, x: float, y: float, state: str) -> str:
        pid = f"pallet-{self._next_pallet}"
        self._next_pallet += 1
        self.pallets[pid] = {"x": x, "y": y, "yaw": 0.0, "state": state, "since": self.t}
        return pid

    def _route(self, f: _Forklift, goal: tuple[float, float]) -> list[tuple[float, float]]:
        from .pathing import plan  # lazy: pathing pulls in zones + occupancy
        return plan((f.x, f.y), goal) or [goal]

    def _approach(self, f: _Forklift, park: tuple[float, float],
                  face: tuple[float, float]) -> list[tuple[float, float]]:
        """Route to ``park``, then a hair toward ``face`` so the forks end up pointing at it."""
        dx, dy = face[0] - park[0], face[1] - park[1]
        d = math.hypot(dx, dy) or 1.0
        return [*self._route(f, park), (park[0] + dx / d * 0.01, park[1] + dy / d * 0.01)]

    def _assign(self, f: _Forklift) -> None:
        taken = {o.pick[0] for o in self.forklifts if o is not f and o.pick and o.phase in ("to_pick", "lifting")}
        free = [p for p in PICK_FACES if p[0] not in taken] or PICK_FACES
        f.pick = self.rng.choice(free)
        f.pallet = self._new_pallet(f.pick[1], f.pick[2], "ready")
        f.route = self._approach(f, (f.pick[3], f.pick[4]), (f.pick[1], f.pick[2]))
        f.phase = "to_pick"

    def _free_lane(self, f: _Forklift) -> Optional[tuple[str, float, float]]:
        busy = {o.lane[0] for o in self.forklifts if o is not f and o.lane and o.phase in ("to_dock", "lowering")}
        busy |= {p["lane"] for p in self.pallets.values() if p["state"] == "delivered"}
        free = [lane for lane in DOCK_LANES if lane[0] not in busy]
        return self.rng.choice(free) if free else None

    def _must_yield(self, f: _Forklift) -> Optional[_Forklift]:
        """Right of way: the higher id gives way to a forklift just ahead, and nobody drives into
        one that is parked lifting or lowering a pallet."""
        hx, hy = math.cos(f.yaw), math.sin(f.yaw)
        for o in self.forklifts:
            if o is f:
                continue
            dx, dy = o.x - f.x, o.y - f.y
            d = math.hypot(dx, dy)
            ahead = dx * hx + dy * hy > 0
            if o.id < f.id and (d < 1.8 or (d < 2.6 and ahead)):
                return o
            if o.phase in ("lifting", "lowering") and d < 2.4 and ahead:
                return o
        return None

    def _back_off(self, f: _Forklift, o: _Forklift, dt: float) -> None:
        """Reverse away from a forklift that is closing in, staying on the floor."""
        dx, dy = f.x - o.x, f.y - o.y
        d = math.hypot(dx, dy) or 1.0
        if d >= 2.2:
            return
        step = 0.6 * dt
        f.x = min(max(f.x + dx / d * step, 0.8), WAREHOUSE["width_m"] - 0.8)
        f.y = min(max(f.y + dy / d * step, 0.8), WAREHOUSE["height_m"] - 0.8)
        f.vx, f.vy = dx / d * 0.6, dy / d * 0.6
        f.detoured = True

    def _speed(self, f: _Forklift) -> float:
        """Creep when another forklift is close ahead, so a gap can open."""
        hx, hy = math.cos(f.yaw), math.sin(f.yaw)
        for o in self.forklifts:
            dx, dy = o.x - f.x, o.y - f.y
            if o is not f and math.hypot(dx, dy) < 2.4 and dx * hx + dy * hy > 0:
                return 0.3
        return FORKLIFT_SPEED

    def _replan(self, f: _Forklift) -> None:
        if not f.detoured or not f.route:
            return
        f.detoured = False
        if len(f.route) >= 2:
            park, nudge = f.route[-2], f.route[-1]
            f.route = [*self._route(f, park), nudge]
        else:
            f.route = self._route(f, f.route[-1])

    def _step(self, f: _Forklift, dt: float) -> None:
        # After waiting 4 s a forklift takes a 2 s turn to go, so two can never lock each other.
        if f.yielding < 0:
            f.yielding = min(0.0, f.yielding + dt)
        elif f.phase in ("to_pick", "to_dock") and (other := self._must_yield(f)) is not None:
            f.yielding += dt
            f.vx = f.vy = 0.0
            self._back_off(f, other, dt)
            if f.yielding >= 4.0 and math.hypot(other.x - f.x, other.y - f.y) > 2.0:
                f.yielding = -2.0
                self._replan(f)
            return
        else:
            if f.yielding > 0:
                self._replan(f)
            f.yielding = 0.0
        if f.phase == "idle":
            self._assign(f)
        elif f.phase == "to_pick":
            if f.drive(dt, self._speed(f)):
                f.phase, f.wait = "lifting", LIFT_S
        elif f.phase == "lifting":
            f.wait -= dt
            if f.wait <= 0:
                f.lane = self._free_lane(f)
                if f.lane is None:
                    f.wait = 1.0              # every lane full: hold the pallet until a truck clears one
                    return
                self.pallets[f.pallet]["state"] = "carried"
                f.route = self._approach(f, (f.lane[1], f.lane[2] - 1.5), (f.lane[1], f.lane[2]))
                f.phase = "to_dock"
        elif f.phase == "to_dock":
            if f.drive(dt, self._speed(f)):
                f.phase, f.wait = "lowering", LIFT_S
        elif f.phase == "lowering":
            f.wait -= dt
            if f.wait <= 0:
                p = self.pallets[f.pallet]
                p.update(state="delivered", since=self.t, lane=f.lane[0], x=f.lane[1], y=f.lane[2], yaw=0.0)
                f.pallet, f.lane, f.pick = None, None, None
                f.phase = "idle"
        if f.pallet and self.pallets[f.pallet]["state"] == "carried":
            p = self.pallets[f.pallet]
            p["x"], p["y"] = f.fork_tip
            p["yaw"] = f.yaw

    def batch(self, dt: float) -> ObjectsIn:
        self.t += dt
        steps = max(1, math.ceil(dt / 0.25))
        for _ in range(steps):
            for f in self.forklifts:
                self._step(f, dt / steps)
        for pid in [k for k, p in self.pallets.items()
                    if p["state"] == "delivered" and self.t - p["since"] >= TRUCK_PICKUP_S]:
            del self.pallets[pid]

        objects: list[dict] = []
        for f in self.forklifts:
            objects.append({"id": f.id, "label": "forklift", "x": f.x, "y": f.y, "yaw": f.yaw,
                            "vx": f.vx, "vy": f.vy, "size": FORKLIFT_SIZE, "confidence": 0.93,
                            "activity": f.activity})
        for pid, p in self.pallets.items():
            activity = {"ready": "Ready for pickup", "carried": "On the forks",
                        "delivered": "Delivered, waiting for the truck"}[p["state"]]
            objects.append({"id": pid, "label": "pallet", "x": p["x"], "y": p["y"], "yaw": p["yaw"],
                            "size": PALLET_SIZE, "confidence": 0.97, "activity": activity})
        cart_x, cart_dir = _shuttle(2.0, 13.0, 0.5, self.t + 4.0)
        objects.append({"id": "cart-1", "label": "cart", "x": cart_x, "y": 0.9,
                        "yaw": 0.0 if cart_dir > 0 else math.pi, "vx": 0.5 * cart_dir,
                        "size": {"length": 1.0, "width": 0.6, "height": 1.0}, "confidence": 0.88,
                        "activity": "Moving parts along the back aisle"})
        return ObjectsIn(source="sim", replace=True, objects=objects)


def _shuttle(lo: float, hi: float, speed: float, t: float) -> tuple[float, int]:
    """Position and direction (+1/-1) of something going back and forth between lo and hi."""
    span = hi - lo
    d = (t * speed) % (2 * span)
    return (lo + d, 1) if d < span else (hi - (d - span), -1)


sim_objects = SimObjects()
