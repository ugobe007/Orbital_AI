"""Fleet Adapters — registry mapping + per-vendor capability ceilings."""
import pytest

from fleet_adapters import (
    Capability,
    SimulatedAgilityAdapter,
    SimulatedBostonDynamicsAdapter,
    SimulatedROS2Adapter,
    capability_ceiling_for,
    get_adapter,
    known_vendors,
)


def test_ros2_vendor_gets_full_velocity_ceiling():
    a = get_adapter("Unitree", "rbt-01")
    from fleet_adapters import UnitreeAdapter
    assert isinstance(a, UnitreeAdapter)
    assert a.vendor == "Unitree"
    assert Capability.VELOCITY in capability_ceiling_for("Unitree")


def test_boston_dynamics_has_no_velocity_override():
    a = get_adapter("Boston Dynamics", "rbt-03")
    assert isinstance(a, SimulatedBostonDynamicsAdapter)
    ceiling = capability_ceiling_for("Boston Dynamics")
    assert Capability.VELOCITY not in ceiling and Capability.ESTOP in ceiling
    with pytest.raises(NotImplementedError):
        a.send_velocity(0.1, 0.0, 0.0)


def test_agility_is_cloud_mission_level():
    a = get_adapter("Agility Robotics", "rbt-04")
    assert isinstance(a, SimulatedAgilityAdapter)
    ceiling = capability_ceiling_for("Agility Robotics")
    assert Capability.MISSION in ceiling and Capability.VELOCITY not in ceiling


def test_unknown_vendor_defaults_to_ros2():
    a = get_adapter("BrandNewCo", "rbt-99")
    assert isinstance(a, SimulatedROS2Adapter) and a.vendor == "BrandNewCo"


def test_ros2_estop_blocks_velocity():
    a = get_adapter("AgiBot", "rbt-02")
    a.estop()
    a.send_velocity(0.3, 0.0, 0.0)  # ignored while halted
    assert a.commands[-1] == (0.0, 0.0, 0.0)


def test_inject_waypoint_guide_api():
    a = get_adapter("Unitree", "rbt-01")
    assert a.inject_waypoint("rbt-01", (1.2, 3.4)) is True
    assert a.injected[-1] == (1.2, 3.4)
    pose = a.get_internal_pose("rbt-01")
    assert {"x", "y", "theta", "timestamp"} <= set(pose)
    assert a.trigger_estop("rbt-01") is True
    assert a.inject_waypoint("rbt-01", (0.0, 0.0)) is False  # halted


def test_bd_inject_without_velocity():
    a = get_adapter("Boston Dynamics", "rbt-03")
    assert a.inject_waypoint("rbt-03", (2.0, 1.0)) is True
    assert a.injected[-1] == (2.0, 1.0)


def test_known_vendors_listed():
    vendors = known_vendors()
    assert "Boston Dynamics" in vendors and "Unitree" in vendors
