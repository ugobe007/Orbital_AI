"""Per-vendor protocol contracts — topic / REST / gRPC names without real SDKs.

Sprint B1: each OEM's inject path is documented as a typed contract the sim adapters
and fake servers exercise. Hardware SDKs swap in later behind the same names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class ProtocolKind(str, Enum):
    ROS2 = "ros2"
    GRPC = "grpc"
    REST = "rest"
    UDP = "udp"
    LCM = "lcm"


@dataclass(frozen=True)
class Ros2Endpoint:
    """A ROS 2 topic or action the adapter must publish/subscribe."""
    name: str
    msg_type: str
    direction: str = "publish"  # publish | subscribe | action


@dataclass(frozen=True)
class GrpcMethod:
    service: str
    method: str
    notes: str = ""


@dataclass(frozen=True)
class RestEndpoint:
    method: str
    path: str
    host_hint: str = ""


@dataclass(frozen=True)
class ProtocolContract:
    vendor: str
    sdk: str
    kind: ProtocolKind
    inject: str  # human description of inject_waypoint mapping
    ros2: tuple[Ros2Endpoint, ...] = ()
    grpc: tuple[GrpcMethod, ...] = ()
    rest: tuple[RestEndpoint, ...] = ()
    udp_ports: tuple[int, ...] = ()
    lcm_channels: tuple[str, ...] = ()
    suppress_internal_slam: bool = False
    notes: str = ""
    # Canonical call shape recorded by sim adapters / fake servers
    inject_op: str = ""


# ── Vendor contracts (guide §6.2) ─────────────────────────────────────────────

UNITREE = ProtocolContract(
    vendor="Unitree",
    sdk="unitree_ros2",
    kind=ProtocolKind.ROS2,
    inject="Publish nav2_msgs/action/NavigateToPose to /{ns}/navigate_to_pose "
           "(or /{ns}/cmd_vel for high-frequency override)",
    inject_op="ros2.navigate_to_pose",
    ros2=(
        Ros2Endpoint("/{ns}/navigate_to_pose", "nav2_msgs/action/NavigateToPose", "action"),
        Ros2Endpoint("/{ns}/cmd_vel", "geometry_msgs/Twist", "publish"),
        Ros2Endpoint("/{ns}/odom", "nav_msgs/Odometry", "subscribe"),
        Ros2Endpoint("/tf", "tf2_msgs/TFMessage", "publish"),
    ),
    suppress_internal_slam=True,
)

AGIBOT = ProtocolContract(
    vendor="AgiBot",
    sdk="aimdk_msgs + ROS 2",
    kind=ProtocolKind.ROS2,
    inject="Publish aimdk_msgs/NavigationGoal to ROS 2 Control Module",
    inject_op="ros2.aimdk_navigation_goal",
    ros2=(
        Ros2Endpoint("/{ns}/aimdk/navigation_goal", "aimdk_msgs/NavigationGoal", "publish"),
        Ros2Endpoint("/{ns}/hal/telemetry", "aimdk_msgs/HalTelemetry", "subscribe"),
    ),
    suppress_internal_slam=True,
)

BOSTON_DYNAMICS = ProtocolContract(
    vendor="Boston Dynamics",
    sdk="bosdyn-client",
    kind=ProtocolKind.GRPC,
    inject="Acquire lease → RobotCommandService.RobotCommand(SE2TrajectoryCommand)",
    inject_op="grpc.RobotCommandService.RobotCommand",
    grpc=(
        GrpcMethod("LeaseService", "Acquire", "Required before command"),
        GrpcMethod("RobotCommandService", "RobotCommand", "SE2TrajectoryCommand payload"),
        GrpcMethod("RobotStateService", "GetRobotState", "Internal pose / battery"),
        GrpcMethod("EstopService", "SetEstopConfig", "Hardware E-Stop"),
    ),
    notes="No ROS 2 TF access; command-level only",
)

DEEP_ROBOTICS = ProtocolContract(
    vendor="Deep Robotics",
    sdk="deeprobotics_sdk",
    kind=ProtocolKind.ROS2,
    inject="ROS 2 nav topics; UDP to motion host for <10ms Safety Halt",
    inject_op="ros2.nav_goal+udp.motion",
    ros2=(
        Ros2Endpoint("/{ns}/goal_pose", "geometry_msgs/PoseStamped", "publish"),
        Ros2Endpoint("/{ns}/cmd_vel", "geometry_msgs/Twist", "publish"),
    ),
    udp_ports=(5005,),
    suppress_internal_slam=True,
    notes="UDP mode preferred for Safety Halt path",
)

FOURIER = ProtocolContract(
    vendor="Fourier Robotics",
    sdk="aurora-sdk + ROS 2",
    kind=ProtocolKind.ROS2,
    inject="ROS 2 nav goals; fuse FSA odometry via robot_localization EKF",
    inject_op="ros2.nav_goal+ekf_fusion",
    ros2=(
        Ros2Endpoint("/{ns}/goal_pose", "geometry_msgs/PoseStamped", "publish"),
        Ros2Endpoint("/odometry/filtered", "nav_msgs/Odometry", "subscribe"),
    ),
    suppress_internal_slam=False,
    notes="Do not suppress internal SLAM; use EKF fusion mode",
)

AGILITY = ProtocolContract(
    vendor="Agility Robotics",
    sdk="Arc REST/WebSocket",
    kind=ProtocolKind.REST,
    inject="POST spatial constraints to Arc /api/v1/tasks",
    inject_op="rest.POST /api/v1/tasks",
    rest=(
        RestEndpoint("POST", "/api/v1/tasks", "https://arc.agilityrobotics.com"),
        RestEndpoint("GET", "/api/v1/robots/{id}/state", "https://arc.agilityrobotics.com"),
        RestEndpoint("POST", "/api/v1/robots/{id}/estop", "https://arc.agilityrobotics.com"),
    ),
    notes="Cloud-to-cloud; requires Arc API key",
)

MAGICLAB = ProtocolContract(
    vendor="MagicLab",
    sdk="MagicDog-Ros2_SDK",
    kind=ProtocolKind.ROS2,
    inject="ROS 2 nav topics; status via LCM magicbot_status",
    inject_op="ros2.nav_goal+lcm.status",
    ros2=(
        Ros2Endpoint("/{ns}/goal_pose", "geometry_msgs/PoseStamped", "publish"),
        Ros2Endpoint("/{ns}/cmd_vel", "geometry_msgs/Twist", "publish"),
    ),
    lcm_channels=("magicbot_status",),
    suppress_internal_slam=True,
)

ALL_CONTRACTS: tuple[ProtocolContract, ...] = (
    UNITREE, AGIBOT, BOSTON_DYNAMICS, DEEP_ROBOTICS, FOURIER, AGILITY, MAGICLAB,
)

_BY_VENDOR = {c.vendor: c for c in ALL_CONTRACTS}


def contract_for(vendor: str) -> ProtocolContract | None:
    return _BY_VENDOR.get(vendor)


def known_protocol_vendors() -> list[str]:
    return sorted(_BY_VENDOR)


def assert_inject_shape(vendor: str, recorded_ops: Sequence[str]) -> None:
    """Raise AssertionError if no recorded op matches the vendor's inject_op."""
    c = contract_for(vendor)
    if c is None:
        raise KeyError(f"No protocol contract for vendor={vendor!r}")
    if c.inject_op not in recorded_ops:
        raise AssertionError(
            f"{vendor}: expected inject op {c.inject_op!r} in {list(recorded_ops)}"
        )
