"""Module 7 — Fleet Management Dashboard API (+ monitor/control actions)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query

from ..benchmark import render_benchmark_report
from ..config import INDUSTRIES, WAREHOUSE, settings
from ..events import hub
from ..models import (
    ActionProposalIn,
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
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    RobotDetail,
    RobotSummary,
    RunOutcome,
    RunSummary,
    ScopeGrantIn,
    SpeedIn,
    Task,
    TaskIn,
    VendorBenchmark,
    Worker,
    WorkerStatus,
    Zone,
    ZoneIn,
)
from ..oem_store import ceiling_scopes_for_vendor, oem_store
from fleet_adapters import known_vendors
from ..orchestrator import actions_message, orchestrator
from ..rbac import (
    Caller,
    CurrentCaller,
    RequireAdmin,
    DemoResumer,
    DemoStopper,
    RequireController,
    RequireOperator,
    RequireProposer,
    RequireViewer,
    Role,
    agent_direct_control,
    agent_rate_per_min,
    diagnose_caller,
)
from .. import recommendations, scope_guard
from ..simulator import DEMO_STOP_RELEASE_S
from ..objects import objects_json, objects_message
from ..occupancy import camera_occupancy
from ..rig_calibration import calibration_store
from ..runs import run_store, runs_message
from ..store import store
from ..workers import worker_store
from ..zones import zone_store

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _broadcast_fleet() -> None:
    await hub.broadcast({"type": "fleet", "robots": [r.model_dump(mode="json") for r in store.fleet()],
                         "workers": _workers_json(), "sequence": store.sequence_public()})


def _workers_json() -> list[dict]:
    return [w.model_dump(mode="json") for w in worker_store.list()]


@router.get("/fleet")
async def get_fleet(_role: Role = Depends(RequireViewer)) -> dict:
    return {
        "facility": {"id": settings.facility_id, "name": settings.facility_name},
        "industries": INDUSTRIES,
        "vendors": store.vendors(),
        "robots": [r.model_dump(mode="json") for r in store.fleet()],
        "workers": _workers_json(),
        "sequence": store.sequence_public(),
    }


@router.get("/workers", response_model=list[Worker])
async def list_workers(_role: Role = Depends(RequireViewer)) -> list[Worker]:
    """People on the floor right now (live only; nothing is kept once they check out)."""
    return worker_store.list()


@router.get("/robot/{robot_id}", response_model=RobotDetail)
async def get_robot(robot_id: str, _role: Role = Depends(RequireViewer)) -> RobotDetail:
    detail = store.robot_detail(robot_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="robot not found")
    return detail


@router.get("/robot/{robot_id}/sensors")
async def get_robot_sensors(robot_id: str, _role: Role = Depends(RequireViewer)) -> dict:
    if robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    snapshot = store.latest_sensors(robot_id)
    return {"robot_id": robot_id, "sensors": snapshot.model_dump(mode="json") if snapshot else None}


@router.get("/map")
async def get_map(_role: Role = Depends(RequireViewer)) -> dict:
    """The warehouse floor plan operators drop waypoints on (Global Spatial Map), with the
    keep-out and slow zones drawn on it, the objects the camera rig is tracking, and the floor
    cells the cameras see as blocked (``camera_cells``, ``[x0, y0, x1, y1]`` in metres)."""
    return {**WAREHOUSE, "zones": [z.model_dump(mode="json") for z in zone_store.list()],
            "objects": objects_json(),
            "camera_cells": camera_occupancy.summary(settings.facility_id)["cells"],
            "people_safety": {"slow_radius_m": settings.human_slow_radius_m,
                              "stop_radius_m": settings.human_stop_radius_m,
                              "slow_speed_mps": settings.human_slow_speed_mps}}


@router.get("/objects")
async def list_objects(_role: Role = Depends(RequireViewer)) -> dict:
    """Live tracked objects (pallets, carts, forklifts…) for this facility."""
    return objects_message()


@router.get("/calibration")
async def get_calibration(_role: Role = Depends(RequireViewer)) -> dict:
    """Latest rig-calibration report for this facility; ``calibration`` is null until one is
    uploaded."""
    return {"facility_id": settings.facility_id,
            "calibration": calibration_store.get(settings.facility_id)}


def _zones_message() -> dict:
    return {"type": "zones", "zones": [z.model_dump(mode="json") for z in zone_store.list()]}


@router.get("/zones", response_model=list[Zone])
async def list_zones(_role: Role = Depends(RequireViewer)) -> list[Zone]:
    return zone_store.list()


@router.post("/zones", response_model=Zone, status_code=201)
async def create_zone(body: ZoneIn, _role: Role = Depends(RequireOperator),
                      caller: Caller = Depends(CurrentCaller)) -> Zone:
    """Draw a keep-out or slow zone. Takes effect on the next route every robot plans.
    Operator-only: zones are safety configuration, so agents may not change them."""
    w, h = float(WAREHOUSE["width_m"]), float(WAREHOUSE["height_m"])
    if any(not (0.0 <= x <= w and 0.0 <= y <= h) for x, y in body.polygon):
        raise HTTPException(status_code=422, detail=f"zone points must lie inside the {w:g} x {h:g} m floor")
    zone = zone_store.create(body, actor=caller.actor)
    _replan_all()
    store.record_control(actor=caller.actor, role=caller.role.value, action="zone_create",
                         robot_id=None, ok=True, detail=f"{zone.type.value} '{zone.name}' ({zone.id})")
    await hub.broadcast(_zones_message())
    return zone


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str, _role: Role = Depends(RequireOperator),
                      caller: Caller = Depends(CurrentCaller)) -> dict:
    zone = zone_store.get(zone_id)
    if zone is None or not zone_store.delete(zone_id):
        raise HTTPException(status_code=404, detail="zone not found")
    _replan_all()
    store.record_control(actor=caller.actor, role=caller.role.value, action="zone_delete",
                         robot_id=None, ok=True, detail=f"{zone.type.value} '{zone.name}' ({zone_id})")
    await hub.broadcast(_zones_message())
    return {"ok": True, "zone_id": zone_id}


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(limit: int = Query(default=50, ge=1, le=500), task_key: Optional[str] = None,
                    robot_id: Optional[str] = None, vendor: Optional[str] = None,
                    _role: Role = Depends(RequireViewer)) -> list[RunSummary]:
    """Recorded task runs, newest first, each scored against its golden run."""
    runs = run_store.list(limit=limit, task_key=task_key, robot_id=robot_id, vendor=vendor)
    return [run_store.summary(r) for r in runs]


@router.get("/runs/golden", response_model=list[RunSummary])
async def list_golden_runs(_role: Role = Depends(RequireViewer)) -> list[RunSummary]:
    return [run_store.summary(r) for r in run_store.golden_runs()]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _role: Role = Depends(RequireViewer)) -> dict:
    """One run with every sample, its score, and the golden run it was scored against."""
    run = run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    summary = run_store.summary(run)
    golden = run_store.golden_for(run.vendor, run.task_key)
    return {"run": run.model_dump(mode="json"), "summary": summary.model_dump(mode="json"),
            "golden": golden.model_dump(mode="json") if golden and golden.id != run.id else None}


@router.post("/runs/{run_id}/golden", response_model=RunSummary)
async def set_golden_run(run_id: str, _role: Role = Depends(RequireOperator),
                         caller: Caller = Depends(CurrentCaller)) -> RunSummary:
    """Make this run the reference that later runs of the same task and vendor are scored
    against. Operator-only: it defines what "good" means for a deployment."""
    run = run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.outcome != RunOutcome.COMPLETE:
        raise HTTPException(status_code=409, detail=f"run {run.outcome.value}; only a complete run can be golden")
    run_store.set_golden(run_id)
    store.record_control(actor=caller.actor, role=caller.role.value, action="golden_run_set",
                         robot_id=run.robot_id, ok=True, detail=f"{run.task_key} ({run.vendor}) → {run.id}")
    await hub.broadcast(runs_message())
    return run_store.summary(run)


def _replan_all() -> None:
    """Drop every in-flight route so the next tick re-plans against the new zones."""
    for robot in store.robots.values():
        robot.route = []
        robot.route_goal = None


@router.post("/robot/{robot_id}/navigate")
async def navigate(robot_id: str, body: NavigateIn, dry_run: bool = False,
                   caller: Caller = Depends(RequireController)) -> dict:
    """Set operator waypoints — Orbital drives the robot there via visual control, bypassing
    its onboard SLAM. Gated on control.velocity (the visual servo issues velocity commands)."""
    if not body.waypoints:
        raise HTTPException(status_code=400, detail="at least one waypoint required")
    _enforce_audited(caller, "navigate", robot_id, APIScope.VELOCITY)
    vendor = store.robots[robot_id].vendor
    for p in body.waypoints:
        zone = zone_store.keepout_at(p.x, p.y, vendor)
        if zone is not None:
            detail = f"waypoint ({p.x:.1f}, {p.y:.1f}) is inside keep-out zone '{zone.name}'"
            store.record_control(actor=caller.actor, role=caller.role.value, action="navigate",
                                 robot_id=robot_id, ok=False, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
    route = " → ".join(f"({p.x:.1f}, {p.y:.1f})" for p in body.waypoints)
    if dry_run:
        return _dry_run(caller, "navigate", robot_id, f"drive {robot_id} via {route}")
    ok = store.set_waypoints(robot_id, [(p.x, p.y) for p in body.waypoints])
    if not ok:
        raise HTTPException(status_code=404, detail="robot not found")
    store.record_control(actor=caller.actor, role=caller.role.value, action="navigate",
                         robot_id=robot_id, ok=True, detail=route)
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "waypoints": [p.model_dump() for p in body.waypoints]}


@router.post("/robot/{robot_id}/navigate/clear")
async def navigate_clear(robot_id: str, caller: Caller = Depends(RequireController)) -> dict:
    if not store.clear_waypoints(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    store.record_control(actor=caller.actor, role=caller.role.value, action="navigate_clear",
                         robot_id=robot_id, ok=True)
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}


@router.post("/robot/{robot_id}/speed")
async def set_speed(robot_id: str, body: SpeedIn, dry_run: bool = False,
                    caller: Caller = Depends(RequireController)) -> dict:
    """Operator speed override (m/s). Scales patrol, visual-nav, and manual motion.
    Gated on control.velocity."""
    _enforce_audited(caller, "set_speed", robot_id, APIScope.VELOCITY)
    if dry_run:
        clamped = min(max(body.speed_mps, 0.05), settings.max_speed_mps)
        return _dry_run(caller, "set_speed", robot_id, f"set {robot_id} speed to {clamped:.2f} m/s")
    applied = store.set_speed(robot_id, body.speed_mps)
    if applied is None:
        raise HTTPException(status_code=404, detail="robot not found")
    store.record_control(actor=caller.actor, role=caller.role.value, action="set_speed",
                         robot_id=robot_id, ok=True, detail=f"{applied} m/s")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "speed_mps": applied}


@router.post("/robot/{robot_id}/drive")
async def drive(robot_id: str, body: DriveIn, caller: Caller = Depends(RequireController)) -> dict:
    """Manual jog along a heading (deg, 0 = east, CCW). Overrides patrol / clears waypoints.
    Gated on control.velocity."""
    import math

    _enforce_audited(caller, "drive", robot_id, APIScope.VELOCITY)
    heading_rad = math.radians(body.heading_deg)
    if not store.set_manual_drive(robot_id, heading_rad, body.speed_mps):
        raise HTTPException(status_code=409, detail="robot not found or halted")
    store.record_control(actor=caller.actor, role=caller.role.value, action="drive",
                         robot_id=robot_id, ok=True, detail=f"heading {body.heading_deg:.0f}°")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id, "heading_deg": body.heading_deg}


@router.post("/robot/{robot_id}/drive/stop")
async def drive_stop(robot_id: str, caller: Caller = Depends(RequireController)) -> dict:
    _enforce_audited(caller, "drive_stop", robot_id, APIScope.VELOCITY)
    if not store.stop_manual_drive(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    await _broadcast_fleet()
    return {"ok": True, "robot_id": robot_id}


@router.get("/tasks", response_model=list[Task])
async def list_tasks(_role: Role = Depends(RequireViewer)) -> list[Task]:
    return store.active_tasks()


def _enforce(robot_id: str, scope: APIScope) -> None:
    """Raise 404/403 unless the robot exists and its OEM has granted `scope`."""
    check = scope_guard.check_robot(robot_id, scope)
    if check.vendor is None:
        raise HTTPException(status_code=404, detail="robot not found")
    if not check.allowed:
        raise HTTPException(status_code=403, detail=check.reason)


def _enforce_audited(caller: Caller, action: str, robot_id: str, scope: APIScope) -> None:
    try:
        _enforce(robot_id, scope)
    except HTTPException as exc:
        store.record_control(actor=caller.actor, role=caller.role.value, action=action,
                             robot_id=robot_id, ok=False, detail=str(exc.detail))
        raise


def _dry_run(caller: Caller, action: str, robot_id: str | None, would: str) -> dict:
    store.record_control(actor=caller.actor, role=caller.role.value, action=action,
                         robot_id=robot_id, ok=True, detail=would, dry_run=True)
    return {"ok": True, "dry_run": True, "robot_id": robot_id, "would": would}


@router.post("/tasks", response_model=None, status_code=201)
async def create_task(task: TaskIn, dry_run: bool = False,
                      caller: Caller = Depends(RequireController)) -> Task | dict:
    if task.robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    _enforce_audited(caller, "dispatch_task", task.robot_id, APIScope.MISSION)
    if dry_run:
        return _dry_run(caller, "dispatch_task", task.robot_id,
                        f"dispatch '{task.description}' to {task.robot_id}")
    created = store.create_task(task, actor=caller.actor)
    store.record_control(actor=caller.actor, role=caller.role.value, action="dispatch_task",
                         robot_id=task.robot_id, ok=True, detail=task.description)
    await _broadcast_fleet()
    return created


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(limit: int = 50, _role: Role = Depends(RequireViewer)) -> list[Alert]:
    return store.recent_alerts(limit=limit)


@router.get("/oem-audit")
async def oem_call_audit(
    limit: int = 100,
    robot_id: str | None = None,
    _role: Role = Depends(RequireAdmin),
) -> dict:
    """Security Phase 5 — recent OEM public-API call audit trail."""
    events = store.oem_audit_recent(limit=min(max(limit, 1), 500), robot_id=robot_id)
    return {"count": len(events), "events": events}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, _role: Role = Depends(RequireOperator)) -> dict:
    if not store.acknowledge_alert(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return {"ok": True}


@router.get("/benchmark")
async def benchmark_report(_role: Role = Depends(RequireViewer)) -> dict:
    return {
        "report": render_benchmark_report(store),
        "vendors": [store.benchmark(v).model_dump(mode="json") for v in store.vendors()],
    }


@router.get("/benchmark/{vendor}", response_model=VendorBenchmark)
async def benchmark_for_vendor(vendor: str, _role: Role = Depends(RequireViewer)) -> VendorBenchmark:
    return store.benchmark(vendor)


@router.get("/orchestrator", response_model=OrchestratorStatus)
async def orchestrator_status(_role: Role = Depends(RequireViewer)) -> OrchestratorStatus:
    return orchestrator.status()


@router.post("/orchestrator/run", response_model=OrchestratorStatus)
async def orchestrator_run(_role: Role = Depends(RequireOperator)) -> OrchestratorStatus:
    """Trigger one supervisory pass on demand (useful for demos + tests)."""
    status = orchestrator.evaluate()
    await orchestrator.refresh_narrative()
    status = orchestrator.status()
    await _broadcast_fleet()
    return status


def _with_block_reason(rec: Recommendation) -> Recommendation:
    if rec.scope is None or rec.status != RecommendationStatus.PROPOSED:
        return rec
    check = scope_guard.check_robot(recommendations.executing_robot(rec) or "", rec.scope)
    if not check.allowed:
        rec.blocked_reason = check.reason
    return rec


def _open_action_or_error(action_id: str) -> Recommendation:
    rec = store.actions.get(action_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="action not found")
    if rec.status != RecommendationStatus.PROPOSED:
        raise HTTPException(status_code=409, detail=f"action is already {rec.status.value}")
    return rec


@router.get("/actions", response_model=list[Recommendation])
async def list_actions(include_closed: bool = False, limit: int = 50,
                       _role: Role = Depends(RequireViewer)) -> list[Recommendation]:
    """Ranked next-best-action queue (orchestrator Policy 4). Open items first, by score."""
    ranked = store.actions.ranked(include_closed=include_closed, limit=min(max(limit, 1), 100))
    return [_with_block_reason(r) for r in ranked]


@router.post("/actions", response_model=None, status_code=201)
async def propose_action(body: ActionProposalIn,
                         caller: Caller = Depends(RequireProposer)) -> Recommendation | dict:
    """Propose a next action into the queue. This is the agents' default write path: nothing
    runs until an operator accepts it."""
    if body.robot_id not in store.robots:
        raise HTTPException(status_code=404, detail="robot not found")
    if body.kind == RecommendationKind.HANDOVER:
        if not body.target_robot_id or not body.task:
            raise HTTPException(status_code=422, detail="handover needs target_robot_id and task")
        if body.target_robot_id not in store.robots:
            raise HTTPException(status_code=404, detail="target robot not found")
    if body.kind == RecommendationKind.DISPATCH_TASK and not body.task:
        raise HTTPException(status_code=422, detail="dispatch_task needs task")
    assignee_id = _assist_assignee(body) if body.kind == RecommendationKind.ASSIST else None

    scope = recommendations.KIND_SCOPE[body.kind]
    summary = f"{body.kind.value} for {body.robot_id}: {body.rationale}"
    if body.dry_run:
        out = _dry_run(caller, "propose_action", body.robot_id, f"propose {summary}")
        if scope is not None:
            check = scope_guard.check_robot(body.target_robot_id or body.robot_id, scope)
            out["blocked_reason"] = None if check.allowed else check.reason
        return out
    rec = store.actions.propose(
        body.kind, robot_id=body.robot_id, target_robot_id=body.target_robot_id, task=body.task,
        score=body.score, rationale=body.rationale, scope=scope,
        assignee_type=recommendations.assignee_for(body.kind), assignee_id=assignee_id,
        source="agent" if caller.is_agent else "operator", actor=caller.actor,
    )
    if rec is None:
        store.record_control(actor=caller.actor, role=caller.role.value, action="propose_action",
                             robot_id=body.robot_id, ok=False, detail="recently dismissed")
        raise HTTPException(status_code=409, detail="an operator dismissed this recently; try again later")
    store.record_control(actor=caller.actor, role=caller.role.value, action="propose_action",
                         robot_id=body.robot_id, ok=True, detail=summary)
    await hub.broadcast(actions_message(store))
    return _with_block_reason(rec.model_copy())


def _assist_assignee(body: ActionProposalIn) -> str:
    """The worker an assist goes to: the one named, or the nearest person on shift."""
    if body.assignee_id:
        worker = worker_store.get(body.assignee_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="worker not found or no longer on the floor")
        if worker.status != WorkerStatus.ON_SHIFT:
            raise HTTPException(status_code=409, detail=f"{body.assignee_id} is on {worker.status.value}")
        return worker.id
    robot = store.robots[body.robot_id]
    worker, _ = worker_store.nearest(robot.pose_external.x, robot.pose_external.y, on_shift_only=True)
    if worker is None:
        raise HTTPException(status_code=409, detail="no one is on shift to assist")
    return worker.id


@router.post("/actions/{action_id}/accept", response_model=Recommendation)
async def accept_action(action_id: str, _role: Role = Depends(RequireOperator),
                        caller: Caller = Depends(CurrentCaller)) -> Recommendation:
    """Accept a proposal. Executable kinds run now, through the same OEM scope check as the
    direct control endpoints; advisory kinds are marked accepted for the operator to handle."""
    rec = _open_action_or_error(action_id)
    if rec.scope is None:
        resolved = store.actions.resolve(action_id, RecommendationStatus.ACCEPTED)
    else:
        _enforce_audited(caller, f"accept:{rec.kind.value}", recommendations.executing_robot(rec) or "", rec.scope)
        executed, detail = recommendations.execute(store, rec, actor=caller.actor)
        if not executed:
            store.actions.resolve(action_id, RecommendationStatus.EXPIRED)
            await hub.broadcast(actions_message(store))
            raise HTTPException(status_code=409, detail=f"no longer applicable: {detail}")
        resolved = store.actions.resolve(action_id, RecommendationStatus.DONE)
        store.record_control(actor=caller.actor, role=caller.role.value, action=f"accept:{rec.kind.value}",
                             robot_id=recommendations.executing_robot(rec), ok=True,
                             detail=f"{detail} (proposed by {rec.actor or rec.source})")
        await _broadcast_fleet()
    await hub.broadcast(actions_message(store))
    return resolved.model_copy()


@router.post("/actions/{action_id}/dismiss", response_model=Recommendation)
async def dismiss_action(action_id: str, _role: Role = Depends(RequireOperator)) -> Recommendation:
    _open_action_or_error(action_id)
    resolved = store.actions.resolve(action_id, RecommendationStatus.DISMISSED)
    await hub.broadcast(actions_message(store))
    return resolved.model_copy()


@router.get("/whoami")
async def whoami(
    x_orbital_role: str | None = Header(default=None, alias="X-Orbital-Role"),
    x_orbital_actor: str | None = Header(default=None, alias="X-Orbital-Actor"),
    x_orbital_proxy_secret: str | None = Header(default=None, alias="X-Orbital-Proxy-Secret"),
    authorization: str | None = Header(default=None),
    orbital_session: str | None = Cookie(default=None),
) -> dict:
    """How Orbital resolves this caller's role — for debugging a proxy or token that is
    refused. Never raises on bad credentials; reports the refusal instead."""
    return diagnose_caller(x_orbital_role=x_orbital_role, x_orbital_actor=x_orbital_actor,
                           x_orbital_proxy_secret=x_orbital_proxy_secret, authorization=authorization,
                           session_cookie=orbital_session)


@router.get("/agent-policy")
async def agent_policy(_role: Role = Depends(RequireViewer)) -> dict:
    """What an agent may do on this deployment — read by the MCP server and shown to models."""
    return {
        "direct_control": agent_direct_control(),
        "rate_per_min": agent_rate_per_min(),
        "always_allowed": ["read", "propose_action", "estop"],
        "operator_only": ["accept_action", "dismiss_action"],
    }


@router.get("/control-audit")
async def control_audit(limit: int = 100, actor: str | None = None,
                        _role: Role = Depends(RequireAdmin)) -> dict:
    """Who issued which control or proposal call (operators and agents), newest first."""
    rows = store.control_audit_recent(limit=min(max(limit, 1), 500), actor=actor)
    return {"count": len(rows), "events": rows}


@router.post("/robot/{robot_id}/estop")
async def estop(robot_id: str, dry_run: bool = False, caller: Caller = Depends(DemoStopper)) -> dict:
    _enforce_audited(caller, "estop", robot_id, APIScope.ESTOP)
    if dry_run:
        return _dry_run(caller, "estop", robot_id, f"halt {robot_id}")
    demo = caller.role == Role.VIEWER
    if not store.estop(robot_id, demo=demo):
        raise HTTPException(status_code=404, detail="robot not found")
    store.record_control(actor=caller.actor, role=caller.role.value, action="estop", robot_id=robot_id, ok=True)
    await _broadcast_fleet()
    out = {"ok": True, "robot_id": robot_id, "state": "halted"}
    if demo:
        out["auto_resume_s"] = int(DEMO_STOP_RELEASE_S)
    return out


@router.post("/robot/{robot_id}/resume")
async def resume(robot_id: str, dry_run: bool = False, caller: Caller = Depends(DemoResumer)) -> dict:
    _enforce_audited(caller, "resume", robot_id, APIScope.ESTOP)
    if dry_run:
        return _dry_run(caller, "resume", robot_id, f"resume {robot_id}")
    if not store.resume(robot_id):
        raise HTTPException(status_code=404, detail="robot not found")
    store.record_control(actor=caller.actor, role=caller.role.value, action="resume", robot_id=robot_id, ok=True)
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
async def oem_catalog(_role: Role = Depends(RequireViewer)) -> dict:
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
async def onboard_oem(body: OEMOnboardIn, _role: Role = Depends(RequireAdmin)) -> dict:
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
async def update_oem_policies(oem_id: str, body: OEMPolicies, _role: Role = Depends(RequireAdmin)) -> IntegrationProfile:
    if oem_store.set_policies(oem_id, body) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.get("/oems", response_model=list[IntegrationProfile])
async def list_oems(_role: Role = Depends(RequireViewer)) -> list[IntegrationProfile]:
    return [p for p in (oem_store.profile(o.id) for o in oem_store.list()) if p is not None]


@router.get("/oems/{oem_id}", response_model=IntegrationProfile)
async def get_oem(oem_id: str, _role: Role = Depends(RequireViewer)) -> IntegrationProfile:
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/grant", response_model=IntegrationProfile)
async def operator_grant(oem_id: str, body: ScopeGrantIn, _role: Role = Depends(RequireAdmin)) -> IntegrationProfile:
    if oem_store.grant_scopes(oem_id, body.scopes) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/revoke", response_model=IntegrationProfile)
async def operator_revoke(oem_id: str, body: ScopeGrantIn, _role: Role = Depends(RequireAdmin)) -> IntegrationProfile:
    if oem_store.revoke_scopes(oem_id, body.scopes) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.delete("/oems/{oem_id}")
async def remove_oem(oem_id: str, _role: Role = Depends(RequireAdmin)) -> dict:
    if not oem_store.remove(oem_id):
        raise HTTPException(status_code=404, detail="OEM not found")
    return {"ok": True, "oem_id": oem_id}


@router.post("/oems/{oem_id}/suspend", response_model=IntegrationProfile)
async def suspend_oem(oem_id: str, _role: Role = Depends(RequireAdmin)) -> IntegrationProfile:
    if oem_store.set_status(oem_id, OEMStatus.SUSPENDED) is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return _profile_or_404(oem_id)


@router.post("/oems/{oem_id}/reactivate", response_model=IntegrationProfile)
async def reactivate_oem(oem_id: str, _role: Role = Depends(RequireAdmin)) -> IntegrationProfile:
    partner = oem_store.get(oem_id)
    if partner is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    # Back to ACTIVE if it still holds scopes, otherwise PENDING (awaiting a grant).
    oem_store.set_status(oem_id, OEMStatus.ACTIVE if partner.granted_scopes else OEMStatus.PENDING)
    return _profile_or_404(oem_id)
