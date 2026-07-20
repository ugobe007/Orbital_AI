"""OEM call audit sink — Security Phase 5 (persist ``oem_api.calls``).

``OemApiClient._record`` fans out to registered sinks. Never raises into the
control path. Default process sink is an in-memory ring buffer; optional Influx
dual-write when ``ORBITAL_INFLUX_URL`` is set.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_sinks: list["OemCallAuditSink"] = []


@dataclass(frozen=True)
class OemAuditEvent:
    vendor: str
    robot_id: str
    op: str
    payload: dict[str, Any]
    ts: float
    ok: bool
    detail: str
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OemCallAuditSink(Protocol):
    def record(self, event: OemAuditEvent) -> None: ...
    def recent(self, *, limit: int = 100, robot_id: str | None = None) -> Sequence[OemAuditEvent]: ...


class MemoryOemCallAudit:
    """Ring buffer of OEM API calls for SIEM handoff / dashboard."""

    def __init__(self, maxlen: int = 2000) -> None:
        self._maxlen = maxlen
        self._events: list[OemAuditEvent] = []
        self._lock = threading.RLock()

    def record(self, event: OemAuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._maxlen:
                overflow = len(self._events) - self._maxlen
                del self._events[:overflow]

    def recent(self, *, limit: int = 100, robot_id: str | None = None) -> Sequence[OemAuditEvent]:
        with self._lock:
            items = self._events
            if robot_id:
                items = [e for e in items if e.robot_id == robot_id]
            if limit <= 0:
                return list(items)
            return list(items[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class InfluxOemCallAudit:
    """Memory primary + best-effort Influx line-protocol dual-write."""

    def __init__(
        self,
        *,
        url: str,
        token: str = "",
        org: str = "orbital",
        bucket: str = "aria_drift",
        maxlen: int = 2000,
    ) -> None:
        self._memory = MemoryOemCallAudit(maxlen=maxlen)
        self.url = url.rstrip("/")
        self.token = token
        self.org = org
        self.bucket = bucket
        self.write_count = 0
        self.write_errors = 0

    def record(self, event: OemAuditEvent) -> None:
        self._memory.record(event)
        self._write_line(event)

    def recent(self, *, limit: int = 100, robot_id: str | None = None) -> Sequence[OemAuditEvent]:
        return self._memory.recent(limit=limit, robot_id=robot_id)

    def _write_line(self, event: OemAuditEvent) -> None:
        vendor = event.vendor.replace(" ", "\\ ").replace(",", "\\,")
        robot = event.robot_id.replace(" ", "\\ ").replace(",", "\\,")
        op = event.op.replace(" ", "\\ ").replace(",", "\\,")
        ok = "true" if event.ok else "false"
        dry = "true" if event.dry_run else "false"
        line = (
            f"oem_api_call,vendor={vendor},robot_id={robot},op={op} "
            f"ok={ok},dry_run={dry} {int(event.ts * 1e9)}"
        )
        endpoint = f"{self.url}/api/v2/write?org={self.org}&bucket={self.bucket}&precision=ns"
        headers = {"Authorization": f"Token {self.token}"} if self.token else {}
        try:
            import httpx

            resp = httpx.post(endpoint, content=line + "\n", headers=headers, timeout=2.0)
            if resp.status_code >= 400:
                self.write_errors += 1
            else:
                self.write_count += 1
        except Exception as exc:  # noqa: BLE001
            self.write_errors += 1
            logger.debug("oem audit influx write failed: %s", exc)


def register_sink(sink: OemCallAuditSink) -> None:
    with _lock:
        if sink not in _sinks:
            _sinks.append(sink)


def unregister_sink(sink: OemCallAuditSink) -> None:
    with _lock:
        try:
            _sinks.remove(sink)
        except ValueError:
            pass


def clear_sinks() -> None:
    with _lock:
        _sinks.clear()


def emit_oem_call(
    *,
    vendor: str,
    robot_id: str,
    op: str,
    payload: dict[str, Any],
    ts: float | None = None,
    ok: bool = True,
    detail: str = "",
    dry_run: bool = True,
) -> None:
    """Fan-out to sinks; never raises."""
    event = OemAuditEvent(
        vendor=vendor,
        robot_id=robot_id,
        op=op,
        payload=dict(payload),
        ts=ts if ts is not None else time.time(),
        ok=ok,
        detail=detail,
        dry_run=dry_run,
    )
    with _lock:
        sinks = list(_sinks)
    for sink in sinks:
        try:
            sink.record(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("oem audit sink failed: %s", exc)


def build_oem_call_audit() -> OemCallAuditSink:
    url = (os.getenv("ORBITAL_INFLUX_URL") or "").strip()
    if not url:
        return MemoryOemCallAudit()
    return InfluxOemCallAudit(
        url=url,
        token=os.getenv("ORBITAL_INFLUX_TOKEN", ""),
        org=os.getenv("ORBITAL_INFLUX_ORG", "orbital"),
        bucket=os.getenv("ORBITAL_INFLUX_BUCKET", "aria_drift"),
    )
