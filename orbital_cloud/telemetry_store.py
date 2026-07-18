"""TelemetryStore — persistence seam for drift time series (Sprint C1).

``Store.benchmark`` and ingest keep the same public API. The default backend is
in-memory; set ``ORBITAL_INFLUX_URL`` to dual-write line protocol (best-effort).
Reads always come from memory so MTBD/p95 stay deterministic without Influx.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)


class TelemetryStore(Protocol):
    def append(self, robot_id: str, ts: float, delta_m: float) -> None: ...
    def series(self, robot_id: str) -> Sequence[tuple[float, float]]: ...
    def ensure_robot(self, robot_id: str) -> None: ...
    def as_dict(self) -> dict[str, deque[tuple[float, float]]]: ...


class MemoryTelemetryStore:
    def __init__(self, maxlen: int = 5000) -> None:
        self._maxlen = maxlen
        self._data: dict[str, deque[tuple[float, float]]] = {}

    def ensure_robot(self, robot_id: str) -> None:
        self._data.setdefault(robot_id, deque(maxlen=self._maxlen))

    def append(self, robot_id: str, ts: float, delta_m: float) -> None:
        self.ensure_robot(robot_id)
        self._data[robot_id].append((ts, delta_m))

    def series(self, robot_id: str) -> Sequence[tuple[float, float]]:
        return self._data.get(robot_id, ())

    def as_dict(self) -> dict[str, deque[tuple[float, float]]]:
        return self._data


class InfluxTelemetryStore:
    """Memory primary + optional InfluxDB line-protocol dual-write."""

    def __init__(
        self,
        *,
        url: str,
        token: str = "",
        org: str = "orbital",
        bucket: str = "aria_drift",
        maxlen: int = 5000,
    ) -> None:
        self._memory = MemoryTelemetryStore(maxlen=maxlen)
        self.url = url.rstrip("/")
        self.token = token
        self.org = org
        self.bucket = bucket
        self.write_count = 0
        self.write_errors = 0

    def ensure_robot(self, robot_id: str) -> None:
        self._memory.ensure_robot(robot_id)

    def append(self, robot_id: str, ts: float, delta_m: float) -> None:
        self._memory.append(robot_id, ts, delta_m)
        self._write_line(robot_id, ts, delta_m)

    def series(self, robot_id: str) -> Sequence[tuple[float, float]]:
        return self._memory.series(robot_id)

    def as_dict(self) -> dict[str, deque[tuple[float, float]]]:
        return self._memory.as_dict()

    def _write_line(self, robot_id: str, ts: float, delta_m: float) -> None:
        # Influx line protocol: measurement,tag field timestamp_ns
        line = f"drift,robot_id={robot_id} delta_m={delta_m} {int(ts * 1e9)}"
        endpoint = f"{self.url}/api/v2/write?org={self.org}&bucket={self.bucket}&precision=ns"
        headers = {"Authorization": f"Token {self.token}"} if self.token else {}
        try:
            import httpx

            resp = httpx.post(endpoint, content=line + "\n", headers=headers, timeout=2.0)
            if resp.status_code >= 400:
                self.write_errors += 1
                logger.debug("influx write %s: %s", resp.status_code, resp.text[:200])
            else:
                self.write_count += 1
        except Exception as exc:  # noqa: BLE001 — never block ingest
            self.write_errors += 1
            logger.debug("influx write failed: %s", exc)


def build_telemetry_store() -> TelemetryStore:
    url = (os.getenv("ORBITAL_INFLUX_URL") or "").strip()
    if not url:
        return MemoryTelemetryStore()
    return InfluxTelemetryStore(
        url=url,
        token=os.getenv("ORBITAL_INFLUX_TOKEN", ""),
        org=os.getenv("ORBITAL_INFLUX_ORG", "orbital"),
        bucket=os.getenv("ORBITAL_INFLUX_BUCKET", "aria_drift"),
    )
