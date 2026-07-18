"""ARIA Edge Node agent — wires Modules 1/2/4 together and syncs the Orbital AI Cloud.

Per tick it:
  1. localizes every robot from the camera rig (Module 1 CV pipeline -> external pose),
  2. reads each robot's self-reported pose via its fleet adapter (Module 3),
  3. publishes poses to the shared ``PoseBus``,
  4. runs the Safety Halt watchdog against the bus only (Module 4 — independent of inject),
  5. on a halt: commands the adapter E-Stop and raises a cloud alert,
     otherwise: if drift is degraded, runs Module 2 (T_delta + lookahead + inject_waypoint)
     or falls back to cmd_vel when no trajectory is loaded,
  6. posts drift telemetry to the cloud (feeds Benchmark Library + dashboard).

This is the on-prem counterpart to ``orbital_cloud.simulator``: same cloud ingest path
(`POST /api/v1/telemetry`, `POST /api/v1/alerts`), so the cloud can't tell a real edge from
the simulator. ``CloudSync`` runs in record-only mode when no cloud URL is reachable, which
also makes the whole loop unit-testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import edge_settings
from .cv_pipeline import PoseSource, SimulatedCVPipeline
from .pose_bus import PoseBus, SafetyWatchdog
from .safety_halt import SafetyHaltController
from .types import CameraFrame, DriftEstimate, HaltDecision, Pose2D
from .waypoint_generator import MicroWaypointGenerator, WaypointInjector

logger = logging.getLogger(__name__)


@dataclass
class RobotBinding:
    """What the edge knows about one robot: identity, how to command it, and which
    control scopes its OEM has granted (mirrors the cloud enforcement locally)."""
    robot_id: str
    vendor: str
    model: str
    adapter: Any = None  # a fleet_adapters.FleetAdapter (optional in the scaffold)
    # OEM scope enforcement: `managed` True means an OEM governs this vendor, so only
    # `granted_scopes` may be exercised. False (unmanaged) is permissive, matching the cloud.
    managed: bool = False
    granted_scopes: set[str] = field(default_factory=set)
    last_external: Optional[Pose2D] = None
    last_internal: Optional[Pose2D] = None

    def scope_ok(self, scope: str) -> bool:
        return (not self.managed) or (scope in self.granted_scopes)


@dataclass
class CloudSync:
    """Posts telemetry/alerts and pulls missions/trajectories from the Orbital AI Cloud."""
    base_url: str = edge_settings.cloud_url
    api_key: str = edge_settings.cloud_api_key
    enabled: bool = False  # opt in explicitly; tests + offline edges stay record-only
    telemetry_sent: list[dict] = field(default_factory=list)
    alerts_sent: list[dict] = field(default_factory=list)
    # Record-only cache for pull APIs (also filled when enabled + HTTP succeeds).
    trajectories: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    missions: list[dict] = field(default_factory=list)

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def post_telemetry(self, payload: dict) -> None:
        self.telemetry_sent.append(payload)
        if not self.enabled:
            return
        self._post("/api/v1/telemetry", payload)

    def post_alert(self, payload: dict) -> None:
        self.alerts_sent.append(payload)
        if not self.enabled:
            return
        self._post("/api/v1/alerts", payload)

    def seed_trajectory(self, robot_id: str, waypoints: list[tuple[float, float]]) -> None:
        """Test / offline helper: preload a trajectory without hitting the cloud."""
        self.trajectories[robot_id] = list(waypoints)

    def pull_trajectory(self, robot_id: str) -> list[tuple[float, float]]:
        """GET /api/v1/trajectory/{robot_id}. Returns cached waypoints when offline."""
        if self.enabled:
            data = self._get(f"/api/v1/trajectory/{robot_id}")
            if data is not None:
                wps = data.get("waypoints") or []
                parsed = [(float(w["x"]), float(w["y"])) for w in wps if "x" in w and "y" in w]
                self.trajectories[robot_id] = parsed
        return list(self.trajectories.get(robot_id) or [])

    def pull_missions(self, facility_id: str | None = None) -> list[dict]:
        """GET /api/v1/missions/{facility}. Returns cached list when offline."""
        fid = facility_id or edge_settings.facility_id
        if self.enabled:
            data = self._get(f"/api/v1/missions/{fid}")
            if data is not None:
                self.missions = list(data.get("missions") or [])
        return list(self.missions)

    def _post(self, path: str, payload: dict) -> None:
        try:
            import httpx

            httpx.post(f"{self.base_url}{path}", json=payload, headers=self._headers(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001 — cloud sync is best-effort, never blocks control
            logger.warning("[edge] cloud sync %s failed: %s", path, exc)

    def _get(self, path: str) -> dict | None:
        try:
            import httpx

            resp = httpx.get(f"{self.base_url}{path}", headers=self._headers(), timeout=5.0)
            if resp.status_code >= 400:
                logger.warning("[edge] cloud GET %s -> %s", path, resp.status_code)
                return None
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[edge] cloud GET %s failed: %s", path, exc)
            return None


class EdgeAgent:
    def __init__(
        self,
        bindings: list[RobotBinding],
        *,
        cv: PoseSource | None = None,
        safety: SafetyHaltController | None = None,
        pose_bus: PoseBus | None = None,
        watchdog: SafetyWatchdog | None = None,
        injector: WaypointInjector | None = None,
        velocity: MicroWaypointGenerator | None = None,
        waypoints: MicroWaypointGenerator | None = None,  # back-compat alias for velocity
        cloud: CloudSync | None = None,
    ) -> None:
        self.bindings = {b.robot_id: b for b in bindings}
        self.cv = cv or SimulatedCVPipeline()
        self.safety = safety or SafetyHaltController()
        self.pose_bus = pose_bus or PoseBus()
        self.injector = injector or WaypointInjector()
        self.velocity = velocity or waypoints or MicroWaypointGenerator()
        self.cloud = cloud or CloudSync()
        self.watchdog = watchdog or SafetyWatchdog(self.pose_bus, self.safety)

    def set_trajectory(self, robot_id: str, waypoints: list[tuple[float, float]]) -> None:
        self.injector.set_trajectory(robot_id, waypoints)
        self.cloud.seed_trajectory(robot_id, waypoints)

    def tick(self, frame: CameraFrame, internal_poses: dict[str, Pose2D]) -> list[dict]:
        """One control cycle across all bound robots. Returns a per-robot result summary."""
        # Sprint A3: refresh missions + per-robot trajectories each tick (cache when offline).
        self.cloud.pull_missions()
        for rid in self.bindings:
            traj = self.cloud.pull_trajectory(rid)
            if traj and not self.injector.has_trajectory(rid):
                self.injector.set_trajectory(rid, traj)
            elif traj:
                current = self.injector._trajectories.get(rid)
                if current != traj:
                    self.injector.set_trajectory(rid, traj)

        detections = {d.robot_id: d for d in self.cv.process_frame(frame)}

        # Publish all observations to the pose bus *before* inject — watchdog never
        # depends on Module 2 completing.
        observed: dict[str, tuple[Pose2D, Pose2D, float]] = {}
        for rid, binding in self.bindings.items():
            det = detections.get(rid)
            internal = internal_poses.get(rid)
            if det is None or internal is None:
                continue
            external = det.world_pose
            drift = DriftEstimate(robot_id=rid, external=external, internal=internal)
            external_moved = external.distance_to(binding.last_external) if binding.last_external else 0.0
            internal_moved = internal.distance_to(binding.last_internal) if binding.last_internal else 0.0
            binding.last_external, binding.last_internal = external, internal
            self.pose_bus.publish(
                rid, external, internal,
                external_moved_m=external_moved, internal_moved_m=internal_moved,
            )
            self.cloud.post_telemetry({
                "robot_id": rid, "vendor": binding.vendor, "model": binding.model,
                "facility_id": edge_settings.facility_id, "delta_meters": round(drift.delta_m, 4),
            })
            observed[rid] = (external, internal, drift.delta_m)

        halt_by_id = {d.robot_id: d for d in self.watchdog.poll_once() if d.halt}

        results: list[dict] = []
        for rid, binding in self.bindings.items():
            if rid not in observed:
                results.append({"robot_id": rid, "action": "skipped", "reason": "no observation"})
                continue

            external, internal, delta_m = observed[rid]
            decision = halt_by_id.get(rid)
            if decision is not None:
                results.append(self._handle_halt(binding, rid, decision, delta_m))
                continue

            if delta_m > edge_settings.drift_degraded_m:
                results.append(self._correct(binding, rid, external, internal, delta_m))
            else:
                results.append({"robot_id": rid, "action": "nominal", "delta_m": round(delta_m, 4)})

        return results

    def _handle_halt(
        self, binding: RobotBinding, rid: str, decision: HaltDecision, delta_m: float,
    ) -> dict:
        estop_blocked = not binding.scope_ok("control.estop")
        if binding.adapter is not None and not estop_blocked:
            try:
                binding.adapter.trigger_estop(rid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[edge] adapter estop failed for %s: %s", rid, exc)
        self.cloud.post_alert({
            "robot_id": rid, "type": decision.alert_type or "drift_exceeded",
            "severity": "critical", "delta_meters": round(delta_m, 3),
            "message": decision.reason + (" [E-Stop not granted]" if estop_blocked else ""),
        })
        return {
            "robot_id": rid,
            "action": "halt_blocked" if estop_blocked else "halt",
            "reason": decision.reason,
        }

    def _correct(
        self,
        binding: RobotBinding,
        rid: str,
        external: Pose2D,
        internal: Pose2D,
        delta_m: float,
    ) -> dict:
        """Guide path: T_delta + inject_waypoint; fallback: cmd_vel when no trajectory."""
        if self.injector.has_trajectory(rid):
            if not (binding.scope_ok("mission.dispatch") or binding.scope_ok("control.velocity")):
                return {
                    "robot_id": rid,
                    "action": "correct_blocked",
                    "reason": "mission.dispatch/control.velocity not granted",
                    "delta_m": round(delta_m, 4),
                }
            tick = self.injector.tick(rid, external, internal)
            if tick.trajectory_complete or tick.w_internal is None:
                return {
                    "robot_id": rid,
                    "action": "trajectory_complete",
                    "delta_m": round(delta_m, 4),
                }
            injected = False
            if binding.adapter is not None:
                try:
                    injected = bool(binding.adapter.inject_waypoint(rid, tick.w_internal))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[edge] inject_waypoint failed for %s: %s", rid, exc)
            return {
                "robot_id": rid,
                "action": "inject",
                "delta_m": round(delta_m, 4),
                "w_internal": list(tick.w_internal),
                "w_abs": list(tick.w_abs) if tick.w_abs else None,
                "injected": injected,
            }

        # No trajectory: legacy velocity correction (ROS2 family only).
        if not binding.scope_ok("control.velocity"):
            return {
                "robot_id": rid,
                "action": "correct_blocked",
                "reason": "control.velocity not granted",
                "delta_m": round(delta_m, 4),
            }
        correction = self.velocity.compute_correction(rid, external, internal)
        if binding.adapter is not None:
            try:
                binding.adapter.send_velocity(correction.vx, correction.vy, correction.wz)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[edge] adapter cmd_vel failed for %s: %s", rid, exc)
        return {"robot_id": rid, "action": "correct", "delta_m": round(delta_m, 4)}

    def hydrate_scopes(self, cloud_base_url: str | None = None) -> None:
        """Best-effort: pull each vendor's granted scopes from the cloud so local
        enforcement matches. Safe to skip (stays permissive) if the cloud is unreachable."""
        base = (cloud_base_url or edge_settings.cloud_url).rstrip("/")
        try:
            import httpx
        except Exception:  # noqa: BLE001
            return
        for binding in self.bindings.values():
            try:
                resp = httpx.get(f"{base}/api/oem/grants/{binding.vendor}", timeout=5.0)
                data = resp.json()
                binding.managed = bool(data.get("managed"))
                binding.granted_scopes = set(data.get("granted_scopes") or [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[edge] scope hydrate failed for %s: %s", binding.vendor, exc)
