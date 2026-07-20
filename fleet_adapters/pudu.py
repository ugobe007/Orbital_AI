"""Pudu Robotics fleet adapter — wired to ``PuduOpenPlatformClient`` (HMAC REST).

Sim / dry-run records inject locally + OEM call shapes. Live path needs
``ORBITAL_SECRET_PUDU_JSON`` (app_key + app_secret) and ``use_hardware=True``.
"""
from __future__ import annotations

from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, Transport
from .oem_apis.pudu import PuduOpenPlatformClient
from .ros2_adapter import SimulatedROS2Adapter
from .secrets import resolve_oem_credentials


class PuduAdapter(SimulatedROS2Adapter):
    vendor = "Pudu Robotics"
    transport = Transport.CLOUD_REST

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        app_key: str = "",
        app_secret: str = "",
        oem_api=None,
    ) -> None:
        super().__init__(robot_id, endpoint, vendor="Pudu Robotics")
        self.use_hardware = use_hardware
        self.oem_api = oem_api or PuduOpenPlatformClient(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            app_key=app_key,
            app_secret=app_secret,
        )

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        # Delivery fleet: mission + estop + telemetry; no raw velocity override.
        return {
            Capability.TELEMETRY,
            Capability.STATE,
            Capability.ESTOP,
            Capability.MISSION,
            Capability.MAP,
        }

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        # Allow connect({"app_key": …}) — common OEM client pattern
        if isinstance(robot_ip, dict) and credentials is None:
            credentials = robot_ip
            robot_ip = ""
        host = robot_ip or self.endpoint
        if host and isinstance(host, str):
            self.oem_api.host = host
        creds = resolve_oem_credentials(self.vendor, credentials)
        ok = super().connect(robot_ip if isinstance(robot_ip, str) else "", creds)
        return ok and self.oem_api.connect(creds)

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        del robot_id
        if self._halted:
            return False
        xy = (float(waypoint[0]), float(waypoint[1]))
        theta = float(waypoint[2]) if len(waypoint) > 2 else 0.0
        api_ok = self.oem_api.inject_waypoint(xy[0], xy[1], theta)
        if not api_ok and self.use_hardware:
            return False
        self.injected.append(xy)
        self.protocol_ops.append("pudu.robot.task")
        self.protocol_calls.append({
            "op": "pudu.robot.task",
            "waypoint": xy,
            "oem_api": self.oem_api.calls[-1].payload if self.oem_api.calls else {},
        })
        return True

    def read_pose(self) -> Pose2D:
        if self.use_hardware:
            pose = self.oem_api.get_internal_pose()
            self._pose = Pose2D(
                float(pose["x"]), float(pose["y"]), float(pose.get("theta", 0.0)),
            )
        return self._pose

    def get_internal_pose(self, robot_id: str | None = None) -> dict:
        if self.use_hardware:
            return self.oem_api.get_internal_pose()
        return super().get_internal_pose(robot_id)

    def estop(self) -> None:
        super().estop()
        self.oem_api.trigger_estop()
