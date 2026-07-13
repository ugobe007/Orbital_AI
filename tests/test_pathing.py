"""Obstacle-aware pathing: routes bend around racks and the sim never drives through one."""
import asyncio
import math

from orbital_cloud import pathing, simulator
from orbital_cloud.config import WAREHOUSE
from orbital_cloud.store import store


def _in_any_rack(x: float, y: float) -> bool:
    """Point inside a *raw* rack rectangle (no inflation) — a true collision."""
    for r in WAREHOUSE["racks"]:
        if r["x"] <= x <= r["x"] + r["w"] and r["y"] <= y <= r["y"] + r["h"]:
            return True
    return False


def test_direct_path_when_clear_is_a_single_segment():
    route = pathing.plan((1.0, 15.0), (23.0, 15.0))  # along the top, no racks
    assert route == [(23.0, 15.0)]


def test_route_bends_around_a_blocking_rack():
    start, goal = (2.0, 4.5), (9.0, 4.5)  # straight line crosses racks A and B
    assert pathing._segment_clear(start, goal) is False
    route = pathing.plan(start, goal)
    assert route[-1] == goal
    # Every leg of the followed path clears the inflated racks, and no waypoint is inside one.
    pts = [start, *route]
    assert all(pathing._segment_clear(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    assert not any(pathing._point_blocked(x, y) for x, y in route[:-1])


def test_simulator_never_drives_a_robot_through_a_rack():
    simulator.prime()
    for _ in range(400):                      # ~80s of sim at 5Hz
        asyncio.run(simulator._tick(0.2))
        for r in store.robots.values():
            assert not _in_any_rack(r.pose_external.x, r.pose_external.y), (
                f"{r.id} drove into a rack at ({r.pose_external.x:.2f}, {r.pose_external.y:.2f})"
            )


def test_operator_waypoint_routes_around_obstacle():
    simulator.prime()
    rid = next(iter(store.robots))
    robot = store.robots[rid]
    robot.pose_external = robot.pose_external.model_copy(update={"x": 2.0, "y": 4.5})
    robot.pose_internal = robot.pose_external.model_copy()
    store.set_waypoints(rid, [(9.0, 4.5)], persist=False)
    robot.state = store.robots[rid].state
    for _ in range(600):
        asyncio.run(simulator._tick(0.2))
        assert not _in_any_rack(robot.pose_external.x, robot.pose_external.y)
        if not robot.nav_queue:
            break
    assert math.hypot(robot.pose_external.x - 9.0, robot.pose_external.y - 4.5) < 0.5
