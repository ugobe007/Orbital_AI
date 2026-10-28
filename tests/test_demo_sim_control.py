"""Public demo: anonymous visitors can stop/start simulated robots, never real ones."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from orbital_cloud import simulator
from orbital_cloud.main import app
from orbital_cloud.models import RobotState
from orbital_cloud.store import store

RID = "rbt-01"


@pytest.fixture
def anon(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "operator:ops-key-456")
    monkeypatch.delenv("ORBITAL_DEMO_SIM_CONTROL", raising=False)
    r = store.robots[RID]
    saved = (r.simulated, r.state, r.error_code, r.halted_at)
    r.simulated = True
    yield TestClient(app)
    r.simulated, r.state, r.error_code, r.halted_at = saved


def test_seed_robots_are_flagged_simulated(anon):
    fleet = anon.get("/api/dashboard/fleet").json()["robots"]
    assert next(b for b in fleet if b["id"] == RID)["simulated"] is True


def test_anonymous_can_stop_and_start_a_simulated_robot(anon):
    r = anon.post(f"/api/dashboard/robot/{RID}/estop")
    assert r.status_code == 200 and r.json()["auto_resume_s"] == int(simulator.DEMO_STOP_RELEASE_S)
    assert store.robots[RID].error_code == "DEMO_STOP"
    assert anon.post(f"/api/dashboard/robot/{RID}/resume").status_code == 200
    assert store.robots[RID].error_code is None


def test_anonymous_control_is_audited_as_demo_visitor(anon):
    anon.post(f"/api/dashboard/robot/{RID}/estop")
    events = store.control_audit_recent(limit=1)
    assert events[0]["actor"] == "demo-visitor" and events[0]["role"] == "viewer"


def test_anonymous_cannot_touch_a_real_robot(anon):
    store.robots[RID].simulated = False
    assert anon.post(f"/api/dashboard/robot/{RID}/estop").status_code == 403
    assert anon.post(f"/api/dashboard/robot/{RID}/resume").status_code == 403


def test_edge_telemetry_marks_robot_real(anon):
    r = anon.post("/api/v1/telemetry", json={"robot_id": RID, "vendor": "Unitree", "model": "G1",
                                              "facility_id": "lab", "delta_meters": 0.01})
    assert r.status_code == 202
    assert store.robots[RID].simulated is False
    assert anon.post(f"/api/dashboard/robot/{RID}/estop").status_code == 403


def test_demo_allowance_can_be_switched_off(anon, monkeypatch):
    monkeypatch.setenv("ORBITAL_DEMO_SIM_CONTROL", "0")
    assert anon.post(f"/api/dashboard/robot/{RID}/estop").status_code == 403


def test_anonymous_still_cannot_dispatch_or_navigate(anon):
    r = anon.post(f"/api/dashboard/robot/{RID}/navigate", json={"waypoints": [{"x": 5.0, "y": 5.0}]})
    assert r.status_code == 403


def test_operator_stop_still_latches(anon):
    anon.post("/api/auth/login", json={"key": "ops-key-456"})
    anon.post(f"/api/dashboard/robot/{RID}/estop")
    assert store.robots[RID].error_code == "E_STOP"


def test_demo_stop_holds_then_self_releases(anon):
    robot = store.robots[RID]
    store.estop(RID, demo=True)
    asyncio.run(simulator._tick(0.1))
    assert robot.state == RobotState.HALTED and robot.error_code == "DEMO_STOP"
    robot.halted_at = time.time() - simulator.DEMO_STOP_RELEASE_S - 1
    asyncio.run(simulator._tick(0.1))
    assert robot.state != RobotState.HALTED and robot.error_code is None
