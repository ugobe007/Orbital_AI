"""Runtime configuration + the seed fleet used by the simulator.

Everything is env-overridable so the same image runs as a local demo or a real
cloud that only ingests from live edge nodes (set ORBITAL_SIMULATOR_ENABLED=0).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("ORBITAL_HOST", "0.0.0.0")
    port: int = int(os.getenv("ORBITAL_PORT", "8090") or "8090")

    facility_id: str = os.getenv("ORBITAL_FACILITY_ID", "facility-sf-001")
    facility_name: str = os.getenv("ORBITAL_FACILITY_NAME", "StageGate SF Pilot")

    simulator_enabled: bool = _flag("ORBITAL_SIMULATOR_ENABLED", "1")
    sim_tick_hz: float = float(os.getenv("ORBITAL_SIM_TICK_HZ", "5") or "5")

    drift_degraded_m: float = float(os.getenv("ORBITAL_DRIFT_DEGRADED_M", "0.1") or "0.1")
    halt_threshold_m: float = float(os.getenv("ORBITAL_HALT_THRESHOLD_M", "0.5") or "0.5")


settings = Settings()


# Industry tabs required by the Module 7 dashboard spec.
INDUSTRIES = ["Humanoids", "Cleaning", "Delivery", "Inventory"]

# Vendors mirror the Fleet Adapter matrix in the build guide.
# (id, vendor, model, industry, base_battery)
SEED_FLEET: list[dict] = [
    {"id": "rbt-01", "vendor": "Unitree", "model": "G1", "industry": "Humanoids"},
    {"id": "rbt-02", "vendor": "AgiBot", "model": "A2", "industry": "Humanoids"},
    {"id": "rbt-03", "vendor": "Boston Dynamics", "model": "Spot", "industry": "Inventory"},
    {"id": "rbt-04", "vendor": "Agility Robotics", "model": "Digit", "industry": "Delivery"},
    {"id": "rbt-05", "vendor": "Deep Robotics", "model": "Lite3", "industry": "Inventory"},
    {"id": "rbt-06", "vendor": "Fourier Robotics", "model": "GR-1", "industry": "Humanoids"},
    {"id": "rbt-07", "vendor": "MagicLab", "model": "MagicDog", "industry": "Cleaning"},
    {"id": "rbt-08", "vendor": "Unitree", "model": "Go2", "industry": "Cleaning"},
    {"id": "rbt-09", "vendor": "AgiBot", "model": "A2-W", "industry": "Delivery"},
]

# Short OEM briefs powering the dashboard "business card" panel (Module 7 CRM panel).
# Real deployments would source these from the Orbital AI Cloud CRM; static for the demo.
VENDOR_BRIEFS: dict[str, str] = {
    "Unitree": "Shenzhen-based quadruped/humanoid OEM; ROS 2-native nav stack, high-frequency cmd_vel override supported.",
    "AgiBot": "Humanoid OEM (aimdk_msgs + ROS 2); telemetry via HAL interface. Fast-growing GR fleet.",
    "Boston Dynamics": "Spot/Atlas; gRPC lease-based control (no ROS 2 TF). Command-level override only.",
    "Agility Robotics": "Digit humanoid; cloud Arc REST/WebSocket API. Cloud-to-cloud integration.",
    "Deep Robotics": "Quadruped OEM; ROS 2 nav + low-latency UDP motion host for <10ms E-Stop.",
    "Fourier Robotics": "GR series; fuse FSA actuator odometry via robot_localization EKF (no SLAM suppression).",
    "MagicLab": "MagicDog quadruped; ROS 2 nav + LCM status channel. Suppress internal SLAM before TF publish.",
}
