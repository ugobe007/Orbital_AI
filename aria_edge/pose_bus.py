"""Shared pose bus between the injection loop and the Safety Halt watchdog.

The build guide requires the safety controller to run as an independent process that does
not share mutable state with the Waypoint Injector. In simulation we use a lock-backed
``PoseBus`` plus a ``SafetyWatchdog`` that only *reads* the bus — so a wedged inject path
cannot prevent halt evaluation once poses are published.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .safety_halt import SafetyHaltController
from .types import DriftEstimate, HaltDecision, Pose2D


@dataclass(frozen=True)
class PoseSample:
    robot_id: str
    external: Pose2D
    internal: Pose2D
    external_moved_m: float = 0.0
    internal_moved_m: float = 0.0
    ts: float = 0.0


class PoseBus:
    """Thread-safe latest-pose map. Process isolation can mmap this later (Sprint D)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, PoseSample] = {}

    def publish(
        self,
        robot_id: str,
        external: Pose2D,
        internal: Pose2D,
        *,
        external_moved_m: float = 0.0,
        internal_moved_m: float = 0.0,
        ts: float | None = None,
    ) -> None:
        sample = PoseSample(
            robot_id=robot_id,
            external=external,
            internal=internal,
            external_moved_m=external_moved_m,
            internal_moved_m=internal_moved_m,
            ts=time.time() if ts is None else ts,
        )
        with self._lock:
            self._latest[robot_id] = sample

    def get(self, robot_id: str) -> PoseSample | None:
        with self._lock:
            return self._latest.get(robot_id)

    def snapshot(self) -> dict[str, PoseSample]:
        with self._lock:
            return dict(self._latest)

    def clear(self) -> None:
        with self._lock:
            self._latest.clear()


HaltHandler = Callable[[HaltDecision], None]


class SafetyWatchdog:
    """Polls the pose bus and evaluates halt invariants without touching the injector."""

    def __init__(
        self,
        bus: PoseBus,
        controller: SafetyHaltController | None = None,
        *,
        on_halt: HaltHandler | None = None,
        hz: float = 20.0,
    ) -> None:
        self.bus = bus
        self.controller = controller or SafetyHaltController()
        self.on_halt = on_halt
        self.hz = hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_decisions: list[HaltDecision] = []

    def poll_once(self) -> list[HaltDecision]:
        decisions: list[HaltDecision] = []
        for sample in self.bus.snapshot().values():
            drift = DriftEstimate(
                robot_id=sample.robot_id,
                external=sample.external,
                internal=sample.internal,
            )
            decision = self.controller.evaluate(
                drift,
                external_moved_m=sample.external_moved_m,
                internal_moved_m=sample.internal_moved_m,
            )
            decisions.append(decision)
            if decision.halt and self.on_halt is not None:
                self.on_halt(decision)
        self.last_decisions = decisions
        return decisions

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aria-safety-watchdog", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        period = 1.0 / max(self.hz, 1.0)
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(period)
