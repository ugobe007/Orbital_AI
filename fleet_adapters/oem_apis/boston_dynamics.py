"""Boston Dynamics Spot — public bosdyn-client gRPC (dev.bostondynamics.com).

Sources (public):
  - https://dev.bostondynamics.com/docs/python/understanding_spot_programming.html
  - https://dev.bostondynamics.com/python/bosdyn-client/src/bosdyn/client/robot_command.html
  - https://github.com/boston-dynamics/spot-sdk
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint


class BostonDynamicsSpotClient(OemApiClient):
    vendor = "Boston Dynamics"
    sdk_package = "bosdyn-client"
    docs_home = "https://dev.bostondynamics.com/"

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "LeaseService.Acquire", "grpc", "bosdyn.api.LeaseService/Acquire",
                "https://dev.bostondynamics.com/docs/python/understanding_spot_programming.html",
                "Required before RobotCommand",
            ),
            OemEndpoint(
                "RobotCommandService.RobotCommand", "grpc", "bosdyn.api.RobotCommandService/RobotCommand",
                "https://dev.bostondynamics.com/python/bosdyn-client/src/bosdyn/client/robot_command.html",
                "SE2Trajectory / synchro mobility — inject path",
            ),
            OemEndpoint(
                "RobotStateService.GetRobotState", "grpc", "bosdyn.api.RobotStateService/GetRobotState",
                "https://dev.bostondynamics.com/",
                "Internal pose + battery",
            ),
            OemEndpoint(
                "EstopService", "grpc", "bosdyn.api.EstopService",
                "https://dev.bostondynamics.com/",
                "Hardware E-Stop keepalive",
            ),
        )

    def sdk_available(self) -> bool:
        try:
            import bosdyn.client  # noqa: F401
            return True
        except ImportError:
            return False

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        creds = credentials or {}
        self._record("connect", {
            "host": self.host,
            "username": creds.get("username", ""),
            "sdk": self.sdk_available(),
            "steps": ["create_robot", "authenticate", "time_sync", "acquire_lease"],
        })
        if not self.dry_run and not self.sdk_available():
            return False
        self.connected = True
        self._record("LeaseService.Acquire", {"client_name": "aria"})
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        # Public helper shape: RobotCommandBuilder.synchro_se2_trajectory_*
        payload = {
            "service": "RobotCommandService",
            "method": "RobotCommand",
            "builder": "RobotCommandBuilder.synchro_se2_trajectory_point_command",
            "se2": {"x": x, "y": y, "theta": theta},
        }
        self._record("inject_waypoint", payload, ok=self.connected or self.dry_run)
        return self.connected or self.dry_run

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"service": "RobotStateService", "method": "GetRobotState"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {"service": "EstopService", "method": "SetEstopConfig"})
        return True
