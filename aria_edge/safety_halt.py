"""Module 4 — Safety Halt Controller.

The last line of defense, running on the edge with a hard real-time budget. It watches the
drift estimate and the consistency between what the cameras see and what the robot reports,
and issues an E-Stop when any invariant breaks:

  * drift_exceeded — external/internal divergence past the halt threshold.
  * hijack_suspected — cameras see the robot moving while it reports itself still
    (someone/something else is driving it).
  * ghost_command — the robot reports motion the cameras don't see (bad odometry / spoof).
  * human_proximity — a person is inside the robot's stop radius (``evaluate_people``).

Mirrors the alert taxonomy in ``orbital_cloud.models.AlertType`` so edge halts and cloud
alerts speak the same language.
"""
from __future__ import annotations

from .config import edge_settings
from .types import DriftEstimate, HaltDecision, Pose2D

# Movement below this (meters/tick) counts as "still" for the consistency checks.
_STILL_EPS_M = 0.02


class SafetyHaltController:
    def __init__(self, halt_threshold_m: float | None = None,
                 human_stop_radius_m: float | None = None) -> None:
        self.halt_threshold_m = (
            edge_settings.halt_threshold_m if halt_threshold_m is None else halt_threshold_m
        )
        self.human_stop_radius_m = (
            edge_settings.human_stop_radius_m if human_stop_radius_m is None else human_stop_radius_m
        )

    def evaluate_people(self, robot_id: str, external: Pose2D,
                        people: list[tuple[float, float]], *, margin_m: float = 0.0) -> HaltDecision:
        """Halt when any person is within the stop radius of the camera-observed pose.
        ``margin_m`` widens the radius when deciding whether a stop may clear."""
        radius = self.human_stop_radius_m + margin_m
        nearest = min((Pose2D(x, y).distance_to(external) for x, y in people), default=None)
        if nearest is not None and nearest <= radius:
            return HaltDecision(
                robot_id, True,
                f"person {nearest:.2f}m away, inside the {self.human_stop_radius_m}m stop radius",
                alert_type="human_proximity",
            )
        return HaltDecision(robot_id, False)

    def evaluate(
        self,
        drift: DriftEstimate,
        *,
        external_moved_m: float = 0.0,
        internal_moved_m: float = 0.0,
    ) -> HaltDecision:
        rid = drift.robot_id

        if drift.delta_m > self.halt_threshold_m:
            return HaltDecision(
                rid, True,
                f"drift {drift.delta_m:.2f}m exceeded halt threshold {self.halt_threshold_m}m",
                alert_type="drift_exceeded",
            )

        cameras_see_motion = external_moved_m > _STILL_EPS_M
        robot_reports_motion = internal_moved_m > _STILL_EPS_M

        if cameras_see_motion and not robot_reports_motion:
            return HaltDecision(
                rid, True,
                "cameras see motion while robot reports still — uncommanded movement",
                alert_type="hijack_suspected",
            )
        if robot_reports_motion and not cameras_see_motion:
            return HaltDecision(
                rid, True,
                "robot reports motion the cameras do not see — odometry fault/spoof",
                alert_type="ghost_command",
            )

        return HaltDecision(rid, False)
