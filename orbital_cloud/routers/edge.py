"""Module 6 — Cloud Orchestration Layer (edge <-> cloud contract).

These are the endpoints a real ARIA Edge Node calls: it GETs missions/trajectory/map
and POSTs drift telemetry + safety-halt alerts. The built-in simulator uses the same
store ingest path, so swapping it for real edges needs no API changes.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..events import hub
from ..models import AlertIn, TelemetryIn
from ..store import store

router = APIRouter(prefix="/api/v1", tags=["edge"])


@router.get("/missions/{facility_id}")
async def get_missions(facility_id: str) -> dict:
    return {
        "facility_id": facility_id,
        "missions": [t.model_dump(mode="json") for t in store.active_tasks()],
    }


@router.get("/trajectory/{robot_id}")
async def get_trajectory(robot_id: str) -> dict:
    robot = store.robots.get(robot_id)
    waypoints = [{"x": x, "y": y} for x, y in (robot.trajectory if robot else [])]
    return {"robot_id": robot_id, "waypoints": waypoints}


@router.get("/map/{facility_id}")
async def get_map(facility_id: str) -> dict:
    """Stub Global Spatial Map (nav_msgs/OccupancyGrid shape) for the demo."""
    return {
        "facility_id": facility_id,
        "resolution": 0.05,
        "width": 400,
        "height": 300,
        "origin": {"x": -2.0, "y": -2.0, "theta": 0.0},
        "note": "occupancy data omitted in v0 demo",
    }


@router.post("/telemetry", status_code=202)
async def post_telemetry(t: TelemetryIn) -> dict:
    store.ingest_telemetry(t)
    return {"ok": True}


@router.post("/alerts", status_code=201)
async def post_alert(a: AlertIn) -> dict:
    saved = store.add_alert(a)
    await hub.broadcast({"type": "alert", "alert": saved.model_dump(mode="json")})
    return {"ok": True, "id": saved.id}
