"""Agility Robotics (Digit) fleet adapter.

Digit integrates cloud-to-cloud via the Arc REST/WebSocket API rather than an on-prem ROS 2
graph. Control is mission/goal level over HTTPS; ``inject_waypoint`` maps to an Arc spatial
constraint / task nudge. ``VELOCITY`` is not in the ceiling.
"""
from __future__ import annotations

from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport


class SimulatedAgilityAdapter(FleetAdapter):
    vendor = "Agility Robotics"
    transport = Transport.CLOUD_REST

    def __init__(self, robot_id: str, endpoint: str = "") -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.goals: list[Pose2D] = []
        self.injected: list[tuple[float, float]] = []
        self.estopped = False

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION, Capability.MAP}

    def read_pose(self) -> Pose2D:
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        del robot_id
        if self.estopped:
            return False
        xy = (float(waypoint[0]), float(waypoint[1]))
        self.injected.append(xy)
        self.goals.append(Pose2D(xy[0], xy[1], 0.0))
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Agility Arc is cloud/goal-level; no local cmd_vel. Use inject_waypoint()."
        )

    def dispatch_goal(self, pose: Pose2D) -> None:
        self.goals.append(pose)

    def estop(self) -> None:
        self.estopped = True

    def resume(self) -> None:
        self.estopped = False
