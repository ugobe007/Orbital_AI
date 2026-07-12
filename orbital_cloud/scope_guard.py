"""Control-path scope enforcement.

Before Orbital issues a control action to a robot (E-Stop, resume, mission dispatch,
velocity correction), it checks that the robot's owning OEM has actually granted the
matching API scope. Robots carry a *vendor*; the guard resolves that vendor to its OEM
partner and its granted scopes.

Policy for **unmanaged** vendors (no OEM registered — e.g. the seed demo fleet):
  * default: allowed, so the built-in demo keeps working;
  * strict mode (``ORBITAL_STRICT_OEM_SCOPES=1``): denied, for locked-down deployments.

A *managed* vendor (OEM registered) is always enforced: only granted scopes pass, and a
SUSPENDED partner passes nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .models import APIScope
from .oem_store import oem_store
from .store import store


def _strict() -> bool:
    return (os.getenv("ORBITAL_STRICT_OEM_SCOPES", "0") or "0").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class ScopeCheck:
    allowed: bool
    reason: str
    managed: bool          # True if an OEM is registered for the robot's vendor
    vendor: Optional[str]


def check_vendor(vendor: str, scope: APIScope) -> ScopeCheck:
    granted = oem_store.granted_scopes_for_vendor(vendor)
    if granted is None:
        # Unmanaged vendor.
        if _strict():
            return ScopeCheck(False, f"{vendor} is unmanaged and strict scope mode is on", False, vendor)
        return ScopeCheck(True, "unmanaged vendor (permissive mode)", False, vendor)
    if scope in granted:
        return ScopeCheck(True, "granted", True, vendor)
    return ScopeCheck(
        False,
        f"OEM for '{vendor}' has not granted '{scope.value}' (granted: "
        f"{', '.join(sorted(s.value for s in granted)) or 'none'})",
        True,
        vendor,
    )


def check_robot(robot_id: str, scope: APIScope) -> ScopeCheck:
    """Resolve a robot's vendor and check the scope. Unknown robot → not allowed."""
    robot = store.robots.get(robot_id)
    if robot is None:
        return ScopeCheck(False, "robot not found", False, None)
    return check_vendor(robot.vendor, scope)
