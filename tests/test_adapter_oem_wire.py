"""Adapters wired to OEM public API clients (all 7 vendors)."""
import pytest

from fleet_adapters import (
    AgiBotAdapter,
    DeepRoboticsAdapter,
    FourierAdapter,
    MagicLabAdapter,
    get_adapter,
)
from fleet_adapters.agility import SimulatedAgilityAdapter
from fleet_adapters.boston_dynamics import SimulatedBostonDynamicsAdapter
from fleet_adapters.fake_servers import FakeArcServer, FakeBosdynServer
from fleet_adapters.oem_apis import (
    AgiBotAimdkClient,
    AgilityArcClient,
    BostonDynamicsSpotClient,
    DeepRoboticsLite3Client,
    FourierAuroraClient,
    MagicLabRos2Client,
    UnitreeRos2Client,
)
from fleet_adapters.unitree import UnitreeAdapter

_WIRED_ROS2 = [
    ("AgiBot", AgiBotAdapter, AgiBotAimdkClient),
    ("Deep Robotics", DeepRoboticsAdapter, DeepRoboticsLite3Client),
    ("Fourier Robotics", FourierAdapter, FourierAuroraClient),
    ("MagicLab", MagicLabAdapter, MagicLabRos2Client),
]


def test_unitree_adapter_has_oem_api_and_records_nav2():
    a = get_adapter("Unitree", "rbt-01")
    assert isinstance(a, UnitreeAdapter)
    assert isinstance(a.oem_api, UnitreeRos2Client)
    assert a.connect() is True
    assert a.inject_waypoint("rbt-01", (1.2, 3.4)) is True
    assert a.oem_api.calls
    ops = [c.op for c in a.oem_api.calls]
    assert "connect" in ops and "inject_waypoint" in ops
    assert a.oem_api.calls[-1].payload["type"] == "nav2_msgs/action/NavigateToPose"
    a.estop()
    assert any(c.op == "trigger_estop" for c in a.oem_api.calls)


def test_unitree_send_velocity_also_hits_oem_api():
    a = get_adapter("Unitree", "rbt-01")
    a.send_velocity(0.2, 0.0, 0.1)
    assert any(c.op == "send_velocity" for c in a.oem_api.calls)


def test_bd_adapter_wires_oem_api_and_fake_server():
    fake = FakeBosdynServer()
    a = SimulatedBostonDynamicsAdapter("spot-1", endpoint="192.168.50.3", fake_server=fake)
    assert isinstance(a.oem_api, BostonDynamicsSpotClient)
    assert a.connect({"username": "user"}) is True
    assert any(c.op == "LeaseService.Acquire" for c in a.oem_api.calls)
    assert a.inject_waypoint("spot-1", (2.0, 1.0, 0.3)) is True
    assert fake.commands and fake.commands[-1]["command"] == "SE2TrajectoryCommand"
    assert a.oem_api.calls[-1].payload["se2"]["theta"] == 0.3
    a.estop()
    assert fake.estops


def test_agility_adapter_wires_oem_api_and_fake_arc():
    arc = FakeArcServer(api_key="k")
    a = SimulatedAgilityAdapter(
        "digit-1",
        endpoint="https://arc.example",
        fake_server=arc,
        api_key="k",
    )
    assert isinstance(a.oem_api, AgilityArcClient)
    assert a.connect({"api_key": "k"}) is True
    assert a.inject_waypoint("digit-1", (0.4, 0.1)) is True
    assert arc.tasks
    assert a.oem_api.calls[-1].payload["path"] == "/api/v1/tasks"
    a.estop()
    assert arc.app.state.estops


def test_get_adapter_passes_use_hardware_flag_to_unitree():
    # use_hardware=True without rclpy must raise at construct
    with pytest.raises(RuntimeError, match="rclpy"):
        get_adapter("Unitree", "rbt-01", use_hardware=True)


@pytest.mark.parametrize("vendor,adapter_cls,client_cls", _WIRED_ROS2)
def test_ros2_family_adapter_wires_oem_api(vendor, adapter_cls, client_cls):
    a = get_adapter(vendor, "rbt-x")
    assert isinstance(a, adapter_cls)
    assert isinstance(a.oem_api, client_cls)
    assert a.connect() is True
    assert a.inject_waypoint("rbt-x", (1.0, 2.0, 0.1)) is True
    ops = [c.op for c in a.oem_api.calls]
    assert "connect" in ops and "inject_waypoint" in ops
    a.estop()
    assert any(c.op == "trigger_estop" for c in a.oem_api.calls)
    assert a.inject_waypoint("rbt-x", (0.0, 0.0)) is False


def test_connect_loads_arc_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ORBITAL_SECRET_ARC_API_KEY", "env-arc-key")
    a = SimulatedAgilityAdapter("digit-env", endpoint="https://arc.example")
    assert a.connect() is True
    assert a.oem_api.api_key == "env-arc-key"


def test_connect_loads_spot_json_from_env(monkeypatch):
    monkeypatch.setenv(
        "ORBITAL_SECRET_SPOT_JSON",
        '{"username":"spot-user","password":"spot-pass"}',
    )
    a = SimulatedBostonDynamicsAdapter("spot-env")
    assert a.connect() is True
    # Client records connect with credentials when provided
    connect_calls = [c for c in a.oem_api.calls if c.op == "connect" or "Lease" in c.op]
    assert connect_calls
