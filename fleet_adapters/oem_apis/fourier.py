"""Fourier Intelligence — public Aurora SDK (DDS / fourier_aurora_client).

Sources (public):
  - https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR1/SDK/Overview/
  - https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR2/SDK/Aurora-SDK/developer_guide/
  - pip: fourier_aurora_client — AuroraClient.get_instance(domain_id, robot_name)
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint


class FourierAuroraClient(OemApiClient):
    vendor = "Fourier Robotics"
    sdk_package = "fourier_aurora_client"
    docs_home = "https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR1/SDK/Overview/"

    def __init__(
        self,
        robot_id: str,
        host: str = "",
        *,
        dry_run: bool = True,
        domain_id: int = 123,
        robot_name: str = "gr1p",
    ) -> None:
        super().__init__(robot_id, host, dry_run=dry_run)
        self.domain_id = domain_id
        self.robot_name = robot_name
        self._client = None

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "AuroraClient.get_instance", "dds",
                "fourier_aurora_client.AuroraClient",
                "https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR2/SDK/Aurora-SDK/developer_guide/",
                "Python client over DDS to Aurora server on robot",
            ),
            OemEndpoint(
                "set_fsm_state", "dds", "AuroraClient.set_fsm_state",
                "https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR2/SDK/Aurora-SDK/developer_guide/",
                "Switch controller FSM before locomotion commands",
            ),
            OemEndpoint(
                "control commands", "dds", "AuroraClient.* (state-dependent)",
                "https://support.fftai.com/en/docs/GR-X-Humanoid-Robot/GR2/SDK/Aurora-SDK/developer_guide/",
                "Command set depends on Aurora operating state; EKF fusion preferred over SLAM suppress",
            ),
        )

    def sdk_available(self) -> bool:
        try:
            import fourier_aurora_client  # noqa: F401
            return True
        except ImportError:
            return False

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        del credentials
        self._record("connect", {
            "domain_id": self.domain_id,
            "robot_name": self.robot_name,
            "sdk": self.sdk_available(),
            "is_ros_compatible": False,
        })
        if not self.dry_run and not self.sdk_available():
            return False
        if self.sdk_available() and not self.dry_run:
            from fourier_aurora_client import AuroraClient  # type: ignore

            self._client = AuroraClient.get_instance(
                domain_id=self.domain_id, robot_name=self.robot_name,
            )
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        self._record("inject_waypoint", {
            "api": "Aurora locomotion / user-command task (confirm per robot FSM state)",
            "target": {"x": x, "y": y, "theta": theta},
            "note": "Do not suppress internal SLAM — use robot_localization EKF fusion",
        })
        return self.dry_run or self.connected

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"api": "Aurora state / odometry interfaces"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {"api": "Aurora safe-stop / FSM idle"})
        return True
