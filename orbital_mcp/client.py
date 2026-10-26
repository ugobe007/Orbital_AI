"""Thin HTTP client over the Orbital Cloud REST API, used by the MCP tools.

Every call goes through the public API as role ``agent``, so RBAC, OEM scope checks, the
direct-control flag, rate limits and audit attribution are all enforced server-side.
Errors come back as ``{"ok": False, "status": ..., "error": ...}`` so the model sees the
server's reason verbatim instead of a stack trace.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_URL = "http://127.0.0.1:8090"


class OrbitalClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def from_env(cls) -> "OrbitalClient":
        headers = {
            "X-Orbital-Role": "agent",
            "X-Orbital-Actor": os.getenv("ORBITAL_AGENT_NAME", "mcp"),
        }
        token = (os.getenv("ORBITAL_AGENT_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        base = (os.getenv("ORBITAL_URL") or DEFAULT_URL).rstrip("/")
        return cls(httpx.Client(base_url=base, headers=headers, timeout=10.0))

    def _call(self, method: str, path: str, *, json: Any = None, params: Optional[dict] = None) -> Any:
        try:
            r = self._http.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            return {"ok": False, "status": None, "error": f"Orbital unreachable: {exc}"}
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            return {"ok": False, "status": r.status_code, "error": detail}
        return body

    # ── Reads ────────────────────────────────────────────────────────────────
    def fleet_summary(self) -> Any:
        orch = self._call("GET", "/api/dashboard/orchestrator")
        if isinstance(orch, dict) and orch.get("ok") is False:
            return orch
        actions = self._call("GET", "/api/dashboard/actions", params={"limit": 3})
        policy = self._call("GET", "/api/dashboard/agent-policy")
        return {
            "summary": orch.get("summary"),
            "narrative": orch.get("narrative"),
            "recent_decisions": orch.get("decisions", [])[:5],
            "top_actions": actions,
            "agent_policy": policy,
        }

    def list_robots(self, state: Optional[str] = None, vendor: Optional[str] = None) -> Any:
        fleet = self._call("GET", "/api/dashboard/fleet")
        if isinstance(fleet, dict) and fleet.get("ok") is False:
            return fleet
        robots = []
        for r in fleet.get("robots", []):
            if state and r["state"] != state:
                continue
            if vendor and r["vendor"].lower() != vendor.lower():
                continue
            robots.append({
                "id": r["id"], "vendor": r["vendor"], "model": r["model"], "state": r["state"],
                "battery_pct": r["battery_pct"], "drift_m": r["drift_delta_m"],
                "task": r.get("current_task"), "control_mode": r.get("control_mode"),
                "pose": {"x": r["pose_external"]["x"], "y": r["pose_external"]["y"]},
            })
        return {"facility": fleet.get("facility"), "robots": robots}

    def get_robot(self, robot_id: str) -> Any:
        return self._call("GET", f"/api/dashboard/robot/{robot_id}")

    def get_map(self) -> Any:
        return self._call("GET", "/api/dashboard/map")

    def list_alerts(self, limit: int = 20, unacknowledged_only: bool = True) -> Any:
        alerts = self._call("GET", "/api/dashboard/alerts", params={"limit": max(1, min(limit, 200))})
        if isinstance(alerts, list) and unacknowledged_only:
            alerts = [a for a in alerts if not a.get("acknowledged")]
        return alerts

    def list_actions(self, include_closed: bool = False) -> Any:
        return self._call("GET", "/api/dashboard/actions", params={"include_closed": include_closed})

    def benchmark(self, vendor: Optional[str] = None) -> Any:
        if vendor:
            return self._call("GET", f"/api/dashboard/benchmark/{vendor}")
        return self._call("GET", "/api/dashboard/benchmark")

    def agent_policy(self) -> Any:
        return self._call("GET", "/api/dashboard/agent-policy")

    def list_runs(self, task_key: Optional[str] = None, robot_id: Optional[str] = None,
                  limit: int = 20) -> Any:
        params: dict = {"limit": max(1, min(limit, 200))}
        if task_key:
            params["task_key"] = task_key
        if robot_id:
            params["robot_id"] = robot_id
        return self._call("GET", "/api/dashboard/runs", params=params)

    # ── Writes ───────────────────────────────────────────────────────────────
    def propose_action(self, kind: str, robot_id: str, rationale: str, *,
                       task: Optional[str] = None, target_robot_id: Optional[str] = None,
                       score: float = 50.0, dry_run: bool = False) -> Any:
        body = {"kind": kind, "robot_id": robot_id, "rationale": rationale, "score": score,
                "task": task, "target_robot_id": target_robot_id, "dry_run": dry_run}
        return self._call("POST", "/api/dashboard/actions", json=body)

    def estop_robot(self, robot_id: str, dry_run: bool = False) -> Any:
        return self._call("POST", f"/api/dashboard/robot/{robot_id}/estop", params={"dry_run": dry_run})

    def resume_robot(self, robot_id: str, dry_run: bool = False) -> Any:
        return self._call("POST", f"/api/dashboard/robot/{robot_id}/resume", params={"dry_run": dry_run})

    def dispatch_task(self, robot_id: str, description: str,
                      waypoints: Optional[list[list[float]]] = None, dry_run: bool = False) -> Any:
        body = {"robot_id": robot_id, "description": description,
                "waypoints": [{"x": p[0], "y": p[1]} for p in (waypoints or [])]}
        return self._call("POST", "/api/dashboard/tasks", json=body, params={"dry_run": dry_run})

    def navigate_robot(self, robot_id: str, waypoints: list[list[float]], dry_run: bool = False) -> Any:
        body = {"waypoints": [{"x": p[0], "y": p[1]} for p in waypoints]}
        return self._call("POST", f"/api/dashboard/robot/{robot_id}/navigate", json=body,
                          params={"dry_run": dry_run})

    def set_speed(self, robot_id: str, speed_mps: float, dry_run: bool = False) -> Any:
        return self._call("POST", f"/api/dashboard/robot/{robot_id}/speed",
                          json={"speed_mps": speed_mps}, params={"dry_run": dry_run})
