"""ARIA Edge Node runtime configuration (env-overridable).

Mirrors the cloud thresholds so the edge and cloud agree on what "degraded" and
"halt" mean. The edge additionally knows *its* facility identity and where the cloud is.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass


def _env_number(name: str, default, cast):
    """A numeric setting. Tolerates the quotes and spaces a dashboard paste tends to add; an
    unparseable value falls back to the default with a warning instead of failing the boot."""
    raw = (os.getenv(name) or "").strip().strip("\"'").strip()
    if not raw:
        return cast(default)
    try:
        return cast(raw)
    except ValueError:
        logging.getLogger(__name__).warning("%s=%r is not a number; using %r", name, raw, default)
        return cast(default)


def env_int(name: str, default: int) -> int:
    return _env_number(name, default, int)


def env_float(name: str, default: float) -> float:
    return _env_number(name, default, float)


@dataclass(frozen=True)
class EdgeSettings:
    facility_id: str = os.getenv("ARIA_FACILITY_ID", "facility-sf-001")

    # Where this edge node syncs telemetry/alerts. Outbound-only in production (mTLS).
    cloud_url: str = os.getenv("ORBITAL_CLOUD_URL", "http://localhost:8090").rstrip("/")
    cloud_api_key: str = os.getenv("ORBITAL_CLOUD_API_KEY", "")

    # Real-time correction cadence — the waypoint generator runs on the edge at 10Hz.
    control_hz: float = env_float("ARIA_CONTROL_HZ", 10)

    # Safety thresholds (meters) — must match orbital_cloud settings.
    drift_degraded_m: float = env_float("ORBITAL_DRIFT_DEGRADED_M", 0.1)
    halt_threshold_m: float = env_float("ORBITAL_HALT_THRESHOLD_M", 0.5)
    human_stop_radius_m: float = env_float("ORBITAL_HUMAN_STOP_RADIUS_M", 0.8)

    # Proportional gain + clamp for the corrective velocity fallback (no trajectory).
    correction_gain: float = env_float("ARIA_CORRECTION_GAIN", 1.5)
    max_correction_mps: float = env_float("ARIA_MAX_CORRECTION_MPS", 0.4)

    # Guide Module 2: lookahead on the global trajectory (meters).
    lookahead_m: float = env_float("ARIA_LOOKAHEAD_M", 0.20)

    # Lab cutover (Sprint D) — sim stays default until gear is online.
    cv_mode: str = os.getenv("ARIA_CV_MODE", "sim")  # sim | aruco
    aruco_marker_map: str = os.getenv("ARIA_ARUCO_MARKER_MAP", "")
    aruco_fixture: str = os.getenv("ARIA_ARUCO_FIXTURE", "")
    unitree_hardware: bool = (os.getenv("ARIA_UNITREE_HARDWARE", "0") or "0").strip().lower() not in (
        "0", "false", "no", "off", "",
    )
    cloud_sync_enabled: bool = (os.getenv("ARIA_CLOUD_SYNC", "0") or "0").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


edge_settings = EdgeSettings()
