"""Synthetic occupancy grid for GET /api/v1/map/{facility} (Sprint C2).

Builds a nav_msgs/OccupancyGrid-shaped payload from the warehouse floor plan so
edge nodes and dashboards can consume real occupancy cells without a SLAM stack.
"""
from __future__ import annotations

from .config import WAREHOUSE, settings


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
            "source": "synthetic_warehouse",
        },
    }
