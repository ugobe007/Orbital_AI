"""Module 6 — Cloud Orchestration Layer (edge <-> cloud contract).

These are the endpoints a real ARIA Edge Node calls: it GETs missions/trajectory/map
and POSTs drift telemetry + safety-halt alerts. The built-in simulator uses the same
store ingest path, so swapping it for real edges needs no API changes.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Path

from ..config import WAREHOUSE
from ..events import hub
from ..models import AlertIn, CalibrationIn, ObjectsIn, RunIn, TelemetryIn, Worker, WorkerPoseIn
from ..objects import ObjectStoreFull, object_store, objects_json, objects_message
from ..occupancy import synthetic_occupancy_grid
from ..rig_calibration import calibration_store
from ..runs import run_store, runs_message
from ..store import store
from ..workers import worker_store
from ..zones import zone_store

_WORKER_ID = Path(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")

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
    grid["objects"] = objects_json(facility_id)
    return grid


@router.post("/objects/{facility_id}", status_code=202)
async def post_objects(body: ObjectsIn,
                       facility_id: str = Path(pattern=r"^[A-Za-z0-9_.-]{1,64}$")) -> dict:
    """Tracked objects from the camera rig or a partner perception stack (label, footprint,
    yaw, velocity). Re-report an object to keep it alive; it expires after its ``ttl_s``."""
    try:
        accepted = object_store.ingest(facility_id, body)
    except ObjectStoreFull as exc:
        raise HTTPException(503, str(exc)) from exc
    await hub.broadcast(objects_message(facility_id))
    return {"ok": True, "accepted": accepted, "live": len(object_store.list(facility_id))}


@router.get("/objects/{facility_id}")
async def get_objects(facility_id: str) -> dict:
    return {"facility_id": facility_id, "objects": objects_json(facility_id)}


@router.post("/telemetry", status_code=202)
async def post_telemetry(t: TelemetryIn) -> dict:
    store.ingest_telemetry(t)
    return {"ok": True}


@router.post("/runs/{facility_id}", status_code=201)
async def post_run(body: RunIn, facility_id: str = Path(pattern=r"^[A-Za-z0-9_.-]{1,64}$")) -> dict:
    """A recorded task run from the edge (camera ground truth + robot pose over time). It is
    scored against the golden run for the same task and vendor; the first clean run of a task
    becomes its golden run until an operator picks another."""
    run = run_store.add(body, facility_id, auto_golden=True)
    await hub.broadcast(runs_message())
    return run_store.summary(run).model_dump(mode="json")


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


@router.get("/workers", response_model=list[Worker])
async def list_workers() -> list[Worker]:
    """Live people on the floor, for edge nodes enforcing human-proximity stops."""
    return worker_store.list()


@router.post("/workers/{worker_id}/pose", response_model=Worker)
async def post_worker_pose(body: WorkerPoseIn, worker_id: str = _WORKER_ID) -> Worker:
    """A person's position: phone check-in, badge reader, or Digital Net people detection.
    Re-post at least every ``ORBITAL_WORKER_STALE_S`` seconds or the worker drops off."""
    w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
    if not (0.0 <= body.x <= w and 0.0 <= body.y <= h):
        raise HTTPException(status_code=422, detail=f"position must lie inside the {w:g} x {h:g} m floor")
    worker = worker_store.upsert(worker_id, body)
    await hub.broadcast({"type": "workers", "workers": [x.model_dump(mode="json") for x in worker_store.list()]})
    return worker


@router.delete("/workers/{worker_id}")
async def delete_worker(worker_id: str = _WORKER_ID) -> dict:
    """Check out: the person leaves the floor and stops affecting robots immediately."""
    if not worker_store.remove(worker_id):
        raise HTTPException(status_code=404, detail="worker not found")
    await hub.broadcast({"type": "workers", "workers": [x.model_dump(mode="json") for x in worker_store.list()]})
    return {"ok": True, "worker_id": worker_id}
