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
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, contact, inbox, orchestrator, simulator
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

@app.middleware("http")
async def _canonical_and_cache(request, call_next):
    """Canonical host + cache control.

    - 301 the www subdomain to the apex so search engines see a single canonical URL.
    - Always revalidate the SPA entry so a redeploy's cache-busted asset URLs are picked
      up immediately (StaticFiles has no cache-control by default, so browsers cache
      heuristically)."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host == "www.orbital-ai.io":
        target = f"https://orbital-ai.io{request.url.path}"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(url=target, status_code=301)
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(dashboard.router)
app.include_router(edge.router)
app.include_router(oem.router)
app.include_router(contact.router)
app.include_router(inbox.router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "facility": settings.facility_id,
        "robots": len(store.robots),
        "simulator": settings.simulator_enabled,
    }


# Static asset roots. The dashboard SPA and its assets (styles.css, app.js,
# orbital-logo.png) are referenced by absolute "/…" URLs, so those assets stay served
# from root. The public marketing site lives at "/" and the dashboard moves to "/app".
_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
_SITE_DIR = Path(__file__).resolve().parent.parent / "site"
_SITE_INDEX = _SITE_DIR / "index.html"
_DASHBOARD_INDEX = _DASHBOARD_DIR / "index.html"


@app.get("/", include_in_schema=False)
async def marketing_home():
    """Public marketing landing page (falls back to the dashboard if the site is absent)."""
    if _SITE_INDEX.is_file():
        return FileResponse(str(_SITE_INDEX))
    return FileResponse(str(_DASHBOARD_INDEX))


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
async def dashboard_app():
    """The live fleet-control dashboard (its absolute asset URLs resolve at root)."""
    return FileResponse(str(_DASHBOARD_INDEX))


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


# Serve dashboard assets (styles.css, app.js, orbital-logo.png) from root, registered
# last so it doesn't shadow the API or the explicit "/" and "/app" routes above. The
# marketing site reuses /orbital-logo.png from here.
if _DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard")
