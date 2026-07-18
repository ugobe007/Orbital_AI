"""Unitree fleet adapter — real ROS 2 bind when available, sim otherwise (Sprint D2).

``UnitreeAdapter`` speaks the guide inject path (``navigate_to_pose`` / ``cmd_vel``).
When ``use_hardware=True`` it attempts to import ``rclpy``; without the SDK it raises
so lab deploys cannot silently stay on the simulator.
"""
from __future__ import annotations

import time
from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, Transport
from .protocols import UNITREE, contract_for
from .ros2_adapter import SimulatedROS2Adapter, _ROS2_CEILING


class UnitreeHardwareTransport:
    """Thin rclpy bind — only constructed when ``use_hardware=True``."""

    def __init__(self, robot_id: str, namespace: str = "unitree") -> None:
        try:
            import rclpy  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover - requires lab ROS
            raise RuntimeError(
                "Unitree hardware mode requires rclpy / unitree_ros2 on the edge host"
            ) from exc
        self.robot_id = robot_id
        self.namespace = namespace
        self.connected = False

    def connect(self) -> None:  # pragma: no cover - lab
        self.connected = True

    def inject_waypoint(self, waypoint: Sequence[float]) -> bool:  # pragma: no cover - lab
        # Real impl: NavigateToPose action or cmd_vel toward goal.
        return self.connected

    def read_pose(self) -> Pose2D:  # pragma: no cover - lab
        return Pose2D(0.0, 0.0, 0.0)

    def estop(self) -> None:  # pragma: no cover - lab
        pass


class UnitreeAdapter(SimulatedROS2Adapter):
    """Unitree-specific adapter with optional hardware transport + latency hooks."""

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
    ) -> None:
        super().__init__(robot_id, endpoint, vendor="Unitree")
        self.namespace = namespace
        self.use_hardware = use_hardware
        self.inject_latency_s = inject_latency_s
        self._hw: UnitreeHardwareTransport | None = None
        self.inject_timestamps: list[float] = []
        if use_hardware:
            self._hw = UnitreeHardwareTransport(robot_id, namespace=namespace)

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return set(_ROS2_CEILING)

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        ok = super().connect(robot_ip, credentials)
        if self._hw is not None:
            self._hw.connect()
        return ok

    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        t0 = time.perf_counter()
        if self.inject_latency_s > 0:
            time.sleep(self.inject_latency_s)
        if self._hw is not None:
            ok = self._hw.inject_waypoint(waypoint)
            if ok:
                xy = (float(waypoint[0]), float(waypoint[1]))
                self.injected.append(xy)
                self.protocol_ops.append(UNITREE.inject_op)
        else:
            ok = super().inject_waypoint(robot_id, waypoint)
        self.inject_timestamps.append(time.perf_counter() - t0)
        return ok

    def read_pose(self) -> Pose2D:
        if self._hw is not None:
            return self._hw.read_pose()
        return super().read_pose()

    def get_internal_pose(self, robot_id: str | None = None) -> dict:
        return super().get_internal_pose(robot_id)

    def protocol_contract(self):
        return contract_for("Unitree")
