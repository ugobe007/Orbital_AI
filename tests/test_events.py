"""Live-update hub: one stuck dashboard must not freeze broadcasts for everyone else."""
import asyncio

from orbital_cloud import events
from orbital_cloud.events import EventHub


class _Stuck:
    async def send_text(self, message):
        await asyncio.sleep(3600)

    async def close(self):
        await asyncio.sleep(3600)


class _Ok:
    def __init__(self):
        self.got = []

    async def send_text(self, message):
        self.got.append(message)

    async def close(self):
        pass


def test_a_stalled_client_is_dropped_and_others_still_get_messages(monkeypatch):
    monkeypatch.setattr(events, "SEND_TIMEOUT_S", 0.05)
    hub, stuck, ok = EventHub(), _Stuck(), _Ok()
    hub._clients |= {stuck, ok}

    async def go():
        await asyncio.wait_for(hub.broadcast({"type": "fleet"}), 1.0)
        await asyncio.wait_for(hub.broadcast({"type": "fleet"}), 1.0)

    asyncio.run(go())
    assert stuck not in hub._clients
    assert len(ok.got) == 2


def test_nothing_is_serialized_with_no_dashboard_connected(monkeypatch):
    hub = EventHub()
    monkeypatch.setattr(events.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    asyncio.run(hub.broadcast({"type": "fleet"}))
    assert not hub.has_clients
