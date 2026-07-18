"""OEM credential resolution from environment (Fly secrets / local .env).

Never logs secret values. Production pattern::

    fly secrets set ORBITAL_SECRET_ARC_API_KEY=... \\
      ORBITAL_SECRET_SPOT_JSON='{"username":"...","password":"..."}'

Lookup order per vendor:
  1. ``ORBITAL_SECRET_<ALIAS>_JSON`` (object merged into credentials)
  2. ``ORBITAL_SECRET_<ALIAS>_API_KEY`` → ``{"api_key": ...}``
  3. ``ORBITAL_SECRET_<ALIAS>_USERNAME`` + ``_PASSWORD`` when both set
"""
from __future__ import annotations

import json
import os
from typing import Mapping

# First matching alias wins for each env key family.
_VENDOR_ALIASES: dict[str, tuple[str, ...]] = {
    "Boston Dynamics": ("SPOT", "BOSTON_DYNAMICS", "BD"),
    "Agility Robotics": ("ARC", "AGILITY"),
    "Unitree": ("UNITREE",),
    "AgiBot": ("AGIBOT", "AIMDK"),
    "Deep Robotics": ("DEEP", "DEEP_ROBOTICS", "LITE3"),
    "Fourier Robotics": ("FOURIER", "AURORA"),
    "MagicLab": ("MAGICLAB", "MAGICDOG"),
}


def vendor_secret_aliases(vendor: str) -> tuple[str, ...]:
    return _VENDOR_ALIASES.get(vendor, ())


def load_oem_credentials(vendor: str) -> dict[str, str]:
    """Return credential dict from env for ``vendor``, or ``{}`` if unset."""
    out: dict[str, str] = {}
    for alias in vendor_secret_aliases(vendor):
        raw = (os.getenv(f"ORBITAL_SECRET_{alias}_JSON") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, Mapping):
                for k, v in parsed.items():
                    if v is not None and str(v) != "":
                        out[str(k)] = str(v)

        api_key = (os.getenv(f"ORBITAL_SECRET_{alias}_API_KEY") or "").strip()
        if api_key and "api_key" not in out:
            out["api_key"] = api_key

        user = (os.getenv(f"ORBITAL_SECRET_{alias}_USERNAME") or "").strip()
        password = (os.getenv(f"ORBITAL_SECRET_{alias}_PASSWORD") or "").strip()
        if user and "username" not in out:
            out["username"] = user
        if password and "password" not in out:
            out["password"] = password

    return out


def resolve_oem_credentials(
    vendor: str,
    credentials: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Prefer explicit ``credentials``; else env secrets; else ``None``."""
    if credentials:
        return {str(k): str(v) for k, v in credentials.items() if v is not None}
    loaded = load_oem_credentials(vendor)
    return loaded or None
