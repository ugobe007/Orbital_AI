"""ROS 2 fleet adapter family.

Covers OEMs whose robots expose a ROS 2 nav stack with a cmd_vel override — Unitree,
AgiBot, Deep Robotics, Fourier, MagicLab. These support the full capability set including
the high-frequency velocity override the TF-Hijack correction needs.

The real adapter binds ``rclpy`` (publish ``geometry_msgs/Twist`` on cmd_vel, broadcast
``TransformStamped`` on /tf, subscribe odometry). ``SimulatedROS2Adapter`` records commands
so the edge loop is runnable and testable without a ROS 2 graph.
"""
from __future__ import annotations

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport

_ROS2_CEILING = {
    Capability.TELEMETRY, Capability.STATE, Capability.VELOCITY,
    Capability.ESTOP, Capability.TELEOP, Capability.MISSION, Capability.MAP,
}


class SimulatedROS2Adapter(FleetAdapter):
    vendor = "ros2-generic"
    transport = Transport.ROS2

    def __init__(self, robot_id: str, endpoint: str = "", vendor: str | None = None) -> None:
        super().__init__(robot_id, endpoint)
        if vendor:
            self.vendor = vendor
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self._halted = False
        self.commands: list[tuple[float, float, float]] = []

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return set(_ROS2_CEILING)

    def read_pose(self) -> Pose2D:
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        if self._halted:
            return
        self.commands.append((vx, vy, wz))

    def estop(self) -> None:
        self._halted = True
        self.commands.append((0.0, 0.0, 0.0))

    def resume(self) -> None:
        self._halted = False
