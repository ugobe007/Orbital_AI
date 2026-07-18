"""Boston Dynamics (Spot) fleet adapter — wired to ``BostonDynamicsSpotClient``.

Sim path: optional ``FakeBosdynServer`` + dry-run OEM client (lease → RobotCommand).
Hardware path: pass ``oem_api`` with ``dry_run=False`` or set ``use_hardware=True``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport
from .oem_apis import BostonDynamicsSpotClient
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
        oem_api: BostonDynamicsSpotClient | None = None,
        use_hardware: bool = False,
    ) -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.estopped = False
        self.injected: list[tuple[float, float]] = []
        self.protocol_ops: list[str] = []
        self.fake_server = fake_server
        self.use_hardware = use_hardware
        self.oem_api = oem_api or BostonDynamicsSpotClient(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
        )

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION}

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        host = robot_ip or self.endpoint
        if host:
            self.oem_api.host = host
        ok = super().connect(robot_ip, credentials)
        return ok and self.oem_api.connect(credentials)

    def read_pose(self) -> Pose2D:
        if self.fake_server is not None:
            state = self.fake_server.get_robot_state()
            p = state["pose"]
            self._pose = Pose2D(p["x"], p["y"], p["theta"])
            self._battery = float(state["battery_pct"])
        else:
            p = self.oem_api.get_internal_pose()
            self._pose = Pose2D(float(p["x"]), float(p["y"]), float(p.get("theta", 0.0)))
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def get_internal_pose(self, robot_id: str | None = None) -> dict:
        del robot_id
        if self.fake_server is not None:
            state = self.fake_server.get_robot_state()
            p = state["pose"]
            return {"x": p["x"], "y": p["y"], "theta": p["theta"], "timestamp": state.get("ts", 0.0)}
        return self.oem_api.get_internal_pose()

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        del robot_id
        if self.estopped:
            return False
        xy = (float(waypoint[0]), float(waypoint[1]))
        theta = float(waypoint[2]) if len(waypoint) > 2 else 0.0
        se2 = {"x": xy[0], "y": xy[1], "theta": theta}

        api_ok = self.oem_api.inject_waypoint(xy[0], xy[1], theta)
        if self.fake_server is not None:
            if not any(l.active for l in self.fake_server.leases):
                self.fake_server.acquire_lease("aria")
            self.fake_server.robot_command(se2_trajectory=se2)

        if not api_ok and self.use_hardware:
            return False

        self.injected.append(xy)
        self.protocol_ops.append(BOSTON_DYNAMICS.inject_op)
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Boston Dynamics exposes no cmd_vel override; use inject_waypoint / mission goals."
        )

    def estop(self) -> None:
        self.estopped = True
        self.oem_api.trigger_estop()
        if self.fake_server is not None:
            self.fake_server.trigger_estop()
        self.protocol_ops.append("grpc.EstopService.SetEstopConfig")

    def resume(self) -> None:
        self.estopped = False
