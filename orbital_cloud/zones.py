"""Floor zones on the Global Spatial Map: keep-out areas and slow (speed-capped) areas.

Zones mirror the semantic layer Auki's domains carry on top of the spatial map: an operator
draws a polygon once and every robot that routes through Orbital respects it — the planner
never enters a keep-out zone and motion inside a slow zone is capped.

Zones survive restarts when ``ORBITAL_DB_PATH`` is set (see ``persistence``).
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from . import persistence
from .models import Zone, ZoneIn, ZoneType


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Even-odd ray cast. Points on an edge may fall either way, which is fine for a
    planner that already keeps a clearance margin."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _applies(zone: Zone, vendor: Optional[str]) -> bool:
    return not zone.vendors or vendor is None or vendor in zone.vendors


class ZoneStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, Zone] = {}
        self.version = 0
        self._load_from_db()

    def _load_from_db(self) -> None:
        persistence.init_db()
        for data in persistence.load_zones():
            try:
                zone = Zone.model_validate_json(data)
            except Exception:  # noqa: BLE001 — skip a corrupt row rather than crash boot
                continue
            self._by_id[zone.id] = zone
        self.version += 1

    def list(self) -> list[Zone]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda z: z.created_at)

    def get(self, zone_id: str) -> Optional[Zone]:
        return self._by_id.get(zone_id)

    def create(self, body: ZoneIn, actor: Optional[str] = None) -> Zone:
        zone = Zone(**body.model_dump(), id=f"zone-{uuid.uuid4().hex[:8]}",
                    created_at=time.time(), actor=actor)
        if zone.type == ZoneType.SLOW and zone.speed_cap_mps is None:
            zone.speed_cap_mps = 0.3
        with self._lock:
            self._by_id[zone.id] = zone
            self.version += 1
        persistence.save_zone(zone.id, zone.model_dump_json())
        return zone

    def delete(self, zone_id: str) -> bool:
        with self._lock:
            if self._by_id.pop(zone_id, None) is None:
                return False
            self.version += 1
        persistence.delete_zone(zone_id)
        return True

    def clear(self) -> None:
        with self._lock:
            for zone_id in list(self._by_id):
                persistence.delete_zone(zone_id)
            self._by_id.clear()
            self.version += 1

    def keepouts(self, vendor: Optional[str] = None) -> list[Zone]:
        return [z for z in self.list() if z.type == ZoneType.KEEPOUT and _applies(z, vendor)]

    def keepout_at(self, x: float, y: float, vendor: Optional[str] = None) -> Optional[Zone]:
        for zone in self.keepouts(vendor):
            if point_in_polygon(x, y, zone.polygon):
                return zone
        return None

    def speed_cap(self, x: float, y: float, vendor: Optional[str] = None) -> Optional[float]:
        caps = [z.speed_cap_mps for z in self.list()
                if z.type == ZoneType.SLOW and z.speed_cap_mps and _applies(z, vendor)
                and point_in_polygon(x, y, z.polygon)]
        return min(caps) if caps else None


zone_store = ZoneStore()
