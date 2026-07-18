"""Unitree fleet adapter — wired to ``UnitreeRos2Client`` (public Nav2 / cmd_vel API).

Sim path: records inject locally + dry-runs the OEM client call shapes.
Hardware path (``use_hardware=True``): requires ``rclpy``; OEM client runs with
``dry_run=False`` so lab binds exercise the documented NavigateToPose surface.
"""
from __future__ import annotations

import time
from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, Transport
from .oem_apis import UnitreeRos2Client
from .protocols import UNITREE, contract_for
from .ros2_adapter import SimulatedROS2Adapter, _ROS2_CEILING
from .secrets import resolve_oem_credentials


class UnitreeAdapter(SimulatedROS2Adapter):
    """Unitree-specific adapter with OEM API client + latency hooks."""

    vendor = "Unitree"
    transport = Transport.ROS2

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        namespace: str = "unitree",
        inject_latency_s: float = 0.0,
        oem_api: UnitreeRos2Client | None = None,
    ) -> None:
        super().__init__(robot_id, endpoint, vendor="Unitree")
        self.namespace = namespace
        self.use_hardware = use_hardware
        self.inject_latency_s = inject_latency_s
        self.inject_timestamps: list[float] = []
        if use_hardware:
            try:
                import rclpy  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "Unitree hardware mode requires rclpy / unitree_ros2 on the edge host"
                ) from exc
        self.oem_api = oem_api or UnitreeRos2Client(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            namespace=namespace,
        )

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return set(_ROS2_CEILING)

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        host = robot_ip or self.endpoint
        if host:
            self.oem_api.host = host
        creds = resolve_oem_credentials(self.vendor, credentials)
        ok = super().connect(robot_ip, creds)
        api_ok = self.oem_api.connect(creds)
        return ok and api_ok

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        t0 = time.perf_counter()
        if self.inject_latency_s > 0:
            time.sleep(self.inject_latency_s)
        if self._halted:
            self.inject_timestamps.append(time.perf_counter() - t0)
            return False

        xy = (float(waypoint[0]), float(waypoint[1]))
        theta = float(waypoint[2]) if len(waypoint) > 2 else 0.0
        api_ok = self.oem_api.inject_waypoint(xy[0], xy[1], theta)
        if not api_ok and self.use_hardware:
            self.inject_timestamps.append(time.perf_counter() - t0)
            return False

        self.injected.append(xy)
        self.protocol_ops.append(UNITREE.inject_op)
        self.protocol_calls.append({
            "op": UNITREE.inject_op,
            "waypoint": xy,
            "oem_api": self.oem_api.calls[-1].payload if self.oem_api.calls else {},
        })
        self.inject_timestamps.append(time.perf_counter() - t0)
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

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        super().send_velocity(vx, vy, wz)
        if not self._halted:
            self.oem_api.send_velocity(vx, vy, wz)

    def protocol_contract(self):
        return contract_for("Unitree")
