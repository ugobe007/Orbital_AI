"""ARIA Edge Node — the on-premise, hardware-bound half of Orbital AI.

This package scaffolds the patent-core edge modules that run next to the robots on a
GPU server inside the facility (never in the cloud, never in the real-time control
path from a remote region):

    Module 1  cv_pipeline          Computer Vision Pipeline — overhead cameras -> ground-truth pose
    Module 2  waypoint_generator   Micro-Waypoint Generator / "TF Hijack" — corrective transform
    Module 4  safety_halt          Safety Halt Controller — drift/hijack/ghost-command E-Stop
              edge_agent           Ties the modules together and syncs the Orbital AI Cloud

The real implementations bind to cameras, CUDA, and ROS 2 (rclpy / tf2_ros). These
scaffolds define the exact interfaces and ship deterministic *simulated* backends so the
whole edge->cloud loop is runnable and testable today without hardware. Swap a simulated
backend for the hardware one behind the same interface — no downstream changes.

Runtime placement (see build guide):
    Robot Network (air-gapped VLAN)  <->  ARIA Edge Node (this package)  --outbound mTLS-->  Orbital AI Cloud
"""

__version__ = "0.1.0"
