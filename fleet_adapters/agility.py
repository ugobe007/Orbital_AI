"""Agility Robotics (Digit) fleet adapter — wired to ``AgilityArcClient``.

Sim path: optional ``FakeArcServer`` + dry-run OEM client (POST /api/v1/tasks).
Live path: ``use_hardware=True`` or ``oem_api`` with ``dry_run=False`` + API key.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport
from .oem_apis import AgilityArcClient
from .protocols import AGILITY
from .secrets import resolve_oem_credentials

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
        oem_api: AgilityArcClient | None = None,
        use_hardware: bool = False,
        api_key: str = "",
    ) -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.goals: list[Pose2D] = []
        self.injected: list[tuple[float, float]] = []
        self.protocol_ops: list[str] = []
        self.estopped = False
        self.fake_server = fake_server
        self.use_hardware = use_hardware
        self.oem_api = oem_api or AgilityArcClient(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            api_key=api_key,
        )

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION, Capability.MAP}

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        host = robot_ip or self.endpoint
        if host:
            self.oem_api.host = host
        creds = resolve_oem_credentials(self.vendor, credentials)
        if creds and creds.get("api_key") and not self.oem_api.api_key:
            self.oem_api.api_key = creds["api_key"]
        ok = super().connect(robot_ip, creds)
        return ok and self.oem_api.connect(creds)

    def read_pose(self) -> Pose2D:
        if self.fake_server is not None:
            state = self.fake_server.get_state(self.robot_id)
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
            state = self.fake_server.get_state(self.robot_id)
            p = state["pose"]
            return {"x": p["x"], "y": p["y"], "theta": p["theta"], "timestamp": 0.0}
        return self.oem_api.get_internal_pose()

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        del robot_id
        if self.estopped:
            return False
        xy = (float(waypoint[0]), float(waypoint[1]))
        theta = float(waypoint[2]) if len(waypoint) > 2 else 0.0

        api_ok = self.oem_api.inject_waypoint(xy[0], xy[1], theta)
        if self.fake_server is not None:
            self.fake_server.post_task({
                "type": "spatial_constraint",
                "robot_id": self.robot_id,
                "waypoint": {"x": xy[0], "y": xy[1], "theta": theta},
            })

        if not api_ok and self.use_hardware:
            return False

        self.injected.append(xy)
        self.goals.append(Pose2D(xy[0], xy[1], theta))
        self.protocol_ops.append(AGILITY.inject_op)
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Agility Arc is cloud/goal-level; no local cmd_vel. Use inject_waypoint()."
        )

    def dispatch_goal(self, pose: Pose2D) -> None:
        self.goals.append(pose)
        self.oem_api.inject_waypoint(pose.x, pose.y, pose.theta)

    def estop(self) -> None:
        self.estopped = True
        self.oem_api.trigger_estop()
        if self.fake_server is not None:
            self.fake_server.estop(self.robot_id)

    def resume(self) -> None:
        self.estopped = False
