"""Obstacle-aware path planning over the warehouse floor.

The simulator's camera-driven motion must route *around* the storage racks, not through
them (Orbital localizes the robot against the Global Spatial Map, so a real robot would
never be steered through a shelf). This module builds a coarse occupancy grid from the
rack rectangles — inflated by the robot's footprint for clearance — and runs A* to produce
a short list of waypoints from a start to a goal, then simplifies it with line-of-sight so
the motion reads as smooth aisle-following rather than a jagged grid walk.

Pure stdlib, no dependencies. The grid is built once from ``WAREHOUSE`` and cached.
"""
from __future__ import annotations

import heapq
import math
from typing import Optional

from .config import WAREHOUSE

CELL = 0.25            # grid resolution (m)
CLEARANCE = 0.45       # inflate obstacles by ~a robot radius so paths keep a safe margin

_W = float(WAREHOUSE["width_m"])
_H = float(WAREHOUSE["height_m"])
_COLS = max(1, int(math.ceil(_W / CELL)))
_ROWS = max(1, int(math.ceil(_H / CELL)))

# Inflated rack rectangles as (x0, y0, x1, y1) — used for both grid build and LOS checks.
_RECTS: list[tuple[float, float, float, float]] = [
    (r["x"] - CLEARANCE, r["y"] - CLEARANCE, r["x"] + r["w"] + CLEARANCE, r["y"] + r["h"] + CLEARANCE)
    for r in WAREHOUSE.get("racks", [])
]


def _point_blocked(x: float, y: float) -> bool:
    if x < 0.0 or y < 0.0 or x > _W or y > _H:
        return True
    for x0, y0, x1, y1 in _RECTS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


# Static occupancy grid (True = blocked). Cell (ci, cj) center is world-mapped below.
_GRID: list[list[bool]] = [
    [_point_blocked((ci + 0.5) * CELL, (cj + 0.5) * CELL) for cj in range(_ROWS)]
    for ci in range(_COLS)
]


def _to_cell(x: float, y: float) -> tuple[int, int]:
    ci = min(max(int(x / CELL), 0), _COLS - 1)
    cj = min(max(int(y / CELL), 0), _ROWS - 1)
    return ci, cj


def _center(ci: int, cj: int) -> tuple[float, float]:
    return (ci + 0.5) * CELL, (cj + 0.5) * CELL


def _blocked_cell(ci: int, cj: int) -> bool:
    if ci < 0 or cj < 0 or ci >= _COLS or cj >= _ROWS:
        return True
    return _GRID[ci][cj]


def _nearest_free(ci: int, cj: int) -> tuple[int, int]:
    """Spiral outward to the closest free cell (start/goal may sit inside inflation)."""
    if not _blocked_cell(ci, cj):
        return ci, cj
    for radius in range(1, max(_COLS, _ROWS)):
        for di in range(-radius, radius + 1):
            for dj in (-radius, radius):
                if not _blocked_cell(ci + di, cj + dj):
                    return ci + di, cj + dj
            for dj in range(-radius + 1, radius):
                if not _blocked_cell(ci + radius, cj + dj) or not _blocked_cell(ci - radius, cj + dj):
                    return (ci + radius, cj + dj) if not _blocked_cell(ci + radius, cj + dj) else (ci - radius, cj + dj)
    return ci, cj


_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


def _astar(start: tuple[int, int], goal: tuple[int, int]) -> Optional[list[tuple[int, int]]]:
    if start == goal:
        return [start]
    open_heap: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g: dict[tuple[int, int], float] = {start: 0.0}

    def h(c: tuple[int, int]) -> float:
        return math.hypot(c[0] - goal[0], c[1] - goal[1])

    while open_heap:
        _, cur = heapq.heappop(open_heap)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return path
        ci, cj = cur
        for di, dj in _NEIGHBORS:
            ni, nj = ci + di, cj + dj
            if _blocked_cell(ni, nj):
                continue
            # No diagonal corner-cutting past a blocked orthogonal neighbor.
            if di != 0 and dj != 0 and (_blocked_cell(ci + di, cj) or _blocked_cell(ci, cj + dj)):
                continue
            step = 1.41421356 if (di and dj) else 1.0
            ng = g[cur] + step
            nb = (ni, nj)
            if ng < g.get(nb, float("inf")):
                g[nb] = ng
                came[nb] = cur
                heapq.heappush(open_heap, (ng + h(nb), nb))
    return None


def _segment_clear(p0: tuple[float, float], p1: tuple[float, float]) -> bool:
    """True if the straight segment stays out of every inflated rack (sampled)."""
    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    steps = max(1, int(dist / (CELL * 0.5)))
    for s in range(steps + 1):
        t = s / steps
        if _point_blocked(p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t):
            return False
    return True


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Line-of-sight string-pulling: drop intermediate points we can reach directly."""
    if len(points) <= 2:
        return points
    out = [points[0]]
    anchor = 0
    for i in range(2, len(points)):
        if not _segment_clear(points[anchor], points[i]):
            out.append(points[i - 1])
            anchor = i - 1
    out.append(points[-1])
    return out


def plan(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
    """Return a list of world waypoints from just after ``start`` to ``goal`` that avoids
    the racks. Falls back to a direct segment when already clear or when no grid path exists.
    The final point is the exact ``goal`` so the robot arrives precisely."""
    if _segment_clear(start, goal):
        return [goal]
    s_cell = _nearest_free(*_to_cell(*start))
    g_cell = _nearest_free(*_to_cell(*goal))
    cells = _astar(s_cell, g_cell)
    if not cells:
        return [goal]
    pts = [start, *[_center(ci, cj) for ci, cj in cells], goal]
    pts = _simplify(pts)
    return pts[1:] if len(pts) > 1 else [goal]
