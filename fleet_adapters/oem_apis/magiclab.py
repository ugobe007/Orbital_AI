"""MagicLab MagicDog — public ROS2 SDK + LCM motion channel.

Sources (public):
  - https://www.magiclab.top/en/opensource
  - https://support.magiclab.top/en/
  - MagicDog-Ros2_SDK (topics/services + SLAM/nav)
  - MagicDog-Motion_SDK (LCM PC ↔ control board)
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint


class MagicLabRos2Client(OemApiClient):
    vendor = "MagicLab"
    sdk_package = "MagicDog-Ros2_SDK + MagicDog-Motion_SDK (LCM)"
    docs_home = "https://support.magiclab.top/en/"

    def __init__(self, robot_id: str, host: str = "", *, dry_run: bool = True, namespace: str = "magicdog") -> None:
        super().__init__(robot_id, host, dry_run=dry_run)
        self.namespace = namespace

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "goal_pose", "ros2_topic", "/{ns}/goal_pose",
                "https://support.magiclab.top/en/",
                "geometry_msgs/PoseStamped — nav inject",
            ),
            OemEndpoint(
                "cmd_vel", "ros2_topic", "/{ns}/cmd_vel",
                "https://support.magiclab.top/en/",
                "High-frequency velocity override",
            ),
            OemEndpoint(
                "magicbot_status", "lcm", "lcm://magicbot_status",
                "https://www.magiclab.top/en/opensource",
                "Status channel (Motion SDK / LCM)",
            ),
            OemEndpoint(
                "Motion LCM", "lcm", "udpm://239.255.76.67:7671",
                "https://www.magiclab.top/en/opensource",
                "MagicDog-Motion_SDK publish channel (typical LCM multicast pattern)",
            ),
        )

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        del credentials
        self._record("connect", {
            "namespace": self.namespace,
            "prereq": ["suppress_internal_slam_before_tf_publisher"],
        })
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        self._record("inject_waypoint", {
            "topic": f"/{self.namespace}/goal_pose",
            "pose": {"x": x, "y": y, "theta": theta},
        })
        return self.dry_run or self.connected

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"lcm": "magicbot_status", "ros2": f"/{self.namespace}/odom"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {"topic": f"/{self.namespace}/cmd_vel", "twist": [0, 0, 0]})
        return True
