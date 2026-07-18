"""OEM public API connection layer — typed stubs for lab implementation.

Each client encodes the *publicly documented* control surface for one vendor and
maps Orbital's ``inject_waypoint`` / pose / estop onto that surface. Real SDKs
are optional; without them the client records call shapes for tests and dry-runs.
"""
from __future__ import annotations

from .agibot import AgiBotAimdkClient
from .agility import AgilityArcClient
from .base import OemApiClient, OemCall, OemEndpoint
from .boston_dynamics import BostonDynamicsSpotClient
from .deep_robotics import DeepRoboticsLite3Client
from .fourier import FourierAuroraClient
from .magiclab import MagicLabRos2Client
from .registry import get_oem_client, known_oem_api_vendors, list_oem_endpoints
from .unitree import UnitreeRos2Client

__all__ = [
    "OemApiClient",
    "OemCall",
    "OemEndpoint",
    "UnitreeRos2Client",
    "BostonDynamicsSpotClient",
    "AgilityArcClient",
    "AgiBotAimdkClient",
    "DeepRoboticsLite3Client",
    "FourierAuroraClient",
    "MagicLabRos2Client",
    "get_oem_client",
    "known_oem_api_vendors",
    "list_oem_endpoints",
]
