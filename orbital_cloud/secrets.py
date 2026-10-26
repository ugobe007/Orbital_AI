"""Cloud-side secret helpers — re-exports OEM loader + RBAC token presence checks.

OEM robot credentials live in ``fleet_adapters.secrets`` (edge/adapters).
Dashboard RBAC tokens stay in ``ORBITAL_RBAC_TOKENS`` (see ``rbac.py``).
"""
from __future__ import annotations

import os

from fleet_adapters.secrets import load_oem_credentials, resolve_oem_credentials, vendor_secret_aliases

__all__ = [
    "load_oem_credentials",
    "resolve_oem_credentials",
    "vendor_secret_aliases",
    "prod_security_flags",
]


def prod_security_flags() -> dict[str, bool]:
    """Snapshot of production security toggles (no secret values)."""

    def _on(name: str) -> bool:
        return (os.getenv(name, "0") or "0").strip().lower() in ("1", "true", "yes", "on")

    tokens = (os.getenv("ORBITAL_RBAC_TOKENS") or "").strip()
    return {
        "rbac_enforce": _on("ORBITAL_RBAC_ENFORCE"),
        "strict_oem_scopes": _on("ORBITAL_STRICT_OEM_SCOPES"),
        "rbac_tokens_configured": bool(tokens),
        "rbac_proxy_secret_configured": bool((os.getenv("ORBITAL_RBAC_PROXY_SECRET") or "").strip()),
        "agents_enabled": (os.getenv("ORBITAL_AGENTS_ENABLED", "1") or "1").strip().lower()
        in ("1", "true", "yes", "on"),
        "agent_direct_control": _on("ORBITAL_AGENT_DIRECT_CONTROL"),
    }
