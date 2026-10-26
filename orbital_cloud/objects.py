"""Tracked objects on the Global Spatial Map (Sprint J3/J4).

The camera rig sees more than robots: pallets, carts, forklifts, a dropped tote. The edge (or a
partner's shape/orientation/motion stack) publishes each one with a label, footprint, yaw and
velocity; the cloud keeps the live set per facility, expires what stops being reported, and
shows it on the map and to agents through MCP ``get_map``.
"""
from __future__ import annotations

import math
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


def objects_message(facility_id: Optional[str] = None) -> dict:
    fid = facility_id or settings.facility_id
    return {"type": "objects", "facility_id": fid, "now": time.time(), "objects": objects_json(fid)}


class SimObjects:
    """A parked pallet at the staging area, a forklift working the aisle between racks H and I,
    and a cart crossing the bottom aisle — enough to show the object layer moving."""

    def __init__(self) -> None:
        self.t = 0.0

    def batch(self, dt: float) -> ObjectsIn:
        self.t += dt
        stage = next(s for s in WAREHOUSE["stations"] if s["id"] == "Stage")
        fork_x, fork_dir = _shuttle(16.5, 21.0, 0.8, self.t)
        cart_x, cart_dir = _shuttle(2.0, 13.0, 0.5, self.t + 4.0)
        return ObjectsIn(source="sim", replace=True, objects=[
            {"id": "pallet-1", "label": "pallet", "x": stage["x"] + 0.9, "y": stage["y"] + 0.7,
             "yaw": 0.0, "size": {"length": 1.2, "width": 1.0, "height": 1.1}, "confidence": 0.97},
            {"id": "forklift-1", "label": "forklift", "x": fork_x, "y": 8.1,
             "yaw": 0.0 if fork_dir > 0 else math.pi, "vx": 0.8 * fork_dir,
             "size": {"length": 2.2, "width": 1.1, "height": 2.1}, "confidence": 0.93},
            {"id": "cart-1", "label": "cart", "x": cart_x, "y": 0.9,
             "yaw": 0.0 if cart_dir > 0 else math.pi, "vx": 0.5 * cart_dir,
             "size": {"length": 1.0, "width": 0.6, "height": 1.0}, "confidence": 0.88},
        ])


def _shuttle(lo: float, hi: float, speed: float, t: float) -> tuple[float, int]:
    """Position and direction (+1/-1) of something going back and forth between lo and hi."""
    span = hi - lo
    d = (t * speed) % (2 * span)
    return (lo + d, 1) if d < span else (hi - (d - span), -1)


sim_objects = SimObjects()
