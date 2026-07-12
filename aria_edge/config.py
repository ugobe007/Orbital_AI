"""ARIA Edge Node runtime configuration (env-overridable).

Mirrors the cloud thresholds so the edge and cloud agree on what "degraded" and
"halt" mean. The edge additionally knows *its* facility identity and where the cloud is.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeSettings:
    facility_id: str = os.getenv("ARIA_FACILITY_ID", "facility-sf-001")

    # Where this edge node syncs telemetry/alerts. Outbound-only in production (mTLS).
    cloud_url: str = os.getenv("ORBITAL_CLOUD_URL", "http://localhost:8090").rstrip("/")
    cloud_api_key: str = os.getenv("ORBITAL_CLOUD_API_KEY", "")

    # Real-time correction cadence — the waypoint generator runs on the edge at 10Hz.
    control_hz: float = float(os.getenv("ARIA_CONTROL_HZ", "10") or "10")

    # Safety thresholds (meters) — must match orbital_cloud settings.
    drift_degraded_m: float = float(os.getenv("ORBITAL_DRIFT_DEGRADED_M", "0.1") or "0.1")
    halt_threshold_m: float = float(os.getenv("ORBITAL_HALT_THRESHOLD_M", "0.5") or "0.5")

    # Proportional gain + clamp for the corrective velocity command.
    correction_gain: float = float(os.getenv("ARIA_CORRECTION_GAIN", "1.5") or "1.5")
    max_correction_mps: float = float(os.getenv("ARIA_MAX_CORRECTION_MPS", "0.4") or "0.4")


edge_settings = EdgeSettings()
