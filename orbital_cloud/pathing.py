"""Obstacle-aware path planning over the warehouse floor.

The simulator's camera-driven motion must route *around* the storage racks, not through
them (Orbital localizes the robot against the Global Spatial Map, so a real robot would
never be steered through a shelf). This module builds a coarse occupancy grid from the
rack rectangles — inflated by the robot's footprint for clearance — and runs A* to produce
a short list of waypoints from a start to a goal, then simplifies it with line-of-sight so
the motion reads as smooth aisle-following rather than a jagged grid walk.

Keep-out zones (``zones.zone_store``) are inflated by the same clearance and folded into the
grid. Pure stdlib, no dependencies. The rack grid is built once from ``WAREHOUSE``; the
zone-aware grid is cached per (zone version, vendor) and rebuilt only when zones change.
"""
from __future__ import annotations

import heapq
import math
from typing import Optional, Sequence

from .config import WAREHOUSE
from .zones import point_in_polygon, zone_store

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

Grid = list[list[bool]]
Polys = Sequence[list[list[float]]]


def _point_blocked(x: float, y: float) -> bool:
    if x < 0.0 or y < 0.0 or x > _W or y > _H:
        return True
    for x0, y0, x1, y1 in _RECTS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def _dist_to_segment(px: float, py: float, a: list[float], b: list[float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _in_inflated(x: float, y: float, poly: list[list[float]]) -> bool:
    if point_in_polygon(x, y, poly):
        return True
    return any(_dist_to_segment(x, y, poly[i - 1], poly[i]) <= CLEARANCE for i in range(len(poly)))


def _blocked_at(x: float, y: float, polys: Polys) -> bool:
    return _point_blocked(x, y) or any(_in_inflated(x, y, p) for p in polys)


def _keepout_polys(vendor: Optional[str]) -> list[list[list[float]]]:
    return [z.polygon for z in zone_store.keepouts(vendor)]


# Static rack occupancy grid (True = blocked). Cell (ci, cj) center is world-mapped below.
_GRID: Grid = [
    [_point_blocked((ci + 0.5) * CELL, (cj + 0.5) * CELL) for cj in range(_ROWS)]
    for ci in range(_COLS)
]


def _to_cell(x: float, y: float) -> tuple[int, int]:
    ci = min(max(int(x / CELL), 0), _COLS - 1)
    cj = min(max(int(y / CELL), 0), _ROWS - 1)
    return ci, cj


def _center(ci: int, cj: int) -> tuple[float, float]:
    return (ci + 0.5) * CELL, (cj + 0.5) * CELL


_grid_cache: dict[tuple[int, Optional[str]], Grid] = {}


def _grid_for(polys: Polys, vendor: Optional[str]) -> Grid:
    if not polys:
        return _GRID
    key = (zone_store.version, vendor)
    grid = _grid_cache.get(key)
    if grid is None:
        if len(_grid_cache) > 32:
            _grid_cache.clear()
        grid = [row[:] for row in _GRID]
        for poly in polys:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            i0, j0 = _to_cell(min(xs) - CLEARANCE, min(ys) - CLEARANCE)
            i1, j1 = _to_cell(max(xs) + CLEARANCE, max(ys) + CLEARANCE)
            for ci in range(i0, i1 + 1):
                for cj in range(j0, j1 + 1):
                    if not grid[ci][cj] and _in_inflated(*_center(ci, cj), poly):
                        grid[ci][cj] = True
        _grid_cache[key] = grid
    return grid


def _blocked_cell(ci: int, cj: int, grid: Grid = _GRID) -> bool:
    if ci < 0 or cj < 0 or ci >= _COLS or cj >= _ROWS:
        return True
    return grid[ci][cj]


def _nearest_free(ci: int, cj: int, grid: Grid = _GRID) -> tuple[int, int]:
    """Spiral outward to the closest free cell (start/goal may sit inside inflation)."""
    if not _blocked_cell(ci, cj, grid):
        return ci, cj
    for radius in range(1, max(_COLS, _ROWS)):
        for di in range(-radius, radius + 1):
            for dj in (-radius, radius):
                if not _blocked_cell(ci + di, cj + dj, grid):
                    return ci + di, cj + dj
        for dj in range(-radius + 1, radius):
            for di in (radius, -radius):
                if not _blocked_cell(ci + di, cj + dj, grid):
                    return ci + di, cj + dj
    return ci, cj


_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


def _astar(start: tuple[int, int], goal: tuple[int, int],
           grid: Grid = _GRID, partial: bool = False) -> Optional[list[tuple[int, int]]]:
    """A* over ``grid``. When the goal is unreachable, returns None — or, with ``partial``,
    the path to the reachable cell closest to the goal."""
    if start == goal:
        return [start]
    open_heap: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g: dict[tuple[int, int], float] = {start: 0.0}

    def h(c: tuple[int, int]) -> float:
        return math.hypot(c[0] - goal[0], c[1] - goal[1])

    def walk_back(cur: tuple[int, int]) -> list[tuple[int, int]]:
        path = [cur]
        while cur in came:
            cur = came[cur]
            path.append(cur)
        path.reverse()
        return path

    best = start
    while open_heap:
        _, cur = heapq.heappop(open_heap)
        if cur == goal:
            return walk_back(cur)
        if h(cur) < h(best):
            best = cur
        ci, cj = cur
        for di, dj in _NEIGHBORS:
            ni, nj = ci + di, cj + dj
            if _blocked_cell(ni, nj, grid):
                continue
            # No diagonal corner-cutting past a blocked orthogonal neighbor.
            if di != 0 and dj != 0 and (_blocked_cell(ci + di, cj, grid) or _blocked_cell(ci, cj + dj, grid)):
                continue
            step = 1.41421356 if (di and dj) else 1.0
            ng = g[cur] + step
            nb = (ni, nj)
            if ng < g.get(nb, float("inf")):
                g[nb] = ng
                came[nb] = cur
                heapq.heappush(open_heap, (ng + h(nb), nb))
    return walk_back(best) if partial else None


def _segment_clear(p0: tuple[float, float], p1: tuple[float, float], polys: Polys = ()) -> bool:
    """True if the straight segment stays out of every inflated rack and keep-out (sampled)."""
    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    steps = max(1, int(dist / (CELL * 0.5)))
    for s in range(steps + 1):
        t = s / steps
        if _blocked_at(p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, polys):
            return False
    return True


def _simplify(points: list[tuple[float, float]], polys: Polys = ()) -> list[tuple[float, float]]:
    """Line-of-sight string-pulling: drop intermediate points we can reach directly."""
    if len(points) <= 2:
        return points
    out = [points[0]]
    anchor = 0
    for i in range(2, len(points)):
        if not _segment_clear(points[anchor], points[i], polys):
            out.append(points[i - 1])
            anchor = i - 1
    out.append(points[-1])
    return out


def plan(start: tuple[float, float], goal: tuple[float, float],
         vendor: Optional[str] = None) -> list[tuple[float, float]]:
    """Return a list of world waypoints from just after ``start`` to ``goal`` that avoids
    the racks and every keep-out zone that applies to ``vendor``. Falls back to a direct
    segment when already clear or when no grid path exists and no keep-outs apply. The final
    point is the exact ``goal`` so the robot arrives precisely — unless the goal sits inside
    a keep-out zone or is cut off by one, in which case the route stops at the nearest
    reachable point outside it."""
    polys = _keepout_polys(vendor)
    if _segment_clear(start, goal, polys):
        return [goal]
    grid = _grid_for(polys, vendor)
    goal_in_keepout = any(point_in_polygon(goal[0], goal[1], p) for p in polys)
    s_cell = _nearest_free(*_to_cell(*start), grid)
    g_cell = _nearest_free(*_to_cell(*goal), grid)
    cells = _astar(s_cell, g_cell, grid, partial=bool(polys))
    if not cells:
        return [goal]
    pts = [start, *[_center(ci, cj) for ci, cj in cells]]
    if not goal_in_keepout and cells[-1] == g_cell:
        pts.append(goal)
    pts = _simplify(pts, polys)
    return pts[1:] if len(pts) > 1 else pts
