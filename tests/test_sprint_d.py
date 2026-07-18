"""Sprint D — ArUco recorded frames, Unitree adapter, 10 Hz latency bench."""
from pathlib import Path

import pytest

from aria_edge.aruco import ArucoPoseEstimator, CameraExtrinsics, load_recorded_frame
from aria_edge.latency_bench import LatencyBench
from aria_edge.types import CameraFrame, MarkerObservation, Pose2D
from fleet_adapters import get_adapter
from fleet_adapters.unitree import UnitreeAdapter


FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "aruco" / "frame_001.json"


def test_load_recorded_aruco_frame():
    frame, mapping, ext = load_recorded_frame(FIXTURE)
    assert frame.camera_id == "cam-0"
    assert len(frame.markers) == 2
    assert mapping[1] == "rbt-01"
    assert ext.x == 12.0


def test_aruco_estimator_on_recorded_frame():
    frame, mapping, ext = load_recorded_frame(FIXTURE)
    est = ArucoPoseEstimator(marker_to_robot=mapping, extrinsics={ext.camera_id: ext})
    dets = est.process_frame(frame)
    assert {d.robot_id for d in dets} == {"rbt-01", "rbt-02"}
    # camera at (12,8) + tvec (0.5, -0.25) → world ≈ (12.5, 7.75)
    r1 = next(d for d in dets if d.robot_id == "rbt-01")
    assert abs(r1.world_pose.x - 12.5) < 1e-6
    assert abs(r1.world_pose.y - 7.75) < 1e-6
    assert r1.confidence >= 0.9


def test_aruco_empty_without_markers_or_pixels():
    est = ArucoPoseEstimator()
    frame = CameraFrame(camera_id="cam-0", ts=0.0, width=640, height=480)
    assert est.process_frame(frame) == []


def test_camera_to_global_rotation():
    ext = CameraExtrinsics(camera_id="c", x=0.0, y=0.0, yaw=3.1415926535)  # ~π
    pose = ext.camera_to_global((1.0, 0.0))
    assert pose.x == pytest.approx(-1.0, abs=1e-6)


def test_unitree_adapter_from_registry():
    a = get_adapter("Unitree", "rbt-01")
    assert isinstance(a, UnitreeAdapter)
    assert a.inject_waypoint("rbt-01", (1.0, 2.0)) is True
    assert a.injected[-1] == (1.0, 2.0)
    assert a.protocol_contract() is not None


def test_unitree_hardware_mode_requires_rclpy():
    with pytest.raises(RuntimeError, match="rclpy"):
        UnitreeAdapter("rbt-01", use_hardware=True)


def test_latency_bench_passes_on_sim_unitree():
    a = get_adapter("Unitree", "rbt-01")
    report = LatencyBench(hz=10, samples=15).run(a)
    assert report.samples == 15
    assert report.budget_ms == 100.0
    assert report.pass_ is True
    assert report.p95_ms <= report.budget_ms


def test_latency_bench_fails_when_inject_too_slow():
    a = UnitreeAdapter("rbt-01", inject_latency_s=0.15)  # 150 ms > 100 ms budget
    report = LatencyBench(hz=10, samples=5).run(a)
    assert report.pass_ is False
