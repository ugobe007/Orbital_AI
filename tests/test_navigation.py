"""Warehouse map + visual-control waypoint navigation."""
from fastapi.testclient import TestClient

from orbital_cloud import simulator
from orbital_cloud.main import app
from orbital_cloud.store import Store

client = TestClient(app)


def test_map_returns_warehouse_geometry():
    m = client.get("/api/dashboard/map").json()
    assert m["width_m"] > 0 and m["height_m"] > 0
    assert len(m["racks"]) > 0
    assert "charge_stations" in m


def test_navigate_sets_waypoints_and_visual_nav_flag():
    # rbt-02 (AgiBot) is unmanaged in the test env → permissive control.
    r = client.post("/api/dashboard/robot/rbt-02/navigate", json={"waypoints": [{"x": 12.0, "y": 8.0}]})
    assert r.status_code == 200
    fleet = client.get("/api/dashboard/fleet").json()["robots"]
    bot = next(b for b in fleet if b["id"] == "rbt-02")
    assert bot["visual_nav"] is True
    assert bot["nav_goal"]["x"] == 12.0
    # Clear returns it to patrol.
    assert client.post("/api/dashboard/robot/rbt-02/navigate/clear").status_code == 200
    fleet = client.get("/api/dashboard/fleet").json()["robots"]
    assert next(b for b in fleet if b["id"] == "rbt-02")["visual_nav"] is False


def test_navigate_requires_velocity_scope():
    # Register an OEM for MagicLab with no grants → control.velocity denied → 403.
    client.post("/api/oem/register", json={
        "company_name": "Nav Test Co", "vendor": "MagicLab",
        "contact_email": "n@x.com", "transport": "ros2",
    })
    r = client.post("/api/dashboard/robot/rbt-07/navigate", json={"waypoints": [{"x": 5.0, "y": 5.0}]})
    assert r.status_code == 403 and "control.velocity" in r.json()["detail"]


def test_navigate_empty_waypoints_rejected():
    assert client.post("/api/dashboard/robot/rbt-02/navigate", json={"waypoints": []}).status_code == 400


def test_visual_control_reaches_waypoint_despite_drift():
    # The robot's SLAM drifts, but visual control drives the *external* pose to the goal.
    store = Store()
    robot = store.robots["rbt-01"]
    robot.pose_external = robot.pose_external.model_copy(update={"x": 0.0, "y": 0.0})
    robot.drift_bias = (0.4, -0.3)   # large odometric drift the camera control ignores
    store.set_waypoints("rbt-01", [(6.0, 4.0)], persist=False)

    for _ in range(400):
        if not robot.nav_queue:
            break
        simulator._advance_nav(robot, 0.2)
    assert not robot.nav_queue                       # arrived
    assert abs(robot.pose_external.x - 6.0) < 0.2
    assert abs(robot.pose_external.y - 4.0) < 0.2


def test_waypoints_clamped_to_bounds():
    store = Store()
    store.set_waypoints("rbt-01", [(999.0, -50.0)], persist=False)
    gx, gy = store.robots["rbt-01"].nav_queue[0]
    assert 0.0 <= gx <= 24.0 and 0.0 <= gy <= 16.0
