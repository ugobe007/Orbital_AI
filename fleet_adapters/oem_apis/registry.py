"""Registry: vendor name → OEM public API client."""
from __future__ import annotations

from .agibot import AgiBotAimdkClient
from .agility import AgilityArcClient
from .base import OemApiClient, OemEndpoint
from .boston_dynamics import BostonDynamicsSpotClient
from .deep_robotics import DeepRoboticsLite3Client
from .fourier import FourierAuroraClient
from .magiclab import MagicLabRos2Client
from .unitree import UnitreeRos2Client

_CLIENTS: dict[str, type[OemApiClient]] = {
    "Unitree": UnitreeRos2Client,
    "Boston Dynamics": BostonDynamicsSpotClient,
    "Agility Robotics": AgilityArcClient,
    "AgiBot": AgiBotAimdkClient,
    "Deep Robotics": DeepRoboticsLite3Client,
    "Fourier Robotics": FourierAuroraClient,
    "MagicLab": MagicLabRos2Client,
}


def get_oem_client(vendor: str, robot_id: str, host: str = "", **kwargs) -> OemApiClient:
    cls = _CLIENTS.get(vendor)
    if cls is None:
        raise KeyError(f"No OEM API client for vendor={vendor!r}. Known: {known_oem_api_vendors()}")
    return cls(robot_id, host, **kwargs)


def known_oem_api_vendors() -> list[str]:
    return sorted(_CLIENTS)


def list_oem_endpoints(vendor: str | None = None) -> dict[str, list[dict]]:
    """Catalog of public endpoints for docs / dashboards."""
    vendors = [vendor] if vendor else known_oem_api_vendors()
    out: dict[str, list[dict]] = {}
    for v in vendors:
        cls = _CLIENTS[v]
        out[v] = [
            {
                "name": e.name,
                "kind": e.kind,
                "path": e.path,
                "docs_url": e.docs_url,
                "notes": e.notes,
            }
            for e in cls.endpoints()
        ]
    return out
