"""Pudu Robotics — Open Platform HMAC-SHA1 REST (ApiAppKey / ApiAppSecret).

Auth follows Pudu's published signing sample (HMAC-SHA1 over ``x-date`` + method +
Accept + Content-Type + Content-MD5 + canonical path+query).

Public / partner surfaces (confirm exact task paths with your Pudu account docs):
  - Health: ``GET …/v1/api/healthCheck``
  - Test host: ``open-platform-test.pudutech.com``
  - Prod host: account-specific ``*.pudutech.com`` fixed public domain

Credentials (never commit)::

    ORBITAL_SECRET_PUDU_JSON={"app_key":"…","app_secret":"…"}
    # or ORBITAL_SECRET_PUDU_API_KEY + ORBITAL_SECRET_PUDU_APP_SECRET
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .base import OemApiClient, OemEndpoint

DEFAULT_PUDU_HOST = "https://open-platform-test.pudutech.com"
API_PREFIX = "/pudu-entry/data-open-platform-service"
GMT_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"


def encode_query_value(value: str) -> str:
    """Match Pudu sample encode() for special characters in query values."""
    out = value
    for src, dst in (
        ("%", "%25"),
        ("#", "%23"),
        ("=", "%3D"),
        ("&", "%26"),
        ("?", "%3F"),
        ("/", "%2F"),
        ("+", "%2B"),
        (" ", "%20"),
    ):
        out = out.replace(src, dst)
    return out


def canonicalize_path_for_sign(url: str) -> tuple[str, str, str]:
    """Return (host, full_url_for_request, path_for_signature).

    Signature path strips ``/release|/test|/prepub`` prefixes and appends
    lexicographically sorted query ``key=value`` pairs (comma-joined multi-values).
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    for prefix in ("/release", "/test", "/prepub"):
        if path.startswith(prefix):
            path = path[len(prefix) :] or "/"
            break
    if not path:
        path = "/"

    query = parsed.query
    if query:
        args: dict[str, list[str]] = {}
        for k, v in parse_qsl(query, keep_blank_values=True):
            args.setdefault(k, []).append(v)
        parts: list[str] = []
        for k in sorted(args):
            values = [v for v in args[k] if v != ""]
            if not values:
                parts.append(k)
            else:
                parts.append(f"{k}={','.join(values)}")
        path = f"{path}?{'&'.join(parts)}"

    return host, url, path


def content_md5_for_body(body: str) -> str:
    """Pudu Content-MD5: Base64( hex(MD5(body)) ) — matches their Go sample."""
    if not body:
        return ""
    md5_hex = hashlib.md5(body.encode("utf-8")).hexdigest()
    return base64.b64encode(md5_hex.encode("utf-8")).decode("ascii")


def build_pudu_authorization(
    *,
    app_key: str,
    app_secret: str,
    method: str,
    path_for_sign: str,
    x_date: str,
    accept: str = "application/json",
    content_type: str = "application/json",
    content_md5: str = "",
) -> str:
    signing_str = (
        f"x-date: {x_date}\n"
        f"{method.upper()}\n"
        f"{accept}\n"
        f"{content_type}\n"
        f"{content_md5}\n"
        f"{path_for_sign}"
    )
    digest = hmac.new(
        app_secret.encode("utf-8"),
        signing_str.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")
    return (
        f'hmac id="{app_key}", algorithm="hmac-sha1", '
        f'headers="x-date", signature="{signature}"'
    )


def gmt_now() -> str:
    return datetime.now(timezone.utc).strftime(GMT_FORMAT)


class PuduOpenPlatformClient(OemApiClient):
    vendor = "Pudu Robotics"
    sdk_package = "httpx + HMAC-SHA1 (no public PyPI SDK)"
    docs_home = "https://www.pudurobotics.com/"

    def __init__(
        self,
        robot_id: str,
        host: str = "",
        *,
        dry_run: bool = True,
        app_key: str = "",
        app_secret: str = "",
    ) -> None:
        super().__init__(robot_id, host or DEFAULT_PUDU_HOST, dry_run=dry_run)
        self.app_key = app_key
        self.app_secret = app_secret

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "Health check",
                "rest",
                f"GET {API_PREFIX}/v1/api/healthCheck",
                "https://www.pudurobotics.com/",
                "HMAC-signed ping; Pudu Open Platform sample",
            ),
            OemEndpoint(
                "Dispatch / call task",
                "rest",
                f"POST {API_PREFIX}/v1/api/robot/task",
                "https://www.pudurobotics.com/",
                "Waypoint inject — confirm exact path with account OpenAPI",
            ),
            OemEndpoint(
                "Robot status / pose",
                "rest",
                f"GET {API_PREFIX}/v1/api/robot/status",
                "https://www.pudurobotics.com/",
                "Internal pose / battery — confirm query params (sn / robot_id)",
            ),
            OemEndpoint(
                "Emergency stop",
                "rest",
                f"POST {API_PREFIX}/v1/api/robot/estop",
                "https://www.pudurobotics.com/",
                "Fleet/robot halt — confirm with partner docs",
            ),
        )

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        creds = credentials or {}
        self.app_key = (
            creds.get("app_key")
            or creds.get("api_key")
            or creds.get("ApiAppKey")
            or self.app_key
        )
        self.app_secret = (
            creds.get("app_secret")
            or creds.get("api_secret")
            or creds.get("ApiAppSecret")
            or self.app_secret
        )
        if creds.get("host"):
            self.host = creds["host"]
        self._record(
            "connect",
            {
                "host": self.host,
                "auth": "hmac-sha1" if self.app_key and self.app_secret else "missing",
                "app_key_set": bool(self.app_key),
            },
        )
        if not self.dry_run and not (self.app_key and self.app_secret):
            return False
        self.connected = True
        return True

    def _api_url(self, suffix: str, query: dict[str, str] | None = None) -> str:
        base = self.host.rstrip("/") + API_PREFIX + suffix
        if not query:
            return base
        # Encode values with Pudu rules before attaching
        q = "&".join(f"{k}={encode_query_value(v)}" for k, v in query.items())
        return f"{base}?{q}"

    def _signed_headers(self, method: str, url: str, body: str = "") -> dict[str, str]:
        host, _, path_for_sign = canonicalize_path_for_sign(url)
        x_date = gmt_now()
        content_md5 = content_md5_for_body(body) if method.upper() == "POST" else ""
        auth = build_pudu_authorization(
            app_key=self.app_key,
            app_secret=self.app_secret,
            method=method,
            path_for_sign=path_for_sign,
            x_date=x_date,
            content_md5=content_md5,
        )
        return {
            "Host": host,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-date": x_date,
            "Authorization": auth,
        }

    def _request(self, method: str, url: str, body: dict[str, Any] | None = None) -> tuple[bool, str]:
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = self._signed_headers(method, url, body_str)
        if self.dry_run or not self.connected:
            return True, ""
        try:
            import httpx

            r = httpx.request(
                method.upper(),
                url,
                content=body_str.encode("utf-8") if body_str else None,
                headers=headers,
                timeout=15.0,
            )
            return r.status_code < 400, r.text[:500]
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def health_check(self, *, a: str = "1", b: str = "2", c: str = "3") -> bool:
        url = self._api_url("/v1/api/healthCheck", {"a": a, "b": b, "c": c})
        ok, detail = self._request("GET", url)
        self._record("health_check", {"url": url, "ok": ok, "detail": detail}, ok=ok)
        return ok

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        body = {
            "robot_id": self.robot_id,
            "sn": self.robot_id,
            "type": "navigate",
            "waypoint": {"x": x, "y": y, "theta": theta},
        }
        url = self._api_url("/v1/api/robot/task")
        self._record(
            "inject_waypoint",
            {"method": "POST", "path": f"{API_PREFIX}/v1/api/robot/task", "body": body},
        )
        if self.dry_run or not self.connected:
            return self.dry_run or self.connected
        ok, detail = self._request("POST", url, body)
        self.calls[-1].ok = ok
        self.calls[-1].detail = detail
        return ok

    def get_internal_pose(self) -> dict[str, float]:
        url = self._api_url("/v1/api/robot/status", {"sn": self.robot_id})
        self._record(
            "get_internal_pose",
            {"method": "GET", "path": f"{API_PREFIX}/v1/api/robot/status", "sn": self.robot_id},
        )
        if self.dry_run or not self.connected:
            return {"x": 0.0, "y": 0.0, "theta": 0.0}
        ok, detail = self._request("GET", url)
        self.calls[-1].ok = ok
        self.calls[-1].detail = detail
        return {"x": 0.0, "y": 0.0, "theta": 0.0}

    def trigger_estop(self) -> bool:
        body = {"robot_id": self.robot_id, "sn": self.robot_id, "action": "estop"}
        url = self._api_url("/v1/api/robot/estop")
        self._record(
            "trigger_estop",
            {"method": "POST", "path": f"{API_PREFIX}/v1/api/robot/estop", "body": body},
        )
        if self.dry_run or not self.connected:
            return True
        ok, detail = self._request("POST", url, body)
        self.calls[-1].ok = ok
        self.calls[-1].detail = detail
        return ok
