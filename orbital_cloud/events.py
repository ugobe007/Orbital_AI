"""WebSocket hub for live dashboard updates.

The simulator (and real edge ingest) push two message kinds to every connected
dashboard: `fleet` snapshots and `alert` events.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket

SEND_TIMEOUT_S = 2.0


class EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def has_clients(self) -> bool:
        return bool(self._clients)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        text = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                # A half-open connection (laptop asleep, phone off wifi) would otherwise block
                # here and freeze the simulator loop for every dashboard.
                await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — client vanished mid-send or stopped reading
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(ws.close(), SEND_TIMEOUT_S)


hub = EventHub()
