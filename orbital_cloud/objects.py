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
from typing import Optional, Sequence

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


class Box:
    """A rotated rectangle on the floor: centre, heading, half length (along the heading) and
    half width. Used for vehicle footprints."""

    __slots__ = ("cx", "cy", "yaw", "hl", "hw", "_c", "_s")

    def __init__(self, cx: float, cy: float, yaw: float, hl: float, hw: float) -> None:
        self.cx, self.cy, self.yaw, self.hl, self.hw = cx, cy, yaw, hl, hw
        self._c, self._s = math.cos(yaw), math.sin(yaw)

    def dist(self, x: float, y: float) -> float:
        """Distance from a point to the rectangle; 0 inside."""
        dx, dy = x - self.cx, y - self.cy
        lx, ly = dx * self._c + dy * self._s, -dx * self._s + dy * self._c
        return math.hypot(max(abs(lx) - self.hl, 0.0), max(abs(ly) - self.hw, 0.0))

    def overlaps(self, other: "Box", pad: float = 0.0) -> bool:
        """True when the two rectangles come within ``pad`` of each other (separating axes)."""
        dx, dy = other.cx - self.cx, other.cy - self.cy
        for ax, ay in ((self._c, self._s), (-self._s, self._c), (other._c, other._s), (-other._s, other._c)):
            r1 = self.hl * abs(self._c * ax + self._s * ay) + self.hw * abs(-self._s * ax + self._c * ay)
            r2 = other.hl * abs(other._c * ax + other._s * ay) + other.hw * abs(-other._s * ax + other._c * ay)
            if abs(dx * ax + dy * ay) > r1 + r2 + pad:
                return False
        return True

    def corners(self) -> list[tuple[float, float]]:
        out = []
        for sl, sw in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            lx, ly = sl * self.hl, sw * self.hw
            out.append((self.cx + lx * self._c - ly * self._s, self.cy + lx * self._s + ly * self._c))
        return out

    def gap(self, other: "Box") -> float:
        """Clearance between two rectangles; 0 when they touch. For convex shapes that don't
        overlap, the closest pair always includes a corner of one of them."""
        if self.overlaps(other):
            return 0.0
        return min(min(other.dist(x, y) for x, y in self.corners()),
                   min(self.dist(x, y) for x, y in other.corners()))

    def bounds(self, pad: float = 0.0) -> tuple[float, float, float, float]:
        ex = self.hl * abs(self._c) + self.hw * abs(self._s) + pad
        ey = self.hl * abs(self._s) + self.hw * abs(self._c) + pad
        return self.cx - ex, self.cy - ey, self.cx + ex, self.cy + ey


def forklift_box(x: float, y: float, yaw: float, length: float, width: float) -> Box:
    """Body plus forks: the forks (and any pallet on them) reach about 0.47 body lengths
    past the front, so the footprint is shifted forward to cover them."""
    front, back = length / 2 + length * 0.5, length / 2
    off = (front - back) / 2
    return Box(x + math.cos(yaw) * off, y + math.sin(yaw) * off, yaw, (front + back) / 2, width / 2)


def vehicle_boxes(facility_id: Optional[str] = None, now: Optional[float] = None) -> list[tuple[str, Box]]:
    """Footprints of the camera-tracked vehicles on the floor, for anyone who must keep clear."""
    return [(o.id, forklift_box(o.x, o.y, o.yaw, o.size.length, o.size.width))
            for o in object_store.list(facility_id or settings.facility_id, now)
            if o.label in VEHICLE_LABELS]


def vehicle_speed_cap(x: float, y: float, facility_id: Optional[str] = None) -> Optional[float]:
    """Speed cap for a robot at (x, y) because a camera-tracked vehicle is close by."""
    for o in object_store.list(facility_id or settings.facility_id):
        if o.label in VEHICLE_LABELS and math.hypot(o.x - x, o.y - y) <= VEHICLE_SLOW_RADIUS_M:
            return settings.human_slow_speed_mps
    return None


def objects_message(facility_id: Optional[str] = None) -> dict:
    fid = facility_id or settings.facility_id
    return {"type": "objects", "facility_id": fid, "now": time.time(), "objects": objects_json(fid)}


PALLET_SIZE = {"length": 0.9, "width": 0.75, "height": 1.0}
FORKLIFT_SIZE = {"length": 1.5, "width": 0.85, "height": 2.0}
FORKLIFT_SPEED = 1.1        # m/s
TURN_RATE = 1.6             # rad/s, turning on the spot
LIFT_S = 2.5                # forks up / down
# Centre of the forklift to the centre of the pallet on its forks.
FORK_REACH = FORKLIFT_SIZE["length"] / 2 + PALLET_SIZE["length"] / 2 - 0.15

# Where forklifts pull pallets from: (name, pallet x, pallet y, side x, side y). The forklift
# parks on that side of the pallet, one fork reach away, facing it.
PICK_FACES: list[tuple[str, float, float, float, float]] = [
    ("Rack G", 18.0, 5.1, 1, 0), ("Rack H", 18.0, 8.1, 1, 0), ("Rack E", 7.1, 8.0, 1, 0),
    ("Rack C", 11.8, 4.0, 1, 0), ("Rack F", 11.8, 10.0, 1, 0),
]
DOCK_LANES: list[tuple[str, float, float]] = [(d["id"], d["x"], d["y"]) for d in WAREHOUSE["dock_lanes"]]

# The outbound truck backs its trailer up to the dock door from the yard beyond the north wall.
TRAILER_SIZE = {"length": 7.0, "width": 2.5, "height": 3.8}
DOCK_DOOR_X = WAREHOUSE["dock"]["x"] + WAREHOUSE["dock"]["w"] / 2
TRUCK_DOCKED_Y = WAREHOUSE["height_m"] + TRAILER_SIZE["length"] / 2
TRUCK_APPROACH_M = 7.0
TRUCK_SPEED = 1.2           # m/s backing in / pulling out
TRUCK_LOAD_S = 2.5          # one pallet from a dock lane into the trailer
TRUCK_CAPACITY = 6
TRUCK_MAX_WAIT_S = 45.0     # leave with a part load rather than wait forever
TRUCK_GAP_S = (12.0, 20.0)  # yard time between trucks

# Keeping clear. A forklift never moves closer to a person than PERSON_STOP_M or to a robot than
# ROBOT_STOP_M (measured from its footprint, forks included), never moves its footprint within
# FORKLIFT_GAP_M of another forklift's, and slows to CREEP_MPS with anyone within SLOW_NEAR_M.
PERSON_STOP_M = 0.7
ROBOT_STOP_M = 0.5
FORKLIFT_GAP_M = 0.3
SLOW_NEAR_M = 2.5
CREEP_MPS = 0.45
DETOUR_AFTER_S = 1.5        # blocked this long: plan a way around whatever is in the way
PERSON_DETOUR_PADS = (1.4, 1.1, 0.8)     # widest that leaves the forklift's goal reachable
FORKLIFT_DETOUR_PADS = (0.9, 0.5, 0.25)
DETOUR_NEAR_M = 4.0
GIVE_WAY_AFTER_S = 4.0      # still stuck on a forklift: pull aside off its path and let it by
GIVE_WAY_HOLD_S = 2.5
REDISPATCH_AFTER_S = 20.0   # a person or robot still in the way: pick somewhere else
SWAP_AFTER_S = 10.0         # two forklifts stuck on each other this long: the other one gives way
REVERSE_M = 1.5             # straight-line move away from a forklift before pulling aside
BACK_OUT_M = (0.8, 1.0)     # back straight out of a rack face / dock lane before turning
HORN_M = 2.0   # people this close to a stuck forklift step out of its way
CART_Y, CART_X, CART_SPEED = 0.9, (2.0, 13.0), 0.5


class _Forklift:
    def __init__(self, fid: str, x: float, y: float) -> None:
        self.id, self.x, self.y, self.yaw = fid, x, y, 0.0
        self.vx = self.vy = 0.0
        self.phase = "idle"           # idle | to_pick | lifting | holding | to_dock | lowering
        self.route: list[tuple[float, float]] = []
        self.pick: Optional[tuple[str, float, float, float, float]] = None
        self.lane: Optional[tuple[str, float, float]] = None
        self.pallet: Optional[str] = None
        self.wait = 0.0
        self.target: Optional[tuple[tuple[float, float], tuple[float, float]]] = None   # (park, face)
        self.blocked_by: Optional[str] = None      # "forklift-2", "person wkr-01", "robot rbt-03"
        self.blocked_s = 0.0
        self.detour_at = -1e9
        self.reverse_m = 0.0           # metres left to drive straight, no turning...
        self.reverse_dir = -1          # ...backwards (-1) or forwards (+1)
        self.aside = False             # driving to a spot off another forklift's path
        self.yield_to: Optional[str] = None   # the forklift we're backing up / pulling aside for
        self.hold_s = 0.0              # pulled aside, letting it by

    @property
    def box(self) -> Box:
        return forklift_box(self.x, self.y, self.yaw, FORKLIFT_SIZE["length"], FORKLIFT_SIZE["width"])

    @property
    def activity(self) -> str:
        if self.reverse_m > 0:
            return "Backing up" if self.reverse_dir < 0 else "Pulling forward to make room"
        if self.aside or self.hold_s > 0:
            return "Pulling aside to let a forklift by"
        if self.blocked_by and self.blocked_s > 0.3:
            who = self.blocked_by.split(" ")[0]
            return {"person": "Stopped: a person is in the way", "robot": "Stopped: a robot is in the way"}.get(
                who, f"Giving way to {self.blocked_by}")
        if self.phase == "to_pick" and self.pick:
            return f"Heading to {self.pick[0]} for a pallet"
        if self.phase == "lifting" and self.pick:
            return f"Lifting a pallet at {self.pick[0]}"
        if self.phase == "to_dock" and self.lane:
            return f"Carrying a pallet to {self.lane[0]}"
        if self.phase == "lowering" and self.lane:
            return f"Setting the pallet down at {self.lane[0]}"
        if self.phase == "holding":
            return "Holding a pallet: every dock lane is full"
        return "Waiting for its next pick"

    def drive(self, dt: float, speed: float = FORKLIFT_SPEED) -> bool:
        """Advance along the route, turning toward each waypoint at TURN_RATE before driving
        to it; True once the last waypoint is reached."""
        left = dt
        budget = speed * dt
        self.vx = self.vy = 0.0
        while self.route and budget > 0:
            tx, ty = self.route[0]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                err = (math.atan2(dy, dx) - self.yaw + math.pi) % (2 * math.pi) - math.pi
                if abs(err) > TURN_RATE * left:
                    self.yaw += math.copysign(TURN_RATE * left, err)
                    return False
                left -= abs(err) / TURN_RATE
                budget = min(budget, speed * left)
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
        return self.x + math.cos(self.yaw) * FORK_REACH, self.y + math.sin(self.yaw) * FORK_REACH


class _Truck:
    """away → arriving (backs in) → loading → leaving → away."""

    def __init__(self, first_gap_s: float) -> None:
        self.phase = "away"
        self.wait = first_gap_s
        self.y = TRUCK_DOCKED_Y + TRUCK_APPROACH_M
        self.vy = 0.0
        self.load = 0
        self.docked_s = 0.0
        self.loading: Optional[str] = None     # pallet moving from its lane into the trailer

    @property
    def activity(self) -> str:
        if self.phase == "arriving":
            return "Backing up to the dock door"
        if self.phase == "loading":
            return f"Loading: {self.load} of {TRUCK_CAPACITY} pallets aboard"
        return f"Pulling out with {self.load} pallet{'s' if self.load != 1 else ''}"


class SimObjects:
    """Forklifts running pallets from the racks to the dock, the pallets themselves (waiting at a
    rack, on the forks, or delivered and waiting for a truck), the truck that backs up to the
    dock and loads them, and a cart crossing the back aisle. Routes go around the racks with
    the same planner the robots use."""

    def __init__(self, seed: int = 7) -> None:
        self.t = 0.0
        self.rng = random.Random(seed)
        self.forklifts = [_Forklift("forklift-1", 21.5, 8.1), _Forklift("forklift-2", 14.0, 1.2)]
        self.pallets: dict[str, dict] = {}     # id → {x, y, yaw, state, since}
        self._next_pallet = 1
        self.truck = _Truck(first_gap_s=8.0)
        self.shipped = 0
        self.trucks_loaded = 0
        self.people: list[tuple[str, float, float]] = []
        self.robots: list[tuple[str, float, float]] = []
        self.cart_x, self.cart_dir, self.cart_v = 6.0, 1, 0.0
        self.deadlock_s = 0.0     # how long the two forklifts have been stuck on each other

    def _new_pallet(self, x: float, y: float, state: str) -> str:
        pid = f"pallet-{self._next_pallet}"
        self._next_pallet += 1
        self.pallets[pid] = {"x": x, "y": y, "yaw": 0.0, "state": state, "since": self.t}
        return pid

    def _route(self, f: _Forklift, goal: tuple[float, float], avoid=()) -> list[tuple[float, float]]:
        from .pathing import plan  # lazy: pathing pulls in zones + occupancy
        return (plan((f.x, f.y), goal, avoid=avoid) if avoid else []) or plan((f.x, f.y), goal) or [goal]

    def _approach(self, f: _Forklift, park: tuple[float, float],
                  face: tuple[float, float], avoid=()) -> list[tuple[float, float]]:
        """Route to ``park``, then a hair toward ``face`` so the forks end up pointing at it."""
        f.target, f.aside, f.hold_s = (park, face), False, 0.0
        dx, dy = face[0] - park[0], face[1] - park[1]
        d = math.hypot(dx, dy) or 1.0
        return [*self._route(f, park, avoid), (park[0] + dx / d * 0.01, park[1] + dy / d * 0.01)]

    def _assign(self, f: _Forklift, avoid_face: Optional[str] = None) -> None:
        taken = {o.pick[0] for o in self.forklifts
                 if o is not f and o.pick and o.phase in ("to_pick", "lifting", "holding")}
        free = [p for p in PICK_FACES if p[0] not in taken and p[0] != avoid_face] or PICK_FACES
        f.pick = self.rng.choice(free)
        _, px, py, sx, sy = f.pick
        if avoid_face and f.pallet:
            self.pallets[f.pallet].update(x=px, y=py)    # the WMS sends it for a pallet elsewhere
        else:
            f.pallet = self._new_pallet(px, py, "ready")
        f.route = self._approach(f, (px + sx * FORK_REACH, py + sy * FORK_REACH), (px, py))
        f.phase = "to_pick"

    def _free_lane(self, f: _Forklift, exclude: Optional[str] = None) -> Optional[tuple[str, float, float]]:
        busy = {o.lane[0] for o in self.forklifts if o is not f and o.lane and o.phase in ("to_dock", "lowering")}
        busy |= {p["lane"] for p in self.pallets.values() if p["state"] == "delivered"}
        busy |= {exclude} if exclude else set()
        free = [lane for lane in DOCK_LANES if lane[0] not in busy]
        return self.rng.choice(free) if free else None

    def _others(self) -> list[tuple[str, str, float, float]]:
        """People and robots on the floor: (kind, id, x, y)."""
        return [("person", pid, x, y) for pid, x, y in self.people] + \
               [("robot", rid, x, y) for rid, x, y in self.robots]

    def _speed(self, f: _Forklift) -> float:
        box = f.box
        if any(box.dist(x, y) < SLOW_NEAR_M for _, _, x, y in self._others()):
            return CREEP_MPS
        if any(o is not f and box.overlaps(o.box, SLOW_NEAR_M / 2) for o in self.forklifts):
            return CREEP_MPS
        return FORKLIFT_SPEED

    def _blocker(self, f: _Forklift, before: Box) -> Optional[str]:
        """What the move from ``before`` to the forklift's current pose would hit, if anything.
        Inside a stop zone only moves that open up distance are allowed: no sliding past people."""
        after = f.box
        for kind, oid, x, y in self._others():
            stop = PERSON_STOP_M if kind == "person" else ROBOT_STOP_M
            d = after.dist(x, y)
            if d < stop and d <= before.dist(x, y) + 1e-3:
                return f"{kind} {oid}"
        for o in self.forklifts:
            if o is f:
                continue
            ob = o.box
            if not after.overlaps(ob, FORKLIFT_GAP_M):
                continue
            g = after.gap(ob)
            if g < FORKLIFT_GAP_M and g <= before.gap(ob) + 1e-3:
                return o.id
        return None

    def _obstacles_near(self, f: _Forklift, park: tuple[float, float]) -> list[tuple[float, float, float, float]]:
        """Everyone within a few metres, as rectangles to route around: padded generously, or
        less when that would fence off ``park`` (whoever stands right there will move)."""
        # Pads are from the obstacle to the forklift's centre line: half its width plus the stop
        # distance for a person, half its width plus the gap for another forklift.
        boxes = [(Box(x, y, 0.0, 0.0, 0.0), PERSON_DETOUR_PADS) for _, _, x, y in self._others()
                 if math.hypot(x - f.x, y - f.y) < DETOUR_NEAR_M]
        boxes += [(o.box, FORKLIFT_DETOUR_PADS) for o in self.forklifts
                  if o is not f and math.hypot(o.x - f.x, o.y - f.y) < DETOUR_NEAR_M]
        rects = []
        for b, pads in boxes:
            for pad in pads:
                r = b.bounds(pad)
                if not (r[0] <= park[0] <= r[2] and r[1] <= park[1] <= r[3]):
                    rects.append(r)
                    break
        return rects

    def _should_detour(self, f: _Forklift) -> bool:
        """Go around whatever is in the way, except a forklift that is still moving (it will
        clear the way) and, when two forklifts block each other, only the higher id goes round."""
        o = next((o for o in self.forklifts if o.id == f.blocked_by), None)
        if o is None:
            return True
        if o.blocked_by == f.id:
            # Higher id gives way; if it's boxed in and nothing has moved for a while, swap.
            return (f.id > o.id) != (int(self.deadlock_s // SWAP_AFTER_S) % 2 == 1)
        return not (o.phase in ("to_pick", "to_dock") and o.blocked_s == 0.0)

    def _detour(self, f: _Forklift) -> None:
        if f.target is None or self.t - f.detour_at < 3.0:
            return
        park, face = f.target
        rects = self._obstacles_near(f, park)
        if rects:
            f.detour_at = self.t
            f.route = self._approach(f, park, face, avoid=rects)

    def _replan(self, f: _Forklift) -> None:
        if f.target is not None:
            park, face = f.target
            f.detour_at = self.t
            f.route = self._approach(f, park, face, avoid=self._obstacles_near(f, park))

    def _pull_aside(self, f: _Forklift, o: _Forklift) -> bool:
        """Drive to a nearby clear spot well off ``o``'s remaining path. False if there is none."""
        from .pathing import _dist_to_segment, _point_blocked
        path = [[o.x, o.y], *[[x, y] for x, y in o.route]]
        w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
        best, best_score = None, -math.inf
        for r in (1.5, 2.5, 3.5):
            for k in range(16):
                a = k * math.pi / 8
                px, py = f.x + math.cos(a) * r, f.y + math.sin(a) * r
                if not (1.0 <= px <= w - 1.0 and 1.0 <= py <= h - 1.0):
                    continue
                if any(_point_blocked(f.x + (px - f.x) * t / 6, f.y + (py - f.y) * t / 6) for t in range(1, 7)):
                    continue
                clear = (min(_dist_to_segment(px, py, path[i], path[i + 1]) for i in range(len(path) - 1))
                         if len(path) > 1 else math.hypot(px - o.x, py - o.y))
                if clear < 1.8 or any(math.hypot(px - x, py - y) < 1.5 for _, _, x, y in self._others()):
                    continue
                score = min(clear, 3.0) - 0.4 * r
                if score > best_score:
                    best, best_score = (px, py), score
        if best is None:
            return False
        f.route, f.aside, f.blocked_s = [best], True, 0.0
        return True

    def _straight_step(self, f: _Forklift, sign: int, d: float) -> bool:
        """Move ``d`` metres straight back (-1) or ahead (+1) if nothing is in the way. Straight
        moves never swing the forks, so they're how a boxed-in forklift makes room."""
        from .pathing import _point_blocked
        before, saved = f.box, (f.x, f.y)
        c, s = math.cos(f.yaw), math.sin(f.yaw)
        f.x, f.y = f.x + sign * c * d, f.y + sign * s * d
        reach = FORKLIFT_SIZE["length"] if sign > 0 else FORKLIFT_SIZE["length"] / 2
        lead = (f.x + sign * c * reach, f.y + sign * s * reach)
        w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
        if not (0.2 <= lead[0] <= w - 0.2 and 0.2 <= lead[1] <= h - 0.2) or _point_blocked(*lead) \
                or self._blocker(f, before):
            f.x, f.y = saved
            return False
        return True

    def _make_room(self, f: _Forklift, ox: float, oy: float, yield_to: Optional[str] = None) -> bool:
        """Start driving straight away from (ox, oy): back up if it's in front, else pull
        forward; the other way if that one is blocked."""
        ahead = math.cos(f.yaw) * (ox - f.x) + math.sin(f.yaw) * (oy - f.y) > 0
        for sign in ((-1, 1) if ahead else (1, -1)):
            x, y = f.x, f.y
            if self._straight_step(f, sign, 0.05):
                f.x, f.y = x, y
                f.reverse_m, f.reverse_dir, f.yield_to, f.aside = REVERSE_M, sign, yield_to, False
                return True
        return False

    def _reverse(self, f: _Forklift, dt: float) -> None:
        """Drive straight (see ``_straight_step``), then pull aside or re-plan."""
        d = min(CREEP_MPS * dt, f.reverse_m)
        if self._straight_step(f, f.reverse_dir, d):
            f.reverse_m -= d
            f.blocked_by = None
            c, s = math.cos(f.yaw), math.sin(f.yaw)
            f.vx, f.vy = f.reverse_dir * c * CREEP_MPS, f.reverse_dir * s * CREEP_MPS
        else:
            f.reverse_m = 0.0
        if f.reverse_m <= 1e-6:
            f.reverse_m, f.reverse_dir, f.blocked_s = 0.0, -1, 0.0
            o = next((o for o in self.forklifts if o.id == f.yield_to), None)
            f.yield_to = None
            if o is None or f.box.gap(o.box) > 2.0 or not self._pull_aside(f, o):
                self._replan(f)

    def _redispatch(self, f: _Forklift) -> None:
        """Someone has been standing in the way too long: go for a different pick, or to a
        different dock lane, the way a dispatcher would rather than leave the forklift idle."""
        f.blocked_s, f.detour_at = 0.0, self.t
        if f.phase == "to_pick" and f.pick:
            self._assign(f, avoid_face=f.pick[0])
        elif f.phase == "to_dock" and f.lane:
            lane = self._free_lane(f, exclude=f.lane[0])
            if lane is not None:
                f.lane = lane
                f.route = self._approach(f, (lane[1], lane[2] - FORK_REACH), (lane[1], lane[2]))

    def _drive(self, f: _Forklift, dt: float) -> bool:
        """Drive along the route unless that would hit someone; True on arrival."""
        if f.reverse_m > 0:
            self._reverse(f, dt)
            return False
        if f.hold_s > 0:
            f.hold_s -= dt
            if f.hold_s <= 0:
                self._replan(f)
            return False
        before, saved = f.box, (f.x, f.y, f.yaw, list(f.route))
        arrived = f.drive(dt, self._speed(f))
        hit = self._blocker(f, before)
        if hit is None:
            f.blocked_by, f.blocked_s = None, 0.0
            if arrived and f.aside:
                f.aside, f.hold_s = False, GIVE_WAY_HOLD_S
                return False
            return arrived
        f.x, f.y, f.yaw, f.route = saved
        f.vx = f.vy = 0.0
        f.blocked_s += dt
        f.blocked_by = hit
        if f.blocked_s >= DETOUR_AFTER_S and self._should_detour(f):
            o = next((o for o in self.forklifts if o.id == hit), None)
            if o is None and f.blocked_s >= REDISPATCH_AFTER_S:
                self._redispatch(f)
            elif o is None:
                # Someone inside the circle the forks sweep pins a forklift that needs to turn:
                # back away from them in a straight line, then route around.
                who = next(((x, y) for kind, oid, x, y in self._others() if f"{kind} {oid}" == hit), None)
                if who and f.blocked_s >= GIVE_WAY_AFTER_S and self.t - f.detour_at >= 3.0:
                    f.detour_at = self.t
                    self._make_room(f, *who)
                else:
                    self._detour(f)
            elif f.blocked_s >= GIVE_WAY_AFTER_S:
                # Close up, any turn swings the forks into the other one: drive straight away
                # from it first, then pull aside off its path.
                if not self._make_room(f, o.x, o.y, o.id) and not self._pull_aside(f, o):
                    f.blocked_s = DETOUR_AFTER_S
            elif not f.aside:
                self._detour(f)
        return False

    def _step(self, f: _Forklift, dt: float) -> None:
        if f.phase == "idle":
            self._assign(f)
        elif f.phase == "to_pick":
            if self._drive(f, dt):
                f.phase, f.wait = "lifting", LIFT_S
        elif f.phase in ("lifting", "holding"):
            f.wait -= dt
            if f.wait <= 0:
                self.pallets[f.pallet]["state"] = "carried"
                f.lane = self._free_lane(f)
                if f.lane is None:
                    f.phase, f.wait = "holding", 1.0   # every lane full: wait for the truck to clear one
                else:
                    f.route = self._approach(f, (f.lane[1], f.lane[2] - FORK_REACH), (f.lane[1], f.lane[2]))
                    f.phase, f.reverse_m = "to_dock", BACK_OUT_M[0]
        elif f.phase == "to_dock":
            if self._drive(f, dt):
                f.phase, f.wait = "lowering", LIFT_S
        elif f.phase == "lowering":
            f.wait -= dt
            if f.wait <= 0:
                p = self.pallets[f.pallet]
                p.update(state="delivered", since=self.t, lane=f.lane[0], x=f.lane[1], y=f.lane[2])
                f.pallet, f.lane, f.pick, f.target = None, None, None, None
                f.phase, f.reverse_m = "idle", BACK_OUT_M[1]
        self._carry(f)

    def _carry(self, f: _Forklift) -> None:
        """Keep a carried pallet on the forks wherever the forklift has moved."""
        if f.pallet and self.pallets[f.pallet]["state"] == "carried":
            p = self.pallets[f.pallet]
            p["x"], p["y"] = f.fork_tip
            p["yaw"] = f.yaw

    def _step_truck(self, dt: float) -> None:
        tr = self.truck
        tr.vy = 0.0
        if tr.phase == "away":
            tr.wait -= dt
            if tr.wait <= 0:
                tr.phase, tr.load, tr.docked_s = "arriving", 0, 0.0
                tr.y = TRUCK_DOCKED_Y + TRUCK_APPROACH_M
        elif tr.phase == "arriving":
            tr.y = max(TRUCK_DOCKED_Y, tr.y - TRUCK_SPEED * dt)
            tr.vy = -TRUCK_SPEED
            if tr.y <= TRUCK_DOCKED_Y:
                tr.phase, tr.wait = "loading", TRUCK_LOAD_S
        elif tr.phase == "loading":
            tr.docked_s += dt
            if tr.loading:
                p = self.pallets[tr.loading]
                tx, ty = DOCK_DOOR_X, WAREHOUSE["height_m"] + 0.4
                dx, dy = tx - p["x"], ty - p["y"]
                d = math.hypot(dx, dy)
                step = d / max(tr.wait, dt) * dt
                tr.wait -= dt
                if tr.wait <= 0 or d <= step:
                    del self.pallets[tr.loading]
                    tr.loading = None
                    tr.load += 1
                    self.shipped += 1
                else:
                    p["x"], p["y"] = p["x"] + dx / d * step, p["y"] + dy / d * step
                return
            waiting = sorted((p["since"], pid) for pid, p in self.pallets.items() if p["state"] == "delivered")
            if waiting and tr.load < TRUCK_CAPACITY:
                tr.loading = waiting[0][1]
                self.pallets[tr.loading]["state"] = "loading"
                tr.wait = TRUCK_LOAD_S
            elif tr.load >= TRUCK_CAPACITY or (tr.docked_s >= TRUCK_MAX_WAIT_S and tr.load > 0):
                tr.phase = "leaving"
        elif tr.phase == "leaving":
            tr.y += TRUCK_SPEED * dt
            tr.vy = TRUCK_SPEED
            if tr.y >= TRUCK_DOCKED_Y + TRUCK_APPROACH_M:
                self.trucks_loaded += 1
                tr.phase, tr.wait = "away", self.rng.uniform(*TRUCK_GAP_S)

    def _step_cart(self, dt: float) -> None:
        """The cart runs the back aisle and stops for anyone in front of it."""
        ahead_x = self.cart_x + self.cart_dir * 0.9
        blocked = any(abs(x - ahead_x) < 0.7 and abs(y - CART_Y) < 0.9 for _, _, x, y in self._others()) or \
            any(f.box.dist(ahead_x, CART_Y) < 0.6 for f in self.forklifts)
        self.cart_v = 0.0 if blocked else CART_SPEED * self.cart_dir
        self.cart_x += self.cart_v * dt
        if not CART_X[0] <= self.cart_x <= CART_X[1]:
            self.cart_x = min(max(self.cart_x, CART_X[0]), CART_X[1])
            self.cart_dir = -self.cart_dir

    def horns(self) -> dict[str, Box]:
        """People a stopped forklift is waiting on, with its footprint: the sim's horn, so the
        crowd steps aside instead of standing in a forklift's way forever."""
        horns: dict[str, Box] = {}
        for f in self.forklifts:
            if f.blocked_by and f.blocked_s >= 1.0:
                box = f.box
                horns.update({pid: box for pid, x, y in self.people if box.dist(x, y) < HORN_M})
        return horns

    def batch(self, dt: float, people: Sequence[tuple[str, float, float]] = (),
              robots: Sequence[tuple[str, float, float]] = ()) -> ObjectsIn:
        """Advance ``dt`` seconds. ``people`` and ``robots`` are ``(id, x, y)`` of everyone else
        on the floor, so forklifts and the cart can keep clear of them."""
        self.people, self.robots = list(people), list(robots)
        self.t += dt
        steps = max(1, math.ceil(dt / 0.25))
        for _ in range(steps):
            for f in self.forklifts:
                self._step(f, dt / steps)
            self._step_truck(dt / steps)
            self._step_cart(dt / steps)
            a, b = self.forklifts[0], self.forklifts[1]
            stuck = a.blocked_by == b.id and b.blocked_by == a.id
            self.deadlock_s = self.deadlock_s + dt / steps if stuck else 0.0

        objects: list[dict] = []
        for f in self.forklifts:
            objects.append({"id": f.id, "label": "forklift", "x": f.x, "y": f.y, "yaw": f.yaw,
                            "vx": f.vx, "vy": f.vy, "size": FORKLIFT_SIZE, "confidence": 0.93,
                            "activity": f.activity})
        tr = self.truck
        if tr.phase != "away":
            objects.append({"id": "truck-1", "label": "truck", "x": DOCK_DOOR_X, "y": tr.y,
                            "yaw": math.pi / 2, "vy": tr.vy, "size": TRAILER_SIZE, "confidence": 0.9,
                            "activity": tr.activity})
        for pid, p in self.pallets.items():
            activity = {"ready": "Ready for pickup", "carried": "On the forks",
                        "delivered": "Delivered, waiting for the truck",
                        "loading": "Going onto the truck"}[p["state"]]
            objects.append({"id": pid, "label": "pallet", "x": p["x"], "y": p["y"], "yaw": p["yaw"],
                            "size": PALLET_SIZE, "confidence": 0.97, "activity": activity})
        objects.append({"id": "cart-1", "label": "cart", "x": self.cart_x, "y": CART_Y,
                        "yaw": 0.0 if self.cart_dir > 0 else math.pi, "vx": self.cart_v,
                        "size": {"length": 1.0, "width": 0.6, "height": 1.0}, "confidence": 0.88,
                        "activity": "Moving parts along the back aisle" if self.cart_v else "Stopped: someone is in the way"})
        return ObjectsIn(source="sim", replace=True, objects=objects)


sim_objects = SimObjects()
