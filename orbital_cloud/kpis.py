"""Running totals for the live floor: the value the fleet is delivering, shown in the console's
value bar. Counted since the server started; nothing is persisted."""
from __future__ import annotations

import time
from typing import Optional

SLOWDOWN_GAP_S = 10.0


class FloorKpis:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started_at = time.time()
        self.deliveries = 0            # robot drop-offs completed
        self.slowdowns = 0             # times a robot eased off because a person or forklift was near
        self.safety_stops = 0          # robot stopped for a person inside its stop radius (one per alert)
        self.pallets_shipped = 0
        self.trucks_loaded = 0
        self._robot_s = 0.0
        self._working_s = 0.0
        self._slowed: set[str] = set()
        self._last_slow: dict[str, float] = {}

    def note_time(self, dt: float, working: bool) -> None:
        self._robot_s += dt
        if working:
            self._working_s += dt

    def note_slowed(self, robot_id: str, slowed: bool, now: Optional[float] = None) -> None:
        """Count each encounter once: a robot edging in and out of someone's radius is one
        slow-down, not one per tick or per flicker."""
        now = time.time() if now is None else now
        if slowed:
            if robot_id not in self._slowed and now - self._last_slow.get(robot_id, -1e9) >= SLOWDOWN_GAP_S:
                self.slowdowns += 1
            self._slowed.add(robot_id)
            self._last_slow[robot_id] = now
        else:
            self._slowed.discard(robot_id)

    def snapshot(self) -> dict:
        return {
            "since": self.started_at,
            "deliveries": self.deliveries,
            "slowdowns": self.slowdowns,
            "safety_stops": self.safety_stops,
            "pallets_shipped": self.pallets_shipped,
            "trucks_loaded": self.trucks_loaded,
            "fleet_busy_pct": round(100 * self._working_s / self._robot_s) if self._robot_s else None,
        }


kpis = FloorKpis()
