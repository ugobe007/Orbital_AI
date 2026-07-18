"""Module 3 — Fleet Adapters: the vendor-agnostic control/telemetry interface.

Every robot OEM speaks a different protocol (ROS 2 cmd_vel, Boston Dynamics gRPC leases,
Agility's cloud Arc REST, …). A ``FleetAdapter`` normalizes them behind one interface so
the ARIA edge and the Orbital cloud never hard-code a vendor. Each adapter also declares
its *capability ceiling* — the most Orbital could ever do with that robot — which the OEM
onboarding flow then narrows to what the OEM has actually granted.

Capability names are the shared vocabulary between this layer and the OEM API scopes in
``orbital_cloud.models.APIScope`` (kept 1:1 on purpose).

Guide parity methods (``inject_waypoint``, ``get_internal_pose``, ``trigger_estop``) sit
alongside the existing velocity/estop helpers so Sprint A can migrate without breaking
capability ceilings.
"""
from __future__ import annotations

import abc
import time
from enum import Enum
from typing import Sequence

from aria_edge.types import Pose2D


class Capability(str, Enum):
    TELEMETRY = "telemetry.read"        # pose/odometry/battery streams
    STATE = "state.read"               # lifecycle/state + faults
    VELOCITY = "control.velocity"       # cmd_vel-style correction (legacy fallback)
    ESTOP = "control.estop"            # emergency stop
    TELEOP = "control.teleop"          # full remote operation
    MISSION = "mission.dispatch"        # send goals/waypoints (TF hijack inject path)
    CAMERA = "camera.read"             # onboard camera feeds
    MAP = "map.read"                   # SLAM map access


class Transport(str, Enum):
    ROS2 = "ros2"
    GRPC = "grpc"
    CLOUD_REST = "cloud_rest"
    UDP = "udp"


class FleetAdapter(abc.ABC):
    """One robot's control/telemetry channel. Subclasses bind a real transport."""

    vendor: str = "generic"
    transport: Transport = Transport.ROS2

    def __init__(self, robot_id: str, endpoint: str = "") -> None:
        self.robot_id = robot_id
        self.endpoint = endpoint
        self._connected = False

    @classmethod
    @abc.abstractmethod
    def capability_ceiling(cls) -> set[Capability]:
        """The maximum set of capabilities this vendor's protocol can expose."""

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        """Guide: establish authenticated connection. Sim adapters ignore credentials."""
        del robot_ip, credentials
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Telemetry ──────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def read_pose(self) -> Pose2D:
        """Robot self-reported (internal) pose — what ARIA measures drift against."""

    @abc.abstractmethod
    def read_battery(self) -> float:
        ...

    def get_internal_pose(self, robot_id: str | None = None) -> dict:
        """Guide API: dict pose. ``robot_id`` ignored when the adapter is already bound."""
        del robot_id
        p = self.read_pose()
        return {"x": p.x, "y": p.y, "theta": p.theta, "timestamp": time.time()}

    def get_status(self, robot_id: str | None = None) -> dict:
        del robot_id
        return {"battery_pct": self.read_battery(), "error_code": "", "state": "ok"}

    # ── Control ──────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def inject_waypoint(self, robot_id: str, waypoint: Sequence[float]) -> bool:
        """Send a single (x, y) waypoint already transformed into the robot's internal frame."""

    @abc.abstractmethod
    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Legacy corrective velocity (used when no trajectory / ROS2 cmd_vel path)."""

    @abc.abstractmethod
    def estop(self) -> None:
        ...

    def trigger_estop(self, robot_id: str | None = None) -> bool:
        """Guide API alias for ``estop``."""
        del robot_id
        self.estop()
        return True

    @abc.abstractmethod
    def resume(self) -> None:
        ...
