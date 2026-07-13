"""Operator drive controls: speed override + manual heading jog."""
import math

from fastapi.testclient import TestClient

from orbital_cloud.main import app
from orbital_cloud.store import store

client = TestClient(app)


def _any_robot() -> str:
    return next(iter(store.robots))


def test_set_speed_clamps_and_applies():
    rid = _any_robot()
    r = client.post(f"/api/dashboard/robot/{rid}/speed", json={"speed_mps": 99})
    assert r.status_code == 200
    from orbital_cloud.config import settings
    assert r.json()["speed_mps"] == round(settings.max_speed_mps, 2)
    assert store.robots[rid].speed_mps == settings.max_speed_mps


def test_manual_drive_sets_heading_and_clears_waypoints():
    rid = _any_robot()
    store.set_waypoints(rid, [(5.0, 5.0)])
    assert store.robots[rid].nav_queue
    r = client.post(f"/api/dashboard/robot/{rid}/drive", json={"heading_deg": 90, "speed_mps": 1.0})
    assert r.status_code == 200
    robot = store.robots[rid]
    assert robot.manual_heading is not None
    assert abs(robot.manual_heading - math.radians(90)) < 1e-6
    assert not robot.nav_queue                 # waypoints cleared by manual jog
    assert robot.control_mode == "manual"
    # Stop returns it out of manual mode.
    r = client.post(f"/api/dashboard/robot/{rid}/drive/stop")
    assert r.status_code == 200
    assert store.robots[rid].manual_heading is None


def test_manual_drive_refused_when_halted():
    rid = _any_robot()
    store.estop(rid)
    r = client.post(f"/api/dashboard/robot/{rid}/drive", json={"heading_deg": 0})
    assert r.status_code == 409
    store.resume(rid)


def test_summary_exposes_speed_and_mode():
    rid = _any_robot()
    store.set_speed(rid, 0.8)
    summary = store.robots[rid].summary()
    assert summary.speed_mps == 0.8
    assert summary.control_mode in ("patrol", "idle", "visual_nav", "manual", "charging", "halted")
