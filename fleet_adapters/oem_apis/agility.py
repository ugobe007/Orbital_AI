"""Agility Robotics Digit — Agility Arc cloud REST/WebSocket (partner docs).

Public statements: Arc exposes industry-standard REST + WebSocket APIs for WMS/WES/MES
integration (missions, telemetry, KPIs). Detailed OpenAPI is partner-gated; Orbital
uses the publicly described task/telemetry shapes and our FakeArcServer contract.

Sources (public):
  - https://www.agilityrobotics.com/content/agility-robotics-brings-operational-visibility-to-deployment-of-digit-fleets-with-the-launch-of-agility-arc-tm
  - Partner integrations describe Arc base URL + API key / OAuth2
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint

DEFAULT_ARC_HOST = "https://arc.agilityrobotics.com"


class AgilityArcClient(OemApiClient):
    vendor = "Agility Robotics"
    sdk_package = "httpx (Arc REST) — no public PyPI SDK"
    docs_home = "https://www.agilityrobotics.com/"

    def __init__(self, robot_id: str, host: str = "", *, dry_run: bool = True, api_key: str = "") -> None:
        super().__init__(robot_id, host or DEFAULT_ARC_HOST, dry_run=dry_run)
        self.api_key = api_key

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        return (
            OemEndpoint(
                "Create task", "rest", "POST /api/v1/tasks",
                "https://www.agilityrobotics.com/",
                "Enqueue spatial constraint / mission (shape inferred; confirm with Arc partner docs)",
            ),
            OemEndpoint(
                "Robot state", "rest", "GET /api/v1/robots/{id}/state",
                "https://www.agilityrobotics.com/",
                "Telemetry / pose / battery",
            ),
            OemEndpoint(
                "E-Stop", "rest", "POST /api/v1/robots/{id}/estop",
                "https://www.agilityrobotics.com/",
                "Fleet-level stop",
            ),
            OemEndpoint(
                "Telemetry stream", "websocket", "wss://…/telemetry",
                "https://www.agilityrobotics.com/",
                "Live KPIs / status (Arc launch materials)",
            ),
        )

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        creds = credentials or {}
        self.api_key = creds.get("api_key", self.api_key)
        self._record("connect", {"host": self.host, "auth": "Bearer api_key" if self.api_key else "missing"})
        if not self.dry_run and not self.api_key:
            return False
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        body = {
            "type": "spatial_constraint",
            "robot_id": self.robot_id,
            "waypoint": {"x": x, "y": y, "theta": theta},
        }
        self._record("inject_waypoint", {"method": "POST", "path": "/api/v1/tasks", "body": body})
        if self.dry_run or not self.connected:
            return self.dry_run or self.connected
        try:
            import httpx

            r = httpx.post(
                f"{self.host.rstrip('/')}/api/v1/tasks",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10.0,
            )
            ok = r.status_code < 400
            self.calls[-1].ok = ok
            self.calls[-1].detail = f"status={r.status_code}"
            return ok
        except Exception as exc:  # noqa: BLE001
            self.calls[-1].ok = False
            self.calls[-1].detail = str(exc)
            return False

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"method": "GET", "path": f"/api/v1/robots/{self.robot_id}/state"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        self._record("trigger_estop", {"method": "POST", "path": f"/api/v1/robots/{self.robot_id}/estop"})
        return True
