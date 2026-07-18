"""ROS 2-family adapters wired to dry-run / live OEM API clients.

Shared base for AgiBot, Deep Robotics, Fourier, MagicLab (and the same inject
pattern Unitree uses). Sim path records local inject + OEM call shapes;
``use_hardware=True`` fails inject when the OEM client returns False.
"""
from __future__ import annotations

from typing import Sequence

from aria_edge.types import Pose2D

from .base import Capability, Transport
from .oem_apis.base import OemApiClient
from .protocols import contract_for
from .ros2_adapter import SimulatedROS2Adapter, _ROS2_CEILING
from .secrets import resolve_oem_credentials


class OemWiredROS2Adapter(SimulatedROS2Adapter):
    """SimulatedROS2Adapter that always holds an ``oem_api`` client."""

    transport = Transport.ROS2

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        vendor: str,
        oem_api: OemApiClient,
        use_hardware: bool = False,
    ) -> None:
        super().__init__(robot_id, endpoint, vendor=vendor)
        self.use_hardware = use_hardware
        self.oem_api = oem_api

    @classmethod
    def capability_ceiling(cls) -> set[Capability]:
        return set(_ROS2_CEILING)

    def connect(self, robot_ip: str = "", credentials: dict | None = None) -> bool:
        host = robot_ip or self.endpoint
        if host:
            self.oem_api.host = host
        creds = resolve_oem_credentials(self.vendor, credentials)
        ok = super().connect(robot_ip, creds)
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

        contract = contract_for(self.vendor)
        op = contract.inject_op if contract else "ros2.navigate_to_pose"
        self.injected.append(xy)
        self.protocol_ops.append(op)
        self.protocol_calls.append({
            "op": op,
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

    def protocol_contract(self):
        return contract_for(self.vendor)


class AgiBotAdapter(OemWiredROS2Adapter):
    vendor = "AgiBot"

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        map_id: int = 1,
        port: int = 53176,
        oem_api=None,
    ) -> None:
        from .oem_apis import AgiBotAimdkClient

        client = oem_api or AgiBotAimdkClient(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            map_id=map_id,
            port=port,
        )
        super().__init__(
            robot_id, endpoint, vendor="AgiBot", oem_api=client, use_hardware=use_hardware,
        )


class DeepRoboticsAdapter(OemWiredROS2Adapter):
    vendor = "Deep Robotics"

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        cmd_port: int = 43893,
        state_port: int = 43897,
        oem_api=None,
    ) -> None:
        from .oem_apis import DeepRoboticsLite3Client

        client = oem_api or DeepRoboticsLite3Client(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            cmd_port=cmd_port,
            state_port=state_port,
        )
        super().__init__(
            robot_id, endpoint,
            vendor="Deep Robotics", oem_api=client, use_hardware=use_hardware,
        )


class FourierAdapter(OemWiredROS2Adapter):
    vendor = "Fourier Robotics"

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        domain_id: int = 123,
        robot_name: str = "gr1p",
        oem_api=None,
    ) -> None:
        from .oem_apis import FourierAuroraClient

        client = oem_api or FourierAuroraClient(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            domain_id=domain_id,
            robot_name=robot_name,
        )
        super().__init__(
            robot_id, endpoint,
            vendor="Fourier Robotics", oem_api=client, use_hardware=use_hardware,
        )


class MagicLabAdapter(OemWiredROS2Adapter):
    vendor = "MagicLab"

    def __init__(
        self,
        robot_id: str,
        endpoint: str = "",
        *,
        use_hardware: bool = False,
        namespace: str = "magicdog",
        oem_api=None,
    ) -> None:
        from .oem_apis import MagicLabRos2Client

        client = oem_api or MagicLabRos2Client(
            robot_id,
            host=endpoint,
            dry_run=not use_hardware,
            namespace=namespace,
        )
        super().__init__(
            robot_id, endpoint,
            vendor="MagicLab", oem_api=client, use_hardware=use_hardware,
        )
