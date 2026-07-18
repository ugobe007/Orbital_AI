"""Module 2 companion — simulated map→odom TF publisher (guide §5.2).

Real robots need ARIA to suppress internal SLAM and broadcast ``map → {ns}/odom``.
This sim records TransformStamped-shaped frames at 30 Hz without rclpy / tf2_ros.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .types import Pose2D


@dataclass(frozen=True)
class TransformStamped:
    """Minimal stand-in for geometry_msgs/TransformStamped."""
    stamp: float
    frame_id: str  # parent — always "map" for ARIA override
    child_frame_id: str  # e.g. "unitree/odom"
    x: float
    y: float
    z: float = 0.0
    # Quaternion (x, y, z, w) — yaw-only from Pose2D.theta
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0


def _yaw_to_quat(theta: float) -> tuple[float, float, float, float]:
    import math

    half = theta * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


@dataclass
class SimulatedTFPublisher:
    """Records map→odom transforms driven by external (camera) pose."""

    robot_id: str
    robot_namespace: str = "robot"
    publish_hz: float = 30.0
    frames: list[TransformStamped] = field(default_factory=list)
    _pose: Pose2D | None = None
    _last_publish: float = 0.0

    @property
    def child_frame(self) -> str:
        return f"{self.robot_namespace}/odom"

    def on_external_pose(self, pose: Pose2D) -> None:
        self._pose = pose

    def publish_once(self, *, now: float | None = None) -> TransformStamped | None:
        if self._pose is None:
            return None
        ts = time.time() if now is None else now
        qx, qy, qz, qw = _yaw_to_quat(self._pose.theta)
        frame = TransformStamped(
            stamp=ts,
            frame_id="map",
            child_frame_id=self.child_frame,
            x=self._pose.x,
            y=self._pose.y,
            z=0.0,
            qx=qx,
            qy=qy,
            qz=qz,
            qw=qw,
        )
        self.frames.append(frame)
        self._last_publish = ts
        return frame

    def tick(self, *, now: float | None = None) -> TransformStamped | None:
        """Publish if the 30 Hz period has elapsed since the last frame."""
        ts = time.time() if now is None else now
        period = 1.0 / max(self.publish_hz, 1.0)
        if self.frames and (ts - self._last_publish) < period:
            return None
        return self.publish_once(now=ts)

    def stream_rate_hz(self, window: float = 1.0) -> float:
        """Approximate Hz from timestamps in the trailing ``window`` seconds."""
        if len(self.frames) < 2:
            return 0.0
        latest = self.frames[-1].stamp
        recent = [f for f in self.frames if latest - f.stamp <= window]
        if len(recent) < 2:
            return 0.0
        span = recent[-1].stamp - recent[0].stamp
        if span <= 0:
            return 0.0
        return (len(recent) - 1) / span
