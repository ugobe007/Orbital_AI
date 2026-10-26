"""Sprint E — next-best-action queue: Policy 4 proposals, queue semantics, and the API."""
import time

import pytest
from fastapi.testclient import TestClient

from orbital_cloud import scope_guard
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.models import (
    Pose,
    RecommendationKind,
    RecommendationStatus,
    RobotState,
)
from orbital_cloud.orchestrator import Orchestrator
from orbital_cloud.recommendations import DISMISS_COOLDOWN_S, RecommendationQueue
from orbital_cloud.store import Store, store as global_store


def _fresh() -> tuple[Store, Orchestrator, list[str]]:
    st = Store()
    return st, Orchestrator(st), list(st.robots)


def _operator_estop(st: Store, rid: str, held_for_s: float) -> None:
    st.estop(rid)
    st.robots[rid].halted_at = time.time() - held_for_s


def _open(st: Store, kind: RecommendationKind) -> list:
    return [a for a in st.actions.ranked() if a.kind == kind]


# ── Policy 4 proposals ─────────────────────────────────────────────────────────

def test_resume_proposed_only_after_operator_halt_settles():
    st, orch, ids = _fresh()
    rid = ids[0]

    _operator_estop(st, rid, held_for_s=10)
    orch.evaluate()
    assert not _open(st, RecommendationKind.RESUME)

    st.robots[rid].halted_at = time.time() - 90
    orch.evaluate()
    [rec] = _open(st, RecommendationKind.RESUME)
    assert rec.robot_id == rid and rec.scope.value == "control.estop"
    assert st.robots[rid].state == RobotState.HALTED  # proposals never execute


def test_resume_not_proposed_for_auto_halts_or_high_drift():
    st, orch, ids = _fresh()
    st.estop(ids[0], auto=True)
    st.robots[ids[0]].halted_at = time.time() - 120
    _operator_estop(st, ids[1], held_for_s=120)
    st.robots[ids[1]].drift_delta_m = settings.drift_degraded_m + 0.05

    orch.evaluate()

    assert not _open(st, RecommendationKind.RESUME)


def test_handover_targets_nearest_idle_peer_and_executes():
    st, orch, ids = _fresh()
    halted, near, far = ids[0], ids[1], ids[2]
    for rid in ids:
        st.robots[rid].state = RobotState.ACTIVE
    st.robots[near].state = RobotState.IDLE
    st.robots[far].state = RobotState.IDLE
    st.robots[halted].pose_external = Pose(x=0, y=0)
    st.robots[near].pose_external = Pose(x=3, y=4)
    st.robots[far].pose_external = Pose(x=30, y=40)
    st.robots[halted].current_task = "Pick bay C3"
    _operator_estop(st, halted, held_for_s=120)

    orch.evaluate()
    [rec] = _open(st, RecommendationKind.HANDOVER)
    assert rec.target_robot_id == near and rec.task == "Pick bay C3"
    assert "5.0m" in rec.rationale

    from orbital_cloud.recommendations import execute
    ok, _ = execute(st, rec)
    assert ok
    assert st.robots[near].current_task == "Pick bay C3"
    assert st.robots[near].state == RobotState.ACTIVE
    assert st.robots[halted].current_task is None


def test_placeholder_tasks_are_not_handed_over():
    st, orch, ids = _fresh()
    st.robots[ids[0]].current_task = "Autonomous patrol"
    _operator_estop(st, ids[0], held_for_s=120)

    orch.evaluate()

    assert not _open(st, RecommendationKind.HANDOVER)


def test_precharge_band_proposes_while_policy_2_handles_low_battery():
    st, orch, ids = _fresh()
    st.robots[ids[0]].state = RobotState.ACTIVE
    st.robots[ids[0]].battery_pct = settings.low_battery_pct + 5
    st.robots[ids[1]].state = RobotState.ACTIVE
    st.robots[ids[1]].battery_pct = settings.low_battery_pct - 5

    orch.evaluate()

    recs = _open(st, RecommendationKind.PRECHARGE)
    assert [r.robot_id for r in recs] == [ids[0]]
    assert st.robots[ids[0]].state == RobotState.ACTIVE
    assert st.robots[ids[1]].state == RobotState.CHARGING


def test_reliability_review_flags_only_fleet_outliers():
    st, orch, ids = _fresh()
    for rid in ids:
        st.robots[rid].created_at = time.time() - 200
        st.robots[rid].degradation_events = 4    # every robot degrades about once every 50s
    st.robots[ids[0]].degradation_events = 20    # this one about once every 10s

    orch.evaluate()

    [rec] = _open(st, RecommendationKind.RELIABILITY_REVIEW)
    assert rec.robot_id == ids[0]
    assert rec.assignee_type.value == "operator" and rec.scope is None
    assert "fleet median" in rec.rationale


def test_proposals_dedupe_and_expire_when_condition_clears():
    st, orch, ids = _fresh()
    _operator_estop(st, ids[0], held_for_s=90)

    orch.evaluate()
    first = _open(st, RecommendationKind.RESUME)
    orch.evaluate()
    second = _open(st, RecommendationKind.RESUME)
    assert len(first) == len(second) == 1 and first[0].id == second[0].id

    st.resume(ids[0])
    orch.evaluate()
    assert not _open(st, RecommendationKind.RESUME)
    closed = st.actions.get(first[0].id)
    assert closed.status == RecommendationStatus.EXPIRED


def test_ranking_orders_by_score():
    st, orch, ids = _fresh()
    st.robots[ids[0]].state = RobotState.ACTIVE
    st.robots[ids[0]].battery_pct = settings.low_battery_pct + 14  # low pre-charge score
    _operator_estop(st, ids[1], held_for_s=300)                       # high resume score

    orch.evaluate()

    ranked = st.actions.ranked()
    assert ranked[0].kind == RecommendationKind.RESUME
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)


def test_narrative_mentions_top_suggestion():
    st, orch, ids = _fresh()
    _operator_estop(st, ids[0], held_for_s=90)

    status = orch.evaluate()

    assert "Suggested next:" in status.narrative and "nominal" not in status.narrative.lower()


# ── Queue semantics ────────────────────────────────────────────────────────────

def test_dismissed_item_stays_quiet_during_cooldown():
    q = RecommendationQueue()
    now = 1000.0
    rec = q.propose(RecommendationKind.PRECHARGE, robot_id="r1", score=50, rationale="x", now=now)
    q.resolve(rec.id, RecommendationStatus.DISMISSED, now=now)

    assert q.propose(RecommendationKind.PRECHARGE, robot_id="r1", score=50, rationale="x", now=now + 10) is None
    again = q.propose(RecommendationKind.PRECHARGE, robot_id="r1", score=50, rationale="x",
                      now=now + DISMISS_COOLDOWN_S + 1)
    assert again is not None and again.id != rec.id


def test_ttl_expires_agent_items_but_stale_check_skips_them():
    q = RecommendationQueue()
    rec = q.propose(RecommendationKind.PRECHARGE, robot_id="r1", score=50, rationale="x",
                    source="agent", ttl_s=60, now=0.0)

    q.sync_policy(set(), now=30.0)
    assert q.get(rec.id).status == RecommendationStatus.PROPOSED
    q.sync_policy(set(), now=61.0)
    assert q.get(rec.id).status == RecommendationStatus.EXPIRED


# ── API ────────────────────────────────────────────────────────────────────────

client = TestClient(app)


@pytest.fixture
def halted_robot():
    rid = list(global_store.robots)[-1]
    robot = global_store.robots[rid]
    saved = (robot.state, robot.error_code, robot.halted_at, robot.drift_delta_m)
    global_store.estop(rid)
    robot.drift_delta_m = 0.0
    yield rid
    robot.state, robot.error_code, robot.halted_at, robot.drift_delta_m = saved


def _propose_resume(rid: str):
    return global_store.actions.propose(
        RecommendationKind.RESUME, robot_id=rid, score=80, rationale="test resume",
        scope=scope_guard.APIScope.ESTOP,
    )


def test_accept_executes_through_scope_guard(halted_robot):
    rec = _propose_resume(halted_robot)

    listed = client.get("/api/dashboard/actions").json()
    assert any(a["id"] == rec.id for a in listed)

    r = client.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert global_store.robots[halted_robot].state != RobotState.HALTED

    again = client.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert again.status_code == 409


def test_accept_denied_when_scope_missing(halted_robot, monkeypatch):
    denied = scope_guard.ScopeCheck(False, "OEM has not granted 'control.estop'", True, "Acme")
    monkeypatch.setattr(scope_guard, "check_robot", lambda *_: denied)
    rec = _propose_resume(halted_robot)

    listed = {a["id"]: a for a in client.get("/api/dashboard/actions").json()}
    assert "control.estop" in listed[rec.id]["blocked_reason"]

    r = client.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert r.status_code == 403
    assert global_store.actions.get(rec.id).status == RecommendationStatus.PROPOSED
    assert global_store.robots[halted_robot].state == RobotState.HALTED
    global_store.actions.resolve(rec.id, RecommendationStatus.EXPIRED)


def test_accept_stale_action_expires_it(halted_robot):
    rec = _propose_resume(halted_robot)
    global_store.resume(halted_robot)

    r = client.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert r.status_code == 409 and "no longer" in r.json()["detail"]
    assert global_store.actions.get(rec.id).status == RecommendationStatus.EXPIRED


def test_accept_resume_rechecks_drift(halted_robot):
    rec = _propose_resume(halted_robot)
    global_store.robots[halted_robot].drift_delta_m = settings.drift_degraded_m + 0.2

    r = client.post(f"/api/dashboard/actions/{rec.id}/accept")
    assert r.status_code == 409 and "not safe to resume" in r.json()["detail"]
    assert global_store.robots[halted_robot].state == RobotState.HALTED


def test_advisory_accept_and_dismiss_broadcast():
    rid = list(global_store.robots)[0]
    review = global_store.actions.propose(
        RecommendationKind.RELIABILITY_REVIEW, robot_id=rid, score=40, rationale="test review",
    )
    r = client.post(f"/api/dashboard/actions/{review.id}/accept")
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    other = global_store.actions.propose(
        RecommendationKind.PRECHARGE, robot_id=rid, score=45, rationale="test precharge",
    )
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "fleet"
        d = client.post(f"/api/dashboard/actions/{other.id}/dismiss")
        assert d.status_code == 200 and d.json()["status"] == "dismissed"
        msg = ws.receive_json()
    assert msg["type"] == "actions"
    assert all(a["id"] != other.id for a in msg["actions"])


def test_unknown_action_is_404():
    assert client.post("/api/dashboard/actions/act-nope/accept").status_code == 404
