"""Console sign-in: trade an operator/admin access key for a session cookie."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import sessions
from ..rbac import Role, _RateLimiter, _token_role_map, role_for_token
from ..store import store

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_limiter = _RateLimiter()
_LOGIN_ATTEMPTS_PER_MIN = 10


class LoginIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    name: Optional[str] = Field(default=None, max_length=40)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _actor(name: Optional[str], role: Role) -> str:
    clean = re.sub(r"[^\w .@-]", "", (name or "").strip())[:40]
    return f"console:{clean or role.value}"


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    if not _token_role_map():
        raise HTTPException(status_code=503, detail=(
            "sign-in is not configured on this deployment: set ORBITAL_RBAC_TOKENS "
            "(e.g. admin:<long-random-key>)"))
    ip = _client_ip(request)
    if not _login_limiter.check(f"login:{ip}", _LOGIN_ATTEMPTS_PER_MIN):
        raise HTTPException(status_code=429, detail="too many sign-in attempts; wait a minute")
    role = role_for_token(body.key)
    if role not in (Role.OPERATOR, Role.ADMIN):
        store.record_control(actor=f"console:{ip}", role="viewer", action="sign_in",
                             robot_id=None, ok=False, detail="access key not recognized")
        raise HTTPException(status_code=401, detail="access key not recognized")
    actor = _actor(body.name, role)
    cookie = sessions.issue(role.value, actor)
    assert cookie is not None
    response.set_cookie(sessions.COOKIE_NAME, cookie, max_age=sessions.TTL_S, httponly=True,
                        secure=_is_https(request), samesite="strict", path="/")
    store.record_control(actor=actor, role=role.value, action="sign_in", robot_id=None, ok=True)
    return {"signed_in": True, "role": role.value, "actor": actor}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    return {"signed_in": False}


@router.get("/me")
async def me(orbital_session: Optional[str] = Cookie(default=None)) -> dict:
    session = sessions.read(orbital_session)
    return {
        "sign_in_available": bool(_token_role_map()),
        "signed_in": session is not None,
        "role": session.role if session else None,
        "actor": session.actor if session else None,
        "expires_at": session.expires_at if session else None,
    }
