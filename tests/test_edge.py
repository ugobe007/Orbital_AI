"""ARIA Edge Node scaffolds — CV pipeline, waypoint generator, safety halt, edge agent."""
from aria_edge.cv_pipeline import SimulatedCVPipeline, external_pose
from aria_edge.edge_agent import CloudSync, EdgeAgent, RobotBinding
from aria_edge.safety_halt import SafetyHaltController
from aria_edge.types import CameraFrame, DriftEstimate, Pose2D
from aria_edge.waypoint_generator import MicroWaypointGenerator
from fleet_adapters import get_adapter


def _frame(gt: dict[str, Pose2D]) -> CameraFrame:
    return CameraFrame(camera_id="cam-0", ts=0.0, width=640, height=480, ground_truth=gt)


def test_cv_pipeline_localizes_ground_truth():
    cv = SimulatedCVPipeline()
    dets = cv.process_frame(_frame({"rbt-01": Pose2D(1.0, 2.0)}))
    assert len(dets) == 1
    assert external_pose(dets, "rbt-01") == Pose2D(1.0, 2.0)


def test_waypoint_generator_clamps_correction():
    gen = MicroWaypointGenerator(gain=1.5, max_mps=0.4)
    corr = gen.compute_correction("rbt-01", Pose2D(1.0, 0.0), Pose2D(0.0, 0.0))
    assert corr.dx == 1.0
    assert abs(corr.vx) <= 0.4  # clamped even though 1.0 * 1.5 > 0.4


def test_safety_halt_on_drift_and_consistency():
    ctl = SafetyHaltController(halt_threshold_m=0.5)
    drift = DriftEstimate("r", Pose2D(0.0, 0.0), Pose2D(0.6, 0.0))
    assert ctl.evaluate(drift).alert_type == "drift_exceeded"

    still = DriftEstimate("r", Pose2D(0.0, 0.0), Pose2D(0.0, 0.0))
    assert ctl.evaluate(still, external_moved_m=0.3, internal_moved_m=0.0).alert_type == "hijack_suspected"
    assert ctl.evaluate(still, external_moved_m=0.0, internal_moved_m=0.3).alert_type == "ghost_command"
    assert ctl.evaluate(still).halt is False


def _agent() -> tuple[EdgeAgent, CloudSync, object]:
    adapter = get_adapter("Unitree", "rbt-01")
    cloud = CloudSync(enabled=False)
    agent = EdgeAgent([RobotBinding("rbt-01", "Unitree", "G1", adapter=adapter)], cloud=cloud)
    return agent, cloud, adapter


def test_edge_agent_nominal_posts_telemetry_only():
    agent, cloud, adapter = _agent()
    res = agent.tick(_frame({"rbt-01": Pose2D(0.0, 0.0)}), {"rbt-01": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "nominal"
    assert len(cloud.telemetry_sent) == 1 and not cloud.alerts_sent


def test_edge_agent_corrects_degraded_drift():
    agent, cloud, adapter = _agent()
    res = agent.tick(_frame({"rbt-01": Pose2D(0.2, 0.0)}), {"rbt-01": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "correct"
    assert adapter.commands  # a corrective cmd_vel was issued


def test_edge_agent_halts_and_alerts_on_excess_drift():
    agent, cloud, adapter = _agent()
    res = agent.tick(_frame({"rbt-01": Pose2D(0.8, 0.0)}), {"rbt-01": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "halt"
    assert cloud.alerts_sent and cloud.alerts_sent[0]["type"] == "drift_exceeded"
    assert adapter._halted is True
