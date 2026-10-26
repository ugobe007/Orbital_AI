"""Sprint F — agent role, proposals, direct-control gate, rate limit, dry-run, audit, MCP."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from orbital_cloud.main import app
from orbital_cloud.models import RecommendationKind, RecommendationStatus, RobotState
from orbital_cloud.rbac import agent_limiter
from orbital_cloud.store import store
from orbital_mcp.client import OrbitalClient

AGENT = {"X-Orbital-Role": "agent", "X-Orbital-Actor": "test-bot"}
agent = TestClient(app, headers=AGENT)
operator = TestClient(app, headers={"X-Orbital-Role": "operator", "X-Orbital-Actor": "alice"})
admin = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("ORBITAL_AGENT_DIRECT_CONTROL", raising=False)
    monkeypatch.delenv("ORBITAL_AGENT_RATE_PER_MIN", raising=False)
    monkeypatch.delenv("ORBITAL_AGENTS_ENABLED", raising=False)
    agent_limiter.reset()
    yield
    agent_limiter.reset()


@pytest.fixture
def robot():
    """A seed robot (unmanaged vendor → permissive scopes) restored after the test."""
    rid = "rbt-05"
    r = store.robots[rid]
    saved = (r.state, r.error_code, r.halted_at, r.current_task, r.nav_queue[:], r.speed_mps)
    yield rid
    r.state, r.error_code, r.halted_at, r.current_task, r.nav_queue, r.speed_mps = saved


def _audit(actor: str) -> list[dict]:
    return admin.get("/api/dashboard/control-audit", params={"actor": actor}).json()["events"]


# ── Server-side agent rules ────────────────────────────────────────────────────

def test_agent_can_read_but_not_accept(robot):
    assert agent.get("/api/dashboard/fleet").status_code == 200
    rec = store.actions.propose(RecommendationKind.PRECHARGE, robot_id=robot, score=40,
                                rationale="agent accept test")
    r = agent.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert r.status_code == 403
    store.actions.resolve(rec.id, RecommendationStatus.EXPIRED)


def test_agent_proposal_is_attributed_and_operator_accept_executes(robot):
    store.robots[robot].state = RobotState.IDLE
    r = agent.post("/api/dashboard/actions", json={
        "kind": "dispatch_task", "robot_id": robot, "task": "Count bay C", "score": 70,
        "rationale": "Bay C count is overdue",
    })
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["source"] == "agent" and rec["actor"] == "agent:test-bot" and rec["scope"] == "mission.dispatch"

    listed = {a["id"] for a in operator.get("/api/dashboard/actions").json()}
    assert rec["id"] in listed

    ok = operator.post(f"/api/dashboard/actions/{rec['id']}/accept")
    assert ok.status_code == 200 and ok.json()["status"] == "done"
    assert store.robots[robot].current_task == "Count bay C"
    task = next(t for t in admin.get("/api/dashboard/tasks").json() if t["description"] == "Count bay C")
    assert task["actor"] == "alice"

    accepted = _audit("alice")[0]
    assert accepted["action"] == "accept:dispatch_task" and "agent:test-bot" in accepted["detail"]
    assert any(e["action"] == "propose_action" and e["ok"] for e in _audit("agent:test-bot"))


def test_proposal_validation():
    bad = agent.post("/api/dashboard/actions", json={
        "kind": "handover", "robot_id": "rbt-01", "rationale": "x"})
    assert bad.status_code == 422
    missing = agent.post("/api/dashboard/actions", json={
        "kind": "resume", "robot_id": "rbt-nope", "rationale": "x"})
    assert missing.status_code == 404


def test_direct_control_is_off_by_default(robot):
    r = agent.post("/api/dashboard/tasks", json={"robot_id": robot, "description": "go"})
    assert r.status_code == 403 and "propose_action" in r.json()["detail"]
    nav = agent.post(f"/api/dashboard/robot/{robot}/navigate", json={"waypoints": [{"x": 5, "y": 5}]})
    assert nav.status_code == 403
    denied = _audit("agent:test-bot")[0]
    assert denied["action"].endswith(f"/robot/{robot}/navigate") and denied["robot_id"] == robot
    assert not denied["ok"] and "ORBITAL_AGENT_DIRECT_CONTROL" in denied["detail"]


def test_direct_control_when_enabled(robot, monkeypatch):
    monkeypatch.setenv("ORBITAL_AGENT_DIRECT_CONTROL", "1")
    r = agent.post("/api/dashboard/tasks", json={"robot_id": robot, "description": "Restock A2"})
    assert r.status_code == 201 and r.json()["actor"] == "agent:test-bot"
    assert agent.get("/api/dashboard/agent-policy").json()["direct_control"] is True


def test_agent_estop_always_allowed_and_dry_run_is_side_effect_free(robot):
    dry = agent.post(f"/api/dashboard/robot/{robot}/estop", params={"dry_run": True})
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    assert store.robots[robot].state != RobotState.HALTED

    real = agent.post(f"/api/dashboard/robot/{robot}/estop")
    assert real.status_code == 200 and store.robots[robot].state == RobotState.HALTED

    rows = _audit("agent:test-bot")[:2]
    assert rows[0]["action"] == "estop" and not rows[0]["dry_run"]
    assert rows[1]["action"] == "estop" and rows[1]["dry_run"]


def test_agent_rate_limit_spares_estop(robot, monkeypatch):
    monkeypatch.setenv("ORBITAL_AGENT_RATE_PER_MIN", "2")
    body = {"kind": "reliability_review", "robot_id": robot, "rationale": "rate test", "dry_run": True}
    assert agent.post("/api/dashboard/actions", json=body).status_code == 201
    assert agent.post("/api/dashboard/actions", json=body).status_code == 201
    limited = agent.post("/api/dashboard/actions", json=body)
    assert limited.status_code == 429
    assert agent.post(f"/api/dashboard/robot/{robot}/estop", params={"dry_run": True}).status_code == 200


def test_viewer_still_cannot_control(robot):
    viewer = TestClient(app, headers={"X-Orbital-Role": "viewer"})
    assert viewer.post(f"/api/dashboard/robot/{robot}/estop").status_code == 403
    assert viewer.post("/api/dashboard/actions", json={
        "kind": "resume", "robot_id": robot, "rationale": "x"}).status_code == 403


def test_kill_switch_refuses_all_agent_calls(robot, monkeypatch):
    monkeypatch.setenv("ORBITAL_AGENTS_ENABLED", "0")
    assert agent.get("/api/dashboard/fleet").status_code == 403
    estop = agent.post(f"/api/dashboard/robot/{robot}/estop")
    assert estop.status_code == 403 and "ORBITAL_AGENTS_ENABLED" in estop.json()["detail"]
    assert store.robots[robot].state != RobotState.HALTED
    assert operator.get("/api/dashboard/fleet").status_code == 200


def test_prod_flags_report_agent_settings(monkeypatch):
    from orbital_cloud.secrets import prod_security_flags

    monkeypatch.setenv("ORBITAL_AGENT_DIRECT_CONTROL", "1")
    flags = prod_security_flags()
    assert flags["agents_enabled"] is True and flags["agent_direct_control"] is True


def test_control_audit_is_admin_only():
    assert agent.get("/api/dashboard/control-audit").status_code == 403
    assert operator.get("/api/dashboard/control-audit").status_code == 403


# ── MCP client + server ────────────────────────────────────────────────────────

def test_client_filters_and_passes_errors_verbatim(robot):
    client = OrbitalClient(agent)
    halted = client.list_robots(state="halted")
    assert all(r["state"] == "halted" for r in halted["robots"])

    denied = client.dispatch_task(robot, "go")
    assert denied["ok"] is False and denied["status"] == 403
    assert "ORBITAL_AGENT_DIRECT_CONTROL" in denied["error"]

    summary = client.fleet_summary()
    assert summary["agent_policy"]["direct_control"] is False and "summary" in summary


def test_mcp_server_registers_tools_and_calls_through():
    pytest.importorskip("mcp")
    from orbital_mcp.server import build_server

    mcp = build_server(lambda: OrbitalClient(agent))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"fleet_summary", "list_robots", "list_workers", "propose_action", "estop_robot", "navigate_robot"} <= names

    result = asyncio.run(mcp.call_tool("list_robots", {"vendor": "Unitree"}))
    assert "Unitree" in str(result)


def test_mcp_http_transport_requires_token():
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient as StarletteClient

    from orbital_mcp.server import build_server, http_app

    mcp = build_server(lambda: OrbitalClient(agent))
    with StarletteClient(http_app(mcp, "s3cret")) as http:
        assert http.post("/mcp", json={}).status_code == 401
        authed = http.post("/mcp", json={}, headers={"Authorization": "Bearer s3cret"})
        assert authed.status_code != 401
