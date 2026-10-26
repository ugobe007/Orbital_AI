"""Dashboard RBAC — Admin / Operator / Viewer (Sprint C4).

Enforcement is opt-in via ``ORBITAL_RBAC_ENFORCE=1``. When enforced:
  * Operator / Admin actions require ``Authorization: Bearer <token>``, a console sign-in
    session cookie (see ``sessions``), or ``X-Orbital-Role`` from the trusted proxy
    (``X-Orbital-Proxy-Secret`` = ``ORBITAL_RBAC_PROXY_SECRET``).
    From anyone else, ``X-Orbital-Role`` can only lower the caller's role.
  * Anonymous callers default to **Viewer** when ``ORBITAL_RBAC_ANON_VIEWER=1`` (default),
    so the marketing embed and public map keep working while control stays locked.

The **Agent** role (Sprint F) is for external AI models. It reads like a Viewer, may always
E-Stop and propose actions, and gets direct control only when
``ORBITAL_AGENT_DIRECT_CONTROL=1``. Agents never accept or dismiss proposals.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

from fastapi import Cookie, Header, HTTPException, Request

from . import sessions


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


# Ordering for "may this header lower my role?" — unlike _RANK, Agent sits above Viewer
# because it can E-Stop and propose.
_PRIVILEGE = {Role.VIEWER: 0, Role.AGENT: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


def proxy_secret_configured() -> bool:
    return bool(_strip_wrapping_quotes(os.getenv("ORBITAL_RBAC_PROXY_SECRET") or ""))


def _proxy_trusted(provided: str | None) -> bool:
    expected = _strip_wrapping_quotes(os.getenv("ORBITAL_RBAC_PROXY_SECRET") or "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(_strip_wrapping_quotes(provided).encode(), expected.encode())


def resolve_role(
    *,
    x_orbital_role: str | None = None,
    authorization: str | None = None,
    x_orbital_proxy_secret: str | None = None,
    session_cookie: str | None = None,
) -> Role:
    """``X-Orbital-Role`` is honoured as-is only from the trusted proxy (StageGate), which
    proves itself with ``X-Orbital-Proxy-Secret``. From anyone else it may only lower the
    role their token (or anonymous access) already grants."""
    claimed: Role | None = None
    if x_orbital_role:
        try:
            claimed = Role(_strip_wrapping_quotes(x_orbital_role).lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown role: {x_orbital_role}") from exc
        if _proxy_trusted(x_orbital_proxy_secret):
            return claimed

    base = _credential_role(authorization, session_cookie)
    if claimed is None or _PRIVILEGE[claimed] <= _PRIVILEGE[base]:
        return claimed or base
    raise HTTPException(
        status_code=403,
        detail=(f"X-Orbital-Role '{claimed.value}' exceeds this caller's '{base.value}' access; "
                "raising a role needs a matching bearer token or the trusted proxy secret"),
    )


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8] if value else ""


def diagnose_caller(
    *,
    x_orbital_role: str | None,
    x_orbital_actor: str | None,
    x_orbital_proxy_secret: str | None,
    authorization: str | None,
    session_cookie: str | None = None,
) -> dict:
    """Explain how this request's headers resolve, without revealing any secret. Fingerprints
    are the first 8 hex chars of SHA-256, so an operator can compare the value each side holds
    (``printf %s "$SECRET" | shasum -a 256``) and spot stray quotes or whitespace."""
    sent = _strip_wrapping_quotes(x_orbital_proxy_secret or "")
    expected = _strip_wrapping_quotes(os.getenv("ORBITAL_RBAC_PROXY_SECRET") or "")
    try:
        role: str | None = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization,
                                        x_orbital_proxy_secret=x_orbital_proxy_secret,
                                        session_cookie=session_cookie).value
        error = None
    except HTTPException as exc:
        role, error = None, str(exc.detail)
    return {
        "role": role,
        "error": error,
        "can_estop": role in (Role.AGENT.value, Role.OPERATOR.value, Role.ADMIN.value),
        "rbac_enforced": rbac_enforced(),
        "role_header": x_orbital_role,
        "actor_header": x_orbital_actor,
        "bearer_sent": bool(authorization),
        "signed_in": sessions.read(session_cookie) is not None,
        "proxy_secret_configured": bool(expected),
        "proxy_secret_sent": bool(sent),
        "proxy_secret_matches": _proxy_trusted(x_orbital_proxy_secret),
        "proxy_secret_sent_fingerprint": _fingerprint(sent),
        "proxy_secret_expected_fingerprint": _fingerprint(expected),
    }


def role_for_token(token: str) -> Role | None:
    return _token_role_map().get(_strip_wrapping_quotes(token))


def _credential_role(authorization: str | None, session_cookie: str | None = None) -> Role:
    if authorization and authorization.lower().startswith("bearer "):
        mapped = role_for_token(authorization[7:])
        if mapped is not None:
            return mapped

    session = sessions.read(session_cookie)
    if session is not None:
        try:
            return Role(session.role)
        except ValueError:
            pass

    if not rbac_enforced():
        return Role.ADMIN  # permissive default for demos/tests

    if anon_viewer_allowed():
        return Role.VIEWER  # public map / marketing embed

    raise HTTPException(status_code=401, detail="RBAC bearer token required")


def require_min_role(minimum: Role):
    """FastAPI dependency factory: require role >= minimum when RBAC is enforced."""

    async def _dep(
        x_orbital_role: str | None = Header(default=None, alias="X-Orbital-Role"),
        x_orbital_proxy_secret: str | None = Header(default=None, alias="X-Orbital-Proxy-Secret"),
        authorization: str | None = Header(default=None),
        orbital_session: str | None = Cookie(default=None),
    ) -> Role:
        role = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization,
                            x_orbital_proxy_secret=x_orbital_proxy_secret, session_cookie=orbital_session)
        _check_agents_enabled(role)
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


def agents_enabled() -> bool:
    return _env_flag("ORBITAL_AGENTS_ENABLED", "1")


def _check_agents_enabled(role: Role) -> None:
    if role == Role.AGENT and not agents_enabled():
        raise HTTPException(status_code=403, detail="agent access is disabled on this deployment (ORBITAL_AGENTS_ENABLED=0)")


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


def _caller(role: Role, x_orbital_actor: str | None, session_cookie: str | None = None) -> Caller:
    session = sessions.read(session_cookie)
    label = _strip_wrapping_quotes(x_orbital_actor or "")[:64] or (session.actor if session else "")
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
        x_orbital_proxy_secret: str | None = Header(default=None, alias="X-Orbital-Proxy-Secret"),
        authorization: str | None = Header(default=None),
        orbital_session: str | None = Cookie(default=None),
    ) -> Caller:
        role = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization,
                            x_orbital_proxy_secret=x_orbital_proxy_secret, session_cookie=orbital_session)
        caller = _caller(role, x_orbital_actor, orbital_session)
        if role == Role.AGENT:
            if not agents_enabled():
                raise _reject_agent(request, caller, 403,
                                    "agent access is disabled on this deployment (ORBITAL_AGENTS_ENABLED=0)")
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
    x_orbital_proxy_secret: str | None = Header(default=None, alias="X-Orbital-Proxy-Secret"),
    authorization: str | None = Header(default=None),
    orbital_session: str | None = Cookie(default=None),
) -> Caller:
    role = resolve_role(x_orbital_role=x_orbital_role, authorization=authorization,
                        x_orbital_proxy_secret=x_orbital_proxy_secret, session_cookie=orbital_session)
    _check_agents_enabled(role)
    return _caller(role, x_orbital_actor, orbital_session)
