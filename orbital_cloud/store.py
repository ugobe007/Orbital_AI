"""In-memory state for the Orbital AI Cloud.

For v0 this is a process-local store (no external DB) so the whole stack runs with
one command. It is the single source of truth for fleet state, drift telemetry,
alerts, and tasks, and it encapsulates the Benchmark Library math (MTBD, recovery
latency, environmental degradation) so the API and simulator share one code path.

A real deployment swaps this for Postgres + InfluxDB behind the same methods.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from typing import Optional

from .config import SEED_FLEET, VENDOR_BRIEFS, WAREHOUSE, settings
from . import persistence
from .models import (
    Alert,
    AlertIn,
    Point,
    Pose,
    RobotDetail,
    RobotState,
    RobotSummary,
    SensorSnapshot,
    Task,
    TaskIn,
    TaskStatus,
    TelemetryIn,
    VendorBenchmark,
)


class RobotRuntime:
    """Mutable per-robot state — superset of the API RobotSummary."""

    def __init__(self, seed: dict):
        self.id: str = seed["id"]
        self.vendor: str = seed["vendor"]
        self.model: str = seed["model"]
        self.industry: str = seed["industry"]
        self.state: RobotState = RobotState.IDLE
        self.battery_pct: float = 100.0
        self.pose_external = Pose(x=0.0, y=0.0, theta=0.0)
        self.pose_internal = Pose(x=0.0, y=0.0, theta=0.0)
        self.drift_delta_m: float = 0.0
        self.current_task: Optional[str] = None
        self.error_code: Optional[str] = None
        self.halted_at: Optional[float] = None
        self.created_at: float = time.time()

        # Simulator internals (ignored when driven by real edge telemetry).
        self.trajectory: list[tuple[float, float]] = []
        self.traj_index: int = 0
        self.drift_bias: tuple[float, float] = (0.0, 0.0)
        self.spike_ticks: int = 0

        # Visual-nav: operator waypoints (map coords). When non-empty, the robot is driven
        # to them via Orbital's camera-based control, overriding the patrol/SLAM path.
        self.nav_queue: list[tuple[float, float]] = []

        # Operator drive controls: commanded speed and (optional) manual jog heading (rad).
        self.speed_mps: float = settings.base_speed_mps
        self.manual_heading: Optional[float] = None

        # Benchmark tracking.
        self.degradation_events: int = 0
        self._degraded_since: Optional[float] = None
        self.recovery_latencies: deque[float] = deque(maxlen=100)

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at)

    @property
    def control_mode(self) -> str:
        if self.state == RobotState.HALTED:
            return "halted"
        if self.state == RobotState.CHARGING:
            return "charging"
        if self.nav_queue:
            return "visual_nav"
        if self.manual_heading is not None:
            return "manual"
        if self.state == RobotState.IDLE:
            return "idle"
        return "patrol"

    def summary(self) -> RobotSummary:
        return RobotSummary(
            id=self.id,
            vendor=self.vendor,
            model=self.model,
            industry=self.industry,
            state=self.state,
            battery_pct=round(self.battery_pct, 1),
            pose_external=self.pose_external,
            pose_internal=self.pose_internal,
            drift_delta_m=round(self.drift_delta_m, 4),
            current_task=self.current_task,
            error_code=self.error_code,
            visual_nav=bool(self.nav_queue),
            nav_goal=Point(x=self.nav_queue[0][0], y=self.nav_queue[0][1]) if self.nav_queue else None,
            waypoints=[Point(x=x, y=y) for x, y in self.nav_queue],
            speed_mps=round(self.speed_mps, 2),
            manual_drive=self.manual_heading is not None,
            control_mode=self.control_mode,
        )


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.robots: dict[str, RobotRuntime] = {}
        self.telemetry: dict[str, deque[tuple[float, float]]] = {}
        self.sensors: dict[str, SensorSnapshot] = {}
        self.alerts: list[Alert] = []
        self.tasks: dict[str, Task] = {}
        self._seed()

    def _seed(self) -> None:
        for seed in SEED_FLEET:
            self.robots[seed["id"]] = RobotRuntime(seed)
            self.telemetry[seed["id"]] = deque(maxlen=5000)

    # ── Telemetry ingest (shared by simulator + POST /api/v1/telemetry) ──────────
    def ingest_telemetry(self, t: TelemetryIn) -> None:
        with self._lock:
            robot = self.robots.get(t.robot_id)
            ts = t.ts or time.time()
            self.telemetry.setdefault(t.robot_id, deque(maxlen=5000)).append((ts, t.delta_meters))

            # Capture the multi-modal snapshot (battery/motors/imu/spatial/temps) if present.
            if any([t.battery, t.motors, t.imu, t.spatial, t.temperatures_c, t.extra]):
                self.sensors[t.robot_id] = SensorSnapshot(
                    ts=ts,
                    battery=t.battery,
                    motors=list(t.motors),
                    imu=t.imu,
                    spatial=t.spatial,
                    temperatures_c=dict(t.temperatures_c),
                    extra=dict(t.extra),
                )

            if robot is None:
                return
            robot.drift_delta_m = t.delta_meters
            # A real edge can drive battery + floor pose straight from its sensor stream.
            if t.battery and t.battery.pct is not None:
                robot.battery_pct = t.battery.pct
            if t.spatial is not None:
                robot.pose_internal = Pose(x=t.spatial.x, y=t.spatial.y, theta=t.spatial.yaw)
            self._track_degradation(robot, t.delta_meters, ts)

    def latest_sensors(self, robot_id: str) -> Optional[SensorSnapshot]:
        with self._lock:
            return self.sensors.get(robot_id)

    def _track_degradation(self, robot: RobotRuntime, delta: float, ts: float) -> None:
        """MTBD/recovery bookkeeping: a degradation event starts when drift crosses
        the degraded threshold and ends (recording recovery latency) when it clears."""
        degraded = delta > settings.drift_degraded_m
        if degraded and robot._degraded_since is None:
            robot._degraded_since = ts
            robot.degradation_events += 1
        elif not degraded and robot._degraded_since is not None:
            robot.recovery_latencies.append(max(0.0, ts - robot._degraded_since))
            robot._degraded_since = None

    # ── Alerts ───────────────────────────────────────────────────────────────────
    def add_alert(self, a: AlertIn) -> Alert:
        with self._lock:
            alert = Alert(id=f"alr-{uuid.uuid4().hex[:10]}", ts=a.ts or time.time(), **a.model_dump(exclude={"ts"}))
            self.alerts.insert(0, alert)
            del self.alerts[200:]  # keep the last 200
            return alert

    def acknowledge_alert(self, alert_id: str) -> bool:
        with self._lock:
            for alert in self.alerts:
                if alert.id == alert_id:
                    alert.acknowledged = True
                    return True
            return False

    def recent_alerts(self, limit: int = 50) -> list[Alert]:
        with self._lock:
            return list(self.alerts[:limit])

    def unacknowledged_alerts(self) -> list[Alert]:
        with self._lock:
            return [a for a in self.alerts if not a.acknowledged]

    # ── Tasks ──────────────────────────────────────────────────────────────────
    def create_task(self, t: TaskIn) -> Task:
        with self._lock:
            task = Task(id=f"tsk-{uuid.uuid4().hex[:10]}", created_at=time.time(), **t.model_dump())
            self.tasks[task.id] = task
            robot = self.robots.get(t.robot_id)
            if robot is not None:
                task.status = TaskStatus.ACTIVE
                robot.current_task = t.description
                robot.state = RobotState.ACTIVE
                if t.waypoints:
                    robot.trajectory = [(w.x, w.y) for w in t.waypoints]
                    robot.traj_index = 0
            return task

    def active_tasks(self) -> list[Task]:
        with self._lock:
            return [t for t in self.tasks.values() if t.status in (TaskStatus.QUEUED, TaskStatus.ACTIVE)]

    # ── Control (monitor+control) ────────────────────────────────────────────────
    def estop(self, robot_id: str, *, auto: bool = False) -> bool:
        """Halt a robot. Operator E-Stops latch until a human resumes; orchestrator/auto stops
        use a distinct code the simulator re-converges from after a cooldown."""
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return False
            robot.state = RobotState.HALTED
            robot.error_code = "AUTO_ESTOP" if auto else "E_STOP"
            robot.halted_at = time.time()
            return True

    def resume(self, robot_id: str) -> bool:
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return False
            robot.state = RobotState.ACTIVE if robot.current_task else RobotState.IDLE
            robot.error_code = None
            robot.halted_at = None
            robot.drift_bias = (0.0, 0.0)  # ARIA re-converges on resume
            return True

    # ── Visual-nav waypoints ─────────────────────────────────────────────────────
    def set_speed(self, robot_id: str, speed_mps: float) -> Optional[float]:
        """Set a robot's commanded speed (m/s), clamped to [0.05, max]. Returns the applied
        value, or None for unknown robots."""
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return None
            robot.speed_mps = min(max(float(speed_mps), 0.05), settings.max_speed_mps)
            return round(robot.speed_mps, 2)

    def set_manual_drive(self, robot_id: str, heading_rad: float, speed_mps: Optional[float] = None) -> bool:
        """Jog a robot along a fixed heading via visual control. Clears any waypoint queue,
        wakes it to ACTIVE, and (optionally) updates speed. Refuses halted robots."""
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None or robot.state == RobotState.HALTED:
                return False
            robot.nav_queue = []
            persistence.save_waypoints(robot_id, [])
            robot.manual_heading = float(heading_rad)
            if speed_mps is not None:
                robot.speed_mps = min(max(float(speed_mps), 0.05), settings.max_speed_mps)
            robot.state = RobotState.ACTIVE
            robot.current_task = "Manual drive (operator)"
            return True

    def stop_manual_drive(self, robot_id: str) -> bool:
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return False
            robot.manual_heading = None
            if robot.current_task == "Manual drive (operator)":
                robot.current_task = "Autonomous patrol"
            return True

    def set_waypoints(self, robot_id: str, points: list[tuple[float, float]], persist: bool = True) -> bool:
        """Queue operator waypoints and put the robot into visual-nav (active) state.
        Coordinates are clamped to the warehouse bounds. Returns False for unknown robots."""
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return False
            w = float(WAREHOUSE["width_m"]); h = float(WAREHOUSE["height_m"])
            clamped = [(min(max(x, 0.0), w), min(max(y, 0.0), h)) for x, y in points]
            robot.nav_queue = clamped
            robot.manual_heading = None  # waypoints take over from a manual jog
            if clamped and robot.state in (RobotState.IDLE, RobotState.ACTIVE):
                robot.state = RobotState.ACTIVE
                robot.current_task = "Visual waypoint nav (SLAM bypass)"
            if persist:
                persistence.save_waypoints(robot_id, [[x, y] for x, y in clamped])
            return True

    def clear_waypoints(self, robot_id: str, persist: bool = True) -> bool:
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return False
            robot.nav_queue = []
            if robot.current_task and "Visual waypoint nav" in robot.current_task:
                robot.current_task = "Autonomous patrol"
            if persist:
                persistence.save_waypoints(robot_id, [])
            return True

    def restore_waypoints(self) -> None:
        """Re-apply operator waypoints saved before a restart (no re-persist)."""
        for robot_id, pts in persistence.load_waypoints().items():
            self.set_waypoints(robot_id, [(float(x), float(y)) for x, y in pts], persist=False)

    def dispatch_charge(self, robot_id: str) -> bool:
        """Send a robot to charge. Idempotent; refuses halted robots (operator owns those)."""
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None or robot.state == RobotState.HALTED:
                return False
            robot.state = RobotState.CHARGING
            return True

    # ── Reads ────────────────────────────────────────────────────────────────────
    def fleet(self) -> list[RobotSummary]:
        with self._lock:
            return [r.summary() for r in self.robots.values()]

    def robot_detail(self, robot_id: str) -> Optional[RobotDetail]:
        with self._lock:
            robot = self.robots.get(robot_id)
            if robot is None:
                return None
            deltas = [d for _, d in self.telemetry.get(robot_id, ())]
            return RobotDetail(
                **robot.summary().model_dump(),
                facility_id=settings.facility_id,
                mtbd_seconds=self._mtbd(robot),
                recovery_latency_seconds=_mean(list(robot.recovery_latencies)),
                env_degradation_score=self._env_score(deltas),
                oem_brief=VENDOR_BRIEFS.get(robot.vendor, ""),
                uptime_seconds=round(robot.uptime_seconds, 1),
                sensors=self.sensors.get(robot_id),
                control=self._control_grants(robot.vendor),
            )

    @staticmethod
    def _control_grants(vendor: str) -> "ControlGrants":
        # Lazy import avoids a circular import (scope_guard imports store).
        from . import scope_guard
        from .models import APIScope, ControlGrants
        return ControlGrants(
            managed=scope_guard.check_vendor(vendor, APIScope.ESTOP).managed,
            estop=scope_guard.check_vendor(vendor, APIScope.ESTOP).allowed,
            velocity=scope_guard.check_vendor(vendor, APIScope.VELOCITY).allowed,
            mission=scope_guard.check_vendor(vendor, APIScope.MISSION).allowed,
        )

    def benchmark(self, vendor: str) -> VendorBenchmark:
        with self._lock:
            robots = [r for r in self.robots.values() if r.vendor.lower() == vendor.lower()]
            deltas: list[float] = []
            for r in robots:
                deltas.extend(d for _, d in self.telemetry.get(r.id, ()))
            total_uptime = sum(r.uptime_seconds for r in robots)
            total_events = sum(r.degradation_events for r in robots)
            recoveries: list[float] = []
            for r in robots:
                recoveries.extend(r.recovery_latencies)
            return VendorBenchmark(
                vendor=vendor,
                samples=len(deltas),
                mean_drift_m=round(_mean(deltas) or 0.0, 4),
                p95_drift_m=round(_p95(deltas), 4),
                mtbd_seconds=round(total_uptime / total_events, 1) if total_events else None,
                mean_recovery_latency_seconds=round(_mean(recoveries), 2) if recoveries else None,
                env_degradation_score=self._env_score(deltas),
                robots=len(robots),
            )

    def vendors(self) -> list[str]:
        with self._lock:
            seen: list[str] = []
            for r in self.robots.values():
                if r.vendor not in seen:
                    seen.append(r.vendor)
            return seen

    def _mtbd(self, robot: RobotRuntime) -> Optional[float]:
        return round(robot.uptime_seconds / robot.degradation_events, 1) if robot.degradation_events else None

    def _env_score(self, deltas: list[float]) -> Optional[float]:
        m = _mean(deltas)
        if m is None:
            return None
        baseline = settings.drift_degraded_m or 0.1
        return round(m / baseline, 3)


def _mean(values: list[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered)) - 1))
    return ordered[idx]


# Process-wide singleton.
store = Store()
