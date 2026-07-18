"""Agility Robotics (Digit) fleet adapter.

Digit integrates cloud-to-cloud via the Arc REST/WebSocket API rather than an on-prem ROS 2
graph. Control is mission/goal level over HTTPS; ``inject_waypoint`` maps to an Arc spatial
constraint / task nudge. ``VELOCITY`` is not in the ceiling.

When a ``FakeArcServer`` is attached, inject POSTs to ``/api/v1/tasks``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport
from .protocols import AGILITY

if TYPE_CHECKING:
    from .fake_servers import FakeArcServer


class SimulatedAgilityAdapter(FleetAdapter):
    vendor = "Agility Robotics"
    transport = Transport.CLOUD_REST

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        fake_server: FakeArcServer | None = None,
    ) -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.goals: list[Pose2D] = []
        self.injected: list[tuple[float, float]] = []
        self.protocol_ops: list[str] = []
        self.estopped = False
        self.fake_server = fake_server

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION, Capability.MAP}

    def read_pose(self) -> Pose2D:
        if self.fake_server is not None:
            state = self.fake_server.get_state(self.robot_id)
            p = state["pose"]
            self._pose = Pose2D(p["x"], p["y"], p["theta"])
            self._battery = float(state["battery_pct"])
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
        op = AGILITY.inject_op
        if self.fake_server is not None:
            self.fake_server.post_task({
                "type": "spatial_constraint",
                "robot_id": self.robot_id,
                "waypoint": {"x": xy[0], "y": xy[1]},
            })
        self.protocol_ops.append(op)
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Agility Arc is cloud/goal-level; no local cmd_vel. Use inject_waypoint()."
        )

    def dispatch_goal(self, pose: Pose2D) -> None:
        self.goals.append(pose)

    def estop(self) -> None:
        self.estopped = True
        if self.fake_server is not None:
            self.fake_server.estop(self.robot_id)

    def resume(self) -> None:
        self.estopped = False
