"""Simulated ARIA Edge Node.

Stands in for the hardware edge (cameras + CV pipeline + TF Hijack + safety halt) so
the cloud, dashboard, alerts, and Benchmark Library are fully exercised without a lab.

Per tick, for each robot it:
  1. advances the ground-truth (external) pose along a patrol loop,
  2. derives the robot's self-reported (internal) pose = external + odometric drift,
     where ARIA continuously decays the drift back toward zero (the correction loop),
  3. occasionally injects a degradation spike (SLAM drift),
  4. ingests the drift delta as telemetry (feeding MTBD/recovery/benchmark),
  5. runs the safety-halt rule: drift past the halt threshold -> E-Stop + alert.

Real edge nodes replace this by POSTing to /api/v1/telemetry and /api/v1/alerts —
the exact same store ingest path.
"""
from __future__ import annotations

import asyncio
import math
import random
import time

from . import pathing, persistence
from .config import SEQUENCE_PERIOD_S, SEQUENCE_THEMES, WAREHOUSE, settings
from .events import hub
from .models import (
    AlertIn,
    AlertSeverity,
    AlertType,
    BatteryTelemetry,
    ImuTelemetry,
    MotorTelemetry,
    RobotState,
    SpatialTelemetry,
    TelemetryIn,
)
from .store import RobotRuntime, store

_SPEED_MPS = 0.6
_DRIFT_DECAY = 0.90          # ARIA re-convergence per tick
_DRIFT_NOISE = 0.015         # baseline odometric noise (m)
_SPIKE_PROB = 0.004          # chance/tick a robot starts drifting badly (kept rare so the fleet stays live)
_DRIFT_HALT = "DRIFT_HALT"   # sim safety halt (auto-recovers immediately)
_AUTO_ESTOP = "AUTO_ESTOP"   # orchestrator auto-halt (auto-recovers after a cooldown)
# "E_STOP" (human/operator) is NOT in this set — it latches until an operator resumes.
_RECOVERABLE_HALTS = {_DRIFT_HALT, _AUTO_ESTOP}
_JOINTS = ("hip_left", "hip_right", "knee_left", "knee_right")


def _sensors(robot: RobotRuntime) -> dict:
    """Synthesize a plausible multi-modal reading so the dashboard vitals panel is alive.

    Values track the robot's actual sim state: temps/currents climb with activity and
    drift, voltage tracks charge, and the spatial block mirrors the robot's *self-reported*
    (internal) pose — so it stays consistent with the drift shown elsewhere.
    """
    active = robot.state == RobotState.ACTIVE
    charging = robot.state == RobotState.CHARGING
    load = 1.0 if active else (0.3 if charging else 0.15)
    pct = robot.battery_pct

    battery = BatteryTelemetry(
        pct=round(pct, 1),
        temperature_c=round(30.0 + load * 8.0 + (100.0 - pct) * 0.03 + random.gauss(0, 0.4), 1),
        voltage_v=round(42.0 + (pct / 100.0) * 8.0, 1),                       # 42–50V pack
        current_a=round((-3.0 if charging else 6.5 if active else 0.8) + random.gauss(0, 0.3), 1),
        cycles=150 + (abs(hash(robot.id)) % 400),
    )

    motors = [
        MotorTelemetry(
            joint=joint,
            temperature_c=round(45.0 + j * 3.0 + load * 15.0 + robot.drift_delta_m * 12.0 + random.gauss(0, 0.6), 1),
            current_a=round((0.4 + load * 3.5) + random.gauss(0, 0.2), 2),
            torque_nm=round((0.4 + load * 3.5) * 3.2 + random.gauss(0, 0.4), 1),
            velocity_rad_s=round((robot.speed_mps * 2.0 if active else 0.0) + random.gauss(0, 0.05), 2),
        )
        for j, joint in enumerate(_JOINTS)
    ]

    imu = ImuTelemetry(
        accel=[round(random.gauss(0, 0.15), 3), round(random.gauss(0, 0.15), 3), round(9.81 + random.gauss(0, 0.05), 3)],
        gyro=[round(random.gauss(0, 0.02), 3) for _ in range(3)],
    )

    spatial = SpatialTelemetry(
        x=round(robot.pose_internal.x, 3), y=round(robot.pose_internal.y, 3), z=0.0,
        yaw=round(robot.pose_internal.theta, 3),
        linear_velocity_mps=round(robot.speed_mps if active else 0.0, 2),
        angular_velocity_rps=round(random.gauss(0, 0.05), 3),
    )

    temperatures_c = {
        "cpu": round(55.0 + load * 12.0 + random.gauss(0, 0.8), 1),
        "ambient": round(23.0 + random.gauss(0, 0.5), 1),
    }
    return dict(battery=battery, motors=motors, imu=imu, spatial=spatial, temperatures_c=temperatures_c)


def _patrol(i: int) -> list[tuple[float, float]]:
    """A rectangular patrol loop, offset per robot so the fleet spreads across the map."""
    cx = 6.0 * (i % 3)
    cy = 5.0 * (i // 3)
    w, h = 3.0, 2.0
    corners = [(cx, cy), (cx + w, cy), (cx + w, cy + h), (cx, cy + h)]
    # Densify each edge so motion is smooth at the patrol speed.
    path: list[tuple[float, float]] = []
    for a, b in zip(corners, corners[1:] + corners[:1]):
        for s in range(6):
            t = s / 6.0
            path.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return path


# Named work points (pickup/drop stations) sourced from the map config so the UI and the
# simulator agree on their coordinates. Missions shuttle payloads between these.
_STATIONS: list[tuple[str, float, float]] = [
    (s["id"], float(s["x"]), float(s["y"])) for s in WAREHOUSE.get("stations", [])
]
_STATION_XY: dict[str, tuple[float, float]] = {label: (x, y) for label, x, y in _STATIONS}

_WORK_DWELL_S = 3.0   # visible "performing task at the pickup" pause before carrying to drop


def _current_theme() -> dict:
    """The active fleet-sequence theme (defaults to the first if the store isn't primed)."""
    tid = store.sequence.get("theme")
    for theme in SEQUENCE_THEMES:
        if theme["id"] == tid:
            return theme
    return SEQUENCE_THEMES[0]


def _station_xy(label: str) -> tuple[float, float]:
    return _STATION_XY.get(label, (WAREHOUSE["width_m"] / 2, WAREHOUSE["height_m"] / 2))


def _assign_task(robot: RobotRuntime) -> None:
    """Give the robot its next mission in the current sequence theme: go to a pickup station
    (or an inbound hand-off point), perform the task there, then carry to a drop-off station.
    Puts it green/ACTIVE on the first leg (en route to the pickup)."""
    theme = _current_theme()
    verb = theme["verb"]
    drop_label = random.choice(theme["dropoff"])
    dx, dy = _station_xy(drop_label)

    if robot.pending_pickup is not None:
        px, py = robot.pending_pickup
        pick_label = "hand-off"
        robot.pending_pickup = None
    else:
        pick_label = random.choice(theme["pickup"])
        px, py = _station_xy(pick_label)

    robot.mission_pickup = (pick_label, px, py)
    robot.mission_dropoff = (drop_label, dx, dy)
    robot.mission_goal = f"{verb}: {pick_label} → {drop_label}"
    robot.mission_phase = "en_route_pickup"
    robot.current_task = f"En route to {pick_label}"
    robot.task_target = (px, py)
    robot.handoff_partner = None
    robot.cooldown_until = None
    robot.work_until = None
    robot.route = []
    robot.route_goal = None
    robot.state = RobotState.ACTIVE


def _begin_work(robot: RobotRuntime) -> None:
    """Arrived at the pickup — perform the task there for a visible dwell before carrying on."""
    label = robot.mission_pickup[0] if robot.mission_pickup else "station"
    robot.mission_phase = "working"
    robot.current_task = f"Working at {label}"
    robot.work_until = time.time() + _WORK_DWELL_S
    robot.task_target = None
    robot.route = []
    robot.route_goal = None


def _begin_carry(robot: RobotRuntime) -> None:
    """Task done at the pickup — carry the payload to the drop-off station."""
    if robot.mission_dropoff is None:
        _complete_task(robot)
        return
    label, dx, dy = robot.mission_dropoff
    robot.mission_phase = "carrying"
    robot.current_task = f"Carrying → {label}"
    robot.work_until = None
    robot.task_target = (dx, dy)
    robot.route = []
    robot.route_goal = None


def _handoff_target(robot: RobotRuntime) -> RobotRuntime | None:
    """Pick a peer to receive the finished payload: prefer a robot already paused between
    tasks (or idle-in-cycle), else a busy peer without a pending pickup. Nearest wins."""
    cands = [
        r for r in store.robots.values()
        if r.id != robot.id
        and r.state in (RobotState.COOLDOWN, RobotState.ACTIVE)
        and r.error_code is None
        and not r.nav_queue and r.manual_heading is None
        and r.pending_pickup is None
    ]
    if not cands:
        return None
    cands.sort(key=lambda r: (
        0 if r.state == RobotState.COOLDOWN else 1,
        math.hypot(r.pose_external.x - robot.pose_external.x, r.pose_external.y - robot.pose_external.y),
    ))
    return cands[0]


def _complete_task(robot: RobotRuntime) -> None:
    """Finish the current delivery: hand the payload to a peer, then go red (COOLDOWN) and
    pause before the next mission."""
    partner = _handoff_target(robot)
    if partner is not None:
        partner.pending_pickup = (robot.pose_external.x, robot.pose_external.y)
        robot.handoff_partner = partner.id
        robot.current_task = f"Delivered → hand-off to {partner.id}"
    else:
        robot.handoff_partner = None
        robot.current_task = "Delivered — awaiting next task"
    robot.mission_phase = "idle"
    robot.task_target = None
    robot.work_until = None
    robot.route = []
    robot.route_goal = None
    robot.state = RobotState.COOLDOWN
    robot.cooldown_until = time.time() + settings.task_pause_s


def _advance_point(robot: RobotRuntime, dt: float, target: tuple[float, float]) -> bool:
    """Drive the camera-observed pose straight to a point; return True on arrival."""
    tx, ty = target
    x, y = robot.pose_external.x, robot.pose_external.y
    dx, dy = tx - x, ty - y
    dist = math.hypot(dx, dy)
    step = robot.speed_mps * dt
    if dist <= step or dist == 0.0:
        theta = math.atan2(dy, dx) if dist else robot.pose_external.theta
        robot.pose_external = robot.pose_external.model_copy(update={"x": tx, "y": ty, "theta": theta})
        return True
    nx, ny = x + dx / dist * step, y + dy / dist * step
    robot.pose_external = robot.pose_external.model_copy(update={"x": nx, "y": ny, "theta": math.atan2(dy, dx)})
    return False


def _drive_to_goal(robot: RobotRuntime, dt: float, goal: tuple[float, float]) -> bool:
    """Follow an obstacle-aware route to ``goal`` (routing around racks). Returns True on
    arrival at the final goal. Re-plans when the goal changes or the route is exhausted."""
    gx, gy = goal
    if (robot.route_goal is None
            or math.hypot(robot.route_goal[0] - gx, robot.route_goal[1] - gy) > 0.05
            or not robot.route):
        robot.route = pathing.plan((robot.pose_external.x, robot.pose_external.y), (gx, gy))
        robot.route_goal = (gx, gy)
    if not robot.route:
        robot.route = [goal]
    if _advance_point(robot, dt, robot.route[0]):
        robot.route.pop(0)
        if not robot.route:
            robot.route_goal = None
            return True
    return False


def _clear_route(robot: RobotRuntime) -> None:
    robot.route = []
    robot.route_goal = None


def _new_sequence() -> None:
    """Rotate the whole fleet onto a fresh theme and reassign every controllable robot, so the
    floor visibly changes objective — and nothing sits stale — every SEQUENCE_PERIOD_S."""
    cur = store.sequence.get("theme")
    choices = [t for t in SEQUENCE_THEMES if t["id"] != cur] or SEQUENCE_THEMES
    theme = random.choice(choices)
    store.set_sequence(
        id=int(store.sequence.get("id", 0)) + 1,
        theme=theme["id"], label=theme["label"], objective=theme["objective"],
        started_at=time.time(), period_s=SEQUENCE_PERIOD_S,
    )
    for robot in store.robots.values():
        # Never override a charging robot, an operator's waypoint/manual jog, or a human
        # E-Stop latch — but wake anything the sim itself parked (idle / cooldown / sim-halt).
        if robot.state == RobotState.CHARGING:
            continue
        if robot.nav_queue or robot.manual_heading is not None:
            continue
        if robot.error_code == "E_STOP":
            continue
        robot.error_code = None
        robot.halted_at = None
        robot.pending_pickup = None
        _assign_task(robot)   # → ACTIVE on the new theme's first leg


def prime() -> None:
    """Seed the fleet on the first mission sequence; leave one robot idle to show that state."""
    _new_sequence()
    for idx, robot in enumerate(store.robots.values()):
        start = _STATIONS[idx % len(_STATIONS)] if _STATIONS else (robot.id, robot.pose_external.x, robot.pose_external.y)
        robot.pose_external = robot.pose_external.model_copy(update={"x": start[1], "y": start[2]})
        robot.pose_internal = robot.pose_external.model_copy()
        if idx % 5 == 4:
            # One robot starts idle so the IDLE state is represented on the floor.
            robot.state = RobotState.IDLE
            robot.mission_phase = "idle"
            robot.mission_goal = None
            robot.task_target = None
            robot.current_task = "Idle"
        elif idx % 3 == 0:
            # Stagger initial cooldowns so the fleet's start/stop rhythm is desynchronized.
            robot.state = RobotState.COOLDOWN
            robot.task_target = None
            robot.current_task = "Awaiting next task"
            robot.cooldown_until = time.time() + (idx % 5) * 2.0
    # Re-apply any operator waypoints that were set before a restart.
    store.restore_waypoints()


def _advance_external(robot: RobotRuntime, dt: float) -> None:
    if not robot.trajectory:
        return
    tx, ty = robot.trajectory[robot.traj_index]
    x, y = robot.pose_external.x, robot.pose_external.y
    dx, dy = tx - x, ty - y
    dist = math.hypot(dx, dy)
    step = robot.speed_mps * dt
    if dist <= step or dist == 0.0:
        robot.traj_index = (robot.traj_index + 1) % len(robot.trajectory)
        nx, ny = tx, ty
        theta = math.atan2(dy, dx) if dist else robot.pose_external.theta
    else:
        nx, ny = x + dx / dist * step, y + dy / dist * step
        theta = math.atan2(dy, dx)
    robot.pose_external = robot.pose_external.model_copy(update={"x": nx, "y": ny, "theta": theta})


def _advance_nav(robot: RobotRuntime, dt: float) -> None:
    """Visual-control navigation: drive the camera-observed (external) pose to the operator's
    waypoint, routing AROUND the racks (Orbital's overhead cameras localize + steer, bending
    the path around obstacles). Deliberately ignores onboard SLAM, so odometric drift can't
    send it off course. Advances the queue on arrival; clears to patrol when it empties.
    """
    if _drive_to_goal(robot, dt, robot.nav_queue[0]):
        robot.nav_queue.pop(0)
        if not robot.nav_queue:
            robot.current_task = "Autonomous patrol"
            persistence.save_waypoints(robot.id, [])   # mission complete — drop persisted goal


def _advance_manual(robot: RobotRuntime, dt: float) -> None:
    """Operator jog: drive the external pose along the commanded heading at the set speed.
    Clamps to the warehouse bounds; if it reaches a wall it stops (drops back to idle)."""
    theta = robot.manual_heading or 0.0
    w = float(WAREHOUSE["width_m"]); h = float(WAREHOUSE["height_m"])
    step = robot.speed_mps * dt
    nx = robot.pose_external.x + math.cos(theta) * step
    ny = robot.pose_external.y + math.sin(theta) * step
    cx = min(max(nx, 0.0), w)
    cy = min(max(ny, 0.0), h)
    # Stop the jog at a wall OR a rack face so the robot never drives into an obstacle.
    if cx != nx or cy != ny or pathing._point_blocked(cx, cy):
        robot.manual_heading = None
        robot.current_task = "Idle"
        robot.state = RobotState.IDLE
        return
    robot.pose_external = robot.pose_external.model_copy(update={"x": cx, "y": cy, "theta": theta})


def _update_drift(robot: RobotRuntime) -> None:
    bx, by = robot.drift_bias
    # ARIA correction: decay drift toward zero, plus a little baseline noise.
    bx = bx * _DRIFT_DECAY + random.gauss(0, _DRIFT_NOISE)
    by = by * _DRIFT_DECAY + random.gauss(0, _DRIFT_NOISE)
    # Degradation spike: an impulse of SLAM drift the correction loop must recover from.
    if random.random() < _SPIKE_PROB:
        mag = random.uniform(0.25, 0.75)
        ang = random.uniform(0, 2 * math.pi)
        bx += mag * math.cos(ang)
        by += mag * math.sin(ang)
    robot.drift_bias = (bx, by)
    robot.pose_internal = robot.pose_external.model_copy(
        update={"x": robot.pose_external.x + bx, "y": robot.pose_external.y + by}
    )
    robot.drift_delta_m = math.hypot(bx, by)


async def _tick(dt: float) -> list[AlertIn]:
    alerts: list[AlertIn] = []
    for robot in store.robots.values():
        if robot.state == RobotState.CHARGING:
            robot.battery_pct = min(100.0, robot.battery_pct + 0.6)
            if robot.battery_pct >= 100.0:
                robot.state = RobotState.ACTIVE if robot.current_task else RobotState.IDLE
            continue

        if robot.state == RobotState.HALTED:
            # Sim safety halts recover immediately; orchestrator auto-stops recover after a
            # cooldown (ARIA re-converges). A human E-Stop latches until an operator resumes.
            if robot.error_code in _RECOVERABLE_HALTS:
                cooled = (
                    robot.error_code == _DRIFT_HALT
                    or robot.halted_at is None
                    or (time.time() - robot.halted_at) >= settings.auto_recover_s
                )
                if cooled:
                    robot.drift_bias = (0.0, 0.0)
                    robot.drift_delta_m = 0.0
                    robot.error_code = None
                    robot.halted_at = None
                    robot.state = RobotState.ACTIVE if robot.current_task else RobotState.IDLE
            continue

        if robot.state == RobotState.COOLDOWN:
            # Task done — hold position (red) until the pause elapses, then take the next task.
            if robot.cooldown_until is None or time.time() >= robot.cooldown_until:
                _assign_task(robot)              # → ACTIVE (green); falls through to move below
            else:
                _update_drift(robot)
                store.ingest_telemetry(
                    TelemetryIn(
                        robot_id=robot.id, vendor=robot.vendor, model=robot.model,
                        facility_id=settings.facility_id, delta_meters=robot.drift_delta_m,
                        **_sensors(robot),
                    )
                )
                continue

        if robot.state == RobotState.ACTIVE:
            if robot.nav_queue:
                _advance_nav(robot, dt)          # operator waypoint via visual control
            elif robot.manual_heading is not None:
                _advance_manual(robot, dt)       # operator manual jog (heading + speed)
            elif robot.mission_phase == "working":
                # Performing the task at the pickup — hold (green) until the dwell elapses,
                # then carry the payload on to the drop-off station.
                if robot.work_until is None or time.time() >= robot.work_until:
                    _begin_carry(robot)
            else:
                if robot.task_target is None:
                    _assign_task(robot)          # ensure an autonomous robot always has work
                if _drive_to_goal(robot, dt, robot.task_target):  # routed around racks to target
                    if robot.mission_phase == "en_route_pickup":
                        _begin_work(robot)       # arrived at pickup → perform the task here
                    else:
                        _complete_task(robot)    # dropped off → hand off + red cooldown pause
            robot.battery_pct = max(0.0, robot.battery_pct - 0.05)
            if robot.battery_pct < 15.0:
                robot.state = RobotState.CHARGING

        _update_drift(robot)
        store.ingest_telemetry(
            TelemetryIn(
                robot_id=robot.id, vendor=robot.vendor, model=robot.model,
                facility_id=settings.facility_id, delta_meters=robot.drift_delta_m,
                **_sensors(robot),
            )
        )

        # Safety-halt rule (mirrors the edge Safety Halt Controller).
        if robot.drift_delta_m > settings.halt_threshold_m and robot.state != RobotState.HALTED:
            robot.state = RobotState.HALTED
            robot.error_code = _DRIFT_HALT
            alerts.append(
                AlertIn(
                    robot_id=robot.id, type=AlertType.DRIFT_EXCEEDED, severity=AlertSeverity.CRITICAL,
                    delta_meters=round(robot.drift_delta_m, 3),
                    message=(f"{robot.vendor} {robot.model}: drift {robot.drift_delta_m:.2f}m exceeded "
                             f"halt threshold {settings.halt_threshold_m}m — E-Stop engaged."),
                )
            )
    return alerts


async def run() -> None:
    prime()
    hz = max(0.5, settings.sim_tick_hz)
    dt = 1.0 / hz
    while True:
        # Rotate the fleet mission sequence when its window elapses (keeps the floor alive).
        seq = store.sequence
        if time.time() - float(seq.get("started_at", 0)) >= float(seq.get("period_s", SEQUENCE_PERIOD_S)):
            _new_sequence()
        alerts = await _tick(dt)
        for a in alerts:
            saved = store.add_alert(a)
            await hub.broadcast({"type": "alert", "alert": saved.model_dump(mode="json")})
        await hub.broadcast({
            "type": "fleet",
            "robots": [r.model_dump(mode="json") for r in store.fleet()],
            "sequence": store.sequence_public(),
        })
        await asyncio.sleep(dt)
