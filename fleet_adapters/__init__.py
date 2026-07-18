"""Module 3 — Fleet Adapters registry.

Maps an OEM/vendor name to the adapter that speaks its protocol and exposes its capability
ceiling. The OEM onboarding flow uses ``capability_ceiling_for`` to know the *maximum* it
could offer, then records which of those the OEM actually grants.
"""
from __future__ import annotations

from .agility import SimulatedAgilityAdapter
from .base import Capability, FleetAdapter, Transport
from .boston_dynamics import SimulatedBostonDynamicsAdapter
from .ros2_adapter import SimulatedROS2Adapter
from .unitree import UnitreeAdapter

# ROS 2-family OEMs share the generic ROS 2 adapter (full cmd_vel override),
# except Unitree which has a dedicated Sprint D2 adapter (hw bind + latency hooks).
_ROS2_VENDORS = {"Unitree", "AgiBot", "Deep Robotics", "Fourier Robotics", "MagicLab"}

_SPECIAL: dict[str, type[FleetAdapter]] = {
    "Unitree": UnitreeAdapter,
    "Boston Dynamics": SimulatedBostonDynamicsAdapter,
    "Agility Robotics": SimulatedAgilityAdapter,
}


def adapter_class_for(vendor: str) -> type[FleetAdapter]:
    if vendor in _SPECIAL:
        return _SPECIAL[vendor]
    return SimulatedROS2Adapter  # default: ROS 2 cmd_vel family


def get_adapter(vendor: str, robot_id: str, endpoint: str = "", **kwargs) -> FleetAdapter:
    cls = adapter_class_for(vendor)
    if cls is SimulatedROS2Adapter:
        return SimulatedROS2Adapter(robot_id, endpoint, vendor=vendor)
    return cls(robot_id, endpoint, **kwargs)


def capability_ceiling_for(vendor: str) -> set[Capability]:
    return adapter_class_for(vendor).capability_ceiling()


def known_vendors() -> list[str]:
    return sorted(_ROS2_VENDORS | set(_SPECIAL))


__all__ = [
    "Capability", "FleetAdapter", "Transport",
    "SimulatedROS2Adapter", "SimulatedBostonDynamicsAdapter", "SimulatedAgilityAdapter",
    "UnitreeAdapter",
    "adapter_class_for", "get_adapter", "capability_ceiling_for", "known_vendors",
]
