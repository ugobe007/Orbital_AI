"""Lab runtime factories for Sprint D cutover."""
import os
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "aruco" / "frame_001.json"


def test_build_pose_source_sim(monkeypatch):
    monkeypatch.setenv("ARIA_CV_MODE", "sim")
    import importlib
    import aria_edge.config as cfg
    importlib.reload(cfg)
    import aria_edge.lab_runtime as lab
    importlib.reload(lab)
    from aria_edge.cv_pipeline import SimulatedCVPipeline

    assert isinstance(lab.build_pose_source(), SimulatedCVPipeline)


def test_build_lab_edge_aruco_tick(monkeypatch):
    monkeypatch.setenv("ARIA_CV_MODE", "aruco")
    monkeypatch.setenv("ARIA_ARUCO_FIXTURE", str(FIXTURE))
    monkeypatch.setenv("ARIA_UNITREE_HARDWARE", "0")
    import importlib
    import aria_edge.config as cfg
    importlib.reload(cfg)
    import aria_edge.lab_runtime as lab
    importlib.reload(lab)
    from aria_edge.aruco import ArucoPoseEstimator, load_recorded_frame
    from aria_edge.types import Pose2D

    assert isinstance(lab.build_pose_source(), ArucoPoseEstimator)
    frame, _, _ = load_recorded_frame(FIXTURE)
    agent = lab.build_lab_edge(cloud_enabled=False)
    res = agent.tick(frame, {"rbt-01": Pose2D(12.4, 7.7), "rbt-02": Pose2D(11.0, 8.7)})
    assert len(res) == 1  # only Unitree binding by default
    assert res[0]["robot_id"] == "rbt-01"
    assert res[0]["action"] in ("nominal", "correct", "inject", "halt")
