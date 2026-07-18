"""Unitree — public surface via unitree_ros2 + Nav2 (not a proprietary REST API).

Sources (public):
  - https://github.com/unitreerobotics/unitree_ros2
  - https://api.nav2.org/actions/humble/navigatetopose.html
  - Community: Nav2 ``NavigateToPose`` → ``/cmd_vel`` → Unitree SDK ``Move(vx,vy,vyaw)``
"""
from __future__ import annotations

from typing import Any

from .base import OemApiClient, OemEndpoint


class UnitreeRos2Client(OemApiClient):
    vendor = "Unitree"
    sdk_package = "unitree_ros2 / rclpy + nav2_msgs"
    docs_home = "https://github.com/unitreerobotics/unitree_ros2"

    def __init__(self, robot_id: str, host: str = "", *, dry_run: bool = True, namespace: str = "unitree") -> None:
        super().__init__(robot_id, host, dry_run=dry_run)
        self.namespace = namespace

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "NavigateToPose", "ros2_action", "/{ns}/navigate_to_pose",
                "https://api.nav2.org/actions/humble/navigatetopose.html",
                "nav2_msgs/action/NavigateToPose — primary inject path",
            ),
            OemEndpoint(
                "cmd_vel", "ros2_topic", "/{ns}/cmd_vel",
                "https://docs.ros.org/en/humble/p/geometry_msgs/",
                "geometry_msgs/Twist — high-frequency TF-hijack override",
            ),
            OemEndpoint(
                "odom", "ros2_topic", "/{ns}/odom",
                "https://docs.ros.org/en/humble/p/nav_msgs/",
                "nav_msgs/Odometry — internal pose source",
            ),
            OemEndpoint(
                "SDK Move", "sdk", "unitree_sdk2py.Move(vx, vy, vyaw)",
                "https://github.com/unitreerobotics/unitree_sdk2_python",
                "Bridges Nav2 Twist into Unitree high-level locomotion",
            ),
        )

    def sdk_available(self) -> bool:
        try:
            import rclpy  # noqa: F401
            return True
        except ImportError:
            return False

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        del credentials
        self._record("connect", {"host": self.host, "namespace": self.namespace, "sdk": self.sdk_available()})
        if not self.dry_run and not self.sdk_available():
            return False
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        goal = {
            "action": f"/{self.namespace}/navigate_to_pose",
            "type": "nav2_msgs/action/NavigateToPose",
            "pose": {"frame_id": "map", "x": x, "y": y, "theta": theta},
        }
        self._record("inject_waypoint", goal, ok=self.connected or self.dry_run)
        return self.connected or self.dry_run

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"topic": f"/{self.namespace}/odom"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {"topic": f"/{self.namespace}/cmd_vel", "twist": [0, 0, 0]})
        return True

    def send_velocity(self, vx: float, vy: float, wz: float) -> None:
        self._record("send_velocity", {"topic": f"/{self.namespace}/cmd_vel", "vx": vx, "vy": vy, "wz": wz})
