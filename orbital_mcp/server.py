"""Orbital AI MCP server — lets any MCP-capable model see the site and act on it.

    python -m orbital_mcp                      # stdio (Cursor, Claude Desktop, …)
    python -m orbital_mcp --transport http     # streamable HTTP on 127.0.0.1:8765/mcp

Environment:
    ORBITAL_URL             Orbital Cloud base URL (default http://127.0.0.1:8090)
    ORBITAL_AGENT_TOKEN     RBAC bearer token mapped to the ``agent`` role (when RBAC is on)
    ORBITAL_AGENT_NAME      attribution label recorded in the control audit (default "mcp")
    ORBITAL_MCP_HTTP_TOKEN  bearer token clients must send to the HTTP transport; required
                            when binding to a non-loopback host
"""
from __future__ import annotations

import argparse
import hmac
import os
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP

from .client import OrbitalClient

INSTRUCTIONS = """\
Orbital AI monitors and controls a mixed fleet of robots (several OEMs) in a facility, using
overhead cameras for ground-truth poses. Start with fleet_summary.

Rules of the road:
- Prefer propose_action. Proposals land in the operator's Next Actions queue; nothing runs
  until a human accepts. Explain your reasoning in `rationale` — the operator reads it.
- estop_robot is always available. Use it when a robot looks unsafe; say why.
- dispatch_task, navigate_robot, set_speed and resume_robot only work if the operator enabled
  direct agent control (see agent_policy). If they return 403, propose instead.
- Every robot's OEM grants specific API scopes. A 403 naming a scope means the OEM has not
  granted it; do not retry, tell the user.
- Use dry_run=true to check what a write would do without doing it.
- Map coordinates are metres on the facility floor (see get_map).
"""

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_server(client_factory: Callable[[], OrbitalClient] = OrbitalClient.from_env,
                 **settings: Any) -> FastMCP:
    client = client_factory()
    mcp = FastMCP("orbital-ai", instructions=INSTRUCTIONS, **settings)

    @mcp.tool()
    def fleet_summary() -> Any:
        """Fleet counts by state, the orchestrator's narrative, recent autonomous decisions,
        the top suggested next actions, and what agents are allowed to do here."""
        return client.fleet_summary()

    @mcp.tool()
    def list_robots(state: Optional[str] = None, vendor: Optional[str] = None) -> Any:
        """Robots with state, battery, drift (metres between camera and self-reported pose),
        current task and floor position. Filter by state (active, idle, charging, cooldown,
        halted, offline) or vendor."""
        return client.list_robots(state=state, vendor=vendor)

    @mcp.tool()
    def get_robot(robot_id: str) -> Any:
        """Full detail for one robot: reliability metrics, latest sensors, and which control
        scopes its OEM has granted (estop, velocity, mission)."""
        return client.get_robot(robot_id)

    @mcp.tool()
    def get_map() -> Any:
        """Facility floor plan: dimensions, racks/obstacles, named stations and charge pads."""
        return client.get_map()

    @mcp.tool()
    def list_alerts(limit: int = 20, unacknowledged_only: bool = True) -> Any:
        """Recent safety and drift alerts, newest first."""
        return client.list_alerts(limit=limit, unacknowledged_only=unacknowledged_only)

    @mcp.tool()
    def list_actions(include_closed: bool = False) -> Any:
        """The ranked Next Actions queue (orchestrator and agent proposals awaiting an operator)."""
        return client.list_actions(include_closed=include_closed)

    @mcp.tool()
    def benchmark(vendor: Optional[str] = None) -> Any:
        """Per-vendor drift and reliability benchmarks (mean/p95 drift, MTBD, recovery latency)."""
        return client.benchmark(vendor=vendor)

    @mcp.tool()
    def agent_policy() -> Any:
        """What agents may do on this deployment: direct control on/off and the write rate limit."""
        return client.agent_policy()

    @mcp.tool()
    def propose_action(kind: str, robot_id: str, rationale: str, task: Optional[str] = None,
                       target_robot_id: Optional[str] = None, score: float = 50.0,
                       dry_run: bool = False) -> Any:
        """Propose a next action for an operator to accept. kind is one of: resume,
        handover (needs target_robot_id and task), precharge, dispatch_task (needs task),
        reliability_review. score is 0-100 priority. Returns the queued item, including
        blocked_reason if the robot's OEM hasn't granted the scope it needs."""
        return client.propose_action(kind, robot_id, rationale, task=task,
                                     target_robot_id=target_robot_id, score=score, dry_run=dry_run)

    @mcp.tool()
    def estop_robot(robot_id: str, dry_run: bool = False) -> Any:
        """Emergency-stop a robot. Always available to agents (subject to the OEM's estop
        scope). The halt latches until an operator resumes it."""
        return client.estop_robot(robot_id, dry_run=dry_run)

    @mcp.tool()
    def resume_robot(robot_id: str, dry_run: bool = False) -> Any:
        """Resume a halted robot. Requires direct agent control; otherwise propose kind=resume."""
        return client.resume_robot(robot_id, dry_run=dry_run)

    @mcp.tool()
    def dispatch_task(robot_id: str, description: str,
                      waypoints: Optional[list[list[float]]] = None, dry_run: bool = False) -> Any:
        """Assign a task to a robot, optionally with [x, y] waypoints in metres. Requires
        direct agent control; otherwise propose kind=dispatch_task."""
        return client.dispatch_task(robot_id, description, waypoints=waypoints, dry_run=dry_run)

    @mcp.tool()
    def navigate_robot(robot_id: str, waypoints: list[list[float]], dry_run: bool = False) -> Any:
        """Drive a robot through [x, y] waypoints (metres) using camera-based visual control.
        Requires direct agent control and the OEM's control.velocity scope."""
        return client.navigate_robot(robot_id, waypoints, dry_run=dry_run)

    @mcp.tool()
    def set_speed(robot_id: str, speed_mps: float, dry_run: bool = False) -> Any:
        """Set a robot's commanded speed in m/s (clamped server-side). Requires direct agent
        control and the OEM's control.velocity scope."""
        return client.set_speed(robot_id, speed_mps, dry_run=dry_run)

    return mcp


class _BearerAuth:
    """ASGI guard for the HTTP transport: every request must carry the configured token."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(auth, self._expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


def http_app(mcp: FastMCP, token: Optional[str]) -> Any:
    app = mcp.streamable_http_app()
    return _BearerAuth(app, token) if token else app


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="orbital_mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        build_server().run("stdio")
        return

    token = (os.getenv("ORBITAL_MCP_HTTP_TOKEN") or "").strip() or None
    if token is None and args.host not in _LOOPBACK:
        parser.error("ORBITAL_MCP_HTTP_TOKEN is required when binding the HTTP transport off loopback")
    import uvicorn

    mcp = build_server(host=args.host, port=args.port)
    uvicorn.run(http_app(mcp, token), host=args.host, port=args.port, log_level="info")
