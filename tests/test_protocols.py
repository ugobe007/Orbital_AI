"""Sprint B — protocol contracts, fake OEM servers, TF publisher."""
import math

import pytest

from aria_edge.tf_publisher import SimulatedTFPublisher
from aria_edge.types import Pose2D
from fleet_adapters import get_adapter
from fleet_adapters.agility import SimulatedAgilityAdapter
from fleet_adapters.boston_dynamics import SimulatedBostonDynamicsAdapter
from fleet_adapters.fake_servers import FakeArcServer, FakeBosdynServer
from fleet_adapters.protocols import (
    ALL_CONTRACTS,
    AGILITY,
    BOSTON_DYNAMICS,
    assert_inject_shape,
    contract_for,
    known_protocol_vendors,
)


def test_all_guide_vendors_have_contracts():
    vendors = known_protocol_vendors()
    for expected in (
        "Unitree", "AgiBot", "Boston Dynamics", "Deep Robotics",
        "Fourier Robotics", "Agility Robotics", "MagicLab",
    ):
        assert expected in vendors
    assert len(ALL_CONTRACTS) == 7


def test_unitree_contract_lists_navigate_to_pose():
    c = contract_for("Unitree")
    assert c is not None
    names = [e.name for e in c.ros2]
    assert "/{ns}/navigate_to_pose" in names
    assert c.inject_op == "ros2.navigate_to_pose"
    assert c.suppress_internal_slam is True


def test_fourier_does_not_suppress_slam():
    assert contract_for("Fourier Robotics").suppress_internal_slam is False


def test_ros2_adapter_records_protocol_op():
    a = get_adapter("Unitree", "rbt-01")
    a.inject_waypoint("rbt-01", (1.0, 2.0))
    assert_inject_shape("Unitree", a.protocol_ops)


def test_fake_bosdyn_requires_lease_then_records_robot_command():
    server = FakeBosdynServer()
    with pytest.raises(RuntimeError, match="Lease"):
        server.robot_command(se2_trajectory={"x": 1.0, "y": 0.0, "theta": 0.0})

    adapter = SimulatedBostonDynamicsAdapter("spot-1", fake_server=server)
    assert adapter.inject_waypoint("spot-1", (1.5, 0.25)) is True
    assert server.leases and server.commands
    assert server.commands[-1]["command"] == "SE2TrajectoryCommand"
    assert_inject_shape("Boston Dynamics", adapter.protocol_ops)
    assert BOSTON_DYNAMICS.inject_op in server.recorded_ops


def test_fake_arc_posts_tasks_with_api_key():
    arc = FakeArcServer(api_key="secret")
    adapter = SimulatedAgilityAdapter("digit-1", fake_server=arc)
    assert adapter.inject_waypoint("digit-1", (0.4, 0.1)) is True
    assert arc.tasks
    assert arc.tasks[0]["body"]["waypoint"]["x"] == 0.4
    assert_inject_shape("Agility Robotics", adapter.protocol_ops)
    assert AGILITY.inject_op in arc.recorded_ops

    # Unauthorized request rejected
    bare = FakeArcServer()
    r = bare.client.post("/api/v1/tasks", json={})
    assert r.status_code == 401


def test_tf_publisher_records_map_to_odom_at_30hz():
    pub = SimulatedTFPublisher("rbt-01", robot_namespace="unitree", publish_hz=30.0)
    pub.on_external_pose(Pose2D(1.0, 2.0, math.pi / 4))
    t0 = 1000.0
    for i in range(30):
        pub.publish_once(now=t0 + i / 30.0)
    assert len(pub.frames) == 30
    frame = pub.frames[0]
    assert frame.frame_id == "map"
    assert frame.child_frame_id == "unitree/odom"
    assert frame.x == 1.0 and frame.y == 2.0
    rate = pub.stream_rate_hz(window=1.0)
    assert 25.0 <= rate <= 35.0


def test_tf_publisher_tick_respects_period():
    pub = SimulatedTFPublisher("rbt-01", publish_hz=30.0)
    pub.on_external_pose(Pose2D(0.0, 0.0))
    assert pub.tick(now=0.0) is not None
    assert pub.tick(now=0.01) is None  # < 1/30 s
    assert pub.tick(now=0.04) is not None
