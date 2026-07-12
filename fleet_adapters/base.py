"""Module 3 — Fleet Adapters: the vendor-agnostic control/telemetry interface.

Every robot OEM speaks a different protocol (ROS 2 cmd_vel, Boston Dynamics gRPC leases,
Agility's cloud Arc REST, …). A ``FleetAdapter`` normalizes them behind one interface so
the ARIA edge and the Orbital cloud never hard-code a vendor. Each adapter also declares
its *capability ceiling* — the most Orbital could ever do with that robot — which the OEM
onboarding flow then narrows to what the OEM has actually granted.

Capability names are the shared vocabulary between this layer and the OEM API scopes in
``orbital_cloud.models.APIScope`` (kept 1:1 on purpose).
"""
from __future__ import annotations

import abc
from enum import Enum

from aria_edge.types import Pose2D


class Capability(str, Enum):
    TELEMETRY = "telemetry.read"        # pose/odometry/battery streams
    STATE = "state.read"               # lifecycle/state + faults
    VELOCITY = "control.velocity"       # cmd_vel-style correction (TF Hijack path)
    ESTOP = "control.estop"            # emergency stop
    TELEOP = "control.teleop"          # full remote operation
    MISSION = "mission.dispatch"        # send goals/waypoints
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
    def connect(self) -> None:
        self._connected = True

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

    # ── Control ──────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Apply a corrective velocity (the edge waypoint generator's output)."""

    @abc.abstractmethod
    def estop(self) -> None:
        ...

    @abc.abstractmethod
    def resume(self) -> None:
        ...
