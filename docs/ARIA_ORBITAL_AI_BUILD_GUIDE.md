# Orbital AI & ARIA: Developer Build Guide

**Project:** StageGate — Orbital AI / ARIA
**Version:** 1.0
**Date:** July 9, 2026
**Confidentiality:** StageGate Internal — Engineering

This document is the authoritative technical reference for building the Orbital AI platform and the ARIA (Active Robot Intelligence Architecture) engine. It covers the development environment, repository structure, all core modules, API contracts, vendor integration specifications, and the prioritized sprint backlog.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Development Environment Setup](#2-development-environment-setup)
3. [Repository Structure](#3-repository-structure)
4. [Module 1: Computer Vision Pipeline](#4-module-1-computer-vision-pipeline)
5. [Module 2: Micro-Waypoint Generator (The TF Hijack)](#5-module-2-micro-waypoint-generator-the-tf-hijack)
6. [Module 3: Fleet Adapters (Per-Vendor)](#6-module-3-fleet-adapters-per-vendor)
7. [Module 4: Safety Halt Controller](#7-module-4-safety-halt-controller)
8. [Module 5: Benchmark Library Pipeline](#8-module-5-benchmark-library-pipeline)
9. [Module 6: Cloud Orchestration Layer](#9-module-6-cloud-orchestration-layer)
10. [Module 7: Fleet Management Dashboard (API Contract)](#10-module-7-fleet-management-dashboard-api-contract)
11. [Cybersecurity Implementation](#11-cybersecurity-implementation)
12. [Sprint Backlog](#12-sprint-backlog)
13. [Hardware Setup Checklist](#13-hardware-setup-checklist)

---

## 1. Architecture Overview

The system is composed of three runtime environments that must be treated as distinct deployment targets.

**Environment 1 — Orbital AI Cloud** runs on AWS or GCP. It hosts the Global Spatial Map, the Mission Planner, the Benchmark Library database, and the OEM Licensing API. This is the only environment with public internet access.

**Environment 2 — ARIA Edge Node** runs on an on-premise GPU server at the customer site. It is air-gapped from the internet. It processes camera feeds, runs the CV pipeline, executes the TF Hijack loop, and communicates with robots over the local private network. It communicates with the Orbital AI Cloud only via a secured, outbound-only mTLS tunnel.

**Environment 3 — Robot Network** is the air-gapped local subnet containing all robots. No robot has direct access to the internet. The ARIA Edge Node is the only node authorized to publish to robot command topics.

```
┌─────────────────────────────────────────────────────────────┐
│                  ORBITAL AI CLOUD (AWS/GCP)                 │
│  Global Spatial Map │ Mission Planner │ Benchmark Library   │
│  OEM Licensing API  │ Telemetry Store │ Dashboard Backend   │
└──────────────────────────────┬──────────────────────────────┘
                               │ mTLS Tunnel (outbound only)
┌──────────────────────────────▼──────────────────────────────┐
│                  ARIA EDGE NODE (On-Premise)                 │
│  CV Pipeline │ Drift Delta Calculator │ Waypoint Generator  │
│  Fleet Adapters │ Safety Halt Controller │ TF Publisher     │
└────────────┬──────────────────────────────────┬─────────────┘
             │ PoE (10GbE)                       │ Wi-Fi 6 / Private 5G
┌────────────▼────────────┐       ┌──────────────▼────────────┐
│   CAMERA NETWORK        │       │   ROBOT SUBNET (VLAN)     │
│   Basler/FLIR PoE Cams  │       │   Unitree │ AgiBot │ BD   │
└─────────────────────────┘       └───────────────────────────┘
```

---

## 2. Development Environment Setup

### 2.1 Required Software

All development for the ARIA Edge Node must be performed on Ubuntu 22.04 LTS. The following packages are required.

```bash
# Step 1: Install ROS 2 Humble
sudo apt install software-properties-common
sudo add-apt-repository universe
sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
  http://packages.ros.org/ros2/ubuntu \
  $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | \
  sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt update && sudo apt install ros-humble-desktop -y
source /opt/ros/humble/setup.bash

# Step 2: Install Nav2 and SLAM Toolbox
sudo apt install ros-humble-nav2-bringup ros-humble-slam-toolbox -y

# Step 3: Install Open-RMF
sudo apt install ros-humble-rmf-traffic ros-humble-rmf-fleet-adapter -y

# Step 4: Install Python dependencies
pip3 install opencv-python numpy scipy nudged \
  grpcio grpcio-tools paho-mqtt influxdb-client \
  cryptography pyzmq

# Step 5: Install NVIDIA CUDA (for CV pipeline on edge server)
# Follow: https://developer.nvidia.com/cuda-downloads
# Minimum: CUDA 12.x + cuDNN 8.x

# Step 6: Clone the StageGate monorepo
git clone https://github.com/stagegate-space/aria-core.git
cd aria-core && pip3 install -r requirements.txt
```

### 2.2 Environment Variables

Create a `.env` file at the root of the repository. Never commit this file.

```bash
# Orbital AI Cloud
ORBITAL_AI_CLOUD_URL=https://api.orbital.stagegate.space
ORBITAL_AI_API_KEY=<your-key>

# ARIA Edge Node
ARIA_EDGE_NODE_ID=edge-node-001
ARIA_FACILITY_ID=facility-sf-001
ARIA_CAMERA_COUNT=8
ARIA_INJECTION_FREQUENCY_HZ=10

# Drift Delta Safety Threshold (meters)
# If delta exceeds this value, trigger Safety Halt
ARIA_HALT_THRESHOLD_METERS=0.5

# mTLS Certificates
MTLS_CA_CERT_PATH=/etc/aria/certs/ca.crt
MTLS_CLIENT_CERT_PATH=/etc/aria/certs/client.crt
MTLS_CLIENT_KEY_PATH=/etc/aria/certs/client.key

# InfluxDB (Benchmark Library)
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=<your-token>
INFLUXDB_ORG=stagegate
INFLUXDB_BUCKET=aria_telemetry
```

---

## 3. Repository Structure

```
aria-core/
├── aria/
│   ├── cv_pipeline/          # Module 1: Computer Vision
│   │   ├── camera_manager.py
│   │   ├── pose_estimator.py
│   │   └── aruco_detector.py
│   ├── waypoint_generator/   # Module 2: TF Hijack & Injection Loop
│   │   ├── drift_calculator.py
│   │   ├── waypoint_injector.py
│   │   └── tf_publisher.py
│   ├── fleet_adapters/       # Module 3: Per-Vendor Adapters
│   │   ├── base_adapter.py
│   │   ├── unitree_adapter.py
│   │   ├── boston_dynamics_adapter.py
│   │   ├── agibot_adapter.py
│   │   ├── agility_adapter.py
│   │   ├── deep_robotics_adapter.py
│   │   ├── fourier_adapter.py
│   │   └── magiclab_adapter.py
│   ├── safety/               # Module 4: Safety Halt Controller
│   │   ├── halt_controller.py
│   │   └── watchdog.py
│   ├── benchmark/            # Module 5: Benchmark Library Pipeline
│   │   ├── telemetry_ingestor.py
│   │   ├── mtbd_calculator.py
│   │   └── report_generator.py
│   └── config/
│       ├── settings.py
│       └── robot_registry.yaml
├── orbital_ai/               # Module 6: Cloud Layer
│   ├── mission_planner.py
│   ├── spatial_map.py
│   └── oem_licensing_api/
│       ├── main.py           # FastAPI application
│       └── routes/
├── dashboard_api/            # Module 7: Dashboard Backend
│   ├── main.py               # FastAPI application
│   └── routes/
├── tests/
├── docker/
│   ├── Dockerfile.edge
│   └── Dockerfile.cloud
├── .env.example
├── requirements.txt
└── README.md
```

---

## 4. Module 1: Computer Vision Pipeline

**File:** `aria/cv_pipeline/pose_estimator.py`
**Purpose:** Ingest frames from the PoE camera network and compute the absolute pose `P_external(t)` for each tracked robot.
**Output:** Publishes `geometry_msgs/PoseWithCovarianceStamped` to the ROS 2 topic `/aria/robot_{id}/pose_external`

### 4.1 Core Logic

The pose estimator runs as a ROS 2 node. It subscribes to raw camera image topics and publishes absolute pose estimates. Two detection strategies are supported and should be implemented in parallel.

**Strategy A — ArUco Marker Detection (Phase 1, use this first):** Each robot in the dev fleet is fitted with a printed ArUco marker (ID 0–99) on its top surface. OpenCV's `cv2.aruco` module detects the marker and computes the pose relative to the camera. The camera's known absolute position in the facility frame is used to project this into global coordinates.

**Strategy B — Learned Visual Detection (Phase 2):** Train a YOLOv8 model on the robot silhouettes. This removes the dependency on physical markers and is required for production deployments where customers cannot modify the robots.

```python
# aria/cv_pipeline/pose_estimator.py — Core skeleton

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped
import cv2
import numpy as np

class ARIAPoseEstimator(Node):
    def __init__(self):
        super().__init__('aria_pose_estimator')
        self.publishers = {}  # robot_id -> ROS2 publisher
        self.camera_extrinsics = self._load_camera_calibration()
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)

    def process_frame(self, frame, camera_id):
        """
        Detects ArUco markers in a camera frame and publishes
        the absolute pose of each detected robot.
        """
        corners, ids, _ = cv2.aruco.detectMarkers(frame, self.aruco_dict)
        if ids is None:
            return

        for i, robot_id in enumerate(ids.flatten()):
            # 1. Estimate pose relative to camera
            rvec, tvec, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners[i], marker_size=0.15,
                cameraMatrix=self.camera_extrinsics[camera_id]['K'],
                distCoeffs=self.camera_extrinsics[camera_id]['D']
            )
            # 2. Transform from camera frame to global facility frame
            global_pose = self._camera_to_global(rvec, tvec, camera_id)

            # 3. Publish with tight covariance (high confidence)
            msg = PoseWithCovarianceStamped()
            msg.header.frame_id = 'map'
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.pose.pose.position.x = global_pose[0]
            msg.pose.pose.position.y = global_pose[1]
            msg.pose.pose.position.z = 0.0
            # Tight covariance = high confidence in external measurement
            msg.pose.covariance[0] = 0.001  # x variance
            msg.pose.covariance[7] = 0.001  # y variance
            self._get_publisher(robot_id).publish(msg)

    def _load_camera_calibration(self):
        """Load camera intrinsics (K, D) and extrinsics from config."""
        # TODO: Load from aria/config/camera_calibration.yaml
        pass

    def _camera_to_global(self, rvec, tvec, camera_id):
        """Apply camera extrinsic transform to get global coordinates."""
        # TODO: Implement affine transformation using nudged library
        pass

    def _get_publisher(self, robot_id):
        if robot_id not in self.publishers:
            topic = f'/aria/robot_{robot_id}/pose_external'
            self.publishers[robot_id] = self.create_publisher(
                PoseWithCovarianceStamped, topic, 10)
        return self.publishers[robot_id]
```

### 4.2 Camera Calibration

Before the CV pipeline can run, every camera in the network must be calibrated. Run the following calibration script once during facility setup.

```bash
# Calibrate all cameras using a checkerboard pattern
python3 aria/cv_pipeline/calibrate_cameras.py \
  --checkerboard_size 9x6 \
  --square_size_mm 25 \
  --output aria/config/camera_calibration.yaml
```

---

## 5. Module 2: Micro-Waypoint Generator (The TF Hijack)

**File:** `aria/waypoint_generator/waypoint_injector.py`
**Purpose:** This is the core of the ARIA engine. It calculates the drift delta and executes the injection loop at 10Hz.
**This module is the primary patent-protected IP. Access is restricted to senior engineers.**

### 5.1 The Injection Loop

The loop runs as a ROS 2 timer callback at a configurable frequency (default: 10Hz).

```python
# aria/waypoint_generator/waypoint_injector.py — Core skeleton

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped, PoseStamped
import numpy as np
from scipy.spatial.transform import Rotation

class ARIAWaypointInjector(Node):
    def __init__(self, robot_id, fleet_adapter):
        super().__init__(f'aria_injector_{robot_id}')
        self.robot_id = robot_id
        self.fleet_adapter = fleet_adapter  # Vendor-specific adapter instance
        self.p_external = None  # Pose from CV pipeline
        self.p_internal = None  # Pose from robot self-report
        self.global_trajectory = []  # List of (x, y) waypoints from Mission Planner
        self.trajectory_index = 0

        # Subscribe to external pose (from CV pipeline)
        self.create_subscription(
            PoseWithCovarianceStamped,
            f'/aria/robot_{robot_id}/pose_external',
            self._on_external_pose, 10)

        # Subscribe to internal pose (from robot's self-report via Fleet Adapter)
        self.create_subscription(
            PoseWithCovarianceStamped,
            f'/aria/robot_{robot_id}/pose_internal',
            self._on_internal_pose, 10)

        # Main injection loop timer
        injection_hz = float(self.declare_parameter('injection_hz', 10.0).value)
        self.create_timer(1.0 / injection_hz, self._injection_loop)

    def _injection_loop(self):
        """
        Core ARIA loop. Runs at 10Hz.
        1. Calculate drift delta.
        2. Select next micro-waypoint on global trajectory.
        3. Transform absolute coordinate to robot's internal frame.
        4. Inject into robot via Fleet Adapter.
        """
        if self.p_external is None or self.p_internal is None:
            return
        if not self.global_trajectory:
            return

        # Step 1: Calculate drift delta (transformation matrix)
        T_delta = self._calculate_drift_delta(self.p_external, self.p_internal)

        # Step 2: Select target absolute coordinate
        # Target is LOOKAHEAD_DISTANCE meters ahead on the global trajectory
        W_abs = self._get_lookahead_waypoint(
            self.p_external, self.global_trajectory,
            lookahead_distance=0.20  # 20cm — tunable parameter
        )
        if W_abs is None:
            self.get_logger().info(f'Robot {self.robot_id}: Trajectory complete.')
            return

        # Step 3: Transform absolute coordinate into robot's internal frame
        # This is the core of the TF Hijack — the robot navigates to where it
        # THINKS the target is, which is physically the correct location.
        W_internal = self._apply_inverse_transform(W_abs, T_delta)

        # Step 4: Inject via vendor-specific fleet adapter
        self.fleet_adapter.inject_waypoint(self.robot_id, W_internal)

    def _calculate_drift_delta(self, p_ext, p_int):
        """
        Calculates the 2D affine transformation matrix T_delta such that:
        T_delta * P_external = P_internal
        Returns a 3x3 homogeneous transformation matrix.
        """
        # Extract (x, y, theta) from both poses
        ext_x = p_ext.pose.pose.position.x
        ext_y = p_ext.pose.pose.position.y
        int_x = p_int.pose.pose.position.x
        int_y = p_int.pose.pose.position.y

        # Translation delta
        dx = int_x - ext_x
        dy = int_y - ext_y

        # Rotation delta (from quaternions)
        ext_q = p_ext.pose.pose.orientation
        int_q = p_int.pose.pose.orientation
        ext_theta = Rotation.from_quat([ext_q.x, ext_q.y, ext_q.z, ext_q.w]).as_euler('xyz')[2]
        int_theta = Rotation.from_quat([int_q.x, int_q.y, int_q.z, int_q.w]).as_euler('xyz')[2]
        d_theta = int_theta - ext_theta

        # Build 3x3 homogeneous transformation matrix
        T = np.array([
            [np.cos(d_theta), -np.sin(d_theta), dx],
            [np.sin(d_theta),  np.cos(d_theta), dy],
            [0,                0,               1]
        ])
        return T

    def _get_lookahead_waypoint(self, p_ext, trajectory, lookahead_distance):
        """
        Finds the next point on the global trajectory that is
        approximately `lookahead_distance` meters ahead of the robot.
        """
        robot_x = p_ext.pose.pose.position.x
        robot_y = p_ext.pose.pose.position.y

        for i in range(self.trajectory_index, len(trajectory)):
            wx, wy = trajectory[i]
            dist = np.sqrt((wx - robot_x)**2 + (wy - robot_y)**2)
            if dist >= lookahead_distance:
                self.trajectory_index = i
                return np.array([wx, wy, 1.0])  # Homogeneous coordinate

        return None  # Trajectory complete

    def _apply_inverse_transform(self, W_abs, T_delta):
        """
        Converts an absolute coordinate into the robot's internal frame.
        W_internal = T_delta * W_abs
        """
        W_internal_h = T_delta @ W_abs
        return W_internal_h[:2]  # Return (x, y)

    def _on_external_pose(self, msg):
        self.p_external = msg

    def _on_internal_pose(self, msg):
        self.p_internal = msg
```

### 5.2 TF Publisher (The SLAM Override)

For ROS 2 robots (Unitree, AgiBot, Deep Robotics, MagicLab), ARIA must also suppress the robot's internal SLAM node and publish the `map → odom` transform directly.

```python
# aria/waypoint_generator/tf_publisher.py

import rclpy
from rclpy.node import Node
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped

class ARIATFPublisher(Node):
    """
    Publishes the map -> odom transform for a robot, overriding
    the robot's internal SLAM (slam_toolbox or nav2_amcl).
    PREREQUISITE: The robot's internal SLAM node must be stopped
    or remapped before this node is started.
    """
    def __init__(self, robot_id, robot_namespace):
        super().__init__(f'aria_tf_publisher_{robot_id}')
        self.robot_namespace = robot_namespace
        self.tf_broadcaster = TransformBroadcaster(self)
        self.current_external_pose = None

        self.create_subscription(
            PoseWithCovarianceStamped,
            f'/aria/robot_{robot_id}/pose_external',
            self._on_external_pose, 10)

        # Publish TF at 30Hz (3x the camera rate for smooth interpolation)
        self.create_timer(1.0 / 30.0, self._publish_tf)

    def _publish_tf(self):
        if self.current_external_pose is None:
            return

        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = 'map'
        t.child_frame_id = f'{self.robot_namespace}/odom'
        t.transform.translation.x = self.current_external_pose.pose.pose.position.x
        t.transform.translation.y = self.current_external_pose.pose.pose.position.y
        t.transform.translation.z = 0.0
        t.transform.rotation = self.current_external_pose.pose.pose.orientation
        self.tf_broadcaster.sendTransform(t)

    def _on_external_pose(self, msg):
        self.current_external_pose = msg
```

---

## 6. Module 3: Fleet Adapters (Per-Vendor)

**File:** `aria/fleet_adapters/base_adapter.py`
**Purpose:** Normalizes the communication protocol between the ARIA Waypoint Injector and each OEM robot.

### 6.1 Base Adapter Interface

All vendor adapters must implement this interface.

```python
# aria/fleet_adapters/base_adapter.py

from abc import ABC, abstractmethod
import numpy as np

class BaseFleetAdapter(ABC):

    @abstractmethod
    def connect(self, robot_ip: str, credentials: dict) -> bool:
        """Establish authenticated connection to the robot."""
        pass

    @abstractmethod
    def inject_waypoint(self, robot_id: str, waypoint: np.ndarray) -> bool:
        """
        Send a single (x, y) waypoint to the robot.
        The waypoint is already transformed into the robot's internal frame.
        Returns True on success, False on failure.
        """
        pass

    @abstractmethod
    def get_internal_pose(self, robot_id: str) -> dict:
        """
        Retrieve the robot's self-reported pose.
        Returns: {'x': float, 'y': float, 'theta': float, 'timestamp': float}
        """
        pass

    @abstractmethod
    def get_status(self, robot_id: str) -> dict:
        """
        Retrieve battery, error codes, and operational state.
        Returns: {'battery_pct': float, 'error_code': str, 'state': str}
        """
        pass

    @abstractmethod
    def trigger_estop(self, robot_id: str) -> bool:
        """Trigger an immediate hardware E-Stop. Must be non-blocking."""
        pass
```

### 6.2 Vendor-Specific Adapter Specifications

The following table defines the exact API call each adapter must implement for `inject_waypoint`.

| Vendor | SDK / Library | `inject_waypoint` Implementation | Notes |
|---|---|---|---|
| **Unitree** | `unitree_ros2` | Publish `nav2_msgs/action/NavigateToPose` to `/{namespace}/navigate_to_pose` | For high-frequency override, publish directly to `/{namespace}/cmd_vel` instead |
| **AgiBot** | `aimdk_msgs` + ROS 2 | Publish to ROS 2 Control Module via `aimdk_msgs/NavigationGoal` | Pull telemetry via HAL interface |
| **Boston Dynamics** | `bosdyn-client` (Python) | Acquire lease → call `RobotCommandService.RobotCommand()` with `SE2TrajectoryCommand` | No ROS 2 TF access; command-level only |
| **Deep Robotics** | `deeprobotics_sdk` | Publish to ROS 2 nav topics; for <10ms latency, use UDP socket to motion host | Use UDP mode for Safety Halt |
| **Fourier Robotics** | `aurora-sdk` + ROS 2 | Publish ROS 2 nav goals; fuse with FSA actuator odometry via `robot_localization` EKF | Do not suppress internal SLAM; use EKF fusion mode |
| **Agility Robotics** | REST/WebSocket (Arc API) | POST spatial constraints to `https://arc.agilityrobotics.com/api/v1/tasks` | Cloud-to-cloud; requires Arc API key |
| **MagicLab** | `MagicDog-Ros2_SDK` | Publish to ROS 2 nav topics; pull status via LCM `magicbot_status` channel | Suppress internal SLAM before starting TF Publisher |

---

## 7. Module 4: Safety Halt Controller

**File:** `aria/safety/halt_controller.py`
**Purpose:** Out-of-band watchdog that monitors the drift delta for all active robots and triggers hardware E-Stops when anomalous conditions are detected.

This module must run as a completely independent process, separate from the injection loop. It must not share any state with the Waypoint Injector to prevent a single point of failure.

```python
# aria/safety/halt_controller.py

import os
import rclpy
from rclpy.node import Node
import numpy as np

HALT_THRESHOLD_METERS = float(os.getenv('ARIA_HALT_THRESHOLD_METERS', 0.5))

class ARIASafetyHaltController(Node):
    """
    Independent watchdog process. Monitors drift delta for all robots.
    Triggers hardware E-Stop if:
    1. Drift delta exceeds HALT_THRESHOLD_METERS (sensor blinding / spoofing).
    2. External cameras detect movement but robot reports stationary (hijack indicator).
    3. External cameras report stationary but robot reports movement (ghost command indicator).
    """
    def __init__(self, fleet_adapters: dict):
        super().__init__('aria_safety_halt_controller')
        self.fleet_adapters = fleet_adapters  # robot_id -> adapter
        self.external_poses = {}
        self.internal_poses = {}

        # Subscribe to all robot poses
        for robot_id in fleet_adapters.keys():
            self.create_subscription(
                PoseWithCovarianceStamped,
                f'/aria/robot_{robot_id}/pose_external',
                lambda msg, rid=robot_id: self._on_external_pose(rid, msg), 10)
            self.create_subscription(
                PoseWithCovarianceStamped,
                f'/aria/robot_{robot_id}/pose_internal',
                lambda msg, rid=robot_id: self._on_internal_pose(rid, msg), 10)

        # Watchdog runs at 20Hz (2x injection rate for safety margin)
        self.create_timer(1.0 / 20.0, self._watchdog_loop)

    def _watchdog_loop(self):
        for robot_id in self.fleet_adapters.keys():
            p_ext = self.external_poses.get(robot_id)
            p_int = self.internal_poses.get(robot_id)
            if p_ext is None or p_int is None:
                continue

            delta = self._calculate_euclidean_delta(p_ext, p_int)

            if delta > HALT_THRESHOLD_METERS:
                self.get_logger().error(
                    f'SAFETY HALT: Robot {robot_id} drift delta = {delta:.3f}m '
                    f'exceeds threshold {HALT_THRESHOLD_METERS}m. '
                    f'Triggering E-Stop.'
                )
                self.fleet_adapters[robot_id].trigger_estop(robot_id)
                # TODO: Push alert to Orbital AI Cloud SIEM

    def _calculate_euclidean_delta(self, p_ext, p_int):
        dx = p_ext.pose.pose.position.x - p_int.pose.pose.position.x
        dy = p_ext.pose.pose.position.y - p_int.pose.pose.position.y
        return np.sqrt(dx**2 + dy**2)

    def _on_external_pose(self, robot_id, msg):
        self.external_poses[robot_id] = msg

    def _on_internal_pose(self, robot_id, msg):
        self.internal_poses[robot_id] = msg
```

---

## 8. Module 5: Benchmark Library Pipeline

**File:** `aria/benchmark/telemetry_ingestor.py`
**Purpose:** Continuously ingests the drift delta for every robot and writes it to InfluxDB. The Benchmark Library is built from this time-series data.

### 8.1 Core Metrics to Track

| Metric | Definition | Calculation |
|---|---|---|
| **Drift Delta Δ(t)** | Euclidean distance between `P_external` and `P_internal` at time `t` | `sqrt((x_ext-x_int)² + (y_ext-y_int)²)` |
| **MTBD** | Mean Time Between Degradation events (delta exceeding 0.1m threshold) | `total_runtime / count(delta > 0.1m)` |
| **Recovery Latency** | Time from degradation event to delta returning below threshold | `t_recovered - t_degraded` |
| **Env. Degradation Score** | Normalized drift rate under specific environmental conditions | `mean(Δ(t)) / baseline_Δ` |

```python
# aria/benchmark/telemetry_ingestor.py

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import os

class BenchmarkTelemetryIngestor:
    def __init__(self):
        self.client = InfluxDBClient(
            url=os.getenv('INFLUXDB_URL'),
            token=os.getenv('INFLUXDB_TOKEN'),
            org=os.getenv('INFLUXDB_ORG')
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.bucket = os.getenv('INFLUXDB_BUCKET')

    def record_delta(self, robot_id: str, vendor: str, model: str,
                     delta_meters: float, facility_id: str):
        point = (
            Point("drift_delta")
            .tag("robot_id", robot_id)
            .tag("vendor", vendor)
            .tag("model", model)
            .tag("facility_id", facility_id)
            .field("delta_meters", delta_meters)
        )
        self.write_api.write(bucket=self.bucket, record=point)
```

---

## 9. Module 6: Cloud Orchestration Layer

**File:** `orbital_ai/mission_planner.py`
**Purpose:** Maintains the Global Spatial Map and generates high-level trajectories for each robot. Communicates with the ARIA Edge Node via a secured mTLS tunnel.

The cloud layer exposes a REST API that the ARIA Edge Node polls for updated mission assignments.

### 9.1 Key API Endpoints (Cloud → Edge)

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/missions/{facility_id}` | `GET` | Returns active mission assignments for all robots at a facility |
| `/api/v1/trajectory/{robot_id}` | `GET` | Returns the current global trajectory (list of waypoints) for a specific robot |
| `/api/v1/map/{facility_id}` | `GET` | Returns the current Global Spatial Map as a nav_msgs/OccupancyGrid |
| `/api/v1/telemetry` | `POST` | Edge node posts drift delta telemetry to the Benchmark Library |
| `/api/v1/alerts` | `POST` | Edge node posts Safety Halt events to the cloud SIEM |

---

## 10. Module 7: Fleet Management Dashboard (API Contract)

This module is contracted to a third-party web development agency. The following API contract defines what the backend must provide to the React/Next.js frontend.

### 10.1 Required Dashboard Endpoints

| Endpoint | Method | Response | Notes |
|---|---|---|---|
| `/api/dashboard/fleet` | `GET` | All robots, current pose, battery, status | Refreshes every 2s |
| `/api/dashboard/robot/{id}` | `GET` | Full robot detail: vendor, model, MTBD, current task | The "business card" panel |
| `/api/dashboard/tasks` | `GET` / `POST` | Active task list; create new task assignment | Dispatches via Open-RMF |
| `/api/dashboard/alerts` | `GET` | Safety Halt events and drift anomalies | Real-time via WebSocket |
| `/api/dashboard/benchmark/{vendor}` | `GET` | Benchmark Library data for a specific OEM | Requires OEM API key |

### 10.2 Frontend Design Requirements for Contractor

The contractor must implement the following UI specifications:

- **Color:** Amber orange (`#F59E0B`) for all CTAs, active states, and alert indicators.
- **Robot Cards:** Each robot card must display only images of the robot and its current task context. No OEM marketing materials.
- **Industry Tabs:** Top-level navigation must use tabs: `Humanoids`, `Cleaning`, `Delivery`, `Inventory`.
- **CRM Panel:** Clicking a robot model opens a "business card" panel with an AI-generated brief on the OEM, their tradeshow attendance, and a draft message feature for outreach.
- **Role-Based Access:** Three roles: `Admin` (full control), `Operator` (task dispatch only), `Viewer` (read-only analytics).

---

## 11. Cybersecurity Implementation

### 11.1 Network Segmentation (VLAN Architecture)

Three VLANs must be configured on the managed switch at every deployment site.

| VLAN | ID | Members | Egress Policy |
|---|---|---|---|
| **Camera Network** | VLAN 10 | All PoE cameras | No egress; inbound to Edge Node only |
| **Robot Subnet** | VLAN 20 | All robots | No internet egress; communicate with Edge Node only |
| **Edge Node Management** | VLAN 30 | ARIA Edge Node | Outbound mTLS tunnel to Orbital AI Cloud only |

### 11.2 mTLS Certificate Generation

```bash
# Generate CA certificate
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 1825 -key ca.key -out ca.crt \
  -subj "/C=US/ST=CA/O=StageGate/CN=ARIA-CA"

# Generate Edge Node client certificate
openssl genrsa -out client.key 4096
openssl req -new -key client.key -out client.csr \
  -subj "/C=US/ST=CA/O=StageGate/CN=edge-node-001"
openssl x509 -req -days 365 -in client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt

# Copy to the ARIA config directory
sudo mkdir -p /etc/aria/certs
sudo cp ca.crt client.crt client.key /etc/aria/certs/
sudo chmod 600 /etc/aria/certs/client.key
```

### 11.3 SROS2 (Secure ROS 2)

All internal ROS 2 DDS traffic between the ARIA Edge Node and robots must be encrypted using SROS2.

```bash
# Generate SROS2 keystore
ros2 security create_keystore /etc/aria/sros2_keystore

# Create keys for each node
ros2 security create_enclave /etc/aria/sros2_keystore /aria_pose_estimator
ros2 security create_enclave /etc/aria/sros2_keystore /aria_waypoint_injector
ros2 security create_enclave /etc/aria/sros2_keystore /aria_safety_halt

# Set environment variables for all ARIA processes
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce
export ROS_SECURITY_KEYSTORE=/etc/aria/sros2_keystore
```

---

## 12. Sprint Backlog

The following backlog is organized by priority. Complete all Sprint 1 items before beginning Sprint 2.

### Sprint 1 — Core Loop (Weeks 1–4)
**Goal:** Achieve a working end-to-end injection loop with one Unitree robot in the lab.

| # | Task | Owner | Complexity |
|---|---|---|---|
| S1-01 | Set up dev lab: cameras, edge server, Unitree G1, private Wi-Fi | Hardware | Medium |
| S1-02 | Calibrate all cameras; generate `camera_calibration.yaml` | CV Engineer | Low |
| S1-03 | Implement `ARIAPoseEstimator` with ArUco detection (Strategy A) | CV Engineer | Medium |
| S1-04 | Implement `ARIAWaypointInjector` core loop | Robotics Engineer | High |
| S1-05 | Implement `Unitree Fleet Adapter` (`inject_waypoint` + `get_internal_pose`) | Robotics Engineer | Low |
| S1-06 | Implement `ARIATFPublisher` and suppress Unitree's internal SLAM | Robotics Engineer | Medium |
| S1-07 | Implement `ARIASafetyHaltController` as independent process | Robotics Engineer | Medium |
| S1-08 | Validate 10Hz injection loop latency end-to-end | All | High |

### Sprint 2 — Multi-Robot & Benchmark (Weeks 5–8)
**Goal:** Add AgiBot to the fleet, begin collecting Benchmark Library data.

| # | Task | Owner | Complexity |
|---|---|---|---|
| S2-01 | Implement `AgiBot Fleet Adapter` | Robotics Engineer | Medium |
| S2-02 | Integrate Open-RMF as traffic scheduler above ARIA | Robotics Engineer | High |
| S2-03 | Implement `BenchmarkTelemetryIngestor` with InfluxDB | Backend Engineer | Low |
| S2-04 | Implement `MTBDCalculator` and `ReportGenerator` | Backend Engineer | Medium |
| S2-05 | Deploy Orbital AI Cloud backend (FastAPI + AWS) | Cloud Engineer | Medium |
| S2-06 | Implement Cloud → Edge trajectory API | Cloud Engineer | Medium |

### Sprint 3 — Boston Dynamics & Dashboard (Weeks 9–12)
**Goal:** Validate command-level override with Spot; launch internal dashboard.

| # | Task | Owner | Complexity |
|---|---|---|---|
| S3-01 | Implement `Boston Dynamics Fleet Adapter` (gRPC) | Robotics Engineer | High |
| S3-02 | Contract and brief Fleet Dashboard agency | Product | Low |
| S3-03 | Implement Dashboard backend API (FastAPI) | Backend Engineer | Medium |
| S3-04 | Implement SROS2 and mTLS across all nodes | Security Engineer | High |
| S3-05 | Conduct third-party security audit | External | High |

### Sprint 4 — Production Hardening & Pilot (Weeks 13–18)
**Goal:** Deploy at first customer pilot site.

| # | Task | Owner | Complexity |
|---|---|---|---|
| S4-01 | Implement Fourier (EKF fusion), Deep Robotics, MagicLab adapters | Robotics Engineer | Medium |
| S4-02 | Implement Agility Robotics (Arc cloud-to-cloud) adapter | Robotics Engineer | High |
| S4-03 | Train YOLOv8 model for markerless robot detection (Strategy B) | ML Engineer | High |
| S4-04 | Deploy full hardware stack at pilot site | Hardware | High |
| S4-05 | Begin collecting live Benchmark Library data for OEM licensing | All | — |

---

## 13. Hardware Setup Checklist

Complete this checklist before beginning Sprint 1.

| Item | Specification | Vendor | Status |
|---|---|---|---|
| Industrial PoE Cameras (×8 minimum) | Global shutter, 60fps, PoE+ | Basler acA1920-40gc or FLIR BFS-PGE-50S5C | ☐ Order |
| GPU Edge Server | NVIDIA RTX 4000 SFF Ada, 64GB RAM, Dual 10GbE NIC | Dell PowerEdge XR4520c | ☐ Order |
| Managed PoE+ Switch | 24-port, PoE+, VLAN support | Cisco Catalyst 1000 | ☐ Order |
| Wi-Fi 6 Access Points (×2) | Wi-Fi 6 (802.11ax), VLAN tagging | Aruba AP-635 | ☐ Order |
| ArUco Marker Sheets | 15cm × 15cm, laminated, IDs 0–10 | Print in-house | ☐ Print |
| Unitree G1 (dev unit) | Standard configuration | Unitree Robotics | ☐ Order |
| AgiBot A2 (dev unit) | Standard configuration | AgiBot | ☐ Order |
| Cat6A Ethernet Cable (×50m) | Shielded, for camera runs | Any | ☐ Order |
| Ceiling Mounting Hardware | Camera mounts, cable management | Any | ☐ Order |
