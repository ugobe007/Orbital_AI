"""Shared value types for the ARIA Edge Node.

Deliberately plain dataclasses (no pydantic) so the edge stays dependency-light and can
run on a constrained on-prem box. The cloud contract (pydantic) lives in
``orbital_cloud.models``; the edge agent maps these into that contract when it syncs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    theta: float = 0.0

    def distance_to(self, other: "Pose2D") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass
class CameraFrame:
    """One synchronized capture from the overhead camera rig (Module 1 input)."""
    camera_id: str
    ts: float
    width: int
    height: int
    # Real frames carry pixel data; the scaffold passes a ground-truth hint the
    # simulated detector "sees" so the loop is deterministic in tests.
    ground_truth: dict[str, Pose2D] = field(default_factory=dict)


@dataclass
class Detection:
    """A robot localized by the CV pipeline in world coordinates."""
    robot_id: str
    world_pose: Pose2D
    confidence: float
    bbox: Optional[tuple[int, int, int, int]] = None  # x, y, w, h (px)


@dataclass
class DriftEstimate:
    """External (camera ground-truth) vs internal (robot self-report) divergence."""
    robot_id: str
    external: Pose2D
    internal: Pose2D

    @property
    def delta_m(self) -> float:
        return self.external.distance_to(self.internal)


@dataclass
class PoseCorrection:
    """Output of the Micro-Waypoint Generator (Module 2) — the corrective transform the
    edge injects into the robot's TF tree / nav goal ("TF Hijack")."""
    robot_id: str
    dx: float
    dy: float
    dtheta: float
    # A short corrective velocity the local controller applies at 10Hz.
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0


@dataclass
class HaltDecision:
    robot_id: str
    halt: bool
    reason: str = ""
    # One of orbital_cloud.models.AlertType values when halt is True.
    alert_type: Optional[str] = None
