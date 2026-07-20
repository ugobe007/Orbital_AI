"""Shared types for OEM public API clients."""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class OemEndpoint:
    """One publicly documented API surface."""
    name: str
    kind: str  # ros2_action | ros2_topic | grpc | rest | http_rpc | udp | dds | lcm
    path: str
    docs_url: str
    notes: str = ""


@dataclass
class OemCall:
    """Recorded outbound call for dry-run / tests."""
    op: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)
    ok: bool = True
    detail: str = ""


class OemApiClient(abc.ABC):
    """Vendor public-API façade used by fleet adapters at hardware bind time."""

    vendor: str
    sdk_package: str
    docs_home: str

    def __init__(self, robot_id: str, host: str = "", *, dry_run: bool = True) -> None:
        self.robot_id = robot_id
        self.host = host
        self.dry_run = dry_run
        self.connected = False
        self.calls: list[OemCall] = []

    @classmethod
    @abc.abstractmethod
    def endpoints(cls) -> tuple[OemEndpoint, ...]:
        ...

    @abc.abstractmethod
    def connect(self, credentials: dict[str, str] | None = None) -> bool:
        ...

    @abc.abstractmethod
    def inject_waypoint(self, x: float, y: float, theta: float = 0.0) -> bool:
        """Map Orbital W_internal → vendor navigate/goal primitive."""

    @abc.abstractmethod
    def get_internal_pose(self) -> dict[str, float]:
        ...

    @abc.abstractmethod
    def trigger_estop(self) -> bool:
        ...

    def sdk_available(self) -> bool:
        """True if the vendor Python package imports in this environment."""
        return False

    def _record(self, op: str, payload: dict[str, Any], *, ok: bool = True, detail: str = "") -> OemCall:
        call = OemCall(op=op, payload=payload, ok=ok, detail=detail)
        self.calls.append(call)
        try:
            from .audit import emit_oem_call

            emit_oem_call(
                vendor=getattr(self, "vendor", "unknown"),
                robot_id=self.robot_id,
                op=op,
                payload=payload,
                ts=call.ts,
                ok=ok,
                detail=detail,
                dry_run=self.dry_run,
            )
        except Exception:  # noqa: BLE001 — audit must never break inject
            pass
        return call
