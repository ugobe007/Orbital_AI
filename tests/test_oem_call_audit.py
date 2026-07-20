"""Security Phase 5 — OEM call audit sink."""
from __future__ import annotations

from fastapi.testclient import TestClient

from fleet_adapters.oem_apis.audit import (
    InfluxOemCallAudit,
    MemoryOemCallAudit,
    OemAuditEvent,
    emit_oem_call,
    register_sink,
    unregister_sink,
)
from fleet_adapters.oem_apis.unitree import UnitreeRos2Client
from orbital_cloud.main import app
from orbital_cloud.store import Store

client = TestClient(app)


def test_memory_audit_records_and_filters():
    sink = MemoryOemCallAudit()
    register_sink(sink)
    try:
        emit_oem_call(
            vendor="Unitree",
            robot_id="rbt-01",
            op="inject_waypoint",
            payload={"x": 1.0},
            dry_run=True,
        )
        emit_oem_call(
            vendor="Unitree",
            robot_id="rbt-02",
            op="trigger_estop",
            payload={},
            dry_run=True,
        )
        assert len(sink.recent()) == 2
        assert len(sink.recent(robot_id="rbt-01")) == 1
        assert sink.recent(robot_id="rbt-01")[0].op == "inject_waypoint"
    finally:
        unregister_sink(sink)


def test_oem_client_record_emits_to_sink():
    sink = MemoryOemCallAudit()
    register_sink(sink)
    try:
        api = UnitreeRos2Client("rbt-audit", dry_run=True)
        api.connect()
        api.inject_waypoint(1.0, 2.0, 0.1)
        ops = [e.op for e in sink.recent(robot_id="rbt-audit")]
        assert "connect" in ops
        assert "inject_waypoint" in ops
        assert all(e.vendor == "Unitree" for e in sink.recent(robot_id="rbt-audit"))
    finally:
        unregister_sink(sink)


def test_influx_audit_keeps_memory_when_unreachable():
    sink = InfluxOemCallAudit(url="http://127.0.0.1:1", token="x")
    sink.record(
        OemAuditEvent(
            vendor="Unitree",
            robot_id="rbt-01",
            op="trigger_estop",
            payload={},
            ts=1.0,
            ok=True,
            detail="",
            dry_run=True,
        )
    )
    assert len(sink.recent()) == 1
    assert sink.write_errors >= 1


def test_store_oem_audit_recent():
    sink = MemoryOemCallAudit()
    store = Store(oem_audit=sink)
    emit_oem_call(vendor="Agility", robot_id="digit-1", op="connect", payload={})
    rows = store.oem_audit_recent(limit=10)
    assert rows and rows[-1]["vendor"] == "Agility"
    unregister_sink(sink)


def test_dashboard_oem_audit_endpoint():
    r = client.get("/api/dashboard/oem-audit?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body and "count" in body
