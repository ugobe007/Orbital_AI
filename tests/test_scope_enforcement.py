"""Control-path scope enforcement — cloud endpoints + edge agent + scope_guard."""
import pytest
from fastapi.testclient import TestClient

from aria_edge.edge_agent import CloudSync, EdgeAgent, RobotBinding
from aria_edge.types import CameraFrame, Pose2D
from fleet_adapters import get_adapter
from orbital_cloud import scope_guard
from orbital_cloud.main import app
from orbital_cloud.models import APIScope

client = TestClient(app)


def _register_and_grant(vendor: str, scopes: list[str]) -> None:
    reg = client.post("/api/oem/register", json={
        "company_name": f"{vendor} Co", "vendor": vendor,
        "contact_email": "ops@example.com", "transport": "ros2",
    }).json()
    oem_id, key = reg["partner"]["id"], reg["credential"]["api_key"]
    if scopes:
        client.post(f"/api/oem/{oem_id}/scopes", json={"scopes": scopes},
                    headers={"authorization": f"Bearer {key}"})


def _robot_of_vendor(vendor: str) -> str:
    fleet = client.get("/api/dashboard/fleet").json()["robots"]
    return next(r["id"] for r in fleet if r["vendor"] == vendor)


# ── Cloud endpoint enforcement ────────────────────────────────────────────────

def test_estop_blocked_until_scope_granted():
    # MagicLab is a seed vendor not touched by other tests. Manage it with telemetry only.
    _register_and_grant("MagicLab", ["telemetry.read"])
    rid = _robot_of_vendor("MagicLab")

    blocked = client.post(f"/api/dashboard/robot/{rid}/estop")
    assert blocked.status_code == 403 and "control.estop" in blocked.json()["detail"]


def test_estop_allowed_after_granting_scope():
    _register_and_grant("Deep Robotics", ["telemetry.read", "control.estop"])
    rid = _robot_of_vendor("Deep Robotics")

    ok = client.post(f"/api/dashboard/robot/{rid}/estop")
    assert ok.status_code == 200 and ok.json()["state"] == "halted"


def test_mission_dispatch_requires_scope():
    _register_and_grant("Fourier Robotics", ["telemetry.read"])
    rid = _robot_of_vendor("Fourier Robotics")
    r = client.post("/api/dashboard/tasks", json={"robot_id": rid, "description": "patrol", "waypoints": []})
    assert r.status_code == 403 and "mission.dispatch" in r.json()["detail"]


def test_grants_endpoint_reports_effective_scopes():
    _register_and_grant("AgiBot", ["telemetry.read", "control.velocity"])
    out = client.get("/api/oem/grants/AgiBot").json()
    assert out["managed"] is True
    assert "control.velocity" in out["granted_scopes"]

    # An unmanaged vendor is reported as such.
    unmanaged = client.get("/api/oem/grants/NoSuchVendor").json()
    assert unmanaged["managed"] is False and unmanaged["granted_scopes"] == []


# ── scope_guard unit behavior (unmanaged + strict) ───────────────────────────

def test_unmanaged_vendor_is_permissive_by_default():
    check = scope_guard.check_vendor("TotallyUnknownCo", APIScope.ESTOP)
    assert check.allowed is True and check.managed is False


def test_strict_mode_denies_unmanaged(monkeypatch):
    monkeypatch.setenv("ORBITAL_STRICT_OEM_SCOPES", "1")
    check = scope_guard.check_vendor("TotallyUnknownCo", APIScope.ESTOP)
    assert check.allowed is False and check.managed is False


# ── Edge-agent local enforcement ──────────────────────────────────────────────

def _frame(gt: dict[str, Pose2D]) -> CameraFrame:
    return CameraFrame(camera_id="c", ts=0.0, width=640, height=480, ground_truth=gt)


def test_edge_blocks_velocity_without_scope():
    adapter = get_adapter("Unitree", "rbt-x")
    binding = RobotBinding("rbt-x", "Unitree", "G1", adapter=adapter,
                           managed=True, granted_scopes={"telemetry.read"})
    agent = EdgeAgent([binding], cloud=CloudSync(enabled=False))
    res = agent.tick(_frame({"rbt-x": Pose2D(0.2, 0.0)}), {"rbt-x": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "correct_blocked"
    assert not adapter.commands  # no cmd_vel issued


def test_edge_blocks_estop_without_scope_but_still_alerts():
    adapter = get_adapter("Unitree", "rbt-y")
    binding = RobotBinding("rbt-y", "Unitree", "G1", adapter=adapter,
                           managed=True, granted_scopes={"telemetry.read"})
    cloud = CloudSync(enabled=False)
    agent = EdgeAgent([binding], cloud=cloud)
    res = agent.tick(_frame({"rbt-y": Pose2D(0.8, 0.0)}), {"rbt-y": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "halt_blocked"
    assert adapter._halted is False        # could not command the stop
    assert cloud.alerts_sent               # but the alert still fired


def test_edge_allows_control_when_granted():
    adapter = get_adapter("Unitree", "rbt-z")
    binding = RobotBinding("rbt-z", "Unitree", "G1", adapter=adapter,
                           managed=True, granted_scopes={"control.velocity", "control.estop"})
    agent = EdgeAgent([binding], cloud=CloudSync(enabled=False))
    res = agent.tick(_frame({"rbt-z": Pose2D(0.2, 0.0)}), {"rbt-z": Pose2D(0.0, 0.0)})
    assert res[0]["action"] == "correct" and adapter.commands
