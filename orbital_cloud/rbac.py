"""Dashboard RBAC — Admin / Operator / Viewer (Sprint C4).

Enforcement is opt-in via ``ORBITAL_RBAC_ENFORCE=1``. When enforced:
  * Operator / Admin actions require ``Authorization: Bearer <token>`` (or role header).
  * Anonymous callers default to **Viewer** when ``ORBITAL_RBAC_ANON_VIEWER=1`` (default),
    so the marketing embed and public map keep working while control stays locked.
"""
from __future__ import annotations

import os
from enum import Enum

from fastapi import Header, HTTPException


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


# Higher number = more privilege.
_RANK = {Role.VIEWER: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


def _strip_wrapping_quotes(value: str) -> str:
    """Remove a single layer of wrapping ' or \" — never store quotes in Fly secrets."""
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def _env_flag(name: str, default: str = "0") -> bool:
    raw = _strip_wrapping_quotes(os.getenv(name, default) or default)
    return raw.lower() not in ("0", "false", "no", "off", "")


def rbac_enforced() -> bool:
    return _env_flag("ORBITAL_RBAC_ENFORCE", "0")


def anon_viewer_allowed() -> bool:
    """Public read access when RBAC is on (default on — keeps orbital-ai.io demo alive)."""
    return _env_flag("ORBITAL_RBAC_ANON_VIEWER", "1")


def _token_role_map() -> dict[str, Role]:
    """ORBITAL_RBAC_TOKENS=admin:secret-a,operator:secret-o,viewer:secret-v"""
    raw = _strip_wrapping_quotes(os.getenv("ORBITAL_RBAC_TOKENS") or "")
    out: dict[str, Role] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role_s, token = part.split(":", 1)
        role_s = _strip_wrapping_quotes(role_s)
        token = _strip_wrapping_quotes(token)
        try:
            out[token] = Role(role_s.lower())
        except ValueError:
            continue
    return out


def resolve_role(
    *,
    x_orbital_role: str | None = None,
    authorization: str | None = None,
) -> Role:
    if x_orbital_role:
        try:
            return Role(_strip_wrapping_quotes(x_orbital_role).lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown role: {x_orbital_role}") from exc

    if authorization and authorization.lower().startswith("bearer "):
        token = _strip_wrapping_quotes(authorization[7:])
        mapped = _token_role_map().get(token)
        if mapped is not None:
            return mapped

    if not rbac_enforced():
        return Role.ADMIN  # permissive default for demos/tests

    if anon_viewer_allowed():
        return Role.VIEWER  # public map / marketing embed

    raise HTTPException(status_code=401, detail="X-Orbital-Role or RBAC bearer token required")


def require_min_role(minimum: Role):
    """FastAPI dependency factory: require role >= minimum when RBAC is enforced."""

    async def _dep(
        x_orbital_role: str | None = Header(default=None, alias="X-Orbital-Role"),
        authorization: str | None = Header(default=None),
    ) -> Role:
        role = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization)
        if _RANK[role] < _RANK[minimum]:
            raise HTTPException(
                status_code=403,
                detail=f"requires role {minimum.value} or higher (have {role.value})",
            )
        return role

    return _dep


RequireViewer = require_min_role(Role.VIEWER)
RequireOperator = require_min_role(Role.OPERATOR)
RequireAdmin = require_min_role(Role.ADMIN)
