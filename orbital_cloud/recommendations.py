"""Next-best-action queue (Sprint E).

The orchestrator's Policy 4 proposes ranked recommendations here; operators (and, from
Sprint F, external agents) accept or dismiss them. Proposals never execute on their own —
``execute`` runs only on an explicit accept, after the caller has enforced the OEM scope.

Dedupe key is ``(kind, robot_id)``: re-proposing refreshes the open item instead of stacking
duplicates. A dismissed policy item stays quiet for ``DISMISS_COOLDOWN_S`` so the operator
isn't nagged every tick.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import TYPE_CHECKING, Optional

from .config import settings
from .models import (
    APIScope,
    AssigneeType,
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    RobotState,
    TaskIn,
)

if TYPE_CHECKING:
    from .store import Store

DEFAULT_TTL_S = 600.0
DISMISS_COOLDOWN_S = 300.0
_MAX_ITEMS = 100

Key = tuple[RecommendationKind, Optional[str]]


class RecommendationQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, Recommendation] = {}
        self._dismissed_at: dict[Key, float] = {}

    def propose(
        self,
        kind: RecommendationKind,
        *,
        score: float,
        rationale: str,
        robot_id: Optional[str] = None,
        target_robot_id: Optional[str] = None,
        task: Optional[str] = None,
        assignee_type: AssigneeType = AssigneeType.ROBOT,
        assignee_id: Optional[str] = None,
        scope: Optional[APIScope] = None,
        source: str = "policy",
        actor: Optional[str] = None,
        ttl_s: float = DEFAULT_TTL_S,
        now: Optional[float] = None,
    ) -> Optional[Recommendation]:
        """Add or refresh an open recommendation. Returns None while a dismissal cools down."""
        now = time.time() if now is None else now
        key: Key = (kind, robot_id)
        with self._lock:
            dismissed = self._dismissed_at.get(key)
            if dismissed is not None and now - dismissed < DISMISS_COOLDOWN_S:
                return None
            existing = self._open_by_key(key)
            if existing is not None:
                existing.score = round(score, 1)
                existing.rationale = rationale
                existing.target_robot_id = target_robot_id
                existing.task = task
                existing.assignee_id = assignee_id
                existing.updated_at = now
                existing.expires_at = now + ttl_s
                return existing
            rec = Recommendation(
                id=f"act-{uuid.uuid4().hex[:10]}", kind=kind, robot_id=robot_id,
                target_robot_id=target_robot_id, task=task, score=round(score, 1),
                rationale=rationale, assignee_type=assignee_type, assignee_id=assignee_id, scope=scope,
                source=source, actor=actor, created_at=now, updated_at=now,
                expires_at=now + ttl_s,
            )
            self._items[rec.id] = rec
            self._trim()
            return rec

    def sync_policy(self, live_keys: set[Key], now: Optional[float] = None) -> int:
        """Expire open policy items whose condition no longer holds, plus anything past its
        TTL. Returns the number expired."""
        now = time.time() if now is None else now
        expired = 0
        with self._lock:
            for rec in self._items.values():
                if rec.status != RecommendationStatus.PROPOSED:
                    continue
                stale = rec.source == "policy" and (rec.kind, rec.robot_id) not in live_keys
                timed_out = rec.expires_at is not None and rec.expires_at <= now
                if stale or timed_out:
                    rec.status = RecommendationStatus.EXPIRED
                    rec.updated_at = now
                    expired += 1
        return expired

    def get(self, rec_id: str) -> Optional[Recommendation]:
        with self._lock:
            return self._items.get(rec_id)

    def ranked(self, *, include_closed: bool = False, limit: int = 50) -> list[Recommendation]:
        with self._lock:
            items = [
                r for r in self._items.values()
                if include_closed or r.status == RecommendationStatus.PROPOSED
            ]
        items.sort(key=lambda r: (r.status != RecommendationStatus.PROPOSED, -r.score, r.created_at))
        return [r.model_copy() for r in items[:limit]]

    def resolve(self, rec_id: str, status: RecommendationStatus, now: Optional[float] = None) -> Optional[Recommendation]:
        now = time.time() if now is None else now
        with self._lock:
            rec = self._items.get(rec_id)
            if rec is None:
                return None
            rec.status = status
            rec.updated_at = now
            if status == RecommendationStatus.DISMISSED:
                self._dismissed_at[(rec.kind, rec.robot_id)] = now
            return rec

    def signature(self) -> tuple:
        """Cheap change detector for the WebSocket broadcast."""
        with self._lock:
            return tuple(sorted(
                (r.id, r.status.value, r.score) for r in self._items.values()
                if r.status == RecommendationStatus.PROPOSED
            ))

    def _open_by_key(self, key: Key) -> Optional[Recommendation]:
        for rec in self._items.values():
            if rec.status == RecommendationStatus.PROPOSED and (rec.kind, rec.robot_id) == key:
                return rec
        return None

    def _trim(self) -> None:
        if len(self._items) <= _MAX_ITEMS:
            return
        closed = sorted(
            (r for r in self._items.values() if r.status != RecommendationStatus.PROPOSED),
            key=lambda r: r.updated_at,
        )
        for rec in closed[: len(self._items) - _MAX_ITEMS]:
            del self._items[rec.id]


KIND_SCOPE: dict[RecommendationKind, Optional[APIScope]] = {
    RecommendationKind.RESUME: APIScope.ESTOP,
    RecommendationKind.HANDOVER: APIScope.MISSION,
    RecommendationKind.PRECHARGE: APIScope.MISSION,
    RecommendationKind.DISPATCH_TASK: APIScope.MISSION,
    RecommendationKind.RELIABILITY_REVIEW: None,
    RecommendationKind.ASSIST: None,
}


def assignee_for(kind: RecommendationKind) -> AssigneeType:
    if kind == RecommendationKind.ASSIST:
        return AssigneeType.PERSON
    return AssigneeType.OPERATOR if KIND_SCOPE[kind] is None else AssigneeType.ROBOT


def executing_robot(rec: Recommendation) -> Optional[str]:
    """The robot whose OEM scope gates this recommendation."""
    return rec.target_robot_id or rec.robot_id


def execute(st: "Store", rec: Recommendation, actor: Optional[str] = None) -> tuple[bool, str]:
    """Carry out an accepted recommendation. The caller has already enforced ``rec.scope``.
    Returns (executed, detail); False means the world moved on and the item is stale."""
    if rec.kind == RecommendationKind.RESUME:
        robot = st.robots.get(rec.robot_id or "")
        if robot is None or robot.state != RobotState.HALTED:
            return False, "robot is no longer halted"
        if robot.drift_delta_m > settings.drift_degraded_m:
            return False, (f"drift is {robot.drift_delta_m:.2f}m now, above "
                           f"{settings.drift_degraded_m:.2f}m; not safe to resume")
        st.resume(robot.id)
        return True, f"resumed {robot.id}"

    if rec.kind == RecommendationKind.HANDOVER:
        source = st.robots.get(rec.robot_id or "")
        target = st.robots.get(rec.target_robot_id or "")
        if source is None or target is None or not rec.task:
            return False, "robot not found"
        if target.state != RobotState.IDLE:
            return False, f"{target.id} is no longer idle"
        st.create_task(TaskIn(robot_id=target.id, description=rec.task), actor=actor)
        with st._lock:
            if source.current_task == rec.task:
                source.current_task = None
        return True, f"handed '{rec.task}' from {source.id} to {target.id}"

    if rec.kind == RecommendationKind.DISPATCH_TASK:
        robot = st.robots.get(rec.robot_id or "")
        if robot is None or not rec.task:
            return False, "robot not found"
        if robot.state in (RobotState.HALTED, RobotState.OFFLINE):
            return False, f"{robot.id} is {robot.state.value}"
        st.create_task(TaskIn(robot_id=robot.id, description=rec.task), actor=actor)
        return True, f"dispatched '{rec.task}' to {robot.id}"

    if rec.kind == RecommendationKind.PRECHARGE:
        if not st.dispatch_charge(rec.robot_id or ""):
            return False, "robot not found or halted"
        return True, f"sent {rec.robot_id} to charge"

    return False, f"{rec.kind.value} has nothing to execute"
