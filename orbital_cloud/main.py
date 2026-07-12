"""Orbital AI Cloud — FastAPI entrypoint.

Run locally:
    uvicorn orbital_cloud.main:app --reload --port 8090

Then open http://localhost:8090 for the Fleet Management Dashboard.
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__, orchestrator, simulator
from .config import settings
from .events import hub
from .models import APIScope, ControlTransport
from .oem_store import oem_store
from .routers import dashboard, edge, oem
from .store import store

# Demo OEM partners with deliberately different grant levels so the governance table and
# scope-aware controls are visibly meaningful: full control, monitor+command, monitor-only.
_DEMO_OEMS = [
    ("Unitree Robotics (demo)", "Unitree", ControlTransport.ROS2,
     [APIScope.TELEMETRY, APIScope.STATE, APIScope.VELOCITY, APIScope.ESTOP, APIScope.MISSION, APIScope.MAP]),
    ("Boston Dynamics (demo)", "Boston Dynamics", ControlTransport.GRPC,
     [APIScope.TELEMETRY, APIScope.STATE, APIScope.ESTOP, APIScope.MISSION]),
    ("Agility Robotics (demo)", "Agility Robotics", ControlTransport.CLOUD_REST,
     [APIScope.TELEMETRY, APIScope.STATE]),
]

_sim_task: asyncio.Task | None = None
_orch_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _sim_task, _orch_task
    if settings.seed_oems:
        oem_store.seed_demo(_DEMO_OEMS)
    if settings.simulator_enabled:
        _sim_task = asyncio.create_task(simulator.run())
    if settings.orchestrator_enabled:
        _orch_task = asyncio.create_task(orchestrator.run())
    try:
        yield
    finally:
        for task in (_sim_task, _orch_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="Orbital AI Cloud", version=__version__, lifespan=lifespan)

# StageGate (TS) and ReadyForRobots (Py) frontends embed this dashboard/API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(edge.router)
app.include_router(oem.router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "facility": settings.facility_id,
        "robots": len(store.robots),
        "simulator": settings.simulator_enabled,
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    # Prime the newly-connected dashboard with the current fleet immediately.
    await websocket.send_json({"type": "fleet", "robots": [r.model_dump(mode="json") for r in store.fleet()]})
    try:
        while True:
            # We don't expect inbound messages; this keeps the socket open and
            # detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)


# Serve the dashboard SPA at "/" (registered last so it doesn't shadow the API).
_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if _DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard")
