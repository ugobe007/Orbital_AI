"""Sprint D1 — ArUco pose estimation behind the PoseSource interface.

Works two ways:
  1. **Recorded frames** (CI / offline): ``CameraFrame.markers`` already holds
     detections from a capture session — no OpenCV required.
  2. **Live / pixel frames**: if ``opencv-python`` is installed and ``frame.pixels``
     is a grayscale or BGR buffer, OpenCV ``cv2.aruco`` runs Strategy A.

Camera extrinsics project camera-frame marker poses into the facility ``map`` frame.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .types import CameraFrame, Detection, MarkerObservation, Pose2D


@dataclass(frozen=True)
class CameraExtrinsics:
    """Overhead camera pose in the facility frame (meters, radians)."""
    camera_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 3.0
    yaw: float = 0.0  # camera facing; 0 = +X facility
    # Intrinsics (optional — used only for live OpenCV path)
    fx: float = 800.0
    fy: float = 800.0
    cx: float = 320.0
    cy: float = 240.0
    marker_size_m: float = 0.15

    def camera_to_global(self, tvec: Sequence[float], yaw_cam: float = 0.0) -> Pose2D:
        """Project a marker translation in the camera frame onto the floor plane.

        Assumes a downward-looking overhead camera: lateral offsets map directly
        into the facility XY after rotating by the camera's yaw.
        """
        tx, ty = float(tvec[0]), float(tvec[1])
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        gx = self.x + c * tx - s * ty
        gy = self.y + s * tx + c * ty
        return Pose2D(gx, gy, _wrap(self.yaw + yaw_cam))


@dataclass
class ArucoPoseEstimator:
    """Strategy A pose source — ArUco markers → world detections."""

    marker_to_robot: dict[int, str] = field(default_factory=dict)
    extrinsics: dict[str, CameraExtrinsics] = field(default_factory=dict)
    default_confidence: float = 0.95
    dictionary_name: str = "DICT_4X4_100"

    def process_frame(self, frame: CameraFrame) -> list[Detection]:
        markers = list(frame.markers or [])
        if not markers and frame.pixels is not None:
            markers = self._detect_opencv(frame)

        ext = self.extrinsics.get(frame.camera_id) or CameraExtrinsics(camera_id=frame.camera_id)
        out: list[Detection] = []
        for m in markers:
            robot_id = self.marker_to_robot.get(m.marker_id, f"marker-{m.marker_id}")
            pose = ext.camera_to_global(m.tvec, m.yaw)
            out.append(
                Detection(
                    robot_id=robot_id,
                    world_pose=pose,
                    confidence=self.default_confidence,
                    bbox=m.bbox,
                )
            )
        return out

    def _detect_opencv(self, frame: CameraFrame) -> list[MarkerObservation]:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            return []

        ext = self.extrinsics.get(frame.camera_id) or CameraExtrinsics(camera_id=frame.camera_id)
        arr = np.frombuffer(frame.pixels, dtype=np.uint8)
        # Prefer encoded image decode; fall back to raw HxW reshape if size matches.
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            try:
                img = arr.reshape((frame.height, frame.width))
            except ValueError:
                return []

        dict_id = getattr(cv2.aruco, self.dictionary_name, cv2.aruco.DICT_4X4_100)
        dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
        corners, ids, _ = cv2.aruco.detectMarkers(img, dictionary)
        if ids is None:
            return []

        K = np.array([[ext.fx, 0, ext.cx], [0, ext.fy, ext.cy], [0, 0, 1]], dtype=float)
        D = np.zeros(5)
        observations: list[MarkerObservation] = []
        for i, mid in enumerate(ids.flatten()):
            rvecs, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
                corners[i], ext.marker_size_m, K, D,
            )
            t = tvecs[0][0]
            # Yaw from rotation vector (z component approximation for top-down)
            yaw = float(rvecs[0][0][2]) if rvecs is not None else 0.0
            c = corners[i][0]
            x0, y0 = int(c[:, 0].min()), int(c[:, 1].min())
            x1, y1 = int(c[:, 0].max()), int(c[:, 1].max())
            observations.append(
                MarkerObservation(
                    marker_id=int(mid),
                    tvec=(float(t[0]), float(t[1]), float(t[2])),
                    yaw=yaw,
                    bbox=(x0, y0, x1 - x0, y1 - y0),
                )
            )
        return observations


def load_recorded_frame(path: str | Path) -> tuple[CameraFrame, dict[int, str], CameraExtrinsics]:
    """Load a recorded-frame JSON fixture (Sprint D1 offline path)."""
    data = json.loads(Path(path).read_text())
    markers = [
        MarkerObservation(
            marker_id=int(m["marker_id"]),
            tvec=tuple(m["tvec"]),  # type: ignore[arg-type]
            yaw=float(m.get("yaw", 0.0)),
            bbox=tuple(m["bbox"]) if m.get("bbox") else None,  # type: ignore[arg-type]
        )
        for m in data.get("markers", [])
    ]
    frame = CameraFrame(
        camera_id=data["camera_id"],
        ts=float(data.get("ts", 0.0)),
        width=int(data.get("width", 640)),
        height=int(data.get("height", 480)),
        markers=markers,
    )
    cal = data.get("calibration", {})
    ext = CameraExtrinsics(
        camera_id=frame.camera_id,
        x=float(cal.get("x", 0.0)),
        y=float(cal.get("y", 0.0)),
        z=float(cal.get("z", 3.0)),
        yaw=float(cal.get("yaw", 0.0)),
    )
    mapping = {int(k): str(v) for k, v in (data.get("marker_to_robot") or {}).items()}
    # Also accept list form [{"marker_id": 1, "robot_id": "rbt-01"}]
    for item in data.get("marker_map", []) or []:
        mapping[int(item["marker_id"])] = str(item["robot_id"])
    return frame, mapping, ext


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))
