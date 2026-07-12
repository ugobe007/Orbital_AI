"""ARIA Edge Node agent — wires Modules 1/2/4 together and syncs the Orbital AI Cloud.

Per tick it:
  1. localizes every robot from the camera rig (Module 1 CV pipeline -> external pose),
  2. reads each robot's self-reported pose via its fleet adapter (Module 3),
  3. measures drift and runs the Safety Halt Controller (Module 4),
  4. on a halt: commands the adapter E-Stop and raises a cloud alert,
     otherwise: if drift is degraded, generates a corrective command (Module 2) and
     applies it through the adapter ("TF Hijack"),
  5. posts drift telemetry to the cloud (feeds Benchmark Library + dashboard).

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
from .safety_halt import SafetyHaltController
from .types import CameraFrame, DriftEstimate, Pose2D
from .waypoint_generator import MicroWaypointGenerator

logger = logging.getLogger(__name__)


@dataclass
class RobotBinding:
    """What the edge knows about one robot: identity + how to command it."""
    robot_id: str
    vendor: str
    model: str
    adapter: Any = None  # a fleet_adapters.FleetAdapter (optional in the scaffold)
    last_external: Optional[Pose2D] = None
    last_internal: Optional[Pose2D] = None


@dataclass
class CloudSync:
    """Posts telemetry/alerts to the Orbital AI Cloud. Record-only when disabled."""
    base_url: str = edge_settings.cloud_url
    api_key: str = edge_settings.cloud_api_key
    enabled: bool = False  # opt in explicitly; tests + offline edges stay record-only
    telemetry_sent: list[dict] = field(default_factory=list)
    alerts_sent: list[dict] = field(default_factory=list)

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

    def _post(self, path: str, payload: dict) -> None:
        try:
            import httpx

            httpx.post(f"{self.base_url}{path}", json=payload, headers=self._headers(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001 — cloud sync is best-effort, never blocks control
            logger.warning("[edge] cloud sync %s failed: %s", path, exc)


class EdgeAgent:
    def __init__(
        self,
        bindings: list[RobotBinding],
        *,
        cv: PoseSource | None = None,
        safety: SafetyHaltController | None = None,
        waypoints: MicroWaypointGenerator | None = None,
        cloud: CloudSync | None = None,
    ) -> None:
        self.bindings = {b.robot_id: b for b in bindings}
        self.cv = cv or SimulatedCVPipeline()
        self.safety = safety or SafetyHaltController()
        self.waypoints = waypoints or MicroWaypointGenerator()
        self.cloud = cloud or CloudSync()

    def tick(self, frame: CameraFrame, internal_poses: dict[str, Pose2D]) -> list[dict]:
        """One control cycle across all bound robots. Returns a per-robot result summary."""
        detections = {d.robot_id: d for d in self.cv.process_frame(frame)}
        results: list[dict] = []

        for rid, binding in self.bindings.items():
            det = detections.get(rid)
            internal = internal_poses.get(rid)
            if det is None or internal is None:
                results.append({"robot_id": rid, "action": "skipped", "reason": "no observation"})
                continue

            external = det.world_pose
            drift = DriftEstimate(robot_id=rid, external=external, internal=internal)

            external_moved = external.distance_to(binding.last_external) if binding.last_external else 0.0
            internal_moved = internal.distance_to(binding.last_internal) if binding.last_internal else 0.0
            binding.last_external, binding.last_internal = external, internal

            self.cloud.post_telemetry({
                "robot_id": rid, "vendor": binding.vendor, "model": binding.model,
                "facility_id": edge_settings.facility_id, "delta_meters": round(drift.delta_m, 4),
            })

            decision = self.safety.evaluate(drift, external_moved_m=external_moved, internal_moved_m=internal_moved)
            if decision.halt:
                if binding.adapter is not None:
                    try:
                        binding.adapter.estop()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[edge] adapter estop failed for %s: %s", rid, exc)
                self.cloud.post_alert({
                    "robot_id": rid, "type": decision.alert_type or "drift_exceeded",
                    "severity": "critical", "delta_meters": round(drift.delta_m, 3),
                    "message": decision.reason,
                })
                results.append({"robot_id": rid, "action": "halt", "reason": decision.reason})
                continue

            if drift.delta_m > edge_settings.drift_degraded_m:
                correction = self.waypoints.compute_correction(rid, external, internal)
                if binding.adapter is not None:
                    try:
                        binding.adapter.send_velocity(correction.vx, correction.vy, correction.wz)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[edge] adapter cmd_vel failed for %s: %s", rid, exc)
                results.append({"robot_id": rid, "action": "correct", "delta_m": round(drift.delta_m, 4)})
            else:
                results.append({"robot_id": rid, "action": "nominal", "delta_m": round(drift.delta_m, 4)})

        return results
