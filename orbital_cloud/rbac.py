"""Dashboard RBAC — Admin / Operator / Viewer (Sprint C4).

Enforcement is opt-in via ``ORBITAL_RBAC_ENFORCE=1``. When enforced:
  * Operator / Admin actions require ``Authorization: Bearer <token>`` (or role header).
    * Anonymous callers default to **Viewer** when ``ORBITAL_RBAC_ANON_VIEWER=1`` (default),
    so the marketing embed and public map keep working while control stays locked.

The **Agent** role (Sprint F) is for external AI models. It reads like a Viewer, may always
E-Stop and propose actions, and gets direct control only when
``ORBITAL_AGENT_DIRECT_CONTROL=1``. Agents never accept or dismiss proposals.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

from fastapi import Header, HTTPException, Request


class Role(str, Enum):
    VIEWER = "viewer"
    AGENT = "agent"
    OPERATOR = "operator"
    ADMIN = "admin"


# Higher number = more privilege. Agent ranks with Viewer; its extra rights are explicit below.
_RANK = {Role.VIEWER: 1, Role.AGENT: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


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


# ── Agent callers ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Caller:
    role: Role
    actor: str   # attribution label; "agent:<name>" for agents

    @property
    def is_agent(self) -> bool:
        return self.role == Role.AGENT


def agent_direct_control() -> bool:
    return _env_flag("ORBITAL_AGENT_DIRECT_CONTROL", "0")


def agent_rate_per_min() -> int:
    try:
        return max(1, int(_strip_wrapping_quotes(os.getenv("ORBITAL_AGENT_RATE_PER_MIN", "30") or "30")))
    except ValueError:
        return 30


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= window_s:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


agent_limiter = _RateLimiter()


def _caller(role: Role, x_orbital_actor: str | None) -> Caller:
    label = _strip_wrapping_quotes(x_orbital_actor or "")[:64]
    if role == Role.AGENT:
        return Caller(role, f"agent:{label or 'unnamed'}")
    return Caller(role, label or role.value)


def _reject_agent(request: Request, caller: Caller, status: int, detail: str) -> HTTPException:
    from .store import store  # lazy: store pulls in the whole cloud state

    robot_id = request.path_params.get("robot_id")
    store.record_control(actor=caller.actor, role=caller.role.value,
                         action=f"{request.method} {request.url.path}", robot_id=robot_id,
                         ok=False, detail=detail)
    return HTTPException(status_code=status, detail=detail)


def _caller_dep(*, agent_allowed, rate_limited: bool):
    async def _dep(
        request: Request,
        x_orbital_role: str | None = Header(default=None, alias="X-Orbital-Role"),
        x_orbital_actor: str | None = Header(default=None, alias="X-Orbital-Actor"),
        authorization: str | None = Header(default=None),
    ) -> Caller:
        role = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization)
        caller = _caller(role, x_orbital_actor)
        if role == Role.AGENT:
            allowed, reason = agent_allowed()
            if not allowed:
                raise _reject_agent(request, caller, 403, reason)
            if rate_limited and not agent_limiter.check(caller.actor, agent_rate_per_min()):
                raise _reject_agent(request, caller, 429,
                                    f"agent rate limit: {agent_rate_per_min()} write calls per minute")
            return caller
        if _RANK[role] < _RANK[Role.OPERATOR]:
            raise HTTPException(status_code=403, detail=f"requires role operator or higher (have {role.value})")
        return caller

    return _dep


def _always() -> tuple[bool, str]:
    return True, ""


def _direct_control() -> tuple[bool, str]:
    if agent_direct_control():
        return True, ""
    return False, ("direct agent control is disabled on this deployment "
                   "(ORBITAL_AGENT_DIRECT_CONTROL=0); use propose_action so an operator can accept it")


# E-Stop is never rate-limited: stopping a robot must always be possible.
RequireStopper = _caller_dep(agent_allowed=_always, rate_limited=False)
RequireProposer = _caller_dep(agent_allowed=_always, rate_limited=True)
RequireController = _caller_dep(agent_allowed=_direct_control, rate_limited=True)


async def CurrentCaller(
    x_orbital_role: str | None = Header(default=None, alias="X-Orbital-Role"),
    x_orbital_actor: str | None = Header(default=None, alias="X-Orbital-Actor"),
    authorization: str | None = Header(default=None),
) -> Caller:
    return _caller(resolve_role(x_orbital_role=x_orbital_role, authorization=authorization), x_orbital_actor)
