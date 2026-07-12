"""Autonomy layer — deterministic policy tests for the Orbital AI Orchestrator."""
from fastapi.testclient import TestClient

from orbital_cloud.main import app
from orbital_cloud.models import AlertIn, AlertSeverity, AlertType, OrchestratorAction, RobotState
from orbital_cloud.orchestrator import Orchestrator
from orbital_cloud.store import Store


def _fresh() -> tuple[Store, Orchestrator, str]:
    st = Store()
    orch = Orchestrator(st)
    rid = next(iter(st.robots))
    return st, orch, rid


def test_low_battery_dispatches_charge():
    st, orch, rid = _fresh()
    st.robots[rid].state = RobotState.ACTIVE
    st.robots[rid].battery_pct = 10.0

    status = orch.evaluate()

    assert st.robots[rid].state == RobotState.CHARGING
    assert any(d.action == OrchestratorAction.DISPATCH_CHARGE and d.auto_executed for d in status.decisions)


def test_auto_estop_on_critical_anomaly():
    st, orch, rid = _fresh()
    st.robots[rid].state = RobotState.ACTIVE
    st.add_alert(AlertIn(robot_id=rid, type=AlertType.HIJACK_SUSPECTED, severity=AlertSeverity.CRITICAL, message="cameras see motion"))

    status = orch.evaluate()

    assert st.robots[rid].state == RobotState.HALTED
    decisions = [d for d in status.decisions if d.action == OrchestratorAction.AUTO_ESTOP]
    assert decisions and decisions[0].auto_executed and decisions[0].robot_id == rid


def test_auto_estop_is_idempotent_per_alert():
    st, orch, rid = _fresh()
    st.robots[rid].state = RobotState.ACTIVE
    st.add_alert(AlertIn(robot_id=rid, type=AlertType.GHOST_COMMAND, severity=AlertSeverity.CRITICAL, message="robot reports motion, cameras still"))

    orch.evaluate()
    orch.evaluate()  # second pass must not re-halt or duplicate the decision

    estops = [d for d in orch.status().decisions if d.action == OrchestratorAction.AUTO_ESTOP]
    assert len(estops) == 1


def test_degraded_drift_is_advisory_not_halting():
    st, orch, rid = _fresh()
    st.robots[rid].state = RobotState.ACTIVE
    # Between degraded (0.1) and halt (0.5) thresholds.
    st.robots[rid].drift_delta_m = 0.2

    status = orch.evaluate()

    assert st.robots[rid].state != RobotState.HALTED
    review = [d for d in status.decisions if d.action == OrchestratorAction.RECOMMEND_REVIEW]
    assert review and not review[0].auto_executed


def test_nominal_fleet_takes_no_action():
    st, orch, _ = _fresh()  # all seed robots idle, full battery, zero drift

    status = orch.evaluate()

    assert not [d for d in status.decisions if d.auto_executed]
    assert "nominal" in status.narrative.lower()
    assert status.summary is not None and status.summary.total == len(st.robots)


def test_orchestrator_endpoints_exposed():
    client = TestClient(app)
    body = client.get("/api/dashboard/orchestrator").json()
    assert set(("enabled", "llm_enabled", "decisions")) <= set(body)

    run = client.post("/api/dashboard/orchestrator/run")
    assert run.status_code == 200
    assert "summary" in run.json()
