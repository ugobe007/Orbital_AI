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

    # Operator drive controls: default patrol/nav speed and the ceiling an operator can set.
    base_speed_mps: float = float(os.getenv("ORBITAL_BASE_SPEED_MPS", "0.6") or "0.6")
    max_speed_mps: float = float(os.getenv("ORBITAL_MAX_SPEED_MPS", "2.5") or "2.5")

    # Autonomy layer: the orchestrator supervises the fleet on this cadence and can
    # take safety-first actions (auto E-Stop, proactive charge dispatch). The LLM
    # narrative is advisory only and never gates a safety action.
    orchestrator_enabled: bool = _flag("ORBITAL_ORCHESTRATOR_ENABLED", "1")
    orchestrator_interval_s: float = float(os.getenv("ORBITAL_ORCHESTRATOR_INTERVAL_S", "5") or "5")
    low_battery_pct: float = float(os.getenv("ORBITAL_LOW_BATTERY_PCT", "25") or "25")
    # An orchestrator/drift auto-halt re-converges and resumes after this cooldown (ARIA
    # recovers on its own). A human-issued E-Stop is NOT auto-recovered — it waits for an operator.
    auto_recover_s: float = float(os.getenv("ORBITAL_AUTO_RECOVER_S", "12") or "12")
    # Autonomous task cycle: after finishing a task a robot hands off to a peer and pauses
    # (shown red) for this long before picking up its next task (shown green) — makes the
    # start/stop rhythm of the fleet visibly legible.
    task_pause_s: float = float(os.getenv("ORBITAL_TASK_PAUSE_S", "10") or "10")
    llm_enabled: bool = _flag("ORBITAL_LLM_ENABLED", "0")
    llm_model: str = os.getenv("ORBITAL_LLM_MODEL", "gpt-4o-mini")

    # Seed a few demo OEM partners at startup so the governance table + scope-aware
    # controls render with data out of the box. Turn off for a clean production start.
    seed_oems: bool = _flag("ORBITAL_SEED_OEMS", "1")


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

# ── Warehouse map (Global Spatial Map for the operator console) ───────────────
# A simple, obstacle-annotated floor plan (meters). Orbital's overhead camera rig
# localizes robots against THIS map; operators drop waypoints on it and Orbital drives
# the robot there via visual control — no reliance on the robot's onboard SLAM.
WAREHOUSE: dict = {
    "name": "StageGate SF Fulfillment",
    "width_m": 24.0,
    "height_m": 16.0,
    # Storage racks (visual obstacles): x, y = bottom-left corner; w, h in meters.
    "racks": [
        {"id": "A", "x": 3.0, "y": 2.0, "w": 1.2, "h": 5.0},
        {"id": "B", "x": 6.5, "y": 2.0, "w": 1.2, "h": 5.0},
        {"id": "C", "x": 10.0, "y": 2.0, "w": 1.2, "h": 5.0},
        {"id": "D", "x": 3.0, "y": 9.0, "w": 1.2, "h": 5.0},
        {"id": "E", "x": 6.5, "y": 9.0, "w": 1.2, "h": 5.0},
        {"id": "F", "x": 10.0, "y": 9.0, "w": 1.2, "h": 5.0},
        {"id": "G", "x": 16.0, "y": 3.0, "w": 5.0, "h": 1.2},
        {"id": "H", "x": 16.0, "y": 6.0, "w": 5.0, "h": 1.2},
        {"id": "I", "x": 16.0, "y": 9.0, "w": 5.0, "h": 1.2},
    ],
    # Charging pads.
    "charge_stations": [
        {"id": "chg-1", "x": 1.2, "y": 15.0},
        {"id": "chg-2", "x": 22.8, "y": 15.0},
    ],
    # Inbound/outbound dock.
    "dock": {"x": 12.0, "y": 15.2, "w": 6.0, "h": 0.8},
    # Named work points the fleet shuttles payloads between. Missions reference these by
    # name ("Move tote: Aisle AB → Dock"), so the operator can read the goal off the map.
    "stations": [
        {"id": "Dock", "x": 12.0, "y": 14.0, "kind": "dock"},
        {"id": "Aisle AB", "x": 5.2, "y": 5.5, "kind": "aisle"},
        {"id": "Aisle BC", "x": 8.7, "y": 5.5, "kind": "aisle"},
        {"id": "Aisle DE", "x": 5.2, "y": 12.0, "kind": "aisle"},
        {"id": "Aisle EF", "x": 8.7, "y": 12.0, "kind": "aisle"},
        {"id": "Bay G", "x": 14.6, "y": 3.6, "kind": "bay"},
        {"id": "Bay H", "x": 14.6, "y": 6.6, "kind": "bay"},
        {"id": "Bay I", "x": 14.6, "y": 9.6, "kind": "bay"},
        {"id": "Stage", "x": 13.0, "y": 11.5, "kind": "stage"},
    ],
    # Overhead camera rig — Orbital's cameras localize robots against this map (the ARIA
    # ground-truth pose). Rendered distinctly from waypoints so the two are never confused.
    "cameras": [
        {"id": "cam-1", "x": 4.0, "y": 4.0, "coverage_m": 4.5},
        {"id": "cam-2", "x": 12.0, "y": 4.0, "coverage_m": 4.5},
        {"id": "cam-3", "x": 20.0, "y": 4.0, "coverage_m": 4.5},
        {"id": "cam-4", "x": 4.0, "y": 12.0, "coverage_m": 4.5},
        {"id": "cam-5", "x": 12.0, "y": 12.0, "coverage_m": 4.5},
        {"id": "cam-6", "x": 20.0, "y": 12.0, "coverage_m": 4.5},
    ],
}

# Fleet mission choreography. Every SEQUENCE_PERIOD_S the whole fleet adopts a new theme
# and each robot gets a fresh pick→drop assignment, so the floor never looks stale. Each
# theme names a verb, an objective sentence, and which stations are sources vs. destinations.
SEQUENCE_PERIOD_S: float = float(os.getenv("ORBITAL_SEQUENCE_PERIOD_S", "30") or "30")
# Fleet sim: how many lead robots publish relay waypoints (others shuttle between them).
LEAD_COUNT: int = max(2, min(5, int(os.getenv("ORBITAL_LEAD_COUNT", "3") or "3")))
SEQUENCE_THEMES: list[dict] = [
    {
        "id": "inbound",
        "label": "Inbound unload",
        "objective": "Clear the inbound dock — carry totes from the dock into the storage aisles.",
        "verb": "Move tote",
        "pickup": ["Dock"],
        "dropoff": ["Aisle AB", "Aisle BC", "Aisle DE", "Aisle EF"],
    },
    {
        "id": "picking",
        "label": "Order picking",
        "objective": "Pick for outbound — pull items from the storage aisles to the stage.",
        "verb": "Pick & carry",
        "pickup": ["Aisle AB", "Aisle BC", "Aisle DE", "Aisle EF"],
        "dropoff": ["Stage"],
    },
    {
        "id": "replen",
        "label": "Replenishment",
        "objective": "Replenish forward pick faces from the reserve bays.",
        "verb": "Replenish",
        "pickup": ["Bay G", "Bay H", "Bay I"],
        "dropoff": ["Aisle AB", "Aisle BC", "Aisle DE", "Aisle EF"],
    },
    {
        "id": "crossdock",
        "label": "Cross-dock",
        "objective": "Cross-dock inbound freight straight through to the outbound bays.",
        "verb": "Cross-dock",
        "pickup": ["Dock"],
        "dropoff": ["Bay G", "Bay H", "Bay I"],
    },
    {
        "id": "cyclecount",
        "label": "Cycle count",
        "objective": "Audit stock — scan racks to verify counts, then stage any discrepancies.",
        "verb": "Cycle count",
        "pickup": ["Aisle AB", "Aisle BC", "Aisle DE", "Aisle EF"],
        "dropoff": ["Stage", "Dock"],
    },
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
