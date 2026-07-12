"""Module 7 — Fleet Management Dashboard API (+ monitor/control actions)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..benchmark import render_benchmark_report
from ..config import INDUSTRIES, settings
from ..events import hub
from ..models import Alert, RobotDetail, RobotSummary, Task, TaskIn, VendorBenchmark
from ..store import store

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _broadcast_fleet() -> None:
    await hub.broadcast({"type": "fleet", "robots": [r.model_dump(mode="json") for r in store.fleet()]})


@router.get("/fleet")
async def get_fleet() -> dict:
    return {
        "facility": {"id": settings.facility_id, "name": settings.facility_name},
        "industries": INDUSTRIES,
        "vendors": store.vendors(),
        "robots": [r.model_dump(mode="json") for r in store.fleet()],
    }


@router.get("/robot/{robot_id}", response_model=RobotDetail)
async def get_robot(robot_id: str) -> RobotDetail:
    detail = store.robot_detail(robot_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="robot not found")
    return detail


@router.get("/tasks", response_model=list[Task])
async def list_tasks() -> list[Task]:
    return store.active_tasks()


@router.post("/tasks", response_model=Task, status_code=201)
async def create_task(task: TaskIn) -> Task:
    if task.robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    created = store.create_task(task)
    await _broadcast_fleet()
    return created


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(limit: int = 50) -> list[Alert]:
    return store.recent_alerts(limit=limit)


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str) -> dict:
    if not store.acknowledge_alert(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return {"ok": True}


@router.get("/benchmark")
async def benchmark_report() -> dict:
    return {
        "report": render_benchmark_report(store),
        "vendors": [store.benchmark(v).model_dump(mode="json") for v in store.vendors()],
    }


@router.get("/benchmark/{vendor}", response_model=VendorBenchmark)
async def benchmark_for_vendor(vendor: str) -> VendorBenchmark:
    return store.benchmark(vendor)


@router.post("/robot/{robot_id}/estop")
async def estop(robot_id: str) -> dict:
    if not store.estop(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "state": "halted"}


@router.post("/robot/{robot_id}/resume")
async def resume(robot_id: str) -> dict:
    if not store.resume(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}
