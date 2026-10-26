"""Occupancy for GET /api/v1/map/{facility} (Sprint C2) and camera-seen obstacles (Sprint J2).

Builds a nav_msgs/OccupancyGrid-shaped payload from the warehouse floor plan so
edge nodes and dashboards can consume real occupancy cells without a SLAM stack, and
keeps the blocked floor cells the Digital Net triangulates so the planner routes around
whatever is actually on the floor.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .config import WAREHOUSE, settings
from .models import CameraOccupancyIn

_MAX_FACILITIES = 64
_MAX_SOURCES = 16

Rect = tuple[float, float, float, float]


class CameraOccupancyFull(RuntimeError):
    pass


@dataclass(frozen=True)
class _Slice:
    cell_m: float
    cells: frozenset[tuple[int, int]]
    updated_at: float
    expires_at: float


class CameraOccupancy:
    """Latest blocked-cell slice per (facility, source). ``version`` changes whenever the set
    of blocked cells does — a new slice or one lapsing — so the planner can cache its grid."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slices: dict[str, dict[str, _Slice]] = {}
        self._versions = itertools.count(1)
        self.version = 0

    def ingest(self, facility_id: str, body: CameraOccupancyIn, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        cols = int(float(WAREHOUSE["width_m"]) / body.cell_m) + 1
        rows = int(float(WAREHOUSE["height_m"]) / body.cell_m) + 1
        cells = frozenset((i, j) for i, j in body.cells if 0 <= i < cols and 0 <= j < rows)
        with self._lock:
            self._expire(now)
            if facility_id not in self._slices and len(self._slices) >= _MAX_FACILITIES:
                raise CameraOccupancyFull(f"camera occupancy for {_MAX_FACILITIES} facilities already")
            by_source = self._slices.setdefault(facility_id, {})
            if body.source not in by_source and len(by_source) >= _MAX_SOURCES:
                raise CameraOccupancyFull(f"{_MAX_SOURCES} occupancy sources already report here")
            prev = by_source.get(body.source)
            by_source[body.source] = _Slice(body.cell_m, cells, now, now + body.ttl_s)
            if prev is None or prev.cells != cells or prev.cell_m != body.cell_m:
                self.version = next(self._versions)
        return len(cells)

    def rects(self, facility_id: str, now: Optional[float] = None) -> list[Rect]:
        """Blocked cells as world rectangles ``(x0, y0, x1, y1)`` in metres."""
        now = time.time() if now is None else now
        with self._lock:
            self._expire(now)
            out = {(i * s.cell_m, j * s.cell_m, (i + 1) * s.cell_m, (j + 1) * s.cell_m)
                   for s in self._slices.get(facility_id, {}).values() for i, j in s.cells}
        return sorted(out)

    def summary(self, facility_id: str, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        rects = self.rects(facility_id, now)
        with self._lock:
            sources = {src: {"cells": len(s.cells), "cell_m": s.cell_m, "updated_at": s.updated_at,
                             "expires_at": s.expires_at}
                       for src, s in self._slices.get(facility_id, {}).items()}
        return {"facility_id": facility_id, "version": self.version, "sources": sources,
                "cells": [[round(v, 3) for v in r] for r in rects]}

    def clear(self) -> None:
        with self._lock:
            if self._slices:
                self.version = next(self._versions)
            self._slices.clear()

    def _expire(self, now: float) -> None:
        changed = False
        for fid in list(self._slices):
            by_source = self._slices[fid]
            for src in [k for k, s in by_source.items() if s.expires_at <= now]:
                changed = changed or bool(by_source[src].cells)
                del by_source[src]
            if not by_source:
                del self._slices[fid]
        if changed:
            self.version = next(self._versions)


camera_occupancy = CameraOccupancy()


def camera_occupancy_message(facility_id: Optional[str] = None) -> dict:
    fid = facility_id or settings.facility_id
    return {"type": "camera_occupancy", **camera_occupancy.summary(fid)}


def synthetic_occupancy_grid(
    facility_id: str | None = None,
    *,
    resolution: float = 0.05,
) -> dict:
    """Rasterize WAREHOUSE racks/walls into occupancy values: -1 unknown, 0 free, 100 occupied."""
    fid = facility_id or settings.facility_id
    width_m = float(WAREHOUSE["width_m"])
    height_m = float(WAREHOUSE["height_m"])
    origin_x = 0.0
    origin_y = 0.0

    width = max(1, int(round(width_m / resolution)))
    height = max(1, int(round(height_m / resolution)))

    # Row-major, y-major like OccupancyGrid: index = y * width + x
    data = [0] * (width * height)

    def paint_rect(x: float, y: float, w: float, h: float, value: int = 100) -> None:
        x0 = max(0, int((x - origin_x) / resolution))
        y0 = max(0, int((y - origin_y) / resolution))
        x1 = min(width, int((x + w - origin_x) / resolution) + 1)
        y1 = min(height, int((y + h - origin_y) / resolution) + 1)
        for yy in range(y0, y1):
            base = yy * width
            for xx in range(x0, x1):
                data[base + xx] = value

    for rack in WAREHOUSE.get("racks", []):
        paint_rect(rack["x"], rack["y"], rack["w"], rack["h"], 100)

    camera_rects = camera_occupancy.rects(fid)
    for x0, y0, x1, y1 in camera_rects:
        # Shrink by half a pixel so a cell paints only the pixels it covers, not its neighbours.
        eps = resolution / 2
        paint_rect(x0 + eps, y0 + eps, x1 - x0 - 2 * eps, y1 - y0 - 2 * eps, 100)

    # Thin perimeter walls
    wall = max(1, int(0.1 / resolution))
    for yy in range(height):
        for xx in range(wall):
            data[yy * width + xx] = 100
            data[yy * width + (width - 1 - xx)] = 100
    for xx in range(width):
        for yy in range(wall):
            data[yy * width + xx] = 100
            data[(height - 1 - yy) * width + xx] = 100

    occupied = sum(1 for v in data if v == 100)
    return {
        "facility_id": fid,
        "frame_id": "map",
        "resolution": resolution,
        "width": width,
        "height": height,
        "origin": {"x": origin_x, "y": origin_y, "theta": 0.0},
        "data": data,
        "encoding": "occupancy_grid_v1",
        "stats": {
            "cells": len(data),
            "occupied": occupied,
            "free": len(data) - occupied,
            "camera_cells": len(camera_rects),
            "source": "synthetic_warehouse+digital_net" if camera_rects else "synthetic_warehouse",
        },
    }
