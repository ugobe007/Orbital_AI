"""Module 2 — Micro-Waypoint Generator ("TF Hijack").

The correction loop. Given the camera ground-truth pose (external) and the robot's own
believed pose (internal), it computes the corrective transform that makes the robot's nav
stack act as if it were at the true pose — injected as a TF offset (tf2_ros) plus a short
corrective velocity applied by the local controller. Runs at 10Hz *on the edge*; the cloud
is never in this path.

The scaffold is a clamped proportional controller over the world-frame error. The real
node publishes ``geometry_msgs/TransformStamped`` on ``/tf`` and ``cmd_vel`` via the
robot's fleet adapter.
"""
from __future__ import annotations

import math

from .config import edge_settings
from .types import Pose2D, PoseCorrection


class MicroWaypointGenerator:
    def __init__(self, gain: float | None = None, max_mps: float | None = None) -> None:
        self.gain = edge_settings.correction_gain if gain is None else gain
        self.max_mps = edge_settings.max_correction_mps if max_mps is None else max_mps

    def compute_correction(self, robot_id: str, external: Pose2D, internal: Pose2D) -> PoseCorrection:
        """World-frame error external-internal, converted to a clamped corrective command.

        The transform offset (dx, dy, dtheta) is the hijack applied to the TF tree; the
        (vx, vy, wz) is the immediate corrective velocity the controller executes this tick.
        """
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
