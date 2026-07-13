"""API + domain models for the Orbital AI Cloud.

Pydantic models double as the API contract (Module 6 & 7) and the shared schema
that StageGate (TS) and ReadyForRobots (Py) clients code against.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RobotState(str, Enum):
    ACTIVE = "active"            # executing a task under ARIA correction
    IDLE = "idle"               # connected, no task
    CHARGING = "charging"
    HALTED = "halted"           # safety E-Stop engaged
    OFFLINE = "offline"


class AlertType(str, Enum):
    DRIFT_EXCEEDED = "drift_exceeded"        # delta > halt threshold
    HIJACK_SUSPECTED = "hijack_suspected"    # cameras see motion, robot reports still
    GHOST_COMMAND = "ghost_command"          # robot reports motion, cameras see still
    LOW_BATTERY = "low_battery"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class Pose(BaseModel):
    x: float
    y: float
    theta: float = 0.0


class Point(BaseModel):
    x: float
    y: float


class NavigateIn(BaseModel):
    """Operator waypoint order: drive the robot through these points via visual control
    (Orbital's cameras localize + steer), bypassing the robot's onboard SLAM."""
    waypoints: list[Point]


class SpeedIn(BaseModel):
    """Operator speed override (m/s) applied to all of a robot's motion."""
    speed_mps: float


class DriveIn(BaseModel):
    """Manual jog: drive the robot along a heading (degrees, 0 = +x / east, CCW) at an
    optional speed. Overrides patrol and clears any waypoint queue until stopped."""
    heading_deg: float
    speed_mps: Optional[float] = None


class RobotSummary(BaseModel):
    """The 2s-refresh fleet payload (GET /api/dashboard/fleet)."""
    id: str
    vendor: str
    model: str
    industry: str
    state: RobotState
    battery_pct: float
    pose_external: Pose            # ARIA ground truth (overhead cameras)
    pose_internal: Pose            # robot self-report
    drift_delta_m: float           # euclidean(external, internal)
    current_task: Optional[str] = None
    error_code: Optional[str] = None
    # Visual-nav state (operator waypoints set on the map; drives external pose).
    visual_nav: bool = False
    nav_goal: Optional[Point] = None
    waypoints: list[Point] = Field(default_factory=list)
    # Operator drive controls.
    speed_mps: float = 0.6         # current commanded speed
    manual_drive: bool = False     # operator is jogging it along a fixed heading
    control_mode: str = "patrol"   # patrol | visual_nav | manual | charging | halted | idle


class ControlGrants(BaseModel):
    """Whether the robot's owning OEM has granted each control action — lets the UI show
    scope-aware controls (disable + explain) instead of firing a command that 403s."""
    managed: bool = False   # is an OEM registered for this vendor?
    estop: bool = True
    velocity: bool = True
    mission: bool = True


class RobotDetail(RobotSummary):
    """The "business card" panel (GET /api/dashboard/robot/{id})."""
    facility_id: str
    mtbd_seconds: Optional[float] = None
    recovery_latency_seconds: Optional[float] = None
    env_degradation_score: Optional[float] = None
    oem_brief: str = ""
    uptime_seconds: float = 0.0
    sensors: Optional["SensorSnapshot"] = None
    control: ControlGrants = Field(default_factory=ControlGrants)


class BatteryTelemetry(BaseModel):
    """Battery pack health — temperature is a leading indicator of thermal faults."""
    pct: Optional[float] = None
    temperature_c: Optional[float] = None
    voltage_v: Optional[float] = None
    current_a: Optional[float] = None
    cycles: Optional[int] = None


class MotorTelemetry(BaseModel):
    """Per-joint/actuator input — current + temperature reveal binds, stalls, overheating."""
    joint: str
    temperature_c: Optional[float] = None
    current_a: Optional[float] = None
    torque_nm: Optional[float] = None
    position_rad: Optional[float] = None
    velocity_rad_s: Optional[float] = None


class ImuTelemetry(BaseModel):
    """Inertial sensor input (3-axis accel m/s^2, 3-axis gyro rad/s)."""
    accel: list[float] = Field(default_factory=list)
    gyro: list[float] = Field(default_factory=list)


class SpatialTelemetry(BaseModel):
    """Full 6-DoF spatial positioning (extends the 2D floor pose with z + orientation)."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    linear_velocity_mps: Optional[float] = None
    angular_velocity_rps: Optional[float] = None


class SensorSnapshot(BaseModel):
    """Latest multi-modal reading for a robot (surfaced in RobotDetail + /sensors)."""
    ts: Optional[float] = None
    battery: Optional[BatteryTelemetry] = None
    motors: list[MotorTelemetry] = Field(default_factory=list)
    imu: Optional[ImuTelemetry] = None
    spatial: Optional[SpatialTelemetry] = None
    temperatures_c: dict[str, float] = Field(default_factory=dict)  # e.g. {"cpu": 61.2, "ambient": 24.0}
    extra: dict[str, float] = Field(default_factory=dict)           # vendor-specific scalars


class TelemetryIn(BaseModel):
    """Edge -> cloud telemetry (POST /api/v1/telemetry).

    ``delta_meters`` (ARIA drift) is the one required signal; every other channel —
    battery, motors, IMU, spatial pose, temperatures — is optional so a minimal edge can
    still report drift while a full ARIA node streams the complete sensor/motor picture.
    """
    robot_id: str
    vendor: str
    model: str
    facility_id: str
    delta_meters: float
    ts: Optional[float] = None      # epoch seconds; server-stamped if omitted
    # Rich multi-modal channels (all optional).
    battery: Optional[BatteryTelemetry] = None
    motors: list[MotorTelemetry] = Field(default_factory=list)
    imu: Optional[ImuTelemetry] = None
    spatial: Optional[SpatialTelemetry] = None
    temperatures_c: dict[str, float] = Field(default_factory=dict)
    extra: dict[str, float] = Field(default_factory=dict)


class AlertIn(BaseModel):
    """Edge -> cloud safety-halt / anomaly event (POST /api/v1/alerts)."""
    robot_id: str
    type: AlertType
    severity: AlertSeverity = AlertSeverity.CRITICAL
    delta_meters: Optional[float] = None
    message: str = ""
    ts: Optional[float] = None


class Alert(AlertIn):
    id: str
    acknowledged: bool = False


class Waypoint(BaseModel):
    x: float
    y: float


class TaskIn(BaseModel):
    robot_id: str
    description: str
    waypoints: list[Waypoint] = Field(default_factory=list)


class Task(TaskIn):
    id: str
    status: TaskStatus = TaskStatus.QUEUED
    created_at: float


class VendorBenchmark(BaseModel):
    """Benchmark Library rollup per OEM (GET /api/dashboard/benchmark/{vendor})."""
    vendor: str
    samples: int
    mean_drift_m: float
    p95_drift_m: float
    mtbd_seconds: Optional[float] = None
    mean_recovery_latency_seconds: Optional[float] = None
    env_degradation_score: Optional[float] = None
    robots: int


# ── Orchestrator (the autonomous supervisory brain) ───────────────────────────

class OrchestratorAction(str, Enum):
    AUTO_ESTOP = "auto_estop"            # safety halt executed on a critical anomaly
    DISPATCH_CHARGE = "dispatch_charge"  # proactively sent a low-battery robot to charge
    RECOMMEND_REVIEW = "recommend_review"  # advisory: operator should look (no action taken)
    MONITOR = "monitor"                  # nominal — logged, no action


class OrchestratorDecision(BaseModel):
    id: str
    ts: float
    robot_id: Optional[str] = None
    action: OrchestratorAction
    severity: AlertSeverity = AlertSeverity.INFO
    rationale: str = ""
    auto_executed: bool = False


class FleetSummary(BaseModel):
    total: int
    active: int
    idle: int
    charging: int
    halted: int
    offline: int
    unacked_alerts: int
    worst_drift_robot: Optional[str] = None
    worst_drift_m: float = 0.0


class OrchestratorStatus(BaseModel):
    """GET /api/dashboard/orchestrator — the autonomy layer's current view."""
    enabled: bool
    llm_enabled: bool
    last_run_ts: Optional[float] = None
    summary: Optional[FleetSummary] = None
    narrative: str = ""
    decisions: list[OrchestratorDecision] = Field(default_factory=list)


# ── OEM onboarding (3rd-party robot companies unlock parts of their API to us) ─

class APIScope(str, Enum):
    """A slice of an OEM's robot API that they grant Orbital access to.

    Kept 1:1 with ``fleet_adapters.base.Capability`` — an OEM can only grant scopes their
    protocol actually supports (the adapter capability ceiling).
    """
    TELEMETRY = "telemetry.read"
    STATE = "state.read"
    VELOCITY = "control.velocity"
    ESTOP = "control.estop"
    TELEOP = "control.teleop"
    MISSION = "mission.dispatch"
    CAMERA = "camera.read"
    MAP = "map.read"


class OEMStatus(str, Enum):
    PENDING = "pending"      # registered, no scopes granted yet
    ACTIVE = "active"        # at least one scope granted
    SUSPENDED = "suspended"  # access paused by an operator


class ControlTransport(str, Enum):
    ROS2 = "ros2"
    GRPC = "grpc"
    CLOUD_REST = "cloud_rest"
    UDP = "udp"


class OEMRegisterIn(BaseModel):
    company_name: str
    vendor: str                         # OEM/vendor key (ideally a known fleet-adapter vendor)
    contact_email: str
    transport: ControlTransport = ControlTransport.ROS2
    website: Optional[str] = None


class OEMPartner(BaseModel):
    """Public OEM record (never carries the raw API key)."""
    id: str
    company_name: str
    vendor: str
    contact_email: str
    transport: ControlTransport
    status: OEMStatus
    ceiling_scopes: list[APIScope] = Field(default_factory=list)
    granted_scopes: list[APIScope] = Field(default_factory=list)
    api_key_prefix: str = ""
    created_at: float
    updated_at: float


class OEMCredential(BaseModel):
    """Returned exactly once, at registration — the OEM stores it; we keep only a hash."""
    api_key: str
    key_prefix: str


class OEMRegistered(BaseModel):
    partner: OEMPartner
    credential: OEMCredential


class ScopeGrantIn(BaseModel):
    scopes: list[APIScope]


class IntegrationProfile(BaseModel):
    """What Orbital can actually do with this OEM's fleet right now."""
    oem_id: str
    company_name: str
    vendor: str
    transport: ControlTransport
    status: OEMStatus
    ceiling_scopes: list[APIScope]
    granted_scopes: list[APIScope]
    missing_scopes: list[APIScope]
    control_ready: bool   # can we run the TF-Hijack correction (velocity + estop granted)?
    monitor_ready: bool   # can we at least monitor (telemetry granted)?


# RobotDetail forward-references SensorSnapshot (defined above but after RobotDetail);
# rebuild so the reference resolves.
RobotDetail.model_rebuild()
