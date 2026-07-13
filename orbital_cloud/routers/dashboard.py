"""Module 7 — Fleet Management Dashboard API (+ monitor/control actions)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..benchmark import render_benchmark_report
from ..config import INDUSTRIES, WAREHOUSE, settings
from ..events import hub
from ..models import (
    Alert,
    APIScope,
    ControlTransport,
    DriveIn,
    IntegrationProfile,
    NavigateIn,
    OEMOnboardIn,
    OEMPolicies,
    OEMStatus,
    OrchestratorStatus,
    RobotDetail,
    RobotSummary,
    ScopeGrantIn,
    SpeedIn,
    Task,
    TaskIn,
    VendorBenchmark,
)
from ..oem_store import ceiling_scopes_for_vendor, oem_store
from fleet_adapters import known_vendors
from ..orchestrator import orchestrator
from .. import scope_guard
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


@router.get("/robot/{robot_id}/sensors")
async def get_robot_sensors(robot_id: str) -> dict:
    if robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    snapshot = store.latest_sensors(robot_id)
    return {"robot_id": robot_id, "sensors": snapshot.model_dump(mode="json") if snapshot else None}


@router.get("/map")
async def get_map() -> dict:
    """The warehouse floor plan operators drop waypoints on (Global Spatial Map)."""
    return WAREHOUSE


@router.post("/robot/{robot_id}/navigate")
async def navigate(robot_id: str, body: NavigateIn) -> dict:
    """Set operator waypoints — Orbital drives the robot there via visual control, bypassing
    its onboard SLAM. Gated on control.velocity (the visual servo issues velocity commands)."""
    if not body.waypoints:
        raise HTTPException(status_code=400, detail="at least one waypoint required")
    _enforce(robot_id, APIScope.VELOCITY)
    ok = store.set_waypoints(robot_id, [(p.x, p.y) for p in body.waypoints])
    if not ok:
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "waypoints": [p.model_dump() for p in body.waypoints]}


@router.post("/robot/{robot_id}/navigate/clear")
async def navigate_clear(robot_id: str) -> dict:
    if not store.clear_waypoints(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}


@router.post("/robot/{robot_id}/speed")
async def set_speed(robot_id: str, body: SpeedIn) -> dict:
    """Operator speed override (m/s). Scales patrol, visual-nav, and manual motion.
    Gated on control.velocity."""
    _enforce(robot_id, APIScope.VELOCITY)
    applied = store.set_speed(robot_id, body.speed_mps)
    if applied is None:
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "speed_mps": applied}


@router.post("/robot/{robot_id}/drive")
async def drive(robot_id: str, body: DriveIn) -> dict:
    """Manual jog along a heading (deg, 0 = east, CCW). Overrides patrol / clears waypoints.
    Gated on control.velocity."""
    import math

    _enforce(robot_id, APIScope.VELOCITY)
    heading_rad = math.radians(body.heading_deg)
    if not store.set_manual_drive(robot_id, heading_rad, body.speed_mps):
        raise HTTPException(status_code=409, detail="robot not found or halted")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "heading_deg": body.heading_deg}


@router.post("/robot/{robot_id}/drive/stop")
async def drive_stop(robot_id: str) -> dict:
    _enforce(robot_id, APIScope.VELOCITY)
    if not store.stop_manual_drive(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}


@router.get("/tasks", response_model=list[Task])
async def list_tasks() -> list[Task]:
    return store.active_tasks()


def _enforce(robot_id: str, scope: APIScope) -> None:
    """Raise 404/403 unless the robot exists and its OEM has granted `scope`."""
    check = scope_guard.check_robot(robot_id, scope)
    if check.vendor is None:
        raise HTTPException(status_code=404, detail="robot not found")
    if not check.allowed:
        raise HTTPException(status_code=403, detail=check.reason)


@router.post("/tasks", response_model=Task, status_code=201)
async def create_task(task: TaskIn) -> Task:
    if task.robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    _enforce(task.robot_id, APIScope.MISSION)
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


@router.get("/orchestrator", response_model=OrchestratorStatus)
async def orchestrator_status() -> OrchestratorStatus:
    return orchestrator.status()


@router.post("/orchestrator/run", response_model=OrchestratorStatus)
async def orchestrator_run() -> OrchestratorStatus:
    """Trigger one supervisory pass on demand (useful for demos + tests)."""
    status = orchestrator.evaluate()
    await orchestrator.refresh_narrative()
    status = orchestrator.status()
    await _broadcast_fleet()
    return status


@router.post("/robot/{robot_id}/estop")
async def estop(robot_id: str) -> dict:
    _enforce(robot_id, APIScope.ESTOP)
    if not store.estop(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "state": "halted"}


@router.post("/robot/{robot_id}/resume")
async def resume(robot_id: str) -> dict:
    _enforce(robot_id, APIScope.ESTOP)
    if not store.resume(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}


# ── OEM governance (operator surface) ─────────────────────────────────────────
# Operators view every partner and can revoke/suspend defensively. Granting stays
# OEM-initiated in production (POST /api/oem/{id}/scopes with the partner's key); the
# operator grant here is a convenience for the console/demo. Open in v0 — production
# gates this behind operator RBAC.

def _profile_or_404(oem_id: str) -> IntegrationProfile:
    profile = oem_store.profile(oem_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return profile


_SCOPE_META: dict[str, str] = {
    APIScope.TELEMETRY.value: "Stream pose, battery, and health telemetry",
    APIScope.STATE.value: "Read task/state machine and mission status",
    APIScope.VELOCITY.value: "Command velocity (visual-nav waypoints, jog, speed)",
    APIScope.ESTOP.value: "Trigger and clear emergency stop",
    APIScope.TELEOP.value: "Full teleoperation hand-on control",
    APIScope.MISSION.value: "Dispatch and cancel missions",
    APIScope.CAMERA.value: "Read onboard camera frames",
    APIScope.MAP.value: "Read the robot's onboard map / SLAM graph",
}


@router.get("/oem-catalog")
async def oem_catalog() -> dict:
    """Everything the onboarding wizard needs: known vendors and the API scopes each
    protocol can support, the transports, and the default governance policies."""
    vendors = [
        {"vendor": v, "ceiling_scopes": [s.value for s in ceiling_scopes_for_vendor(v)]}
        for v in known_vendors()
    ]
    return {
        "vendors": vendors,
        "transports": [t.value for t in ControlTransport],
        "scopes": [{"value": s.value, "label": _SCOPE_META.get(s.value, s.value)} for s in APIScope],
        "default_policies": OEMPolicies().model_dump(),
    }


@router.post("/oems", response_model=dict, status_code=201)
async def onboard_oem(body: OEMOnboardIn) -> dict:
    """Wizard endpoint — register a new robot-API partner, unlock the requested scopes,
    set governance policies, and return the profile + the one-time API key."""
    partner, credential = oem_store.register(body)
    if body.scopes:
        oem_store.grant_scopes(partner.id, body.scopes)
    if body.policies is not None:
        oem_store.set_policies(partner.id, body.policies)
    profile = _profile_or_404(partner.id)
    return {"profile": profile.model_dump(mode="json"), "credential": credential.model_dump(mode="json")}


@router.post("/oems/{oem_id}/policies", response_model=IntegrationProfile)
async def update_oem_policies(oem_id: str, body: OEMPolicies) -> IntegrationProfile:
    if oem_store.set_policies(oem_id, body) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.get("/oems", response_model=list[IntegrationProfile])
async def list_oems() -> list[IntegrationProfile]:
    return [p for p in (oem_store.profile(o.id) for o in oem_store.list()) if p is not None]


@router.get("/oems/{oem_id}", response_model=IntegrationProfile)
async def get_oem(oem_id: str) -> IntegrationProfile:
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/grant", response_model=IntegrationProfile)
async def operator_grant(oem_id: str, body: ScopeGrantIn) -> IntegrationProfile:
    if oem_store.grant_scopes(oem_id, body.scopes) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/revoke", response_model=IntegrationProfile)
async def operator_revoke(oem_id: str, body: ScopeGrantIn) -> IntegrationProfile:
    if oem_store.revoke_scopes(oem_id, body.scopes) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.delete("/oems/{oem_id}")
async def remove_oem(oem_id: str) -> dict:
    if not oem_store.remove(oem_id):
        raise HTTPException(status_code=404, detail="OEM not found")
    return {"ok": True, "oem_id": oem_id}


@router.post("/oems/{oem_id}/suspend", response_model=IntegrationProfile)
async def suspend_oem(oem_id: str) -> IntegrationProfile:
    if oem_store.set_status(oem_id, OEMStatus.SUSPENDED) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/reactivate", response_model=IntegrationProfile)
async def reactivate_oem(oem_id: str) -> IntegrationProfile:
    partner = oem_store.get(oem_id)
    if partner is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    # Back to ACTIVE if it still holds scopes, otherwise PENDING (awaiting a grant).
    oem_store.set_status(oem_id, OEMStatus.ACTIVE if partner.granted_scopes else OEMStatus.PENDING)
    return _profile_or_404(oem_id)
