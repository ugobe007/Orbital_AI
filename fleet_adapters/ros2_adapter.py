"""ROS 2 fleet adapter family.

Covers OEMs whose robots expose a ROS 2 nav stack with a cmd_vel override — Unitree,
AgiBot, Deep Robotics, Fourier, MagicLab. These support the full capability set including
high-frequency waypoint inject and velocity override the TF-Hijack correction needs.

The real adapter binds ``rclpy`` (NavigateToPose / cmd_vel, TF broadcast, odometry).
``SimulatedROS2Adapter`` records inject + velocity commands so the edge loop is runnable
without a ROS 2 graph.
"""
from __future__ import annotations

from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport
from .protocols import contract_for

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
        self.injected: list[tuple[float, float]] = []
        self.protocol_ops: list[str] = []
        self.protocol_calls: list[dict] = []

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return set(_ROS2_CEILING)

    def read_pose(self) -> Pose2D:
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        del robot_id
        if self._halted:
            return False
        xy = (float(waypoint[0]), float(waypoint[1]))
        self.injected.append(xy)
        contract = contract_for(self.vendor)
        op = contract.inject_op if contract else "ros2.navigate_to_pose"
        self.protocol_ops.append(op)
        self.protocol_calls.append({
            "op": op,
            "waypoint": xy,
            "topics": [e.name for e in (contract.ros2 if contract else ())],
        })
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        if self._halted:
            return
        self.commands.append((vx, vy, wz))
        self.protocol_ops.append("ros2.cmd_vel")

    def estop(self) -> None:
        self._halted = True
        self.commands.append((0.0, 0.0, 0.0))

    def resume(self) -> None:
        self._halted = False
