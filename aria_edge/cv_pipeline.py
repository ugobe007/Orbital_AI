"""Module 1 — Computer Vision Pipeline.

Ground truth for the whole platform. Overhead cameras + a detector localize every robot
in world coordinates; that external pose is what the drift estimate is measured against
(the robot's own SLAM/odometry is *not* trusted). This is the sensor the "TF Hijack"
correction and the safety halt both key off of.

Real backend: multi-camera capture -> undistort/rectify -> detector (YOLO/keypoint) ->
homography to floor plane -> per-robot world pose, fused across cameras. That is CUDA/
camera-bound, so it lives behind the ``PoseSource`` interface. The ``SimulatedCVPipeline``
returns detections straight from a ground-truth hint so the edge loop runs in CI.
"""
from __future__ import annotations

from typing import Protocol

from .types import CameraFrame, Detection, Pose2D


class PoseSource(Protocol):
    """Anything that turns a synchronized capture into world-frame robot detections."""

    def process_frame(self, frame: CameraFrame) -> list[Detection]: ...


class SimulatedCVPipeline:
    """Deterministic stand-in: reports each robot at its ground-truth pose, with a small
    fixed confidence so downstream code exercises the confidence path."""

    def __init__(self, confidence: float = 0.98) -> None:
        self.confidence = confidence

    def process_frame(self, frame: CameraFrame) -> list[Detection]:
        return [
            Detection(robot_id=rid, world_pose=pose, confidence=self.confidence)
            for rid, pose in frame.ground_truth.items()
        ]


class CudaCVPipeline:
    """Hardware backend placeholder — prefer ``ArucoPoseEstimator`` for Strategy A.

    Kept as an explicit NotImplemented so deployments can't silently fall back to the
    simulator on real hardware without opting into a detector.
    """

    def __init__(self, *_args, **_kwargs) -> None:  # noqa: D401
        self._ready = False

    def process_frame(self, frame: CameraFrame) -> list[Detection]:  # pragma: no cover - hw
        raise NotImplementedError(
            "CudaCVPipeline is reserved for YOLO/CUDA Strategy B; use "
            "ArucoPoseEstimator (recorded or live OpenCV) for Sprint D1."
        )


def external_pose(detections: list[Detection], robot_id: str) -> Pose2D | None:
    """Convenience: pull one robot's world pose out of a detection batch."""
    for d in detections:
        if d.robot_id == robot_id:
            return d.world_pose
    return None
