"""Sprint G5–G7 — people on the floor: check-ins, human-proximity safety, person assignees."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from aria_edge.edge_agent import CloudSync, EdgeAgent, RobotBinding
from aria_edge.safety_halt import SafetyHaltController
from aria_edge.types import CameraFrame, Pose2D
from fleet_adapters import get_adapter
from orbital_cloud import simulator
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.models import (
    AlertType,
    AssigneeType,
    RecommendationKind,
    RecommendationStatus,
    RobotState,
    WorkerPoseIn,
    WorkerStatus,
)
from orbital_cloud.orchestrator import Orchestrator
from orbital_cloud.store import Store, store
from orbital_cloud.workers import SimCrowd, proximity_speed_cap, worker_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _empty_floor():
    worker_store.clear()
    simulator._last_human_alert.clear()
    yield
    worker_store.clear()
    simulator._last_human_alert.clear()
    for robot in store.robots.values():
        if robot.state == RobotState.HALTED:
            store.resume(robot.id)
        robot.nav_queue = []


def _place(worker_id: str, x: float, y: float, **kw) -> None:
    worker_store.upsert(worker_id, WorkerPoseIn(x=x, y=y, **kw))


# ── G5: workers ────────────────────────────────────────────────────────────────

def test_check_in_list_and_check_out():
    r = client.post("/api/v1/workers/badge-17/pose", json={"x": 4.0, "y": 3.0, "role": "picker"})
    assert r.status_code == 200
    assert r.json()["display_name"] is None  # pseudonymous by default
    ids = [w["id"] for w in client.get("/api/dashboard/workers").json()]
    assert ids == ["badge-17"]
    assert client.get("/api/dashboard/fleet").json()["workers"][0]["id"] == "badge-17"
    assert client.get("/api/v1/workers").json()[0]["pose"] == {"x": 4.0, "y": 3.0}
    assert client.delete("/api/v1/workers/badge-17").status_code == 200
    assert client.get("/api/dashboard/workers").json() == []
    assert client.delete("/api/v1/workers/badge-17").status_code == 404


def test_check_in_rejects_off_floor_positions_and_bad_ids():
    assert client.post("/api/v1/workers/w1/pose", json={"x": -1.0, "y": 3.0}).status_code == 422
    assert client.post("/api/v1/workers/bad id!/pose", json={"x": 1.0, "y": 1.0}).status_code == 422


def test_stale_check_ins_drop_off():
    worker_store.upsert("w1", WorkerPoseIn(x=1.0, y=1.0), now=time.time() - settings.worker_stale_s - 1)
    _place("w2", 2.0, 2.0)
    assert [w.id for w in worker_store.list()] == ["w2"]


def test_sim_crowd_walks():
    crowd = SimCrowd()
    crowd.spawn(2)
    start = {w.id: (w.pose.x, w.pose.y) for w in worker_store.list()}
    assert len(start) == 2
    now = time.time() + 60  # past any dwell
    for i in range(20):
        crowd.tick(0.5, now=now + i * 0.5)
    moved = [w for w in worker_store.list() if (w.pose.x, w.pose.y) != start[w.id]]
    assert moved


# ── G6: human-proximity safety ─────────────────────────────────────────────────

def _moving_robot(rid: str = "rbt-02"):
    simulator.prime()
    robot = store.robots[rid]
    robot.state = RobotState.ACTIVE
    robot.error_code = None
    robot.pose_external = robot.pose_external.model_copy(update={"x": 13.5, "y": 1.0})
    store.set_waypoints(rid, [(13.5, 13.0)], persist=False)
    return robot


def test_robot_slows_inside_the_slow_radius():
    robot = _moving_robot()
    robot.speed_mps = 1.0
    assert simulator._zone_speed(robot) == 1.0
    _place("w1", 13.5 + settings.human_slow_radius_m - 0.2, 1.0)
    assert proximity_speed_cap(13.5, 1.0) == settings.human_slow_speed_mps
    assert simulator._zone_speed(robot) == settings.human_slow_speed_mps


def test_robot_stops_for_a_person_then_resumes_when_they_leave():
    robot = _moving_robot()
    _place("w1", 13.5, 1.5)

    alerts = asyncio.run(simulator._tick(0.2))
    assert robot.state == RobotState.HALTED and robot.error_code == simulator.HUMAN_STOP
    [alert] = [a for a in alerts if a.robot_id == robot.id]
    assert alert.type == AlertType.HUMAN_PROXIMITY and alert.severity.value == "warning"

    before = (robot.pose_external.x, robot.pose_external.y)
    alerts = asyncio.run(simulator._tick(0.2))
    assert robot.state == RobotState.HALTED
    assert (robot.pose_external.x, robot.pose_external.y) == before
    assert not [a for a in alerts if a.robot_id == robot.id]  # one alert per stop, not per tick

    _place("w1", 13.5, 1.0 + settings.human_stop_radius_m + 0.2)  # inside release margin
    asyncio.run(simulator._tick(0.2))
    assert robot.state == RobotState.HALTED

    _place("w1", 18.0, 1.0)
    asyncio.run(simulator._tick(0.2))
    assert robot.state == RobotState.ACTIVE and robot.error_code is None


def test_operator_estop_is_not_cleared_by_people_leaving():
    robot = _moving_robot()
    store.estop(robot.id)
    asyncio.run(simulator._tick(0.2))
    assert robot.error_code == "E_STOP"


def test_edge_controller_people_rule():
    ctl = SafetyHaltController(human_stop_radius_m=0.8)
    assert ctl.evaluate_people("r", Pose2D(0, 0), [(0.5, 0.0)]).alert_type == "human_proximity"
    assert ctl.evaluate_people("r", Pose2D(0, 0), [(1.0, 0.0)]).halt is False
    assert ctl.evaluate_people("r", Pose2D(0, 0), [(1.0, 0.0)], margin_m=0.4).halt is True
    assert ctl.evaluate_people("r", Pose2D(0, 0), []).halt is False


def test_edge_agent_stops_once_for_a_person_and_clears():
    adapter = get_adapter("Unitree", "rbt-01")
    cloud = CloudSync(enabled=False, people=[(0.3, 0.0)])
    agent = EdgeAgent([RobotBinding("rbt-01", "Unitree", "G1", adapter=adapter)], cloud=cloud)

    def tick():
        frame = CameraFrame(camera_id="cam-0", ts=0.0, width=640, height=480,
                            ground_truth={"rbt-01": Pose2D(0.0, 0.0)})
        return agent.tick(frame, {"rbt-01": Pose2D(0.0, 0.0)})[0]

    assert tick()["action"] == "halt"
    assert adapter._halted is True
    assert cloud.alerts_sent[-1]["type"] == "human_proximity"
    assert cloud.alerts_sent[-1]["severity"] == "warning"
    assert tick()["action"] == "held_for_person"
    assert len(cloud.alerts_sent) == 1
    cloud.people = [(5.0, 5.0)]
    assert tick()["action"] == "nominal"


# ── G7: work assigned to people ────────────────────────────────────────────────

def _halted(st: Store, rid: str, for_s: float) -> None:
    st.estop(rid)
    st.robots[rid].halted_at = time.time() - for_s


def test_orchestrator_sends_nearest_on_shift_worker_to_a_stuck_robot():
    st = Store()
    orch = Orchestrator(st)
    rid = list(st.robots)[0]
    robot = st.robots[rid]
    rx, ry = robot.pose_external.x, robot.pose_external.y
    _place("near-break", rx + 1.0, ry, status=WorkerStatus.BREAK)
    _place("near", rx + 3.0, ry, display_name="Sam", role="picker")
    _place("far", rx + 10.0, ry)

    _halted(st, rid, for_s=5)
    orch.evaluate()
    assert not [a for a in st.actions.ranked() if a.kind == RecommendationKind.ASSIST]

    _halted(st, rid, for_s=120)
    orch.evaluate()
    [rec] = [a for a in st.actions.ranked() if a.kind == RecommendationKind.ASSIST]
    assert rec.assignee_type == AssigneeType.PERSON and rec.assignee_id == "near"
    assert "Sam (picker)" in rec.rationale
    assert rec.scope is None


def test_no_assist_for_a_person_stop():
    st = Store()
    orch = Orchestrator(st)
    rid = list(st.robots)[0]
    _halted(st, rid, for_s=120)
    st.robots[rid].error_code = simulator.HUMAN_STOP
    _place("w1", st.robots[rid].pose_external.x + 1.0, st.robots[rid].pose_external.y)
    orch.evaluate()
    assert not [a for a in st.actions.ranked() if a.kind == RecommendationKind.ASSIST]


def test_assist_proposal_api_picks_a_worker_and_accepts():
    rid = list(store.robots)[0]
    assert client.post("/api/dashboard/actions", json={
        "kind": "assist", "robot_id": rid, "rationale": "Check the gripper"}).status_code == 409

    robot = store.robots[rid]
    _place("w-near", robot.pose_external.x + 1.0, robot.pose_external.y)
    _place("w-break", robot.pose_external.x + 0.5, robot.pose_external.y, status=WorkerStatus.BREAK)
    r = client.post("/api/dashboard/actions", json={
        "kind": "assist", "robot_id": rid, "rationale": "Check the gripper"})
    assert r.status_code == 201
    rec = r.json()
    assert rec["assignee_type"] == "person" and rec["assignee_id"] == "w-near"

    assert client.post("/api/dashboard/actions", json={
        "kind": "assist", "robot_id": rid, "rationale": "x", "assignee_id": "w-break"}).status_code == 409
    assert client.post("/api/dashboard/actions", json={
        "kind": "assist", "robot_id": rid, "rationale": "x", "assignee_id": "nobody"}).status_code == 404

    accepted = client.post(f"/api/dashboard/actions/{rec['id']}/accept").json()
    assert accepted["status"] == RecommendationStatus.ACCEPTED.value
