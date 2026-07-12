"""Agility Robotics (Digit) fleet adapter.

Digit integrates cloud-to-cloud via the Arc REST/WebSocket API rather than an on-prem ROS 2
graph. Control is mission/goal level over HTTPS; there is no sub-10ms local velocity path,
so the TF-Hijack correction runs in a degraded "goal nudge" mode and ``VELOCITY`` is not in
the ceiling. Telemetry arrives over the Arc WebSocket.

Real backend: authenticated Arc REST client + WebSocket telemetry. The scaffold records intent.
"""
from __future__ import annotations

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
        self.estopped = False

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION, Capability.MAP}

    def read_pose(self) -> Pose2D:
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Agility Arc is cloud/goal-level; no local cmd_vel. Use dispatch_goal()."
        )

    def dispatch_goal(self, pose: Pose2D) -> None:
        self.goals.append(pose)

    def estop(self) -> None:
        self.estopped = True

    def resume(self) -> None:
        self.estopped = False
