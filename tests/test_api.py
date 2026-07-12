"""API contract tests (Module 6 edge + Module 7 dashboard + control)."""
from fastapi.testclient import TestClient

from orbital_cloud.main import app

client = TestClient(app)


def _a_robot_id() -> str:
    return client.get("/api/dashboard/fleet").json()["robots"][0]["id"]


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["robots"] >= 1


def test_fleet_shape():
    body = client.get("/api/dashboard/fleet").json()
    assert "Humanoids" in body["industries"]
    assert body["robots"] and {"id", "vendor", "drift_delta_m", "state"} <= set(body["robots"][0])


def test_robot_detail_and_404():
    rid = _a_robot_id()
    detail = client.get(f"/api/dashboard/robot/{rid}").json()
    assert detail["id"] == rid and "oem_brief" in detail
    assert client.get("/api/dashboard/robot/nope").status_code == 404


def test_estop_and_resume_control():
    rid = _a_robot_id()
    assert client.post(f"/api/dashboard/robot/{rid}/estop").status_code == 200
    assert client.get(f"/api/dashboard/robot/{rid}").json()["state"] == "halted"
    assert client.post(f"/api/dashboard/robot/{rid}/resume").status_code == 200
    assert client.get(f"/api/dashboard/robot/{rid}").json()["state"] != "halted"


def test_task_dispatch_sets_robot_active():
    rid = _a_robot_id()
    r = client.post("/api/dashboard/tasks", json={"robot_id": rid, "description": "Restock aisle 4", "waypoints": []})
    assert r.status_code == 201 and r.json()["status"] == "active"
    detail = client.get(f"/api/dashboard/robot/{rid}").json()
    assert detail["current_task"] == "Restock aisle 4"


def test_edge_telemetry_feeds_benchmark():
    rid = _a_robot_id()
    vendor = client.get(f"/api/dashboard/robot/{rid}").json()["vendor"]
    before = client.get(f"/api/dashboard/benchmark/{vendor}").json()["samples"]
    r = client.post("/api/v1/telemetry", json={
        "robot_id": rid, "vendor": vendor, "model": "x", "facility_id": "f", "delta_meters": 0.42,
    })
    assert r.status_code == 202
    after = client.get(f"/api/dashboard/benchmark/{vendor}").json()["samples"]
    assert after == before + 1


def test_edge_alert_surfaces_in_dashboard():
    rid = _a_robot_id()
    r = client.post("/api/v1/alerts", json={
        "robot_id": rid, "type": "drift_exceeded", "severity": "critical", "message": "test halt",
    })
    assert r.status_code == 201
    alerts = client.get("/api/dashboard/alerts").json()
    assert any(a["message"] == "test halt" for a in alerts)


def test_edge_trajectory_missions_map():
    rid = _a_robot_id()
    assert "waypoints" in client.get(f"/api/v1/trajectory/{rid}").json()
    assert "missions" in client.get("/api/v1/missions/facility-sf-001").json()
    assert client.get("/api/v1/map/facility-sf-001").json()["width"] == 400
