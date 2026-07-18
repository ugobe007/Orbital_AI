"""AgiBot — public AimDK PncService HTTP-RPC (open.agibot.com).

Sources (public):
  - https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation
  - Default robot IP example: 192.168.100.110:53176
"""
from __future__ import annotations

from .base import OemApiClient, OemEndpoint

DEFAULT_PNC_PORT = 53176


class AgiBotAimdkClient(OemApiClient):
    vendor = "AgiBot"
    sdk_package = "httpx → AimDK PncService RPC (+ aimdk_msgs for ROS2 HAL)"
    docs_home = "https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation"

    def __init__(
        self,
        robot_id: str,
        host: str = "",
        *,
        dry_run: bool = True,
        map_id: int = 1,
        port: int = DEFAULT_PNC_PORT,
    ) -> None:
        super().__init__(robot_id, host or "192.168.100.110", dry_run=dry_run)
        self.map_id = map_id
        self.port = port

    @classmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        base = f"http://{{host}}:{DEFAULT_PNC_PORT}/rpc/aimdk.protocol.PncService"
        return (
            OemEndpoint(
                "PlanningNaviToPose2D", "http_rpc",
                f"{base}/PlanningNaviToPose2D",
                "https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation",
                "Primary inject — absolute pose in map frame (~0.4 m accuracy)",
            ),
            OemEndpoint(
                "PlanningNaviToGoal", "http_rpc",
                f"{base}/PlanningNaviToGoal",
                "https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation",
                "Navigate to pre-mapped target_id",
            ),
            OemEndpoint(
                "ActionCancel", "http_rpc",
                f"{base}/ActionCancel",
                "https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation",
                "Cancel running nav task",
            ),
            OemEndpoint(
                "ActionGetState", "http_rpc",
                f"{base}/ActionGetState",
                "https://open.agibot.com/docs/en/aimdk/a2/v2_1/dev_guide/07-08-navigation",
                "Poll task state",
            ),
        )

    def _rpc_url(self, method: str) -> str:
        return f"http://{self.host}:{self.port}/rpc/aimdk.protocol.PncService/{method}"

    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        del credentials
        self._record("connect", {
            "host": self.host,
            "port": self.port,
            "prereq": ["relocalized", "MC=RL_LOCOMOTION_DEFAULT"],
        })
        self.connected = True
        return True

    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        body = {
            "task_id": 0,
            "map_id": self.map_id,
            "pose": {"position": {"x": x, "y": y}, "angle": theta},
            "ackerman_mode": False,
        }
        url = self._rpc_url("PlanningNaviToPose2D")
        self._record("inject_waypoint", {"url": url, "body": body})
        if self.dry_run:
            return True
        try:
            import httpx

            r = httpx.post(url, json=body, timeout=10.0)
            ok = r.status_code < 400
            self.calls[-1].ok = ok
            self.calls[-1].detail = r.text[:200]
            return ok
        except Exception as exc:  # noqa: BLE001
            self.calls[-1].ok = False
            self.calls[-1].detail = str(exc)
            return False

    def get_internal_pose(self) -> dict[str, float]:
        self._record("get_internal_pose", {"note": "via AimDK HAL / localization topics"})
        return {"x": 0.0, "y": 0.0, "theta": 0.0, "timestamp": 0.0}

    def trigger_estop(self) -> bool:
        # Public docs emphasize ActionCancel for nav; hardware E-Stop is platform-specific.
        self._record("trigger_estop", {"url": self._rpc_url("ActionCancel"), "body": {"task_id": 0}})
        return True
