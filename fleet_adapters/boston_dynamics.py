"""Boston Dynamics (Spot/Atlas) fleet adapter.

BD robots use a gRPC lease-based SDK, not ROS 2 TF — so there is no low-latency cmd_vel
override to hijack. Orbital's control here is command-level (E-Stop, SE2 trajectory inject);
the velocity path raises. Capability ceiling excludes ``VELOCITY``/``TELEOP``.

When a ``FakeBosdynServer`` is attached, inject acquires a lease and records
``RobotCommandService.RobotCommand`` with an SE2TrajectoryCommand-shaped payload.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport
from .protocols import BOSTON_DYNAMICS

if TYPE_CHECKING:
    from .fake_servers import FakeBosdynServer


class SimulatedBostonDynamicsAdapter(FleetAdapter):
    vendor = "Boston Dynamics"
    transport = Transport.GRPC

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        fake_server: FakeBosdynServer | None = None,
    ) -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.estopped = False
        self.injected: list[tuple[float, float]] = []
        self.protocol_ops: list[str] = []
        self.fake_server = fake_server

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION}

    def read_pose(self) -> Pose2D:
        if self.fake_server is not None:
            state = self.fake_server.get_robot_state()
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
        op = BOSTON_DYNAMICS.inject_op
        se2 = {"x": xy[0], "y": xy[1], "theta": 0.0}
        if self.fake_server is not None:
            if not any(l.active for l in self.fake_server.leases):
                self.fake_server.acquire_lease("aria")
            self.fake_server.robot_command(se2_trajectory=se2)
        self.protocol_ops.append(op)
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Boston Dynamics exposes no cmd_vel override; use inject_waypoint / mission goals."
        )

    def estop(self) -> None:
        self.estopped = True
        if self.fake_server is not None:
            self.fake_server.trigger_estop()
        self.protocol_ops.append("grpc.EstopService.SetEstopConfig")

    def resume(self) -> None:
        self.estopped = False
