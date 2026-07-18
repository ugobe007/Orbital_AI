"""Pose bus + safety watchdog isolation (Sprint A4)."""
from aria_edge.pose_bus import PoseBus, SafetyWatchdog
from aria_edge.safety_halt import SafetyHaltController
from aria_edge.types import Pose2D
from aria_edge.waypoint_generator import WaypointInjector


def test_watchdog_halts_from_bus_without_injector():
    bus = PoseBus()
    halted: list[str] = []
    wd = SafetyWatchdog(
        bus,
        SafetyHaltController(halt_threshold_m=0.5),
        on_halt=lambda d: halted.append(d.robot_id),
    )
    # Publish excess drift; never touch WaypointInjector.
    bus.publish("rbt-01", Pose2D(0.8, 0.0), Pose2D(0.0, 0.0))
    decisions = wd.poll_once()
    assert decisions[0].halt is True
    assert decisions[0].alert_type == "drift_exceeded"
    assert halted == ["rbt-01"]


def test_halt_still_fires_if_injector_would_wedge():
    bus = PoseBus()
    inj = WaypointInjector()
    inj.set_trajectory("rbt-01", [(10.0, 0.0)])

    def wedged_tick(*_a, **_k):
        raise RuntimeError("injector wedged")

    inj.tick = wedged_tick  # type: ignore[method-assign]

    wd = SafetyWatchdog(bus, SafetyHaltController(halt_threshold_m=0.5))
    bus.publish("rbt-01", Pose2D(0.9, 0.0), Pose2D(0.0, 0.0))
    # Watchdog path must succeed even though injector is broken.
    assert wd.poll_once()[0].halt is True
    try:
        inj.tick("rbt-01", Pose2D(0.9, 0.0), Pose2D(0.0, 0.0))
        assert False, "expected wedge"
    except RuntimeError:
        pass
