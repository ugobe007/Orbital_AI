"""Dashboard RBAC — Admin / Operator / Viewer (Sprint C4).

Enforcement is opt-in via ``ORBITAL_RBAC_ENFORCE=1`` so existing demos and tests
stay open. When enforced, clients send ``X-Orbital-Role`` or a mapped Bearer token.
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


def rbac_enforced() -> bool:
    return (os.getenv("ORBITAL_RBAC_ENFORCE", "0") or "0").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def _token_role_map() -> dict[str, Role]:
    """ORBITAL_RBAC_TOKENS=admin:secret-a,operator:secret-o,viewer:secret-v"""
    raw = (os.getenv("ORBITAL_RBAC_TOKENS") or "").strip()
    out: dict[str, Role] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role_s, token = part.split(":", 1)
        try:
            out[token.strip()] = Role(role_s.strip().lower())
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
            return Role(x_orbital_role.strip().lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown role: {x_orbital_role}") from exc

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        mapped = _token_role_map().get(token)
        if mapped is not None:
            return mapped

    if not rbac_enforced():
        return Role.ADMIN  # permissive default for demos/tests
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
