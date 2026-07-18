"""Module 2 — TF Hijack / Waypoint Injector (guide-faithful, sim-safe).

Core loop (≈10 Hz on the edge, never via cloud):
  1. Build ``T_delta`` such that ``T_delta @ P_external ≈ P_internal`` (2D SE(2)).
  2. Pick a lookahead waypoint ``W_abs`` on the global trajectory.
  3. Map it into the robot frame: ``W_internal = T_delta @ W_abs``.
  4. Inject ``W_internal`` through the fleet adapter.

No ROS / NumPy dependency — pure Python so the edge stays light and unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .config import edge_settings
from .types import InjectionTick, Pose2D, PoseCorrection

Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def calculate_drift_delta(external: Pose2D, internal: Pose2D) -> Matrix3:
    """3×3 homogeneous ``T_delta`` with ``T_delta @ [x_ext, y_ext, 1] → [x_int, y_int, 1]``
    under a pure SE(2) (rotation about origin + translation) model matching the build guide.
    """
    dx = internal.x - external.x
    dy = internal.y - external.y
    d_theta = _wrap_angle(internal.theta - external.theta)
    c, s = math.cos(d_theta), math.sin(d_theta)
    return (
        (c, -s, dx),
        (s, c, dy),
        (0.0, 0.0, 1.0),
    )


def apply_transform(w_abs: Sequence[float], t_delta: Matrix3) -> tuple[float, float]:
    """``W_internal = T_delta @ W_abs`` (homogeneous); returns (x, y)."""
    wx = float(w_abs[0])
    wy = float(w_abs[1])
    x = t_delta[0][0] * wx + t_delta[0][1] * wy + t_delta[0][2]
    y = t_delta[1][0] * wx + t_delta[1][1] * wy + t_delta[1][2]
    return (x, y)


def get_lookahead_waypoint(
    robot_xy: tuple[float, float],
    trajectory: Sequence[tuple[float, float]],
    *,
    start_index: int = 0,
    lookahead_distance: float = 0.20,
) -> tuple[int, tuple[float, float]] | None:
    """Next trajectory point at least ``lookahead_distance`` meters ahead of the robot."""
    rx, ry = robot_xy
    for i in range(start_index, len(trajectory)):
        wx, wy = trajectory[i]
        dist = math.hypot(wx - rx, wy - ry)
        if dist >= lookahead_distance:
            return i, (wx, wy)
    return None


@dataclass
class WaypointInjector:
    """Stateful per-robot injector: trajectory index + injection tick."""

    lookahead_m: float = field(default_factory=lambda: edge_settings.lookahead_m)
    _trajectories: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    _index: dict[str, int] = field(default_factory=dict)

    def set_trajectory(self, robot_id: str, waypoints: Sequence[tuple[float, float]]) -> None:
        self._trajectories[robot_id] = [(float(x), float(y)) for x, y in waypoints]
        self._index[robot_id] = 0

    def clear_trajectory(self, robot_id: str) -> None:
        self._trajectories.pop(robot_id, None)
        self._index.pop(robot_id, None)

    def has_trajectory(self, robot_id: str) -> bool:
        return bool(self._trajectories.get(robot_id))

    def tick(self, robot_id: str, external: Pose2D, internal: Pose2D) -> InjectionTick:
        """One guide injection cycle. Caller decides whether to call ``inject_waypoint``."""
        t_delta = calculate_drift_delta(external, internal)
        dx = internal.x - external.x
        dy = internal.y - external.y
        dtheta = _wrap_angle(internal.theta - external.theta)

        traj = self._trajectories.get(robot_id) or []
        if not traj:
            return InjectionTick(
                robot_id=robot_id,
                t_delta=t_delta,
                w_abs=None,
                w_internal=None,
                trajectory_complete=True,
                dx=dx,
                dy=dy,
                dtheta=dtheta,
            )

        start = self._index.get(robot_id, 0)
        hit = get_lookahead_waypoint(
            (external.x, external.y),
            traj,
            start_index=start,
            lookahead_distance=self.lookahead_m,
        )
        if hit is None:
            return InjectionTick(
                robot_id=robot_id,
                t_delta=t_delta,
                w_abs=None,
                w_internal=None,
                trajectory_complete=True,
                dx=dx,
                dy=dy,
                dtheta=dtheta,
            )

        idx, w_abs = hit
        self._index[robot_id] = idx
        w_internal = apply_transform(w_abs, t_delta)
        return InjectionTick(
            robot_id=robot_id,
            t_delta=t_delta,
            w_abs=w_abs,
            w_internal=w_internal,
            trajectory_complete=False,
            dx=dx,
            dy=dy,
            dtheta=dtheta,
        )


class MicroWaypointGenerator:
    """Legacy P-controller when no trajectory is available (ROS2 cmd_vel path)."""

    def __init__(self, gain: float | None = None, max_mps: float | None = None) -> None:
        self.gain = edge_settings.correction_gain if gain is None else gain
        self.max_mps = edge_settings.max_correction_mps if max_mps is None else max_mps

    def compute_correction(self, robot_id: str, external: Pose2D, internal: Pose2D) -> PoseCorrection:
        dx = external.x - internal.x
        dy = external.y - internal.y
        dtheta = _wrap_angle(external.theta - internal.theta)
        vx = _clamp(dx * self.gain, self.max_mps)
        vy = _clamp(dy * self.gain, self.max_mps)
        wz = _clamp(dtheta * self.gain, self.max_mps)
        return PoseCorrection(robot_id=robot_id, dx=dx, dy=dy, dtheta=dtheta, vx=vx, vy=vy, wz=wz)


def _clamp(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))


def _wrap_angle(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))
