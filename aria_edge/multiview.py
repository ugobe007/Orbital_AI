"""Multi-view depth from the Digital Net (Sprint J1–J2).

Neighbouring rig cameras see the same floor from different angles, so any point two or more
of them see can be placed in 3-D. That lets the cell build its own map of what is on the
floor instead of trusting a typed-in plan:

* :class:`Camera` — calibrated pinhole camera (intrinsics + world pose). Pixel coordinates are
  assumed undistorted; ``cv_pipeline`` undistorts frames before detection.
* :func:`triangulate` — linear (DLT) triangulation from ≥2 views, refined by Gauss-Newton on
  pixel reprojection error, with the error reported per view so a bad camera stands out.
* :class:`OccupancyVolume` — fuses triangulated points into a voxel grid whose evidence decays,
  and slices it into the 2-D blocked cells the cloud planner routes around.

Stdlib only, like the calibration solver.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Sequence[float]) -> Vec3:
    n = math.sqrt(_dot(a, a))
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


@dataclass(frozen=True)
class Camera:
    """Pinhole camera. ``rotation`` maps world axes into the camera frame (rows = camera
    right, down, forward), so a world point X lands at ``rotation · (X − position)``."""
    id: str
    fx: float
    fy: float
    cx: float
    cy: float
    position: Vec3
    rotation: Mat3
    width: int = 1920
    height: int = 1200

    @classmethod
    def look_at(cls, id: str, position: Sequence[float], target: Sequence[float], *,
                fx: float, fy: Optional[float] = None, cx: Optional[float] = None,
                cy: Optional[float] = None, width: int = 1920, height: int = 1200) -> "Camera":
        """A camera at ``position`` aimed at ``target`` with world +z as up. A camera looking
        straight down (the usual overhead mount) takes world +y as its "up" instead."""
        fwd = _unit(_sub(target, position))
        up = (0.0, 0.0, 1.0) if abs(fwd[2]) < 0.999 else (0.0, 1.0, 0.0)
        right = _unit(_cross(fwd, up))
        down = _cross(fwd, right)
        return cls(id=id, fx=fx, fy=fy if fy is not None else fx,
                   cx=cx if cx is not None else width / 2, cy=cy if cy is not None else height / 2,
                   position=tuple(float(v) for v in position), rotation=(right, down, fwd),
                   width=width, height=height)

    def to_camera(self, point: Sequence[float]) -> Vec3:
        d = _sub(point, self.position)
        r = self.rotation
        return (_dot(r[0], d), _dot(r[1], d), _dot(r[2], d))

    def project(self, point: Sequence[float]) -> Optional[tuple[float, float]]:
        """Pixel (u, v) of a world point, or None when it is behind the camera."""
        x, y, z = self.to_camera(point)
        if z <= 1e-6:
            return None
        return (self.fx * x / z + self.cx, self.fy * y / z + self.cy)

    def sees(self, point: Sequence[float]) -> bool:
        uv = self.project(point)
        return uv is not None and 0 <= uv[0] < self.width and 0 <= uv[1] < self.height


@dataclass(frozen=True)
class Triangulation:
    point: Vec3
    errors_px: dict[str, float]    # reprojection error per camera id
    rms_px: float
    views: int

    @property
    def worst_camera(self) -> str:
        return max(self.errors_px, key=self.errors_px.get)


class TriangulationError(ValueError):
    pass


def triangulate(views: Iterable[tuple[Camera, tuple[float, float]]], *,
                refine_iters: int = 5) -> Triangulation:
    """Place one point in 3-D from its pixel position in two or more cameras.

    The linear solve works in normalised image coordinates (pixels with the intrinsics taken
    out), which keeps the 4×4 system well conditioned without Hartley scaling; Gauss-Newton
    then minimises the pixel error, which is what the per-view report is in.
    """
    views = list(views)
    if len(views) < 2:
        raise TriangulationError("need at least two views")
    if len({cam.id for cam, _ in views}) != len(views):
        raise TriangulationError("each camera may contribute one view")

    ata = [[0.0] * 4 for _ in range(4)]
    for cam, (u, v) in views:
        xn, yn = (u - cam.cx) / cam.fx, (v - cam.cy) / cam.fy
        r = cam.rotation
        t = tuple(-_dot(r[i], cam.position) for i in range(3))
        rows = [(*r[i], t[i]) for i in range(3)]
        for row in ([xn * rows[2][k] - rows[0][k] for k in range(4)],
                    [yn * rows[2][k] - rows[1][k] for k in range(4)]):
            for i in range(4):
                for j in range(4):
                    ata[i][j] += row[i] * row[j]
    h = _smallest_eigenvector(ata)
    if abs(h[3]) < 1e-12:
        raise TriangulationError("point is at infinity (views are parallel)")
    point = (h[0] / h[3], h[1] / h[3], h[2] / h[3])

    for _ in range(refine_iters):
        step = _gauss_newton_step(views, point)
        if step is None:
            break
        point = (point[0] + step[0], point[1] + step[1], point[2] + step[2])
        if _dot(step, step) < 1e-16:
            break

    errors: dict[str, float] = {}
    for cam, (u, v) in views:
        uv = cam.project(point)
        if uv is None:
            raise TriangulationError(f"point lands behind camera {cam.id}")
        errors[cam.id] = math.hypot(uv[0] - u, uv[1] - v)
    rms = math.sqrt(sum(e * e for e in errors.values()) / len(errors))
    return Triangulation(point=point, errors_px=errors, rms_px=rms, views=len(views))


def _gauss_newton_step(views: list[tuple[Camera, tuple[float, float]]],
                       point: Vec3) -> Optional[Vec3]:
    jtj = [[0.0] * 3 for _ in range(3)]
    jtr = [0.0, 0.0, 0.0]
    for cam, (u, v) in views:
        x, y, z = cam.to_camera(point)
        if z <= 1e-6:
            return None
        r0, r1, r2 = cam.rotation
        res = (u - (cam.fx * x / z + cam.cx), v - (cam.fy * y / z + cam.cy))
        ju = [cam.fx * (r0[k] / z - x * r2[k] / (z * z)) for k in range(3)]
        jv = [cam.fy * (r1[k] / z - y * r2[k] / (z * z)) for k in range(3)]
        for row, rr in ((ju, res[0]), (jv, res[1])):
            for i in range(3):
                jtr[i] += row[i] * rr
                for j in range(3):
                    jtj[i][j] += row[i] * row[j]
    return _solve3(jtj, jtr)


def _solve3(a: list[list[float]], b: list[float]) -> Optional[Vec3]:
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-18:
        return None
    out = []
    for c in range(3):
        m = [row[:] for row in a]
        for r in range(3):
            m[r][c] = b[r]
        out.append((m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                    - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                    + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])) / det)
    return (out[0], out[1], out[2])


def _smallest_eigenvector(m: list[list[float]], sweeps: int = 60) -> list[float]:
    """Cyclic Jacobi on a small symmetric matrix; returns the eigenvector of the smallest
    eigenvalue (the null-space direction of the DLT system)."""
    n = len(m)
    a = [row[:] for row in m]
    vecs = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-30:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = vecs[k][p], vecs[k][q]
                    vecs[k][p], vecs[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    i = min(range(n), key=lambda d: a[d][d])
    return [vecs[k][i] for k in range(n)]


@dataclass
class TrackReport:
    points: list[tuple[str, Triangulation]] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)    # track id -> reason


def triangulate_tracks(rig: dict[str, Camera],
                       tracks: dict[str, list[tuple[str, float, float]]], *,
                       max_rms_px: float = 3.0) -> TrackReport:
    """Triangulate many tracked features at once. ``tracks`` maps a feature id to the
    ``(camera_id, u, v)`` sightings of it; features seen by fewer than two known cameras, or
    whose views disagree by more than ``max_rms_px``, are rejected with a reason."""
    report = TrackReport()
    for tid, sightings in tracks.items():
        views = [(rig[cid], (u, v)) for cid, u, v in sightings if cid in rig]
        if len(views) < 2:
            report.rejected[tid] = "seen by fewer than two calibrated cameras"
            continue
        try:
            tri = triangulate(views)
        except TriangulationError as exc:
            report.rejected[tid] = str(exc)
            continue
        if tri.rms_px > max_rms_px:
            report.rejected[tid] = (f"views disagree by {tri.rms_px:.1f} px "
                                    f"(worst: camera {tri.worst_camera})")
            continue
        report.points.append((tid, tri))
    return report


def load_rig(path: str | Path) -> dict[str, Camera]:
    """Rig file: ``{"cameras": [{"id", "fx", "fy"?, "cx"?, "cy"?, "width"?, "height"?,
    "position": [x, y, z], and either "look_at": [x, y, z] or "rotation": 3×3}]}``."""
    return rig_from_dict(json.loads(Path(path).read_text()))


def rig_from_dict(data: dict) -> dict[str, Camera]:
    rig: dict[str, Camera] = {}
    for c in data["cameras"]:
        size = {"width": int(c.get("width", 1920)), "height": int(c.get("height", 1200))}
        if "look_at" in c:
            cam = Camera.look_at(str(c["id"]), c["position"], c["look_at"], fx=float(c["fx"]),
                                 fy=c.get("fy"), cx=c.get("cx"), cy=c.get("cy"), **size)
        else:
            rot = tuple(tuple(float(v) for v in row) for row in c["rotation"])
            cam = Camera(id=str(c["id"]), fx=float(c["fx"]), fy=float(c.get("fy", c["fx"])),
                         cx=float(c.get("cx", size["width"] / 2)), cy=float(c.get("cy", size["height"] / 2)),
                         position=tuple(float(v) for v in c["position"]), rotation=rot, **size)
        rig[cam.id] = cam
    return rig


class OccupancyVolume:
    """Voxel evidence grid. Every triangulated point adds ``hit`` to its voxel (once per
    frame); evidence halves every ``half_life_s`` so something that drives away clears. A
    voxel counts as occupied at ``threshold`` — with the defaults that is two frames in quick
    succession, so a single stray point never blocks an aisle."""

    def __init__(self, *, voxel_m: float = 0.1, half_life_s: float = 1.5, hit: float = 1.0,
                 threshold: float = 1.8, max_score: float = 8.0,
                 z_min: float = 0.08, z_max: float = 2.2) -> None:
        self.voxel_m = voxel_m
        self.half_life_s = half_life_s
        self.hit = hit
        self.threshold = threshold
        self.max_score = max_score
        self.z_min = z_min
        self.z_max = z_max
        self._voxels: dict[tuple[int, int, int], tuple[float, float]] = {}

    def _key(self, p: Sequence[float]) -> tuple[int, int, int]:
        s = self.voxel_m
        return (math.floor(p[0] / s), math.floor(p[1] / s), math.floor(p[2] / s))

    def _decayed(self, score: float, since: float, now: float) -> float:
        return score * 0.5 ** (max(0.0, now - since) / self.half_life_s)

    def add_points(self, points: Iterable[Sequence[float]], now: float) -> int:
        """Fuse one frame of points. Points on the floor (below ``z_min``) or overhead (above
        ``z_max``) cannot block a robot and are ignored. Returns voxels touched."""
        keys = {self._key(p) for p in points if self.z_min <= p[2] <= self.z_max}
        for k in keys:
            score, since = self._voxels.get(k, (0.0, now))
            self._voxels[k] = (min(self.max_score, self._decayed(score, since, now) + self.hit), now)
        return len(keys)

    def occupied(self, now: float) -> list[tuple[int, int, int]]:
        live: list[tuple[int, int, int]] = []
        for k, (score, since) in list(self._voxels.items()):
            s = self._decayed(score, since, now)
            if s < 0.05:
                del self._voxels[k]
            elif s >= self.threshold:
                live.append(k)
        return live

    def slice_cells(self, now: float, cell_m: float = 0.25) -> list[tuple[int, int]]:
        """Floor cells (``cell_m`` squares from the map origin) with an occupied voxel above
        them anywhere between ``z_min`` and ``z_max``."""
        s = self.voxel_m
        cells = {(math.floor((i + 0.5) * s / cell_m), math.floor((j + 0.5) * s / cell_m))
                 for i, j, _ in self.occupied(now)}
        return sorted(cells)

    def payload(self, now: float, *, cell_m: float = 0.25, ttl_s: float = 3.0,
                source: str = "digital-net") -> dict:
        """Body for ``POST /api/v1/occupancy/{facility}``."""
        return {"source": source, "cell_m": cell_m, "ttl_s": ttl_s,
                "cells": [list(c) for c in self.slice_cells(now, cell_m)]}
