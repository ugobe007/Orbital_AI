"""Sprint G — keep-out and slow zones on the Global Spatial Map."""
import asyncio
import math

import pytest
from fastapi.testclient import TestClient

from orbital_cloud import pathing, simulator
from orbital_cloud.main import app
from orbital_cloud.models import ZoneIn, ZoneType
from orbital_cloud.store import store
from orbital_cloud.zones import ZoneStore, point_in_polygon, zone_store

client = TestClient(app)

# A box in the open corridor between rack C (x ≤ 11.2) and racks G/H (x ≥ 16).
BOX = [[12.5, 6.0], [14.5, 6.0], [14.5, 8.0], [12.5, 8.0]]


@pytest.fixture(autouse=True)
def _no_zones():
    zone_store.clear()
    yield
    zone_store.clear()


def _route_enters(start, route, polygon) -> bool:
    pts = [start, *route]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.05))
        for s in range(steps + 1):
            t = s / steps
            if point_in_polygon(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, polygon):
                return True
    return False


def test_point_in_polygon():
    assert point_in_polygon(13.5, 7.0, BOX)
    assert not point_in_polygon(11.0, 7.0, BOX)
    assert not point_in_polygon(13.5, 9.0, BOX)


def test_plan_routes_around_a_keepout_zone():
    start, goal = (13.5, 1.0), (13.5, 13.0)
    assert pathing.plan(start, goal) == [goal]
    zone_store.create(ZoneIn(name="Spill", type=ZoneType.KEEPOUT, polygon=BOX))
    route = pathing.plan(start, goal)
    assert route[-1] == goal
    assert len(route) > 1
    assert not _route_enters(start, route, BOX)


def test_keepout_scoped_to_other_vendor_does_not_apply():
    start, goal = (13.5, 1.0), (13.5, 13.0)
    zone_store.create(ZoneIn(name="Humanoid only", type=ZoneType.KEEPOUT, polygon=BOX, vendors=["Unitree"]))
    assert pathing.plan(start, goal, vendor="AgiBot") == [goal]
    assert _route_enters(start, pathing.plan(start, goal, vendor="AgiBot"), BOX)
    assert not _route_enters(start, pathing.plan(start, goal, vendor="Unitree"), BOX)


def test_goal_inside_keepout_stops_outside_it():
    zone_store.create(ZoneIn(name="Spill", type=ZoneType.KEEPOUT, polygon=BOX))
    start = (13.5, 1.0)
    route = pathing.plan(start, (13.5, 7.0))
    assert not _route_enters(start, route, BOX)


def test_slow_zone_caps_speed_inside_only():
    zone_store.create(ZoneIn(name="Pick face", type=ZoneType.SLOW, polygon=BOX, speed_cap_mps=0.2))
    robot = next(iter(store.robots.values()))
    robot.speed_mps = 1.0
    robot.pose_external = robot.pose_external.model_copy(update={"x": 13.5, "y": 7.0})
    assert simulator._zone_speed(robot) == 0.2
    robot.pose_external = robot.pose_external.model_copy(update={"x": 13.5, "y": 12.0})
    assert simulator._zone_speed(robot) == 1.0


def test_slow_zone_defaults_a_cap():
    zone = zone_store.create(ZoneIn(name="Slow", type=ZoneType.SLOW, polygon=BOX))
    assert zone.speed_cap_mps == 0.3


def test_simulated_robot_never_enters_keepout():
    zone_store.create(ZoneIn(name="Spill", type=ZoneType.KEEPOUT, polygon=BOX))
    simulator.prime()
    rid = "rbt-02"
    robot = store.robots[rid]
    robot.pose_external = robot.pose_external.model_copy(update={"x": 13.5, "y": 1.0})
    store.set_waypoints(rid, [(13.5, 13.0)], persist=False)
    for _ in range(600):
        asyncio.run(simulator._tick(0.2))
        assert not point_in_polygon(robot.pose_external.x, robot.pose_external.y, BOX)
        if not robot.nav_queue:
            break
    assert math.hypot(robot.pose_external.x - 13.5, robot.pose_external.y - 13.0) < 0.5


def test_zone_api_round_trip_and_map():
    r = client.post("/api/dashboard/zones", json={"name": "Spill", "type": "keepout", "polygon": BOX})
    assert r.status_code == 201
    zone = r.json()
    assert zone["id"].startswith("zone-")
    assert [z["id"] for z in client.get("/api/dashboard/zones").json()] == [zone["id"]]
    assert client.get("/api/dashboard/map").json()["zones"][0]["id"] == zone["id"]
    assert client.get("/api/v1/map/sf-01").json()["zones"][0]["id"] == zone["id"]
    assert client.delete(f"/api/dashboard/zones/{zone['id']}").status_code == 200
    assert client.get("/api/dashboard/zones").json() == []
    assert client.delete(f"/api/dashboard/zones/{zone['id']}").status_code == 404


@pytest.mark.parametrize("body", [
    {"name": "Two points", "type": "keepout", "polygon": [[1, 1], [2, 2]]},
    {"name": "Off the floor", "type": "keepout", "polygon": [[1, 1], [30, 1], [1, 5]]},
    {"name": "Bad point", "type": "keepout", "polygon": [[1, 1, 1], [3, 1], [1, 5]]},
])
def test_zone_api_rejects_bad_polygons(body):
    assert client.post("/api/dashboard/zones", json=body).status_code == 422


def test_navigate_refuses_waypoint_inside_keepout():
    client.post("/api/dashboard/zones", json={"name": "Spill", "type": "keepout", "polygon": BOX})
    r = client.post("/api/dashboard/robot/rbt-02/navigate", json={"waypoints": [{"x": 13.5, "y": 7.0}]})
    assert r.status_code == 409 and "Spill" in r.json()["detail"]


def test_viewer_and_agent_cannot_edit_zones(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "agent:bot-secret")
    body = {"name": "Spill", "type": "keepout", "polygon": BOX}
    assert client.post("/api/dashboard/zones", json=body).status_code == 403
    r = client.post("/api/dashboard/zones", json=body, headers={"Authorization": "Bearer bot-secret"})
    assert r.status_code == 403


def test_zones_survive_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBITAL_DB_PATH", str(tmp_path / "orbital.db"))
    first = ZoneStore()
    zone = first.create(ZoneIn(name="Dock lane", type=ZoneType.SLOW, polygon=BOX, speed_cap_mps=0.4))
    reloaded = ZoneStore().get(zone.id)
    assert reloaded is not None and reloaded.speed_cap_mps == 0.4
