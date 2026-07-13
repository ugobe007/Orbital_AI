"""Orbital AI Orchestrator — the autonomous supervisory brain of the Cloud
Orchestration Layer (Module 6).

It runs on a fixed cadence and, from the fleet snapshot + open alerts, decides and
executes *safety-first* supervisory actions:

  1. Auto E-Stop on a critical, unhandled anomaly (drift/hijack/ghost-command).
  2. Proactive charge dispatch for a low-battery robot before it strands mid-task.
  3. Advisory "operator review" recommendations for degraded-but-safe drift.

The decision policy is deterministic and testable — it is the source of truth and
the *only* thing that ever takes an action. An optional LLM produces a human-readable
narrative on top (env-gated, fail-open); it is advisory and never gates a safety action.

Real deployments swap the in-memory store for Postgres/InfluxDB behind the same reads;
the policy code is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from .config import settings
from .events import hub
from .models import (
    AlertSeverity,
    AlertType,
    FleetSummary,
    OrchestratorAction,
    OrchestratorDecision,
    OrchestratorStatus,
    RobotState,
)
from .store import Store, store

logger = logging.getLogger(__name__)

# Critical alert types the orchestrator will auto-halt on if the robot is still moving.
_AUTO_HALT_TYPES = {AlertType.DRIFT_EXCEEDED, AlertType.HIJACK_SUSPECTED, AlertType.GHOST_COMMAND}

# Don't re-recommend review for the same robot more often than this.
_REVIEW_COOLDOWN_S = 30.0


class Orchestrator:
    def __init__(self, st: Store) -> None:
        self._store = st
        self._decisions: list[OrchestratorDecision] = []
        self._processed_alert_ids: set[str] = set()
        self._last_review_ts: dict[str, float] = {}
        self._last_run_ts: Optional[float] = None
        self._last_summary: Optional[FleetSummary] = None
        self._last_narrative: str = ""

    # ── Public API ────────────────────────────────────────────────────────────
    def evaluate(self) -> OrchestratorStatus:
        """Run one supervisory pass. Executes safety actions and records decisions."""
        now = time.time()
        new_decisions: list[OrchestratorDecision] = []

        fleet = {r.id: r for r in self._store.fleet()}
        summary = self._summarize(list(fleet.values()))

        # Policy 1 — auto E-Stop on a critical, not-yet-handled anomaly.
        for alert in self._store.unacknowledged_alerts():
            if alert.id in self._processed_alert_ids:
                continue
            if alert.severity != AlertSeverity.CRITICAL or alert.type not in _AUTO_HALT_TYPES:
                continue
            self._processed_alert_ids.add(alert.id)
            robot = fleet.get(alert.robot_id or "")
            if robot is None:
                continue
            if robot.state == RobotState.HALTED:
                # Edge safety controller already halted it; log that we concur.
                new_decisions.append(self._decision(
                    now, alert.robot_id, OrchestratorAction.MONITOR, AlertSeverity.WARNING,
                    f"Confirmed edge halt on {robot.vendor} {robot.model}: {alert.type.value}.",
                    auto_executed=False,
                ))
                continue
            executed = self._store.estop(alert.robot_id or "", auto=True)
            new_decisions.append(self._decision(
                now, alert.robot_id, OrchestratorAction.AUTO_ESTOP, AlertSeverity.CRITICAL,
                f"Auto E-Stop {robot.vendor} {robot.model}: critical {alert.type.value}"
                + (f" (Δ {alert.delta_meters:.2f}m)" if alert.delta_meters is not None else "")
                + " — auto-clears once ARIA re-converges.",
                auto_executed=executed,
            ))

        # Policy 2 — proactive charge dispatch before a low battery strands the robot.
        for robot in fleet.values():
            if robot.state in (RobotState.CHARGING, RobotState.HALTED, RobotState.OFFLINE):
                continue
            if robot.battery_pct < settings.low_battery_pct:
                executed = self._store.dispatch_charge(robot.id)
                if executed:
                    new_decisions.append(self._decision(
                        now, robot.id, OrchestratorAction.DISPATCH_CHARGE, AlertSeverity.WARNING,
                        f"Battery {robot.battery_pct:.0f}% < {settings.low_battery_pct:.0f}% — "
                        f"dispatched {robot.vendor} {robot.model} to charge.",
                        auto_executed=True,
                    ))

        # Policy 3 — advisory review for degraded-but-safe drift (no action taken).
        for robot in fleet.values():
            if robot.state == RobotState.HALTED:
                continue
            if settings.drift_degraded_m < robot.drift_delta_m <= settings.halt_threshold_m:
                last = self._last_review_ts.get(robot.id, 0.0)
                if now - last >= _REVIEW_COOLDOWN_S:
                    self._last_review_ts[robot.id] = now
                    new_decisions.append(self._decision(
                        now, robot.id, OrchestratorAction.RECOMMEND_REVIEW, AlertSeverity.INFO,
                        f"{robot.vendor} {robot.model} drift {robot.drift_delta_m:.2f}m is degraded "
                        f"but under the halt threshold — operator review suggested.",
                        auto_executed=False,
                    ))

        if new_decisions:
            self._decisions = (new_decisions + self._decisions)[:50]

        # Bound the processed-alert set so it can't grow without limit.
        if len(self._processed_alert_ids) > 500:
            live = {a.id for a in self._store.recent_alerts(200)}
            self._processed_alert_ids &= live

        self._last_run_ts = now
        self._last_summary = summary
        self._last_narrative = self._template_narrative(summary, new_decisions)
        return self.status()

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            enabled=settings.orchestrator_enabled,
            llm_enabled=settings.llm_enabled,
            last_run_ts=self._last_run_ts,
            summary=self._last_summary,
            narrative=self._last_narrative,
            decisions=list(self._decisions[:25]),
        )

    async def refresh_narrative(self) -> None:
        """Optionally upgrade the templated narrative with an LLM summary (fail-open)."""
        if not settings.llm_enabled or self._last_summary is None:
            return
        llm = await self._llm_narrative(self._last_summary)
        if llm:
            self._last_narrative = llm

    # ── Internals ───────────────────────────────────────────────────────────────
    def _decision(self, ts: float, robot_id: Optional[str], action: OrchestratorAction,
                  severity: AlertSeverity, rationale: str, *, auto_executed: bool) -> OrchestratorDecision:
        return OrchestratorDecision(
            id=f"dec-{uuid.uuid4().hex[:10]}", ts=ts, robot_id=robot_id,
            action=action, severity=severity, rationale=rationale, auto_executed=auto_executed,
        )

    def _summarize(self, robots: list) -> FleetSummary:
        by_state = {s: 0 for s in ("active", "idle", "charging", "halted", "offline")}
        worst_id: Optional[str] = None
        worst_drift = 0.0
        for r in robots:
            by_state[r.state.value] = by_state.get(r.state.value, 0) + 1
            if r.drift_delta_m > worst_drift:
                worst_drift, worst_id = r.drift_delta_m, r.id
        return FleetSummary(
            total=len(robots),
            active=by_state["active"], idle=by_state["idle"], charging=by_state["charging"],
            halted=by_state["halted"], offline=by_state["offline"],
            unacked_alerts=len(self._store.unacknowledged_alerts()),
            worst_drift_robot=worst_id, worst_drift_m=round(worst_drift, 3),
        )

    def _template_narrative(self, s: FleetSummary, decisions: list[OrchestratorDecision]) -> str:
        parts = [
            f"{s.active}/{s.total} active, {s.charging} charging, {s.halted} halted; "
            f"{s.unacked_alerts} open alert(s)."
        ]
        if s.worst_drift_robot:
            parts.append(f"Worst drift: {s.worst_drift_robot} at {s.worst_drift_m:.2f}m.")
        acted = [d for d in decisions if d.auto_executed]
        if acted:
            parts.append("Actions: " + "; ".join(d.rationale for d in acted))
        elif not decisions:
            parts.append("No action required — fleet nominal.")
        return " ".join(parts)

    async def _llm_narrative(self, s: FleetSummary) -> str:
        """Best-effort OpenAI narrative. Never raises; returns '' on any problem."""
        import os

        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return ""
        try:
            import httpx

            prompt = (
                "You are the Orbital AI fleet supervisor. In two sentences, give an operator a "
                "calm, factual status read and the single most important thing to watch. "
                f"Fleet: total={s.total}, active={s.active}, charging={s.charging}, halted={s.halted}, "
                f"open_alerts={s.unacked_alerts}, worst_drift_robot={s.worst_drift_robot}, "
                f"worst_drift_m={s.worst_drift_m}."
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 120,
                    },
                )
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001 — narrative is advisory; never break the loop
            logger.debug("[orchestrator] LLM narrative unavailable: %s", exc)
            return ""


# Process-wide singleton bound to the shared store.
orchestrator = Orchestrator(store)


async def run() -> None:
    """Background supervisory loop — evaluate, (optionally) narrate, broadcast."""
    interval = max(1.0, settings.orchestrator_interval_s)
    while True:
        try:
            status = orchestrator.evaluate()
            await orchestrator.refresh_narrative()
            status = orchestrator.status()
            await hub.broadcast({"type": "orchestrator", "status": status.model_dump(mode="json")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
            logger.warning("[orchestrator] tick failed: %s", exc)
        await asyncio.sleep(interval)
