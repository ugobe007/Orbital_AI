"""Lab cutover factories — assemble PoseSource / adapters from edge env (Sprint D).

Keeps hardware switches in one place so the edge agent constructor stays simple.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fleet_adapters import get_adapter

from . import config
from .aruco import ArucoPoseEstimator, CameraExtrinsics, load_recorded_frame
from .cv_pipeline import PoseSource, SimulatedCVPipeline
from .edge_agent import CloudSync, EdgeAgent, RobotBinding
from .mtls import load_mtls_config

logger = logging.getLogger(__name__)


def build_pose_source() -> PoseSource:
    """``ARIA_CV_MODE=sim|aruco`` (default sim)."""
    mode = (config.edge_settings.cv_mode or "sim").strip().lower()
    if mode in ("sim", "simulated", ""):
        return SimulatedCVPipeline()
    if mode == "aruco":
        return _build_aruco_estimator()
    raise ValueError(f"Unknown ARIA_CV_MODE={mode!r}; use sim or aruco")


def _build_aruco_estimator() -> ArucoPoseEstimator:
    mapping: dict[int, str] = {}
    extrinsics: dict[str, CameraExtrinsics] = {}
    settings = config.edge_settings

    map_path = (settings.aruco_marker_map or "").strip()
    if map_path:
        data = json.loads(Path(map_path).read_text())
        for item in data.get("marker_map", data if isinstance(data, list) else []):
            if isinstance(item, dict):
                mapping[int(item["marker_id"])] = str(item["robot_id"])
        cal = data.get("calibration") or {}
        cam_id = str(data.get("camera_id", "cam-0"))
        if cal or data.get("camera_id"):
            extrinsics[cam_id] = CameraExtrinsics(
                camera_id=cam_id,
                x=float(cal.get("x", 0.0)),
                y=float(cal.get("y", 0.0)),
                z=float(cal.get("z", 3.0)),
                yaw=float(cal.get("yaw", 0.0)),
            )

    fixture = (settings.aruco_fixture or "").strip()
    if fixture and Path(fixture).is_file():
        _frame, fix_map, ext = load_recorded_frame(fixture)
        mapping = {**fix_map, **mapping}
        extrinsics[ext.camera_id] = ext

    if not mapping:
        logger.warning("[lab] ArUco mode with empty marker map — detections will use marker-N ids")
    return ArucoPoseEstimator(marker_to_robot=mapping, extrinsics=extrinsics)


def build_unitree_binding(robot_id: str = "rbt-01", model: str = "G1") -> RobotBinding:
    adapter = get_adapter(
        "Unitree",
        robot_id,
        use_hardware=config.edge_settings.unitree_hardware,
    )
    return RobotBinding(robot_id=robot_id, vendor="Unitree", model=model, adapter=adapter)


def build_lab_edge(
    bindings: list[RobotBinding] | None = None,
    *,
    cloud_enabled: bool | None = None,
) -> EdgeAgent:
    """Edge agent configured for lab cutover (CV mode + optional mTLS cloud sync)."""
    mtls = load_mtls_config()
    enabled = (
        config.edge_settings.cloud_sync_enabled if cloud_enabled is None else cloud_enabled
    )
    cloud = CloudSync(enabled=enabled, mtls=mtls)
    return EdgeAgent(
        bindings or [build_unitree_binding()],
        cv=build_pose_source(),
        cloud=cloud,
    )
