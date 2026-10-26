"""API + domain models for the Orbital AI Cloud.

Pydantic models double as the API contract (Module 6 & 7) and the shared schema
that StageGate (TS) and ReadyForRobots (Py) clients code against.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RobotState(str, Enum):
    ACTIVE = "active"            # executing a task under ARIA correction
    IDLE = "idle"               # connected, no task
    CHARGING = "charging"
    COOLDOWN = "cooldown"       # task complete — paused between tasks (shown red)
    HALTED = "halted"           # safety E-Stop engaged
    OFFLINE = "offline"


class AlertType(str, Enum):
    DRIFT_EXCEEDED = "drift_exceeded"        # delta > halt threshold
    HIJACK_SUSPECTED = "hijack_suspected"    # cameras see motion, robot reports still
    GHOST_COMMAND = "ghost_command"          # robot reports motion, cameras see still
    LOW_BATTERY = "low_battery"
    HUMAN_PROXIMITY = "human_proximity"      # a person inside the robot's stop radius


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


class ZoneType(str, Enum):
    KEEPOUT = "keepout"   # robots never route into it
    SLOW = "slow"         # robots inside are capped at speed_cap_mps


class ZoneIn(BaseModel):
    """A floor zone drawn on the Global Spatial Map. ``vendors`` limits which fleets it
    applies to; empty means every robot."""
    name: str = Field(min_length=1, max_length=60)
    type: ZoneType
    polygon: list[list[float]] = Field(min_length=3, max_length=64)
    speed_cap_mps: Optional[float] = Field(default=None, gt=0, le=5)
    vendors: list[str] = Field(default_factory=list)

    @field_validator("polygon")
    @classmethod
    def _points_are_xy(cls, polygon: list[list[float]]) -> list[list[float]]:
        if any(len(p) != 2 for p in polygon):
            raise ValueError("each polygon point must be [x, y]")
        return polygon


class Zone(ZoneIn):
    id: str
    created_at: float
    actor: Optional[str] = None


class RunSample(BaseModel):
    """One time step of a recorded run. ``t`` is seconds since the run started; x/y/theta is the
    camera ground truth, ix/iy/itheta the robot's own (SLAM) estimate."""
    t: float = Field(ge=0)
    x: float
    y: float
    theta: float = 0.0
    ix: Optional[float] = None
    iy: Optional[float] = None
    itheta: Optional[float] = None
    drift_m: float = Field(default=0.0, ge=0)
    speed_mps: float = Field(default=0.0, ge=0)
    state: str = Field(default="active", max_length=20)


class RunOutcome(str, Enum):
    COMPLETE = "complete"
    ABORTED = "aborted"      # pre-empted (new mission sequence, operator override, shutdown)
    FAILED = "failed"


class RunIn(BaseModel):
    """A recorded task run: one robot doing one named task (e.g. ``Aisle AB → Dock``), as
    uploaded by an edge or produced by the recorder."""
    robot_id: str = Field(min_length=1, max_length=64)
    vendor: str = Field(default="", max_length=40)
    task_key: str = Field(min_length=1, max_length=120)
    started_at: float
    ended_at: float
    outcome: RunOutcome = RunOutcome.COMPLETE
    samples: list[RunSample] = Field(min_length=2, max_length=5000)
    source: str = Field(default="edge", max_length=40)

    @field_validator("samples")
    @classmethod
    def _time_ordered(cls, samples: list[RunSample]) -> list[RunSample]:
        if any(b.t < a.t for a, b in zip(samples, samples[1:])):
            raise ValueError("samples must be in time order")
        return samples


class Run(RunIn):
    id: str
    facility_id: str


class RunScore(BaseModel):
    """How a run compares with the golden run for the same task and vendor."""
    verdict: str                        # pass | slow | deviated | unstable | failed | aborted | no_golden | golden
    reasons: list[str] = Field(default_factory=list)
    golden_id: Optional[str] = None
    cycle_time_s: float
    golden_cycle_time_s: Optional[float] = None
    cycle_ratio: Optional[float] = None
    mean_dev_m: Optional[float] = None
    max_dev_m: Optional[float] = None
    recoveries: int = 0
    golden_recoveries: Optional[int] = None
    max_drift_m: float = 0.0


class RunSummary(BaseModel):
    id: str
    robot_id: str
    vendor: str
    task_key: str
    started_at: float
    ended_at: float
    outcome: RunOutcome
    source: str
    samples: int
    golden: bool
    score: RunScore


class ObjectSize(BaseModel):
    """Bounding box in metres: length along the object's yaw, width across it, height up."""
    length: float = Field(default=0.5, gt=0, le=50)
    width: float = Field(default=0.5, gt=0, le=50)
    height: float = Field(default=0.0, ge=0, le=20)


class TrackedObjectIn(BaseModel):
    """One object seen by the camera rig: what it is, where, which way it faces and how it is
    moving. Published by the edge (or a partner's perception stack) every frame or so; an
    object that stops being reported expires after ``ttl_s``."""
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    label: str = Field(min_length=1, max_length=40)
    x: float = Field(ge=-1000, le=1000)
    y: float = Field(ge=-1000, le=1000)
    z: float = 0.0
    yaw: float = 0.0
    vx: float = Field(default=0.0, ge=-20, le=20)
    vy: float = Field(default=0.0, ge=-20, le=20)
    size: ObjectSize = Field(default_factory=ObjectSize)
    confidence: float = Field(default=1.0, ge=0, le=1)
    ttl_s: Optional[float] = Field(default=None, gt=0, le=300)


class ObjectsIn(BaseModel):
    """A batch of objects from one source. With ``replace``, objects from this source that are
    not in the batch are dropped at once instead of waiting to expire (a full-frame snapshot)."""
    source: str = Field(default="edge", pattern=r"^[A-Za-z0-9_.:-]{1,40}$")
    objects: list[TrackedObjectIn] = Field(default_factory=list, max_length=200)
    replace: bool = False


class TrackedObject(TrackedObjectIn):
    source: str
    facility_id: str
    first_seen: float
    updated_at: float
    expires_at: float


class MarkerObservationIn(BaseModel):
    """One floor marker as seen by one capture, in that capture's own frame (metres, radians)."""
    marker_id: int = Field(ge=0, le=100_000)
    x: float = Field(ge=-1000, le=1000)
    y: float = Field(ge=-1000, le=1000)
    theta: float = 0.0


class CalibrationCaptureIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    expected_pose: Optional[Pose] = None
    observations: list[MarkerObservationIn] = Field(min_length=1, max_length=40)


class MarkerAnchorIn(BaseModel):
    """A tape-measured floor position for a marker."""
    marker_id: int = Field(ge=0, le=100_000)
    x: float
    y: float


class CalibrationIn(BaseModel):
    """Rig calibration captures (Sprint H). Bounded so one upload can't tie up the solver."""
    origin_marker: int = 0
    origin_pose: Optional[Pose] = None
    anchors: list[MarkerAnchorIn] = Field(default_factory=list, max_length=20)
    threshold_m: Optional[float] = Field(default=None, gt=0, le=1.0)
    captures: list[CalibrationCaptureIn] = Field(min_length=1, max_length=60)

    @field_validator("captures")
    @classmethod
    def _bounded_marker_set(cls, captures: list[CalibrationCaptureIn]) -> list[CalibrationCaptureIn]:
        if len({o.marker_id for c in captures for o in c.observations}) > 150:
            raise ValueError("at most 150 distinct markers per calibration")
        if len({c.id for c in captures}) != len(captures):
            raise ValueError("capture ids must be unique")
        return captures


class WorkerStatus(str, Enum):
    ON_SHIFT = "on_shift"
    BREAK = "break"        # visible on the map, never assigned work


class WorkerPoseIn(BaseModel):
    """A person's position on the floor: a phone check-in, a badge reader, or the Digital
    Net's multi-view people detection. ``display_name`` is optional — workers are
    pseudonymous by default."""
    x: float
    y: float
    display_name: Optional[str] = Field(default=None, max_length=60)
    role: str = Field(default="associate", max_length=40)
    status: WorkerStatus = WorkerStatus.ON_SHIFT


class Worker(BaseModel):
    id: str
    display_name: Optional[str] = None
    role: str = "associate"
    status: WorkerStatus = WorkerStatus.ON_SHIFT
    pose: Point
    last_seen: float
    source: str = "checkin"    # checkin | sim


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
    # Mission narration: the fleet-sequence goal this robot is executing ("Move tote:
    # Aisle AB → Dock") and which leg it's on (en_route_pickup | working | carrying | idle).
    mission: Optional[str] = None
    mission_phase: Optional[str] = None
    # Fleet sim role: lead robots publish relay waypoints; shuttles run between them.
    fleet_role: Optional[str] = None  # "lead" | "shuttle" | None
    error_code: Optional[str] = None
    # Autonomous task cycle: the robot this one just handed its payload off to (drawn as a
    # transient link on the map during the cooldown pause). None outside a hand-off.
    handoff_partner: Optional[str] = None
    # Visual-nav state (operator waypoints set on the map; drives external pose).
    visual_nav: bool = False
    nav_goal: Optional[Point] = None
    waypoints: list[Point] = Field(default_factory=list)
    # Camera-planned route the robot is actually following right now — bends around racks
    # so the drawn path matches the motion (obstacle-aware, not straight-line).
    path: list[Point] = Field(default_factory=list)
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
    actor: Optional[str] = None


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


# ── Next-best-action queue (orchestrator Policy 4 + operator accept/dismiss) ──

class RecommendationKind(str, Enum):
    RESUME = "resume"                          # operator-latched halt whose drift has re-converged
    HANDOVER = "handover"                      # move a halted robot's task to an idle peer
    PRECHARGE = "precharge"                    # charge before the battery hits the auto-dispatch floor
    RELIABILITY_REVIEW = "reliability_review"  # repeated degradation events — needs a human look
    DISPATCH_TASK = "dispatch_task"            # agent-proposed new task for a robot
    ASSIST = "assist"                          # send a person on the floor to a robot


class RecommendationStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"    # operator took it on; nothing for Orbital to execute
    DONE = "done"            # Orbital executed it
    DISMISSED = "dismissed"
    EXPIRED = "expired"      # TTL passed or the triggering condition cleared


class AssigneeType(str, Enum):
    ROBOT = "robot"
    PERSON = "person"
    OPERATOR = "operator"


class Recommendation(BaseModel):
    id: str
    kind: RecommendationKind
    robot_id: Optional[str] = None
    target_robot_id: Optional[str] = None   # robot that executes it, when not robot_id (handover)
    task: Optional[str] = None
    score: float
    rationale: str
    assignee_type: AssigneeType = AssigneeType.ROBOT
    assignee_id: Optional[str] = None       # the worker, when assignee_type is person
    scope: Optional[APIScope] = None        # OEM scope needed on the executing robot
    status: RecommendationStatus = RecommendationStatus.PROPOSED
    source: str = "policy"                  # policy | agent
    actor: Optional[str] = None
    created_at: float
    updated_at: float
    expires_at: Optional[float] = None
    blocked_reason: Optional[str] = None    # filled at read time when the scope isn't granted


class ActionProposalIn(BaseModel):
    """POST /api/dashboard/actions — an agent (or operator) proposes a next action."""
    kind: RecommendationKind
    robot_id: str
    rationale: str = Field(min_length=1, max_length=500)
    target_robot_id: Optional[str] = None   # required for handover
    task: Optional[str] = Field(default=None, max_length=200)  # required for handover / dispatch_task
    assignee_id: Optional[str] = None       # assist: the worker to send; nearest on shift if omitted
    score: float = Field(default=50.0, ge=0.0, le=100.0)
    dry_run: bool = False


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


class OEMPolicies(BaseModel):
    """Operator guardrails captured at onboarding — governance bounds Orbital honours for
    this partner's fleet on top of raw scope grants."""
    max_speed_mps: float = 1.5          # ceiling the operator will command for this OEM
    drift_halt_threshold_m: float = 0.5  # safety-halt trip point
    auto_estop_on_critical: bool = True  # let the orchestrator auto-halt on critical drift
    require_approval_for_teleop: bool = True  # teleop needs an explicit operator hand-on
    geofence: str = "facility"           # allowed operating zone label


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
    policies: OEMPolicies = Field(default_factory=OEMPolicies)
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


class OEMOnboardIn(OEMRegisterIn):
    """Wizard payload: register a new robot-API partner and, in one shot, unlock the API
    scopes (permissions) and set the governance policies."""
    scopes: list[APIScope] = Field(default_factory=list)
    policies: Optional[OEMPolicies] = None


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
    policies: OEMPolicies = Field(default_factory=OEMPolicies)
    control_ready: bool   # can we run the TF-Hijack correction (velocity + estop granted)?
    monitor_ready: bool   # can we at least monitor (telemetry granted)?


# RobotDetail forward-references SensorSnapshot (defined above but after RobotDetail);
# rebuild so the reference resolves.
RobotDetail.model_rebuild()
