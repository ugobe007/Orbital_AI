"""Boston Dynamics (Spot/Atlas) fleet adapter.

BD robots use a gRPC lease-based SDK, not ROS 2 TF — so there is no low-latency cmd_vel
override to hijack. Orbital's control here is command-level only (E-Stop, mission goals);
the TF-Hijack correction is therefore unavailable and the capability ceiling excludes
``VELOCITY``/``TELEOP``. This is exactly the kind of per-vendor limit the OEM onboarding
flow must respect.

Real backend: ``bosdyn.client`` lease + estop keepalive. The scaffold records intent.
"""
from __future__ import annotations

from aria_edge.types import Pose2D

from .base import Capability, FleetAdapter, Transport


class SimulatedBostonDynamicsAdapter(FleetAdapter):
    vendor = "Boston Dynamics"
    transport = Transport.GRPC

    def __init__(self, robot_id: str, endpoint: str = "") -> None:
        super().__init__(robot_id, endpoint)
        self._pose = Pose2D(0.0, 0.0, 0.0)
        self._battery = 100.0
        self.estopped = False

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        # No cmd_vel hijack over gRPC leases — command-level control only.
        return {Capability.TELEMETRY, Capability.STATE, Capability.ESTOP, Capability.MISSION}

    def read_pose(self) -> Pose2D:
        return self._pose

    def read_battery(self) -> float:
        return self._battery

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        raise NotImplementedError(
            "Boston Dynamics exposes no cmd_vel override; use mission-level goals instead."
        )

    def estop(self) -> None:
        self.estopped = True

    def resume(self) -> None:
        self.estopped = False
