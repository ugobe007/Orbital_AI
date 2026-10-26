"""Console sign-in sessions.

An operator signs in to the dashboard with one of the ``ORBITAL_RBAC_TOKENS`` access keys and
gets a signed, HttpOnly cookie instead of keeping the key in the browser. The cookie is
stateless (role, actor and expiry plus an HMAC), so sessions survive a redeploy. The signing
key is ``ORBITAL_SESSION_SECRET`` when set, otherwise it is derived from the token list, so
rotating the access keys signs everyone out.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

COOKIE_NAME = "orbital_session"
TTL_S = 12 * 3600


@dataclass(frozen=True)
class Session:
    role: str
    actor: str
    expires_at: float


def _key() -> Optional[bytes]:
    secret = (os.getenv("ORBITAL_SESSION_SECRET") or "").strip()
    if secret:
        return secret.encode()
    tokens = (os.getenv("ORBITAL_RBAC_TOKENS") or "").strip()
    if tokens:
        return hashlib.sha256(b"orbital-session:" + tokens.encode()).digest()
    return None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(role: str, actor: str, ttl_s: int = TTL_S) -> Optional[str]:
    key = _key()
    if key is None:
        return None
    payload = _b64(json.dumps({"r": role, "a": actor, "e": int(time.time()) + ttl_s}).encode())
    sig = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read(cookie: Optional[str]) -> Optional[Session]:
    key = _key()
    if not cookie or key is None or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
        session = Session(role=str(data["r"]), actor=str(data["a"]), expires_at=float(data["e"]))
    except (ValueError, KeyError, TypeError):
        return None
    if session.expires_at < time.time():
        return None
    return session
