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

from .config import settings
from .events import hub
from .models import AlertIn, AlertSeverity, AlertType, RobotState, TelemetryIn
from .store import RobotRuntime, store

_SPEED_MPS = 0.6
_DRIFT_DECAY = 0.90          # ARIA re-convergence per tick
_DRIFT_NOISE = 0.015         # baseline odometric noise (m)
_SPIKE_PROB = 0.01           # chance/tick a robot starts drifting badly
_DRIFT_HALT = "DRIFT_HALT"   # sim-triggered halt (auto-recovers); vs manual "E_STOP"


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


def prime() -> None:
    """Assign patrol trajectories and start most of the fleet on an autonomous task."""
    for idx, robot in enumerate(store.robots.values()):
        robot.trajectory = _patrol(idx)
        robot.traj_index = 0
        start = robot.trajectory[0]
        robot.pose_external = robot.pose_external.model_copy(update={"x": start[0], "y": start[1]})
        robot.pose_internal = robot.pose_external.model_copy()
        # Leave one robot idle to exercise that state; the rest patrol.
        if idx % 5 != 4:
            robot.state = RobotState.ACTIVE
            robot.current_task = "Autonomous patrol"


def _advance_external(robot: RobotRuntime, dt: float) -> None:
    if not robot.trajectory:
        return
    tx, ty = robot.trajectory[robot.traj_index]
    x, y = robot.pose_external.x, robot.pose_external.y
    dx, dy = tx - x, ty - y
    dist = math.hypot(dx, dy)
    step = _SPEED_MPS * dt
    if dist <= step or dist == 0.0:
        robot.traj_index = (robot.traj_index + 1) % len(robot.trajectory)
        nx, ny = tx, ty
        theta = math.atan2(dy, dx) if dist else robot.pose_external.theta
    else:
        nx, ny = x + dx / dist * step, y + dy / dist * step
        theta = math.atan2(dy, dx)
    robot.pose_external = robot.pose_external.model_copy(update={"x": nx, "y": ny, "theta": theta})


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
            # Sim-triggered halts auto-recover (ARIA re-converges); manual E-Stops wait for an operator.
            if robot.error_code == _DRIFT_HALT:
                robot.drift_bias = (0.0, 0.0)
                robot.drift_delta_m = 0.0
                robot.error_code = None
                robot.state = RobotState.ACTIVE if robot.current_task else RobotState.IDLE
            continue

        if robot.state == RobotState.ACTIVE:
            _advance_external(robot, dt)
            robot.battery_pct = max(0.0, robot.battery_pct - 0.05)
            if robot.battery_pct < 15.0:
                robot.state = RobotState.CHARGING

        _update_drift(robot)
        store.ingest_telemetry(
            TelemetryIn(
                robot_id=robot.id, vendor=robot.vendor, model=robot.model,
                facility_id=settings.facility_id, delta_meters=robot.drift_delta_m,
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
        alerts = await _tick(dt)
        for a in alerts:
            saved = store.add_alert(a)
            await hub.broadcast({"type": "alert", "alert": saved.model_dump(mode="json")})
        await hub.broadcast({
            "type": "fleet",
            "robots": [r.model_dump(mode="json") for r in store.fleet()],
        })
        await asyncio.sleep(dt)
