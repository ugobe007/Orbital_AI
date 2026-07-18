"""In-process fake OEM servers for Sprint B2 integration tests.

No real bosdyn / Arc SDKs — these record protocol-shaped calls so pytest can assert
lease → RobotCommand and REST POST /api/v1/tasks without network hardware.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient


# ── Boston Dynamics (gRPC-shaped, in-process) ─────────────────────────────────

@dataclass
class FakeBosdynLease:
    client_name: str
    acquired_at: float = field(default_factory=time.time)
    active: bool = True


@dataclass
class FakeBosdynServer:
    """Records LeaseService / RobotCommandService / EstopService call shapes."""

    leases: list[FakeBosdynLease] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    estops: list[dict[str, Any]] = field(default_factory=list)
    states: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire_lease(self, client_name: str = "aria") -> FakeBosdynLease:
        with self._lock:
            lease = FakeBosdynLease(client_name=client_name)
            self.leases.append(lease)
            return lease

    def robot_command(self, *, se2_trajectory: dict[str, Any], lease_required: bool = True) -> dict:
        with self._lock:
            if lease_required and not any(l.active for l in self.leases):
                raise RuntimeError("LeaseService.Acquire required before RobotCommand")
            payload = {
                "service": "RobotCommandService",
                "method": "RobotCommand",
                "command": "SE2TrajectoryCommand",
                "se2_trajectory": se2_trajectory,
                "op": "grpc.RobotCommandService.RobotCommand",
                "ts": time.time(),
            }
            self.commands.append(payload)
            return {"status": "STATUS_PROCESSING", "id": len(self.commands)}

    def get_robot_state(self) -> dict:
        with self._lock:
            state = {
                "service": "RobotStateService",
                "method": "GetRobotState",
                "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
                "battery_pct": 100.0,
                "ts": time.time(),
            }
            self.states.append(state)
            return state

    def trigger_estop(self) -> dict:
        with self._lock:
            payload = {
                "service": "EstopService",
                "method": "SetEstopConfig",
                "op": "grpc.EstopService.SetEstopConfig",
                "ts": time.time(),
            }
            self.estops.append(payload)
            for lease in self.leases:
                lease.active = False
            return payload

    @property
    def recorded_ops(self) -> list[str]:
        ops = [c["op"] for c in self.commands]
        ops.extend(e["op"] for e in self.estops)
        return ops


# ── Agility Arc (REST-shaped FastAPI app) ─────────────────────────────────────

def build_fake_arc_app() -> FastAPI:
    app = FastAPI(title="Fake Agility Arc")
    app.state.tasks = []
    app.state.estops = []

    def _auth(authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Arc API key required")

    @app.post("/api/v1/tasks")
    def create_task(body: dict, authorization: str | None = Header(default=None)) -> dict:
        _auth(authorization)
        task = {
            "id": f"task-{len(app.state.tasks) + 1}",
            "op": "rest.POST /api/v1/tasks",
            "body": body,
            "ts": time.time(),
        }
        app.state.tasks.append(task)
        return {"id": task["id"], "status": "accepted"}

    @app.get("/api/v1/robots/{robot_id}/state")
    def robot_state(robot_id: str, authorization: str | None = Header(default=None)) -> dict:
        _auth(authorization)
        return {
            "robot_id": robot_id,
            "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "battery_pct": 100.0,
            "state": "idle",
        }

    @app.post("/api/v1/robots/{robot_id}/estop")
    def robot_estop(robot_id: str, authorization: str | None = Header(default=None)) -> dict:
        _auth(authorization)
        payload = {"robot_id": robot_id, "op": "rest.POST /api/v1/robots/{id}/estop", "ts": time.time()}
        app.state.estops.append(payload)
        return {"ok": True}

    return app


@dataclass
class FakeArcServer:
    """Wraps the fake Arc FastAPI app with a TestClient."""

    api_key: str = "test-arc-key"
    app: FastAPI = field(default_factory=build_fake_arc_app)
    _client: TestClient | None = field(default=None, init=False, repr=False)

    @property
    def client(self) -> TestClient:
        if self._client is None:
            self._client = TestClient(self.app)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def post_task(self, body: dict) -> dict:
        r = self.client.post("/api/v1/tasks", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def get_state(self, robot_id: str) -> dict:
        r = self.client.get(f"/api/v1/robots/{robot_id}/state", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def estop(self, robot_id: str) -> dict:
        r = self.client.post(f"/api/v1/robots/{robot_id}/estop", headers=self._headers())
        r.raise_for_status()
        return r.json()

    @property
    def tasks(self) -> list:
        return list(self.app.state.tasks)

    @property
    def recorded_ops(self) -> list[str]:
        return [t["op"] for t in self.app.state.tasks]
