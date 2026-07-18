"""Deep Robotics Lite3 — public ROS2↔UDP transfer + MotionSDK ports.

Sources (public):
  - https://github.com/DeepRoboticsLab/Lite3_ROS
  - https://github.com/DeepRoboticsLab/Lite3_MotionSDK
  - Perception manual: /cmd_vel → UDP to motion host; odom/imu/joints published
  - MotionSDK UDP ports commonly documented as 43893 (cmd) / 43897 (state)
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint


class DeepRoboticsLite3Client(OemApiClient):
    vendor = "Deep Robotics"
    sdk_package = "Lite3_ROS transfer + Lite3_MotionSDK"
    docs_home = "https://github.com/DeepRoboticsLab/Lite3_ROS"

    def __init__(
        self,
        robot_id: str,
        host: str = "",
        *,
        dry_run: bool = True,
        cmd_port: int = 43893,
        state_port: int = 43897,
    ) -> None:
        super().__init__(robot_id, host or "192.168.1.120", dry_run=dry_run)
        self.cmd_port = cmd_port
        self.state_port = state_port

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "cmd_vel", "ros2_topic", "/cmd_vel",
                "https://github.com/DeepRoboticsLab/Lite3_ROS",
                "geometry_msgs/Twist — transfer bridges to motion-host UDP",
            ),
            OemEndpoint(
                "leg_odom", "ros2_topic", "/leg_odom",
                "https://github.com/DeepRoboticsLab/Lite3_ROS",
                "Internal pose (PoseWithCovarianceStamped / Odometry)",
            ),
            OemEndpoint(
                "MotionSDK Sender", "udp", "udp://{host}:43893",
                "https://github.com/DeepRoboticsLab/Lite3_MotionSDK",
                "Low-latency joint/motion commands (<10 ms path for safety)",
            ),
            OemEndpoint(
                "MotionSDK Receiver", "udp", "udp://*:43897",
                "https://github.com/DeepRoboticsLab/Lite3_MotionSDK",
                "Joint/IMU state stream",
            ),
        )

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        del credentials
        self._record("connect", {
            "host": self.host,
            "cmd_port": self.cmd_port,
            "state_port": self.state_port,
            "bridge": "transfer_ros2 systemd service",
        })
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        # High-level: publish goal_pose / drive via cmd_vel toward (x,y). Record both.
        self._record("inject_waypoint", {
            "primary": {"topic": "/goal_pose", "x": x, "y": y, "theta": theta},
            "fast_path": {"udp": f"{self.host}:{self.cmd_port}", "note": "safety/halt prefers UDP"},
        })
        return self.dry_run or self.connected

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"topic": "/leg_odom"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {
            "ros2": {"topic": "/cmd_vel", "twist": [0, 0, 0]},
            "udp": {"host": self.host, "port": self.cmd_port, "mode": "stop"},
        })
        return True
