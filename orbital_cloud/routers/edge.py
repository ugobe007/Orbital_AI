"""Module 6 — Cloud Orchestration Layer (edge <-> cloud contract).

These are the endpoints a real ARIA Edge Node calls: it GETs missions/trajectory/map
and POSTs drift telemetry + safety-halt alerts. The built-in simulator uses the same
store ingest path, so swapping it for real edges needs no API changes.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Path

from ..events import hub
from ..models import AlertIn, CalibrationIn, TelemetryIn
from ..occupancy import synthetic_occupancy_grid
from ..rig_calibration import calibration_store
from ..store import store
from ..zones import zone_store

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
    """Synthetic Global Spatial Map (nav_msgs/OccupancyGrid shape) for edge consumers. Zones
    ride alongside the grid as polygons so the edge can enforce them without re-rasterizing."""
    grid = synthetic_occupancy_grid(facility_id)
    grid["zones"] = [z.model_dump(mode="json") for z in zone_store.list()]
    return grid


@router.post("/telemetry", status_code=202)
async def post_telemetry(t: TelemetryIn) -> dict:
    store.ingest_telemetry(t)
    return {"ok": True}


@router.post("/calibration/{facility_id}", status_code=201)
async def post_calibration(body: CalibrationIn,
                           facility_id: str = Path(pattern=r"^[A-Za-z0-9_.-]{1,64}$")) -> dict:
    """Solve a camera-rig marker map from raw captures and keep the report for the dashboard."""
    entry = await asyncio.to_thread(calibration_store.solve_and_store, facility_id, body)
    await hub.broadcast({"type": "calibration", "calibration": entry})
    return entry


@router.get("/calibration/{facility_id}")
async def get_calibration(facility_id: str) -> dict:
    entry = calibration_store.get(facility_id)
    if entry is None:
        raise HTTPException(404, f"no calibration uploaded for {facility_id}")
    return entry


@router.post("/alerts", status_code=201)
async def post_alert(a: AlertIn) -> dict:
    saved = store.add_alert(a)
    await hub.broadcast({"type": "alert", "alert": saved.model_dump(mode="json")})
    return {"ok": True, "id": saved.id}
